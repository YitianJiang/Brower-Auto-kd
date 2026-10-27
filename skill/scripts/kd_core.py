# -*- coding: utf-8 -*-
"""
金蝶云星空 RPA —— 最小公共底座（自包含，无第三方依赖，只要求传进来的 page 是
Playwright 已接管金蝶的页面对象）。

用法：
    from kd_core import eval_all_frames, mouse_click_center, wait_until, visible_dialogs

★ 本文件是全套 kd_*.py 的公共依赖。拷走时把它和要用的模块放同一目录即可。

⚠️⚠️ 最重要的约定：**凡是要取值的 JS，一律返回对象 `{...}`，不要返回裸标量。**
   原因：`page.evaluate` 返回的 `0 / '' / false` 与"没找到元素返回 null"在 Python 侧
   难以区分；而本模块的 eval_all_frames 会在多个 frame 里轮询、丢弃假值。
   返回对象（如 `return {n: count}`）能同时表达"取到了值"和"值是 0"。
   （真实教训：条件行数 0 被判成"没拿到结果"→ 死等 12 秒后误报错。）

依赖：    pip install playwright
"""
from __future__ import annotations

import re

# 可见性过滤：金蝶 SPA 把开过的页面 DOM 全留着，所有定位都必须先叠这一层
VIS_JS = """
const vis = e => {
    if (!e) return false;
    const r = e.getBoundingClientRect();
    const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0
        && s.display !== 'none' && s.visibility !== 'hidden';
};
"""


# ★★ 2026-10-03（SF-55）：**前台控件**定位片段 —— 凡"按 id 找控件"都要用它。
#   为什么必须：`VIS_JS` 的 vis() 只看尺寸 + display/visibility，
#   而金蝶 SPA 把**后台标签页的表单 DOM 也留着**（实测同时存在 11 个「应收单 - 新增」）。
#   弱过滤 + `[0]` 会命中**最老那张（孤儿草稿）**：
#     · 写操作**真的执行了**（API 返回新值、日志打印成功）
#     · 但**写在了别的表单上**，当前这张单的值仍是默认（如 FDATE 落成"今天"）
#   ⇒ 表现是**随机的**（同一批同一份代码，时对时错），只有回读/服务端查询能发现。
FG_JS = """
const svis = e => {
    if (!e) return false;
    const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    if (!(r.width > 0 && r.height > 0)) return false;
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    if (e.offsetParent === null && s.position !== 'fixed') return false;
    return true;
};
const onTop = e => {
    if (!e) return false;
    const r = e.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return false;
    const t = document.elementFromPoint(x, y);
    return !!(t && (e === t || e.contains(t)));
};
const inActivePanel = e => {
    let p = e;
    while (p) {
        const c = p.classList;
        if (c && (c.contains('k-content') || c.contains('k-tabstrip-content')))
            return c.contains('k-state-active');
        p = p.parentElement;
    }
    return false;
};
const inView = e => {
    if (!e) return false;
    const r = e.getBoundingClientRect();
    return !(r.right <= 0 || r.bottom <= 0 || r.left >= innerWidth || r.top >= innerHeight);
};
// 只会命中外层 BOX（...-FIELD-EDITOR），真正的 input 在盒子里
const boxes = f => Array.from(document.querySelectorAll('[id$="-' + f + '-EDITOR"]'))
    .filter(e => svis(e) && e.querySelector('input'));
const pickBox = f => {
    const b = boxes(f);
    if (!b.length) return null;
    return b.find(onTop) || b.filter(inView).find(inActivePanel)
        || b.find(inActivePanel) || b.find(inView) || b[0];
};
const pickInput = box => box.querySelector('input') || box;
"""


def norm_field(v):
    """2026/9/30、2026-09-30、2026/09/30 00:00 → '2026-09-30'；非日期原样返回。"""
    s = str(v if v is not None else "").strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return s


def eval_all_frames(page, js, arg=None):
    """在所有 frame 里依次求值，返回**第一个非 None** 的结果。

    ⚠️ 判据是 `is not None`，不是真值 —— 否则 JS 返回 0/''/false 会被丢弃。
       但这仍无法区分"返回了 null"和"没执行到"，所以**要取值就让 JS 返回对象**。
    """
    for fr in page.frames:
        try:
            r = fr.evaluate(js, arg) if arg is not None else fr.evaluate(js)
        except Exception:
            continue
        if r is not None:
            return r
    return None


def wait_until(page, fn, timeout_ms=15000, interval_ms=500, what="条件"):
    """轮询直到 fn() 为真；超时抛 TimeoutError（带最后一次返回值，便于排查）。"""
    waited, last = 0, None
    while waited < timeout_ms:
        last = fn()
        if last:
            return last
        page.wait_for_timeout(interval_ms)
        waited += interval_ms
    raise TimeoutError(f"等待{what}超时（{timeout_ms}ms），最后一次结果：{last!r}")


def box_center(page, css, must_contain=None):
    """取元素中心坐标；返回 dict(cx, cy, in_view, hit)。

    `hit=False` 表示坐标处的顶层元素不是它（被遮挡或落在视口外）→ 别硬点，重试。
    实测教训：控件 y=711 而视口高 641 时 elementFromPoint 返回 null，
    点击落到窗口外，下拉根本不打开。
    """
    js = VIS_JS + """(a) => {
        const [sel, kw] = a;
        let el = Array.from(document.querySelectorAll(sel)).filter(vis)[0];
        if (!el && kw) el = Array.from(document.querySelectorAll(sel))
            .filter(vis).find(e => (e.innerText || '').includes(kw));
        if (!el) return null;
        el.scrollIntoView({block: 'center', inline: 'nearest'});
        const r = el.getBoundingClientRect();
        const cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
        const top = document.elementFromPoint(cx, cy);
        return {cx, cy, vw: innerWidth, vh: innerHeight,
                in_view: r.top >= 0 && r.bottom <= innerHeight,
                hit: top ? el.contains(top) : false};
    }"""
    return eval_all_frames(page, js, [css, must_contain])


def mouse_click_center(page, css, must_contain=None, tries=3):
    """真实鼠标点击元素中心。

    ★ 金蝶里「合成 MouseEvent」在大量场景无效（Kendo 认真实输入、事件委托在祖先容器、
      按钮带 onclick="return false"），所以关键点击一律走这一条：
      取坐标 → page.mouse.click。
    """
    for _ in range(tries):
        p = box_center(page, css, must_contain)
        if p and p.get("hit"):
            page.mouse.click(p["cx"], p["cy"])
            page.wait_for_timeout(400)
            return p
        page.wait_for_timeout(400)
    raise RuntimeError(f"点不到（被遮挡/不可见）：{css} {must_contain or ''} → {p!r}")


def visible_dialogs(page):
    """读所有**可见**弹窗/提示条的 innerText（保存后立刻用它判成败，见 kd_save.py）。

    金蝶的"保存失败"往往只体现在这里（必录项校验），而 save 本身不抛异常。
    """
    js = VIS_JS + """() => {
        const out = [];
        for (const e of document.querySelectorAll(
                 '.k-window, .kd-el-popmessage, .kd-message-question, [id$="-POPFLOATEMESSAGE"]')) {
            if (!vis(e)) continue;
            const t = (e.innerText || '').trim();
            if (t) out.push(t);
        }
        return out;
    }"""
    return eval_all_frames(page, js) or []


def active_tab(page):
    """当前激活标签名（切页后用它确认，别立即查 DOM）。"""
    js = VIS_JS + """() => {
        const li = Array.from(document.querySelectorAll(
            'ul.k-tabstrip-items > li.k-item.contentHeadEL')).filter(vis);
        const on = li.find(e => /k-state-active|k-active/.test(e.className)) || li[0];
        return on ? (on.innerText || '').trim() : null;
    }"""
    return eval_all_frames(page, js)


def tabs(page):
    """真标签页列表 [{id, text, tx, ty, cx, cy}]（tx/ty=标签本体用于切换，cx/cy=✕ 用于关闭）。

    ⚠️ 不要用 `.k-item` —— 工具栏菜单项也是 `li.k-item`，会捞出 20+ 个假标签。
    ★ `id`（`li_xxxFMAINTAB_c`）是**唯一可靠的标签身份**：同名标签可以有几十个，
      按文字找会找错；`force_remove_tab()` 也依赖它。
    """
    js = VIS_JS + """() => {
        return Array.from(document.querySelectorAll(
                'ul.k-tabstrip-items > li.k-item.contentHeadEL')).filter(vis).map(li => {
            const r = li.getBoundingClientRect();
            const x = li.querySelector('span.k-i-close.mainTabCloseButton');
            const xr = x ? x.getBoundingClientRect() : null;
            return {id: li.id || null,
                    text: (li.innerText || '').trim().replace(/\\s+/g, ' '),
                    tx: Math.round(r.x + Math.min(60, r.width / 2)), ty: Math.round(r.y + r.height / 2),
                    cx: xr ? Math.round(xr.x + xr.width / 2) : null,
                    cy: xr ? Math.round(xr.y + xr.height / 2) : null};
        });
    }"""
    return eval_all_frames(page, js) or []


def activate_tab(page, keyword, timeout_ms=8000):
    """按名字**包含匹配**切到某个标签（真实鼠标点标签本体）。

    ⚠️ 是包含匹配：activate_tab("销售增值税专用发票") 会命中「… - 修改」页。
    """
    import time
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        for t in tabs(page):
            if keyword in t["text"]:
                page.mouse.click(t["tx"], t["ty"])
                page.wait_for_timeout(1200)
                return t["text"]
        page.wait_for_timeout(400)
    raise RuntimeError(f"找不到标签（包含）：{keyword!r}")
