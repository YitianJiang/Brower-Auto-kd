# -*- coding: utf-8 -*-
"""离线自测：`detail_align_totals` 的「写后回读 + 无进展早停」（SF-54 加固，2026-10-03）。

**不连浏览器、不动金蝶、不写任何文件** —— 纯逻辑回归，随时可跑：
    python src/selftest_align.py

覆盖三件事：
  1. 写入生效时 → 第 1 轮就达标，正常返回（**早停不得误伤正常路径**）；
  2. 写入零生效时（复现浙江至优 SF-54）→ **第 2 轮即早停**（而不是空转满 4 轮），
     且 `_fix()` 的写后回读要报「未生效」；
  3. 只写得动「不含税」时 → 仍能把不含税顶正，税额侧早停。
"""
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from steps import KingdeeUI  # noqa: E402

D = Decimal


class _Log:
    def __init__(self):
        self.lines = []

    def _p(self, tag, m):
        self.lines.append((tag, str(m)))

    def info(self, m):
        self._p("INFO", m)

    def ok(self, m):
        self._p("OK", m)

    def warn(self, m):
        self._p("WARN", m)

    def error(self, m):
        self._p("ERR", m)

    def text(self):
        return "\n".join(f"[{t}] {m}" for t, m in self.lines)

    def has(self, tag, kw):
        return any(t == tag and kw in m for t, m in self.lines)


class StubUI:
    """只实现 `detail_align_totals` 需要的那几个方法；网格就是一张内存表。"""

    AMOUNT_FIELDS = KingdeeUI.AMOUNT_FIELDS

    # field -> row key
    _F2K = {"FNOTAXAMOUNTFOR_D": "notax", "FTAXAMOUNTFOR_D": "tax",
            "FALLAMOUNTFOR_D": "all", "FDISCOUNTAMOUNTFOR": "discount"}

    def __init__(self, rows, write_ok=("notax", "tax")):
        """rows: [{seq, all, notax, tax, discount}]；write_ok: 哪些列写得动。"""
        self._rows = [dict(r) for r in rows]
        self.write_ok = set(write_ok)
        self.log = _Log()
        self.cfg = {}
        self.writes = []
        self.blurs = 0

    # ---- 被测代码依赖的接口 ----
    def amount_fields(self, kind="receivable"):
        return dict(self.AMOUNT_FIELDS[kind])

    def detail_amounts(self, kind="receivable"):
        return [dict(r) for r in self._rows]

    def detail_set_cell(self, idx, field, value, settle_ms=0):
        key = self._F2K[field]
        self.writes.append((idx, key, value))
        if key in self.write_ok:
            self._rows[idx][key] = D(value)
            # 金蝶里「价税合计」= 不含税 + 税额，写任一列都会重算
            self._rows[idx]["all"] = self._rows[idx]["notax"] + self._rows[idx]["tax"]

    def detail_blur(self, settle_ms=0):
        self.blurs += 1

    # ---- 断言辅助 ----
    def rounds_seen(self):
        n = 0
        for r in range(1, 10):
            if self.log.has("INFO", f"[对齐第 {r} 轮]"):
                n = r
        return n


FIELDS = KingdeeUI.AMOUNT_FIELDS["receivable"]["notax"]


def _rows(notax_list, tax_list):
    return [{"seq": str(i + 1),
             "notax": D(str(n)), "tax": D(str(t)),
             "all": D(str(n)) + D(str(t)), "discount": D("0")}
            for i, (n, t) in enumerate(zip(notax_list, tax_list))]


def case_ok_write():
    """① 写入生效 → 正常收敛，且**不得**触发早停（早停不得误伤正常路径）。"""
    ui = StubUI(_rows([1000, 2000, 3000], [130, 260, 390]))
    s = KingdeeUI.detail_align_totals(ui, target_notax="6100.00", target_tax="880.00")
    assert s["ok"], f"应当达标，实际 {s}"
    assert not ui.log.has("WARN", "无进展"), "正常路径不得触发早停"
    assert not ui.log.has("WARN", "未生效"), "正常路径不得报「写入未生效」"
    assert s["notax"] == D("6100") and s["tax"] == D("880"), f"末值不对：{s}"
    assert ui.blurs >= 1, "写入后应当 blur 触发联动"
    return (f"正常收敛（{ui.rounds_seen()} 轮）· 写入 {len(ui.writes)} 次 · "
            f"不含税 {s['notax']} / 税额 {s['tax']} · 未触发早停")


def case_dead_write():
    """② 写入零生效（复现 SF-54）→ 第 2 轮早停 + 回读报未生效。"""
    ui = StubUI(_rows([1000, 2000, 3000], [130, 260, 390]), write_ok=())
    s = KingdeeUI.detail_align_totals(ui, target_notax="6100.00", target_tax="880.00")
    assert not s["ok"], "零生效不可能达标"
    assert ui.rounds_seen() == 2, f"应在第 2 轮早停，实际跑了 {ui.rounds_seen()} 轮"
    assert ui.log.has("WARN", "未生效"), "`_fix` 应报「写入**未生效**」"
    assert ui.log.has("WARN", "无进展"), "应报「对齐无进展」并早停"
    assert not ui.log.has("INFO", "[对齐第 3 轮]"), "不得空转第 3 轮"
    return (f"第 {ui.rounds_seen()} 轮早停（对照线上空转 4 轮）· "
            f"写入 {len(ui.writes)} 次全被回读拆穿")


def case_partial_write():
    """③ 只写得动不含税 → 不含税顶正，税额侧早停。"""
    ui = StubUI(_rows([1000, 2000, 3000], [130, 260, 390]), write_ok=("notax",))
    s = KingdeeUI.detail_align_totals(ui, target_notax="6100.00", target_tax="880.00")
    assert abs(s["notax"] - D("6100.00")) < D("0.01"), f"不含税应顶正，实际 {s['notax']}"
    assert not s["ok"], "税额写不动，整体不应 ok"
    assert ui.log.has("WARN", "无进展"), "税额侧无进展应早停"
    return f"不含税 {s['notax']} 已顶正 · 税额 {s['tax']} 写不动 → 早停"


def main():
    for i, fn in enumerate((case_ok_write, case_dead_write, case_partial_write), 1):
        try:
            msg = fn()
        except AssertionError as e:
            print(f"✗ 用例 {i} {fn.__name__}：{e}")
            return 1
        print(f"✓ 用例 {i} {fn.__name__}：{msg}")
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
