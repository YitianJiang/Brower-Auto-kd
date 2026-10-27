# -*- coding: utf-8 -*-
"""
广源包装集团 2026-09 链 · 第 6 步：提交 + 审核 专票（凭证生成的前置条件）
==============================================================================
背景（2026-09-21 实测）：
  直接生成凭证会被拦：凭证生成对话框顶部红字「所选单据未全部审核!」
  ⇒ 必须先把专票提交/审核，才能进总账。
护栏：用户 2026-09-21 明确批准「提交并审核」，故本脚本临时把
  config 的 safety.auto_submit / auto_audit 置 true 再跑 Guard；
  跑完只影响本次进程，不落盘改 config。

用法：
    python src/gy3_6_submit_inv.py --inv SVINV00033668            # 提交
    python src/gy3_6_submit_inv.py --inv SVINV00033668 --audit    # 提交 + 审核
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, shot, Guard  # noqa: E402
from steps import KingdeeUI, eval_all_frames  # noqa: E402
from kdquery import KdQuery, num  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_6_submit_inv")
SHOT_DIR = abs_path("screenshots", CFG)

# 用户已批准提交+审核 → 本次进程临时放行这两个护栏（不改 config.yaml）
_CFG = dict(CFG)
_CFG["safety"] = dict(CFG.get("safety", {}) or {})
_CFG["safety"].update({"auto_submit": True, "auto_audit": True})
GUARD = Guard(_CFG, log)

JS_DIALOGS = """() => {
    const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
    const g = e => (e.innerText || '').replace(/\\s+/g,' ').trim().slice(0, 300);
    return {
        windows: Array.from(document.querySelectorAll('.k-window')).filter(vis).map(g).filter(Boolean),
        notices: Array.from(document.querySelectorAll('.k-notification, .k-tooltip')).filter(vis)
                    .map(g).filter(Boolean),
    };
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inv", required=True)
    ap.add_argument("--audit", action="store_true")
    args = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, GUARD)
        kq = KdQuery(pg)

        def status():
            r = kq.rows("IV_SALESIC", "FBillNo,FDocumentStatus",
                        f"FBillNo = '{args.inv}'", top=3)
            return r[0].get("FDocumentStatus") if r else None

        log.info(f"提交前状态 = {status()}")

        ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=True)
        ui.open_invoice_list()
        pg.wait_for_timeout(3000)
        ui.set_bill_no_filter([args.inv])
        rows = ui.grid_all_rows() or []
        bills = sorted({str(r.get("FBILLNO")) for r in rows})
        if args.inv not in bills:
            log.err(f"列表里没筛出 {args.inv}：{bills}")
            br.close()
            return 1
        if ui.selected_count():
            ui.clear_selection()
        log.info(f"勾选 {args.inv} → {ui.select_rows([args.inv])}")

        # ---- 提交 ----
        ui.submit_bill()
        pg.wait_for_timeout(5000)
        d = eval_all_frames(pg, JS_DIALOGS) or {}
        for k, v in d.items():
            if v:
                log.warn(f"提交后 {k}: {v}")
        log.info(f"提交后状态 = {status()}")
        shot(pg, SHOT_DIR, "gy3_6_after_submit", log)

        # ---- 审核 ----
        if args.audit and status() != "C":
            ui.audit_bill()
            pg.wait_for_timeout(5000)
            d = eval_all_frames(pg, JS_DIALOGS) or {}
            for k, v in d.items():
                if v:
                    log.warn(f"审核后 {k}: {v}")
            log.info(f"审核后状态 = {status()}")
            shot(pg, SHOT_DIR, "gy3_6_after_audit", log)

        st = status()
        (log.ok if st == "C" else log.warn)(f"最终单据状态 = {st}（C=已审核）")

        # ---- 关键复核：金额 / 日期 / 人员 / 子单 ----
        # ⚠️ FAllAmount/FNotaxAmount/FTaxAmount 在 IV_SALESIC 上是**明细级**字段，
        #    和单头字段混在一条查询里会退化/串行（实测打印出的是"某一行的值"，
        #    不是整单合计）→ 单头只查单头字段，金额单独查明细求和。
        h = kq.rows("IV_SALESIC",
                    "FBillNo,FDATE,FINVOICEDATE,FDocumentStatus,"
                    "F_PAEZ_COMBO,FDRAWER,FPAYEE,FSrcBillNo",
                    f"FBillNo = '{args.inv}'", top=3)
        for r in h[:1]:
            log.ok(f"  {r.get('FBillNo')} 业务日={str(r.get('FDATE'))[:10]} "
                   f"发票日={str(r.get('FINVOICEDATE'))[:10]} 状态={r.get('FDocumentStatus')}")
            log.ok(f"  开票人={r.get('FDRAWER')} 收款人={r.get('FPAYEE')} "
                   f"复核人={r.get('F_PAEZ_COMBO')} 来源={r.get('FSrcBillNo')}")
        ent = kq.rows("IV_SALESIC",
                      "FMaterialId.FName,FNotaxAmount,FDETAILTAXAMOUNT,FAllAmount",
                      f"FBillNo = '{args.inv}'", top=200)
        a = sum(float(x.get("FNotaxAmount") or 0) for x in ent)
        t = sum(float(x.get("FDETAILTAXAMOUNT") or 0) for x in ent)
        g = sum(float(x.get("FAllAmount") or 0) for x in ent)
        log.ok(f"  明细 {len(ent)} 行：不含税 {a:,.2f} / 税额 {t:,.2f} / 价税 {g:,.2f}")

        kids = kq.rows("AR_receivable", "FBillNo,FAllAmountFor",
                       "FBillNo like 'AR105-202609-0005-%'", top=20)
        if not kids:
            log.ok("★ 子单数 = 0 —— 提交/审核后仍未产生 -00000N 子单")
        else:
            for k in kids:
                log.warn(f"  ⚠️ 出现子单 {k.get('FBillNo')} {num(k.get('FAllAmountFor')):+.2f}")

        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
