# -*- coding: utf-8 -*-
"""
顶部「全功能搜索」导航 —— 含一条必踩的坑：**搜到了却点不动**。

★★ 症状（2026-10-03 本批 8 家全军覆没的第一现场）
    ```
    TimeoutError: Locator.click: Timeout 6000ms exceeded.
      - locator resolved to <li class="kd-fullsearch-item-container k-state-focused">…</li>
      - <div draggable="true" class="kd-form-collapse-container">…</div> intercepts pointer events
    ```

★★ 根因（`probe_search_click` 打出来的遮挡链）
    ```
    视口 1366x641   条目中心=(996,107)
    [0] DIV cls='kd-form-collapse-container' rect=[991,57,214,58] z=10010 pos=absolute draggable=true
    [1] BODY cls='kdmainformbg'
    文本='提交：保存：1837971/2 该字段需要您手动查找'
    ```
    = **上一次失败保存留下的残留浮动提示条**，浮在右上角，
    **正好盖住搜索面板的结果列表**（结果列表就在搜索框正下方）。
    ⚠️ 反直觉点：**剥它的 `pointer-events` 也没用** —— 它下面直接就是 `BODY`，
    条目本身**并不在那个坐标上**（`getBoundingClientRect()` 的中心与实际命中点不一致）。
    ⇒ 「真实鼠标点坐标」这条路**根本走不通**，`locator.click` 更是必然超时。

★★ 解药（三步，缺一不可）
    1. `dismiss_floating_hints(page)` —— 把文案命中提示特征的
       `.kd-form-collapse-container` 直接 `display:none`（`close_stray_windows` 只管 `.k-window`）。
    2. 结果条目点击顺序：**真实鼠标（带剥层） → JS `el.click()` → 最后才 locator**。
       实测 **`el.click()` 有效**（点完标签列表立刻多出目标页）——
       该条目绑的是 **jQuery click 处理器**，不走 Kendo 指针事件，所以合成 click 能触发。
       （⚠️ 这条与"合成 MouseEvent 在 Kendo 下拉/复选框上无效"的旧结论**不冲突**：
         区别在于处理器是 jQuery 还是 Kendo。）
    3. 剥层若最终**没点成**，必须把 `pointer-events` **还回去** ——
       残留的 `pointer-events:none` 会把后续所有点击也废掉，比原问题更糟。

依赖：kd_core.py（同目录）
用法：
    open_list(page, log, "销售出库单列表")          # 一步到位（含所有兜底）
    dismiss_floating_hints(page, log)               # 也可单独在流程开头清一次
"""
from __future__ import annotations

from kd_core import VIS_JS, eval_all_frames, tabs  # noqa: F401

# 搜索结果条目：取坐标 + 剥层 + 命中检测
SEARCH_POS_JS = VIS_JS + """
(kws) => {
    // 还原上一次可能残留的剥离（幂等，防跨次污染）
    const restore = () => {
        const rec = window.__kdPeel || [];
        for (const [el, v] of rec) { try { el.style.pointerEvents = v; } catch (e) {} }
        window.__kdPeel = [];
    };
    restore();
    const items = Array.from(
        document.querySelectorAll('.kd-fullsearch-item-container')).filter(vis);
    for (const kw of kws) {
        const el = items.find(e => (e.innerText || '').includes(kw));
        if (!el) continue;
        el.scrollIntoView({block: 'center'});
        let r = el.getBoundingClientRect();
        let cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
        const hit = () => { const t = document.elementFromPoint(cx, cy); return t && el.contains(t); };
        if (cy < 0 || cy > innerHeight || !hit()) {
            el.scrollIntoView({block: 'start'});
            r = el.getBoundingClientRect();
            cx = Math.round(r.x + r.width / 2);
            cy = Math.round(r.y + r.height / 2);
        }
        if (cy < 0 || cy > innerHeight) return null;
        let peeled = 0;
        for (let k = 0; k < 5 && !hit(); k++) {
            const top = document.elementFromPoint(cx, cy);
            if (!top || top === document.body || top === document.documentElement) break;
            let victim = null, p = top;
            while (p && p !== document.body) {
                if (p.classList && p.classList.contains('kd-form-collapse-container')) {
                    victim = p; break; }
                p = p.parentElement;
            }
            if (!victim) victim = top;
            if (victim === el || el.contains(victim)) break;
            (window.__kdPeel = window.__kdPeel || []).push([victim, victim.style.pointerEvents || '']);
            victim.style.pointerEvents = 'none';
            peeled++;
        }
        if (!hit()) return null;
        return {x: cx, y: cy, peeled: peeled,
                text: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60)};
    }
    return null;
}"""

UNPEEL_JS = """() => { const rec = window.__kdPeel || [];
    for (const [el, v] of rec) { try { el.style.pointerEvents = v; } catch (e) {} }
    window.__kdPeel = []; return rec.length; }"""

# ★ 解药核心：JS 合成点击（该条目绑 jQuery click，能触发）
SEARCH_JS_CLICK = VIS_JS + """
(kws) => {
    const items = Array.from(
        document.querySelectorAll('.kd-fullsearch-item-container')).filter(vis);
    for (const kw of kws) {
        const el = items.find(e => (e.innerText || '').includes(kw));
        if (!el) continue;
        el.click();
        return (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
    }
    return null;
}"""

DISMISS_HINTS_JS = VIS_JS + """
() => {
    const pat = /(该字段需要您手动查找|提交：保存：|保存：提交：)/;
    const hit = Array.from(document.querySelectorAll('.kd-form-collapse-container'))
        .filter(vis).filter(e => pat.test(e.innerText || ''));
    hit.forEach(e => { e.style.display = 'none'; });
    return hit.length;
}"""


def dismiss_floating_hints(page, log=None):
    """隐藏残留浮动提示条（删不掉、会挡住右上角搜索结果和工具栏）。返回条数。"""
    try:
        n = eval_all_frames(page, DISMISS_HINTS_JS) or 0
    except Exception:  # noqa: BLE001
        return 0
    if n and log:
        log.warn(f"隐藏残留浮动提示条 {n} 个（会挡住右上角搜索结果/工具栏）")
    return n


def click_search_result(page, log, name, hint=None):
    """
    点中搜索结果条目（多级兜底）。返回实际用的方式：'mouse' / 'js' / 'locator'。
    name = 功能名（如「销售出库单列表」）；hint = 菜单路径片段（重名消歧）。
    """
    kws = [h for h in ([hint, name] if hint else [name]) if h]

    pos = eval_all_frames(page, SEARCH_POS_JS, kws)
    if pos:
        if pos.get("peeled") and log:
            log.warn(f"搜索结果被浮层挡住 → 临时剥掉 {pos['peeled']} 层 pointer-events 再点")
        page.mouse.click(pos["x"], pos["y"])
        page.wait_for_timeout(300)
        eval_all_frames(page, UNPEEL_JS)
        if log:
            log.info(f"打开功能（真实鼠标 {pos['x']},{pos['y']}）：{pos['text']}")
        return "mouse"

    # ★ 剥层了却没点成 → 必须还回去，否则后续点击全废
    try:
        eval_all_frames(page, UNPEEL_JS)
    except Exception:  # noqa: BLE001
        pass

    got = eval_all_frames(page, SEARCH_JS_CLICK, kws)
    if got:
        if log:
            log.warn(f"搜索结果被残留浮动条挡住，真实鼠标点不到 → "
                     f"改用 JS el.click() 兜底：{got}")
        return "js"

    loc = page.locator(".kd-fullsearch-item-container:visible").filter(has_text=name)
    if hint:
        loc = loc.filter(has_text=hint)
    loc.first.click(timeout=6000)
    if log:
        log.info(f"打开功能（locator 兜底）：{name}")
    return "locator"


def open_list(page, log, name, hint=None, wait_ms=4000):
    """
    一步到位：清残留提示条 → 点搜索结果（多级兜底）→ 等页面。
    ⚠️ 调用方应**先检查目标标签是否已存在**，存在就直接 `kd_core.activate_tab`，
       别重复搜（重复开会让 tabstrip 溢出、互相错位）。
    """
    dismiss_floating_hints(page, log)
    how = click_search_result(page, log, name, hint)
    page.wait_for_timeout(wait_ms)
    if log:
        try:
            log.info(f"标签数 = {len(tabs(page))}（点击方式={how}）")
        except Exception:  # noqa: BLE001
            pass
    return how
