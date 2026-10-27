# -*- coding: utf-8 -*-
"""
下拉控件操作 —— 含金蝶最难的两种下拉：
  ① 标准 Kendo DropDownList：内部有 `input[data-role="dropdownlist"]` → 走 widget API
  ② **kdcombo（如复核人 `F_PAEZ_COMBO`）**：内部**没有** data-role=dropdownlist、
     拿不到 kendo 实例 → `widget.open()` / `el.click()` 全部无效，
     **必须真实鼠标**：先 scrollIntoView 再点控件中心。

★★ 两条必须遵守的铁律（2026-10-03 实测定标，都是"静默失败"）
  1. **定位控件必须用强化版可见性 `svis`**：`vis()` 只看 width/height + display/visibility，
     会漏掉「祖先 display:none」「被祖先裁掉」「定位到屏幕外」「被压在别的面板下」。
     金蝶 SPA 里**后台标签页的表单 DOM 依然在**，这些"伪可见"元素会被
     `filter(vis)[0]` 挑中 → 脚本去点后台表单的控件 → **静默无效**。
     实测：连续 3 次日志打印 `选择下拉项：姚玉平 @(549,545)` 且命中检测通过，
     但前台表单回读**始终是空**。
  2. **选值要点"贴着本控件"的那个下拉容器**：后台表单的同名下拉，选项文本
     （姚玉平/陈聪/张婷…）**一模一样**，只按文本找必然可能挑错。
     ⇒ `pick_combo_option` 必须传 `field`，按控件位置限定容器。
  3. 顺带：Kendo 下拉有**展开动画**，动画期间 li 坐标还在漂移 →
     **轮询到 `elementFromPoint` 命中该 li 才点**，不要"取一次坐标就点"。

依赖：kd_core.py（同目录）
用法：
    fill_combo(page, "F_PAEZ_COMBO", "姚玉平")     # 复核人
    fill_combo(page, "FDRAWER", "蒋一天")
    print(read_combo(page, "F_PAEZ_COMBO"))         # {'text': '姚玉平', 'value': '...', 'onTop': True}
"""
from __future__ import annotations

from kd_core import (VIS_JS, FG_JS, eval_all_frames, box_center,  # noqa: F401
                     mouse_click_center, norm_field)

# ★★ 强化版可见性：控件定位一律用它，不要用 kd_core.VIS_JS 里的 vis()
STRONG_VIS_JS = """
// ⚠️ svis 只答"真的渲染出来了没"。**不要**在这里判"在不在视口内"（见 SF-47）：
//   窗口矮时（实测 1366x641）表单下半部分的**真控件**会落在折叠下方，
//   加了视口判断就会把它误杀 → 半成品单直接收不了尾。
const svis = e => {
    if (!e) return false;
    const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    if (!(r.width > 0 && r.height > 0)) return false;
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    if (e.offsetParent === null && s.position !== 'fixed') return false;   // 祖先 display:none
    return true;
};
const inView = e => {
    if (!e) return false;
    const r = e.getBoundingClientRect();
    return !(r.right <= 0 || r.bottom <= 0 || r.left >= innerWidth || r.top >= innerHeight);
};
// 在不在**当前激活的标签面板**里 —— 比 inView 更能区分前台/后台同名控件
const inActivePanel = e => {
    let p = e;
    while (p) { const c = p.classList;
        if (c && (c.contains('k-content') || c.contains('k-tabstrip-content')))
            return c.contains('k-state-active');
        p = p.parentElement; }
    return false;
};
const onTop = e => {
    if (!e) return false;
    const r = e.getBoundingClientRect();
    const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return false;
    const t = document.elementFromPoint(x, y);
    return !!(t && (e === t || e.contains(t)));
};
// 找"当前真正在前台"的那个控件：svis 过滤（不要求落在视口内），再按前台程度排序
const findEditor = f => {
    const eds = Array.from(document.querySelectorAll('[id$="-' + f + '-EDITOR"]')).filter(svis);
    if (!eds.length) return null;
    return eds.find(onTop) || eds.filter(inView).find(inActivePanel)
        || eds.find(inActivePanel) || eds.find(inView) || eds[0];
};
"""


def open_combo(page, field):
    """点开下拉。返回 'api'（走了 Kendo API）或 'mouse'（真实鼠标）。"""
    js = STRONG_VIS_JS + """(f) => {
        const box = findEditor(f);
        if (!box) return null;
        // ★ 开下拉前先把控件滚进视口（SF-47）：
        //   视口矮时 Kendo 的 popup 会贴在控件下方、整块落在视口外，
        //   选择项的 elementFromPoint 命中检测永远 false → "按最后坐标点一次"点空，
        //   表现为静默无效（回读为空）。scrollIntoView 同步生效。
        box.scrollIntoView({block: 'center', inline: 'nearest'});
        const inp = box.querySelector('input[data-role="dropdownlist"]');
        const $ = window.jQuery;
        if ($ && inp) {
            const w = $(inp).data('kendoDropDownList');
            if (w) { try { w.open(); } catch (e) { w.toggle(); } return {mode: 'api'}; }
        }
        return {mode: 'mouse'};        // kdcombo：交给真实鼠标
    }"""
    r = eval_all_frames(page, js, field)
    if not r:
        raise RuntimeError(f"找不到下拉控件（或不可见）：{field!r}")
    if r["mode"] == "mouse":
        # ★ 必须真实鼠标 + scrollIntoView（否则控件在视口外，elementFromPoint 返回 null）
        mouse_click_center(page, f'[id$="-{field}-EDITOR"]')
    page.wait_for_timeout(600)
    return r["mode"]


def list_combo_options(page, limit=60, field=None):
    """列出当前展开下拉里的可见选项文本（调试/报错时打出来看）。"""
    js = STRONG_VIS_JS + """(a) => {
        const [f, n] = a;
        const out = [];
        for (const c of Array.from(document.querySelectorAll(
                '.k-list-container,.k-animation-container')).filter(svis)) {
            for (const li of c.querySelectorAll('li')) {
                if (!svis(li)) continue;
                const t = (li.textContent || '').trim();
                if (t) out.push(t);
            }
        }
        return out.slice(0, n);
    }"""
    return eval_all_frames(page, js, [field or "", limit]) or []


def pick_combo_option(page, text, exact=True, field=None, tries=8, gap_ms=350):
    """在已展开的下拉里选中文本匹配的项。

    ⚠️ 合成 MouseEvent 对 Kendo 列表项大概率无效 → 取 li 中心坐标后 `page.mouse.click` 真点。
    ⚠️ `field` **强烈建议传**：只认"贴着该控件"的下拉容器，避免选中后台表单的同名下拉。
    """
    js = STRONG_VIS_JS + """(a) => {
        const [t, ex, fld] = a;
        const box = fld ? findEditor(fld) : null;
        const br = box ? box.getBoundingClientRect() : null;
        let cs = Array.from(document.querySelectorAll(
                  '.k-list-container,.k-animation-container')).filter(svis);
        if (br) {
            const cx0 = br.x + br.width / 2, cy0 = br.y + br.height / 2;
            const near = cs.filter(c => {
                const r = c.getBoundingClientRect();
                const dx = Math.abs((r.x + r.width / 2) - cx0);
                const dy = (r.y + r.height / 2) - cy0;
                return dx <= 460 && dy >= -80 && dy <= 620;
            });
            if (near.length) cs = near;
        }
        for (const c of cs) {
            const lis = Array.from(c.querySelectorAll('li')).filter(svis);
            if (!lis.length) continue;
            const hit = lis.find(x => {
                const s = (x.textContent || '').trim();
                return ex ? s === t : s.includes(t);
            });
            const all = lis.map(x => (x.textContent || '').trim()).slice(0, 30);
            if (!hit) return {err: 'no such option', want: t, all};
            hit.scrollIntoView({block: 'center'});
            const r = hit.getBoundingClientRect();
            const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
            const top = document.elementFromPoint(x, y);
            return {text: (hit.textContent || '').trim(), all, x, y,
                    hit: !!(top && (hit === top || hit.contains(top)))};
        }
        return {err: 'no visible list container'};
    }"""
    r = None
    for i in range(tries):
        r = eval_all_frames(page, js, [str(text), bool(exact), field or ""])
        if not r:
            r = {"err": "no visible list container"}
        elif r.get("err") == "no such option":
            raise RuntimeError(f"下拉里找不到选项：{text!r} —— {r}")
        elif r.get("hit"):
            break
        if i < tries - 1:
            page.wait_for_timeout(gap_ms)
    if not r or r.get("err"):
        raise RuntimeError(f"下拉里找不到选项：{text!r} —— {r}")
    page.mouse.click(r["x"], r["y"])
    page.wait_for_timeout(500)
    return r["text"]


def read_combo(page, field):
    """读下拉当前显示文本（普通 `[id$="-EDITOR_c"]` 读不到它的值）。"""
    js = STRONG_VIS_JS + """(f) => {
        const box = findEditor(f);
        if (!box) return null;
        const k = box.querySelector('.k-input');
        const inp = box.querySelector('input[data-role="dropdownlist"]');
        return {text: k ? (k.textContent || '').trim() : null,
                value: inp ? (inp.value || '').trim() : null,
                id: box.id, onTop: onTop(box)};
    }"""
    return eval_all_frames(page, js, field)


def fill_combo(page, field, text, exact=True, verify=True, tries=3):
    """一步到位：打开下拉 → 选项 → **回读校验**（失败自动重试）。

    ⚠️ 必带回读校验 —— 金蝶的下拉"写入成功"可能只是显示变了、模型没同步。
    ⚠️ 若界面**已经显示目标值**，直接放过（不要重选）。重选是整链最脆弱的一步，
       而且下推时金蝶往往已经默认带出正确值（如复核人「姚玉平」）。模型万一真没同步，
       保存时服务端会报「必填未填」——不会静默出错。
    """
    if verify:
        cur0 = read_combo(page, field) or {}
        if cur0.get("text") == str(text):
            return True

    last = None
    for i in range(1, tries + 1):
        open_combo(page, field)
        try:
            pick_combo_option(page, text, exact=exact, field=field)
        except RuntimeError as e:
            last = e
            opts = list_combo_options(page, field=field)
            page.keyboard.press("Escape")
            page.wait_for_timeout(700)
            continue
        if verify:
            cur = read_combo(page, field) or {}
            if cur.get("text") != str(text):
                last = RuntimeError(f"下拉 {field} 回填校验失败：期望 {text!r}，实际 {cur!r}")
                page.keyboard.press("Escape")
                page.wait_for_timeout(700)
                continue
        return True
    if last:
        raise last
    return True


# ---- 附：日期必须走 Kendo API，直接改 value 不同步模型 ----
def read_field(page, field):
    """读**前台表单**某字段当前值。返回 {val, widget, box, cand, onTop, active}；找不到返回 None。

    `cand` = 页面上同名控件个数（>1 说明确实存在"后台表单同名控件"的歧义）。
    """
    js = FG_JS + """(f) => {
        const p = n => String(n).padStart(2, '0');
        const box = pickBox(f);
        if (!box) return null;
        const inp = pickInput(box);
        const $ = window.jQuery;
        let w = null;
        try {
            const d = $ && ($(inp).data('kendoDatePicker') || $(box).data('kendoDatePicker'));
            if (d && d.value()) { const v = d.value();
                w = v.getFullYear() + '-' + p(v.getMonth() + 1) + '-' + p(v.getDate()); }
        } catch (e) {}
        return {val: inp.value, widget: w, box: box.id, cand: boxes(f).length,
                onTop: onTop(box), active: inActivePanel(box)};
    }"""
    r = eval_all_frames(page, js, field)
    return r if isinstance(r, dict) else None


def fill_date(page, field, ymd, tries=3, log=None):
    """写日期字段，ymd 形如 '2026-09-30'。

    ★★ 2026-10-03（SF-55）**只认前台表单 + 写后回读**：
      · 定位改用 `pickBox()`（onTop → 激活面板），**不再用 `[id$=...].filter(vis)[0]`**；
        后者在 SPA 里会命中**后台标签页那张单**的控件 —— 写是写进去了，只是写错了单。
      · 写完回读（`read_field`），日期必须**逐日相等**；不一致重写（≤`tries` 轮），
        仍不一致 **抛 RuntimeError**（凡写必回读，宁可不保存也不让错值落库）。

    ⚠️ 日期字段**不要**用"真实鼠标点击 + keyboard.type"兜底 ——
       实测掩码框只得到 `'20260930'`，widget=None，model 不入。
       kendoDatePicker 的 API（如本函数）才是有效路径，问题从来不在"怎么写"而在"写给谁"。

    返回 True；不达标抛错。
    """
    import time as _t
    tgt = norm_field(ymd)
    write_js = FG_JS + """(a) => {
        const [f, v] = a;
        const box = pickBox(f);
        if (!box) return {err: 'not found', cand: boxes(f).length};
        const el = pickInput(box);
        el.click(); el.focus();
        const $ = window.jQuery;
        const w = $ && ($(el).data('kendoDatePicker') || $(box).data('kendoDatePicker'));
        if (!w) return {err: 'no kendoDatePicker', box: box.id};
        w.value(new Date(String(v).replace(/-/g, '/')));
        w.trigger('change');
        return {ok: true, shown: (el.value || '').trim(), box: box.id,
                cand: boxes(f).length, onTop: onTop(box)};
    }"""
    last = None
    for i in range(1, max(1, tries) + 1):
        r = eval_all_frames(page, write_js, [field, str(ymd)])
        if not isinstance(r, dict) or r.get("err"):
            raise RuntimeError("fill_date %s 失败：%s" % (field, r))
        _t.sleep(0.4)
        back = read_field(page, field) or {}
        if norm_field(back.get("val")) == tgt:
            if log and back.get("cand", 1) > 1:
                log.info("  %s：页面上有 %d 个同名控件 → 已挑前台那个 %s"
                         % (field, back["cand"], back.get("box")))
            if log and i > 1:
                log.warn("★ %s 第 1 次写入没生效，第 %d 次才写进去" % (field, i))
            return True
        last = back
        if log:
            log.warn("★★ %s 写入未生效（%d/%d）：目标 %s，回读 val=%r widget=%r；"
                     "候选控件 %s 个 onTop=%s" % (field, i, tries, tgt, back.get("val"),
                                                  back.get("widget"), back.get("cand"),
                                                  back.get("onTop")))
    raise RuntimeError(
        "fill_date %s 写入 %d 轮后回读仍不符（目标 %s，读到 %r）—— 已停止；"
        "SF-55：写端零校验会让错值静默落库" % (field, tries, tgt, (last or {}).get("val")))
