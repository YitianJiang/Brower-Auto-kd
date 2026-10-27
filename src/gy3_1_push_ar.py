# -*- coding: utf-8 -*-
"""
广源包装集团 2026-09 链 · 第 1 步：下推应收单（**不保存**）+ 摸清字段标识
=====================================================================
目的：本轮新增三条口径（业务日期/到期日=月底、发票两日期=月底、凭证摘要按
存货类别/计价数量算 kg），其中「到期日」「存货类别」「计价数量」的**字段标识未知**。
本脚本把应收单推出来停在页面上，然后 dump 单头 + 明细表头，绝不保存/提交。

用法：
    python src/gy3_1_push_ar.py --date-from 2026-08-21 --date-to 2026-09-20
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger  # noqa: E402
from steps import KingdeeUI  # noqa: E402
from push_chain import filter_outstock  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_1_push_ar")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date-from", default="2026-08-21")
    ap.add_argument("--date-to", default="2026-09-20")
    ap.add_argument("--bill-nos", default="")
    ap.add_argument("--customer", default="广源包装集团股份有限公司")
    args = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, None)

        rows, sel = filter_outstock(ui, args)

        log.info("下推应收单（整单转换，**不保存**）")
        ui.push_to_receivable(merge=True)
        if not ui.activate_tab("应收单 - 新增", timeout_ms=10000):
            raise RuntimeError("没等到「应收单 - 新增」标签")
        pg.wait_for_timeout(2500)

        d = ui.detail_amounts("receivable")
        log.info(f"应收单明细 {len(d)} 行，价税合计 {sum(r['all'] for r in d):,.2f}")

        cols = ui.dump_columns()
        log.info("===== 明细表头（field ← 标题）=====")
        for c in cols.get("grid", []):
            log.info(f"   {c['field']:<34} ← {c['label']}")
        log.info("===== 单头字段（field ← 标题）=====")
        for c in cols.get("header", []):
            log.info(f"   {c['field']:<28} ← {c['label']!r}  (id=…{c['id']})")

        log.info("===== 明细前 2 行原始字段 =====")
        raw = ui.detail_rows()
        for r in raw[:2]:
            for k, v in r.items():
                log.info(f"     {k:<34} = {v}")
            log.info("     ----")

        log.warn("停在「应收单 - 新增」，未保存未提交")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
