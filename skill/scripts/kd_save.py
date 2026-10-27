# -*- coding: utf-8 -*-
"""
保存 / 提交的**成败判定** —— 核心是"别只信没抛异常"。

★★ 判据优先级：查询回读（权威） > 界面 FBILLNO 非空 > 无弹窗
   真实教训：保存时弹「字段必录提示 / [开票信息.复核人] 是必填项，不能为空！」，
   而 save 本身不抛异常、截图正常 —— 但**界面 FBILLNO 恒空、数据库查不到这张单**。
   ⇒ "保存成功"可能是假的。

依赖：kd_core.py（可选 kd_query.py 做落库回读）
用法：
    r = save_and_verify(page, kq=kq, form_id="IV_SALESIC")
    if not r["ok"]:
        print(r["dialogs"])      # 一眼看到"是必填项，不能为空"

★★ 本模块只做**保存**。若你要在附近加"删除/作废"逻辑，先看 kd_guard.py：
   **不能删除凭证**（红线，硬拦截，与 config / --yes 无关），入口先
   `assert_allowed("delete_voucher")` 或 `assert_click_safe(菜单文案)`。
"""
from __future__ import annotations

from kd_core import VIS_JS, eval_all_frames, visible_dialogs, mouse_click_center

# 失败弹窗的判据词（命中了就是"其实没成功"）
BAD_WORDS = ("必填项", "不能为空", "不能小于", "不能大于", "校验不通过",
             "不允许", "错误", "失败")


def read_bill_no(page, field="FBILLNO"):
    """读界面上的单据编号。**空 = 保存没成功的最灵敏信号**。"""
    js = VIS_JS + """(f) => {
        const el = Array.from(document.querySelectorAll('[id$="-' + f + '-EDITOR_c"]')).filter(vis)[0];
        return {value: el ? (el.value || '').trim() : null};
    }"""
    return eval_all_frames(page, js, field)


def click_save(page):
    """点「保存」—— 必须真实鼠标 + 可见性过滤（隐藏页面的同名按钮会吃掉点击）。

    ⚠️ `.kdmenuitem-content` 里保存/提交/审核 文本完全相同；
       只按文本取第一个 = 点到隐藏页面 → 静默无效、截图看不出。
    """
    mouse_click_center(page, '.kdmenuitem-content', must_contain='保存')
    page.wait_for_timeout(2500)


def save_and_verify(page, kq=None, form_id=None, bill_no=None,
                    bill_no_field="FBillNo", bad_words=BAD_WORDS):
    """保存并判定成败。

    Args:
        kq:  KdQuery 实例（可选）。给了就做**落库回读**（权威判据）。
        form_id / bill_no: 落库回读用（如 "IV_SALESIC" + 刚生成的单号）。
            bill_no=None 时会先读界面 FBILLNO；仍为空则跳过落库回读（判失败）。
    Returns:
        {"ok": bool, "ui_bill_no": str|None, "dialogs": [...], "db_rows": int|None,
         "reason": str}
    """
    click_save(page)
    dialogs = [d for d in visible_dialogs(page) if d.strip()]
    bad = [d for d in dialogs if any(w in d for w in bad_words)]
    ui = (read_bill_no(page) or {}).get("value") or ""

    out = {"ok": False, "ui_bill_no": ui or None, "dialogs": dialogs,
           "db_rows": None, "reason": ""}

    if bad:
        out["reason"] = "界面有失败提示：" + " | ".join(d[:120] for d in bad)
        return out

    no = bill_no or ui
    if kq and form_id and no:
        rows = kq.rows(form_id, bill_no_field, f"{bill_no_field} = '{no}'", top=3)
        out["db_rows"] = len(rows)
        if not rows:
            out["reason"] = f"落库回读查不到 {no}（界面看着正常，但数据没进库）"
            return out
    elif kq and form_id:
        out["reason"] = "界面 FBILLNO 为空，无法落库回读 —— 视为保存失败"
        return out

    out["ok"] = bool(no) or bool(kq is None)
    out["reason"] = "ok"
    return out


def save_expect_success(page, kq=None, form_id=None, bill_no=None, **kw):
    """同上，但失败直接抛异常（脚本里用这个）。"""
    r = save_and_verify(page, kq=kq, form_id=form_id, bill_no=bill_no, **kw)
    if not r["ok"]:
        raise RuntimeError(f"保存未成功：{r['reason']}；弹窗={r['dialogs']}")
    return r
