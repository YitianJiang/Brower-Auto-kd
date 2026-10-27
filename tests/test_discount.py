# -*- coding: utf-8 -*-
"""
折扣分摊模块回归测试
====================
用 2026-09-21 广源包装集团那单的**真实数据**当固定用例：
    46 行出库明细、含税合计 304,245.60、扣款 2,733.50，
    目标 不含税 266,824.88 / 税额 34,687.22 / 价税合计 301,512.10。
当时人工算出来的锚点行是 seq 42（含税 24,200），尾差 +0.03。
如果这个模块跟当时结果对不上，说明通用化改错了，必须修。

用法：python tests/test_discount.py      （纯计算，不连浏览器）
"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from discount import (  # noqa: E402
    allocate_by_amount, pick_anchor_row, approx_net_of_tax,
    plan_alignment, summarize, to_d,
    plan_material_align, apply_material_align, group_rows,
)

# 广源 46 行按物料分组时的真实形态：4 张出库单 × 多行 → 16 种物料，
# 多数物料在应收单里占 2~3 行（这正是分币差的来源）。
GY_MATERIALS = [
    "192稳定剂", "TLYZ7绿浆", "WF-1052预印耐磨光油", "WF-1062预印防滑光油",
    "163流平剂", "181消泡剂", "8950树脂", "96洋红", "TLA999黑浆",
    "TLAN5耐晒紫浆", "TLYV102树脂", "TLYV103树脂", "TLYZ1黄浆",
    "TLYZ3红浆", "TLYZ6蓝浆", "百威专红",
]

# ---- 广源包装集团真实样本（46 行含税金额，按 seq 1..46）----
GY_AMOUNTS = [
    7600, 7600, 1900, 1140, 7920, 3800, 11000, 22000, 4440, 1900,
    7600, 1900, 5700, 1140, 3800, 10560, 3800, 3800, 11000, 22000,
    12160, 7600, 3800, 5700, 1900, 3800, 1900, 7920, 3800, 3800,
    22000, 7400, 11400, 5700, 1900, 3800, 1140, 3800, 10560, 1900,
    1900, 24200, 3800, 528, 237.6, 11000,
]
GY_DISCOUNT = Decimal("2733.5")
GY_TARGET_NOTAX = Decimal("266824.88")
GY_TARGET_TAX = Decimal("34687.22")


class TestAllocate(unittest.TestCase):

    def test_base_matches_invoice(self):
        """46 行含税合计必须等于 304,245.60"""
        self.assertEqual(sum(to_d(a) for a in GY_AMOUNTS), Decimal("304245.60"))
        self.assertEqual(len(GY_AMOUNTS), 46)

    def test_anchor_is_seq42(self):
        """锚点行 = 含税金额最大的行 = 索引 41（seq 42，24,200）"""
        self.assertEqual(pick_anchor_row(GY_AMOUNTS), 41)

    def test_rounding_tail_is_3_cents(self):
        """逐行四舍五入后合计 2,733.47，尾差 +0.03（与当时一致）"""
        discs, info = allocate_by_amount(GY_AMOUNTS, GY_DISCOUNT, anchor_fix=False)
        self.assertEqual(info["sum_before_fix"], Decimal("2733.47"))
        self.assertEqual(info["gap"], Decimal("0.03"))

    def test_anchor_fix_lands_total(self):
        """尾差补到最大行后，折扣合计精确等于扣款 2,733.50"""
        discs, info = allocate_by_amount(GY_AMOUNTS, GY_DISCOUNT)
        self.assertEqual(sum(discs), Decimal("2733.50"))
        self.assertEqual(info["anchor"], 41)
        self.assertTrue(info["anchor_fixed"])

    def test_spot_values_match_manual(self):
        """抽查几个当时人工填过的值：seq1=68.28、seq42 补后=217.46、seq44=4.74"""
        discs, _ = allocate_by_amount(GY_AMOUNTS, GY_DISCOUNT)
        self.assertEqual(discs[0], Decimal("68.28"))          # 7600 → 68.279
        self.assertEqual(discs[43], Decimal("4.74"))          # 528  → 4.742
        self.assertEqual(discs[44], Decimal("2.13"))          # 237.6 → 2.134
        # 最大行：理论 217.427 → 217.43，补尾差 +0.03 → 217.46
        self.assertEqual(discs[41], Decimal("217.46"))
        raw = discs[41] - Decimal("0.03")
        self.assertEqual(raw, Decimal("217.43"))

    def test_no_discount_means_zero(self):
        discs, info = allocate_by_amount(GY_AMOUNTS, 0)
        self.assertEqual(set(discs), {Decimal("0.00")})
        self.assertEqual(info["gap"], Decimal("0"))
        self.assertFalse(info["anchor_fixed"])

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            allocate_by_amount([], 100)

    def test_zero_base_raises(self):
        with self.assertRaises(ValueError):
            allocate_by_amount([0, 0], 100)


class TestAlignPlan(unittest.TestCase):

    def _rows(self, discs):
        """按页面上读到的口径造行数据：不含税=round(折后含税/1.13)、税额=差"""
        rows = []
        for i, (a, d) in enumerate(zip(GY_AMOUNTS, discs), start=1):
            net = to_d(a) - to_d(d)
            notax, tax = approx_net_of_tax(net)
            rows.append({"seq": i, "all": to_d(a), "disc": to_d(d),
                         "notax": notax, "tax": tax})
        return rows

    def test_plan_targets_anchor(self):
        discs, _ = allocate_by_amount(GY_AMOUNTS, GY_DISCOUNT)
        rows = self._rows(discs)
        plan = plan_alignment(rows, target_notax=GY_TARGET_NOTAX,
                              target_tax=GY_TARGET_TAX)
        self.assertEqual(plan["anchor"], 41)
        self.assertEqual(plan["anchor_seq"], 42)
        # 建议值 = 当前值 + 差额
        self.assertEqual(
            plan["suggest"]["notax"],
            to_d(rows[41]["notax"]) + plan["diff"]["notax"])

    def test_summarize_detects_hit(self):
        discs, _ = allocate_by_amount(GY_AMOUNTS, GY_DISCOUNT)
        rows = self._rows(discs)
        s = summarize(rows, target_notax=GY_TARGET_NOTAX, target_tax=GY_TARGET_TAX)
        self.assertEqual(s["n"], 46)
        self.assertEqual(s["disc"], Decimal("2733.50"))
        # 理论口径算出来的合计与目标应当非常接近（分位级），
        # 说明真实需要校正的量确实很小 —— 与「差值在 0.1 内才校正」的口径吻合
        self.assertLess(abs(s["d_notax"]), Decimal("0.1"))
        self.assertLess(abs(s["d_tax"]), Decimal("0.1"))

    def test_summarize_ok_flag(self):
        rows = [{"seq": 1, "all": Decimal("113"), "disc": Decimal("0"),
                 "notax": Decimal("100"), "tax": Decimal("13")}]
        self.assertTrue(summarize(rows, target_notax=100, target_tax=13)["ok"])
        self.assertFalse(summarize(rows, target_notax=100.5, target_tax=13)["ok"])


class TestMaterialAlign(unittest.TestCase):
    """逐物料对齐（不产生 -00000N 子单的事前方案）"""

    def _rows(self):
        discs, _ = allocate_by_amount(GY_AMOUNTS, GY_DISCOUNT)
        rows = []
        for i, (a, d) in enumerate(zip(GY_AMOUNTS, discs)):
            net = to_d(a) - to_d(d)
            notax, tax = approx_net_of_tax(net)
            rows.append({"seq": i + 1,
                         "material": GY_MATERIALS[i % len(GY_MATERIALS)],
                         "all": net, "disc": to_d(d), "notax": notax, "tax": tax})
        return rows

    def test_group_rows_keeps_first_seen_order(self):
        order, groups = group_rows([{"material": "B"}, {"material": "A"},
                                    {"material": "B"}])
        self.assertEqual(order, ["B", "A"])
        self.assertEqual(groups["B"], [0, 2])

    def test_plan_finds_divergent_materials(self):
        """46 行 / 16 物料，必然有物料「逐行舍入 ≠ 汇总舍入」"""
        plan = plan_material_align(self._rows())
        self.assertEqual(len(plan["materials"]), 16)
        self.assertTrue(plan["need_fix"])
        bad = [m for m in plan["materials"] if m["delta"] != 0]
        self.assertGreaterEqual(len(bad), 1)
        # 每个需要掰的物料都要有锚点行，且锚点属于该物料
        for m in bad:
            self.assertIn(m["anchor_row"], m["rows"])

    def test_apply_makes_every_material_match(self):
        """掰完之后：逐物料「行不含税之和」==「汇总后舍入」"""
        rows = self._rows()
        plan = plan_material_align(rows)
        fixed = apply_material_align(rows, plan)
        for m in plan["materials"]:
            got = sum((to_d(fixed[i]["notax"]) for i in m["rows"]), Decimal("0"))
            self.assertEqual(got, m["want_notax"], msg=f"物料 {m['name']} 未对齐")

    def test_apply_keeps_row_price_untouched(self):
        """掰平只做「不含税 ± δ / 税额 ∓ δ」→ 每行价税合计（=折后含税）不变

        ⚠️ 注意：**总不含税会移动 delta_total**（各物料 δ 之和不保证为 0）。
           所以 Path B 的目标必须取自「专票真实逐物料金额」—— 它们天然加起来
           就等于实际开票总额，掰完总额自动对上；不要拿 round(总含税/1.13) 当目标。
        """
        rows = self._rows()
        plan = plan_material_align(rows)
        fixed = apply_material_align(rows, plan)
        for a, b in zip(rows, fixed):
            self.assertEqual(to_d(a["all"]), to_d(b["all"]))
            self.assertEqual(to_d(a["notax"]) + to_d(a["tax"]),
                             to_d(b["notax"]) + to_d(b["tax"]))
        # 含税合计不变（折扣分摊的基数、价税合计都没动）
        self.assertEqual(sum(to_d(r["all"]) for r in rows),
                         sum(to_d(r["all"]) for r in fixed))
        # 总不含税的移动量 == 报告出来的 delta_total
        self.assertEqual(
            sum(to_d(r["notax"]) for r in fixed) - sum(to_d(r["notax"]) for r in rows),
            plan["delta_total"])

    def test_plan_on_already_aligned_rows_is_noop(self):
        """单行物料（每物料只有 1 行）永远不需要掰 —— 假阴性保护"""
        rows = [{"material": f"M{i}", "all": to_d(a),
                 "notax": approx_net_of_tax(to_d(a))[0],
                 "tax": approx_net_of_tax(to_d(a))[1]}
                for i, a in enumerate(GY_AMOUNTS)]
        plan = plan_material_align(rows)
        self.assertFalse(plan["need_fix"])
        self.assertEqual(plan["adjusts"], [])


class TestHelpers(unittest.TestCase):

    def test_to_d_handles_dirty_strings(self):
        self.assertEqual(to_d("¥1,234.56"), Decimal("1234.56"))
        self.assertEqual(to_d("  "), Decimal("0"))
        self.assertEqual(to_d(None), Decimal("0"))
        self.assertEqual(to_d(0.1), Decimal("0.1"))

    def test_net_of_tax_is_reference_only(self):
        notax, tax = approx_net_of_tax(Decimal("1130"))
        self.assertEqual(notax, Decimal("1000.00"))
        self.assertEqual(tax, Decimal("130.00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
