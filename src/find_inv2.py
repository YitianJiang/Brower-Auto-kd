# -*- coding: utf-8 -*-
"""只读：专票是否落库（放大 top，按创建时间倒序）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright
from common import load_config, abs_path, Logger
from kdquery import KdQuery
CFG = load_config(); log = Logger(abs_path("logs", CFG), name="find_inv2")
with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    pg = br.contexts[0].pages[0]
    kq = KdQuery(pg)
    allrs = []
    for c in ("慈溪市润科包装材料有限公司", "杭州小章印刷材料有限公司"):
        rs = kq.rows("IV_SALESIC",
                     "FBillNo,FDate,FINVOICEDATE,FCreateDate,FDocumentStatus,"
                     "FCustomerId.FName,FDRAWER,FPAYEE,F_PAEZ_COMBO",
                     f"FCustomerId.FName = '{c}'", top=300)
        log.info(f"{c}: {len(rs)} 张")
        allrs += rs
    for r in sorted(allrs, key=lambda x: str(x.get("FCreateDate")), reverse=True)[:6]:
        log.info(f"  {r.get('FBillNo')} 客户={str(r.get('FCustomerId.FName'))[:12]} "
                 f"业务日={str(r.get('FDate'))[:10]} 发票日={str(r.get('FINVOICEDATE'))[:10]} "
                 f"创建={str(r.get('FCreateDate'))[:19]} 状态={r.get('FDocumentStatus')} "
                 f"复核人={r.get('F_PAEZ_COMBO')}")
    br.close()
