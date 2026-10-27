# -*- coding: utf-8 -*-
"""
只读：全批 16 家「区间内出库单金额」 vs 「Excel 目标金额」对拍。
目的：一次找出所有日期区间填错 / 单据缺失的单位。
"""
import sys
import datetime
from pathlib import Path
from decimal import Decimal
sys.path.insert(0, str(Path(__file__).resolve().parent))
import openpyxl
from playwright.sync_api import sync_playwright
from common import load_config, abs_path, Logger
from kdquery import KdQuery

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="recon_range")
XL = r"C:\Users\Administrator\Desktop\下推.xlsx"
EPOCH = datetime.date(1899, 12, 30)
D = lambda v: Decimal(str(v or 0))

wb = openpyxl.load_workbook(XL, data_only=True)
units = []
for r in list(wb.active.iter_rows(values_only=True))[1:]:
    if not r or not r[2]:
        continue
    units.append({
        "start": EPOCH + datetime.timedelta(days=int(r[0])),
        "end": EPOCH + datetime.timedelta(days=int(r[1])),
        "name": str(r[2]).strip(), "all": D(r[6]), "notax": D(r[4]), "tax": D(r[5]),
    })
inc = ",".join(f"'{u['name']}'" for u in units)

with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    pg = br.contexts[0].pages[0]
    kq = KdQuery(pg)

    log.info("探出库单【明细金额】字段…")
    fld_ok = None
    for f in ("FALLAMOUNTFOR", "FAllAmountFor", "FAllAmount", "FTAXAMOUNTFOR", "FAmount"):
        try:
            kq.rows("SAL_OUTSTOCK", f"FBillNo,{f}", "FBillNo <> ''", top=1)
            log.ok(f"  可用: {f}")
            if fld_ok is None:
                fld_ok = f
        except Exception as e:  # noqa: BLE001
            log.warn(f"  不可用: {f} → {str(e)[:80]}")
    if not fld_ok:
        log.err("没有可用的金额字段，退出")
        sys.exit(1)

    log.info("")
    log.info("=" * 104)
    log.info(f"区间内出库单 金额（{fld_ok}） vs Excel 目标价税 —— 全批 16 家")
    log.info("=" * 104)
    log.info(f"{'单位':<30}{'区间':<24}{'单':>4}{'行':>5}{'出库单价税':>15}{'Excel目标':>15}{'差额':>13}  判定")

    try:
        rows = kq.rows("SAL_OUTSTOCK", f"FBillNo,FDate1,FCustomerID.FName,{fld_ok}",
                       f"FCustomerID.FName in ({inc}) "
                       f"and FDate1 >= '2026-08-15' and FDate1 <= '2026-10-05'", top=5000)
    except Exception as e:  # noqa: BLE001
        log.err(f"查询失败: {str(e)[:200]}")
        rows = []

    by = {}
    for r in rows:
        by.setdefault(str(r.get("FCustomerID.FName")), []).append(r)

    bad = []
    for u in units:
        lst = by.get(u["name"], [])
        s, e = str(u["start"]), str(u["end"])
        inr = [x for x in lst if s <= str(x.get("FDate1"))[:10] <= e]
        bills = {str(x.get("FBillNo")) for x in inr}
        # 明细级金额 → 按单聚合同一单的多行
        per = {}
        for x in inr:
            k = str(x.get("FBillNo"))
            per[k] = per.get(k, Decimal(0)) + D(x.get(fld_ok))
        amt = sum(per.values())
        gap = amt - u["all"]
        ok = abs(gap) < Decimal("0.05") and amt > 0
        mark = "OK" if ok else ("★无单" if not bills else "★不符")
        log.info(f"{u['name']:<30}{s}~{e}  {len(bills):>4}{len(inr):>5}"
                 f"{float(amt):>15,.2f}{float(u['all']):>15,.2f}{float(gap):>13,.2f}  {mark}")
        if not ok:
            bad.append((u["name"], s, e, len(bills), float(amt), float(u["all"]), float(gap)))

    log.info("")
    if bad:
        log.warn(f"★ 有 {len(bad)} 家对不上：")
        for n, s, e, nb, a, t, g in bad:
            log.warn(f"    {n}  区间 {s}~{e}  {nb} 单  出库单 {a:,.2f} vs 目标 {t:,.2f}  差 {g:,.2f}")
    else:
        log.ok("★ 全部 16 家出库单金额与 Excel 目标完全吻合")

    # 对不上的，把该家 8/20~10/05 的出库单日期也列出来，方便改区间
    if bad:
        log.info("")
        log.info("=" * 104)
        log.info("对不上的这几家，8/20~10/05 的实际出库单：")
        log.info("=" * 104)
        for n, *_ in bad:
            log.info(f"  ▸ {n}")
            for x in sorted([y for y in by.get(n, [])], key=lambda x: str(x.get("FDate1"))):
                log.info(f"      {x.get('FBillNo')}  FDate1={str(x.get('FDate1'))[:10]}"
                         f"  金额={x.get(fld_ok)}")

    br.close()
log.close()
