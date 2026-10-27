# -*- coding: utf-8 -*-
"""
金蝶 RPA 安全闸门 —— 把动作分成两类：

  A. **可放行的不可逆动作**（提交 / 审核 / 生成凭证 / 保存凭证）
     由 config 的开关控制，默认 false；确认无误后临时打开。
  B. **绝对禁止的动作**（★ 删除凭证等）
     与 config 无关 —— **改配置、传 --yes、传任何参数都放不开**，一律抛 GuardError。

====================================================================
★★ 红线：不能删除凭证（用户 2026-09-21 定的规则，最高优先级）
====================================================================
凭证是「单据 → 专票 → 凭证」这条链路的**终态与唯一账务留痕**：
  - 删掉它，来源单据与总账之间就断了对应关系，总账 / 报表 / 税务口径同时失真；
  - 金蝶里**没有回收站**，删除是不可逆的，只能回滚数据库备份；
  - 而且删除是"一路通畅"的动作：没有必录项校验、不弹错、kdsvc 正常返回 ——
    脚本很容易把「删除」当成普通菜单项点下去，**不报错但账乱了**。

✅ 想改凭证时，允许的做法（都别删）：
  - 改摘要 / 改分录金额 → 「凭证 - 修改」页修改后保存
  - 想让它不进账 → 反审核 / 作废（作废保留号码与痕迹，删除不留）
  - 真想重来 → 先**人工**确认，再走"反审核 → 修改"重新做，不要删

❌ 明令禁止：
  - 点「凭证」列表工具栏的「删除」
  - 在「凭证 - 修改」页按删除 / 作废快捷键去清空凭证
  - 批量脚本里对凭证调用任何 delete 类操作

依赖：无（纯逻辑，可离线自测）。kd_core 不是必需，方便单独拷走。
用法：
    from kd_guard import SafetyGuard, GuardError, assert_allowed
    g = SafetyGuard(cfg.get("safety", {}))
    g.check("submit")            # A 类：看 config 开关
    g.check("delete_voucher")    # B 类：永远抛 GuardError ★
    assert_allowed("delete_voucher")  # 无 config 场景的简化写法
"""
from __future__ import annotations

# ============================================================
# A 类：可选放行的不可逆动作 → config.safety 里的开关名
# ============================================================
SWITCH_MAP = {
    "submit": "auto_submit",
    "audit": "auto_audit",
    "voucher": "auto_voucher",
    "voucher_save": "auto_voucher_save",
}

# ============================================================
# B 类：★ 绝对禁止的动作（与 config 无关，永不放行）
#     action 名 → (中文名, 替代做法)
# ============================================================
FORBIDDEN_ACTIONS = {
    # ---- 凭证：红线本体 ----
    "delete_voucher": ("删除凭证", "改摘要/改分录后保存；确需重来走「反审核 → 修改」，不要删"),
    "delete_vouchers": ("批量删除凭证", "同上，逐张走「反审核 → 修改」"),
    "batch_delete_voucher": ("批量删除凭证", "同上"),
    "delete_gl_voucher": ("删除总账凭证", "同上"),
    "void_voucher": ("作废凭证（需人工确认）", "作废会留痕，但仍属不可逆，必须人工来点"),
    # ---- 同源扩展：删除已审核单据同样不可逆（删前必须反审核 + 人工确认）----
    "delete_bill": ("删除单据", "先反审核，再由人工确认删除"),
    "delete_audited_bill": ("删除已审核单据", "先反审核，再由人工确认删除"),
    "delete_row": ("删除明细行", "一般无害；若在凭证页则视为红线"),
}

# 界面上"危险动作"的文案关键词 —— 点菜单前先过一道，命中就别点
DANGER_WORDS = ("删除凭证", "作废凭证", "凭证删除")


class GuardError(Exception):
    """触发安全闸门：必须由人工介入才能继续。"""


def is_forbidden(action: str) -> bool:
    """该动作是否属于绝对禁止类。"""
    return str(action) in FORBIDDEN_ACTIONS


def forbidden_reason(action: str) -> str:
    """给出禁止原因与替代做法（用于报错文案 / 自查）。"""
    name, alt = FORBIDDEN_ACTIONS.get(str(action), ("未知动作", ""))
    return f"「{name}」是**绝对禁止**的动作。\n  → 正确做法：{alt}"


def assert_allowed(action: str):
    """★ 红线检查：命中禁止清单就抛 GuardError。任何删除凭证的路径都要先过这里。"""
    if is_forbidden(action):
        raise GuardError(
            "安全闸门拦截：" + forbidden_reason(action) +
            "\n  → 该规则**硬编码在代码里**，不受 config.yaml 开关、--yes 或任何命令行参数影响。"
        )
    return True


def assert_click_safe(text: str):
    """点菜单/按钮前调一次：文案命中危险词就拦住。

    典型用法：
        txt = read_menu_text(...)          # 例如「删除」「凭证删除」
        assert_click_safe(txt)             # 命中即抛，防止脚本顺手点下去
    """
    t = str(text or "")
    for w in DANGER_WORDS:
        if w in t:
            raise GuardError(
                f"安全闸门拦截：要点的项文案为 {t!r}，含危险词 {w!r}。\n"
                "  → 凭证不可删除（红线）。请改用「凭证 - 修改」页修改后保存。"
            )
    return True


class SafetyGuard:
    """A 类开关式放行 + B 类红线硬拦截。

    Args:
        safety_cfg: config.yaml 里的 safety 段（dict）。缺字段一律视为 false（最保守）。
        logger:     可选，带 .warn/.err 方法的对象。
    """

    def __init__(self, safety_cfg=None, logger=None):
        self.s = dict(safety_cfg or {})
        self.log = logger

    # ---------- B 类：红线（先查，永不放行） ----------
    def refuse(self, action: str):
        self._err(f"安全闸门拦截：{forbidden_reason(action)}")
        raise GuardError(forbidden_reason(action))

    # ---------- A 类：开关式 ----------
    def check(self, action: str, hint: str = ""):
        # ★ 顺序很重要：先判红线，再看开关 —— 免得有人以为"开了开关就能删"
        if is_forbidden(action):
            self.refuse(action)

        key = SWITCH_MAP.get(str(action))
        if key is None:
            return  # 未登记的动作不做拦截（如 read / save）

        if not self.s.get(key, False):
            msg = (f"安全护栏拦截：需要执行「{action}」，但 config 的 safety.{key} = false。\n"
                   f"  → 请先人工核对上一步生成的单据，确认无误后再放行。")
            if hint:
                msg += f"\n  → {hint}"
            self._err(msg)
            raise GuardError(msg)
        self._warn(f"已放行不可逆动作「{action}」（{key}=true）")

    def assert_click_safe(self, text: str):
        return assert_click_safe(text)

    def _warn(self, m):
        if self.log:
            self.log.warn(m)

    def _err(self, m):
        if self.log:
            self.log.err(m)


# ============================================================
# 自测（不需要浏览器）：python kd_guard.py
# ============================================================
def _selftest():
    print("=== 1) 红线：删除凭证永远拦得住 ===")
    for a in ("delete_voucher", "delete_vouchers", "delete_gl_voucher"):
        try:
            assert_allowed(a)
            raise AssertionError(f"{a} 竟然放行了！")
        except GuardError as e:
            print(f"  ✓ {a} 被拦：{str(e).splitlines()[0]}")

    print("=== 2) 红线不受 config / 开关影响 ===")
    g = SafetyGuard({"auto_submit": True, "auto_audit": True,
                     "auto_voucher": True, "auto_voucher_save": True,
                     "auto_delete": True})          # 就算有人写了个 auto_delete=true
    try:
        g.check("delete_voucher")
        raise AssertionError("开了全开关后删除凭证竟然放行了！")
    except GuardError:
        print("  ✓ 全部开关打开 + safety.auto_delete=true，删除凭证仍被拦")

    print("=== 3) A 类：开关式动作正常放行 / 拦截 ===")
    g_off = SafetyGuard({})
    try:
        g_off.check("submit")
        raise AssertionError("默认应拦截 submit")
    except GuardError:
        print("  ✓ 默认（全 false）拦 submit")
    g_on = SafetyGuard({"auto_submit": True})
    g_on.check("submit")
    print("  ✓ auto_submit=true 放行 submit")
    g_on.check("read")                      # 未登记动作不拦
    print("  ✓ 未登记动作（read）不拦")

    print("=== 4) 点菜单前文案体检 ===")
    for txt in ("删除", "凭证删除", "删除凭证"):
        try:
            assert_click_safe(txt)
            print(f"  · {txt!r} 未命中（仅含词表精确词才拦）")
        except GuardError:
            print(f"  ✓ {txt!r} 被拦")
    assert_click_safe("保存")
    print("  ✓ '保存' 正常放行")

    print("\nkd_guard 自测通过 —— 红线（不能删除凭证）已固化在代码里。")


if __name__ == "__main__":
    _selftest()
