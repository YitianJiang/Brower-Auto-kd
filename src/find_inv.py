# -*- coding: utf-8 -*-
"""只读：查最近生成的专票（按客户+创建时间），确认保存是否真的落库。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright
from common import load_config, abs_path, Logger
from kdquery import KdQuery
CFG = load_config(); log = Logger(abs_path("logs", CFG), name="find_inv")
with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    pg = br.contexts[0].pages[0]
    kq = KdQuery(pg)
    rs = kq.rows("IV_SALESIC",
                 "FBillNo,FDate,FINVOICEDATE,FCreateDate,FDocumentStatus,"
                 "FCustomerId.FName,FDRAWER,FPAYEE,F_PAEZ_COMBO",
                 "FCustomerId.FName in ('慈溪市润科包装材料有限公司','杭州小章印刷材料有限公司')",
                 top=20)
    for r in sorted(rs, key=lambda x: str(x.get("FCreateDate")), reverse=True)[:8]:
        log.info(f"  {r.get('FBillNo')} 客户={r.get('FCustomerId.FName')} "
                 f"业务日={str(r.get('FDate'))[:10]} 发票日={str(r.get('FINVOICEDATE'))[:10]} "
                 f"创建={str(r.get('FCreateDate'))[:19]} 状态={r.get('FDocumentStatus')} "
                 f"开票人={r.get('FDRAWER')} 收款人={r.get('FPAYEE')} 复核人={r.get('F_PAEZ_COMBO')}")
    # 金额（明细级求和）
    for r in sorted(rs, key=lambda x: str(x.get("FCreateDate")), reverse=True)[:1]:
        b = r.get("FBillNo")
        ent = kq.rows("IV_SALESIC", "FNotaxAmount,FTaxAmount,FAllAmount,FPriceQty",
                      f"FBillNo = '{b}'", top=200)
        nt = sum(float(e.get("FNotaxAmount") or 0) for e in ent)
        tx = sum(float(e.get("FTaxAmount") or 0) for e in ent)
        al = sum(float(e.get("FAllAmount") or 0) for e in ent)
        log.ok(f"  {b} 明细 {len(ent)} 行：不含税={nt:.2f} 税额={tx:.2f} 价税={al:.2f}")
    br.close()
