# -*- coding: utf-8 -*-
"""只读预检：核实 2026-11-02 待重推的 14 张应收单当前状态（零副作用）。

背景：10 月杭州账套 8 张专票漏行（少开 69,836.75），用户已**手工删除**这 8 张专票
及其 8 个凭证；另有 6 张标准应收单当时就没推过票/凭证。现在要重推这 14 张。

本脚本只做服务端反证（ExecuteBillQuery）：
  ① 应收单是否还在、状态、金额、明细行数
  ② 是否真的没有专票（IV_SALESIC.FSrcBillNo 反查）
  ③ 是否真的没有凭证（GL_VOUCHER 1122 借方 = 该单价税，且日期 >= 2026-10-01）
任一不满足 → 打印 ⚠️ 并以退出码 1 结束，**不推**。

用法：python src/verify_repush_20261102.py
"""
import csv
import sys
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config  # noqa: E402
from kdquery import KdQuery, num  # noqa: E402

# (应收单号, 客户, 专票业务日期, 预期价税合计, 来源标记)
ITEMS = [
    ("AR105-202610-0003", "宁波日和新材料有限公司", "2026-10-31", "60926.48", "重推-漏行"),
    ("AR105-202610-0014", "常熟市双乐彩印包装有限公司", "2026-10-31", "26620.00", "重推-漏行"),
    ("AR105-202610-0015", "杭州嘉顺印务有限公司", "2026-10-31", "134300.00", "重推-漏行"),
    ("AR105-202610-0019", "杭州墨科包装材料有限公司", "2026-10-31", "15457.00", "重推-漏行"),
    ("AR105-202610-0022", "南通楚宏纸业有限公司", "2026-10-31", "10360.00", "重推-漏行"),
    ("AR105-202610-0031", "杭州奥科包装材料有限公司", "2026-10-31", "9000.00", "重推-漏行"),
    ("AR105-202610-0033", "南京盛溪印刷包装有限公司", "2026-10-31", "53034.00", "重推-漏行"),
    ("AR105-202610-0034", "浙江楼鑫包装科技有限公司", "2026-10-31", "1800.00", "重推-漏行"),
    ("AR105-202610-0066", "淮安秉信环保包装有限公司", "2026-10-31", "339903.20", "补推-原本无票"),
    ("AR105-202610-0076", "马鞍山品尚智包包装服务有限公司", "2026-10-30", "2065.59", "补推-原本无票"),
    ("AR105-202610-0077", "浙江赛欧包装有限公司", "2026-10-31", "115000.00", "补推-原本无票"),
    ("AR105-202610-0078", "浙江赛欧包装有限公司", "2026-10-30", "124000.00", "补推-原本无票"),
    ("AR105-202610-0079", "杭州秉信环保包装有限公司", "2026-10-30", "727708.55", "补推-原本无票"),
    ("AR105-202610-0080", "郑州秉信包装有限公司", "2026-10-30", "319345.79", "补推-原本无票"),
]
OUT = ROOT / "output" / "repush_check_20261102.csv"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ars", default="", help="只核这几张（逗号分隔），用于分批重推的第 2 批")
    ap.add_argument("--out", default="", help="输出 CSV 路径（默认 output/repush_check_20261102.csv）")
    a = ap.parse_args()
    items = ITEMS
    if a.ars:
        want = {x.strip() for x in a.ars.split(",") if x.strip()}
        items = [x for x in ITEMS if x[0] in want]
        if not items:
            print(f"--ars {a.ars!r} 不在清单里")
            return 2
    out_path = Path(a.out) if a.out else OUT

    cfg = load_config()
    ars = [x[0] for x in items]
    q = ",".join(f"'{a}'" for a in ars)
    heads, det_n, iv, vch = {}, {}, {}, {}

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])

        # ① 应收单头
        for r in kq.rows("AR_receivable",
                         "FBillNo,FDocumentStatus,FDate,FAllAmountFor,FCustomerID.FName",
                         f"FBillNo in ({q})", top=100):
            heads[str(r["FBillNo"])] = r

        # ② 明细行数（按主键一次取，SF-73 两阶段：先主键，不带 LIKE）
        for r in kq.rows("AR_receivable",
                         "FBillNo,FMaterialId.FName,FPriceQty,FALLAMOUNTFOR_D",
                         f"FBillNo in ({q})", top=5000):
            det_n[str(r["FBillNo"])] = det_n.get(str(r["FBillNo"]), 0) + 1

        # ③ 专票（应为空）
        for r in kq.rows("IV_SALESIC",
                         "FBillNo,FSrcBillNo,FDocumentStatus,FAllAmount",
                         f"FSrcBillNo in ({q})", top=500):
            iv.setdefault(str(r.get("FSrcBillNo")), []).append(
                (r.get("FBillNo"), r.get("FDocumentStatus"), num(r.get("FAllAmount"))))

        # ④ 凭证：应收账款 1122 借方 = 该单价税，10 月以后
        for r in kq.rows("GL_VOUCHER",
                         "FBillNo,FDate,FVOUCHERGROUPNO,FDocumentStatus,FEXPLANATION,"
                         "FACCOUNTID.FNumber,FDEBIT,FCREDIT",
                         "FACCOUNTID.FNumber = '1122' and FDEBIT > 0 and FDATE >= '2026-10-01'",
                         top=3000):
            amt = D(str(num(r.get("FDEBIT")))).quantize(D("0.01"))
            vch.setdefault(str(amt), []).append(
                (r.get("FBillNo"), str(r.get("FDate"))[:10], str(r.get("FEXPLANATION"))[:24]))
        br.close()

    rows, bad = [], 0
    print(f"{'应收单':<20}{'状态':<4}{'明细':>4}  {'预期价税':>12}  {'实际价税':>12}  "
          f"{'专票':<6}{'凭证':<6}  结论")
    print("-" * 108)
    for ar, cust, bdate, amt, tag in items:
        h = heads.get(ar)
        st = (h or {}).get("FDocumentStatus") or "-"
        got = D(str(num((h or {}).get("FAllAmountFor")))).quantize(D("0.01"))
        want = D(amt)
        n_det = det_n.get(ar, 0)
        ivs = iv.get(ar) or []
        vs = vch.get(str(want), [])
        ok = bool(h) and st == "C" and got == want and n_det > 0 and not ivs and not vs
        if not ok:
            bad += 1
        print(f"{ar:<20}{st:<4}{n_det:>4}  {want:>12}  {got:>12}  "
              f"{len(ivs):<6}{len(vs):<6}  {'✅ 可推' if ok else '⚠️ 需人工看'}"
              + ("" if not ivs else f" 票:{[x[0] for x in ivs]}"))
        rows.append({"ar": ar, "customer": cust, "biz_date": bdate, "amount": str(want),
                     "tag": tag, "ar_status": st, "ar_det_lines": n_det,
                     "ar_amount_now": str(got), "iv": ";".join(x[0] for x in ivs) or "",
                     "vch": ";".join(f"{x[0]}/{x[1]}" for x in vs) or "",
                     "ready": "Y" if ok else "N"})

    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("-" * 108)
    print(f"合计 {len(items)} 张，价税 {sum(D(x[3]) for x in items):,.2f}；"
          f"可推 {len(items) - bad} 张，需人工 {bad} 张")
    print(f"明细已写入 {out_path}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
