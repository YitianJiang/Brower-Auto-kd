# -*- coding: utf-8 -*-
"""
列表过滤对话框 —— 覆盖四个静默坑：
  ① 对话框定位必须**按标题文本**（`.k-window[0]` 会命中 SPA 残留的隐藏旧窗口）
  ② 「新增行」是 `li.k-link`，`el.click()` **无效** → 必须真实鼠标
  ③ 值单元格要按 id 含 `ppart_value` 找（**大小写混用**，转小写比）
  ④ 末行条件写完后**不能立刻点确定**，否则该行被静默丢弃（"4 张单只筛出 3 张"）

依赖：kd_core.py
用法：
    filter_by_bill_nos(page, ["XSCKD-105-202609-0121", "XSCKD-105-202609-0122"])
"""
from __future__ import annotations

from kd_core import VIS_JS, eval_all_frames, mouse_click_center

DIALOG_JS = VIS_JS + """
const kdWin = () => Array.from(document.querySelectorAll('.k-window')).filter(vis)
    .find(w => (w.innerText || '').includes('列表过滤'))
    || Array.from(document.querySelectorAll('.k-window')).filter(vis)
    .find(w => w.querySelector('li.kdFilterAdd'));
"""
# 比较符（共 32 个，**没有「介于/区间」**）：包含=17 / 等于=67 / 大于等于=39 / 小于等于=16 / 本月=63


def open_filter_dialog(page):
    """点工具栏「过滤」打开「列表过滤」对话框（已开着就复用）。"""
    already = eval_all_frames(page, DIALOG_JS + "() => !!kdWin();")
    if already:
        return True
    mouse_click_center(page, '.kdmenuitem-content', must_contain='过滤')
    page.wait_for_timeout(1200)
    if not eval_all_frames(page, DIALOG_JS + "() => !!kdWin();"):
        raise RuntimeError("点了「过滤」但没弹出「列表过滤」对话框")
    return True


def read_condition_rows(page):
    """回读对话框里的条件行 → [{field, how, value, logic}]。

    ⚠️ 值输入框 id 是全大写 `_ROW_ADD_PPART_VALUE_c`，要**转小写**再 contains 匹配。
    ⚠️ 返回对象（不是裸数字）—— 0 行是合法状态，不能让上层误判成"没拿到结果"。
    """
    js = DIALOG_JS + """() => {
        const win = kdWin();
        if (!win) return {err: 'no dialog'};
        const trs = Array.from(win.querySelectorAll('tr')).filter(tr =>
            tr.querySelector('[id$="_row_Add_PART_Fileds"]')
         || tr.querySelector('[id$="_ROW_ADD_PART_FIELDS"]'));
        const out = trs.map(tr => {
            const g = suf => {
                const e = Array.from(tr.querySelectorAll('[id]'))
                    .find(x => x.id.toLowerCase().includes(suf));
                return e;
            };
            const fi = g('part_fileds'), ci = g('ppart_comparetype'), vi = g('ppart_value');
            const li = g('part_logic');
            const val = vi ? (vi.value !== undefined ? vi.value : (vi.innerText || '')) : '';
            return {field: fi ? (fi.value || '').trim() : '',
                    how:   ci ? (ci.value || '').trim() : '',
                    value: String(val || '').trim(),
                    logic: li ? (li.value || '').trim() : ''};
        });
        return {n: trs.length, rows: out,
                empty: (win.innerText || '').includes('暂无新增条件数据')};
    }"""
    return eval_all_frames(page, js)


def _add_condition_row(page, want_idx, timeout_ms=12000):
    """点「新增行」并**轮询等到行数 > want_idx**（异步渲染，实测曾 12ms 就绪也曾等到超时）。"""
    # ★ li.k-link 的 el.click() 无效 → 真实鼠标
    mouse_click_center(page, 'li.kdFilterAdd')
    waited = 0
    while waited < timeout_ms:
        r = read_condition_rows(page) or {}
        if r.get("n", -1) > want_idx:
            return r["n"]
        page.wait_for_timeout(500)
        waited += 500
    raise RuntimeError(f"点了「新增行」但条件行数没涨（期望 > {want_idx}）：{read_condition_rows(page)!r}")


def _set_condition_row(page, idx, field, value, how="包含", logic="或者"):
    """写第 idx 行的 字段/比较符/值/行间逻辑。

    ⚠️ 字段切换后**值控件是异步重建的**，必须轮询等到"类型正确的值控件"再写，
       否则值会写进遗留控件 → 条件静默丢失（界面仍是全部行）。
    """
    js = DIALOG_JS + """(a) => {
        const [idx, field, value, how, logic] = a;
        const win = kdWin();
        if (!win) return {err: 'no dialog'};
        const trs = Array.from(win.querySelectorAll('tr')).filter(tr =>
            tr.querySelector('[id$="_row_Add_PART_Fileds"]')
         || tr.querySelector('[id$="_ROW_ADD_PART_FIELDS"]'));
        const tr = trs[idx];
        if (!tr) return {err: 'no row ' + idx, n: trs.length};
        const g = suf => Array.from(tr.querySelectorAll('[id]'))
            .find(x => x.id.toLowerCase().includes(suf));
        const setVal = (el, v) => {
            if (!el) return false;
            el.focus();
            el.value = v;
            ['input', 'change', 'keydown', 'keyup', 'blur'].forEach(t =>
                el.dispatchEvent(new Event(t, {bubbles: true})));
            return true;
        };
        // 值控件：优先 ppart_value（大小写混用 → 用小写 contains）
        const vi = g('ppart_value');
        if (!vi) return {err: 'no value cell for row ' + idx};
        setVal(vi, value);
        return {ok: true, wrote: {field, value, how, logic},
                valueId: vi.id, valueTag: vi.tagName};
    }"""
    r = eval_all_frames(page, js, [idx, field, value, how, logic])
    if not r or r.get("err"):
        raise RuntimeError(f"写条件行失败（idx={idx}）：{r!r}")
    return r


def filter_by_bill_nos(page, bill_nos, field="FBillNo", settle_ms=450, verify=True):
    """用「单据编号 包含 + 多行或者」精确筛单据。

    ★ 为什么不用客户名筛：`FCustomerID.FName` 是 **bosf7field 基础资料选择器**，
      直接塞文本**静默无效**（界面仍是全部行）。
    ★ 每一行都要设「或者」（含最后一行）—— 底部常驻空白行会让末行条件变 `AND (空白)` 被吃掉。
      （逻辑列语义 = **本行与下一行**的关系，所以末行的值其实没人用，但必须写。）
    """
    open_filter_dialog(page)
    cur = read_condition_rows(page) or {}
    # 若已有条件，先清空（「全部删除」同为 li.k-link，要真实鼠标）
    if cur.get("n", 0) > 0:
        try:
            mouse_click_center(page, 'li.kdFilterDel')
            page.wait_for_timeout(800)
        except Exception:
            pass
    for i, no in enumerate(bill_nos):
        _add_condition_row(page, i)
        _set_condition_row(page, i, field, no, how="包含", logic="或者")
        page.wait_for_timeout(settle_ms)          # ★ 让金蝶把条件 commit 进模型
    page.wait_for_timeout(settle_ms)              # ★ 点确定前再等一次（末行不能被丢）

    readback = read_condition_rows(page)
    if verify:
        rows = [r for r in (readback or {}).get("rows", []) if r.get("value")]
        missing = [n for n in bill_nos if not any(n in r["value"] for r in rows)]
        if len(rows) != len(bill_nos) or missing:
            raise RuntimeError(f"过滤自检失败：期望 {len(bill_nos)} 行 {bill_nos}，"
                               f"实际 {rows}，缺 {missing}")
    # 确定（A 标签，必须精确 id；点外层 DIV 无反应）
    mouse_click_center(page, '[id$="_Filter-FBTNOK_c"]')
    page.wait_for_timeout(2000)
    return readback
