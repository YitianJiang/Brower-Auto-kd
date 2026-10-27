# scripts/ —— 自包含可复用模块

> 这些文件**不依赖原项目**，拷到一个目录里就能用（只要你有 Playwright 接管的金蝶 page）。
> 目标：把"每次都要重写、每次都会写错"的那部分固化成代码。
> 完整项目实现见 SKILL.md §七（本目录只放**最通用、最容易写错**的部分）。

## 依赖关系

```
kd_core.py  ←── 公共底座（所有交互模块都 import 它）
   ├── kd_combo.py    下拉（含 kdcombo 真实鼠标）
   ├── kd_grid.py     列表全量读 / 勾选
   ├── kd_filter.py   过滤对话框
   ├── kd_save.py     保存/提交判定
   ├── kd_query.py    只读查询（ExecuteBillQuery）
   ├── kd_verify.py   全链核验（核验项 + 摘要对拍）
   ├── kd_stray.py    残留弹窗 / 标签清场
   ├── kd_search.py   顶部全功能搜索导航（含"搜到了却点不动"的解药）
   └── kd_tab_probe.py 标签诊断（孤儿标签 / 哪种点击有效）

kd_guard.py         ← 安全闸门（零依赖，与 config 无关，建议每个脚本开头都 import）
digest_rule.py      ← 纯计算，零依赖
discount_rule.py    ← 纯计算，零依赖
kd_digest_via_gl.py ← 独立脚本（只依赖 Playwright）：凭证报告列表没了时，走「凭证查询」补摘要
```

## ★★ 红线：不能删除凭证

`kd_guard.py` 把动作分成两类，**每个写单据的脚本都应在入口引一次**：

| 类别 | 例子 | 放行方式 |
|---|---|---|
| A 开关式不可逆 | 提交 / 审核 / 生成凭证 / 保存凭证 | config 的 `safety.auto_*` 开关（默认 false） |
| B ★ 绝对禁止 | **删除凭证** / 批量删凭证 / 删除已审核单据 / 作废凭证 | **没有放行方式**，永远抛 `GuardError` |

```python
from kd_guard import assert_allowed, assert_click_safe, SafetyGuard

assert_allowed("delete_voucher")   # ★ 无条件抛 —— 用作"绝不允许走到这一步"的断言
assert_click_safe(menu_text)       # 点菜单前体检：命中「删除凭证/作废凭证/凭证删除」即抛

g = SafetyGuard(cfg.get("safety", {}))
g.check("submit")                  # A 类：看开关
g.check("delete_voucher")          # B 类：就算所有开关都 True，照样抛
```

**理由**：凭证是「单据 → 专票 → 凭证」的终态与唯一账务留痕；金蝶无回收站、删除不可逆；
且删除动作**没有必录校验、不弹错、kdsvc 正常返回** —— 脚本极易顺手点掉而不自知，事后只能回滚备份。
要改凭证 → 「凭证 - 修改」页；想留痕不进账 → **作废**（作废留痕，删除不留）。

> 注意 `assert_click_safe` 只拦**精确危险词**，不拦单独的「删除」——
> 过滤对话框的「删除行 / 全部删除」是合法日常操作。

自检（不需要浏览器）：`python kd_guard.py` —— 会验证「所有开关打开 + `auto_delete=true` 仍然拦得住删除凭证」。

## 各模块对应哪个坑

| 文件 | 解决的静默失败 | 核心函数 |
|---|---|---|
| `kd_core.py` | SF-10（0 被当假值）、SF-01（可用性过滤）、SF-09（视口外点击） | `eval_all_frames` / `mouse_click_center` / `visible_dialogs` / `activate_tab` |
| `kd_combo.py` | SF-09（kdcombo 必须真实鼠标）、SF-27（日期走 Kendo API）、**SF-55（写日期必须只认前台表单 + 写后回读）** | `fill_combo` / `read_combo` / **`fill_date`（已加前台定位 + 回读校验）** / **`read_field`** |
| `kd_grid.py` | SF-24（虚拟滚动）、SF-21（列错位）、SF-06/07（勾选） | `grid_all_rows` / `visible_grid_table` / `select_rows_by_billno` / `clear_selection` |
| `kd_filter.py` | SF-05（末行条件丢失）、SF-08（新增行无效）、SF-11（窗口定位）、SF-25（大小写） | `filter_by_bill_nos` / `read_condition_rows` |
| `kd_save.py` | SF-02（假保存）、SF-01（隐藏按钮） | `save_and_verify` / `save_expect_success` |
| `kd_query.py` | SF-18/19/20（查询口径）、SF-30（`GL_VOUCHER.FBillNo` 是 FID）、**SF-35（保存后读不到单号→反查）** | `KdQuery.ar_head/ar_entries/ar_children` / **`voucher_by_digest`** / **`ar_by_source_bill`** / **`latest_ar`** / **`latest_iv`** |
| `kd_verify.py` | 跑完怎么确认真成了 | `verify_unit` / `verify_ar` / `verify_invoice` / `verify_voucher` / `Checks` |
| `kd_stray.py` | SF-31（别人残留弹窗吃掉点击）、SF-32/33（并发环境）、**SF-51（孤儿标签摘除：走 `kendomainTabControl`，不再"关不掉就跳"）** | `dump_visible_dialogs` / `close_stray_windows` / **`force_remove_tab`** / `close_tabs_keep` |
| `kd_search.py` | **SF-49（搜到了却点不动：残留浮动提示条盖住结果列表）** | `open_list` / `click_search_result` / `dismiss_floating_hints` |
| `kd_tab_probe.py` | **SF-40（孤儿标签点不掉）、SF-41（刷新=登出）、SF-42（"关掉N个"是假的）** | `--dump` / `--try-ways` / `--close-orphans` |
| `kd_digest_via_gl.py` | **SF-50（报告列表瞬时单槽、关了回不来）→ 改走 SF-52（凭证查询虚拟滚动+翻页）/ SF-53（业务凭证查询的账簿占位陷阱）** | `--vchno 155 --digest "…" [--go]` |
| `kd_guard.py` | —（**红线**，不是静默失败：删除凭证不可逆且无校验） | `assert_allowed` / `assert_click_safe` / `SafetyGuard.check` |
| `digest_rule.py` | —（摘要口径，含 `omit_zero` 省略零项） | `classify_rows` / `build_digest` |
| `discount_rule.py` | —（分摊与对齐） | `allocate_by_amount` / `plan_material_align` |

> **SF-34（截图每次卡 30 秒）没有对应模块** —— 它是**截图封装**的写法问题，不是业务动作。
> 修法见 `references/silent-failures.md` SF-34：`page.screenshot(..., timeout=5000,
> animations="disabled", caret="hide")`，失败再降级 CDP `Page.captureScreenshot` 直取。
> 一条整链 3 处截图 → 白扔 90 秒，**这是提速最划算的一处**。

## 两个新增模块怎么用（2026-09-22）

**`kd_verify.py` —— 跑完整链之后，用它出结论（只读，零副作用）**
```bash
# 单笔
python kd_verify.py --ar AR105-202609-0001 --inv SVINV00033675 \
    --cust 慈溪市润科包装材料有限公司 --vch 转66 \
    --notax 18141.59 --tax 2358.41 --all 20500 --rows 5 --date 2026-09-30
# 多笔
python kd_verify.py --spec spec.json      # [{"cust":...,"ar":...,"inv":...,"all":...}, ...]
```
判据链：应收单状态 C → 三项金额 → **子单数 0** → 专票状态 C → 与应收单逐分对拍 →
**从应收单明细重算摘要 → 用摘要原文定位凭证** → 借贷平衡且 = 价税合计 → 状态 `[B]`。
退出码 `0/1`，可直接串进流水线。

**`kd_stray.py` —— 点击全被拦 / 标签点不动时先用它**
```bash
python kd_stray.py --dump                  # ① ★ 先看是谁的单（别把别人的失败当自己的）
python kd_stray.py --close                 # ② 关掉标题含「处理结果」的窗（真实鼠标点 ✕）
python kd_stray.py --close-tabs --keep 销售出库单列表   # ③ 生成凭证后的标准清场
```

**`kd_tab_probe.py` —— 标签越积越多 / 疑似激活错标签时**
```bash
python kd_tab_probe.py --dump             # 列标签，标出缺 k-tab-on-top 的（此前被认为"关不掉"）
python kd_tab_probe.py --try-ways         # 逐种点击方式试关，找出当前环境哪种有效
python kd_tab_probe.py --close-orphans --dry   # 黑名单跳过式清场（先干跑）
python kd_tab_probe.py --close-orphans    # 真清场：关不掉的记下跳过，**继续关其他**
```

### ★★★ SF-51 定案：「孤儿标签」其实**摘得掉** —— 主栏 widget 是 `kendomainTabControl`

```python
from kd_stray import force_remove_tab
force_remove_tab(page, {"id": tab["id"]}, logger=log)   # 返回 True 只是"请求已发出"
# ⚠️ 是否真摘掉，**必须回读 tabs(page) 对比判定**（判据永远在"回读"这一侧）
```

- **主标签栏的 widget 注册名是 `kendomainTabControl`**（金蝶自研的 Kendo TabStrip **子类**），
  **不是** `kendoTabStrip`。实测整页 14 个 `ul.k-tabstrip-items` 里**只有主栏**取不到
  `kendoTabStrip`（其余 13 个是单据表单内的页组）⇒ 所有 `data('kendoTabStrip')` 尝试必然落空。
- 这就是"✕ / `locator.click` / `mouse.click` / `force=True` / `dispatch_event` / JS `el.click()`
  六种全无效、连金蝶自带「关闭其他页签」也点空"的**最后一层原因：不是点不中，是入口找错了**。
- 实测 `ctl.remove($(li))` **6/6 摘除成功**（标签 7 → 1，保留锚点完好）。
  `close_tabs_keep` 已改为「优先 `locator.click()`（等位置稳定，见 SF-49）→ **回读判定** →
  `force_remove_tab()` 兜底 → 仍失败才记黑名单跳过」。
- ⚠️ **`ctl.items()` 恒定比真实标签数多 2** —— 主栏长期驻留 **2 个零宽幽灵 `li`**
  （`text=''`、无 ✕、rect 宽高为 0）。`tabs()` 的 `vis()` 要求 `width>0` 正好滤掉；
  自己写 dump 若用 `offsetParent!==null` 会多算 2 个 → **序号错位 → 摘错标签**。
- ⚠️ 只摘 tabstrip 的 `li`，**不碰单据数据**（不保存/不提交/不删除）；
  摘掉未保存草稿页 = 丢弃草稿（无损失）；**别人的/别的链路的活单据标签不要动**。

★ **千万不要用刷新页面来清标签** —— 金蝶 reload 会**直接登出**（SF-41），
  会打断用户的会话。**现在也不需要了**：孤儿能摘掉，不必腾位。

> ★★★ 2026-10-27 教训：**「主标签栏选择器」这个知识点曾经在 skill 侧修对了、
> 项目侧却没同步（各自抄了旧选择器 `ul.k-tabstrip-items`）→ 反复复发**。
> 根治：项目侧 `src/kd_tab_sel.py` 是**唯一真相源**（`MAIN_TAB_SELECTOR = "ul.kdMainTabsHeaderExtend"`、
> `WIDGET_KEY = "kendomainTabControl"`），生产代码（steps.py / gl_goto_voucher.py /
> run_batch_*.py / selftest_force_remove.py）一律 import 它，**不再各自手写选择器字符串**。
> 改选择器只改 `kd_tab_sel.py` 一处；开跑前跑 `python src/selftest_force_remove.py` 自检，
> 或 `run_batch_*.py --precheck`（已内置该自检，选错会当场 ✗ 返回码 3）。

**`kd_search.py` —— "搜到了却点不动"（`intercepts pointer events`）时用它**
```python
from kd_search import open_list, dismiss_floating_hints, click_search_result
open_list(page, log, "销售出库单列表")      # 一步到位：清残留提示条 → 点结果（多级兜底）
dismiss_floating_hints(page, log)          # 也可在流程开头单独清一次
```
症状：`Locator.click: Timeout …` 且日志说
`<div class="kd-form-collapse-container"> intercepts pointer events`。
真凶是**右上角的残留浮动提示条**（文案含「该字段需要您手动查找」，`z-index:10010`），
它**正好盖住搜索结果列表**。⚠️ 剥它的 `pointer-events` **没用**（下面直接是 `BODY`，
条目不在那个坐标上）⇒ 必须 **JS `el.click()`** 兜底（该条目绑 jQuery click，能触发）。
⚠️ 用 `kd_combo.py` 的"合成点击无效"结论时注意区分：**处理器是 jQuery 就行，是 Kendo 就不行。**
```
★ 安全边界：`--close` **只动标题命中 allowlist 的窗**（默认 `("处理结果",)`），
且**点的是「退出/取消/关闭」或右上角 ✕，不含「确定」** —— 误点「确定」会真提交/真保存。
要放宽必须显式 `--titles ...`。

## 快速上手

```python
from playwright.sync_api import sync_playwright
from kd_core import active_tab
from kd_query import KdQuery
from kd_filter import filter_by_bill_nos
from kd_grid import grid_all_rows, select_rows_by_billno, clear_selection, selected_count
from kd_combo import fill_combo, fill_date
from kd_save import save_and_verify
from kd_guard import SafetyGuard, assert_allowed        # ★ 红线闸门

with sync_playwright() as p:
    br = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    page = br.contexts[0].pages[0]
    kq = KdQuery(page)

    # ★ 先立规矩：任何"删除凭证"路径在入口就被拦（与 config / --yes 无关）
    assert_allowed("delete_voucher")
    guard = SafetyGuard(cfg.get("safety", {}))

    # 只读核对（零副作用）
    print(kq.ar_head("AR105-202609-0005"))
    print("子单数 =", len(kq.ar_children("AR105-202609-0005")))     # 期望 0

    # 筛单 + 勾选
    filter_by_bill_nos(page, ["XSCKD-105-202609-0121"])
    print("列表行数 =", len(grid_all_rows(page)))
    clear_selection(page)
    print(select_rows_by_billno(page, ["XSCKD-105-202609-0121"]))

    # 填字段 + 保存（带判定）
    fill_date(page, "FDATE", "2026-09-30")
    fill_combo(page, "F_PAEZ_COMBO", "姚玉平")
    print(save_and_verify(page, kq=kq, form_id="IV_SALESIC"))

    guard.check("submit")            # A 类：看 config 开关（默认 false 会抛）
```

## 自测（不需要浏览器）

```bash
python digest_rule.py      # 摘要口径：x/y/z/s + 文本
python discount_rule.py    # 扣款分摊 + 尾差落最大行
python kd_guard.py         # ★ 红线：全开关打开也拦得住"删除凭证"
```

**`kd_digest_via_gl.py` —— 「凭证生成报告列表」已被清场关掉时，走「凭证查询」补摘要**
```bash
python kd_digest_via_gl.py --vchno 155 --digest "某公司销售水性油墨14527.49公斤"        # 先不保存，看回读
python kd_digest_via_gl.py --vchno 155 --digest "…" --go                                # 回读一致才保存
```
什么时候用：凭证**已生成**但**摘要没写**，而 `write_digest` 报
`没有「凭证生成报告列表」标签`。原因见 SF-50 —— 那张列表是**瞬时单槽**的
（只留最近一次生成、**搜不到、关了打不开**）。
本脚本改走：**凭证查询 → 定位 凭证字+号 → 双击行 → 「凭证 - 修改」 → 双击摘要格 → 保存**。
三个硬约束：
- **要留 ≥2 个标签空位**（凭证查询 + 凭证 - 修改），否则撞 SF-51 的「上限15个」。
- 目标行常在**非首屏**（200 行/页）；翻页靠 `kendoKDGrid.dataSource.page(n)`，别找分页按钮。
- 列表**虚拟滚动** → 双击前必须 `scrollIntoView`；坐标仍在视口外就**放弃**（别硬点）。
- 凭证需处于**暂存(A)**；已提交(B)/已审核(C) 时摘要格带 `k-gridCellstate-disabled`，脚本会**报错停下**。

## 注意事项

- 这些函数都**把 page 当参数传进来**，不自己建连接 —— 方便你塞进已有的循环/批量脚本。
- ★ **凡涉及"删除/作废"的路径，入口一律先 `assert_allowed(...)` / `assert_click_safe(...)`** ——
  这类动作通常无校验、不报错，靠人眼看日志发现不了。
- **没有**做成一个"大而全的框架"：因为金蝶页面差异大，过度抽象反而更难调。
  每个文件都是**可以整段拎出来看懂**的粒度。
- ⚠️ 所有定位都基于**金蝶 8.2 / 恒星版**实测的 DOM 结构与 id 后缀；
  换大版本前先用 `probe`（现场 DOM 探测器）复核。


## ★★ SF-55：写「日期/单头字段」必须**只认前台表单 + 写后回读**（2026-10-03）

**症状**：应收单的**业务日期 + 到期日落成"今天"**（而不是传入的 `--biz-date`），
单号年月跟着错成 `-202610-`；而日志明明白白写着
`填写 FDATE = 2026-09-30   [kendoDatePicker]`、API 也返回了新值。
13 家批量里**恰好 7 家中招**（其余 6 家正确）——**同一批、同一份代码、结果 mixed**。

**根因**：`[id$="-FDATE-EDITOR_c"]` + `filter(vis)[0]` 是**弱可见性**。
金蝶 SPA 把**后台标签页的表单 DOM 也留着**（实测同时存在 **11 个「应收单 - 新增」**），
这些控件尺寸/display/visibility 全 OK ⇒ 通过弱判定 ⇒ `[0]` 命中
**DOM 里最老的那张（孤儿草稿）**。
⇒ 写**真的写进去了**，只是**写在了别的表单上**；当前这张单的值仍是默认值。
**表现是随机的**，这最能骗人。

**实测（同一张草稿，见 `references/silent-failures.md` SF-55）**
| 写法 | 结果 |
|---|---|
| `kendoDatePicker.value()` + `trigger('change')` | ✅ 生效，到期日 `FENDDATE_H` 联动 |
| 同上 + 真实鼠标 blur | ✅ 生效 |
| **真实鼠标点击 + `keyboard.type('2026/9/30')`** | ❌ **无效**：掩码框得到 `'20260930'`，widget=None |

⇒ **日期字段绝不要用键盘打字兜底**；API 路径没问题，**问题在"写给谁"**。

**正确用法**
```python
from kd_combo import fill_date, read_field

fill_date(page, "FDATE", "2026-09-30", linked="FENDDATE_H")   # ★ linked = 联动字段，硬判据
print(read_field(page, "FDATE"))            # {'val':'2026/9/30','widget':'2026-09-30','cand':3,'onTop':True,...}
```

★★ **`linked=` 是关键，别省**（2026-10-03 重推 7 家实测，**7/7 完全相关**）：
读**同一个控件自身**的值是**自证** —— 写端和读端犯同一个错时完全失效。
本批 2 家 `widget` 回读明明是 `2026-09-30`，保存后服务端却是 `2026-10-03`；
而**到期日有没有跟着联动**100% 预测成败：

| 到期日回读 | 服务端 `FDate` | 结果 |
|---|---|---|
| 跟着变（2026-09-30） | 2026-09-30 | ✅ 单号 `-202609-` |
| **没跟**（保持 2026/8/31 或今天） | **2026-10-03** | ✗ 单号 `-202610-` |

⇒ **回读要读"能反证的东西"**：由它派生出来的独立数据（联动字段 / 服务端 / 下游单据）。
- `read_field()` 返回的 **`cand`**（同名控件个数）是判断"是否踩了 SF-55"的关键指标：
  `cand > 1` 就说明 DOM 里确实有别的表单同名控件。
- 更稳的兜底：**保存后用 `kd_query` 回读**（`AR_receivable` 的 `FDate` / `FENDDATE_H`
  都**可以直接查询**）。界面回显与日志都不算数，**服务端才算数**。

**配套的两个只读检查**（重推/补单前必做）
1. `ar_by_source_bill(出库单号)` 必须为空 —— 否则会推重；
2. 出库单 `FBILLALLAMOUNT` 合计 ≈ 目标价税 + 折扣额 —— 防止拿错单。

**顺带记住**：`AR105-YYYYMM-####` 的 `YYYYMM` 取自**业务日期**（不是创建时间）。
实测 `AR105-202609-0092..0097` 的 `FDate` 全是 `2026-09-30`，而 `FCreateDate` 是 `2026-10-03`。
⇒ **单号月份不对，先回查业务日期。**
