# -*- coding: utf-8 -*-
"""扫描「补数式对齐」痕迹（SF-63）。

判据：明细行的金额字段被改过、但数量/单价没改 ⇒
       Σ(数量 × 含税单价) − Σ(折扣额) ≠ Σ(行价税合计)
杰翙 AR105-202610-0018 实测差 −2,400.00（漏了 TLA8白浆 200kg 那行）。

用法：
    python src/scan_align_suspects.py                # 默认 202609 + 202610
    python src/scan_align_suspects.py --month 202610
    python src/scan_align_suspects.py --bill AR105-202610-0018
"""
import argparse
import csv
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

from common import load_config
from kdquery import KdQuery

ENTRY_FIELDS = ("FBillNo,FMaterialId.FName,FPriceQty,FTaxPrice,"
                "FNOTAXAMOUNTFOR_D,FALLAMOUNTFOR_D,FDISCOUNTAMOUNTFOR")
TOL = Decimal("0.05")


def d(v):
    if v is None or v == "":
        return Decimal("0")
    return Decimal(str(v))


def scan_bill(kq, bill):
    rs = kq.rows("AR_receivable", ENTRY_FIELDS, f"FBillNo='{bill}'", top=1000)
    if not rs:
        return None
    q = sum(d(r["FPriceQty"]) * d(r["FTaxPrice"]) for r in rs)
    disc = sum(d(r["FDISCOUNTAMOUNTFOR"]) for r in rs)
    all_amt = sum(d(r["FALLAMOUNTFOR_D"]) for r in rs)
    gap = q - disc - all_amt

    head = kq.rows("AR_receivable", "FBillNo,FDate,FDocumentStatus,FAllAmountFor",
                   f"FBillNo='{bill}'", top=5)
    h_all = d(head[0]["FAllAmountFor"]) if head else Decimal("0")
    return {
        "bill": bill,
        "date": (head[0]["FDate"] or "")[:10] if head else "",
        "status": head[0]["FDocumentStatus"] if head else "",
        "n": len(rs),
        "qty": sum(d(r["FPriceQty"]) for r in rs),
        "qty_x_price": q,
        "discount": disc,
        "entry_all": all_amt,
        "head_all": h_all,
        "gap": gap,
        "head_gap": h_all - all_amt,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", action="append", default=None,
                    help="应收单月份前缀，如 202610（可多次）")
    ap.add_argument("--bill", default=None)
    ap.add_argument("--out", default="output/align_scan.csv")
    args = ap.parse_args()

    cfg = load_config()
    months = args.month or ["202609", "202610"]
    rows = []
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        kq = KdQuery(pg)

        if args.bill:
            bills = [args.bill]
        else:
            bills = []
            for m in months:
                pre = f"AR105-{m}-"
                rs = kq.rows("AR_receivable", "FBillNo", f"FBillNo like '{pre}%'", top=2000)
                bills += sorted({r["FBillNo"] for r in rs if r["FBillNo"]})
            print(f"待扫应收单 {len(bills)} 张")

        for i, b in enumerate(bills, 1):
            try:
                r = scan_bill(kq, b)
            except Exception as e:
                print(f"  !! {b} 扫描失败：{type(e).__name__} {str(e)[:80]}")
                continue
            if r:
                rows.append(r)
            if i % 25 == 0:
                print(f"  ... {i}/{len(bills)}")
        br.close()

    sus = [r for r in rows if abs(r["gap"]) > TOL]
    head_bad = [r for r in rows if abs(r["head_gap"]) > TOL]
    sus.sort(key=lambda x: -abs(x["gap"]))

    print("\n=== 可疑（行金额被改、数量单价对不上）===")
    if not sus:
        print("  无")
    for r in sus:
        print(f"  {r['bill']} {r['date']} 状态{r['status']} 行{r['n']} "
              f"数量{r['qty']} Σ数量×单价={r['qty_x_price']:,.2f} "
              f"折扣{r['discount']:,.2f} 行价税合计={r['entry_all']:,.2f} "
              f"**差 {r['gap']:+,.2f}**")

    print("\n=== 明细合计 ≠ 单头价税合计 ===")
    if not head_bad:
        print("  无")
    for r in head_bad:
        print(f"  {r['bill']} 行合计 {r['entry_all']:,.2f} vs 单头 {r['head_all']:,.2f} "
              f"差 {r['head_gap']:+,.2f}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            w.writeheader()
            w.writerows(rows)
    print(f"\n明细已写入 {args.out}（共 {len(rows)} 单，可疑 {len(sus)} 单）")


if __name__ == "__main__":
    main()
