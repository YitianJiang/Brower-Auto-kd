# -*- coding: utf-8 -*-
"""
只读核查：某张应收单下面挂了几张 `-00000N` 子单（= 金蝶「应收单↔发票」分币差的载体单）。

用途：下推专票的「合并 / 不合并」对照实验里，**判定实验是否成功**。
      跑完一次链就查一次，子单数 = 0 才算真的避开了。

⚠️ 全程只读：走 ExecuteBillQuery，不打开单据、不加锁、不写任何数据。

用法：
    python src/check_subbills.py                          # 查今天新增的所有子单
    python src/check_subbills.py AR105-202609-0004        # 查这张单的子单
    python src/check_subbills.py AR105-202609-0004 --detail   # 连子单明细一起看
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, abs_path  # noqa: E402
from kdquery import KdQuery, num  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="check_subbills")


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    detail = "--detail" in sys.argv
    parent = argv[0] if argv else ""

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pages = [pg for c in br.contexts for pg in c.pages]
        if not pages:
            raise SystemExit("调试浏览器里没有页面")
        kq = KdQuery(pages[0])

        if parent:
            log.info(f"查 {parent} 名下的子单（只读）")
            kids = kq.ar_children(parent)
            head = kq.ar_head(parent)
        else:
            log.info("查最近创建的所有 -00000N 应收单子单（只读）")
            kids = kq.rows(
                "AR_receivable",
                "FBillNo,FCreateDate,FCreatorId.FName,FDocumentStatus,"
                "FAllAmountFor,FDATE",
                "FBillNo like '%-0000%'", top=200)
            head = []

        if head:
            for h in head:
                log.info(f"父单 {h.get('FBillNo')}  日期={h.get('FDATE')}  "
                         f"状态={h.get('FDocumentStatus')}  "
                         f"价税={num(h.get('FAllAmountFor')):.2f}  "
                         f"不含税={num(h.get('FNOTAXAMOUNT')):.2f}  "
                         f"税额={num(h.get('FTaxAmount')):.2f}  "
                         f"创建={h.get('FCreateDate')}")

        if not kids:
            log.ok("★ 子单数 = 0 —— 这次没有产生 -00000N 子单")
            br.close()
            return 0

        total = 0.0
        log.warn(f"发现 {len(kids)} 张子单：")
        for k in kids:
            amt = num(k.get("FAllAmountFor"))
            total += amt
            log.info(f"  {k.get('FBillNo')}  {amt:+.2f}  "
                     f"{k.get('FDocumentStatus')}  {k.get('FCreateDate')}  "
                     f"{k.get('FCreatorId.FName')}")
            if detail:
                for e in kq.ar_entries(str(k.get("FBillNo"))):
                    log.info(f"      · {e.get('FMaterialId.FName')}  "
                             f"数量={e.get('FPriceQty')}  "
                             f"不含税={e.get('FNOTAXAMOUNTFOR_D')}  "
                             f"税额={e.get('FTAXAMOUNTFOR_D')}  "
                             f"价税={e.get('FALLAMOUNTFOR_D')}")
        log.warn(f"子单金额合计 = {total:+.2f}（合计 0 ⇒ 账务无影响，只是列表多几条）")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
