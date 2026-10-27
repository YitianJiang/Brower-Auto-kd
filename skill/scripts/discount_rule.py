# -*- coding: utf-8 -*-
"""
折扣分摊与尾差校正 —— **纯计算**模块（不依赖浏览器，可离线单测）。

背景（用户口径，2026-09-21 广源包装集团确立）
------------------------------------------------
一张含扣款的发票：原始价税合计 A，实际开票少收一笔扣款 D（例 A=304,245.60，D=2,733.50）。
三步：
1. **折扣额按行分摊**：每行折扣 = round(D × 该行含税 ÷ 全部行含税合计, 2)
2. **尾差落最大行**：逐行舍入的尾差补到「含税金额最大的那一行」
3. **目标对齐**：折扣后总不含税/总税额与实际开票差在 0.1 内时，改**金额最大的行**顶到目标

★★ 最重要的一条（不看就会算错）
    改「折扣额」    → 价税合计 = 含税单价×数量 − 折扣额，再反算不含税/税额
    改「不含税金额」→ 价税合计 = 不含税 + 税额（税额不变）
    改「税额」      → 价税合计 = 不含税 + 税额（不含税不变）
    ⇒ 价税合计只能靠「折扣额/含税单价」精确控制；不含税与税额是分厘级微调旋钮。
    ⇒ 而且金蝶的「不含税」**不是** `折后含税 ÷ (1+税率)`，它走**单价反算**，差 1~2 分。
      所以**真正的对齐必须走页面读数闭环**（读实际合计 → 算差 → 写 → 再读），
      本模块的离线值只能当"参考量级"。

⚠️ 符号约定（两个方向别搞反）：
    `plan_alignment()["diff"]`   = 目标 − 当前
    `summarize()["d_notax"]`     = **当前 − 目标**
    写单元格时用 `当前值 − summarize 的 d`；用 `+` 会越修越远（实测 0.02→0.04→0.08 跑飞）。
"""
from decimal import Decimal, ROUND_HALF_UP

__all__ = ["to_d", "allocate_by_amount", "pick_anchor_row", "approx_net_of_tax",
           "plan_alignment", "summarize", "group_rows", "plan_material_align",
           "apply_material_align"]

CENT = Decimal("0.01")
ZERO = Decimal("0")


def to_d(x) -> Decimal:
    """安全转 Decimal（走 str 中转，避免 float 二进制表示污染）"""
    if isinstance(x, Decimal):
        return x
    if x is None or x == "":
        return ZERO
    if isinstance(x, str):
        s = x.replace("¥", "").replace(",", "").strip()
        if not s:
            return ZERO
        return Decimal(s)
    return Decimal(str(x))


def _q(x: Decimal) -> Decimal:
    """四舍五入到分"""
    return to_d(x).quantize(CENT, rounding=ROUND_HALF_UP)


def pick_anchor_row(amounts, by="amount"):
    """选出「锚点行」= 金额最大的那一行（并列时取序号靠前的）。"""
    vals = [to_d(a) for a in amounts]
    if not vals:
        raise ValueError("amounts 为空，无法选出锚点行")
    return max(range(len(vals)), key=lambda i: (vals[i], -i))


def allocate_by_amount(amounts, total_discount, anchor_fix=True):
    """按行金额占比把扣款分摊到各行折扣额。

    公式：该行折扣额 = round(扣款总额 × 该行含税金额 ÷ 含税合计, 2)
    Returns: (discs, info)   discs = 每行应填的折扣额（Decimal 列表）
    """
    amts = [to_d(a) for a in amounts]
    if not amts:
        raise ValueError("amounts 为空")
    base = sum(amts, ZERO)
    if base == 0:
        raise ValueError("含税合计为 0，无法按占比分摊")

    want = to_d(total_discount)
    raw = [want * a / base for a in amts]
    discs = [_q(r) for r in raw]
    before = sum(discs, ZERO)
    gap = want - before
    anchor = pick_anchor_row(amts)
    if anchor_fix and gap != 0:
        discs[anchor] = discs[anchor] + gap
    return discs, {"base": base, "want": want, "sum_before_fix": before, "gap": gap,
                   "anchor": anchor, "anchor_fixed": bool(anchor_fix and gap != 0),
                   "sum_after_fix": sum(discs, ZERO), "raw": raw}


def approx_net_of_tax(amount, tax_rate=Decimal("0.13")):
    """参考用：折后含税 → (不含税, 税额) 理论值。⚠️ 仅预览，金蝶走单价反算会有 1~2 分差。"""
    a = to_d(amount)
    r = to_d(tax_rate)
    notax = _q(a / (Decimal("1") + r))
    return notax, _q(a - notax)


def plan_alignment(rows, disc_field="disc", all_field="all",
                   target_notax=None, target_tax=None, anchor=None):
    """离线预估「尾差校正方案」（参考用，实际以页面闭环为准）。

    Returns: {anchor, anchor_seq, cur, target, diff, suggest, need_fix}
             diff = 目标 − 当前
    """
    if not rows:
        raise ValueError("rows 为空")
    if anchor is None:
        anchor = pick_anchor_row([r.get(all_field, 0) for r in rows])

    cur_notax = sum((to_d(r.get("notax", 0)) for r in rows), ZERO)
    cur_tax = sum((to_d(r.get("tax", 0)) for r in rows), ZERO)
    cur_all = sum((to_d(r.get(all_field, 0)) for r in rows), ZERO)
    out = {"anchor": anchor, "anchor_seq": rows[anchor].get("seq"),
           "cur": {"notax": cur_notax, "tax": cur_tax, "all": cur_all},
           "target": {"notax": to_d(target_notax) if target_notax is not None else None,
                      "tax": to_d(target_tax) if target_tax is not None else None},
           "diff": {}, "suggest": {}}
    if target_notax is not None:
        d = to_d(target_notax) - cur_notax
        out["diff"]["notax"] = d
        out["suggest"]["notax"] = _q(to_d(rows[anchor].get("notax", 0)) + d)
    if target_tax is not None:
        d = to_d(target_tax) - cur_tax
        out["diff"]["tax"] = d
        out["suggest"]["tax"] = _q(to_d(rows[anchor].get("tax", 0)) + d)
    out["need_fix"] = any(abs(v) >= CENT for v in out["diff"].values())
    return out


def group_rows(rows, key="material"):
    """按 key 给明细行分组（保持首次出现顺序）。Returns: (order, groups)"""
    order, groups = [], {}
    for i, r in enumerate(rows):
        name = str(r.get(key) or "")
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(i)
    return order, groups


def plan_material_align(rows, key="material", tax_rate=Decimal("0.13"),
                        all_field="all", notax_field="notax", tax_field="tax"):
    """★ 让「应收单逐物料金额」= 「专票（分录合并）同物料金额」→ **不产生 -00000N 子单**。

    原理（实证见 references/sub-bills-00000N.md）
        应收单每行不含税   = round(该行折后含税 ÷ (1+税率), 2)      ← 逐行舍入
        专票（分录合并）行 = round(物料折后含税**合计** ÷ (1+税率), 2) ← 汇总后舍入
    同一物料两种舍入顺序可能差 1 分；金蝶在【发票提交】回写时把分币差拆成子单。

    做法：差 δ 时取该物料**金额最大的一行**：不含税 += δ、税额 −= δ
          （一增一减 → 该行价税合计不变 → 折扣分摊口径不变）

    ⚠️ 必须在**下推专票之前、应收单还没审核时**做（已审核需先反审核）。
    ⚠️ 目标值应取「专票真实逐物料金额」，别用 round(含税/1.13) 猜
        （掰平会移动总不含税 `delta_total`，猜错总账对不上）。
    """
    if not rows:
        raise ValueError("rows 为空")
    order, groups = group_rows(rows, key)
    mats, adjusts = [], []
    delta_total = ZERO
    for name in order:
        idxs = groups[name]
        cur = sum((to_d(rows[i].get(notax_field, 0)) for i in idxs), ZERO)
        gross = sum((to_d(rows[i].get(all_field, 0)) for i in idxs), ZERO)
        want, _wtax = approx_net_of_tax(gross, tax_rate)
        delta = want - cur
        anchor_row = None
        if delta != 0:
            local = pick_anchor_row([to_d(rows[i].get(all_field, 0)) for i in idxs])
            anchor_row = idxs[local]
            adjusts.append((anchor_row, delta, -delta))
            delta_total += delta
        mats.append({"name": name, "rows": idxs, "n": len(idxs), "gross": gross,
                     "cur_notax": cur, "want_notax": want, "delta": delta,
                     "anchor_row": anchor_row})
    return {"materials": mats, "adjusts": adjusts,
            "delta_total": delta_total, "need_fix": bool(adjusts)}


def apply_material_align(rows, plan, notax_field="notax", tax_field="tax"):
    """在内存里套用 plan 的结果（dry-run / 单测用）。返回**新**列表，每行 all 不变。"""
    out = [dict(r) for r in rows]
    for idx, d_notax, d_tax in plan.get("adjusts") or []:
        out[idx][notax_field] = _q(to_d(out[idx].get(notax_field, 0)) + d_notax)
        out[idx][tax_field] = _q(to_d(out[idx].get(tax_field, 0)) + d_tax)
    return out


def summarize(rows, all_field="all", disc_field="disc",
              target_notax=None, target_tax=None, tol=Decimal("0.01")):
    """汇总当前 4 个合计 + 与目标的差。

    ⚠️ 符号：`d_notax`/`d_tax` = **当前 − 目标**（与 plan_alignment 的 diff 相反）。
       写单元格用 `当前值 − d`。
    """
    notax = sum((to_d(r.get("notax", 0)) for r in rows), ZERO)
    tax = sum((to_d(r.get("tax", 0)) for r in rows), ZERO)
    allamt = sum((to_d(r.get(all_field, 0)) for r in rows), ZERO)
    disc = sum((to_d(r.get(disc_field, 0)) for r in rows), ZERO)
    out = {"n": len(rows), "notax": notax, "tax": tax, "all": allamt, "disc": disc}
    ok = True
    if target_notax is not None:
        out["d_notax"] = notax - to_d(target_notax)
        ok = ok and abs(out["d_notax"]) <= tol
    if target_tax is not None:
        out["d_tax"] = tax - to_d(target_tax)
        ok = ok and abs(out["d_tax"]) <= tol
    out["ok"] = ok
    return out


if __name__ == "__main__":
    # 自测：广源那单的扣款分摊
    amts = ["1000.00", "2000.00", "3000.00", "6000.00"]
    discs, info = allocate_by_amount(amts, "2733.50")
    print("分摊：", [str(d) for d in discs], "合计=", info["sum_after_fix"],
          "锚点行=", info["anchor"])
    assert info["sum_after_fix"] == Decimal("2733.50")
    print("discount_rule 自测通过")
