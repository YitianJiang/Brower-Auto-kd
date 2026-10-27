# -*- coding: utf-8 -*-
"""
列表读取与勾选 —— 覆盖三个必踩的坑：
  ① 虚拟滚动：DOM 只渲染 ~200 行 → 读全量必须走**数据源**（kendoKDGrid）
  ② 列严格对齐：`filter(Boolean)` 会让"某格为空"的行整体错位
  ③ 表头复选框是**三态**语义：循环点到归零，且 0 行时别点

依赖：kd_core.py
用法：
    rows = grid_all_rows(page)                       # [{FBILLNO:..., FDate1:...}, ...] 全量
    n    = selected_count(page)                      # 真实选中行数（读分页栏，不是 DOM :checked）
    select_rows_by_billno(page, ["XSCKD-...-0121"])  # 真实鼠标逐行勾
    clear_selection(page)                            # 循环点表头到归零
"""
from __future__ import annotations

from kd_core import VIS_JS, eval_all_frames, mouse_click_center

# 出库单列表实测关键列（表头 20 个 th）：日期=3 / 单据编号=4 / 账务日期=5 / 客户=7


def grid_all_rows(page, min_rows=1, timeout_ms=20000):
    """读当前可见列表的**全量数据源**。

    ⚠️ 点完「过滤确定」后列表在异步重载：这一瞬间 grid 会短暂不存在（返回 None）
       或已出现但数据未回（读到 0 行）→ 必须**轮询等到 min_rows 行**，不要一枪定生死。
    ⚠️ 金蝶用自定义 `kendoKDGrid`（不是标准 `kendoGrid`）。
    """
    js = VIS_JS + """() => {
        const $ = window.jQuery;
        const t = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
        if (!t) return null;
        const gridEl = t.closest('.kd-grid-element');
        if (!gridEl) return {err: 'no .kd-grid-element'};
        const g = $(gridEl).data('kendoKDGrid');
        if (!g) return {err: 'no kendoKDGrid'};
        const flat = row => {
            const o = {};
            for (const k in row) {
                if (k.startsWith('_') || k.endsWith('__value')) continue;
                const v = row[k], ty = typeof v;
                if (v === null) { o[k] = null; continue; }
                if (ty === 'string' || ty === 'number' || ty === 'boolean') o[k] = v;
            }
            return o;
        };
        const data = g.dataSource.data().map(flat);
        return {n: data.length, domRows: t.querySelectorAll('tbody tr').length, rows: data};
    }"""
    waited, last = 0, None
    while waited <= timeout_ms:
        last = eval_all_frames(page, js)
        if isinstance(last, dict) and not last.get("err") and last.get("n", 0) >= min_rows:
            return last["rows"]
        page.wait_for_timeout(700)
        waited += 700
    raise RuntimeError(f"列表数据源未就绪（等 {timeout_ms}ms）：{last!r}")


def visible_grid_table(page):
    """读当前可见列表的可见行，**列严格对齐**（表头 th 与 td 同下标配对，空列保留 ''）。

    ⚠️ 绝不要 `filter(Boolean)` —— 发票列表的「发票号」常年为空，
       一过滤整行就往前错位（把「发票日期」读成「开票方式」的值）。
    """
    js = VIS_JS + """() => {
        const tables = Array.from(document.querySelectorAll('table')).filter(vis);
        const head = tables.find(t => t.querySelectorAll('th').length > 3);
        const body = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
        if (!head || !body) return null;
        const cols = Array.from(head.querySelectorAll('th')).map(th =>
            (th.getAttribute('data-title') || th.innerText || '').trim());
        const rows = Array.from(body.querySelectorAll('tbody tr')).filter(vis).map(tr => {
            const tds = Array.from(tr.querySelectorAll('td')).map(td => (td.innerText || '').trim());
            const o = {};
            cols.forEach((c, i) => { o[c || ('col' + i)] = tds[i] ?? ''; });   // 空列保留 ''
            return o;
        });
        return {cols, rows};
    }"""
    return eval_all_frames(page, js)


def selected_count(page):
    """真实选中行数 —— 读分页栏「已选 N 行」（DOM `:checked` 最多数到 201，不可用）。"""
    js = VIS_JS + """() => {
        const el = document.querySelector('.kd-grid-pager-col');
        const t = el ? (el.innerText || '') : '';
        const m = t.match(/已选\\s*(\\d+)\\s*行/);
        return {text: t.trim(), n: m ? parseInt(m[1], 10) : null};
    }"""
    return eval_all_frames(page, js)


def _header_checkbox_box(page):
    return eval_all_frames(page, VIS_JS + """() => {
        const h = Array.from(document.querySelectorAll('input.gridCheckHeader')).filter(vis)[0];
        if (!h) return null;
        h.scrollIntoView({block: 'center'});
        const r = h.getBoundingClientRect();
        return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
    }""")


def clear_selection(page, max_clicks=4):
    """取消全部选中。

    ⚠️ 表头复选框是**三态**语义：`未选/部分选中` 点一下 = **全选**，
       `全选` 点一下才 = 全清。老实现"点一次了事"会把"本来只勾 1 行"点成 565 行全选。
    ⚠️ 为 0 时**别点**（点了反而全选）。
    """
    for _ in range(max_clicks):
        cur = selected_count(page) or {}
        if cur.get("n") == 0:
            return 0
        p = _header_checkbox_box(page)
        if not p:
            return cur.get("n")
        page.mouse.click(p["x"], p["y"])
        page.wait_for_timeout(900)
    return (selected_count(page) or {}).get("n")


def select_rows_by_billno(page, bill_nos, billno_field="FBILLNO"):
    """按单据编号勾行 —— **真实鼠标逐行点**。

    ⚠️ 行复选框是 `<input onclick="return false;" class="gridCheck gridCheckCell">`，
       金蝶在**祖先容器**做事件委托 → 合成事件勾不上且不报错。
    ⚠️ 必须限定在**可见**的 `table.k-selectable` 里找行，否则会点到隐藏页面列表的行上。
    """
    want = set(bill_nos)
    js = VIS_JS + """(a) => {
        const [field, wants] = a;
        const want = new Set(wants);
        const body = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
        if (!body) return {err: 'no visible table.k-selectable'};
        const out = [];
        for (const tr of body.querySelectorAll('tbody tr')) {
            const txt = (tr.innerText || '');
            const hit = Array.from(want).find(no => txt.includes(no));
            if (!hit) continue;
            const cb = tr.querySelector('input.gridCheck.gridCheckCell');
            if (!cb) continue;
            cb.scrollIntoView({block: 'center'});
            const r = cb.getBoundingClientRect();
            out.push({bill: hit, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
        }
        return {found: out, domRows: body.querySelectorAll('tbody tr').length,
                domBills: Array.from(body.querySelectorAll('tbody tr')).map(t =>
                    (t.innerText || '').split('\\t').find(s => /-\\d{4}$/.test(s)) || '').slice(0, 300)};
    }"""
    r = eval_all_frames(page, js, [billno_field, list(bill_nos)])
    if not r or r.get("err"):
        raise RuntimeError(f"勾行失败：{r!r}")
    if not r["found"]:
        raise RuntimeError(f"可见区域没找到这些单据：{bill_nos}；当前页单号样例={r.get('domBills', [])[:12]}")
    for it in r["found"]:
        page.mouse.click(it["x"], it["y"])
        page.wait_for_timeout(250)
    got = (selected_count(page) or {}).get("n")
    return {"clicked": [i["bill"] for i in r["found"]], "selected": got}
