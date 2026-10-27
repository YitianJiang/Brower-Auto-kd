# -*- coding: utf-8 -*-
"""
主流程编排
----------
销售出库单 -> 应收单 -> 增值税专用发票(分录合并) -> 凭证(改摘要)

用法：
    python src/run.py --plan      # 只解析 Excel，打印执行计划，不连浏览器、不碰金蝶
    python src/run.py             # 正式跑（会连调试浏览器操作金蝶）
    python src/run.py --limit 3   # 只处理前 3 组，小批量验证用
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (load_config, abs_path, Logger, Guard, GuardError, shot,
                    read_excel, write_back, resolve_range, group_by_invoice,
                    resolve_biz_date, to_date,
                    COL_CUSTOMER, COL_OUTSTOCK_NO, COL_AMOUNT, COL_INVOICE_NO,
                    COL_INVOICE_DATE, COL_DIGEST, COL_RANGE_EXCEPT,
                    COL_DRAWER, COL_BIZ_DATE,
                    COL_AR_NO, COL_INV_BILL_NO, COL_VOUCHER_NO,
                    COL_STATUS, COL_ERROR)
from steps import KingdeeUI

CFG = load_config()

# 生成凭证之后清场：关掉所有标签，但**保留**这些（用户口径：留销售出库单列表，下一组起点）
KEEP_TABS = ["销售出库单列表"]
# 是否启用清场（CLI --no-clean-tabs 可临时关掉）
CLEAN_TABS = [bool((CFG.get("safety", {}) or {}).get("clean_tabs_after_voucher", True))]


def build_digest(row, cfg):
    """生成摘要：Excel「摘要」列优先，否则用配置模板，都没有就留空并提示"""
    d = row.get(COL_DIGEST)
    if d is not None and str(d).strip():
        return str(d).strip()
    tpl = (cfg.get("rules", {}) or {}).get("digest_template", "")
    if tpl:
        return (tpl.replace("{客户}", str(row.get(COL_CUSTOMER) or ""))
                   .replace("{发票号}", str(row.get(COL_INVOICE_NO) or ""))
                   .replace("{月份}", str(datetime.date.today().month)))
    return ""


def plan(rows, log):
    """只做数据解析和分组，打印执行计划（不碰浏览器）"""
    groups = group_by_invoice(rows)
    log.info(f"共读入 {len(rows)} 行，分成 {len(groups)} 组（每组 = 一张金蝶发票）")
    log.info("=" * 78)
    warn_n = 0
    for i, (key, grp) in enumerate(groups, 1):
        basis, keyval = key
        try:
            s, e, src = resolve_range(grp[0], CFG)
        except ValueError as ex:
            log.err(str(ex))
            s = e = None
            src = "解析失败"
        total = sum(float(r.get(COL_AMOUNT) or 0) for r in grp)
        cust = grp[0].get(COL_CUSTOMER) or ""
        log.info(f"第 {i} 组  [{basis}] {keyval}")
        log.info(f"         客户={cust}  开票人={grp[0].get(COL_DRAWER) or '(空)'}"
                 f"  业务日期={grp[0].get(COL_BIZ_DATE) or '(空)'}"
                 f"  发票日期={grp[0].get(COL_INVOICE_DATE) or '(空)'}")
        log.info(f"         出库单 {len(grp)} 张: "
                 f"{', '.join(str(r.get(COL_OUTSTOCK_NO)) for r in grp)}")
        log.info(f"         出库单日期区间 {s} ~ {e}   [{src}]")
        log.info(f"         价税合计合计 {total:,.2f}")

        # --- 组内一致性校验 ---
        if len({str(r.get(COL_CUSTOMER) or "") for r in grp}) > 1:
            log.warn("         !! 组内客户不一致，合并开票会出问题"); warn_n += 1
        if len({str(r.get(COL_DRAWER) or "") for r in grp}) > 1:
            log.warn("         !! 组内开票人不一致，取第一行"); warn_n += 1
        if len({str(r.get(COL_BIZ_DATE) or "") for r in grp}) > 1:
            log.warn("         !! 组内业务日期不一致，取第一行"); warn_n += 1
        if len({str(r.get(COL_INVOICE_DATE) or "") for r in grp}) > 1:
            log.warn("         !! 组内发票日期不一致，取第一行"); warn_n += 1
        ranges = set()
        for r in grp:
            try:
                ranges.add(resolve_range(r, CFG)[:2])
            except ValueError:
                pass
        if len(ranges) > 1:
            log.warn(f"         !! 组内出库单日期区间不一致 {ranges}，需人工确认"); warn_n += 1
        for r in grp:
            if not r.get(COL_DRAWER) or not r.get(COL_BIZ_DATE) or not r.get(COL_INVOICE_DATE):
                log.warn(f"         !! 行 {r['__row__']} 缺 开票人/业务日期/发票日期"); warn_n += 1
                break
        log.info("-" * 78)

    if warn_n:
        log.warn(f"共 {warn_n} 处需要留意，请核对上面的 !! 标记")
    else:
        log.ok("分组与必填字段校验通过")
    return groups


def process_group(ui, key, grp, log, results):
    """处理一组（一张金蝶发票）"""
    basis, keyval = key
    inv_no = str(grp[0].get(COL_INVOICE_NO) or "").strip()
    cust = grp[0].get(COL_CUSTOMER) or ""
    log.info(f"=== 开始处理组 [{basis}] {keyval} 客户={cust} ===")
    log.info(f"    将填入金蝶 -> 开票人={grp[0].get(COL_DRAWER)} "
             f"业务日期={grp[0].get(COL_BIZ_DATE)} 发票日期={grp[0].get(COL_INVOICE_DATE)}"
             + (f" 发票号={inv_no}" if inv_no else " 发票号=(不填)"))

    s, e, src = resolve_range(grp[0], CFG)
    log.info(f"日期区间 {s} ~ {e} [{src}]")

    bill_nos = [str(r.get(COL_OUTSTOCK_NO)).strip() for r in grp if r.get(COL_OUTSTOCK_NO)]
    if not bill_nos:
        raise ValueError(f"组 {key} 没有出库单号")

    # ---- 步骤 0：出库单列表筛选 ----
    ui.open_outstock_list()
    ui.set_date_filter(s, e)
    ui.select_rows(bill_nos)

    # ---- 步骤 1：下推应收单 ----
    ui.push_to_receivable(merge=True)

    # 业务日期：Excel「业务日期」列优先，否则按 config 规则（上月最后一天）
    biz_raw = grp[0].get(COL_BIZ_DATE)
    if biz_raw:
        biz = to_date(biz_raw)
        biz_why = "Excel 指定"
    else:
        biz, biz_why = resolve_biz_date(CFG)
    if biz:
        ui.fill_bill_field("FDATE", f"{biz:%Y-%m-%d}")
        log.info(f"应收单业务日期设为 {biz:%Y-%m-%d} [{biz_why}]"
                 f"（到期日 FENDDATE_H 会随之自动联动）")

    for r in grp:
        ui.fill_receivable_fields(r)
        ui.save_bill()
        ui.submit_bill()   # 受护栏控制，默认停在这里
        # 注：发票单据实测「提交即审核」，应收单是否同样待验证；
        #     若同样，这里再点「审核」会报"单据已审核"，应删掉
        ui.audit_bill()    # 受护栏控制
        # TODO(采集后填)：把生成的应收单号读回来写进 results

    # ---- 步骤 2：应收单下推增值税专用发票（分录合并）----
    ui.open_receivable_list()
    ui.select_rows([])  # TODO：改为勾选上一步生成的应收单号
    ui.push_to_invoice(merge=True)          # 已确认：分录合并
    # 填这四项：开票人 / 复核人 / 业务日期 / 发票日期（发票号码不填）
    ui.backfill_invoice_info(
        drawer=grp[0].get(COL_DRAWER),
        biz_date=grp[0].get(COL_BIZ_DATE),
        invoice_date=grp[0].get(COL_INVOICE_DATE),
        checker=(CFG.get("rules", {}) or {}).get("invoice_checker") or "姚玉平",
    )
    expect = sum(float(r.get(COL_AMOUNT) or 0) for r in grp)
    ui.verify_amount(expect)
    ui.save_bill()
    # ⚠️ 实测（2026-09-18）：本组织「提交」= 提交即审核通过，
    #    服务端直接回「审核成功」，单据变已审核 → 不要再单独点「审核」（会报已审核）
    ui.submit_bill()

    # ---- 步骤 3：生成凭证 ----
    # ★用户口径 + 2026-09-21 实测：凭证**只能在应收单列表里生成**，
    #   单据详情页里点「凭证」没有任何入口。
    #   列表工具栏「凭证」是 split 按钮 → 下拉选「生成凭证」→ 弹出凭证生成对话框
    #   → 【必须先勾选账簿行】，否则只报「请选择账簿！」→ 点「凭证生成」
    #   → 自动打开「凭证生成报告列表」，逐行看提示类型是否「错误」
    ui.open_receivable_list()
    ui.clear_selection()
    # TODO(待补)：把上一步保存后读回的应收单号传进来（目前 select_rows 需要单号）
    #   ui.select_rows([ar_nos...])
    ui.generate_voucher()
    errs = ui.voucher_report_errors()
    if errs:
        for e in errs:
            log.err(f"凭证生成失败 {e.get('单据编号')} [{e.get('检查项')}] {e.get('提示信息')}")
        raise RuntimeError("凭证生成报告里有错误，停止（不要继续改摘要）")
    log.ok("凭证生成报告无错误")

    # ---- 步骤 4：改摘要（凭证已生成，在凭证界面改）----
    digest = build_digest(grp[0], CFG)
    if digest:
        ui.edit_digest(digest)
        ui.save_voucher()
    else:
        log.warn("摘要为空（Excel 未填且 config 无模板），跳过改摘要，需人工补")

    # ---- 步骤 5：★凭证做完 → 清标签，只留「销售出库单列表」 ----
    # 用户口径（2026-09-21）：**生成凭证之后**（本链最后一步）把开出来的一堆标签都关掉，
    # 只保留「销售出库单列表」。为什么要清：金蝶 SPA 把每个开过的页面 DOM 都留在
    # document 里，标签越堆越多，脚本越容易点到「隐藏页面」的按钮/列表。
    if CLEAN_TABS[0]:
        closed = ui.close_all_tabs(keep_names=KEEP_TABS, quiet=True)
        log.info(f"清标签：关掉 {len(closed)} 个 -> {closed}")
        # 从搜索打开功能是"替换当前标签"，所以出库单列表可能已被顶掉；不在就重开一个
        if not any(k in (t.get("text") or "") for t in ui.tabs() for k in KEEP_TABS):
            ui.open_outstock_list()
        log.ok(f"清标签后只剩：{[t.get('text') for t in ui.tabs()]}")

    # 回写
    for r in grp:
        results.setdefault(r["__row__"], {})[COL_STATUS] = "完成"
    log.ok(f"组 {key} 处理完成")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="只打印执行计划")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 组")
    ap.add_argument("--excel", default=None, help="指定 Excel 路径")
    ap.add_argument("--no-clean-tabs", action="store_true",
                    help="生成凭证后不清标签页（默认清，只留销售出库单列表）")
    args = ap.parse_args()

    if args.no_clean_tabs:
        CLEAN_TABS[0] = False

    log = Logger(abs_path("logs", CFG), name="run")
    results = {}

    try:
        excel = Path(args.excel) if args.excel else abs_path("excel", CFG)
        if not excel.exists():
            log.err(f"Excel 不存在：{excel}")
            log.err("请先运行 make_template.py 生成模板，或把数据放到 data/开票数据.xlsx")
            return 1

        _, rows = read_excel(excel, CFG["paths"].get("sheet"))
        if not rows:
            log.err("Excel 里没有数据行")
            return 1

        groups = plan(rows, log)

        if args.plan:
            log.ok("--plan 模式，不连接浏览器，到此结束")
            return 0

        # ---- 正式跑 ----
        from playwright.sync_api import sync_playwright
        guard = Guard(CFG, log)
        shot_dir = abs_path("screenshots", CFG)

        with sync_playwright() as p:
            log.info(f"连接调试浏览器 {CFG['browser']['cdp_url']}")
            try:
                browser = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
            except Exception as e:
                log.err(f"连接失败：{e}")
                log.err("请先运行「启动调试浏览器.bat」，并在里面登录金蝶")
                return 1

            pages = [pg for c in browser.contexts for pg in c.pages]
            if not pages:
                log.err("调试浏览器里没有打开的标签页")
                return 1
            page = pages[0]
            log.info(f"接管页面：{page.title()[:40]!r}  {page.url[:90]}")

            ui = KingdeeUI(page, CFG, log, guard)

            start = int(CFG["safety"].get("start_row", 1) or 1)
            maxrows = int(CFG["safety"].get("max_rows", 0) or 0)
            todo = groups[start - 1:] if start > 1 else groups
            if maxrows:
                todo = todo[:maxrows]
            if args.limit:
                todo = todo[:args.limit]

            log.info(f"本次将处理 {len(todo)} 组")

            for i, (key, grp) in enumerate(todo, 1):
                log.info(f"########## 第 {i}/{len(todo)} 组 ##########")
                try:
                    process_group(ui, key, grp, log, results)
                except GuardError:
                    log.err("触发安全护栏，停止。请人工在金蝶里核对已生成的单据后再继续。")
                    break
                except NotImplementedError as e:
                    log.err(f"步骤尚未实现（需要先做现场采集）：{e}")
                    break
                except Exception as e:
                    log.err(f"处理失败：{type(e).__name__}: {e}")
                    if CFG["safety"].get("stop_on_error", True):
                        shot(page, shot_dir, f"ERROR_组{i}", log)
                        log.err("按配置 stop_on_error=true，停止运行")
                        break

            browser.close()

        # 回写结果
        if results:
            out = abs_path("result", CFG)
            write_back(excel, results, CFG["paths"].get("sheet"))
            log.ok(f"结果已回写到 {excel.name}")

        log.ok("全部结束")
        return 0
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
