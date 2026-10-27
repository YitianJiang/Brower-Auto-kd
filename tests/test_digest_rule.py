# -*- coding: utf-8 -*-
"""凭证摘要口径（计价数量拆分）回归测试 —— 纯计算，不连浏览器。"""
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from digest_rule import to_d, fmt_qty, classify_rows, build_digest  # noqa: E402

# 广源那单 46 行的真实形态（物料/存货类别/计价数量）—— 按实测样例抽样构造
ROWS = [
    {"material": "TLYZ1黄浆", "category": "自制半成品", "qty": "400.00000"},
    {"material": "TLYZ1黄浆", "category": "自制半成品", "qty": "100.00000"},
    {"material": "WF-1052预印耐磨光油", "category": "自制半成品", "qty": "300.00000"},
    {"material": "WF-1062预印防滑光油", "category": "自制半成品", "qty": "250.00000"},
    {"material": "163流平剂", "category": "原材料", "qty": "10.00000"},
    {"material": "181消泡剂", "category": "原材料", "qty": "5.50000"},
    {"material": "TLYZ7绿浆", "category": "自制半成品", "qty": "120.00000"},
]


class TestFmt(unittest.TestCase):
    def test_strip_trailing_zeros(self):
        self.assertEqual(fmt_qty("400.00000"), "400")
        self.assertEqual(fmt_qty("13217.90"), "13217.9")
        self.assertEqual(fmt_qty("383.51"), "383.51")
        self.assertEqual(fmt_qty("7651.8"), "7651.8")
        self.assertEqual(fmt_qty("0"), "0")

    def test_to_d_dirty(self):
        self.assertEqual(to_d("¥1,234.56"), Decimal("1234.56"))
        self.assertEqual(to_d(None), Decimal("0"))
        self.assertEqual(to_d(""), Decimal("0"))


class TestClassify(unittest.TestCase):
    def test_split(self):
        r = classify_rows(ROWS)
        self.assertEqual(r["x"], Decimal("15.5"))     # 163流平剂10 + 181消泡剂5.5
        self.assertEqual(r["y"], Decimal("550"))      # WF-1052 300 + WF-1062 250
        self.assertEqual(r["z"], Decimal("1185.5"))
        self.assertEqual(r["s"], Decimal("620"))      # 1185.5 - 15.5 - 550
        self.assertEqual(r["n"], 7)
        self.assertEqual(r["n_raw"], 2)
        self.assertEqual(r["n_oil"], 2)
        self.assertEqual(r["both_rows"], [])

    def test_overlap_flagged(self):
        rows = ROWS + [{"material": "光油原料A", "category": "原材料", "qty": "2"}]
        r = classify_rows(rows)
        self.assertEqual(len(r["both_rows"]), 1)
        self.assertEqual(r["x"], Decimal("17.5"))
        self.assertEqual(r["y"], Decimal("552"))

    def test_match_equals(self):
        rows = [{"material": "光油", "category": "自制半成品", "qty": "3"},
                {"material": "预印防滑光油", "category": "自制半成品", "qty": "7"}]
        self.assertEqual(classify_rows(rows, match="contains")["y"], Decimal("10"))
        self.assertEqual(classify_rows(rows, match="equals")["y"], Decimal("3"))

    def test_empty(self):
        r = classify_rows([])
        self.assertEqual((r["x"], r["y"], r["z"], r["s"]), (0, 0, 0, 0))


class TestDigest(unittest.TestCase):
    def test_text(self):
        txt = build_digest("广源包装集团股份有限公司",
                           x=Decimal("15.5"), y=Decimal("550"), s=Decimal("620"))
        self.assertEqual(
            txt,
            "广源包装集团股份有限公司销售水性油墨620公斤，光油550公斤，原材料15.5公斤")

    def test_matches_historic_style(self):
        """与历史凭证里的同类摘要风格一致：客户名 + 销售 + 品名+数量+公斤"""
        txt = build_digest("江西广源包装有限公司", x="20", y="4000", s="3820")
        self.assertTrue(txt.startswith("江西广源包装有限公司销售"))
        self.assertTrue(txt.endswith("公斤"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
