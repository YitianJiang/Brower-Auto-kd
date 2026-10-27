# -*- coding: utf-8 -*-
"""
折扣分摊与尾差校正 —— 纯计算模块（不依赖浏览器/playwright，可离线单测）
======================================================================

背景（用户口径，2026-09-21 广源包装集团确立）
------------------------------------------------
供应商给了一张含扣款的发票：出库单/应收单的原始价税合计是 A，实际开票时
少收了一笔扣款 D（例：A=304,245.60，D=2,733.50）。要求：

1. **折扣额按行分摊**：每行折扣 = D × 该行含税金额 ÷ 全部行含税合计，逐行
   四舍五入到分。逐行舍入后合计常常差几分（尾差）。
2. **尾差落到最大行**：把尾差补到「含税金额最大的那一行」的折扣额上。
3. **目标对齐**：折扣后总不含税 / 总税额要与实际开票完全一致；若偏差在
   0.1 以内，**改金额最大的那一行**的不含税金额和税额把总额顶到目标值。

为什么要有这个模块
------------------
之前这些计算散在 gy_fill.py / gy_adjust.py / gy_adjust2.py 三个一次性脚本里，
扣款金额、目标值、行号全靠硬编码 → 换一个客户就得重新写脚本。
现在把**算法**抽出来：输入行金额 + 扣款 + 目标，输出「每行填什么值」。

★ 重要：金蝶行内联动有它自己的取整怪癖
--------------------------------------
实测（见 docs/界面结构速查.md）：
    改「折扣额」    → 价税合计 = 含税单价×数量 − 折扣额，再反算不含税/税额
    改「不含税金额」 → 价税合计 = 不含税 + 税额（税额不变）
    改「税额」      → 价税合计 = 不含税 + 税额（不含税不变）
而且金蝶的「不含税」**不是**简单「折后含税 ÷ (1+税率)」，走单价反算会差 1~2 分。
所以本模块的离线预估只能当「参考量级」，**真正的对齐必须走页面读数闭环**
（见 steps.py 的 `detail_align_totals`，它每轮读实际合计再算差，不靠公式回算）。
"""
from decimal import Decimal, ROUND_HALF_UP

__all__ = [
    "to_d", "allocate_by_amount", "pick_anchor_row",
    "approx_net_of_tax", "plan_alignment", "summarize",
    "group_rows", "plan_material_align", "apply_material_align",
]

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
    """选出「锚点行」= 金额最大的那一行（并列时取序号靠前的）。

    Args:
        amounts: 每行含税金额（用于比较的权重）
        by:      "amount" 按含税金额最大
    Returns:
        行索引（0-based）
    """
    vals = [to_d(a) for a in amounts]
    if not vals:
        raise ValueError("amounts 为空，无法选出锚点行")
    # 索引小的优先：用 (值降序, 索引升序)
    return max(range(len(vals)), key=lambda i: (vals[i], -i))


def allocate_by_amount(amounts, total_discount, anchor_fix=True):
    """按行金额占比把扣款分摊到各行折扣额。

    公式： 该行折扣额 = round(扣款总额 × 该行含税金额 ÷ 含税合计, 2)

    Args:
        amounts:       每行含税金额（价税合计），列表
        total_discount: 扣款总额（价税合计口径）
        anchor_fix:     True = 把逐行舍入产生的尾差补到最大行（用户口径）
    Returns:
        (discs, info)
        discs: list[Decimal]  每行应填的折扣额
        info:  dict           诊断信息（含税合计、分摊前后合计、尾差、锚点行）
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

    return discs, {
        "base": base,
        "want": want,
        "sum_before_fix": before,
        "gap": gap,
        "anchor": anchor,
        "anchor_fixed": bool(anchor_fix and gap != 0),
        "sum_after_fix": sum(discs, ZERO),
        "raw": raw,
    }


def approx_net_of_tax(amount, tax_rate=Decimal("0.13")):
    """参考用：折后含税 → (不含税, 税额) 的理论值。

    ⚠️ 只用于**预览**。金蝶实际走单价反算，会有 1~2 分差，
       真正对账必须读页面合计，不要拿这个值去写单元格。
    """
    a = to_d(amount)
    r = to_d(tax_rate)
    notax = _q(a / (Decimal("1") + r))
    return notax, _q(a - notax)


def plan_alignment(rows, disc_field="disc", all_field="all",
                   target_notax=None, target_tax=None, anchor=None):
    """离线预估「尾差校正方案」（参考用，实际以页面闭环为准）。

    Args:
        rows:    [{"seq":..., "all":含税, "disc":折扣, "notax":不含税, "tax":税额}, ...]
        target_notax / target_tax: 目标合计（None 表示不校这一项）
        anchor:  指定锚点行索引；None = 自动选含税最大的行
    Returns:
        dict: {anchor, cur:{notax,tax,all}, target:{...}, diff:{...}, suggest:{...},
               need_fix: bool}
    """
    if not rows:
        raise ValueError("rows 为空")
    if anchor is None:
        anchor = pick_anchor_row([r.get(all_field, 0) for r in rows])

    cur_notax = sum((to_d(r.get("notax", 0)) for r in rows), ZERO)
    cur_tax = sum((to_d(r.get("tax", 0)) for r in rows), ZERO)
    cur_all = sum((to_d(r.get(all_field, 0)) for r in rows), ZERO)

    out = {
        "anchor": anchor,
        "anchor_seq": rows[anchor].get("seq"),
        "cur": {"notax": cur_notax, "tax": cur_tax, "all": cur_all},
        "target": {"notax": to_d(target_notax) if target_notax is not None else None,
                   "tax": to_d(target_tax) if target_tax is not None else None},
        "diff": {},
        "suggest": {},
    }
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
    """按 key 给明细行分组（保持首次出现顺序）。

    Returns:
        (order, groups)
        order  = [分组名, ...]        按在 rows 里首次出现的先后
        groups = {分组名: [行索引, ...]}
    rows 里 key 为空的行归到 "" 组（= 「未分组」）。
    """
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
    """★ 让「应收单逐物料金额」= 「专票（分录合并）同物料金额」→ 不产生 -00000N 子单。

    原理（实证见 docs/广源应收单子单-原因分析.md）
    ----------------------------------------------------
        应收单每行不含税   = round(该行折后含税 ÷ (1+税率), 2)      ← 逐行舍入
        专票（分录合并）行 = round(物料折后含税**合计** ÷ (1+税率), 2) ← 汇总后舍入

    同一物料两种舍入顺序可能差 1 分；金蝶在【发票提交】做开票回写时，
    把这些分币差拆成 `-00000N` 子单承载（广源那次出了 6 张，净差 0）。

    本函数把应收单的逐行值掰到「汇总后舍入」的同一口径：
        差 δ 时取该物料**金额最大的一行**做：不含税 += δ、税额 −= δ。
        ▸ 两者一增一减 → 该行「价税合计 = 不含税 + 税额」不变
        ▸ 折后含税不变 → 折扣分摊口径不变，四个合计也不受影响

    ⚠️ 这是「事前对齐」方案：必须在**下推专票之前、应收单还没审核时**做。
       若应收单已审核，需要先反审核（此时还没下推专票，反审核不会被下游挡住）。

    Returns:
        dict:
            materials:   [{name, rows, n, gross, cur_notax, want_notax, delta, anchor_row}]
            adjusts:     [(行索引, d_notax, d_tax), ...]   只在 delta != 0 时给出
            delta_total: 所有 δ 之和（= 掰平后总不含税会变动多少；理想为 0）
            need_fix:    bool（是否真有物料需要掰）
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
                     "cur_notax": cur, "want_notax": want,
                     "delta": delta, "anchor_row": anchor_row})
    return {"materials": mats, "adjusts": adjusts,
            "delta_total": delta_total, "need_fix": bool(adjusts)}


def apply_material_align(rows, plan, notax_field="notax", tax_field="tax"):
    """在内存里套用 plan_material_align 的结果（用于 dry-run 验算 / 单测）。

    返回**新的**行列表（不改原对象）。每行 all 保持不变。
    """
    out = [dict(r) for r in rows]
    for idx, d_notax, d_tax in plan.get("adjusts") or []:
        out[idx][notax_field] = _q(to_d(out[idx].get(notax_field, 0)) + d_notax)
        out[idx][tax_field] = _q(to_d(out[idx].get(tax_field, 0)) + d_tax)
    return out


def summarize(rows, all_field="all", disc_field="disc",
              target_notax=None, target_tax=None, target_all=None,
              tol=Decimal("0.01")):
    """汇总当前 4 个合计 + 与目标的差，返回 dict（打印/断言都用它）

    ⚠️ 符号约定：`d_notax` / `d_tax` / `d_all` = **当前 − 目标**（要和目标比才知道差多少）。
       `plan_alignment()` 里的 `diff` 是 **目标 − 当前**，两者符号相反 ——
       写单元格时必须用 `当前值 − summarize 的 d`，用 `+` 会越修越远。

    ★ 2026-10-03（用户口径「三个数都精确到分」）：
       `target_all` 让「价税合计」也独立参与判定。由于金额都已 quantize 到分，
       差只会是 0 或整分（不存在 0.001 中间态），所以「两位小数完全一致」= 差 == 0。
    """
    notax = sum((to_d(r.get("notax", 0)) for r in rows), ZERO)
    tax = sum((to_d(r.get("tax", 0)) for r in rows), ZERO)
    allamt = sum((to_d(r.get(all_field, 0)) for r in rows), ZERO)
    disc = sum((to_d(r.get(disc_field, 0)) for r in rows), ZERO)
    tol = to_d(tol)
    out = {"n": len(rows), "notax": notax, "tax": tax, "all": allamt, "disc": disc}
    ok = True
    if target_notax is not None:
        out["d_notax"] = notax - to_d(target_notax)
        ok = ok and abs(out["d_notax"]) <= tol
    if target_tax is not None:
        out["d_tax"] = tax - to_d(target_tax)
        ok = ok and abs(out["d_tax"]) <= tol
    if target_all is not None:
        out["d_all"] = allamt - to_d(target_all)
        ok = ok and abs(out["d_all"]) <= tol
    out["ok"] = ok
    return out
