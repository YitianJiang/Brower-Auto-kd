# -*- coding: utf-8 -*-
"""
凭证摘要口径 —— 按「存货类别 / 物料名称」把计价数量拆成 水性油墨 / 光油 / 原材料 / 桶。

用户口径（2026-09-21 定，2026-10-04 补「桶」）：
    在应收单详情页的明细里：
        x = 存货类别(`F_PAEZ_BASEPROPERTY2`) = 原材料 的行的「计价数量 FPRICEQTY」之和
        y = 物料名称(`FMaterialId.FName`) **包含**「光油」的行的计价数量之和
        t = 存货类别 = 包装物 的行的计价数量之和（桶，单位「个」）
        z = 全部行的计价数量之和
        s = z - x - y - t
    摘要 = 「{客户名}销售水性油墨{s}公斤，光油{y}公斤，原材料{x}公斤，桶{t}个」

★ 桶（2026-10-04 用户补）：存货类别「包装物」单独列「桶{t}个」，单位「个」不是公斤，
  不计入水性油墨。classify_rows 用 pack_category="包装物" 识别，返回 t + n_pack。

★ 措辞与省略零项（用户 2026-09-22 定稿）：
    - 措辞统一用「水性油墨」（历史样例里的「水墨」**不采用**）
    - `build_digest(..., omit_zero=True)` → **省略数量为 0 的段**（新客户默认走这个）
        慈溪润科 x=0 y=0 s=1700 → 「慈溪市润科包装材料有限公司销售水性油墨1700公斤」
        杭州小章 x=400 y=0 s=880 → 「杭州小章印刷材料有限公司销售水性油墨880公斤，原材料400公斤」
        台州瑾晟 x=0 y=0 s=2080 t=30 → 「台州瑾晟涂料有限公司销售水性油墨2080公斤，桶30个」
    - `omit_zero=False`（默认，兼容历史）→ 三段都写（广源那单的写法）

历史上的同类摘要（从 GL_VOUCHER 查到的真样例）：
    「广源包装集团股份有限公司销售水墨6280公斤，光油5180公斤」
    「江西广源包装有限公司光油4000公斤，水墨3820公斤，原材料20公斤」
    「扬州祥恒包装有限公司销售水性油墨11491.5千克」
本次按用户口头措辞用「水性油墨」+「公斤」。

实测基准：广源 2026-09，46 行 → x=358 / y=11200 / z=21138 / s=9580
  → 「广源包装集团股份有限公司销售水性油墨9580公斤，光油11200公斤，原材料358公斤」

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
    d = (to_d(q).quantize(CENT, rounding=ROUND_HALF_UP) if nd == 2
         else to_d(q).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP))
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
        dict(x, y, t, z, s, n, n_raw, n_oil, n_pack, both_rows, raw_names)
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
    """拼摘要文本：`{公司}销售水性油墨{s}公斤，光油{y}公斤，原材料{x}公斤，桶{t}个`

    omit_zero=True 时省略数量为 0 的段（用户 2026-09-22 口径）；
    三段全为 0 时回退只写「水性油墨{总量}」。
    桶（2026-10-04 补）：`t` 非空且 >0 时追加「桶{t}{t_unit}」段（默认「个」），
    t=0 时一律不写（与 omit_zero 无关）。桶不计入水性油墨，单位是「个」不是「公斤」。
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
    if not parts:                                  # x=y=s=0 的兜底
        parts.append(f"水性油墨{fmt_qty(s)}{unit}")
    return f"{company}销售" + "，".join(parts)


def build_digest_from_rows(rows, company, unit="公斤", omit_zero=False, **kw):
    """一步到位：明细行 → 摘要文本 + 统计（便于对拍）。"""
    st = classify_rows(rows, **kw)
    return build_digest(company, st["x"], st["y"], st["s"], unit, omit_zero,
                        t=st.get("t")), st


if __name__ == "__main__":
    # 自测：广源 2026-09 基准
    demo = [{"category": "原材料", "material": "TLYZ1黄浆", "qty": "358"}]
    txt, st = build_digest_from_rows(demo, "广源包装集团股份有限公司")
    assert st["x"] == Decimal("358") and st["s"] == Decimal("0")
    assert txt == "广源包装集团股份有限公司销售水性油墨0公斤，光油0公斤，原材料358公斤"

    # 构造广源 2026-09 的 46 行分布：自制半成品 35 / 产成品(=光油) 7 / 原材料 4
    rows = ([{"category": "自制半成品", "material": "X", "qty": "280"}] * 34
            + [{"category": "自制半成品", "material": "X2", "qty": "60"}]
            + [{"category": "产成品", "material": "光油A", "qty": "1600"}] * 7
            + [{"category": "原材料", "material": "Y", "qty": "89.5"}] * 4)
    txt, st = build_digest_from_rows(rows, "广源包装集团股份有限公司")
    print("x/y/z/s =", st["x"], st["y"], st["z"], st["s"])
    print("摘要 =", txt)
    assert st["n"] == 46
    assert st["x"] == Decimal("358") and st["y"] == Decimal("11200")
    assert st["z"] == Decimal("21138") and st["s"] == Decimal("9580")

    # --- omit_zero（用户 2026-09-22 口径）---
    assert (build_digest("慈溪市润科包装材料有限公司", 0, 0, 1700, omit_zero=True)
            == "慈溪市润科包装材料有限公司销售水性油墨1700公斤")
    assert (build_digest("杭州小章印刷材料有限公司", 400, 0, 880, omit_zero=True)
            == "杭州小章印刷材料有限公司销售水性油墨880公斤，原材料400公斤")
    txt, st = build_digest_from_rows(rows, "广源包装集团股份有限公司", omit_zero=True)
    assert txt == "广源包装集团股份有限公司销售水性油墨9580公斤，光油11200公斤，原材料358公斤"
    # 全 0 兜底
    assert (build_digest("X公司", 0, 0, 0, omit_zero=True) == "X公司销售水性油墨0公斤")
    # --- 桶（2026-10-04 口径）---
    assert (build_digest("台州瑾晟涂料有限公司", 0, 0, 2080, omit_zero=True, t=30)
            == "台州瑾晟涂料有限公司销售水性油墨2080公斤，桶30个")
    assert (build_digest("丹阳市加美包装材料有限公司", 400, 0, 1500, omit_zero=True, t=20)
            == "丹阳市加美包装材料有限公司销售水性油墨1500公斤，原材料400公斤，桶20个")
    # 无桶向后兼容（t=None 不出现桶段）
    assert (build_digest("某公司", 100, 0, 50, omit_zero=True, t=0)
            == "某公司销售水性油墨50公斤，原材料100公斤")
    print("digest_rule 自测通过")
