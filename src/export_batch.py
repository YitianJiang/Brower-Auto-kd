# -*- coding: utf-8 -*-
"""
导出本批 13 家的「精确出库单号 + 折扣 + 目标金额」清单（只读）。
输出 output/batch_20261003.json，并打印每家应执行的 push_chain 命令。
清单口径（用户 2026-10-03 决定）：
  - 跳过：江西英彩（疑似已推）、扬州祥恒（疑似已推）、浙江三阳（金额差 4000 待查）
  - 前 4 家异常单位改用「金额对拍命中的那一张（或两张）单」
  - 日期一律 2026-09-30；下推用 --bill-nos 精确指定，绝不按日期区间筛
"""
import sys
import json
import datetime
from pathlib import Path
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright
from common import load_config, abs_path, Logger
from kdquery import KdQuery

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="export_batch")
ROOT = Path(__file__).resolve().parent.parent

# 人工确认过的 4 家（金额对拍命中的单）
FIXED = {
    "马鞍山祥恒包装有限公司": ["XSCKD-105-202609-0086"],
    "上海慧雄包装有限公司": ["XSCKD-105-202609-0181"],
    "祥恒（常州）包装有限公司": ["XSCKD-105-202608-0141"],
    "宁波市黎春包装有限公司": ["XSCKD-105-202609-0188", "XSCKD-105-202609-0186"],
}
# 跳过
SKIP = {"江西英彩科技有限公司", "扬州祥恒包装有限公司", "浙江三阳包装产业股份有限公司"}

MASTER = [
    # 客户, 起始, 终止, 折扣, 不含税, 税额, 价税
    ("安徽济丰包装科技有限公司", "2026-08-26", "2026-09-25", "1860", "129895.27", "16886.38", "146781.65"),
    ("安徽英彩印刷科技有限公司", "2026-08-21", "2026-09-20", "22.5", "1573.01", "204.49", "1777.5"),
    ("滁州远达彩印包装有限公司", "2026-08-26", "2026-09-25", "789.36", "22586.4", "2936.24", "25522.64"),
    ("福建广源包装有限公司", "2026-08-21", "2026-09-20", "1109.5", "73030.53", "9493.97", "82524.5"),
    ("江西广源包装有限公司", "2026-08-21", "2026-09-20", "4257", "112372.27", "14608.33", "126980.6"),
    ("马鞍山祥恒包装有限公司", "2026-08-26", "2026-09-25", "708", "168773.46", "21940.56", "190714.02"),
    ("宁波日和新材料有限公司", "2026-08-20", "2026-09-19", "147", "52949.4", "6883.42", "59832.82"),
    ("宁波市黎春包装有限公司", "2026-08-26", "2026-09-25", "1652.87", "129618.38", "16850.4", "146468.78"),
    ("上海慧雄包装有限公司", "2026-08-26", "2026-09-25", "6261.16", "195181.45", "25373.59", "220555.04"),
    ("祥恒（常州）包装有限公司", "2026-08-26", "2026-09-25", "7407.85", "41359.66", "5376.74", "46736.4"),
    ("祥恒创意（重庆）新材料有限公司", "2026-08-26", "2026-09-25", "3593.5", "34491.64", "4483.91", "38975.55"),
    ("浙江广泉包装有限公司", "2026-08-21", "2026-09-20", "2502.5", "154068.61", "20028.89", "174097.5"),
    ("浙江至优环保科技有限公司", "2026-08-26", "2026-09-25", "5047.5", "84869.5", "11033", "95902.5"),
]

with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    pg = br.contexts[0].pages[0]
    kq = KdQuery(pg)

    items = []
    for cust, s, e, disc, notax, tax, all_amt in MASTER:
        if cust in FIXED:
            bills = FIXED[cust]
            src = "人工指定(金额对拍命中)"
        else:
            try:
                rs = kq.rows("SAL_OUTSTOCK", "FBillNo,FDate1,FCustomerID.FName",
                             f"FCustomerID.FName = '{cust}' "
                             f"and FDate1 >= '{s}' and FDate1 <= '{e}'", top=200)
                bills = sorted({str(r.get("FBillNo")) for r in rs})
                src = f"{s}~{e}"
            except Exception as ex:  # noqa: BLE001
                log.err(f"{cust} 查询失败: {str(ex)[:120]}")
                bills = []
                src = "查询失败"
        items.append({
            "customer": cust, "bills": bills, "discount": disc,
            "notax": notax, "tax": tax, "all": all_amt, "range": src,
        })
        flag = "★" if cust in FIXED else " "
        log.info(f"{flag} {cust:<30} {len(bills):>2} 单  {bills}  折扣={disc}  目标={all_amt}")

    br.close()

out = ROOT / "output" / "batch_20261003.json"
out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
log.ok(f"清单已写出 {out}")

log.info("")
log.info("=" * 100)
log.info("应执行的命令（每家一条，业务日=发票日=2026-09-30）")
log.info("=" * 100)
PY = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
tot_all = Decimal(0)
for it in items:
    if not it["bills"]:
        log.warn(f"# 跳过（无单）: {it['customer']}")
        continue
    tot_all += Decimal(it["all"])
    log.info(
        f'{PY} src/push_chain.py --customer "{it["customer"]}" '
        f'--bill-nos {",".join(it["bills"])} '
        f'--discount {it["discount"]} --target-notax {it["notax"]} --target-tax {it["tax"]} '
        f'--biz-date 2026-09-30 --invoice-date 2026-09-30 '
        f'--no-invoice-merge --step voucher --yes')
log.info("")
log.info(f"共 {len([i for i in items if i['bills']])} 家，价税合计目标 {tot_all:,.2f}")
log.close()
