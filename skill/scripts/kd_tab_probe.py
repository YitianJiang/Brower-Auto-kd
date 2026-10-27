#!/usr/bin/env python
"""标签诊断工具 —— 专治「✕ 点了删不掉 / 标签越积越多 / 激活错标签」（SF-40 / SF-42）。

用途
----
1. `--dump`（默认）：列出所有标签，标出 **孤儿嫌疑**（缺 `k-tab-on-top`、内容面板已销毁）
2. `--try-ways`      ：逐种点击方式试关一个目标，找出当前环境里哪种有效
3. `--close-orphans` ：清场 —— 先 `locator.click()`，**回读判定**；失败就
                       **走 `kendomainTabControl.remove()` 摘除**（SF-51 定案，孤儿也能摘），
                       仍失败才记黑名单跳过（⚠️ 会真摘；`--dry` 只报告不点）

为什么不刷新页面：**金蝶 `reload()` = 登出**（SF-41）。别用刷新清标签。
★★ 孤儿标签的入口：主栏 widget 的 data 键是 **`kendomainTabControl`**，
   **不是** `kendoTabStrip`（整页 14 个 tabstrip 里只有主栏取不到它）。详见 SF-51。

判定"孤儿标签"的依据（实测 2026-10-03）：
  - 活标签   `li` 有  `k-tab-on-top`（激活时还有 `k-state-active`）
  - 孤儿标签 `li` **没有** `k-tab-on-top`，且点 ✕ 静默无响应
  注意：孤儿**不是**因为坐标错/被遮挡 —— `elementFromPoint(✕中心)` 是能命中的。

用法
----
    python kd_tab_probe.py --dump
    python kd_tab_probe.py --try-ways
    python kd_tab_probe.py --close-orphans --dry
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kd_core import VIS_JS, eval_all_frames  # noqa: E402

DEFAULT_CDP = "http://127.0.0.1:9222"

JS_DUMP = VIS_JS + """() => {
    const strip = Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0];
    if (!strip) return {err: 'no visible tabstrip'};
    const sr = strip.getBoundingClientRect();
    const items = Array.from(strip.querySelectorAll('.k-item.contentHeadEL'));
    return {
        vw: innerWidth, vh: innerHeight,
        stripW: Math.round(sr.width), scrollW: strip.scrollWidth,
        overflow: strip.scrollWidth > strip.clientWidth + 2,
        // 金蝶用自绘标签管理，这里必为 null 才符合预期
        kendoTabStrip: (window.jQuery && strip)
            ? String(!!window.jQuery(strip).data('kendoTabStrip')) : 'no-jq',
        n: items.length,
        items: items.map((e, i) => {
            const c = e.querySelector('.mainTabCloseButton, .k-i-close');
            const rc = c ? c.getBoundingClientRect() : null;
            const r = e.getBoundingClientRect();
            const cls = String(e.className || '');
            return {
                i,
                id: e.id || null,
                text: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 24),
                vis: vis(e),
                active: cls.includes('k-state-active'),
                onTop: cls.includes('k-tab-on-top'),      // ★ 缺它 = 孤儿嫌疑
                orphanSuspect: !cls.includes('k-tab-on-top'),
                cx: rc ? Math.round(rc.x + rc.width/2) : null,
                cy: rc ? Math.round(rc.y + rc.height/2) : null,
                h: Math.round(r.height),
            };
        }),
    };
}"""

JS_FIRST_CLOSE = VIS_JS + """(sel) => {
    const el = Array.from(document.querySelectorAll(sel)).filter(vis)[0];
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
}"""

CLOSE_SEL = "li.k-item.contentHeadEL span.mainTabCloseButton"


def snap(page):
    return eval_all_frames(page, JS_DUMP) or {}


def show(d, title=""):
    if not d or d.get("err"):
        print("❌", (d or {}).get("err"))
        return
    print(f"\n{title}")
    print(f"视口 {d['vw']}x{d['vh']}  tabstrip 宽 {d['stripW']}px  "
          f"scrollW={d['scrollW']}  {'⚠️ 溢出!' if d['overflow'] else '未溢出'}  "
          f"kendoTabStrip={d['kendoTabStrip']}（null 才正常）")
    print(f"{'i':>3} {'文本':<26} {'vis':<6} {'active':<7} {'on-top':<7} "
          f"{'✕坐标':<13} {'高':<4} 判定")
    print("-" * 96)
    for it in d["items"]:
        verdict = "孤儿嫌疑(可摘)" if it["orphanSuspect"] and it["vis"] else \
                  ("活" if it["vis"] else "(不可见)")
        print(f"{it['i']:>3} {it['text']:<26} {str(it['vis']):<6} {str(it['active']):<7} "
              f"{str(it['onTop']):<7} {str((it['cx'], it['cy'])):<13} {it['h']:<4} {verdict}")
    n_orphan = sum(1 for x in d["items"] if x["orphanSuspect"] and x["vis"])
    print(f"\n可见标签 {sum(1 for x in d['items'] if x['vis'])} 个，"
          f"其中**孤儿嫌疑 {n_orphan} 个**")
    if n_orphan:
        print("⇒ 孤儿会累积并把 tabstrip 挤爆 → 坐标错位 → activate 错标签 → "
              "「找不到可见的列表网格」整链失败（SF-40）")


def count(page):
    d = snap(page)
    return len(d.get("items", []))


def cmd_try_ways(page):
    """逐种点击方式试关，找出当前环境哪种有效。"""
    print("\n逐个尝试关闭方式（每次重取标签）……")
    ways = [
        ("locator.click()", lambda pg, t: pg.locator(
            f'[id="{t["id"]}"] span.mainTabCloseButton').first.click(timeout=6000)
            if t.get("id") else None),
        ("page.mouse.click(cx,cy)", lambda pg, t: pg.mouse.click(t["cx"], t["cy"])
            if t.get("cx") else None),
        ("locator.click(force=True)", lambda pg, t: pg.locator(
            f'[id="{t["id"]}"] span.mainTabCloseButton').first.click(force=True, timeout=6000)
            if t.get("id") else None),
        ("JS el.click()", lambda pg, t: pg.evaluate(
            VIS_JS + """(id) => { const e = document.querySelector(`[id="${id}"] span.mainTabCloseButton`);
                                  if (e) e.click(); return !!e; }""", t.get("id"))),
    ]
    results = {}
    for name, fn in ways:
        d = snap(page)
        tgt = next((x for x in d.get("items", [])
                    if x["vis"] and x["orphanSuspect"]), None)   # 优先拿孤儿做实验
        if not tgt:
            print(f"[{name}] 没有孤儿标签可测，跳过")
            results[name] = None
            continue
        n0 = len(d["items"])
        print(f"\n[{name}] 目标 {tgt['text']!r} ✕@({tgt['cx']},{tgt['cy']}) id={tgt['id']}")
        try:
            fn(page, tgt)
        except Exception as e:  # noqa: BLE001
            print(f"   抛异常：{type(e).__name__}: {str(e)[:100]}")
        time.sleep(1.5)
        n1 = count(page)
        ok = n1 < n0
        results[name] = ok
        print(f"   {n0} → {n1} 个  {'✓ 有效' if ok else '✗ 无效'}")
    print("\n=== 汇总 ===")
    for k, v in results.items():
        print(f"  {'✓' if v else ('✗' if v is False else '-')}  {k}")
    print("\n⚠️ 注意：**对孤儿标签，以上方式可能全部无效**（这是 SF-40 的特征）。")
    print("   此时不要刷新页面（会登出，SF-41），改用 kd_stray.py / --close-orphans 跳过。")


def cmd_close_orphans(page, dry):
    """黑名单跳过式清场。"""
    failed, closed, blocked = set(), [], 0
    for _ in range(80):
        d = snap(page)
        items = [x for x in d.get("items", []) if x["vis"]]
        if len(items) <= 1:
            break
        # 保留第一个（通常是出库单列表）；优先关孤儿之外的
        pool = [x for x in items[1:] if x.get("id") not in failed]
        pool = [x for x in pool if "销售出库单列表" not in (x["text"] or "")]
        if not pool:
            print(f"无可关标签（已跳过 {len(failed)} 个关不掉的）")
            break
        t = pool[-1]
        before = [x.get("id") for x in items]
        if dry:
            print(f"[dry] 会关 {t['text']!r} id={t['id']}")
            failed.add(t["id"])      # dry 模式下直接当"关不掉"避免死循环
            continue
        ok = False
        if t.get("id"):
            try:
                page.locator(f'[id="{t["id"]}"] span.mainTabCloseButton').first.click(timeout=6000)
                ok = True
            except Exception as e:  # noqa: BLE001
                print(f"   locator 失败：{type(e).__name__}: {str(e)[:70]}")
        if not ok and t.get("cx"):
            try:
                page.mouse.click(t["cx"], t["cy"])
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.8)
        after = [x.get("id") for x in (snap(page).get("items") or [])]
        if after == before:
            # ★ SF-51 定案：✕ 点不动 ≠ 无解 —— 走主标签栏自己的 widget
            #   `kendomainTabControl.remove()`（**不是** kendoTabStrip），实测 6/6 摘除成功。
            try:
                from kd_stray import force_remove_tab
                if force_remove_tab(page, t, logger=print):
                    time.sleep(0.7)
                    after2 = [x.get("id") for x in (snap(page).get("items") or [])]
                    if after2 != before:
                        closed.append(t["text"])
                        print(f"   ✓ {t['text']!r} 点 ✕ 无反应 → kendomainTabControl.remove() 摘除成功")
                        continue
            except Exception as e:  # noqa: BLE001
                print(f"   force_remove_tab 不可用：{type(e).__name__}: {str(e)[:70]}")
            failed.add(t["id"])
            blocked += 1
            print(f"   ✗ {t['text']!r} 点了没反应 → 记为孤儿，跳过（累计 {len(failed)}）")
        else:
            closed.append(t["text"])
            print(f"   ✓ 关掉 {t['text']!r}")
    d = snap(page)
    left = [x["text"] for x in d.get("items", []) if x["vis"]]
    print(f"\n清场结束：关掉 {len(closed)} 个，跳过 {len(failed)} 个关不掉的，剩 {len(left)} 个 {left}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true", help="只列出标签并标注孤儿（默认行为）")
    ap.add_argument("--try-ways", action="store_true", help="逐种点击方式试关")
    ap.add_argument("--close-orphans", action="store_true", help="黑名单跳过式清场")
    ap.add_argument("--dry", action="store_true", help="只报告不点击（配合 --close-orphans）")
    ap.add_argument("--cdp", default=DEFAULT_CDP, help="调试浏览器地址")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright  # noqa: E402
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(a.cdp)
        pages = [pg for c in br.contexts for pg in c.pages]
        if not pages:
            print("调试浏览器里没有页面")
            return 1
        page = pages[0]
        if a.try_ways:
            cmd_try_ways(page)
        elif a.close_orphans:
            show(snap(page), "=== 清场前 ===")
            cmd_close_orphans(page, a.dry)
        else:
            show(snap(page), "=== 标签现状 ===")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
