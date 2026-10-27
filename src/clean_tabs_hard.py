"""硬清场：对付"僵尸标签"（✕ 可见可点但点了没反应）。

背景（2026-10-03）：金蝶自绘标签（`kdMainTabs` + `kendoSortable`，不是标准 kendoTabStrip），
关闭事件绑在内部 tab 对象上。标签 DOM 还在、内部对象已失效时，
✕ 点了**静默无响应**（不报错、不弹窗），标签就成了僵尸，越积越多
→ tabstrip 被挤爆 → 坐标错位 → 激活错标签 → 「找不到可见的列表网格」整链失败。

三级策略（逐级降级，每级都验证效果）：
  ① 右键标签栏 → 若有「关闭其他/关闭全部」→ 直接点（最优雅）
  ② 逐个关（真实鼠标）
  ③ 仍清不掉 → **刷新页面**（SPA 重置，最彻底）

⚠️ 刷新会丢失页面上的未保存内容；单据已入库的不受影响。
   默认不刷新，需显式 --reload。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, shot  # noqa: E402
from steps import KingdeeUI  # noqa: E402

CFG = load_config()

JS_MENU = KingdeeUI._INVISIBLE + """() => {
    const sels = ['.k-popup', '.k-menu', '.k-context-menu', '.kd-contextmenu', '.k-animation-container'];
    const seen = new Set(), out = [];
    sels.forEach(s => document.querySelectorAll(s).forEach(e => {
        if (!vis(e) || seen.has(e)) return;
        seen.add(e);
        const t = (e.innerText || '').trim().replace(/\\s+/g, ' ');
        if (t) out.push({sel: s, text: t.slice(0, 200),
                         items: Array.from(e.querySelectorAll('li,a,span,div'))
                                  .map(x => (x.innerText||'').trim()).filter(Boolean).slice(0, 20)});
    }));
    return out;
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-name", action="append", default=["销售出库单列表"])
    ap.add_argument("--reload", action="store_true", help="允许最后手段：刷新页面")
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()

    log = Logger(abs_path("logs", CFG), name="clean_tabs_hard")
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        page = [pg for c in br.contexts for pg in c.pages][0]
        ui = KingdeeUI(page, CFG, log, None)

        before = ui.tabs()
        log.info(f"清场前 {len(before)} 个：{[t['text'][:16] for t in before]}")

        # ---- ① 右键标签栏 → 「关闭其他/所有页签」----
        # ★ 关键：右键点**要保留的那个**标签，再用「关闭其他页签」——
        #   走金蝶原生关闭逻辑（会正确销毁内部 tab 对象），不像逐点 ✕ 那样留下僵尸。
        if before and a.go:
            keep_tab = next((t for t in before
                             if any(k in (t["text"] or "") for k in a.keep_name)), None)
            anchor = keep_tab or before[-1]
            log.info(f"右键锚点标签 = {anchor['text']!r} @({anchor['tx']},{anchor['ty']})"
                     f"（{'保留目标' if keep_tab else '非保留，将用「关闭所有」'}）")
            page.mouse.click(anchor["tx"], anchor["ty"], button="right")
            page.wait_for_timeout(900)
            menus = page.evaluate(JS_MENU)
            items = []
            for m in menus:
                if "页签" in (m["text"] or ""):
                    items = m["items"]
                    break
            log.info(f"右键菜单项（去重）= {sorted(set(items))}")
            # 优先「关闭其他页签」（锚点是保留目标时）；否则「关闭所有页签」
            order = (["关闭其他页签", "关闭所有页签"] if keep_tab
                     else ["关闭所有页签", "关闭其他页签"])
            hit = next((x for x in order if x in set(items)), None)
            if hit:
                log.info(f"★ 命中「{hit}」，真实鼠标点击")
                js = KingdeeUI._INVISIBLE + """(label) => {
                    const els = Array.from(document.querySelectorAll('li,a,span,div'))
                        .filter(e => vis(e) && (e.innerText || '').trim() === label);
                    if (!els.length) return null;
                    const r = els[els.length - 1].getBoundingClientRect();
                    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }"""
                pos = page.evaluate(js, hit)
                if pos:
                    page.mouse.click(pos["x"], pos["y"])
                    page.wait_for_timeout(3000)
                    log.ok(f"已点「{hit}」→ 现在 {len(ui.tabs())} 个标签")
                else:
                    log.warn(f"取不到「{hit}」坐标")
            else:
                log.warn(f"菜单里没有关闭项：{items}")
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)

        mid = ui.tabs()
        log.info(f"① 右键后 {len(mid)} 个：{[t['text'][:16] for t in mid]}")

        # ---- ② 逐个关 ----
        if a.go and len(mid) > 1:
            closed = ui.close_all_tabs(keep_names=a.keep_name)
            log.info(f"② 逐个关结果：{len(closed)} 个 → 剩 {len(ui.tabs())}")

        left = ui.tabs()
        log.info(f"清场后 {len(left)} 个：{[t['text'][:16] for t in left]}")

        # ---- ③ 还清不掉 → 刷新 ----
        if len(left) > 3 and a.go and a.reload:
            log.warn(f"仍有 {len(left)} 个僵尸标签 → 刷新页面重置 SPA")
            shot(page, abs_path("shots", CFG) if "shots" in CFG.get("paths", {}) else abs_path("logs", CFG),
                 "before_reload", log)
            page.reload(wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            log.info(f"刷新后标题 = {page.title()[:40]!r}")
            try:
                after = ui.tabs()
                log.ok(f"刷新后 {len(after)} 个标签：{[t['text'][:20] for t in after]}")
            except Exception as e:  # noqa: BLE001
                log.warn(f"刷新后取标签失败：{e}")
            shot(page, abs_path("logs", CFG), "after_reload", log)

        br.close()
    log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
