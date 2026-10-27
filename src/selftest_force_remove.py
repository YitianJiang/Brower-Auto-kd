# -*- coding: utf-8 -*-
"""
`KingdeeUI._force_remove_tab()` 的**非破坏性**冒烟测试。

只做两件事：
  ① 用与 `_force_remove_tab` **完全相同的 JS 串形态**（`_INVISIBLE` 前缀 + 零参箭头函数 +
     字符串占位替换）在页面里求值一次，但**返回诊断信息而不是执行 remove** ——
     用来证明这段 JS 在当前页面能编译并跑通（Playwright 对"带语句前缀的字符串"是否
     当函数处理，是这套写法唯一的风险点）。
  ② 打印：找到主标签栏没、拿到 kendomainTabControl 没、按 id 定位到 li 没。

★ 不调用 remove、不关任何标签、不碰单据数据。
用法：python src/selftest_force_remove.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, abs_path  # noqa: E402
from steps import KingdeeUI  # noqa: E402
from kd_tab_sel import MAIN_TAB_SELECTOR, WIDGET_KEY  # noqa: E402

CFG = load_config()

# ⚠️ 与 KingdeeUI._force_remove_tab 的 JS 保持同形态，只把"摘除"换成"回报"。
# ★ 选择器走单一真相源 kd_tab_sel.MAIN_TAB_SELECTOR —— 这里就是「开跑前自检」：
#   若哪天有人又把主标签栏选择器改错，本脚本会当场 ✗，而不是等页签攒到 15 才爆。
CHECK_JS = KingdeeUI._INVISIBLE + """() => {
    const id = __TAB_ID__;
    const strip = document.querySelector('__MAIN_TAB__')
               || Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0]
               || Array.from(document.querySelectorAll('.k-tabstrip-items')).filter(vis)[0];
    if (!strip) return {strip: false};
    const $ = window.jQuery || window.$;
    if (!$) return {strip: true, jq: false};
    const root = $(strip).closest('.k-widget')[0];
    const ctl = root ? $(root).data('__WIDGET_KEY__') : null;
    let el = null;
    try { el = id ? strip.querySelector('#' + CSS.escape(id)) : null; } catch (e) { el = null; }
    return {strip: true, jq: true, root: !!root, hasCtl: !!ctl,
            hasRemove: !!(ctl && typeof ctl.remove === 'function'),
            id, foundEl: !!el,
            elText: el ? (el.innerText || '').trim().slice(0, 24) : null,
            ctlItems: ctl ? ctl.items().length : null,
            ctlKeys: ctl ? Object.keys(ctl).length : null};
}"""
CHECK_JS = CHECK_JS.replace("__MAIN_TAB__", MAIN_TAB_SELECTOR)
CHECK_JS = CHECK_JS.replace("__WIDGET_KEY__", WIDGET_KEY)


def run_selftest(pg):
    """核心自检逻辑：返回 (ok, detail)。不依赖 Logger，供 run_batch --precheck 复用。

    pg = 已接管金蝶的 Playwright page。只做**只读**诊断（不 remove、不关标签）。
    """
    tabs = KingdeeUI(pg, CFG, None, None).tabs()
    if not tabs:
        return False, "没有标签，无法冒烟"
    ok = True
    detail = []
    for t in tabs:
        js = CHECK_JS.replace("__TAB_ID__", json.dumps(t["id"]))
        try:
            r = pg.evaluate(js)
        except Exception as e:  # noqa: BLE001
            ok = False
            detail.append(f"{t['text']!r} JS 求值失败: {type(e).__name__}")
            continue
        good = r.get("hasCtl") and r.get("hasRemove") and r.get("foundEl")
        ok = ok and good
        detail.append(f"{'✓' if good else '✗'} {t['text']!r} hasCtl={r.get('hasCtl')} "
                      f"hasRemove={r.get('hasRemove')} foundEl={r.get('foundEl')}")
    # 反面用例：不存在的 id 必须 foundEl=False 且 hasCtl=True
    js = CHECK_JS.replace("__TAB_ID__", json.dumps("li___NOT_EXIST___"))
    r = pg.evaluate(js)
    ok = ok and (r.get("foundEl") is False) and r.get("hasCtl")
    detail.append(f"反面用例 foundEl={r.get('foundEl')}（应 False）hasCtl={r.get('hasCtl')}")
    return ok, "\n".join(detail)


def main():
    log = Logger(abs_path("logs", CFG), name="selftest_force_remove")
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, None)
        tabs = ui.tabs()
        log.info(f"当前标签 {len(tabs)} 个：{[t['text'] for t in tabs]}")
        if not tabs:
            log.warn("没有标签，无法冒烟")
            br.close()
            return 1

        ok = True
        for t in tabs:
            js = CHECK_JS.replace("__TAB_ID__", json.dumps(t["id"]))
            try:
                r = pg.evaluate(js)
            except Exception as e:  # noqa: BLE001
                log.err(f"  {t['text']!r} JS 求值失败：{type(e).__name__}: {str(e)[:140]}")
                ok = False
                continue
            good = r.get("hasCtl") and r.get("hasRemove") and r.get("foundEl")
            ok = ok and good
            log.info(f"  {'✓' if good else '✗'} {t['text']!r:24s} {r}")

        # 反面用例：不存在的 id 必须返回 foundEl=False（不出异常）
        js = CHECK_JS.replace("__TAB_ID__", json.dumps("li___NOT_EXIST___"))
        r = pg.evaluate(js)
        log.info(f"  反面用例（不存在的 id）：foundEl={r.get('foundEl')} "
                 f"（应为 False）hasCtl={r.get('hasCtl')}")
        ok = ok and (r.get("foundEl") is False) and r.get("hasCtl")

        log.ok("冒烟通过：JS 形态可求值、主标签栏 widget 可达、id 定位可用"
               if ok else "冒烟**未通过**，见上面 ✗")
        br.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
