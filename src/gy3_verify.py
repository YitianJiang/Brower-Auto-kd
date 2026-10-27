# -*- coding: utf-8 -*-
"""
广源包装集团 2026-09 链 · 核对器（全程只读）
==========================================
一条命令看清：应收单 → 专票 → 子单 → 金额对照。
用于每一步之后验收（尤其是「有没有多出 -00000N 子单」）。

用法：
    python src/gy3_verify.py                          # 自动挑最近的广源应收单
    python src/gy3_verify.py --ar-no AR105-202609-0005
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger  # noqa: E402
from kdquery import KdQuery, num  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_verify")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ar-no", default="")
    args = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        kq = KdQuery(pg)

        ar_no = args.ar_no.strip()
        if not ar_no:
            rs = kq.rows("AR_receivable",
                         "FBillNo,FCreateDate,FCustomerId.FName",
                         "FBillNo like 'AR105-202609-%'", top=80)
            rs = [r for r in rs if "广源包装集团" in str(r.get("FCustomerId.FName") or "")]
            if not rs:
                raise SystemExit("没找到广源的应收单")
            rs.sort(key=lambda r: str(r.get("FCreateDate")))
            ar_no = str(rs[-1]["FBillNo"])
        log.info(f"应收单 = {ar_no}")

        log.info("===== ① 应收单单头 =====")
        for h in kq.rows("AR_receivable",
                         "FBillNo,FDATE,FENDDATE_H,FCustomerId.FName,FAllAmountFor,"
                         "FNOTAXAMOUNT,FTaxAmount,FDocumentStatus,FCreateDate",
                         f"FBillNo = '{ar_no}'"):
            log.info(f"  {h.get('FBillNo')}  客户={h.get('FCustomerId.FName')}")
            log.info(f"  业务日期={h.get('FDATE')}  到期日={h.get('FENDDATE_H')}  "
                     f"状态={h.get('FDocumentStatus')}  创建={h.get('FCreateDate')}")
            log.info(f"  价税={num(h.get('FAllAmountFor')):.2f}  "
                     f"不含税={num(h.get('FNOTAXAMOUNT')):.2f}  "
                     f"税额={num(h.get('FTaxAmount')):.2f}")

        log.info("===== ② 应收单明细（逐行）=====")
        ent = kq.rows("AR_receivable",
                      "FMaterialId.FName,FPriceQty,FDiscountAmountFor,"
                      "FNOTAXAMOUNTFOR_D,FTAXAMOUNTFOR_D,FALLAMOUNTFOR_D",
                      f"FBillNo = '{ar_no}'", top=200)
        log.info(f"  {len(ent)} 行；折扣合计={sum(num(e.get('FDiscountAmountFor')) for e in ent):.2f}"
                 f"  不含税合计={sum(num(e.get('FNOTAXAMOUNTFOR_D')) for e in ent):.2f}"
                 f"  税额合计={sum(num(e.get('FTAXAMOUNTFOR_D')) for e in ent):.2f}"
                 f"  价税合计={sum(num(e.get('FALLAMOUNTFOR_D')) for e in ent):.2f}")

        log.info("===== ③ 子单核查（应为 0 张）=====")
        kids = kq.rows("AR_receivable",
                       "FBillNo,FAllAmountFor,FCreateDate,FDocumentStatus",
                       f"FBillNo like '{ar_no}-%'", top=50)
        if not kids:
            log.ok("★ 子单数 = 0 —— 没有产生 -00000N 子单")
        else:
            log.warn(f"发现 {len(kids)} 张子单：")
            for k in kids:
                log.info(f"    {k.get('FBillNo')}  {num(k.get('FAllAmountFor')):+.2f}  "
                         f"{k.get('FCreateDate')}")

        log.info("===== ④ 专票 =====")
        ivs = kq.rows("IV_SALESIC",
                      "FBillNo,FDATE,FINVOICEDATE,FDocumentStatus,FAllAmount,"
                      "FNotaxAmount,FSrcBillNo",
                      f"FSrcBillNo = '{ar_no}'", top=20)
        for i in ivs:
            log.info(f"  {i.get('FBillNo')}  业务日期={i.get('FDATE')}  "
                     f"发票日期={i.get('FINVOICEDATE')}  状态={i.get('FDocumentStatus')}  "
                     f"价税={num(i.get('FAllAmount')):.2f}  不含税={num(i.get('FNotaxAmount')):.2f}")
            n = len(kq.rows("IV_SALESIC", "FMaterialId.FName,FNotaxAmount,FAllAmount",
                            f"FBillNo = '{i.get('FBillNo')}'", top=200))
            log.info(f"     明细 {n} 行")
        if not ivs:
            log.warn("  暂未找到该应收单下推的专票")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
