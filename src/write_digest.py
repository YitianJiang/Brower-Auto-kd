# -*- coding: utf-8 -*-
"""
通用：算 + 写 + 存「凭证摘要」。
摘要口径（用户 2026-09-21 定，与 digest_rule.py 一致）：
  取应收单明细 —— ① 存货类别=原材料的行计价数量求和 = x
                ② 物料名称含「光油」的行计价数量求和 = y
                ③ 所有行计价数量求和 = z
  s = z − x − y ；摘要 =「<单位名>销售水性油墨s公斤，光油y公斤，原材料x公斤」

路径：凭证生成报告列表 → 选行「查看业务凭证」→ 业务凭证列表
      → 选行「查看总账凭证」→「凭证 - 修改」（摘要在这里改）→ 保存

用法：
    python src/write_digest.py --ar-no AR105-202609-0007 --company 慈溪市润科包装材料有限公司
    python src/write_digest.py --ar-no … --company … --go      # 写完保存凭证
    python src/write_digest.py --ar-no … --company … --peek    # 只读：只打印算出来的摘要
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, shot, Guard  # noqa: E402
from steps import KingdeeUI  # noqa: E402
from kdquery import KdQuery  # noqa: E402
from digest_rule import classify_rows, build_digest, fmt_qty  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="write_digest")
SHOT_DIR = abs_path("screenshots", CFG)

# 只放行「保存凭证」（用户口径：凭证只保存，不提交不审核）
_CFG = dict(CFG)
_CFG["safety"] = dict(CFG.get("safety", {}) or {})
_CFG["safety"].update({"auto_voucher_save": True})
GUARD = Guard(_CFG, log)


def compute_digest(kq, ar_no, company):
    ent = kq.rows("AR_receivable",
                  "FMaterialId.FName,FPriceQty,F_PAEZ_BASEPROPERTY2",
                  f"FBillNo = '{ar_no}'", top=500)
    rows = [{"material": r.get("FMaterialId.FName"),
             "qty": r.get("FPriceQty"),
             "category": r.get("F_PAEZ_BASEPROPERTY2")} for r in ent]
    from collections import Counter
    c = classify_rows(rows)
    log.info(f"应收单 {ar_no} 明细 {len(rows)} 行；"
             f"存货类别分布={dict(Counter(str(r['category']) for r in rows))}")
    log.info(f"  x(原材料)={fmt_qty(c['x'])}（{c['n_raw']} 行） "
             f"y(光油)={fmt_qty(c['y'])}（{c['n_oil']} 行） "
             f"t(桶/包装物)={fmt_qty(c['t'])}（{c['n_pack']} 行） "
             f"z={fmt_qty(c['z'])} → s={fmt_qty(c['s'])}")
    if c["both_rows"]:
        log.warn(f"  有 {len(c['both_rows'])} 行同时命中「原材料」和「光油」，"
                 f"这些行被 x、y 各计一次（口径按用户原话）")
    text = build_digest(company, c["x"], c["y"], c["s"], omit_zero=True, t=c["t"])
    log.ok(f"★ 摘要 = {text}")
    return text, c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ar-no", required=True)
    ap.add_argument("--company", required=True, help="单位名（摘要开头）")
    ap.add_argument("--go", action="store_true", help="写完保存凭证")
    ap.add_argument("--peek", action="store_true", help="只算摘要，不碰界面")
    args = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        kq = KdQuery(pg)

        text, _ = compute_digest(kq, args.ar_no, args.company)
        if args.peek:
            br.close()
            return 0

        ui = KingdeeUI(pg, CFG, log, GUARD)
        names = [t["text"] for t in ui.tabs()]
        log.info(f"标签：{names}")
        target = next((n for n in names if "凭证生成报告" in str(n)), None)
        if not target:
            log.err("没有「凭证生成报告列表」标签 —— 凭证还没生成？")
            br.close()
            return 1
        ui.activate_tab(target, timeout_ms=8000)
        pg.wait_for_timeout(1500)

        log.info("→ 业务凭证列表")
        ui.open_biz_voucher_list()
        pg.wait_for_timeout(2500)
        log.info("→ 总账凭证（凭证 - 修改）")
        ui.open_gl_voucher_for_edit()
        pg.wait_for_timeout(3000)
        if "凭证 - 修改" not in str(ui.active_tab() or ""):
            act = next((n for n in [t["text"] for t in ui.tabs()]
                        if "凭证 - 修改" in str(n)), None)
            if act:
                ui.activate_tab(act, timeout_ms=8000)
                pg.wait_for_timeout(1500)
        log.info(f"  激活：{ui.active_tab()!r}")

        entries = ui.read_voucher_entries()
        log.info(f"---- 凭证分录 {len(entries)} 行 ----")
        for i, e in enumerate(entries):
            log.info(f"  [{i}] 摘要={e.get('FEXPLANATION')!r} 科目={e.get('FACCOUNTID')!r} "
                     f"借={e.get('FDEBIT')!r} 贷={e.get('FCREDIT')!r} "
                     f"客户={e.get('FCUSTOMERID')!r} "
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
        shot(pg, SHOT_DIR, f"digest_{args.ar_no}", log)

        if args.go and ok:
            ui.save_voucher()
            pg.wait_for_timeout(3000)
            log.ok("凭证已保存（未提交未审核）")
            shot(pg, SHOT_DIR, f"digest_saved_{args.ar_no}", log)
        else:
            log.info("（未保存凭证）")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
