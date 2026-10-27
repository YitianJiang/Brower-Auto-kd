# -*- coding: utf-8 -*-
"""
从**源头出库单**下推「剩余下推.xlsx」里的客户（2026-10-03 第二批，11 家）。

与第一批（run_repush_20261003b.py）的区别：
  * 数据源换成 E:\\蒋一天\\git\\GenerateDiscountDetail\\剩余下推.xlsx
  * **不依赖 batch_20261003.json**：出库单号不写在表里，改为**金蝶实时查询**——
    每家取「日期在 2026-08-26~2026-09-25 且尚未下推（无下游应收单）」的已审核出库单。
  * **折扣口径（2026-10-03 定）**：表里「需分摊折扣额」列**不作为扣款依据** ——
    实测 11 家里 8 家「未下推出库单价税合计 == 表里开票价税合计」（出库单本身就是开票金额），
    再扣一遍折扣金额就对不上了。**唯一例外：郑州秉信** —— 出库单 478,153.12 比开票价税多 300，
    用户口径「折扣 300，之前传的表漏了」⇒ 传 `--discount 300` 从出库单里分摊扣掉。
    见 `DISCOUNT_POLICY`。
  * 金额门槛：未下推出库单合计必须 ≈ **表里开票价税 + 该家应扣折扣** 才推；否则**挂起等人工**。

只读预检（防推重）：按来源出库单反查下游应收单，必须为空。
断点续跑：output/rest_20261003d_result.json。
失败策略：连续 2 家失败即停。

用法：
    python src/run_rest_20261003d.py --probe      # 只读：列出每家未下推出库单与金额对拍
    python src/run_rest_20261003d.py --precheck   # 只跑预检
    python src/run_rest_20261003d.py              # 正式推
    python src/run_rest_20261003d.py --only 湖北通发包装材料有限公司
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

os.environ["KD_NO_SHOT"] = "1"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PY = sys.executable
RESULT = ROOT / "output" / "rest_20261003d_result.json"
XL = r"E:\蒋一天\git\GenerateDiscountDetail\剩余下推.xlsx"
DATE_FROM = "2026-08-26"          # 下推出库单起始日期（46260）
DATE_TO = "2026-09-25"            # 下推出库单结束日期（46290）
TOL = 0.05                        # 金额容差

# ★★ 扣款策略（价税合计口径）：只有郑州秉信需要扣。
#   表里「需分摊折扣额」列（1860 / 22.5 / 789.36 / …）在本批**不是**扣款依据 ——
#   实测那几家的未下推出库单价税合计**正好等于**表里开票价税，折扣已经体现在出库单里了。
#   郑州秉信是唯一「出库单 = 开票价税 + 300」的家（用户 2026-10-03 口径：表里该列漏了，实际 300）。
DISCOUNT_POLICY = {
    "郑州秉信包装有限公司": 300.0,
}
sys.path.insert(0, str(SRC))


def parse_date_txt(t):
    """'9月30号' → '2026-09-30'（本批上下文固定 2026）。"""
    m = re.match(r"^\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]\s*$", str(t))
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", str(t).strip()):
        return str(t).strip()
    raise SystemExit(f"无法解析日期：{t!r}")


def load_items():
    """读「剩余下推.xlsx」。

    第 2/3 列是「下推出库单起始/结束日期」= Excel 序列号（每家可不同，如福建广源 46255/46285），
    换算基准 1899-12-30。第 5 列「需分摊折扣额（默认负数）」负数=少收，录金蝶取正（abs）。
    """
    import openpyxl
    from datetime import datetime, timedelta
    wb = openpyxl.load_workbook(XL, data_only=True)
    ws = wb["Sheet1"]

    def sn2date(s):
        if s in (None, ""):
            return None
        return (datetime(1899, 12, 30) + timedelta(days=int(s))).strftime("%Y-%m-%d")

    items = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[3]:
            continue
        # ★ 折扣额口径（2026-10-03 用户定）：下推表里折扣给的是**负数**（负=少收/扣减），
        #   但金蝶「折扣额」字段必须录**正数**（正=打折），所以这里统一取绝对值。
        #   取 abs 后无论表里填正填负，脚本内部恒为正，--discount / 金额对拍 / 金蝶折扣额全链路符号一致。
        items.append({
            "customer": str(r[3]).strip(),
            "date": parse_date_txt(r[0]),
            "date_from": sn2date(r[1]) or DATE_FROM,
            "date_to": sn2date(r[2]) or DATE_TO,
            "discount": abs(float(r[4] or 0)),
            "notax": float(r[5] or 0),
            "tax": float(r[6] or 0),
            "all": float(r[7] or 0),
            "range": f"{sn2date(r[1]) or DATE_FROM}~{sn2date(r[2]) or DATE_TO}",
        })
    return items


# ------------------------------------------------------------------
# 只读：查出每家「区间内 + 未下推」的出库单
# ------------------------------------------------------------------
def probe(items, log=print):
    from playwright.sync_api import sync_playwright
    from common import load_config
    from kdquery import KdQuery, KdQueryError, num

    cfg = load_config()
    out = {}
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])
        try:
            kq.rows("AR_receivable", "FBillNo", "FBillNo = 'AR105-202609-0105'")
        except KdQueryError as e:
            log(f"!! 会话不可用：{str(e)[:150]}")
            br.close()
            return None
        for it in items:
            c = it["customer"]
            f = (f"FCustomerID.FName = '{c}' and FDocumentStatus = 'C' "
                 f"and FDate >= '{it['date_from']}' and FDate <= '{it['date_to']}'")
            try:
                rows = kq.rows("SAL_OUTSTOCK",
                               "FBillNo,FDate,FDocumentStatus,FCustomerID.FName,FBILLALLAMOUNT",
                               f, top=200)
            except KdQueryError as e:
                out[c] = {"err": str(e)[:150]}
                continue
            un, done = [], []
            for r in sorted(rows, key=lambda x: str(x.get("FDate") or "")):
                b = str(r.get("FBillNo") or "")
                amt = num(r.get("FBILLALLAMOUNT"))
                try:
                    ars = kq.ar_by_source_bill(b)
                except KdQueryError:
                    ars = []
                (done if ars else un).append({"bill": b, "date": str(r.get("FDate"))[:10],
                                              "amt": amt, "ar": ars})
            out[c] = {
                "un": un,
                "done": done,
                "un_amt": round(sum(x["amt"] for x in un), 2),
                "done_amt": round(sum(x["amt"] for x in done), 2),
            }
        br.close()
    return out


def verdict(it, pr):
    """返回 (can_push, reason)。can_push=True 才允许推。"""
    if not pr:
        return False, "探测未返回"
    if "err" in pr:
        return False, f"查询失败：{pr['err']}"
    if not pr["un"]:
        return False, ("★ 区间内没有未下推的出库单"
                       + (f"（已有下游应收单 {sorted({a for x in pr['done'] for a in x['ar']})}）"
                          if pr["done"] else ""))
    disc = float(it.get("disc") or 0)
    want = round(it["all"] + disc, 2)   # 期望的出库单毛额 = 开票价税 + 应扣折扣
    got = pr["un_amt"]
    near = abs(got - want) <= TOL
    has_done = bool(pr["done"])
    if near and not has_done:
        tip = f"出库单 {got:,.2f} = 开票价税 {it['all']:,.2f}" + \
              (f" + 折扣 {disc:,.2f}" if disc else "") + "，区间内无已下推单"
        return True, tip
    parts = [f"未下推 {got:,.2f} vs 期望 {want:,.2f}"
             f"（= 开票价税 {it['all']:,.2f}" + (f" + 折扣 {disc:,.2f}" if disc else "")
             + f"；差 {got - want:+,.2f}）"]
    if has_done:
        parts.append(f"★ 区间内已有下推单 {pr['done_amt']:,.2f}"
                     f"（{sorted({a for x in pr['done'] for a in x['ar']})}）")
    if near and has_done:
        return False, "；".join(parts) + " —— 有半成品单，需人工决定是否先清理"
    return False, "；".join(parts) + " —— 金额对不上，挂起"


def pre_clean():
    try:
        r = subprocess.run([PY, str(SRC / "clean_tabs.py"), "--go", "--keep-name", "销售出库单列表"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=240)
        lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        return " | ".join(lines[-2:]) if lines else f"（无输出 rc={r.returncode}）"
    except Exception as e:  # noqa: BLE001
        return f"pre_clean 异常（继续）：{type(e).__name__}: {e}"


def find_ar(bills):
    from playwright.sync_api import sync_playwright
    from common import load_config
    from kdquery import KdQuery
    cfg = load_config()
    got = []
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])
        for b in bills:
            got += [x for x in kq.ar_by_source_bill(b) if x]
        br.close()
    return sorted(set(got))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只读：列出每家未下推出库单 + 金额对拍")
    ap.add_argument("--precheck", action="store_true", help="只跑预检，不推单")
    ap.add_argument("--only", default="", help="只跑指定客户")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    items = load_items()
    # ★ 本批扣款直接取表里「需分摊折扣额（默认负数）」列（load_items 已 abs 成正数）。
    #   表里折扣列为空(None)的家 → 无扣款，disc=0，不传 --discount。
    for it in items:
        it["disc"] = float(it["discount"] or 0.0)
        it["xls_discount"] = it["discount"]
    if a.only:
        items = [x for x in items if x["customer"] == a.only]
        if not items:
            raise SystemExit(f"--only {a.only!r} 不在剩余下推.xlsx 里")
    if a.limit:
        items = items[:a.limit]

    print("=" * 112)
    print(f"第二批清单（{len(items)} 家，业务日期/到期日 = {items[0]['date'] if items else '-'}，"
          f"出库单区间 {DATE_FROM} ~ {DATE_TO}）")
    for i, it in enumerate(items, 1):
        print(f"  {i:2d}. {it['customer']:30s} 本批扣款={it['disc']:>9} "
              f"（表里折扣列 {it['xls_discount']}）"
              f" 开票价税={it['all']:>12,.2f}")
    print("=" * 112)

    print("\n---- 只读探测：区间内出库单 ----")
    pr = probe(items)
    if pr is None:
        return 2
    todo, blocked = [], []
    for it in items:
        c = it["customer"]
        ok, why = verdict(it, pr.get(c))
        p = pr.get(c) or {}
        print(("  ✓ " if ok else "  ✗ ") + f"{c[:26]:26s} {why}")
        for x in (p.get("un") or []):
            print(f"        未下推 {x['bill']}  {x['date']}  {x['amt']:>12,.2f}")
        (todo if ok else blocked).append(it)
    print(f"\n→ 可直接推 {len(todo)} 家；挂起 {len(blocked)} 家")
    if blocked:
        print("   挂起：" + "、".join(x["customer"] for x in blocked))
        print("   （预检不过一律不推：宁可不做，也不能推重/推错）")

    if a.probe:
        print("\n--probe：到此为止，没有推任何单。")
        return 0
    if a.precheck:
        print("\n--precheck：到此为止，没有推任何单。")
        return 0
    if not todo:
        print("没有可推的家，退出。")
        return 1

    prev = []
    if RESULT.exists():
        try:
            prev = json.loads(RESULT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    done = {r["customer"] for r in prev if r.get("ok")}
    pend = [it for it in todo if it["customer"] not in done]
    print(f"\n已完成/跳过 {len(todo) - len(pend)} 家，**待推 {len(pend)} 家**")

    results = list(prev)
    fails = 0
    t0 = time.time()
    for i, it in enumerate(pend, 1):
        c = it["customer"]
        bills = [x["bill"] for x in pr[c]["un"]]
        print(f"\n{'=' * 112}\n[{i}/{len(pend)}] {c}   业务日期={it['date']}\n"
              f"     出库单 {','.join(bills)}\n"
              f"     开票价税 {it['all']:,.2f}（不含税 {it['notax']:,.2f} / 税额 {it['tax']:,.2f}）"
              + (f"   应扣折扣 {it['disc']:,.2f}\n" if it["disc"] else "\n")
              + f"{'=' * 112}", flush=True)
        ts = time.time()
        print(f"  [⓪ 清场] {pre_clean()}", flush=True)

        # ★ 折扣：只给有扣款的家传（其余家出库单毛额 == 开票价税，传了反而把金额扣错）
        # ★ 三个数精确到分（用户 2026-10-03 口径）：notax/tax/all 都传，push_chain 对齐后
        #   三数须与表里完全一致，任一差 1 分都会 raise 拦住保存。
        cmd = [PY, str(SRC / "push_chain.py"),
               "--customer", c, "--bill-nos", ",".join(bills),
               "--target-notax", f"{it['notax']}", "--target-tax", f"{it['tax']}",
               "--target-all", f"{it['all']}",
               "--biz-date", it["date"], "--invoice-date", it["date"],
               "--no-invoice-merge", "--step", "voucher", "--yes"]
        if it["disc"]:
            cmd += ["--discount", f"{it['disc']}"]
        rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
        rec = {"customer": c, "bills": bills, "all": it["all"], "date": it["date"],
               "discount": it["disc"], "xls_discount": it["xls_discount"],
               "ok": False, "rc": rc}
        if rc == 0:
            rec["chain_ok"] = True
            try:
                ars = find_ar(bills)
                rec["ar"] = ars
                if ars:
                    ar_no = ars[-1]
                    rec["ar_used"] = ar_no
                    wd = subprocess.run([PY, str(SRC / "write_digest.py"),
                                         "--ar-no", ar_no, "--company", c, "--go"],
                                        cwd=str(ROOT)).returncode
                    rec["digest_ok"] = (wd == 0)
                    if wd != 0:
                        print(f"!! {c} 摘要写失败（rc={wd}）", flush=True)
                    else:
                        rec["ok"] = True
                else:
                    print(f"!! {c} 反查不到应收单号", flush=True)
            except Exception as e:  # noqa: BLE001
                rec["err"] = f"{type(e).__name__}: {str(e)[:200]}"
                print(f"!! {c} 写摘要异常：{rec['err'][:150]}", flush=True)
        else:
            print(f"!! {c} push_chain 失败（rc={rc}）—— 半成品不自动重试，"
                  f"请先只读核实已生成到哪一步，再人工续跑", flush=True)
        rec["sec"] = round(time.time() - ts, 1)
        results = [r for r in results if r.get("customer") != c]
        results.append(rec)
        RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-- {c} 用时 {rec['sec']}s  ok={rec['ok']}  "
              f"应收单={rec.get('ar_used') or rec.get('ar') or '—'}", flush=True)
        if rec["ok"]:
            fails = 0
        else:
            fails += 1
            if fails >= 2:
                print("!! 连续 2 家失败，停止（避免系统性故障下越跑越乱）", flush=True)
                break

    # ★ 2026-10-03 修复：okn/tot 之前统计的是 `results`（含断点文件里历史 N 家），
    #   与本轮 `len(pend)` 口径不一致 → 出现「成功 15/1 家」的假失败、退出码 1。
    #   这里只统计**本轮 pend 里的客户**，与分母对齐。
    this_customers = {it["customer"] for it in pend}
    okn = sum(1 for r in results if r.get("ok") and r.get("customer") in this_customers)
    tot = sum(float(r["all"]) for r in results
              if r.get("ok") and r.get("customer") in this_customers)
    print(f"\n{'=' * 112}\n===== 第二批结束：成功 {okn} / {len(pend)} 家"
          f"（成功部分开票价税合计 {tot:,.2f}），用时 {(time.time() - t0) / 60:.1f} 分钟 =====\n"
          f"{'=' * 112}", flush=True)
    return 0 if okn == len(pend) else 1


if __name__ == "__main__":
    sys.exit(main())
