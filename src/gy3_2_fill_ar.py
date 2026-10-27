# -*- coding: utf-8 -*-
"""
广源包装集团 2026-09 链 · 第 2 步：应收单填折扣 + 金额对齐 + 摘要取数 + 填日期
================================================================================
对**当前已下推、未保存**的「应收单 - 新增」页操作：
  1. 折扣额按行含税占比分摊扣款 2,733.50
  2. 尾差对齐到实际开票 不含税 266,824.88 / 税额 34,687.22
  3. 按「存货类别 / 物料名称」拆计价数量 → x / y / z / s，拼出凭证摘要文本
  4. 业务日期 FDATE = 到期日 FENDDATE_H = 月底（2026-09-30）
  5. （--save 才）保存 —— 保存即审核，属不可逆动作

用法：
    python src/gy3_2_fill_ar.py                 # 只填 + 回读校验，不保存
    python src/gy3_2_fill_ar.py --save          # 填完保存（=审核）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, shot  # noqa: E402
from steps import KingdeeUI  # noqa: E402
from discount import to_d  # noqa: E402
from digest_rule import classify_rows, build_digest  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_2_fill_ar")
SHOT_DIR = abs_path("screenshots", CFG)

DISCOUNT = 2733.5
TARGET_NOTAX = "266824.88"
TARGET_TAX = "34687.22"
MONTH_END = "2026-09-30"
COMPANY_FALLBACK = "广源包装集团股份有限公司"


def money(x):
    return f"{to_d(x):,.2f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="填完后保存（保存即审核，不可逆）")
    ap.add_argument("--month-end", default=MONTH_END)
    args = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, None)
        if not ui.activate_tab("应收单 - 新增", timeout_ms=8000):
            raise RuntimeError("没找到「应收单 - 新增」标签，请先跑 gy3_1")
        pg.wait_for_timeout(2000)

        # ---------- ① 折扣分摊 ----------
        rows = ui.detail_amounts("receivable")
        base = sum(r["all"] for r in rows)
        log.info(f"应收单明细 {len(rows)} 行，价税合计 {money(base)}")
        if len(rows) != 46 or base not in (to_d("304245.60"), to_d("301512.10")):
            raise RuntimeError(f"行数/金额不符预期：{len(rows)} 行 / {money(base)}"
                               f"（应为 46 行，且合计为 304,245.60 或已折 301,512.10）")

        discs, info = ui.fill_discount_by_ratio(DISCOUNT, kind="receivable")
        s = ui.detail_summary("receivable")
        log.info(f"分摊后：折扣 {money(s['disc'])} / 不含税 {money(s['notax'])}"
                 f" / 税额 {money(s['tax'])} / 价税合计 {money(s['all'])}")

        # ---------- ② 尾差对齐 ----------
        s = ui.detail_align_totals(target_notax=TARGET_NOTAX,
                                   target_tax=TARGET_TAX, kind="receivable")
        log.info(f"对齐结果：不含税 {money(s['notax'])}（{s.get('d_notax')}）"
                 f" / 税额 {money(s['tax'])}（{s.get('d_tax')}）"
                 f" / 价税合计 {money(s['all'])}  ok={s['ok']}")

        # ---------- ③ 摘要取数 ----------
        raw = ui.detail_rows()
        rws = [{"material": r.get("FMATERIALNAME"),
                "category": r.get("F_PAEZ_BASEPROPERTY2"),
                "qty": r.get("FPRICEQTY")} for r in raw]
        cats = {}
        for r in rws:
            cats[r["category"]] = cats.get(r["category"], 0) + 1
        log.info(f"存货类别分布（{len(rws)} 行）：{cats}")
        stat = classify_rows(rws)
        log.info(f"计价数量：x(原材料)={stat['x']}  y(含光油)={stat['y']} "
                 f"z(全部)={stat['z']}  s=z-x-y={stat['s']}")
        log.info(f"  命中行数：原材料 {stat['n_raw']} 行 / 光油 {stat['n_oil']} 行")
        if stat["both_rows"]:
            log.warn(f"⚠️ 有 {len(stat['both_rows'])} 行同时是「原材料」且名称含「光油」，"
                     f"会被重复计数，请人工确认：{stat['both_rows']}")
        digest = build_digest(COMPANY_FALLBACK, stat["x"], stat["y"], stat["s"])
        log.ok(f"凭证摘要 = {digest}")

        # ---------- ④ 业务日期 / 到期日 = 月底 ----------
        for fld in ("FDATE", "FENDDATE_H"):
            r = ui.fill_bill_field(fld, args.month_end)
            log.info(f"  {fld} ← {args.month_end}  ({r})")
        pg.wait_for_timeout(800)
        got = ui.read_bill_fields(["FBILLNO", "FDATE", "FENDDATE_H"])
        log.info(f"回读：{got}")

        def norm(v):
            """2026/9/30 → 2026-09-30"""
            s = str(v or "").strip().replace("/", "-")
            parts = s.split("-")
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            return s

        for fld in ("FDATE", "FENDDATE_H"):
            if norm(got.get(fld)) != norm(args.month_end):
                log.warn(f"⚠️ {fld} 回读 {got.get(fld)!r} ≠ 目标 {args.month_end}")
            else:
                log.ok(f"  {fld} = {args.month_end} ✓")

        shot(pg, SHOT_DIR, "gy3_ar_filled", log)

        if not args.save:
            log.warn("未保存（加 --save 才保存）。页面停在「应收单 - 新增」")
            br.close()
            return 0

        ui.save_bill()
        pg.wait_for_timeout(3500)
        got = ui.read_bill_fields(["FBILLNO", "FDATE", "FENDDATE_H"])
        log.ok(f"已保存：{got}")
        shot(pg, SHOT_DIR, "gy3_ar_saved", log)
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
