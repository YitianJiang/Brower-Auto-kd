# -*- coding: utf-8 -*-
"""
批量：应收单（已存在）→ 提交审核 → 下推专票 → 合并生成凭证 → 写摘要
==============================================================================
数据源：桌面「应收单_*.xlsx」（两列：单据编号 / 客户）。
目标：每张应收单走完「下推增值税专用发票 → 形成凭证」。
★ 合并口径（用户 2026-10-04 定）：
    同一单位多张应收单 → 下推成多张专票 → **统一生成一张凭证**（一个单位一张凭证）。
    退货（负金额）应收单也下推，与同单位正数发票合并进同一张凭证。

流程（每个单位）：
  ① 提交 + 审核该单位所有非 C 状态应收单（A/B → C）
  ② 逐张应收单下推专票（分录不合并），回填 业务日期/发票日期 = MONTH_END，保存 + 提交
  ③ 专票列表勾选该单位**全部**专票 → 生成**一张**凭证（报告 0 错误）
  ④ 聚合该单位所有应收单明细数量 → 写摘要 → 保存凭证

只读预检（防推重）：每张应收单反查下游专票必须为空。
断点续跑：output/ar_voucher_batch_result.json（按单位名记录 ok）。
失败策略：连续 2 个单位失败即停。

用法：
    python src/run_ar_voucher_batch.py --probe      # 只读：列出分组 + 状态 + 下游专票
    python src/run_ar_voucher_batch.py              # 正式跑全部
    python src/run_ar_voucher_batch.py --only 江苏广源包装有限公司
    python src/run_ar_voucher_batch.py --limit 3    # 只跑前 3 个单位
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ["KD_NO_SHOT"] = "1"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
RESULT = ROOT / "output" / "ar_voucher_batch_result.json"
XL = r"C:\Users\Administrator\Desktop\应收单_2026100401125492_934055.xlsx"
MONTH_END = "2026-09-30"            # 专票 业务日期 / 发票日期 统一月底

sys.path.insert(0, str(SRC))


class VoucherFailedError(RuntimeError):
    """凭证生成失败（专票已生成审核，只差凭证）。不计入「连续失败」计数，跳过继续。"""


# ------------------------------------------------------------------
# 数据源：读桌面 xlsx，按单位分组
# ------------------------------------------------------------------
def load_groups():
    import openpyxl
    from collections import OrderedDict
    wb = openpyxl.load_workbook(XL, data_only=True)
    ws = wb[wb.sheetnames[0]]
    groups = OrderedDict()
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[0]:
            continue
        ar = str(r[0]).strip()
        cust = str(r[1]).strip()
        groups.setdefault(cust, []).append(ar)
    return groups


# ------------------------------------------------------------------
# 只读探测：分组 + 每张单的状态 + 下游专票
# ------------------------------------------------------------------
def probe(groups, log=print):
    from playwright.sync_api import sync_playwright
    from common import load_config
    from kdquery import KdQuery, KdQueryError, num

    cfg = load_config()
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])
        try:
            kq.rows("AR_receivable", "FBillNo", "FBillNo = 'AR105-202609-0004'")
        except KdQueryError as e:
            log(f"!! 会话不可用：{str(e)[:150]}")
            br.close()
            return None
        out = {}
        for cust, ars in groups.items():
            rows = []
            for ar in ars:
                r = kq.rows("AR_receivable",
                            "FBillNo,FDATE,FDocumentStatus,FCustomerID.FName,FALLAMOUNTFOR",
                            f"FBillNo = '{ar}'", top=3)
                if not r:
                    rows.append({"ar": ar, "missing": True})
                    continue
                h = r[0]
                ivs = kq.rows("IV_SALESIC", "FBillNo", f"FSrcBillNo = '{ar}'", top=10)
                # ★ 费用应收识别：明细物料全空 = 费用应收，不参与本轮下推（用户 2026-10-04 定）
                mats = kq.rows("AR_receivable", "FMaterialId.FName",
                               f"FBillNo = '{ar}'", top=500)
                is_fee = len(mats) > 0 and all(not (m.get("FMaterialId.FName") or "").strip()
                                               for m in mats)
                rows.append({"ar": ar, "status": h.get("FDocumentStatus"),
                             "amt": num(h.get("FALLAMOUNTFOR")),
                             "iv": [str(x.get("FBillNo")) for x in ivs],
                             "fee": is_fee})
            out[cust] = rows
        br.close()
    return out


# ------------------------------------------------------------------
# 正式执行一个单位
# ------------------------------------------------------------------
def run_one(ui, kq, cust, ars, log):
    """
    一个单位的完整链路。返回 dict（含 ok / ars / ivs / voucher 信息）。
    """
    rec = {"customer": cust, "ars": ars, "ok": False}

    # ---- ① 提交 + 审核应收单（A/B → C）----
    need_audit = []
    for ar in ars:
        r = kq.rows("AR_receivable", "FBillNo,FDocumentStatus",
                    f"FBillNo = '{ar}'", top=3)
        st = r[0].get("FDocumentStatus") if r else None
        if st != "C":
            need_audit.append(ar)
    if need_audit:
        log.info(f"① 提交+审核应收单 {len(need_audit)} 张：{need_audit}")
        ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=True)
        ui.open_receivable_list()
        ui.page.wait_for_timeout(2500)
        ui.clear_selection()
        # ★ 先精确筛单，避免 1500 行虚拟滚动截断（同一单位多张单也要都筛出来）
        ui.set_bill_no_filter(need_audit)
        n = ui.select_rows(need_audit)
        log.info(f"  应收单列表勾选 {n} 张")
        if n <= 0:
            raise RuntimeError("应收单没勾上")
        ui.submit_bill()
        ui.page.wait_for_timeout(4000)
        ui.audit_bill()
        ui.page.wait_for_timeout(4000)
        # 服务端回读确认
        for ar in ars:
            r = kq.rows("AR_receivable", "FBillNo,FDocumentStatus",
                        f"FBillNo = '{ar}'", top=3)
            st = r[0].get("FDocumentStatus") if r else None
            log.info(f"  {ar} 状态 = {st}")
            if st != "C":
                raise RuntimeError(f"应收单 {ar} 提交审核后状态仍非 C（={st}）")
    else:
        log.info("① 应收单均已审核(C)，跳过提交审核")

    # ---- ② 逐张应收单下推专票 ----
    iv_nos = []
    for ar in ars:
        log.info(f"② 下推专票：{ar}")
        ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=True)
        ui.open_receivable_list()
        # ★ 先精确筛单：应收单列表按明细展开共 1500 行、虚拟滚动只渲染 200 行，
        #   不筛单的话 select_rows 只扫得到渲染窗口内的行 → 明细多的单会被截断
        #   （台州瑾晟 11 行只勾中 6 行、专票缺 5 行 11320 元的根因）。
        ui.clear_selection()
        filtered = ui.set_bill_no_filter([ar])
        log.info(f"  筛选 {ar} → 列表 {filtered} 行")
        n = ui.select_rows([ar])
        log.info(f"  勾选 {ar} → {n}")
        if n <= 0:
            raise RuntimeError(f"应收单 {ar} 没勾上")
        ui.push_to_invoice(merge=False)     # 分录不合并，不产生 -00000N 子单
        if not ui.activate_tab("销售增值税专用发票 - 新增", timeout_ms=12000):
            raise RuntimeError(f"{ar} 没等到专票标签")
        ui.page.wait_for_timeout(2500)

        # 回填 业务日期/发票日期/复核人（开票人保留系统默认，发票号不填）
        from common import load_config as _lc
        checker = (_lc().get("rules", {}) or {}).get("invoice_checker")
        ui.backfill_invoice_info(drawer=None, biz_date=MONTH_END,
                                 invoice_date=MONTH_END, checker=checker)
        ui.page.wait_for_timeout(900)

        ui.save_bill()
        ui.page.wait_for_timeout(3500)
        inv_no = _read_no(ui)
        if not inv_no:
            inv_no = _backfill_no(ui, "iv", ar)
        inv_no = _verify_iv_no(ui, inv_no, ar)
        log.ok(f"  专票已保存：{inv_no}")
        # ★ 保存后服务端回读业务日期，防 FDATE 写进后台孤儿草稿页（SF-55），
        #   否则日期错成"今天"→ 凭证取到错误期间 → TOAV202 失败（2026-10-04 事故）。
        _verify_inv_biz_date(ui, inv_no, MONTH_END)
        ui.submit_bill()
        ui.page.wait_for_timeout(2500)
        log.ok(f"  专票已提交（提交即审核）：{inv_no}")
        iv_nos.append(inv_no)

    rec["ivs"] = iv_nos

    # ---- ③ 专票列表勾选全部 → 生成一张凭证 ----
    log.info(f"③ 生成凭证（{len(iv_nos)} 张专票合并一张）：{iv_nos}")
    ui.close_all_tabs(keep_names=["销售增值税专用发票列表", "销售出库单列表"], quiet=True)
    ui.open_invoice_list()
    ui.clear_selection()
    # ★ 同样先精确筛单，避免专票列表虚拟滚动截断（多单合并时明细行更多）
    ui.set_bill_no_filter(iv_nos)
    n = ui.select_rows(iv_nos)
    log.info(f"  专票列表勾选 {n} 张")
    if n <= 0:
        raise RuntimeError("专票没勾上")
    ui.generate_voucher()
    # ★★ 报告列表是「瞬时单槽」+ 惰性渲染：生成凭证后，报告列表标签的**创建有延迟**
    #   （金蝶异步生成，标签可能晚几秒才弹出）。过去一次性 `next()` 找标签，找不到就
    #   跳过激活 → read_voucher_report() 读到空 → 把「凭证生成失败(TOAV202)」漏判成
    #   「0 错误」→ 继续跑，最后在「查看业务凭证」勾不到行报 out of range（静默漏单，
    #   台州恩特就是这么漏的）。⇒ 改为「轮询等标签出现 → 激活 → 轮询等数据渲染」。
    target = None
    _t0 = time.time()
    while (time.time() - _t0) < 15:
        target = next((n for n in [t["text"] for t in ui.tabs()] if "凭证生成报告" in str(n)), None)
        if target:
            break
        ui.page.wait_for_timeout(500)
    if target:
        ui.activate_tab(target, timeout_ms=8000)
    # 激活后再轮询等报告列表数据渲染出来（FMESSAGE 等字段就位），最多 10s
    _t1 = time.time()
    rep_rows = []
    while (time.time() - _t1) < 10:
        rep_rows = ui.read_voucher_report() or []
        if rep_rows:
            break
        ui.page.wait_for_timeout(500)
    if not target:
        log.warn("  ⚠️ 没等到「凭证生成报告」标签，报告读不到（可能生成中）")
    if not rep_rows:
        log.warn(f"  ⚠️ 报告列表数据未渲染（读了 10s 仍空），标签={target!r}")

    errs = ui.voucher_report_errors()
    if errs:
        for e in errs:
            log.err(f"  凭证生成失败 {e.get('单据编号')} [{e.get('检查项')}] {e.get('提示信息')}")
        raise VoucherFailedError("凭证生成报告有失败（专票已生成，凭证待补）")

    log.ok("  凭证生成报告 0 错误")

    # ---- ④ 聚合摘要 + 写摘要 + 保存凭证 ----
    text = _compute_digest_multi(kq, ars, cust, log)
    log.ok(f"④ 聚合摘要 = {text}")

    # 从「凭证生成报告列表」→ 业务凭证 → 总账凭证（凭证 - 修改）
    if not target:
        raise RuntimeError("没有「凭证生成报告列表」标签 —— 凭证没生成？")
    ui.activate_tab(target, timeout_ms=8000)
    ui.page.wait_for_timeout(1500)
    ui.open_biz_voucher_list()
    ui.page.wait_for_timeout(2500)
    ui.open_gl_voucher_for_edit()
    ui.page.wait_for_timeout(3000)
    if "凭证 - 修改" not in str(ui.active_tab() or ""):
        act = next((n for n in [t["text"] for t in ui.tabs()] if "凭证 - 修改" in str(n)), None)
        if act:
            ui.activate_tab(act, timeout_ms=8000)
            ui.page.wait_for_timeout(1500)

    entries = ui.read_voucher_entries()
    idxs = [i for i, e in enumerate(entries) if (e.get("FEXPLANATION") or "").strip()]
    log.info(f"  凭证分录 {len(entries)} 行，要改摘要 {len(idxs)} 行")
    for i in idxs:
        ui.set_one_digest(i, text)
    ui.page.wait_for_timeout(800)
    after = ui.read_voucher_entries()
    ok = all((after[i].get("FEXPLANATION") or "").strip() == text for i in idxs)
    log.ok(f"  回读校验：{'全部一致 ✓' if ok else '有不一致 ✗'}")
    if not ok:
        raise RuntimeError("摘要回读不一致，未保存凭证")

    ui.save_voucher()
    ui.page.wait_for_timeout(3000)
    log.ok("  凭证已保存（未提交未审核）")
    rec["ok"] = True
    rec["digest"] = text
    return rec


def _read_no(ui):
    got = ui.read_bill_fields(["FBillNo"])
    return str(got.get("FBillNo") or "").strip() or None


def _backfill_no(ui, kind, ar_no):
    """兜底：界面读不到专票号，用服务端按来源应收单反查。"""
    from kdquery import KdQuery
    kq = KdQuery(ui.page)
    r = kq.rows("IV_SALESIC", "FBillNo,FCreateDate", f"FSrcBillNo = '{ar_no}'", top=20)
    r = sorted(r, key=lambda x: str(x.get("FCreateDate") or ""), reverse=True)
    if r:
        ui.log.warn(f"界面读不到专票号，按来源 {ar_no} 反查取最近：{r[0].get('FBillNo')}")
        return str(r[0].get("FBillNo"))
    return None


def _verify_iv_no(ui, inv_no, ar_no):
    """★ 服务端反证：界面读到的专票号必须能被来源应收单反查到，否则以服务端为准。"""
    from kdquery import KdQuery
    kq = KdQuery(ui.page)
    r = kq.rows("IV_SALESIC", "FBillNo", f"FBillNo = '{inv_no}' and FSrcBillNo = '{ar_no}'", top=5)
    if r:
        return inv_no
    # 反查：该应收单最新的专票号
    r2 = kq.rows("IV_SALESIC", "FBillNo,FCreateDate", f"FSrcBillNo = '{ar_no}'", top=20)
    r2 = sorted(r2, key=lambda x: str(x.get("FCreateDate") or ""), reverse=True)
    if r2:
        real = str(r2[0].get("FBillNo"))
        ui.log.warn(f"★ 界面专票号 {inv_no} 反查不到来源 {ar_no}，改用服务端 {real}")
        return real
    raise RuntimeError(f"专票号 {inv_no} 既反查不到来源，也查不到 {ar_no} 的下游专票")


def _verify_inv_biz_date(ui, inv_no, want, tries=3, gap_ms=1500):
    """★ 专票保存后**服务端回读**业务日期 FDATE（权威判据）。

    为什么必须有（2026-10-04 事故，13 家凭证 TOAV202 失败）：
      脚本明明 `fill_bill_field("FDATE", "2026-09-30")` 且界面回读也显示 09-30，
      但服务端最终存的是**今天 10-04** —— 因为 `pickBox` 的 `b[0]` 兜底把值写进了
      **后台残留的「销售增值税专用发票 - 新增」孤儿草稿页**（SF-55 在专票上复现），
      当前真正保存的单 FDATE 仍是默认"今天"。界面回读是**自证**（写读同一错对象），
      骗过了过去所有检查。⇒ 只认 ExecuteBillQuery 服务端回读。

    后果：FDATE=10-04 → 凭证按"单据日期获取期间"取到 10 月期间，但账簿只开 9 月 →
      报「凭证日期不在账簿期间」TOAV202 失败（专票已生成，凭证补不上）。
    判据：回读 FDATE 必须 == want（业务月底），不符抛错，在下推凭证前拦下。
    """
    if not inv_no or not want:
        return None
    from kdquery import KdQuery
    k_want = str(want).strip().replace("/", "-")
    kq = KdQuery(ui.page)
    got = None
    for i in range(1, max(1, tries) + 1):
        try:
            rows = kq.rows("IV_SALESIC", "FBillNo,FDATE", f"FBillNo = '{inv_no}'", top=5)
        except Exception as e:  # noqa: BLE001
            ui.log.warn(f"服务端回读专票 {inv_no} 业务日期第 {i} 次失败：{str(e)[:110]}")
            rows = []
        if rows:
            got = str(rows[0].get("FDATE") or "")[:10]
            break
        ui.page.wait_for_timeout(gap_ms)
    if not got:
        ui.log.warn(f"服务端回读专票 {inv_no} 未取到行（可能刚落库）—— 已跳过，"
                    f"批量末尾用 verify_batch 兜底复核")
        return None
    if got == k_want:
        ui.log.ok(f"服务端回读专票 {inv_no}：业务日期 {got} ✓")
        return got
    raise RuntimeError(
        f"★ 专票 {inv_no} 业务日期 = {got}，应为 {k_want}（SF-55：FDATE 可能写进了后台孤儿草稿页）"
        f"。已停止，不继续生成凭证。请反审核该专票 → 改业务日期为 {k_want} → 重新审核后补凭证。")


def _compute_digest_multi(kq, ars, company, log):
    """聚合多张应收单明细数量，算摘要（口径同 write_digest）。"""
    from collections import Counter
    from digest_rule import classify_rows, build_digest, fmt_qty
    rows = []
    for ar in ars:
        ent = kq.rows("AR_receivable",
                      "FMaterialId.FName,FPriceQty,F_PAEZ_BASEPROPERTY2",
                      f"FBillNo = '{ar}'", top=500)
        for r in ent:
            rows.append({"material": r.get("FMaterialId.FName"),
                         "qty": r.get("FPriceQty"),
                         "category": r.get("F_PAEZ_BASEPROPERTY2")})
    c = classify_rows(rows)
    log.info(f"  聚合 {len(ars)} 张应收单 → {len(rows)} 行明细；"
             f"类别分布={dict(Counter(str(r['category']) for r in rows))}")
    log.info(f"  x(原材料)={fmt_qty(c['x'])} y(光油)={fmt_qty(c['y'])} z={fmt_qty(c['z'])} → s={fmt_qty(c['s'])}")
    if c["both_rows"]:
        log.warn(f"  ⚠️ {len(c['both_rows'])} 行同时命中「原材料」和「光油」，x、y 各计一次")
    return build_digest(company, c["x"], c["y"], c["s"], omit_zero=True)


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只读：列出分组 + 状态 + 下游专票")
    ap.add_argument("--only", default="", help="只跑指定单位")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个单位")
    ap.add_argument("--start-from", default="", help="从指定单位开始（跳过之前的）")
    a = ap.parse_args()

    groups = load_groups()
    total_ars = sum(len(v) for v in groups.values())
    print("=" * 100)
    print(f"应收单 {total_ars} 张 / {len(groups)} 个单位；多单单位：")
    for c, v in groups.items():
        if len(v) > 1:
            print(f"    {c}: {len(v)} 张 -> {v}")
    print("=" * 100)

    if a.only:
        if a.only not in groups:
            raise SystemExit(f"--only {a.only!r} 不在应收单列表里")
        groups = {a.only: groups[a.only]}
    if a.limit:
        groups = dict(list(groups.items())[:a.limit])
    if a.start_from:
        keys = list(groups.keys())
        idx = next((i for i, k in enumerate(keys) if k == a.start_from), None)
        if idx is None:
            raise SystemExit(f"--start-from {a.start_from!r} 不在列表里")
        groups = dict(list(groups.items())[idx:])

    pr = probe(groups)
    if pr is None:
        return 2

    if a.probe:
        for cust, rows in pr.items():
            print(f"\n{cust} ({len(rows)} 张)")
            for r in rows:
                if r.get("missing"):
                    print(f"    {r['ar']}  ← 查无此单！")
                    continue
                flag = "← 已有下游专票" if r["iv"] else ""
                fee = "  ★费用应收(跳过)" if r.get("fee") else ""
                print(f"    {r['ar']}  状态={r['status']}  价税={r['amt']:>12,.2f}  {','.join(r['iv'])}{flag}{fee}")
        print("\n--probe 到此为止，没有写任何东西。")
        return 0

    # ★ 排除费用应收单（用户 2026-10-04 定：费用应收不参与本轮下推）
    for cust, rows in list(pr.items()):
        fees = [r["ar"] for r in rows if r.get("fee")]
        if fees:
            print(f"跳过费用应收（{cust}）：{fees}")
            keep = [r["ar"] for r in rows if not r.get("fee")]
            if keep:
                groups[cust] = keep
            else:
                del groups[cust]
    pr = {c: [r for r in rows if not r.get("fee")] for c, rows in pr.items()}
    pr = {c: rows for c, rows in pr.items() if rows}

    # ★ 排除已完成(ok) + 待人工重做(redo)的单位：它们已有下游专票是正常的，不应触发"防推重"预检
    _prev_all = []
    if RESULT.exists():
        try:
            _prev_all = json.loads(RESULT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _prev_all = []
    _done_names = {r["customer"] for r in _prev_all if r.get("ok")}
    _redo_names = {r["customer"] for r in _prev_all if r.get("redo")}
    _vf_names = {r["customer"] for r in _prev_all if r.get("voucher_failed")}
    _skip_names = _done_names | _redo_names | _vf_names
    if _skip_names:
        print(f"跳过已完成/待重做/凭证失败单位（共 {len(_skip_names)}）：{sorted(_skip_names)}")
        for c in _skip_names:
            groups.pop(c, None)
            pr.pop(c, None)

    # 预检：所有单都必须存在、且无下游专票
    for cust, rows in pr.items():
        for r in rows:
            if r.get("missing"):
                print(f"!! {cust} 的 {r['ar']} 查无此单，停止")
                return 1
            if r["iv"]:
                print(f"!! {cust} 的 {r['ar']} 已有下游专票 {r['iv']}，停止（防推重）")
                return 1

    from playwright.sync_api import sync_playwright
    from common import load_config, Logger, Guard, abs_path
    from steps import KingdeeUI
    from kdquery import KdQuery

    cfg = load_config()
    log = Logger(abs_path("logs", cfg), name="ar_voucher_batch")
    # 用户已批准 提交/审核/生成凭证/保存凭证 → 临时放行（不改 config）
    _cfg = dict(cfg)
    _cfg["safety"] = dict(cfg.get("safety", {}) or {})
    _cfg["safety"].update({"auto_submit": True, "auto_audit": True,
                           "auto_voucher": True, "auto_voucher_save": True})
    guard = Guard(_cfg, log)

    prev = []
    if RESULT.exists():
        try:
            prev = json.loads(RESULT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    done = {r["customer"] for r in prev if r.get("ok")}
    pend = [c for c in groups if c not in done]
    print(f"\n已完成/跳过 {len(groups) - len(pend)} 个单位，待跑 {len(pend)} 个单位")

    results = list(prev)
    fails = 0
    t0 = time.time()
    try:
        with sync_playwright() as p:
            br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
            pg = br.contexts[0].pages[0]
            ui = KingdeeUI(pg, cfg, log, guard)
            kq = KdQuery(pg)

            for i, cust in enumerate(pend, 1):
                ars = groups[cust]
                print(f"\n{'=' * 100}\n[{i}/{len(pend)}] {cust}  （{len(ars)} 张：{','.join(ars)}）\n{'=' * 100}", flush=True)
                ts = time.time()
                rec = {"customer": cust, "ars": ars, "ok": False}
                try:
                    rec = run_one(ui, kq, cust, ars, log)
                except VoucherFailedError as e:  # 凭证失败：专票已生成，跳过继续，不计连续失败
                    log.err(f"{cust} 凭证生成失败：{str(e)[:300]}")
                    rec["err"] = f"凭证失败:{str(e)[:300]}"
                    rec["voucher_failed"] = True
                except Exception as e:  # noqa: BLE001
                    log.err(f"{cust} 失败：{type(e).__name__}: {str(e)[:300]}")
                    rec["err"] = f"{type(e).__name__}: {str(e)[:300]}"
                rec["sec"] = round(time.time() - ts, 1)
                results = [r for r in results if r.get("customer") != cust]
                results.append(rec)
                RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"-- {cust} 用时 {rec['sec']}s  ok={rec['ok']}  "
                      f"专票={rec.get('ivs') or '—'}", flush=True)
                # ★★ 2026-10-04 放宽停止策略：偶发竞态（row out of range / 找不到列表网格 /
                #   过滤错位）都是「重跑往往能过」的，不该终止整批（否则 26 家要重启 7 次）。
                #   改为：失败仅记录+跳过，继续跑；只有「会话不可用」才停。
                #   每跑 6 个单位做一次会话健康检查（probe 一次），失效就停。
                if rec["ok"] or rec.get("voucher_failed"):
                    fails = 0
                else:
                    fails += 1
                    # 会话健康检查：连续 3 个失败时确认会话还活着，否则停
                    if fails >= 3:
                        alive = False
                        try:
                            _r = kq.rows("AR_receivable", "FBillNo",
                                         "FBillNo = 'AR105-202609-0004'", top=1)
                            alive = bool(_r)
                        except Exception:  # noqa: BLE001
                            alive = False
                        if not alive:
                            print("!! 会话不可用，停止", flush=True)
                            break
                        log.warn(f"  连续 {fails} 个失败但会话仍可用，继续跑（偶发竞态）")

            # ---- 自动重试一轮：偶发竞态失败的单位（非凭证失败、非 redo）重跑一次 ----
            # 这些失败多在「下推专票之前」的列表/过滤/勾选阶段，专票尚未生成，重跑安全。
            # 凭证失败(voucher_failed)专票已生成，重跑会推重，绝不重试。
            _retry = [r["customer"] for r in results
                      if r.get("customer") in {c for c in pend}
                      and not r.get("ok") and not r.get("voucher_failed") and not r.get("redo")]
            if _retry:
                print(f"\n{'=' * 100}\n自动重试 {len(_retry)} 个偶发失败单位：{_retry}\n{'=' * 100}", flush=True)
                for cust in _retry:
                    ars = groups.get(cust) or []
                    # 重试前防推重：确认该单位应收单还没有下游专票
                    has_iv = False
                    for ar in ars:
                        try:
                            _iv = kq.rows("IV_SALESIC", "FBillNo", f"FSrcBillNo = '{ar}'", top=3)
                            if _iv:
                                has_iv = True
                                log.warn(f"  重试跳过 {cust}：{ar} 已有下游专票 {[x.get('FBillNo') for x in _iv]}")
                                break
                        except Exception:  # noqa: BLE001
                            pass
                    if has_iv:
                        continue
                    print(f"\n[重试] {cust}  （{len(ars)} 张）", flush=True)
                    ts = time.time()
                    rec = {"customer": cust, "ars": ars, "ok": False}
                    try:
                        rec = run_one(ui, kq, cust, ars, log)
                    except VoucherFailedError as e:
                        log.err(f"{cust} 重试凭证失败：{str(e)[:200]}")
                        rec["err"] = f"凭证失败:{str(e)[:200]}"
                        rec["voucher_failed"] = True
                    except Exception as e:  # noqa: BLE001
                        log.err(f"{cust} 重试仍失败：{type(e).__name__}: {str(e)[:200]}")
                        rec["err"] = f"{type(e).__name__}: {str(e)[:200]}"
                    rec["sec"] = round(time.time() - ts, 1)
                    results = [r for r in results if r.get("customer") != cust]
                    results.append(rec)
                    RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(f"-- [重试] {cust} ok={rec['ok']} 专票={rec.get('ivs') or '—'}", flush=True)

            br.close()
    finally:
        pass

    this = {c for c in pend}
    okn = sum(1 for r in results if r.get("ok") and r.get("customer") in this)
    print(f"\n{'=' * 100}\n===== 批量结束：成功 {okn} / {len(pend)} 个单位，"
          f"用时 {(time.time() - t0) / 60:.1f} 分钟 =====\n{'=' * 100}", flush=True)
    log.close()
    return 0 if okn == len(pend) else 1


if __name__ == "__main__":
    sys.exit(main())
