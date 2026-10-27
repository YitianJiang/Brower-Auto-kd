# -*- coding: utf-8 -*-
"""
广源包装集团 2026-09 链 · 第 5 步：报告列表 → 业务凭证 → 总账凭证（凭证-修改）
==============================================================================
路径（2026-09-21 实测）：
  凭证生成报告列表 → 选行 →「查看业务凭证」→ 业务凭证列表
  → 选行 →「查看总账凭证」→「凭证 - 修改」（摘要在这里改）

摘要口径（用户 2026-09-21）：
  取应收单详情页明细 —— ① 存货类别=原材料 的行计价数量求和 = x
  ② 物料名称含「光油」的行计价数量求和 = y  ③ 所有行计价数量求和 = z
  s = z - x - y ; 摘要 =「<单位名>销售水性油墨s公斤，光油y公斤，原材料x公斤」

用法：
    python src/gy3_5_digest.py                       # 只读：下钻并打印分录
    python src/gy3_5_digest.py --digest "…"          # 下钻 + 写摘要（不保存）
    python src/gy3_5_digest.py --digest "…" --go     # 下钻 + 写摘要 + 保存凭证
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, shot, Guard  # noqa: E402
from steps import KingdeeUI  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_5_digest")
SHOT_DIR = abs_path("screenshots", CFG)

# 用户 2026-09-21 要求整链跑到「凭证 + 摘要」；
# 按 config 的边界「只做到保存/暂存为止，不提交不审核」→ 只放行 voucher_save。
_CFG = dict(CFG)
_CFG["safety"] = dict(CFG.get("safety", {}) or {})
_CFG["safety"].update({"auto_voucher_save": True})
GUARD = Guard(_CFG, log)

# 应收单明细算出来的摘要（用户口径）
DIGEST = "广源包装集团股份有限公司销售水性油墨9580公斤，光油11200公斤，原材料358公斤"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", default="")
    ap.add_argument("--go", action="store_true", help="写完后保存凭证")
    ap.add_argument("--rows", default="", help='限定行，如 "0,1,2"；默认所有非空摘要行')
    ap.add_argument("--assume-open", action="store_true",
                    help="当前标签已是「凭证 - 修改」时直接用，不重新下钻")
    args = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, GUARD)

        names = [t["text"] for t in ui.tabs()]
        log.info(f"标签：{names}")

        if args.assume_open and "凭证 - 修改" in str(ui.active_tab() or ""):
            log.info("当前已在「凭证 - 修改」，跳过下钻")
        else:
            target = next((n for n in names if "凭证生成报告" in str(n)), None)
            if not target:
                log.err("没有「凭证生成报告列表」标签页 —— 请先跑 gy3_4_voucher.py --go")
                br.close()
                return 1
            ui.activate_tab(target, timeout_ms=8000)
            pg.wait_for_timeout(1500)

            log.info("→ 业务凭证列表")
            ui.open_biz_voucher_list()
            pg.wait_for_timeout(2500)
            log.info(f"  标签：{[t['text'] for t in ui.tabs()]}")

            log.info("→ 总账凭证（凭证 - 修改）")
            ui.open_gl_voucher_for_edit()
            pg.wait_for_timeout(3000)
            log.info(f"  标签：{[t['text'] for t in ui.tabs()]}")
            log.info(f"  激活：{ui.active_tab()!r}")
            if "凭证 - 修改" not in str(ui.active_tab() or ""):
                act = next((n for n in [t["text"] for t in ui.tabs()]
                            if "凭证 - 修改" in str(n)), None)
                if act:
                    ui.activate_tab(act, timeout_ms=8000)
                    pg.wait_for_timeout(1500)

        entries = ui.read_voucher_entries()
        log.info(f"---- 凭证分录 {len(entries)} 行 ----")
        for i, e in enumerate(entries):
            log.info(f"  [{i}] 摘要={e.get('FEXPLANATION')!r} 科目={e.get('FACCOUNTID')!r} "
                     f"借方={e.get('FDEBIT')!r} 贷方={e.get('FCREDIT')!r} "
                     f"客户={e.get('FCUSTOMERID')!r} "
                     f"{'可编辑' if e.get('__editable__') else '只读'}")

        shot(pg, SHOT_DIR, "gy3_5_voucher", log)

        if not args.digest:
            log.info("（未传 --digest，只读模式）")
            br.close()
            return 0

        idxs = ([int(x) for x in args.rows.split(",") if x.strip()]
                if args.rows.strip()
                else [i for i, e in enumerate(entries) if (e.get("FEXPLANATION") or "").strip()])
        log.info(f"要改摘要的行：{idxs}")
        for i in idxs:
            ui.set_one_digest(i, args.digest)
        pg.wait_for_timeout(800)

        after = ui.read_voucher_entries()
        for i in idxs:
            log.info(f"  [{i}] 改后摘要 = {after[i].get('FEXPLANATION')!r}")
        shot(pg, SHOT_DIR, "gy3_5_digest_done", log)

        if args.go:
            ui.save_voucher()
            pg.wait_for_timeout(3000)
            log.ok("凭证已保存")
            shot(pg, SHOT_DIR, "gy3_5_saved", log)
        else:
            log.info("（未传 --go，摘要已写但未保存凭证）")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
