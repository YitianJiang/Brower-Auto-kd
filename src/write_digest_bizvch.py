# -*- coding: utf-8 -*-
"""
写凭证摘要 —— **绕道「业务凭证查询」**的版本（当「凭证生成报告列表」已关闭时用）。

背景：`write_digest.py` 依赖「凭证生成报告列表」标签，而那张列表是**瞬时**的
      （只保留最近一次凭证生成的报告，搜「凭证生成报告列表」0 结果）。
      批量跑完后收尾清场会把它关掉 → 就再也回不去了（本批马鞍山祥恒 / 宁波日和即如此）。
      但顶部搜索能搜到 **「业务凭证查询」(YWPZCX-YS，财务会计>应收款管理>账务处理)**，
      它的工具栏有 **「查看总账凭证」** —— 与报告列表那条路殊途同归，
      一样能打开标签「凭证 - 修改」改摘要。

路径：业务凭证查询（过滤 来源单据号 = 专票号）→ 勾行 →「查看总账凭证」→「凭证 - 修改」
      → 双击摘要格改文本 → 回读校验 → 保存

用法：
    python src/write_digest_bizvch.py --inv SVINV00034015 --ar-no AR105-202609-0096 \
        --company 马鞍山祥恒包装有限公司 --dry     # 只过滤 + dump，不打开凭证
    python src/write_digest_bizvch.py --inv … --ar-no … --company … --go
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright          # noqa: E402
from common import load_config, abs_path, Logger, shot, Guard  # noqa: E402
from steps import KingdeeUI                               # noqa: E402
from kdquery import KdQuery                               # noqa: E402
from write_digest import compute_digest                   # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="write_digest_bizvch")
SHOT_DIR = abs_path("screenshots", CFG)

# 只放行「保存凭证」（用户口径：凭证只保存，不提交不审核）
_CFG = dict(CFG)
_CFG["safety"] = dict(CFG.get("safety", {}) or {})
_CFG["safety"].update({"auto_voucher_save": True})
GUARD = Guard(_CFG, log)

BIZVCH_PATH = "财务会计>应收款管理>账务处理"
BIZVCH_NAME = "业务凭证查询"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inv", required=True, help="来源单据号（销售增值税专用发票号）")
    ap.add_argument("--ar-no", required=True)
    ap.add_argument("--company", required=True)
    ap.add_argument("--go", action="store_true", help="写完保存凭证")
    ap.add_argument("--dry", action="store_true", help="只过滤 + dump 列表，不打开凭证")
    args = ap.parse_args()

    rc = 0
    try:
        with sync_playwright() as p:
            br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
            pg = br.contexts[0].pages[0]
            kq = KdQuery(pg)

            text, _ = compute_digest(kq, args.ar_no, args.company)

            ui = KingdeeUI(pg, CFG, log, GUARD)
            ui.open_menu_by_search(BIZVCH_NAME, path=BIZVCH_PATH)
            pg.wait_for_timeout(3000)
            log.info(f"当前标签：{[t['text'] for t in ui.tabs()]}")

            # 过滤：来源单据号 包含 <专票号>
            n = ui.set_bill_no_filter([args.inv], field="FSourceBillNo")
            log.info(f"按来源单据号过滤后：{n} 行")
            rows = ui.grid_all_rows() or []
            log.info(f"数据源 {len(rows)} 行：")
            hits = 0
            for r in rows:
                src = str(r.get("FSOURCEBILLNO") or r.get("FSourceBillNo") or "")
                if args.inv in src:
                    hits += 1
                log.info(f"  {str(r.get('FBILLNO') or r.get('FBillNo') or '')[:18]:18s} "
                         f"来源={src:18s} 字={r.get('FVOUCHERGROUPID_FNAME')} "
                         f"号={r.get('FVOUCHERGROUPNO')} 借={r.get('FDEBIT')}")

            if args.dry:
                log.warn("--dry：只过滤，不开凭证、不保存")
                br.close()
                return 0

            if hits != 1:
                raise RuntimeError(f"期望恰好 1 张业务凭证命中 {args.inv}，实际 {hits} 张 —— 停止")

            log.info("→ 查看总账凭证（凭证 - 修改）")
            ui._reset_selection_and_pick_first()
            ui.click_menu("查看总账凭证", timeout_ms=6000)
            pg.wait_for_timeout(3500)
            if "凭证 - 修改" not in str(ui.active_tab() or ""):
                act = next((t["text"] for t in ui.tabs() if "凭证 - 修改" in str(t["text"])), None)
                if act:
                    ui.activate_tab(act, timeout_ms=8000)
                    pg.wait_for_timeout(1500)
            log.info(f"  激活：{ui.active_tab()!r}")

            entries = ui.read_voucher_entries()
            log.info(f"---- 凭证分录 {len(entries)} 行 ----")
            for i, e in enumerate(entries):
                log.info(f"  [{i}] 摘要={e.get('FEXPLANATION')!r} 科目={e.get('FACCTFULLNAME')!r} "
                         f"借={e.get('FDEBIT')!r} 贷={e.get('FCREDIT')!r} "
                         f"{'可编辑' if e.get('__editable__') else '只读'}")

            idxs = [i for i, e in enumerate(entries) if (e.get("FEXPLANATION") or "").strip()]
            log.info(f"要改摘要的行：{idxs}")
            for i in idxs:
                ui.set_one_digest(i, text)
            pg.wait_for_timeout(800)

            after = ui.read_voucher_entries()
            ok = all((after[i].get("FEXPLANATION") or "").strip() == text for i in idxs)
            for i in idxs:
                log.info(f"  [{i}] 改后 = {after[i].get('FEXPLANATION')!r}")
            log.ok(f"回读校验：{'全部一致 ✓' if ok else '有不一致 ✗'}")
            shot(pg, SHOT_DIR, f"bizvch_digest_{args.inv}", log)

            if args.go and ok:
                ui.save_voucher()
                pg.wait_for_timeout(3000)
                log.ok("凭证已保存（未提交未审核）")
                shot(pg, SHOT_DIR, f"bizvch_digest_saved_{args.inv}", log)
            elif not ok:
                log.err("回读不一致 —— 不保存")
                rc = 1
            else:
                log.info("（未保存凭证）")
            br.close()
    except Exception as e:  # noqa: BLE001
        log.err(f"失败：{type(e).__name__}: {str(e)[:300]}")
        rc = 1
    finally:
        log.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
