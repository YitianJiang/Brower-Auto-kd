# -*- coding: utf-8 -*-
"""只读：按客户 + 业务日期找刚生成的应收单。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright
from common import load_config, abs_path, Logger
from kdquery import KdQuery
CFG = load_config(); log = Logger(abs_path("logs", CFG), name="find_ar")
with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    pg = br.contexts[0].pages[0]
    kq = KdQuery(pg)
    rs = kq.rows("AR_receivable",
                 "FBillNo,FDate,FCreateDate,FDocumentStatus,FAllAmountFor,FCustomerId.FName",
                 "FCustomerId.FName in ('慈溪市润科包装材料有限公司','杭州小章印刷材料有限公司') "
                 "and FDate >= '2026-09-01 00:00:00'", top=50)
    for r in sorted(rs, key=lambda x: str(x.get("FCreateDate"))):
        log.info(f"  {r.get('FBillNo')} 客户={r.get('FCustomerId.FName')} "
                 f"业务日={str(r.get('FDate'))[:10]} 创建={str(r.get('FCreateDate'))[:19]} "
                 f"状态={r.get('FDocumentStatus')} 价税={r.get('FAllAmountFor')}")
    br.close()
