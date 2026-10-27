#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kd_digest_via_gl.py —— 当「凭证生成报告列表」已被关掉、写不了摘要时，走**「凭证查询」**补摘要。

背景（见 references/silent-failures.md SF-50 / SF-52）：
  · 「凭证生成报告列表」是**瞬时单槽**的：只保留"最近一次"凭证生成的报告，
    顶部搜索里搜不到它，**一旦被清场关掉就再也打不开**。
  · 于是补摘要只能绕道：**凭证查询**（财务会计>总账>凭证管理）
    → 定位到 **凭证字 + 凭证号** → **双击该行** → 打开标签「凭证 - 修改」→ 改摘要 → 保存。
  · 两个必须处理的坑：
      1) 列表 **200 行/页**，期间内常有几百上千行 → 目标行往往**不在首屏**，要翻页；
         翻页用金蝶自定义 grid 的 `kendoKDGrid.dataSource.page(n)`，比找分页按钮可靠。
      2) 列表是**虚拟滚动**（`.k-grid-content` 只露 ~10 行）→ 目标行的 `<tr>` 虽在 DOM 里，
         但 `getBoundingClientRect().y` **在视口外**，直接 `mouse.dblclick` **点空且不报错**。
         ⇒ 必须先 `scrollIntoView({block:'center'})`，再取坐标、确认在视口内才点。

前置条件：
  · 已有一个登录好的 Chrome 开着 CDP（默认 http://127.0.0.1:9222）。
  · 目标凭证处于**暂存(A)**；若已提交(B)/已审核(C)，摘要格带 `k-gridCellstate-disabled`，
    会报"不可编辑" —— 此时应先「还原」，或走人工。
  · SPA 标签数 < 15（见 SF-51）：本脚本要新开「凭证查询」+「凭证 - 修改」共 2 个标签。

用法：
    python kd_digest_via_gl.py --vchno 155 \
        --digest "某公司销售水性油墨14527.49公斤"          # 不保存（先看回读）
    python kd_digest_via_gl.py --vchno 155 --digest "…" --go
    python kd_digest_via_gl.py --vchno 155 --digest "…" --go --cdp http://127.0.0.1:9222
"""
import argparse
import sys
import time

from playwright.sync_api import sync_playwright

# ---------- 页面侧 JS ----------
JS_TAB_CLICK = """(want) => {
  const vis = e => !!(e && e.getClientRects && e.getClientRects().length);
  const strip = Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0];
  if (!strip) return null;
  const items = Array.from(strip.querySelectorAll('.k-item.contentHeadEL'));
  const t = items.find(e => (e.innerText||'').trim() === want)
         || items.find(e => (e.innerText||'').includes(want));
  if (!t) return null;
  const b = t.getBoundingClientRect();
  return {x: Math.round(b.x + Math.min(40, b.width/3)), y: Math.round(b.y + b.height/2)};
}"""

JS_ACTIVE_TAB = """() => {
  const vis = e => !!(e && e.getClientRects && e.getClientRects().length);
  const strip = Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0];
  if (!strip) return null;
  const t = Array.from(strip.querySelectorAll('.k-item.contentHeadEL'))
    .find(e => String(e.className).includes('k-state-active'));
  return t ? (t.innerText||'').trim() : null;
}"""

# 关闭可见的「取消/退出」对话框（把挡路的残留弹窗清掉）
JS_CLOSE_DLG = """() => {
  const vis = e => { const b=e.getBoundingClientRect();
    return b.width>0 && b.height>0 && e.offsetParent !== null; };
  const out = [];
  Array.from(document.querySelectorAll('.k-window')).filter(vis).forEach(w => {
    const btn = Array.from(w.querySelectorAll('a,button,span'))
      .find(b => vis(b) && ['取消','退出'].includes((b.innerText||'').trim()));
    if (btn) { btn.click(); out.push((w.innerText||'').trim().slice(0,30)); }
  });
  return out;
}"""

# 顺带把「金蝶提示 / 超出页签上限」这类模态点「确定」关掉
JS_OK_MODAL = """() => {
  const vis = e => { const b=e.getBoundingClientRect();
    return b.width>0 && b.height>0 && e.offsetParent !== null; };
  const out = [];
  Array.from(document.querySelectorAll('.k-window')).filter(vis).forEach(w => {
    const t = (w.innerText||'');
    if (t.includes('金蝶提示') || t.includes('上限15个') || t.includes('不能打开当前功能页签')) {
      const btn = Array.from(w.querySelectorAll('a,button,span'))
        .find(b => vis(b) && (b.innerText||'').trim() === '确定');
      if (btn) { btn.click(); out.push(t.slice(0,40)); }
    }
  });
  return out;
}"""

JS_GRID = """(n) => {
  const vis = e => { const b=e.getBoundingClientRect(); const s=getComputedStyle(e);
    return b.width>0 && b.height>0 && s.display!=='none'; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  if (!g) return {err: 'no grid'};
  const rows = Array.from(g.querySelectorAll('tbody tr')).slice(0, n).map(tr => {
    const o = {};
    tr.querySelectorAll('span[data-field]').forEach(s => {
      o[s.getAttribute('data-field')] = (s.innerText||'').trim(); });
    return o;
  });
  return {rowCount: g.querySelectorAll('tbody tr').length, rows: rows};
}"""

JS_GRID_META = """() => {
  const $ = window.jQuery;
  const vis = e => { const b=e.getBoundingClientRect(); return b.width>0 && b.height>0; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  const el = g && g.closest('.kd-grid-element');
  const gr = el && $(el).data('kendoKDGrid');
  if (!gr) return null;
  const ds = gr.dataSource;
  return {total: ds.total(), pageSize: ds.pageSize(), page: ds.page()};
}"""

JS_GOTO_PAGE = """(n) => {
  const $ = window.jQuery;
  const vis = e => { const b=e.getBoundingClientRect(); return b.width>0 && b.height>0; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  const el = g && g.closest('.kd-grid-element');
  const gr = el && $(el).data('kendoKDGrid');
  if (gr) gr.dataSource.page(n);
}"""

JS_SCROLL_ROW = """(i) => {
  const vis = e => { const b=e.getBoundingClientRect(); const s=getComputedStyle(e);
    return b.width>0 && b.height>0 && s.display!=='none'; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  const tr = Array.from(g.querySelectorAll('tbody tr'))[i];
  if (tr) tr.scrollIntoView({block: 'center', inline: 'nearest'});
}"""

JS_ROW_RECT = """(i) => {
  const vis = e => { const b=e.getBoundingClientRect(); const s=getComputedStyle(e);
    return b.width>0 && b.height>0 && s.display!=='none'; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  const tr = Array.from(g.querySelectorAll('tbody tr'))[i];
  if (!tr) return null;
  const cell = tr.querySelector('span[data-field="FVOUCHERGROUPNO"]')
            || tr.querySelector('span[data-field]');
  if (!cell) return null;
  const b = cell.getBoundingClientRect();
  if (b.y < 0 || b.y > window.innerHeight) return {off: true, y: Math.round(b.y)};
  return {x: Math.round(b.x + b.width/2), y: Math.round(b.y + b.height/2)};
}"""

JS_READ_ENTRIES = """() => {
  const vis = e => { const b=e.getBoundingClientRect(); const s=getComputedStyle(e);
    return b.width>0 && b.height>0 && s.display!=='none'; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  if (!g) return null;
  return Array.from(g.querySelectorAll('tbody tr')).map(tr => {
    const o = {};
    tr.querySelectorAll('span[data-field]').forEach(s => {
      o[s.getAttribute('data-field')] = (s.innerText||'').trim(); });
    const ex = tr.querySelector('span[data-field="FEXPLANATION"]');
    if (ex) {
      const d = ex.closest('td').querySelector('div');
      o.__editable__ = !(d && d.className.toString().includes('disabled'));
    }
    return o;
  });
}"""

JS_CELL = """(i) => {
  const vis = e => { const b=e.getBoundingClientRect(); const s=getComputedStyle(e);
    return b.width>0 && b.height>0 && s.display!=='none'; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  if (!g) return {err:'no visible grid'};
  const tr = Array.from(g.querySelectorAll('tbody tr'))[i];
  if (!tr) return {err:'row out of range'};
  const s = tr.querySelector('span[data-field="FEXPLANATION"]');
  if (!s) return {err:'no digest cell'};
  const d = s.closest('td').querySelector('div');
  const r = s.closest('td').getBoundingClientRect();
  return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
          v: (s.innerText||'').trim(),
          editable: !(d && d.className.toString().includes('disabled'))};
}"""

JS_MENU_RECT = """(want) => {
  const vis = e => { const b=e.getBoundingClientRect();
    return b.width>0 && b.height>0 && e.offsetParent !== null; };
  const cands = Array.from(document.querySelectorAll('.kdmenuitem-content'))
    .filter(e => vis(e) && (e.innerText||'').trim() === want);
  const el = cands.find(e => (e.id||'').includes('BILLMENU_TOOLBAR')) || cands[0];
  if (!el) return null;
  const b = el.getBoundingClientRect();
  return {x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2)};
}"""


class Log:
    def info(self, m): print(f"  {m}", flush=True)
    def ok(self, m): print(f"✔ {m}", flush=True)
    def warn(self, m): print(f"⚠ {m}", flush=True)
    def err(self, m): print(f"✘ {m}", flush=True)


def click_menu(pg, text, log):
    r = pg.evaluate(JS_MENU_RECT, text)
    if not r:
        raise RuntimeError(f"找不到菜单 {text!r}")
    pg.mouse.click(r["x"], r["y"])
    log.info(f"点菜单 {text!r} @{r}")


def activate_tab(pg, name, log):
    r = pg.evaluate(JS_TAB_CLICK, name)
    if not r:
        return False
    pg.mouse.click(r["x"], r["y"])
    pg.wait_for_timeout(2200)
    log.info(f"激活标签 {name!r} → 当前 {pg.evaluate(JS_ACTIVE_TAB)!r}")
    return True


def locate(pg, zi, no, log, scan=220):
    d = pg.evaluate(JS_GRID, scan)
    if d.get("err") or d["rowCount"] == 0:
        log.info("列表空 → 打开「过滤」并应用默认方案")
        click_menu(pg, "过滤", log)
        pg.wait_for_timeout(2200)
        ok = pg.evaluate("""() => {
            const vis = e => { const b=e.getBoundingClientRect(); return b.width>0 && b.height>0; };
            const a = Array.from(document.querySelectorAll('a[id$="_Filter-FBTNOK_c"]')).filter(vis)[0];
            if (!a) return null;
            const b = a.getBoundingClientRect();
            return {x: Math.round(b.x+b.width/2), y: Math.round(b.y+b.height/2)};
        }""")
        if ok:
            pg.mouse.click(ok["x"], ok["y"])
        pg.wait_for_timeout(9000)
        d = pg.evaluate(JS_GRID, scan)

    def scan_page(dd):
        for i, r in enumerate(dd["rows"]):
            if str(r.get("FVOUCHERGROUPNO") or "") == no \
                    and str(r.get("FVOUCHERGROUPID_FNAME") or "") == zi:
                return i
        return None

    i = scan_page(d)
    if i is not None:
        return 1, i
    meta = pg.evaluate(JS_GRID_META)
    log.info(f"分页信息 {meta}")
    if not meta:
        return None, None
    pages = int(-(-meta["total"] // max(1, meta["pageSize"])))
    for pno in range(2, pages + 1):
        pg.evaluate(JS_GOTO_PAGE, pno)
        pg.wait_for_timeout(2600)
        d = pg.evaluate(JS_GRID, scan)
        i = scan_page(d)
        if i is not None:
            return pno, i
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vchno", required=True, help="凭证号，如 155")
    ap.add_argument("--vchzi", default="转", help="凭证字，默认 转")
    ap.add_argument("--digest", required=True, help="要写入的摘要（所有非空摘要行统一改）")
    ap.add_argument("--go", action="store_true", help="回读一致后点「保存」")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--scan", type=int, default=220, help="每页扫描行数上限")
    a = ap.parse_args()

    log = Log()
    rc = 0
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(a.cdp)
        pg = br.contexts[0].pages[0]

        pg.keyboard.press("Escape")
        pg.wait_for_timeout(700)
        for js in (JS_CLOSE_DLG, JS_OK_MODAL):
            gone = pg.evaluate(js)
            if gone:
                log.info(f"关掉挡路弹窗：{gone}")
        pg.wait_for_timeout(1000)

        if not activate_tab(pg, "凭证查询", log):
            raise RuntimeError("没有「凭证查询」标签 —— 请先在金蝶里打开它（顶部搜索「凭证查询」）")

        pno, idx = locate(pg, a.vchzi, a.vchno, log, scan=a.scan)
        if idx is None:
            raise RuntimeError(f"翻遍所有页都没找到 {a.vchzi}{a.vchno}")
        log.ok(f"{a.vchzi}{a.vchno} 在第 {pno} 页第 {idx} 行")

        # 虚拟滚动 → 滚到行 → 取视口内坐标 → 双击
        pg.evaluate(JS_SCROLL_ROW, idx)
        pg.wait_for_timeout(1200)
        r = pg.evaluate(JS_ROW_RECT, idx)
        if not r or r.get("off"):
            raise RuntimeError(f"滚到行后坐标仍在视口外：{r}")
        before = pg.evaluate(JS_ACTIVE_TAB)
        pg.mouse.dblclick(r["x"], r["y"])
        pg.wait_for_timeout(4500)
        after = pg.evaluate(JS_ACTIVE_TAB)
        log.info(f"双击 @{r} → {before!r} → {after!r}")
        if "凭证 - 修改" not in (after or ""):
            if not activate_tab(pg, "凭证 - 修改", log):
                raise RuntimeError("双击后没打开「凭证 - 修改」")

        entries = pg.evaluate(JS_READ_ENTRIES) or []
        log.info(f"分录 {len(entries)} 行")
        for i, e in enumerate(entries):
            if (e.get("FEXPLANATION") or "").strip() or (e.get("FDEBIT") or ""):
                log.info(f"  [{i}] {e.get('FEXPLANATION')!r} {e.get('FACCTFULLNAME')!r} "
                         f"借={e.get('FDEBIT')} 贷={e.get('FCREDIT')} "
                         f"{'可编辑' if e.get('__editable__') else '只读'}")

        idxs = [i for i, e in enumerate(entries) if (e.get("FEXPLANATION") or "").strip()]
        if not idxs:
            raise RuntimeError("没有非空摘要行 —— 页面对不对？")
        for i in idxs:
            info = pg.evaluate(JS_CELL, i)
            if not isinstance(info, dict) or info.get("err"):
                raise RuntimeError(f"第 {i} 行摘要格定位失败：{info}")
            if not info.get("editable"):
                raise RuntimeError(f"第 {i} 行摘要格**不可编辑**（凭证可能已提交/已审核，先「还原」）")
            if info.get("v") == a.digest:
                log.info(f"第 {i} 行已是目标摘要，跳过")
                continue
            pg.mouse.dblclick(info["x"], info["y"])
            pg.wait_for_timeout(700)
            inp = pg.locator('input[id$="-FENTITY_FEXPLANATION_c"]:visible').first
            inp.fill(a.digest)
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(600)

        after2 = pg.evaluate(JS_READ_ENTRIES) or []
        ok = all((after2[i].get("FEXPLANATION") or "").strip() == a.digest for i in idxs)
        for i in idxs:
            log.info(f"  [{i}] 改后 = {after2[i].get('FEXPLANATION')!r}")
        if ok:
            log.ok("回读校验：全部一致")
        else:
            log.err("回读校验：有不一致 → 不保存")
            rc = 1

        if a.go and ok:
            click_menu(pg, "保存", log)
            pg.wait_for_timeout(3000)
            log.ok("已点「保存」（不提交不审核）")
        elif not a.go:
            log.info("（-go 未给，不保存）")
        br.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
