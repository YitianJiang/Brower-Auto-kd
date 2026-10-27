# -*- coding: utf-8 -*-
"""
清理**挡路的残留窗口 / 标签** —— 全是"真实鼠标"，合成 click 对它无效。

为什么单开一个模块（2026-09-22 实测教训）：
  同一账套里可能**别的用户 / 别的组织**正在跑单（实测撞到成都批次
  `AR109-*` / `SVINV0003366x~3368x`）。他们失败后会留下一个
  **「处理结果」批处理窗**（内容常是别人的单号，如 `XSCKD-101-202512-S0283`），
  浮在最上层，把后续**所有**真实鼠标点击吃掉，表现为：
      "标签点不动" / `<div …> intercepts pointer events` 超时。
  关掉它脚本才走得动。**但先确认那不是自己的单**，别把别人的失败当自己的去"补救"。

★★ 安全边界（很重要）：
  `close_stray_windows()` 默认**只动标题在 allowlist 里的窗**（默认 `("处理结果",)`），
  **不会**去点业务对话框的「确定」——误点会真提交/真保存。
  要加标题就显式传 `titles=(...)`。

依赖：kd_core.py
用法：
    python kd_stray.py --dump                 # 只列出，不动（先看是谁的）
    python kd_stray.py --close                # 关掉标题含「处理结果」的窗
    python kd_stray.py --close --titles 提示
    python kd_stray.py --tabs                 # 只列标签
    python kd_stray.py --close-tabs --keep 销售出库单列表
"""
from __future__ import annotations

import argparse
import json
import sys

from kd_core import VIS_JS, eval_all_frames, tabs, active_tab, activate_tab

# 已知的"别人留下的批处理结果窗"标题（只关这些，避免误点业务对话框）
STRAY_TITLES = ("处理结果",)

# ★★★ 2026-10-03 定案（SF-51）：孤儿标签的**最后手段** —— 走主标签栏自己的 widget。
#
#   主标签栏（`ul.kdMainTabsHeaderExtend`）的 data 键是 **`kendomainTabControl`**，
#   金蝶自研的 Kendo TabStrip **子类**（有 items/remove/dataSource/activateTab），
#   **不是** `kendoTabStrip`。实测整页 14 个 `ul.k-tabstrip-items` 里，
#   **只有主栏**取不到 `data('kendoTabStrip')`（另外 13 个都是单据表单内的页组）。
#   ⇒ 一切 `data('kendoTabStrip')` 的尝试必然落空 —— 这正是
#     "点 ✕ 静默无响应、`locator.click` / `mouse.click` / `force` / `dispatch_event` /
#      JS `el.click()` 五种全无效、连金蝶自带「关闭其他页签」也点空"的**最后一层原因**：
#     不是点不中，是入口找错了。
#
#   实测：`ctl.remove($(li))` **6/6 摘除成功**，标签数 7 → 1，保留锚点完好。
#
#   ⚠️ 边界：只摘 tabstrip 的 `li`，**不碰任何单据数据**（不保存/不提交/不删除）。
#      摘掉一个**未保存草稿**页 = 丢弃草稿（草稿本来就没落库，无损失）；
#      但**正在被别的用户/别的链路使用的活单据标签不要动**。
#   ⚠️ 口径：`ctl.items()` 恒定比真实标签数 **多 2**（主栏长期驻留 2 个零宽幽灵 `li`），
#      估算标签配额时以 `tabs(page)` 为准。
FORCE_REMOVE_JS = VIS_JS + """() => {
    const tabId = __TAB_ID__;
    const strip = document.querySelector('ul.kdMainTabsHeaderExtend')
               || Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0];
    if (!strip) return false;
    const $ = window.jQuery || window.$;
    if (!$) return false;
    const root = $(strip).closest('.k-widget')[0];
    const ctl = root ? $(root).data('kendomainTabControl') : null;
    if (!ctl || typeof ctl.remove !== 'function') return false;
    let el = null;
    try { el = tabId ? strip.querySelector('#' + CSS.escape(tabId)) : null; } catch (e) { el = null; }
    if (!el) return false;
    try { ctl.remove($(el)); } catch (e) { return false; }
    return true;
}"""


def force_remove_tab(page, tab, logger=None):
    """摘掉一个标签（含"关不掉的孤儿"）。

    返回 True **只表示摘除请求发出去了** —— 是否真摘掉，必须由调用方
    回读 `tabs(page)` 对比判定（判据永远在"回读"这一侧，见 SF-54/SF-55）。
    """
    log = (lambda m: None) if logger is None else logger
    tid = (tab or {}).get("id")
    if not tid:
        log("[warn] force_remove_tab：该标签没有 id，无法摘除")
        return False
    js = FORCE_REMOVE_JS.replace("__TAB_ID__", json.dumps(tid))
    try:
        return bool(page.evaluate(js))
    except Exception as e:            # noqa: BLE001
        log(f"[warn] kendomainTabControl 摘标签失败：{type(e).__name__}: {str(e)[:80]}")
        return False


def dump_visible_dialogs(page, max_len=160):
    """列出当前**可见**的 `.k-window`（标题 + 按钮 + 文本摘要）。

    ★ 先用它确认"不是自己的单"，再决定关不关（SF-31）。
    """
    js = VIS_JS + """() => {
        const ws = Array.from(document.querySelectorAll('.k-window')).filter(vis);
        return {n: ws.length, items: ws.map(w => {
            const t = w.querySelector('.k-window-title');
            const btns = Array.from(w.querySelectorAll('a,button,span.k-button'))
                .filter(vis).map(x => (x.innerText || '').trim()).filter(Boolean);
            return {title: t ? (t.innerText || '').trim() : '',
                    btns: btns.slice(0, 8),
                    text: (w.innerText || '').replace(/\\s+/g, ' ').trim()};
        })};
    }"""
    r = eval_all_frames(page, js) or {"n": 0, "items": []}
    for it in r.get("items", []):
        it["text"] = it["text"][:max_len]
    return r


def _top_dialog_grab(page, titles):
    """取最上层匹配 titles 的窗的关闭按钮坐标（真实鼠标要点坐标）。"""
    js = VIS_JS + """(titles) => {
        const ws = Array.from(document.querySelectorAll('.k-window')).filter(vis);
        const hit = ws.filter(w => {
            const t = w.querySelector('.k-window-title');
            const s = t ? (t.innerText || '').trim() : '';
            return titles.some(x => s.includes(x));
        });
        if (!hit.length) return null;
        const w = hit[hit.length - 1];
        const t = w.querySelector('.k-window-title');
        const title = t ? (t.innerText || '').trim() : '';
        const btns = Array.from(w.querySelectorAll('a,button,span.k-button'))
            .filter(vis).map(x => (x.innerText || '').trim());
        // 优先「退出 / 取消 / 关闭」，其次右上角 ✕ —— ★ 特意**不含「确定」**
        let el = Array.from(w.querySelectorAll('a,button,span.k-button')).filter(vis)
            .find(x => ['退出', '取消', '关闭'].includes((x.innerText || '').trim()));
        if (!el) el = w.querySelector('[aria-label="Close"]');
        if (!el) return {title, btns, noClose: true};
        el.scrollIntoView({block: 'center'});
        const r = el.getBoundingClientRect();
        return {title, btns, label: (el.innerText || '').trim(),
                x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    }"""
    return eval_all_frames(page, js, list(titles))


def close_stray_windows(page, titles=STRAY_TITLES, max_iter=6, wait_ms=1200, logger=None):
    """用**真实鼠标**关掉标题命中 `titles` 的弹窗，返回关掉的标题列表。

    ★ 只在标题命中时才动手；命中不了就原样留着交人工。
    """
    log = (lambda m: None) if logger is None else logger
    done = []
    for i in range(max_iter):
        info = _top_dialog_grab(page, titles)
        if not info:
            break
        if info.get("noClose"):
            log(f"[warn] 窗 {info.get('title')!r} 没有可点的关闭按钮，留给人工（按钮={info.get('btns')}）")
            break
        log(f"[info] 关窗 #{i + 1}: title={info.get('title')!r} "
            f"点={info.get('label') or '✕'} @({info['x']},{info['y']})")
        page.mouse.click(info["x"], info["y"])
        page.wait_for_timeout(wait_ms)
        done.append(info.get("title") or "(无标题)")
    return done


def dismiss_save_confirm(page, wait_ms=1200, logger=None):
    """关标签/关单据时若弹「有未保存的修改，是否关闭」→ 点「确定」。

    ⚠️ 只在弹窗**文案命中** 关闭/放弃/未保存/不保存 时才点，避免误点业务对话框。
    """
    log = (lambda m: None) if logger is None else logger
    js = VIS_JS + """() => {
        const w = Array.from(document.querySelectorAll('.k-window')).filter(vis)
            .find(x => /关闭|放弃|未保存|不保存/.test(x.innerText || ''));
        if (!w) return null;
        const b = Array.from(w.querySelectorAll('a,button,span.k-button')).filter(vis)
            .find(x => /^(确定|是|关闭|放弃)$/.test((x.innerText || '').trim()));
        if (!b) return null;
        b.scrollIntoView({block: 'center'});
        const r = b.getBoundingClientRect();
        return {text: (w.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 100),
                x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    }"""
    r = eval_all_frames(page, js)
    if r:
        log(f"[info] 关标签确认框：{r.get('text')!r}")
        page.mouse.click(r["x"], r["y"])
        page.wait_for_timeout(wait_ms)
    return r


def close_tabs_keep(page, keep_names=("销售出库单列表",), max_iter=40,
                    wait_ms=900, logger=None):
    """★ 生成凭证后的标准清场：关掉所有标签，**保留「销售出库单列表」**。

    用户口径（2026-09-21）：出库单列表是下一组的起点，留着省一次搜索。
    策略：先关非激活的、从右往左（关一个位置就变，每轮重新取列表）；
          非保留的激活标签最后关；关完把保留标签切回前台。
    返回 {"closed": [...], "left": [...]}。
    """
    log = (lambda m: None) if logger is None else logger
    closed = []
    failed = set()                       # 关不掉的标签 —— 记下跳过，别死循环（SF-51）
    for _ in range(max_iter):
        ts = tabs(page)
        if not ts:
            break
        cur = active_tab(page)
        keep = [t for t in ts if any(k in (t.get("text") or "") for k in keep_names)]
        targets = [t for t in ts if t not in keep]      # 按 dict 比较，坐标相同即同一标签
        targets = [t for t in targets
                   if (t.get("id") or t.get("text") or "") not in failed]
        if not targets:
            break
        pool = [t for t in targets if (t.get("text") or "") != cur] or targets
        pool = [t for t in pool if t.get("cx") is not None]
        if not pool:
            log("[warn] 剩余标签都没有 ✕ 按钮，停止清理")
            break
        t = pool[-1]
        key = t.get("id") or t.get("text") or ""
        sig_before = tuple(sorted((x.get("text") or "") for x in tabs(page)))
        # ★ 2026-10-03：**优先 `locator.click()`** —— 鼠标移上去标签进入 hover 态、
        #   宽度会变、✕ 位置随之漂移；`locator.click()` 会等元素位置稳定再算坐标，
        #   而 `mouse.click(提前取好的坐标)` 用的是漂移前的旧坐标，必然点空（SF-49）。
        clicked = False
        if t.get("id"):
            try:
                page.locator(f'[id="{t["id"]}"] span.mainTabCloseButton').first.click(timeout=6000)
                clicked = True
            except Exception as e:                      # noqa: BLE001
                log(f"[warn] locator 关标签失败：{type(e).__name__}: {str(e)[:80]}")
        if not clicked and t.get("cx") is not None:
            page.mouse.click(t["cx"], t["cy"])          # 兜底（坐标可能已漂移）
        page.wait_for_timeout(wait_ms)
        dismiss_save_confirm(page, wait_ms=wait_ms, logger=logger)
        # ★ 回读判定：✕ 无效（孤儿标签）→ 走 kendomainTabControl.remove() 摘除
        if tuple(sorted((x.get("text") or "") for x in tabs(page))) == sig_before:
            if force_remove_tab(page, t, logger=logger):
                page.wait_for_timeout(600)
                if tuple(sorted((x.get("text") or "") for x in tabs(page))) != sig_before:
                    log(f"[ok] 标签 {t.get('text')!r} 点 ✕ 无反应 → "
                        f"kendomainTabControl.remove() 摘除成功")
                    closed.append(t.get("text") or "")
                    continue
            failed.add(key)
            log(f"[warn] 标签 {t.get('text')!r} 摘不掉（已跳过，不再重试）")
            continue
        closed.append(t.get("text") or "")
    left = tabs(page)
    if not any(any(k in (t.get("text") or "") for k in keep_names) for t in left):
        log(f"[warn] 要保留的标签 {keep_names} 不在剩余列表里：{[t.get('text') for t in left]}")
    else:
        for k in keep_names:                            # 切回保留的标签
            if any(k in (t.get("text") or "") for t in left):
                try:
                    activate_tab(page, k)
                except Exception as e:                  # noqa: BLE001
                    log(f"[warn] 切回保留标签 {k!r} 失败：{e}")
                break
    return {"closed": closed, "left": [t.get("text") for t in left]}


def main():
    ap = argparse.ArgumentParser(description="清理挡路的残留窗口 / 标签")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222", help="CDP 地址")
    ap.add_argument("--dump", action="store_true", help="只列出可见弹窗（★ 先看是谁的单）")
    ap.add_argument("--close", action="store_true", help="关掉标题命中 --titles 的弹窗")
    ap.add_argument("--titles", nargs="*", default=list(STRAY_TITLES),
                    help=f"要关的窗标题关键字（默认 {list(STRAY_TITLES)}）")
    ap.add_argument("--tabs", action="store_true", help="只列标签")
    ap.add_argument("--close-tabs", action="store_true", help="清场：关标签")
    ap.add_argument("--keep", nargs="*", default=["销售出库单列表"], help="清场时保留的标签名")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright  # noqa: E402
    say = lambda m: print(m, flush=True)
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(a.cdp)
        pg = br.contexts[0].pages[0]
        if a.dump or not (a.close or a.tabs or a.close_tabs):
            d = dump_visible_dialogs(pg)
            say(f"可见弹窗 {d['n']} 个：")
            for it in d["items"]:
                say(f"  - 标题={it['title']!r} 按钮={it['btns']}")
                say(f"    文本: {it['text']}")
        if a.close:
            say(f"关掉的窗：{close_stray_windows(pg, titles=tuple(a.titles), logger=say)}")
        if a.tabs or a.close_tabs:
            say(f"当前标签：{[t['text'] for t in tabs(pg)]}")
        if a.close_tabs:
            r = close_tabs_keep(pg, keep_names=tuple(a.keep), logger=say)
            say(f"清场结果：关掉 {len(r['closed'])} 个 {r['closed']}，剩 {r['left']}")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
