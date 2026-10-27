# -*- coding: utf-8 -*-
"""
广源包装集团 2026-09 链 · 第 3 步：应收单下推专票（**分录不合并**）+ 回填两日期 + 提交
==============================================================================
口径（用户 2026-09-21）：
  * 专票的「发票日期 FINVOICEDATE」与「业务日期 FDATE」都 = 月底（2026-09-30）
  * 下推规则用「应收单 - 销售专用发票 - 分录不合并」→ 专票行数 = 应收单行数，
    逐行一一对应，不产生 -00000N 分币子单（详见 docs/广源应收单子单-原因分析.md）

用法：
    python src/gy3_3_invoice.py                    # 自动挑最近一张广源应收单
    python src/gy3_3_invoice.py --ar-no AR105-...  # 指定
    python src/gy3_3_invoice.py --step save        # 只保存不提交（默认 save）
    python src/gy3_3_invoice.py --step submit      # 保存 + 提交（提交即审核）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, shot  # noqa: E402
from steps import KingdeeUI  # noqa: E402
from kdquery import KdQuery, num  # noqa: E402
from discount import to_d  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_3_invoice")
SHOT_DIR = abs_path("screenshots", CFG)

MONTH_END = "2026-09-30"


def norm(v):
    s = str(v or "").strip().replace("/", "-")
    p = s.split("-")
    if len(p) == 3 and all(x.isdigit() for x in p):
        return f"{int(p[0]):04d}-{int(p[1]):02d}-{int(p[2]):02d}"
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ar-no", default="")
    ap.add_argument("--month-end", default=MONTH_END)
    ap.add_argument("--step", choices=["save", "submit"], default="save")
    args = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, None)
        kq = KdQuery(pg)

        # ---- 找应收单 ----
        ar_no = args.ar_no.strip()
        if not ar_no:
            rs = kq.rows("AR_receivable",
                         "FBillNo,FCreateDate,FCustomerId.FName,FAllAmountFor",
                         "FBillNo like 'AR105-202609-%'", top=50)
            rs = [r for r in rs if "广源包装集团" in str(r.get("FCustomerId.FName") or "")]
            if not rs:
                raise SystemExit("没找到广源的应收单")
            rs.sort(key=lambda r: str(r.get("FCreateDate")))
            ar_no = str(rs[-1]["FBillNo"])
            log.info(f"自动选中最近一张：{ar_no}")
        head = kq.rows("AR_receivable",
                       "FBillNo,FDATE,FENDDATE_H,FCustomerId.FName,FAllAmountFor,"
                       "FNOTAXAMOUNT,FTaxAmount,FDocumentStatus",
                       f"FBillNo = '{ar_no}'")
        for h in head:
            log.ok(f"应收单 {h.get('FBillNo')}  客户={h.get('FCustomerId.FName')}  "
                   f"业务日期={h.get('FDATE')}  到期日={h.get('FENDDATE_H')}  "
                   f"状态={h.get('FDocumentStatus')}  价税={num(h.get('FAllAmountFor')):.2f}  "
                   f"不含税={num(h.get('FNOTAXAMOUNT')):.2f}  税额={num(h.get('FTaxAmount')):.2f}")
        if not head:
            raise SystemExit(f"查不到应收单 {ar_no}")

        # 占地标签先关掉，否则下推/生成凭证会报「正在被 XX 使用」
        ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=True)

        ui.open_receivable_list()
        ui.clear_selection()
        n = ui.select_rows([ar_no])
        log.info(f"应收单列表勾选 {ar_no} → {n}")
        if n <= 0:
            raise RuntimeError("应收单没勾上，停止")

        log.info("★ 下推专票：转换规则 = 分录不合并")
        ui.push_to_invoice(merge=False)
        if not ui.activate_tab("销售增值税专用发票 - 新增", timeout_ms=12000):
            raise RuntimeError("没等到专票标签")
        pg.wait_for_timeout(2500)

        detail = ui.detail_amounts("invoice")
        log.info(f"专票明细 {len(detail)} 行  不含税 {sum(r['notax'] for r in detail):,.2f}"
                 f" / 税额 {sum(r['tax'] for r in detail):,.2f}"
                 f" / 价税 {sum(r['all'] for r in detail):,.2f}")
        for r in detail[:2]:
            log.info(f"   行 seq={r['seq']} 不含税={r['notax']} 税额={r['tax']} 价税={r['all']}")

        # ---- 回填两个日期 ----
        for fld in ("FDATE", "FINVOICEDATE"):
            rr = ui.fill_bill_field(fld, args.month_end)
            log.info(f"  {fld} ← {args.month_end}  ({rr})")
        pg.wait_for_timeout(900)
        got = ui.read_bill_fields(["FBILLNO", "FDATE", "FINVOICEDATE", "FDRAWER"])
        log.info(f"回读：{got}")
        for fld in ("FDATE", "FINVOICEDATE"):
            if norm(got.get(fld)) != norm(args.month_end):
                log.warn(f"⚠️ {fld} 回读 {got.get(fld)!r} ≠ {args.month_end}")
            else:
                log.ok(f"  {fld} = {args.month_end} ✓")

        shot(pg, SHOT_DIR, "gy3_inv_filled", log)
        ui.save_bill()
        pg.wait_for_timeout(3500)
        got = ui.read_bill_fields(["FBILLNO", "FDATE", "FINVOICEDATE"])
        log.ok(f"专票已保存：{got}")
        inv_no = str(got.get("FBILLNO") or "").strip()

        if args.step == "submit":
            ui.submit_bill()
            pg.wait_for_timeout(3000)
            log.ok("专票已提交（提交即审核）")
        shot(pg, SHOT_DIR, "gy3_inv_saved", log)

        # ---- 用查询回读确认（不依赖界面）----
        pg.wait_for_timeout(1500)
        h = kq.rows("IV_SALESIC",
                    "FBillNo,FDATE,FINVOICEDATE,FDocumentStatus,FAllAmount,FSrcBillNo",
                    f"FSrcBillNo = '{ar_no}'", top=20)
        for r in h:
            log.ok(f"专票 {r.get('FBillNo')}  业务日期={r.get('FDATE')}  "
                   f"发票日期={r.get('FINVOICEDATE')}  状态={r.get('FDocumentStatus')}  "
                   f"价税={num(r.get('FAllAmount')):.2f}  来源={r.get('FSrcBillNo')}")
        log.info(f"（界面上读到的专票号：{inv_no or '未读到'}）")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
