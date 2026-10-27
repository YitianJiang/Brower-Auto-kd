# -*- coding: utf-8 -*-
"""
广源包装集团 2026-09 链 · 第 4 步：专票 → 生成凭证 → 读报告
==============================================================================
路径（沿用 2026-09-21 实测）：
  专票列表 → 勾单 → 凭证 →「生成凭证」对话框 → 勾账簿行 → 点「凭证生成」
  → 自动打开「凭证生成报告列表」→ 读报告（错行数必须为 0）

用法：
    python src/gy3_4_voucher.py --inv SVINV00033668              # 只开对话框勾账簿，不点最终按钮
    python src/gy3_4_voucher.py --inv SVINV00033668 --go         # 真的生成凭证并读报告
    python src/gy3_4_voucher.py --inv SVINV00033668 --go --digest "广源包装集团股份有限公司销售水性油墨9580公斤，光油11200公斤，原材料358公斤"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, shot  # noqa: E402
from steps import KingdeeUI, eval_all_frames  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_4_voucher")
SHOT_DIR = abs_path("screenshots", CFG)
VOUCHER_GEN_BTN = 'a[id$="-FVOUCHERGENE_c"]'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inv", required=True, help="专票号，如 SVINV00033668")
    ap.add_argument("--go", action="store_true", help="真的点「凭证生成」")
    ap.add_argument("--digest", default="", help="预留：要写入凭证的摘要（本步暂不写）")
    args = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, None)   # guard=None：本脚本按用户口令执行整链

        log.info(f"起始标签：{[x['text'] for x in ui.tabs()]}")
        ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=True)

        # 1) 专票列表 + 按单号筛选
        ui.open_invoice_list()
        pg.wait_for_timeout(3000)
        n = ui.set_bill_no_filter([args.inv])
        rows = ui.grid_all_rows() or []
        bills = sorted({str(r.get("FBILLNO")) for r in rows})
        log.info(f"过滤命中行数={n} 单号={bills}")
        if args.inv not in bills:
            log.err(f"列表里没筛出 {args.inv}，停止")
            br.close()
            return 1

        if ui.selected_count():
            ui.clear_selection()
            pg.wait_for_timeout(600)
        got = ui.select_rows([args.inv])
        log.info(f"勾选 {args.inv} → {got}")
        if got <= 0:
            raise RuntimeError("没勾上")

        # 2) 凭证 → 生成凭证
        ui.open_voucher_dialog()
        ui.check_voucher_book_row(0)

        if not args.go:
            log.info("已停在「凭证生成」对话框（未点最终按钮）")
            shot(pg, SHOT_DIR, "gy3_4_dialog", log)
            br.close()
            return 0

        btn = pg.locator(f'{VOUCHER_GEN_BTN}:visible').first
        if btn.count() == 0:
            raise RuntimeError("找不到「凭证生成」按钮")
        btn.click(timeout=10000)
        log.info("已点「凭证生成」，等待报告…")
        pg.wait_for_timeout(12000)

        log.info(f"标签：{[x['text'] for x in ui.tabs()]}")

        # ★2026-09-21 实测：点完「凭证生成」后
        #   ① 若单据未全部审核 → 对话框顶部出现红字「所选单据未全部审核!」，
        #      且**不生成报告**。这时绝不能去读"第一个可见 grid" ——
        #      那个 grid 是弹窗背后的【专票列表】，会读出一堆看着很像报告的字段
        #      （单据编号/价税合计/客户…），极易误判成"报告 0 错误、生成成功"。
        #   ② 所以要**先在对话框内部**找报告 grid；找不到就报未通过。
        chk = eval_all_frames(pg, """() => {
            const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
            const w = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(x => (x.innerText || '').includes('凭证生成'));
            if (!w) return {err: 'no dialog'};
            const t = (w.innerText || '').replace(/\\s+/g, ' ');
            return {txt: t.slice(0, 300),
                    blocked: t.includes('未全部审核') || t.includes('未审核'),
                    hasGrid: !!w.querySelector('table.k-selectable'),
                    hasOk: !!Array.from(w.querySelectorAll('a,button')).filter(vis)
                             .find(e => (e.innerText||'').trim() === '确定')};
        }""")
        log.info(f"对话框状态：{chk}")
        if isinstance(chk, dict) and chk.get("blocked"):
            log.err("★ 凭证生成被拦：所选单据未全部审核 —— 请先跑 gy3_6_submit_inv.py 提交/审核专票")

        rep = ui.read_voucher_report()
        log.info("---- 凭证生成报告 ----")
        if not rep:
            log.warn("报告列表为空 / 没读到（可能是被拦，或报告在别的标签页里）")
        for r in rep or []:
            log.info(f"  {r}")
        errs = ui.voucher_report_errors()
        log.info(f"报告行数={len(rep or [])} 错误行数={len(errs or [])}")
        if (rep or chk) and not errs and (not isinstance(chk, dict) or not chk.get("blocked")):
            log.ok("★ 报告零错误")
        else:
            for e in errs or []:
                log.err(f"  {e}")

        shot(pg, SHOT_DIR, "gy3_4_report", log)
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
