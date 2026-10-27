# -*- coding: utf-8 -*-
"""
凭证摘要口径：按「存货类别 / 物料名称」把计价数量拆成 水性油墨 / 光油 / 原材料
=========================================================================
用户口径（2026-09-21 定，2026-10-04 补「桶」）：

    在应收单详情页的明细里：
        x = 存货类别 = 原材料   的行的「计价数量 FPRICEQTY」之和
        y = 物料名称 含「光油」 的行的「计价数量」之和
        t = 存货类别 = 包装物   的行的「计价数量」之和（桶，单位「个」）
        z = 全部行的「计价数量」之和
        s = z - x - y - t
    摘要 = 「{客户名}销售水性油墨{s}公斤，光油{y}公斤，原材料{x}公斤，桶{t}个」
    桶（包装物）单独列，不计入水性油墨，单位是「个」（不是公斤）。

历史上的同类摘要（从 GL_VOUCHER 里查到的真样例）：
    「广源包装集团股份有限公司销售水墨6280公斤，光油5180公斤」
    「江西广源包装有限公司光油4000公斤，水墨3820公斤，原材料20公斤」
    「扬州祥恒包装有限公司销售水性油墨11491.5千克」
本次按用户口头措辞用「水性油墨」+「公斤」。

⚠️ 纯计算模块，不依赖浏览器，可离线单测。
"""
from decimal import Decimal, ROUND_HALF_UP

__all__ = ["to_d", "fmt_qty", "classify_rows", "build_digest"]

CENT = Decimal("0.01")


def to_d(x) -> Decimal:
    """安全转 Decimal（走 str 中转，兼容 '400.00000' / '¥1,234.56' / None）"""
    if isinstance(x, Decimal):
        return x
    if x is None or x == "":
        return Decimal("0")
    if isinstance(x, str):
        s = x.replace("¥", "").replace(",", "").strip()
        if not s:
            return Decimal("0")
        return Decimal(s)
    return Decimal(str(x))


def fmt_qty(q, nd=2):
    """数量转成摘要里的写法：最多 nd 位小数，去掉末尾多余的 0。

    实测样例对照：400.00000 → '400'；13217.90 → '13217.9'；383.51 → '383.51'
    """
    d = to_d(q).quantize(CENT, rounding=ROUND_HALF_UP) if nd == 2 \
        else to_d(q).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def classify_rows(rows, category_field="category", name_field="material",
                  qty_field="qty",
                  raw_category="原材料", oil_keyword="光油",
                  pack_category="包装物",
                  match="contains"):
    """按口径把明细拆成四类数量。

    Args:
        rows: [{"category": "自制半成品", "material": "TLYZ1黄浆", "qty": "400.00000"}, ...]
        pack_category: 存货类别=该值的行视为「桶」（包装物），单位「个」。
        match: "contains"（默认，用户口径「包含光油这两个字」）| "equals"
    Returns:
        dict(x, y, t, z, s, n, n_raw, n_oil, n_pack, both_rows, overlap, raw_names)
        - x/y/t/z/s 均为 Decimal；t=桶（包装物）数量
        - both_rows: 同时命中「原材料」和「光油」的行（会被重复计，需人工确认）
    """
    x = y = t = z = Decimal("0")
    n_raw = n_oil = n_pack = 0
    both, raw_names = [], []
    for i, r in enumerate(rows):
        cat = str(r.get(category_field) or "").strip()
        name = str(r.get(name_field) or "").strip()
        q = to_d(r.get(qty_field))
        z += q
        is_raw = cat == raw_category
        is_oil = (oil_keyword in name) if match == "contains" else (name == oil_keyword)
        is_pack = cat == pack_category
        if is_raw:
            x += q
            n_raw += 1
            if cat not in raw_names:
                raw_names.append(cat)
        if is_oil:
            y += q
            n_oil += 1
        if is_pack:
            t += q
            n_pack += 1
        if is_raw and is_oil:
            both.append({"i": i, "material": name, "category": cat, "qty": str(q)})
    return {
        "x": x, "y": y, "t": t, "z": z, "s": z - x - y - t, "n": len(rows),
        "n_raw": n_raw, "n_oil": n_oil, "n_pack": n_pack,
        "both_rows": both, "raw_names": raw_names,
    }


def build_digest(company, x, y, s, unit="公斤", omit_zero=False, t=None, t_unit="个"):
    """拼摘要文本（用户口径）。

    omit_zero=False（默认，2026-09-21 广源口径）：
        `{公司}销售水性油墨{s}公斤，光油{y}公斤，原材料{x}公斤`  —— 三段全写
    omit_zero=True（2026-09-22 用户确认，用于「某类为 0」的客户）：
        为 0 的段**不写**，措辞仍是「销售水性油墨」——
        `{公司}销售水性油墨{s}公斤，原材料{x}公斤`（y=0 时不出现「光油」段）
    依据：这两家客户的历史凭证摘要一律只列非零项，例如
        `杭州小章印刷材料有限公司水墨460公斤，原材料2000公斤`
        `慈溪市润科包装材料有限公司水墨400千克，原材料100千克`
    桶（2026-10-04 补）：`t` 非空且 >0 时追加「桶{t}{t_unit}」段（默认「个」），
        t=0 时一律不写（与 omit_zero 无关）。桶不计入水性油墨，单位是「个」不是「公斤」。
    兜底：若三段都为 0（理论上无明细行），仍保留「水性油墨」段，避免拼出空摘要。
    """
    parts = []
    if not omit_zero or to_d(s) != 0:
        parts.append(f"水性油墨{fmt_qty(s)}{unit}")
    if not omit_zero or to_d(y) != 0:
        parts.append(f"光油{fmt_qty(y)}{unit}")
    if not omit_zero or to_d(x) != 0:
        parts.append(f"原材料{fmt_qty(x)}{unit}")
    if t is not None and to_d(t) != 0:
        parts.append(f"桶{fmt_qty(t)}{t_unit}")
    if not parts:                      # 全 0 时兜底
        parts.append(f"水性油墨{fmt_qty(s)}{unit}")
    return f"{company}销售" + "，".join(parts)
