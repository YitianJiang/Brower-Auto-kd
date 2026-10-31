# -*- coding: utf-8 -*-
"""行数勾稽 + 单价勾稽（v3，跨单汇总）：应收单明细 vs 来源销售出库单明细。

v1 逐单比对会误报「部分下推」：同一张出库单可能被拆到多张应收单。
v2 先把**所有**应收单的明细按来源出库单号汇总，再与该出库单的明细比。
v3 增加**单价同源**判据（SF-64）。

判据：
  ① 同一源单下 Σ(应收行数) != 出库行数，或 Σ(应收数量) != 出库数量
     ⇒ 漏行 / 漏量（杰翙 AR105-202610-0018 就是这形态）。
  ② 同一 (源单, 物料) 下，逐行核「出库单价 × 本行数量 − 本行折扣」是否 ≈ 本行价税合计
     ⇒ 差 > 0.50 说明差额被摊进了金额或单价（500 元以上是摊平/改价，0.1 级是反算舍入）。

  ★ ② 是 ① 的兜底：一旦前端把「改不含税金额」改成联动反算单价，
    金额会变得自洽（Σ数量×单价 = 行金额），① 和「内部算术判据」全都失效，
    只剩「能否用来源单价解释行金额」这一条还站得住。
    实测标定：AR-202608-0088（折扣分摊反算单价）最大差 0.12 → 通过；
              AR-202607-0074（改价 3,000）、AR-202610-0018（摊平 2,400）→ 报出。

用法：python src/scan_row_mismatch.py [--month 202610]
"""
import argparse
import os
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

from common import load_config
from kdquery import KdQuery

AR_FIELDS = ("FBillNo,FMaterialId.FName,FPriceQty,FTaxPrice,"
             "FALLAMOUNTFOR_D,FDISCOUNTAMOUNTFOR,FSourceBillNo")
OS_FIELDS = "FBillNo,FMaterialId.FName,FRealQty,FTaxPrice"
LINE_TOL = 0.50


def d(v):
    return Decimal("0") if v in (None, "") else Decimal(str(v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", action="append", default=None)
    args = ap.parse_args()
    months = args.month or ["202607", "202608", "202609", "202610"]

    cfg = load_config()
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        kq = KdQuery(pg)

        bills = []
        for m in months:
            rs = kq.rows("AR_receivable", "FBillNo", f"FBillNo like 'AR105-{m}-%'", top=2000)
            bills += sorted({r["FBillNo"] for r in rs if r["FBillNo"]})
        print(f"待查 {len(bills)} 张应收单")

        agg = defaultdict(lambda: [0, Decimal("0")])      # 源单 -> [行数, 数量]
        contrib = defaultdict(list)                        # 源单 -> [(AR单号, 行数, 数量)]
        ar_px = defaultdict(set)                           # (源单, 物料) -> {应收含税单价}
        ar_lines = defaultdict(list)                       # (源单, 物料) -> [(数量, 行金额, 折扣)]
        ar_mats = defaultdict(set)                          # 源单 -> {物料}
        for i, b in enumerate(bills, 1):
            rows = kq.rows("AR_receivable", AR_FIELDS, f"FBillNo='{b}'", top=1000)
            per = defaultdict(lambda: [0, Decimal("0")])
            for r in rows:
                s = (r.get("FSourceBillNo") or "").strip()
                if not s:
                    continue
                per[s][0] += 1
                per[s][1] += d(r.get("FPriceQty"))
                mat = str(r.get("FMaterialId.FName") or "")
                ar_px[(s, mat)].add(round(float(r.get("FTaxPrice") or 0), 6))
                ar_lines[(s, mat)].append((
                    float(r.get("FPriceQty") or 0),
                    float(r.get("FALLAMOUNTFOR_D") or 0),
                    float(r.get("FDISCOUNTAMOUNTFOR") or 0),
                ))
                ar_mats[s].add(mat)
            for s, (n, q) in per.items():
                agg[s][0] += n
                agg[s][1] += q
                contrib[s].append((b, n, q))
            if i % 50 == 0:
                print(f"  ... {i}/{len(bills)}")

        srcs = sorted(agg)
        print(f"涉及来源出库单 {len(srcs)} 张，开始比对")
        bad, bad_price = [], []
        for k in range(0, len(srcs), 15):
            chunk = srcs[k:k + 15]
            cond = " or ".join(f"FBillNo='{x}'" for x in chunk)
            out = defaultdict(lambda: [0, Decimal("0")])
            os_px = defaultdict(set)
            for r in kq.rows("SAL_OUTSTOCK", OS_FIELDS, f"({cond})", top=2000):
                out[r["FBillNo"]][0] += 1
                out[r["FBillNo"]][1] += d(r.get("FRealQty"))
                os_px[(r["FBillNo"], str(r.get("FMaterialId.FName") or ""))].add(
                    round(float(r.get("FTaxPrice") or 0), 6))
            for s in chunk:
                an, aq = agg[s]
                on, oq = out.get(s, [0, Decimal("0")])
                if on and (an != on or aq != oq):
                    bad.append((s, an, aq, on, oq, contrib[s]))
                for mat in ar_mats[s]:
                    prices = os_px.get((s, mat))
                    if not prices:
                        continue
                    for q, amt, disc in ar_lines[(s, mat)]:
                        if not any(abs(p * q - disc - amt) <= LINE_TOL for p in prices):
                            bad_price.append((s, mat, q, disc, amt, sorted(prices)))
        br.close()

    print("\n=== 应收合计 vs 出库单（跨单汇总）不一致 ===")
    if not bad:
        print("  无")
    for s, an, aq, on, oq, c in sorted(bad, key=lambda x: x[2] - x[4]):
        detail = "；".join(f"{b} {n}行/{q}kg" for b, n, q in c)
        print(f"  {s}: 出库 {on}行/{oq}kg  ← 应收 {an}行/{aq}kg "
              f"（行差 {an - on:+d}，量差 {aq - oq:+}）")
        print(f"      来源：{detail}")
    print(f"\n共 {len(bad)} 张出库单对不上")

    print(f"\n=== 行金额无法由出库单价解释（容差 {LINE_TOL:.2f}）===")
    if not bad_price:
        print("  无")
    for s, mat, q, disc, amt, prices in bad_price:
        exp = "；".join(f"{p * q - disc:,.2f}" for p in prices)
        print(f"  {s} / {mat}: 数量 {q:g} 折扣 {disc:,.2f} 行金额 {amt:,.2f}"
              f"（按出库单价应为 {exp}）")
    print(f"\n共 {len(bad_price)} 行对不上")


if __name__ == "__main__":
    main()
