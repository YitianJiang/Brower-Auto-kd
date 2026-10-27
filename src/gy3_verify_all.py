# -*- coding: utf-8 -*-
"""
广源包装集团 2026-09 链 · 最终核验（只读）
==========================================
一次跑完核对全链四个环节：应付出库→应收单→专票→凭证（含摘要落地）。
用法：python src/gy3_verify_all.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, abs_path  # noqa: E402
from kdquery import KdQuery, num  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_verify_all")
AR = "AR105-202609-0005"
INV = "SVINV00033668"
DIGEST = "广源包装集团股份有限公司销售水性油墨9580公斤，光油11200公斤，原材料358号"


def main():
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        kq = KdQuery(pg)

        # 1) 应收单
        log.info("========== ① 应收单 ==========")
        for r in kq.rows("AR_receivable",
                         "FBillNo,FDATE,FENDDATE_H,FDocumentStatus,FCustomerId.FName,"
                         "FAllAmountFor,FNOTAXAMOUNT,FTaxAmount,FCreateDate",
                         f"FBillNo = '{AR}'"):
            log.ok(f"  {r.get('FBillNo')} 客户={r.get('FCustomerId.FName')} "
                   f"业务日={str(r.get('FDATE'))[:10]} 到期日={str(r.get('FENDDATE_H'))[:10]} "
                   f"状态={r.get('FDocumentStatus')}")
            log.ok(f"  不含税={num(r.get('FNOTAXAMOUNT')):,.2f} "
                   f"税额={num(r.get('FTaxAmount')):,.2f} "
                   f"价税={num(r.get('FAllAmountFor')):,.2f}")

        ent = kq.rows("AR_receivable",
                      "FMaterialId.FName,FPriceQty,FNOTAXAMOUNTFOR_D,FALLAMOUNTFOR_D",
                      f"FBillNo = '{AR}'", top=200)
        a = sum(float(x.get("FNOTAXAMOUNTFOR_D") or 0) for x in ent)
        g = sum(float(x.get("FALLAMOUNTFOR_D") or 0) for x in ent)
        log.ok(f"  明细 {len(ent)} 行：不含税合计 {a:,.2f} / 价税合计 {g:,.2f}")

        # 2) 子单
        log.info("========== ② 子单核查 ==========")
        kids = kq.rows("AR_receivable", "FBillNo,FAllAmountFor",
                       f"FBillNo like '{AR}-%'", top=20)
        if not kids:
            log.ok(f"  ★ 子单数 = 0（没有 -00000N 分币子单）")
        else:
            for k in kids:
                log.warn(f"  {k.get('FBillNo')} {num(k.get('FAllAmountFor')):+.2f}")

        # 3) 专票
        log.info("========== ③ 专票 ==========")
        for r in kq.rows("IV_SALESIC",
                         "FBillNo,FDATE,FINVOICEDATE,FDocumentStatus,FCustomerId.FName,"
                         "FDRAWER,FPAYEE,F_PAEZ_COMBO,FSrcBillNo",
                         f"FBillNo = '{INV}'", top=3)[:1]:
            log.ok(f"  {r.get('FBillNo')} 客户={r.get('FCustomerId.FName')} "
                   f"业务日={str(r.get('FDATE'))[:10]} 发票日={str(r.get('FINVOICEDATE'))[:10]} "
                   f"状态={r.get('FDocumentStatus')}（C=已审核）")
            log.ok(f"  开票人={r.get('FDRAWER')} 收款人={r.get('FPAYEE')} "
                   f"复核人={r.get('F_PAEZ_COMBO')} 来源={r.get('FSrcBillNo')}")
        ient = kq.rows("IV_SALESIC",
                       "FMaterialId.FName,FNotaxAmount,FDETAILTAXAMOUNT,FAllAmount",
                       f"FBillNo = '{INV}'", top=200)
        ia = sum(float(x.get("FNotaxAmount") or 0) for x in ient)
        it = sum(float(x.get("FDETAILTAXAMOUNT") or 0) for x in ient)
        ig = sum(float(x.get("FAllAmount") or 0) for x in ient)
        log.ok(f"  明细 {len(ient)} 行：不含税 {ia:,.2f} / 税额 {it:,.2f} / 价税 {ig:,.2f}")

        # 4) 凭证
        log.info("========== ④ 总账凭证 ==========")
        rows = kq.rows("GL_VOUCHER",
                       "FBillNo,FDATE,FEXPLANATION,FACCOUNTID.FName,FDEBIT,FCREDIT",
                       "FEXPLANATION like '%水性油墨%' and FEXPLANATION like '%广源包装集团%'",
                       top=50)
        if not rows:
            log.warn("  没查到含新摘要的凭证分录")
        seen = set()
        dr = cr = 0.0
        for r in rows:
            key = str(r.get("FBillNo"))
            seen.add(key)
            dr += float(r.get("FDEBIT") or 0)
            cr += float(r.get("FCREDIT") or 0)
            log.ok(f"  {r.get('FBillNo')} {str(r.get('FDATE'))[:10]} "
                   f"科目={r.get('FACCOUNTID.FName')} 借={r.get('FDEBIT')} 贷={r.get('FCREDIT')}")
            log.info(f"      摘要={r.get('FEXPLANATION')!r}")
        log.ok(f"  凭证号 {sorted(seen)}  借方合计 {dr:,.2f} / 贷方合计 {cr:,.2f} "
               f"{'平衡 ✓' if abs(dr - cr) < 0.01 else '不平 ✗'}")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
