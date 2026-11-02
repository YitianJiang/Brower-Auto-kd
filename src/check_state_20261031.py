# -*- coding: utf-8 -*-
"""只读探针：核实 2026-10-31 批次里失败两家的实际进度（不写任何数据）。

用法：python src/check_state_20261031.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config  # noqa: E402
from kdquery import KdQuery, num  # noqa: E402

CFG = load_config()
CHECK = [
    ("安庆市三环康泰纸塑有限公司", "2026-09-26", "2026-10-25", ["AR105-202610-0093"]),
    ("台州森恒包装有限公司", "2026-09-26", "2026-10-25", []),
]

with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    kq = KdQuery(br.contexts[0].pages[0])

    for cust, d1, d2, known_ar in CHECK:
        print("=" * 90)
        print(cust)

        # 1) 出库单
        outs = kq.rows("SAL_OUTSTOCK", "FBillNo,FDate,FCustomerID.FName,FBILLALLAMOUNT",
                       f"FCustomerID.FName = '{cust}' and FDocumentStatus = 'C' "
                       f"and FDate >= '{d1}' and FDate <= '{d2}'", top=200)
        outs = [r for r in outs if "-109-" not in str(r.get("FBillNo"))]
        print(f"  出库单 {len(outs)} 张，合计 {sum(num(r.get('FBILLALLAMOUNT')) for r in outs):,.2f}")
        bills = sorted({str(r["FBillNo"]) for r in outs})
        for b in bills:
            print("    ", b)

        # 2) 应收单（按来源出库单精确反查 + 已知单号）
        ars = set(known_ar)
        for b in bills:
            try:
                ars |= set(kq.ar_by_source_bill(b))
            except Exception as e:  # noqa: BLE001
                print(f"    !! ar_by_source_bill({b}) 失败 {type(e).__name__}")
        ars = sorted(ars)
        print(f"  应收单：{ars or '无'}")
        for a in ars:
            h = kq.ar_head(a)
            if h:
                h = h[0]
                print(f"    {a} 状态={h.get('FDocumentStatus')} 日期={str(h.get('FDATE'))[:10]} "
                      f"价税={num(h.get('FAllAmountFor')):,.2f} 客户={h.get('FCustomerId.FName')}")
                sub = kq.ar_children(a)
                if sub:
                    print(f"      子单：{[x.get('FBillNo') for x in sub]}")

        # 3) 专票（来源应收单）
        for a in ars:
            iv = kq.rows("IV_SALESIC", "FBillNo,FSrcBillNo,FDocumentStatus,FAllAmount",
                         f"FSrcBillNo = '{a}'", top=50)
            print(f"  专票(来源 {a})：{[(x.get('FBillNo'), x.get('FDocumentStatus'), num(x.get('FAllAmount'))) for x in iv] or '无'}")

        # 4) 凭证（按日期 + 摘要含客户名）
        vs = kq.rows("GL_VOUCHER",
                     "FBillNo,FDate,FDocumentStatus,FEXPLANATION,"
                     "FACCOUNTID.FNumber,FDEBIT,FCREDIT",
                     f"FEXPLANATION like '%{cust}%' and FDATE >= '2026-10-01'", top=200)
        fids = sorted({str(x.get("FBillNo")) for x in vs})
        print(f"  凭证（摘要含客户名）：{len(fids)} 张 {fids}")

    br.close()
print("=" * 90)
print("只读核实完成")
