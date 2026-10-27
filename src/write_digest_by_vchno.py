# -*- coding: utf-8 -*-
"""
按「凭证字 + 凭证号」写凭证摘要 —— **走「凭证查询」**的补写工具。

用于「凭证生成报告列表」已被清场关掉、回不去的情况（本批马鞍山祥恒 / 宁波日和）。
定位与打开逻辑复用 `gl_goto_voucher.open_voucher()`。

用法：
    python src/write_digest_by_vchno.py --no 155 --ar-no AR105-202609-0096 \
        --company 马鞍山祥恒包装有限公司                 # 只算摘要 + 打开页 + dump 分录，不保存
    python src/write_digest_by_vchno.py --no 155 --ar-no … --company … --go
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
from gl_goto_voucher import open_voucher                  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="write_digest_by_vchno")
SHOT_DIR = abs_path("screenshots", CFG)

# 只放行「保存凭证」（用户口径：凭证只保存，不提交不审核）
_CFG = dict(CFG)
_CFG["safety"] = dict(CFG.get("safety", {}) or {})
_CFG["safety"].update({"auto_voucher_save": True})
GUARD = Guard(_CFG, log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no", required=True, help="凭证号")
    ap.add_argument("--zi", default="转")
    ap.add_argument("--ar-no", required=True)
    ap.add_argument("--company", required=True)
    ap.add_argument("--expect", default="", help="★ SF-30：打开凭证前用「摘要含该词」二次锚定"
                    "（同字号跨字组重复时必传；通常传客户名）。"
                    "★ 补摘要场景：摘要还没写，客户名不在摘要里，传 expect 会跳过正确凭证"
                    "⇒ 默认不传，除非显式给 --expect")
    ap.add_argument("--date-range", default="", help="账务日期过滤区间 start,end（如 2026-10-01,2026-10-31）。"
                    "★ 凭证查询默认过滤上一期，跨期凭证必传，否则翻遍列表也找不到")
    ap.add_argument("--go", action="store_true")
    args = ap.parse_args()

    date_range = tuple(x.strip() for x in args.date_range.split(",")) \
        if args.date_range else None
    if date_range and len(date_range) != 2:
        raise SystemExit("--date-range 格式：start,end")

    rc = 0
    try:
        with sync_playwright() as p:
            br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
            pg = br.contexts[0].pages[0]
            kq = KdQuery(pg)

            text, _ = compute_digest(kq, args.ar_no, args.company)
            ui = KingdeeUI(pg, CFG, log, GUARD)

            if not open_voucher(pg, ui, log, args.zi, args.no,
                                expect=(args.expect or None),
                                date_range=date_range):
                raise RuntimeError(f"没能打开 {args.zi}{args.no} 的可编辑页")
            pg.wait_for_timeout(1500)

            entries = ui.read_voucher_entries()
            log.info(f"---- 凭证分录 {len(entries)} 行 ----")
            for i, e in enumerate(entries):
                log.info(f"  [{i}] 摘要={e.get('FEXPLANATION')!r} 科目={e.get('FACCTFULLNAME')!r} "
                         f"借={e.get('FDEBIT')!r} 贷={e.get('FCREDIT')!r} "
                         f"{'可编辑' if e.get('__editable__') else '只读'}")
            if not entries:
                raise RuntimeError("读不到凭证分录 —— 当前不在「凭证 - 修改」页？")

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
            shot(pg, SHOT_DIR, f"vchno_digest_{args.zi}{args.no}", log)

            if args.go and ok:
                ui.save_voucher()
                pg.wait_for_timeout(3000)
                log.ok("凭证已保存（未提交未审核）")
                shot(pg, SHOT_DIR, f"vchno_digest_saved_{args.zi}{args.no}", log)
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
