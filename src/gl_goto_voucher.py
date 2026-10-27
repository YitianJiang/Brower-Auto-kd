# -*- coding: utf-8 -*-
"""
定位并打开指定「总账凭证」到可编辑页 —— 走「凭证查询」这条路。

为什么需要：`write_digest.py` 依赖「凭证生成报告列表」，而那张列表是**瞬时单槽**的
（只保留最近一次凭证生成的报告；搜「凭证生成报告列表」0 结果，关掉就回不来）。
批量收尾清场把它关掉后，就只能从「凭证查询」按 **凭证字 + 凭证号** 捞凭证 ——
实测「凭证查询」里**双击一行**会打开标签「凭证 - 修改」（凭证为「暂存」时可编辑，
工具栏「保存」为可用）。

踩过才知道：
  · 「凭证查询」默认过滤 = 期间 2026 年 9 期，**979 行 / 200 行一页 = 5 页**；
    目标凭证在第 5 页第 153 行 —— **不在首屏**，必须翻页。
  · 列表是**虚拟滚动**（`.k-grid-content` 只露 ~10 行），第 153 行的 `<tr>` 虽在 DOM 里，
    但 `getBoundingClientRect()` 的 y 远在视口外 → 直接双击 = 点空。
    ⇒ 必须 `scrollIntoView({block:'center'})` 再取坐标。
  · 翻页用 `<grid>.dataSource.page(n)`（`kendoKDGrid`）比找分页按钮可靠。
  · 切标签用**真实鼠标点 tabstrip 元素坐标**；`ui.activate_tab()` 对溢出/带空格的名字不可靠。

用法：
    python src/gl_goto_voucher.py --no 155            # 只定位（dump 坐标）
    python src/gl_goto_voucher.py --no 155 --dbl      # 定位并双击打开
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright          # noqa: E402
from common import load_config, Logger, abs_path          # noqa: E402
from steps import KingdeeUI                               # noqa: E402
from kd_tab_sel import MAIN_TAB_SELECTOR                 # noqa: E402

TAB_JS = """(want) => {
  const vis = e => !!(e && e.getClientRects && e.getClientRects().length);
  // ★ 主标签栏选择器走单一真相源（SF-51）
  const strip = document.querySelector('__MAIN_TAB__')
             || Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0];
  if (!strip) return null;
  const items = Array.from(strip.querySelectorAll('.k-item.contentHeadEL'));
  const t = items.find(e => (e.innerText||'').trim() === want)
         || items.find(e => (e.innerText||'').includes(want));
  if (!t) return null;
  const b = t.getBoundingClientRect();
  return {x: Math.round(b.x + Math.min(40, b.width/3)), y: Math.round(b.y + b.height/2),
          text: (t.innerText||'').trim().slice(0,30)};
}"""

ACTIVE_JS = """() => {
  const vis = e => !!(e && e.getClientRects && e.getClientRects().length);
  const strip = document.querySelector('__MAIN_TAB__')
             || Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0];
  if (!strip) return null;
  const t = Array.from(strip.querySelectorAll('.k-item.contentHeadEL'))
    .find(e => String(e.className).includes('k-state-active'));
  return t ? (t.innerText||'').trim().slice(0,30) : null;
}"""

# 一次性把占位符替换成单一真相源选择器（模块加载时完成）
TAB_JS = TAB_JS.replace("__MAIN_TAB__", MAIN_TAB_SELECTOR)
ACTIVE_JS = ACTIVE_JS.replace("__MAIN_TAB__", MAIN_TAB_SELECTOR)

CLOSE_DLG = """() => {
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

GRID = """
(n) => {
  const vis = e => { const b=e.getBoundingClientRect(); const s=getComputedStyle(e);
    return b.width>0 && b.height>0 && s.display!=='none'; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  if (!g) return {err: 'no grid'};
  const heads = Array.from(g.querySelectorAll('thead th')).map(th => ({
      field: th.getAttribute('data-field') || '', text: (th.innerText||'').trim().slice(0,14)}));
  const rows = Array.from(g.querySelectorAll('tbody tr')).slice(0, n).map(tr => {
    const o = {};
    tr.querySelectorAll('span[data-field]').forEach(s => {
      o[s.getAttribute('data-field')] = (s.innerText||'').trim().slice(0,40); });
    return o;
  });
  return {heads: heads, rowCount: g.querySelectorAll('tbody tr').length, rows: rows};
}"""

GRID_META = """() => {
  const $ = window.jQuery;
  const vis = e => { const b=e.getBoundingClientRect(); return b.width>0 && b.height>0; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  const el = g && g.closest('.kd-grid-element');
  const gr = el && $(el).data('kendoKDGrid');
  if (!gr) return null;
  const ds = gr.dataSource;
  return {total: ds.total(), pageSize: ds.pageSize(), page: ds.page()};
}"""

PAGE_JS = """(n) => {
  const $ = window.jQuery;
  const vis = e => { const b=e.getBoundingClientRect(); return b.width>0 && b.height>0; };
  const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
  const el = g && g.closest('.kd-grid-element');
  const gr = el && $(el).data('kendoKDGrid');
  if (gr) gr.dataSource.page(n);
}"""


def activate_tab_by_text(pg, want, log=None):
    """用真实鼠标点 tabstrip 里的标签本体（比 activate_tab 可靠）。"""
    r = pg.evaluate(TAB_JS, want)
    if not r:
        if log:
            log.warn(f"找不到标签 {want!r}")
        return False
    pg.mouse.click(r["x"], r["y"])
    pg.wait_for_timeout(2200)
    if log:
        log.info(f"点标签 {r['text']!r} → 当前激活 {pg.evaluate(ACTIVE_JS)!r}")
    return True


def locate_row(pg, ui, log, zi, no, rows=220, expect=None):
    """在「凭证查询」里翻页找到 (凭证字, 凭证号)，返回 (页号, 页内行号)。

    expect: 摘要须含该关键词（★ SF-30 修复：同一「凭证字+号」可能跨字组/账簿重复，
            只用号定位会命中别的凭证。传 expect 用摘要二次锚定，命中错误时继续找下一处。）
    """
    d = pg.evaluate(GRID, rows)
    if d.get("err") or d["rowCount"] == 0 or not d["heads"]:
        log.info("列表空 → 点「过滤」应用默认方案")
        try:
            ui.click_menu("过滤", timeout_ms=3000)
            pg.wait_for_timeout(2200)
            ui.close_filter_dialog(ok_button=True)
            pg.wait_for_timeout(9000)
        except Exception as e:  # noqa: BLE001
            log.warn(f"过滤异常：{str(e)[:100]}")
        d = pg.evaluate(GRID, rows)

    def _scan(dd, start=0):
        for i in range(start, len(dd["rows"])):
            r = dd["rows"][i]
            if str(r.get("FVOUCHERGROUPNO") or "") == no \
                    and str(r.get("FVOUCHERGROUPID_FNAME") or "") == zi:
                if expect and expect not in str(r.get("FEXPLANATION") or ""):
                    continue   # 同号但摘要不符 → 不是这张，继续找
                return i
        return None

    i = _scan(d)
    if i is not None:
        return 1, i
    meta = pg.evaluate(GRID_META)
    log.info(f"分页信息：{meta}")
    if not meta:
        return None, None
    pages = int(-(-meta["total"] // max(1, meta["pageSize"])))
    for pno in range(2, pages + 1):
        pg.evaluate(PAGE_JS, pno)
        pg.wait_for_timeout(2600)
        d = pg.evaluate(GRID, rows)
        i = _scan(d)
        if i is not None:
            log.ok(f"在第 {pno}/{pages} 页第 [{i}] 行找到 {zi}{no}"
                   + (f"（摘要含 {expect!r}）" if expect else ""))
            return pno, i
    if expect:
        log.warn(f"翻遍所有页都没有「{zi}{no} 且摘要含 {expect!r}」的凭证"
                 f"（可能过滤期间不对，或该凭证不在当前过滤范围）")
    return None, None


def open_voucher(pg, ui, log, zi, no, rows=220, expect=None, date_range=None):
    """定位 → 滚到行 → 双击 → 激活「凭证 - 修改」。返回是否成功。
    expect: 摘要须含该关键词（SF-30：同字号跨字组重复时用摘要二次锚定）。
    date_range: (start, end) 可选 —— 打开「凭证查询」后先按「日期」FDATE 过滤
        （★ 凭证查询默认过滤 = 上一期，10 月凭证查不到；必须先改期间。
           ★★ 字段是 FDATE，不是应收单/出库单的 FDate1 —— 凭证查询列表无账务日期字段）。
    """
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(700)
    gone = pg.evaluate(CLOSE_DLG)
    if gone:
        log.info(f"关掉残留对话框：{gone}")
    pg.wait_for_timeout(1000)

    if not activate_tab_by_text(pg, "凭证查询", log):
        log.info("「凭证查询」标签不在 → 用搜索打开")
        ui.open_menu_by_search("凭证查询", path="财务会计>总账>凭证管理")
        pg.wait_for_timeout(3000)
        if not activate_tab_by_text(pg, "凭证查询", log):
            return False

    if date_range:
        log.info(f"先设日期过滤：FDATE {date_range[0]} ~ {date_range[1]}")
        ui.set_date_filter(date_range[0], date_range[1], field="FDATE")
        pg.wait_for_timeout(3000)

    pno, idx = locate_row(pg, ui, log, zi, no, rows=rows, expect=expect)
    if idx is None:
        log.warn(f"翻遍所有页都没找到 {zi}{no}")
        return False
    log.ok(f"{zi}{no} 在第 {pno} 页第 {idx} 行")

    # 虚拟滚动 → 先滚到行，再取视口内坐标
    pg.evaluate("""(i) => {
        const vis = e => { const b=e.getBoundingClientRect(); const s=getComputedStyle(e);
          return b.width>0 && b.height>0 && s.display!=='none'; };
        const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
        const tr = Array.from(g.querySelectorAll('tbody tr'))[i];
        if (tr) tr.scrollIntoView({block: 'center', inline: 'nearest'});
    }""", idx)
    pg.wait_for_timeout(1200)
    r = pg.evaluate("""(i) => {
        const vis = e => { const b=e.getBoundingClientRect(); const s=getComputedStyle(e);
          return b.width>0 && b.height>0 && s.display!=='none'; };
        const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
        const tr = Array.from(g.querySelectorAll('tbody tr'))[i];
        if (!tr) return null;
        const cell = tr.querySelector('span[data-field="FVOUCHERGROUPNO"]');
        if (!cell) return null;
        const b = cell.getBoundingClientRect();
        if (b.y < 0 || b.y > window.innerHeight) return {off: true, y: Math.round(b.y)};
        return {x: Math.round(b.x + b.width/2), y: Math.round(b.y + b.height/2)};
    }""", idx)
    if not r or r.get("off"):
        log.warn(f"滚到行后坐标仍在视口外：{r}")
        return False
    before = set(t["text"] for t in ui.tabs())
    pg.mouse.dblclick(r["x"], r["y"])
    pg.wait_for_timeout(4500)
    log.info(f"双击 @{r} → 激活 {pg.evaluate(ACTIVE_JS)!r}")
    # 若有新开的「凭证 - 修改」，切到它
    act = pg.evaluate(ACTIVE_JS) or ""
    if "凭证 - 修改" not in act:
        newer = [t["text"] for t in ui.tabs() if "凭证 - 修改" in t["text"]]
        if newer:
            activate_tab_by_text(pg, newer[-1], log)
    return "凭证 - 修改" in (pg.evaluate(ACTIVE_JS) or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no", required=True, help="凭证号（如 155）")
    ap.add_argument("--zi", default="转", help="凭证字（默认 转）")
    ap.add_argument("--dbl", action="store_true", help="定位后双击打开")
    ap.add_argument("--rows", type=int, default=220)
    a = ap.parse_args()

    cfg = load_config()
    log = Logger(abs_path("logs", cfg), name="gl_goto_voucher")
    try:
        with sync_playwright() as p:
            br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
            pg = br.contexts[0].pages[0]
            ui = KingdeeUI(pg, cfg, log, None)
            if a.dbl:
                ok = open_voucher(pg, ui, log, a.zi, a.no, rows=a.rows)
                print("结果：", "OK" if ok else "失败")
            else:
                activate_tab_by_text(pg, "凭证查询", log)
                print(locate_row(pg, ui, log, a.zi, a.no, rows=a.rows))
            br.close()
    except Exception as e:  # noqa: BLE001
        log.err(f"失败：{type(e).__name__}: {str(e)[:250]}")
        raise
    finally:
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
