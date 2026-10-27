# -*- coding: utf-8 -*-
"""
销售退货单 → 红字应收单 下推脚本
=================================================================
背景：有退货的客户（桐乡华淳、江西广源），其「销售退货单」要单独下推**红字应收**，
      与蓝字出库单→应收单分开处理，二者净额才对得上开票价税合计。

下推目标：退货单 → 应收单（金蝶按源单类型自动生成**红字**应收，金额为负）。
        只做「退货单 → 红字应收单 → 保存 → 提交」，不做专票/凭证（退货红字另走）。

用法：
    # 只看不写
    python src/push_return.py --customer 桐乡市华淳贸易有限公司 \
        --bill-nos XSTHD-105-202610-0001 --biz-date 2026-10-31 --dry-run

    # 正式推红字应收（到应收单为止）
    python src/push_return.py --customer 桐乡市华淳贸易有限公司 \
        --bill-nos XSTHD-105-202610-0001 --biz-date 2026-10-31 --yes
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, Guard, GuardError, shot  # noqa: E402
from steps import KingdeeUI  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="push_return")
SHOT_DIR = abs_path("screenshots", CFG)

# 退货单列表（单一真相源已进 steps.MENU_PATH_HINT 的话可直接复用，否则这里补）
RETURN_LIST_NAME = "销售退货单列表"
RETURN_LIST_PATH = "供应链>销售管理>退货处理"


def parse_args():
    ap = argparse.ArgumentParser(description="销售退货单 → 红字应收单")
    ap.add_argument("--customer", required=True, help="客户名（日志与核对）")
    ap.add_argument("--bill-nos", required=True, help="退货单号，逗号分隔（如 XSTHD-105-202610-0001）")
    ap.add_argument("--biz-date", default="", help="红字应收业务日期 YYYY-MM-DD")
    ap.add_argument("--yes", action="store_true", help="放行提交等不可逆动作")
    ap.add_argument("--dry-run", action="store_true", help="只读不写：筛选+勾选，不下推")
    return ap.parse_args()


def filter_return(ui, args):
    """打开退货单列表 → 过滤 → 勾选退货单，返回勾选行数。"""
    # 先清重复列表标签（保留「销售退货单列表」锚点）
    anchor = RETURN_LIST_NAME
    tabs = ui.tabs()
    names = [(t.get("text") or "").strip() for t in tabs]
    same_anchor = [t for t in tabs if (t.get("text") or "").strip() == anchor]
    if len(same_anchor) > 1:
        log.warn(f"有 {len(same_anchor)} 个「{anchor}」标签，只留最后 1 个")
        for t in same_anchor[:-1]:
            try:
                ui.close_tab(t)
            except Exception as e:  # noqa: BLE001
                log.warn(f"关重复锚点失败：{type(e).__name__}: {str(e)[:80]}")
        ui.page.wait_for_timeout(1200)

    # 打开退货单列表（不存在才搜索打开，已存在直接切）
    ok = ui.open_menu_by_search(anchor, path=RETURN_LIST_PATH)
    if not ok:
        raise RuntimeError("没能打开「销售退货单列表」")

    bill_nos = [b.strip() for b in (args.bill_nos or "").split(",") if b.strip()]
    log.info(f"按单据编号筛选退货单 {len(bill_nos)} 张：{bill_nos}")
    ui.set_bill_no_filter(bill_nos)

    rows = ui.grid_all_rows() or []
    bills = {str(r.get("FBILLNO") or "").strip() for r in rows if r.get("FBILLNO")}
    log.info(f"筛选后 {len(rows)} 行明细，覆盖 {len(bills)} 张退货单")
    if not rows:
        raise SystemExit("筛选后没有任何退货单，停止")

    # 硬校验：期望退货单必须一张不少、不多
    missing = [b for b in bill_nos if not any(b in x for x in bills)]
    extra = [x for x in bills if not any(w in x for w in bill_nos)]
    if missing or extra:
        log.err(f"筛选结果与预期不符 —— 缺 {missing}，多 {extra}")
        raise SystemExit(3)
    log.ok(f"退货单核对通过：{len(bills)} 张全部命中")

    # 客户核对（退货单客户字段 FRETCUSTID_FNAME）
    custs = {str(r.get("FRETCUSTID_FNAME") or "") for r in rows}
    if custs and custs != {args.customer}:
        log.warn(f"客户核对：退货单客户 {custs}，与 --customer {args.customer!r} 不完全一致")
    else:
        log.ok(f"客户核对一致：{args.customer}")

    ui.clear_selection()
    sel = ui.select_rows(bill_nos)
    log.info(f"勾选结果：命中 {sel} 行；真实选中 {ui.selected_count()}")
    return rows, sel


def step_return_receivable(ui, args):
    """退货单下推红字应收单。返回红字应收单号（可能读不到，靠反查）。"""
    # 下推 → 选「应收单」（金蝶按退货单类型自动生成红字）
    ui.push_to_receivable(merge=True)
    if not ui.activate_tab("应收单 - 新增", timeout_ms=10000):
        raise RuntimeError("没等到「应收单 - 新增」标签，下推可能失败")
    ui.page.wait_for_timeout(2500)

    # 删空行兜底（防御性）
    ui.detail_drop_empty_rows("receivable")

    if args.biz_date:
        # ★ SF-55：只认前台 + 写后回读；linked 字段做「确实变了」见证
        ui.fill_bill_field("FDATE", args.biz_date, linked="FENDDATE_H")
        d1 = (ui.read_foreground_field("FDATE") or {}).get("val")
        d2 = (ui.read_foreground_field("FENDDATE_H") or {}).get("val")
        log.info(f"红字应收业务日期 = {args.biz_date} → 界面回读 FDATE={d1!r} / 到期日={d2!r}")

    detail = ui.detail_amounts("receivable")
    log.info(f"红字应收明细 {len(detail)} 行，价税合计 {sum(r['all'] for r in detail):,.2f}")

    if args.dry_run:
        log.warn("--dry-run：不保存、不下推，到此为止")
        return None

    ui.save_bill()
    ui.page.wait_for_timeout(3000)
    # 红字应收单号读取（界面读不到就按来源退货单反查）
    ar_no = _read_no(ui, "FBillNo")
    if not ar_no:
        ar_no = _backfill_return_ar(ui, args)
    log.ok(f"红字应收单已保存：{ar_no or '(单号未读到)'}")
    shot(ui.page, SHOT_DIR, "push_return_receivable", log)
    return ar_no


def _read_no(ui, field="FBillNo"):
    try:
        v = (ui.read_foreground_field(field) or {}).get("val")
        return str(v).strip() if v else ""
    except Exception:  # noqa: BLE001
        return ""


def _backfill_return_ar(ui, args):
    """按来源退货单号反查刚生成的红字应收单号。"""
    from kdquery import KdQuery
    kq = KdQuery(ui.page)
    got = []
    for b in (args.bill_nos or "").split(","):
        b = b.strip()
        if not b:
            continue
        try:
            got += [x for x in kq.ar_by_source_bill(b) if x]
        except Exception:  # noqa: BLE001
            pass
    got = sorted(set(got))
    log.info(f"按来源退货单反查红字应收单：{got}")
    return got[-1] if got else ""


def main():
    args = parse_args()
    if args.dry_run and args.yes:
        log.warn("--dry-run 与 --yes 同时给了：dry-run 优先，不会写任何东西")

    t0 = time.time()
    guard = Guard(CFG, log)
    if args.yes and not args.dry_run:
        guard.s["auto_submit"] = True
        guard.s["auto_audit"] = True
        log.warn("已用 --yes 放行提交/审核")

    with sync_playwright() as p:
        log.info(f"连接调试浏览器 {CFG['browser']['cdp_url']}")
        browser = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pages = [pg for c in browser.contexts for pg in c.pages]
        if not pages:
            raise SystemExit("调试浏览器里没有页面，请先登录金蝶")
        page = pages[0]
        ui = KingdeeUI(page, CFG, log, guard)
        log.info(f"接管页面：{page.title()[:40]!r}")

        rows, sel = filter_return(ui, args)
        if args.dry_run:
            log.warn("--dry-run 结束（未下推）")
            browser.close()
            log.close()
            return 0

        ar_no = step_return_receivable(ui, args)

        # 提交（保存即审核）
        guard.check("submit", "提交红字应收单（本组织实测：保存即审核）")
        ui.submit_bill()
        ui.page.wait_for_timeout(2000)
        log.ok("红字应收单已提交（保存即审核）")

        # 清场：保留「销售出库单列表」锚点（与蓝字流程保持一致）
        try:
            closed = ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=True)
            log.ok(f"清场：关掉 {closed} 个标签")
        except Exception as e:  # noqa: BLE001
            log.warn(f"清场异常（继续）：{type(e).__name__}: {str(e)[:80]}")

        log.ok(f"全部结束：红字应收单 {ar_no or '-'} / 总耗时 {time.time() - t0:.1f}s")
        browser.close()
    log.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GuardError as e:
        log.err(f"护栏拦截：{e}")
        sys.exit(2)
