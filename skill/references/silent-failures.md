# 静默失败全记录（现象 → 根因 → 判据 → 修法）

> 金蝶 8.x 网页端 RPA 的**头号风险不是报错，而是"不报错但做错事"**：
> 脚本日志全绿、截图正常、金额看着对，数据却没落库 / 落到别的行 / 落到隐藏页面。
> 本文件是逐条实测档案。触发症状时**先来这里按现象检索**。
> 编号 `SF-NN` 被 SKILL.md 速查表引用。

## ★★ 先读：红线（不是"静默失败"，是**绝对禁止**）

**不能删除凭证。** 它与下面 46 条不同：那些是"想做的事做错了"，这条是"根本不该做"。

删除凭证恰好**同时命中本文件的全部特征**——没有必录校验、不弹错、kdsvc 正常返回、
截图一切正常——所以它比任何一个 SF 都更危险：**脚本会把"删掉凭证"当成一次普通而成功的操作。**

| | |
|---|---|
| 后果 | 凭证是「单据 → 专票 → 凭证」的终态与唯一账务留痕；删掉后来源单据与总账的对应关系断裂，总账/报表/税务口径同时失真 |
| 可逆性 | **不可逆**。金蝶**无回收站**，只能回滚数据库备份 |
| 为什么脚本特别容易犯 | 删除动作**无校验、无弹窗、返回成功**；列表工具栏的「删除」与「刷新」位置接近；批量脚本循环里误点一次就没了 |
| ✅ 正确做法 | 改摘要/分录 → 「凭证 - 修改」页改完保存；想留痕不进账 → **作废**（作废留痕，删除不留）；真要重来 → 先**人工**确认再走"反审核 → 修改" |
| 代码拦截 | `scripts/kd_guard.py` 的 `assert_allowed("delete_voucher")` / `SafetyGuard.check("delete_voucher")`、项目侧 `common.Guard.FORBIDDEN` —— **永远抛 `GuardError`，与 config 开关和 `--yes` 无关** |

```python
assert_allowed("delete_voucher")   # ★ 无条件抛
assert_click_safe(menu_text)       # 点菜单前体检，命中「删除凭证/作废凭证/凭证删除」即抛
```

> 详见 SKILL.md **§〇 红线**。`assert_click_safe` 只拦精确危险词，**不拦单独的「删除」**
> （过滤对话框的「删除行 / 全部删除」是合法操作）。

## 速查总表

| ID | 现象（你会看到的） | 一行根因 | 类别 |
|---|---|---|---|
| SF-01 | 点「保存/提交」后界面毫无变化，截图也看不出 | 点到了**隐藏页面**的同名按钮 | 定位 |
| SF-02 | 保存不报错、截图正常，但单号恒空、查询查不到 | 保存弹**必录校验**框，脚本没看弹窗 | 判据 |
| SF-03 | 提交后不知道到底成没成 | 只有 **kdsvc 服务端原文**是真相 | 判据 |
| SF-04 | 下拉打不开 / 选完下次又变回默认 | Kendo 下拉**只认 API 或真实输入** | 控件 |
| SF-05 | 4 张单只筛出 3 张，日志每行都写"成功" | 过滤**末行条件被静默丢弃** | 过滤 |
| SF-06 | 勾选行了，页面"真实选中 0 行" | 行级复选框必须**真实鼠标** | 勾选 |
| SF-07 | 本来只勾 1 行，一点表头变 555 行全选 | 表头复选框是**三态**语义 | 勾选 |
| SF-08 | 点「新增行」返回成功，条件行数永远 0 | `li.k-link` 的 `el.click()` **无效** | 过滤 |
| SF-09 | 复核人下拉点不开，或用 API 写入无效 | kdcombo 必须**真实鼠标 + scrollIntoView** | 控件 |
| SF-10 | 明明 0 行却拿到 `None`，判成"找不到窗口"死等超时 | `eval_all_frames` **只返回真值**，`0` 被丢弃 | 工程 |
| SF-11 | 对话框明明开着，却读不到里面的条件行 | 按 `.k-window[0]` 命中了**隐藏旧窗口** | 定位 |
| SF-12 | 报告"0 错误生成成功"，其实一张都没生成 | 读到了**弹窗背后**的列表 | 判据 |
| SF-13 | 报告行数恒 0 | 没 `activate_tab`，读到背后列表 | 标签 |
| SF-14 | 改完一个格，相邻联动列读出来是空 | 焦点还在编辑态，联动值**未回填** | 明细 |
| SF-15 | 点关闭标签没反应 | 确认框不是 `.k-window`，是**内嵌警告条** | 关闭 |
| SF-16 | 点未激活标签的 ✕ 没反应，反复点原地打转 | 点 ✕ 只会**切换过去**，不关闭 | 标签 |
| SF-17 | 搜索框打了字却搜不出东西 | 搜索**只在面板打开那一瞬间**发起 | 搜索 |
| SF-18 | `FSrcBillNo = 'AR105-...'` 报类型转换错 / 0 行 | `FSrcBillNo` 存的是**FID 数字** | 查询 |
| SF-19 | `FCreateDate > '2026-09-21T20:00'` 报日期转换错 | 过滤串必须是 `'YYYY-MM-DD HH:MM:SS'` | 查询 |
| SF-20 | 46 行明细只查出 1 行 | 头字段+明细字段混放被**去重成单据头条数** | 查询 |
| SF-21 | 某行某格为空，整行数据**整体前移错位** | 读列表用了 `filter(Boolean)` | 读表 |
| SF-22 | 下推对话框选了规则，实际用了默认值 | 下拉**只活在同一次 evaluate** | 下推 |
| SF-23 | 生成凭证报"该业务单据正在被 XXX 使用" | 源单据的「- 修改」标签还开着 | 凭证 |
| SF-24 | 列表 1067 行，DOM 里只有 200 行 | **虚拟滚动**，必须读数据源 | 读表 |
| SF-25 | `[id$="_row_Add_PPART_VALUE_c"]` 查不到元素 | 大小写混用，要转小写匹配 | 定位 |
| SF-26 | 弹窗「确定」点了不关 | 必须**真实鼠标**点坐标 | 弹窗 |
| SF-27 | 直接改日期 input 的 value，提交后日期没变 | 必须走 **Kendo DatePicker API** | 控件 |
| SF-28 | 保存后列表看不到刚存的单 | 列表**不会自动刷新** | 流程 |
| SF-29 | 同名标签两个，操作落到了错的那个 | 必须按**序号**定位 | 标签 |
| SF-30 | `GL_VOUCHER.FBillNo` 查到 `205495`，对不上「转 66」 | 服务端 `FBillNo` 是**内部 FID**，不是凭证字号 | 查询 |
| SF-31 | 点击全被拦，提示某元素 `intercepts pointer events` | 账套里**别人**残留的「处理结果」失败弹窗压在最上层 | 环境 |
| SF-32 | 搜索结果条目 `locator.click()` 超时，报折叠容器拦截 | `.kd-form-collapse-container` 拦截 → **真实鼠标** | 搜索 |
| SF-33 | 单据号从 `-0001` 跳到 `-0003`，以为丢了一张 | 同账套**别的用户/组织**在并发跑单，正常跳号 | 环境 |
| SF-34 | 截图报 `Page.screenshot: Timeout 30000ms`，每次卡满 30 秒 | SPA 常驻动画 + DOM 累积 → 等不到"稳定"；**必须显式 timeout + animations=disabled + CDP 兜底** | 性能 |
| SF-35 | 保存后「(单号未读到)」，下一步直接 RuntimeError 断链 | `[id$="-FBillNo-EDITOR_c"]` 常不存在/不可见 → 用**只读查询反查**（来源单号 > 时间窗） | 流程 |
| SF-36 | 清场明明跑了，凭证仍报「该业务单据正在被 XXX 使用」 | SPA 隐藏标签能堆几十个，`max_iter` 被耗尽 → **优先关「- 新增/- 修改」占单标签**，`max_iter` 提到 80 | 清场 |
| SF-37 | 打开某列表时搜索结果点不动（`kd-form-collapse-container` 拦截） | **上一步刚开的单据标签**盖住了搜索面板 → 先 `close_all_tabs` 清占单标签再打开 | 搜索 |
| SF-38 | 客户上月已推过，本月按日期区间又推了一遍 | **日期区间模式没有 missing/extra 校验** → 能用 `--bill-nos` 就不用日期筛；先按**创建时间**查重 | 流程 |
| SF-39 | 表里填 8/26~9/25，该客户一张出库单都没筛到 | 出库单日期在**每月25日/月底**，区间首尾各差一天 → 用金额对拍找出该用的那张单 | 流程 |
| SF-40 | 标签越堆越多，最后整链报「找不到可见的列表网格」 | **标签累积**挤爆 tabstrip（宽 1156px 只装得下约 10 个）→ 坐标错位、`activate_tab` 点错页。**正常开页是"替换"不是"新增"** → 预防=**每轮开跑前先清场** | 标签 |
| SF-41 | 刷新金蝶页面后要重新登录 | **`reload()` = 登出**（SPA 不保会话）→ **绝不要刷新用户的页面** | ★红线 |
| SF-42 | 日志说"清场关掉 79 个"，但标签一个没少 | `closed.append` **无条件**记录 → 数字是"尝试次数"；且 `mouse.click(旧坐标)` 点不中（hover 漂移） | 标签 |
| SF-43 | 下拉选值：**日志说选上了、回读却是空** | Kendo 下拉**展开动画未收敛**就取坐标点击 → 点到别处。**轮询到 `elementFromPoint` 命中才点** + 外层重试 | 控件 |
| SF-44 | 单据已是半成品，重跑 push_chain 会**推重** | 改用 `act_invoice` / `gy3_4_voucher` / `write_digest` 单段续跑；手工补做的单**必须先写断点文件** | 流程 |
| SF-45 | 控件点了没反应、回读始终为空（坐标也一样） | **`vis()` 把后台标签页里的同名字段判成"可见"** → 点的是后台控件/后台下拉项。改用 `svis` + `onTop` + 按控件位置限定下拉容器 | 控件 |
| SF-46 | 单次截图要 88 秒；批量越跑越慢 | `page.screenshot(timeout=…)` 的 **`timeout` 管不住"等字体/停动画"** → **改成 CDP 直取优先**。另：**批量运行期间别用别的脚本连同一浏览器做探针**（CDP 串行） | 性能 |
| SF-47 | 续跑半成品报**「找不到下拉控件 F_PAEZ_COMBO（或不可见）」** | `svis()` 里"必须在视口内"把**窗口矮时落在折叠下方的真控件**误杀（视口 1366×641，控件 y=701）→ `svis` 只管"渲染出来没"，视口单列 `inView`；开下拉前 `scrollIntoView` | 控件 |
| SF-48 | 一次截图**卡 9 分钟** | CDP `Page.captureScreenshot` 要**等渲染器出一帧**，窗口最小化/被遮挡就永远等；**CDP send 无 timeout** → `KD_NO_SHOT=1` 整段关截图 | 性能 |
| SF-49 | 批量**第一家就挂、家家都挂**（`intercepts pointer events`） | 三环死锁：孤儿草稿页 → 判重 `close_all_tabs(keep_names=[])` **把锚点也清掉** → 回搜索被**残留浮动提示条**压住 → 判重显式保锚点 + `dismiss_floating_hints()` + JS `el.click()` 兜底 | 清场/搜索 |
| SF-50 | 凭证生成了却**写不上摘要**，且再也回不去 | **「凭证生成报告列表」是瞬时单槽**（只留最近一次生成），**搜不到、关了不可打开** → `KEEP_TABS` 必须含它，且**摘要要在同一轮紧跟生成写完**；补救走 SF-52 | 流程 |
| SF-51 | 开新功能页报**「超出了参数控制打开页签数的上限15个」** | SPA 标签数达 15 就拒开（CDP 只看得到 1 个 page）；**10+ 个孤儿「应收单 - 新增」关不掉**占位 → 先 `clean_tabs` 腾位，留 ≥2 空位再开工 | 标签 |
| SF-52 | 「凭证查询」里定位到目标行、**双击毫无反应** | 列表 **200 行/页 + 虚拟滚动**（979 行 5 页），目标行 `<tr>` 在 DOM 但 **y 在视口外** → 双击前必须 `scrollIntoView` 再取坐标；翻页用 `kendoKDGrid.dataSource.page(n)` | 列表/坐标 |
| SF-53 | 「业务凭证查询」能搜到、也有「查看总账凭证」，但**永远 0 行** | 默认过滤的**账簿值是未选占位**（渲染成 `select 105`）→ 等于没选账簿。别走这条路，改用 SF-52 | 过滤 |
| SF-54 | **「写入」报成功，其实根本没生效**（明细折扣末行 / 对齐锚点行） | `detail_set_cell`（双击→全选→输入→回车）**全程零校验**：坐标算得出、点击无异常、回车无异常 → 一律当成功。行复用 / 联动重算 / `detail_blur()` **把网格从底部滚回顶部**，都能让编辑器没走完 commit ⇒ **凡"写"必"回读"**；对齐循环**第一轮无进展就该早停抛错**，别空转 4 轮才 WARN | 写入/校验 |
| SF-61 | 日志写「打开功能（真实鼠标 996,107）：应收单列表」，**标签数却只有 1** → 随后「按单号过滤 → 0 行 / 找不到可见的列表网格」 | **开功能页只点了、没开**，代码仍 `return True`，后续过滤/勾选全部落在**上一个列表**上（河南华洋 2026-10-29：在出库单列表上过滤 AR 单号）。★ 实测**不是**配额满 15（同批 `items()` 只有 3~6），是开页动作没生效（弹窗/浮层拦点击）；而日志只打可见 `tabs()` 不打 `items()` 配额，导致误判 ⇒ 开完**必须校验目标标签真的出现**，没出现就 `dismiss_quota_dialog()` + `release_tab_quota()` 重试 ≤2 次，仍无**直接抛错**；开页前另做**主动配额闸门**（≥10 先释放），且日志**两个数一起打** | 标签/开页 |

---

## 详解

### SF-01 隐藏页面的同名按钮吃掉点击 ★★
- **现象**：点「保存」后界面毫无变化；截图看不出异常；换到服务端返回才发现"根本没发请求"。
- **根因**：SPA 把开过的页面 DOM 全留在同一个 document 里，`保存/提交/审核` 在所有 pageId 的工具栏里文本完全一样。
  只按文本取第一个 → 点到**隐藏页面**的按钮。（曾因此白排查 1 小时）
- **判据**：`vis(e) && e.offsetParent !== null`；并优先单据工具栏（避免点到筛选栏的「保存方案」）。
- **修法**：
```js
const cands = [...document.querySelectorAll('.kdmenuitem-content')]
    .filter(e => e.textContent.trim() === t && vis(e) && e.offsetParent !== null);
const btn = cands.find(e => e.id.includes('BILLMENU_TOOLBAR')) || cands[0];
```
点击用 Playwright **真实鼠标**：`page.locator(f'[id="{btn.id}"]').click()`；JS `el.click()` 只作兜底。

### SF-02 保存"成功"其实是假的 ★★
- **现象**：`save_bill()` 不抛异常、截图正常，但**界面 `FBILLNO` 恒空**、`ExecuteBillQuery` 查不到这张单。
- **根因**：保存时弹「字段必录提示」对话框（实测 `共有1处必录项未填 / [开票信息.复核人] 是必填项，不能为空！`），
  脚本没检查弹窗就当成成功。
- **判据优先级**：**查询回读（权威） > 界面 FBILLNO 非空 > 无弹窗**。只信"没抛异常"＝一定会踩。
- **修法**：保存后立刻读一切可见弹窗/提示：
```js
Array.from(document.querySelectorAll('.k-window')).filter(vis).map(e => e.innerText)
```
有内容即失败；再用 `KdQuery` 回读确认落库。

### SF-03 唯一可靠的成败判据：kdsvc ★
- **现象**：金蝶「保存成功」提示一闪而过，界面可能毫无变化，截图抓不到。
- **根因**：所有动作都 POST 到同一个 `...DynamicFormService.Call.common.kdsvc`，
  请求/响应是 gzip+base64(+urlencode)。响应解码后是 JSON 数组，真正的结果在 `showConfirm` / `ShowWarnningMsg` 里。
- **修法**：监听 response，解码后按判据词判定（成功/已提交/已审核 → ok；失败/不能为空/不允许/校验不通过 → bad）。
  代码见 SKILL.md §四。
- **⚠️ 例外**：专票提交时 kdsvc **有时抓不到消息**（`messages()` 为空），
  此时**允许以界面为准**（提示条「…审核成功！」+ 单据状态=已审核）。双判定要留这条路。

### SF-04 Kendo 下拉必须走 API（或真实输入）
- **现象**：合成 `MouseEvent` 点 `.k-select` 打不开下拉；或用真实鼠标点开了，下次调用又失效。
- **修法**：
```js
const inp = box.querySelector('input[data-role=dropdownlist]');
const w = window.jQuery(inp).data('kendoDropDownList');
const idx = w.dataSource.view().findIndex(d => (d.name || d.value) === want);
w.select(idx); w.trigger('change');
```
- ⚠️ 切换 radio / 触发联动后下拉是**异步重建**的，等 ~2.5–3s 再读 `dataSource.view()`，否则拿到空列表。
- ⚠️ 同一字段常有**一份可见 + 一份隐藏**控件（旧页面残留），先按可见性挑。
- ⚠️ 例外：kdcombo 没有 kendo 实例 → 见 SF-09。

### SF-05 过滤条件末行被静默丢弃 ★★
- **现象**：4 张单只筛出 3 张；日志里每行都显示"设置成功"。
- **根因**：金蝶在 input 事件后**异步**把条件 commit 进内部模型。最后一行写完值**立刻点确定** → 那一行丢失。
  （前面的行没事，只因后面还有操作顺带等到了时间。另外底部常驻空白行会让末行条件变成 `AND (空白)` 被吃掉。）
- **判据**：`_read_condition_rows()` 回读自检 + 筛选后**硬校验单号集合**（期望的一张不少、也不能多出别的单）。
- **修法**：每一行都设「或者」（含最后一行）；新增行后等 450ms；点确定前再等一次。
- ⚠️ 回读时值输入框 id 是全大写 `_ROW_ADD_PPART_VALUE_c`，要按 `ppart_value` 转小写找（SF-25）。

### SF-06 行级复选框必须真实鼠标 ★★
- **现象**：派发了 click 事件，页面「已选 N 行」仍是 0，且不报错。
- **根因**：行的复选框是 `<input type="checkbox" onclick="return false;" class="k-checkbox gridCheck gridCheckCell">`，
  金蝶在**祖先容器**做事件委托，`onclick="return false"` 又挡了默认切换 → 合成 MouseEvent 无效。
- 另有隐藏坑：老实现扫 `document.querySelectorAll('tbody tr')` 会命中**隐藏页面**的列表，
  点了 46 个全在隐藏表格里 → 页面上"真实选中 0"。
- **修法**：`page.mouse.click(x, y)` 点**可见**行复选框中心；真实选中数读分页栏「已选 N 行」。

### SF-07 表头复选框是三态语义 ★
- **现象**：「本来只勾了 1 行」被一次 `clear_selection()` 点成 **565 行全选**，后续勾选状态全乱、可能推错单据。
- **根因**：`未选/部分选中` 点一下变「**全选**」，全选点一下才「全清」；表头状态与真实选中数还会不同步
  （实测首屏曾残留 555 行选中，可能要点两次才归零）。
- **修法**：**循环点表头并读 `selected_count()` 直到归零**；为 0 时别点（点了反而全选）。

### SF-08 过滤对话框「新增行/全部删除」是 `li.k-link`
- **现象**：`el.click()` 返回 true、不报错，但条件行数永远是 0 → 死等超时。
```html
<li class="k-link kdFilterAdd">新增行</li>      <!-- 真实鼠标点坐标才生效 -->
```
- **修法**：JS 取坐标 → `page.mouse.click`。实测 `0→1 立即生效`。
- ⚠️「新增行」是异步渲染的：点完要**轮询等到行数 > 目标行号**，不能只靠固定 sleep。

### SF-09 kdcombo（复核人）必须真实鼠标 ★★
- **现象**：`widget.open()` / `el.click()` 全部无效；或点开失败、下拉根本不出现。
- **根因**：`F_PAEZ_COMBO` 控件 id 是 `-F_PAEZ_COMBO-EDITOR`（**没有** `_c`），
  内部**没有** `input[data-role="dropdownlist"]`、也拿不到 kendo 实例。
- **修法**：
```
① JS scrollIntoView({block:'center'}) 把控件滚进视口
② page.mouse.click(控件中心) 展开
③ JS 取目标 li 的 getBoundingClientRect 中心 → page.mouse.click 选中
④ read_combo() 回读校验（读 .k-input 的 textContent）
```
- ⚠️ **不 scrollIntoView 会直接失败且很难查**：控件 y=711 而视口高只有 641，
  `document.elementFromPoint(cx, cy)` 返回 `null`（点在视口外），点击落到窗口外。
- **判据**：`elementFromPoint` 返回 null / `!box.contains(top)` ⇒ 被遮挡或不可见，**重试而不是硬点**。

### SF-10 `eval_all_frames` 只返回「真值」 ★★
- **现象**：对话框里明明是 0 行（正常初始态），却拿到 `None` → 判成 -1 → 死等 12 秒后报错。
- **根因**：实现是 `for fr: r = fr.evaluate(...); if r: return r`，最后 `return None`。
  **`0` / `''` / `false` 都是假值** → 被丢弃，继续试下一个 frame，最终 `None`。
- **修法**：**凡要取值（尤其可能为 0 的计数）的 JS，一律返回对象**：`return {n: count}` / `return {ok:true, ...}`。

### SF-11 过滤对话框定位必须按标题文本
- **现象**：对话框明明开着，`querySelectorAll('tr')` 却为 0 → 误判"新增行没生效"。
- **根因**：SPA 把关掉的窗口留在 DOM 里（实测同时挂着 3 个过滤窗口：2 个隐藏的 + 1 个可见的），
  `.k-window` 的**顺序会变** → 拿到隐藏窗口。
- **修法**：
```js
const win = [...document.querySelectorAll('.k-window')].filter(vis)
    .find(w => (w.innerText || '').includes('列表过滤'))
    || [...document.querySelectorAll('.k-window')].filter(vis).find(w => w.querySelector('li.kdFilterAdd'));
```
- 另：**专票列表的过滤对话框默认没有任何条件行**（"暂无新增条件数据"），
  出库单/应收单的自带一行空白行 → 专票必须显式点「新增行」。

### SF-12 报告"0 错误生成成功"其实一张都没生成 ★★
- **现象**：以为生成成功，实际被「**所选单据未全部审核!**」拦住了。
- **根因**：拦截时**不产生报告**，此时去读"第一个可见 grid"，读到的是**弹窗背后的专票列表**——
  字段里同样有 `单据编号 / 价税合计 / 客户 / 单据状态`，极像报告行 → 得出错误结论。
- **修法**：生成后**先在对话框内部**判断：`txt.includes('未全部审核')` / `hasGrid` / 有无「确定」按钮；
  再 `activate_tab("凭证生成报告列表")` 之后才读报告。

### SF-13 报告行数恒 0（没切标签）
- **根因**：`read_voucher_report()` 读的是"第一个可见 grid"，不先切标签就读到背后的列表。
- **修法**：凭证生成成功后**会新开标签**「凭证生成报告列表」→ 先 `activate_tab(...)` 再读。
- ⚠️ `activate_tab()` 是**包含匹配**：`activate_tab("销售增值税专用发票")` 会命中「… - 修改」页。

### SF-14 明细改完一格，联动列读出来是空
- **现象**：改折扣额后「税额」格空白 → 读到假空。
- **根因**：焦点还在编辑态；重算是**服务端异步**的。
- **修法**：Enter 后等 **1.5~2.5s**，并**点同行的另一个非编辑列（如「物料名称」）移开焦点**再读值。

### SF-15 关闭标签的确认框不是 `.k-window`
- **现象**：点了 ✕ 没反应，或找不到"是否保存"的对话框。
- **根因**：确认框是**内嵌警告条** `.kd-el-popmessage.kd-message-question`（id 尾 `-POPFLOATEMESSAGE`），
  文案两种：`内容已经修改，是否保存？` / `关闭页签会丢失尚未保存的下推生成数据。想要保存单据吗？`
  按钮是 `<button class="k-button">`：**是 / 否 / 取消** → 丢弃就点「否」。
  用 `.k-window` 找会一无所获（这是"关闭没反应"的真因）。

### SF-16 点未激活标签的 ✕ 只会切换
- **现象**：点 ✕ "没反应"，反复点原地打转。
- **修法**：`点标签本体(tx,ty) → 等 1.5s → 重新取标签列表（坐标会变）→ 点该标签的 cx,cy`。
- 关一个位置就变 → **每轮重新取标签列表**，先关非激活的（从右往左），非保留的激活标签最后关，关完把保留标签切回前台。

### SF-17 顶部搜索：在输入框打字不触发搜索 ★
- **现象**：`type()` 期间 **0 个网络请求**，面板一直显示「最近搜索历史」；按 Enter 只是关掉面板。
- **根因**：搜索**只在「面板被打开的那一瞬间」发起**，用的是输入框里已有的值。
- **修法**：
```
Esc 关面板 → 点图标开面板 → 清空 + type(关键字) → ★Esc 关掉 → 再点图标重开（这一步才真去搜）
→ .kd-fullsearch-item-container → 点中目标
```
- 重名必须用**菜单路径**消歧（实测 `销售出库单列表` 有 3 条，正确的是「供应链>销售管理>出货处理」）；
  没提示且多条同名结果时**直接报错**，不许猜。

### SF-18 `FSrcBillNo` 存的是 FID 数字
- `FSrcBillNo like 'AR105-202609-%'` 永远 0 行；`FSrcBillNo = 'AR105-202609-0005'` 报
  `Conversion failed … to data type int`。
- **修法**：查来源关系先用 `AR_receivable.FID` 换出数字，或直接按客户/创建时间筛。

### SF-19 `FCreateDate` 过滤串格式
- 必须是 `'YYYY-MM-DD HH:MM:SS'`；写 `'2026-09-21T20:00'`（带 T）会报
  `Conversion failed when converting date and/or time from character string`。

### SF-20 头字段 + 明细字段混放会被去重
- 输出字段里混入"单据头"和"单据体（明细）"字段时，返回行数被去重成"单据头条数"
  （46 行明细只回 1 行）。**要查明细就只放明细字段**。
- 另：`IV_SALESIC` 的 `FAllAmount / FNotaxAmount / FTaxAmount` 是**明细级**字段，
  与单头字段混在一条 FieldKeys 里会串行 → 单头只查单头字段，金额单独查明细再求和。

### SF-21 读列表必须「列严格对齐」
- `td.innerText` 数组**不要** `filter(Boolean)` —— 只要某行有一格为空，整行就往前错位。
  （发票列表的「发票号」常年为空，会把「发票日期」读成「开票方式」的值。）
- **修法**：用表头 `th` 与 `td` 同下标配对，空列保留为 `''`。

### SF-22 下推对话框的下拉只活在同一次 evaluate
- 打开下拉、选 li、点确定必须在**同一个 JS**里做完；否则下拉已关闭 → **静默用默认值**
  （默认往往是"不合并"）。

### SF-23 生成凭证报"正在被 XXX 使用"
- 报错原文：`该业务单据正在被蒋一天使用，本次不生成凭证`。
- **根因**：「应收单 - 新增」「销售增值税专用发票 - 修改」等标签仍开着，把单据锁住了。
- **修法**：生成前 `close_all_tabs(keep_names=["销售出库单列表"])` 或至少关掉源单据的详情/修改页。

### SF-24 虚拟滚动：DOM 只有 ~200 行
- 1067 行的列表，`tbody tr` 只渲染前 200。**要拿全量必须读数据源**：
```js
const g = jQuery(gridEl).data('kendoKDGrid') || jQuery(gridEl).data('kendoGrid');
const rows = g.dataSource.data().map(d => d.toJSON ? d.toJSON() : d);   // 全量
```
数据源字段名 ≠ 表头文案（出库单列表实测：`FBILLNO/FCUSTOMERID_FNAME/FDATE1/FREALQTY/FARJOINQTY`）。

### SF-25 大小写混用
- 条件行值输入框 id 是全大写 `_ROW_ADD_PPART_VALUE_c`，用 `[id$="_row_Add_PPART_VALUE_c"]` **命不中**。
- **修法**：`[...document.querySelectorAll('[id]')].filter(e => e.id.toLowerCase().includes('ppart_value'))`。
- 定位条件行值格**先按 id 含 `ppart_value`**；按 `[data-role=...]` 找会先命中「字段列」的 combobox → 值写不进去、过滤静默失效。

### SF-26 弹窗「确定」必须真实鼠标 ★
- 合成 `el.dispatchEvent(new MouseEvent('click'))` **完全不触发** —— 对话框点了不关（下推对话框实测）。
- **修法**：`page.mouse.click(x, y)`（先算按钮中心坐标）；用对话框 innerText 关键字限定
  （如 '整单转换' / '销售专用发票'）防点到隐藏页同名按钮。

### SF-27 日期必须走 Kendo DatePicker API
- 直接改 input 的 `value` **不同步模型**，提交后日期没变。
```js
jQuery(el).data('kendoDatePicker').value(new Date('2026/9/30')); .trigger('change');
```

### SF-28 列表不会自动刷新刚保存的单据
- 存档后要 `click_menu("刷新")` 才能看到。

### SF-29 同名标签必须按序号定位
- 两张都叫「应收单 - 新增」时，`activate_tab(名字)` 只命中第一个。
- **修法**：`tabs()[i]` → `tx/ty` 点击 → 重取 → 点 `cx/cy`。

### SF-30 `GL_VOUCHER.FBillNo` 是内部 FID，不是凭证字号 ★★
- **现象**：想核验「转 66」这张凭证，查 `GL_VOUCHER.FBillNo = '转66'` → 0 行；
  或反查发现 `FBillNo` 是 `205495` 这种纯数字，跟界面上的凭证字号对不上。
- **根因**：服务端 `FBillNo` 返回的是**单据内部 FID**（主键），凭证字号（转/付/收 + 序号）
  是另一个概念，只在**界面列表 / 凭证生成报告**里可见。
- **修法**：**按摘要原文 + 日期定位凭证**，比拿字号去猜更稳：
  ```python
  exp = build_digest(cust, x, y, s, omit_zero=True)      # 先重算摘要
  kq.rows("GL_VOUCHER",
          "FBillNo,FDate,FDocumentStatus,FEXPLANATION,FACCOUNTID.FName,FDEBIT,FCREDIT",
          f"FEXPLANATION = '{exp}' and FDATE >= '2026-09-29 00:00:00'")
  ```
  凭证字号「转 66 / 转 67」另外从**凭证生成报告**或凭证页读出来，用来给人看。
- **追加（2026-10-04 实测，重要）**：`FVOUCHERGROUPNO`（凭证号，如 `196`）**能查，但它在
  多个凭证字组下重复** —— 「转196 / 记196 / 收196 / 付196」… 都是 `196`。单按
  `FVOUCHERGROUPNO=196` 查，会命中**别的字组的无关凭证**（实测 196/197/198 各命中一堆
  「应付码头费 / 计提工资 / 结转损益」等别的账簿凭证，完全不是自己要的销售凭证）。
  正确姿势 = **凭证号 + 凭证字组 双字段**定位：
  ```python
  # 服务端字段是 FVOUCHERGROUPID.FName（点号访问），不是界面的 FVOUCHERGROUPID_FNAME 别名
  kq.rows("GL_VOUCHER",
          "FEXPLANATION,FVOUCHERGROUPNO,FVOUCHERGROUPID.FName,FDEBIT,FDATE",
          "FVOUCHERGROUPNO=196 and FVOUCHERGROUPID.FName='转'")
  ```
  或按「借方金额 `FDEBIT` + `FDATE`」定位后**挑摘要含客户名的那行**（同 SF-30 修法，权威）。

### SF-31 别人残留的「处理结果」弹窗把点击全吃掉 ★
- **现象**：脚本本身没报错，但所有真实鼠标点击都提示
  `<div class="…"> … intercepts pointer events`，或点了没反应。
- **根因**：同一账套里**另一个用户/另一个组织**在同时操作（实测撞到成都批次
  `AR109-*` / `SVINV0003366x~3368x`），失败后留下一个**「处理结果」弹窗**
  （内容如 `183306=XSCKD-101-202512-S0283`，不是自己的单）浮在最上层。
- **修法**：
  1. 先 dump 弹窗内容，**确认不是自己的单**（别把别人的失败当成自己的去补救）；
  2. `src/close_result_dlg.py` —— **真实鼠标**点 ✕ 关掉（合成 click 对 `.k-window` 无效，见 SF-26）；
  3. 关完再跑；跑完用 `ExecuteBillQuery` 回读**确认自己那批独立完整**。

### SF-32 搜索结果条目 `locator.click()` 被折叠容器拦掉
- **现象**：`page.get_by_text('销售出库单列表').click()` 超时，报
  `<div class="kd-form-collapse-container"> … intercepts pointer events`。
- **根因**：功能页外壳的折叠容器盖在结果条目上（DOM 里条目可见，但指针事件被上面那层接走）。
- **修法**：
  ```js
  // 1) 取坐标（先确认没在视口外，实测出现过 y=-32）
  el.scrollIntoView({block:'start'});
  const r = el.getBoundingClientRect();  // → {x: r.x+r.width/2, y: r.y+r.height/2}
  // 2) 命中检测：确认点上真的是目标条目
  const hit = document.elementFromPoint(cx, cy);
  // 3) page.mouse.click(cx, cy)   ← 真实鼠标
  ```
  另外：**目标列表已经开着就别再搜**（会开出第二个同名标签 → SF-29），
  搜之前先关掉占用焦点的同名标签（`src/gy_closetab.py`）。

### SF-33 单据号跳号 ≠ 丢单
- **现象**：本次生成的是 `AR105-202609-0001`，下一张直接 `-0003`，中间 `-0002` 不见了。
- **根因**：同账套**别的用户/组织**在并发跑单，号段是全账套共享的（见 SF-31）。
- **修法**：**不要按号段推断成败，也不要去补单**。以 `ExecuteBillQuery` 回读自己的单据为准。

### SF-34 截图每次都卡满 30 秒 ★★（单家白扔 1.5 分钟）
- **现象**：日志反复出现
  ```
  WARN 截图失败 xxx: Page.screenshot: Timeout 30000ms exceeded.
  Call log: - taking page screenshot / - waiting for fonts to load... / - fonts loaded
  ```
  注意 `fonts loaded` 已经打了，**说明不是字体问题**，但 Playwright 仍耗满默认 30s 超时。
- **根因**：金蝶 SPA 把**开过的页面 DOM 全留在 `document` 里**（标签越多节点越多），
  且页面上有常驻 CSS 动画/轮询 → `page.screenshot()` 截图前的「等字体 + 等渲染稳定」
  永远等不到稳定态。
- **代价**：一条整链里有 3 处截图（专票保存后 / 改摘要后 / 凭证保存后）
  → **3 × 30s = 90s 纯浪费**，2026-09-22 实测确认。
- **修法**（三招叠加，缺一不可）：
  ```python
  # ① 显式超时，别用默认 30s
  # ② animations 抑制常驻动画
  page.screenshot(path=..., timeout=5000, animations="disabled", caret="hide")
  # ③ 仍失败 → CDP 直取，完全不经过 Playwright 的等待逻辑
  sess = page.context.new_cdp_session(page)          # 建议缓存，别每次新建
  b64 = sess.send("Page.captureScreenshot", {"format": "png"})
  Path(fp).write_bytes(base64.b64decode(b64["data"]))
  ```
- **别把截图失败当致命错**：`except` 里 WARN + 跳过即可，不要中断主流程。

### SF-35 保存后读不到单号 → 整链断掉 ★★
- **现象**：`read_bill_fields(["FBillNo"])` 返回空 → 日志「(单号未读到)」→
  下一步要单号时直接 `RuntimeError`，只能人工另跑脚本查号再续跑（一次来回 1~2 分钟）。
- **根因**：读单号靠 `[id$="-FBillNo-EDITOR_c"]` 且**要求该元素可见**；
  保存后单号常不在这个控件里（渲染到标题区/工具栏），于是查不到。
- **修法**：读不到就用**只读查询反查**（零副作用），别让流程断：
  1. **优先精确关联**：应收单的明细字段 `FSourceBillNo` = 来源出库单号
     → 拿手里的出库单号反查应收单号（比"最近一条"可靠得多）
  2. **兜底时间窗**：`FCreateDate >= 三分钟前`（可选 `+ FCustomerId.FName = 客户`）倒序取第一条
  3. **命中必须 WARN 留痕**：多人共用账套时，"最近一张"可能拿到别人的单 —— 不静默
  见 `scripts/kd_query.py` 的 `ar_by_source_bill` / `latest_ar` / `latest_iv`。

### SF-36 清场被「隐藏标签」耗尽循环，漏关了真正占单的那张 ★★
- **现象**：`凭证生成失败 SVINV... [其他] 该业务单据正在被 XXX 使用，本次不生成凭证`。
  明明清场跑了、日志也显示"关掉 N 个标签"，但**占单的那张没关掉**。
- **根因**：金蝶 SPA 会把开过的页面 DOM 全留着，**隐藏标签可以堆到几十个**
  —— 实测界面 `tabs()` 只显示 5 个，`clean_tabs` 却关出了 **40 个**。
  而 `close_all_tabs` 的 `max_iter=40` 被这些隐藏标签**耗光**，轮到
  「销售增值税专用发票 - 修改」（正占着那张专票）时循环已经结束了。
- **两个关键事实**：
  1. **专票提交后标签名会从「XX - 新增」变成「XX - 修改」** —— 它一直占着这张单，
     清场必须认到它。
  2. 占单标签就那么几个，**列表类标签关不完不影响后续**。
- **修法**：`close_all_tabs` 里挑目标时**优先挑带「- 新增 / - 修改」的**，
  并把 `max_iter` 提到 80：
  ```python
  busy = [t for t in pool if any(k in (t.get("text") or "") for k in ("- 新增", "- 修改"))]
  if busy: pool = busy          # ★ 先关占单的，别被几十个隐藏标签耗尽
  ```

### SF-37 自己刚开的单据标签，会挡住功能搜索面板 ★
- **现象**：`open_menu_by_search("应收单列表")` 报
  `Locator.click: Timeout 6000ms` + `<div class="kd-form-collapse-container"> intercepts pointer events`
  （与 SF-32 同一层拦截，但**成因不同**）。
- **根因**：上一步刚建的**「应收单 - 新增」标签还开着**，它的 `.kd-form-collapse-container`
  盖在功能搜索面板上面 —— 于是搜索**结果条目点不动**（真实鼠标路径的命中检测也过不了）。
- **修法**：**打开新列表之前，先把已完成使命的占单标签关掉**：
  ```python
  closed = ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=True)
  ui.page.wait_for_timeout(800)
  ui.open_receivable_list()      # 这时搜索面板才不被挡
  ```
  单据此刻已保存并提交，标签留着没有价值；「销售出库单列表」要保留（下一组起点）。

### SF-38 按「日期区间」下推没有防重复保护 —— 会静默重推 ★★
- **现象**：某个客户上个月已经推过一遍，这个月按新的日期区间再筛，**区间里又把它捞出来**，
  于是**又推了一遍**，产生重复的应收单/专票/凭证。全程不报错、金额看着都对。
- **根因**：`filter_outstock()` 只在 **`--bill-nos` 模式**下做 `missing/extra` 校验
  （筛出的单与预期不符就 `SystemExit(3)` 停止）；
  **日期区间模式下 `want=None`，完全不校验**筛出来的是什么。
- **代价**：实测 2026-10-03 那一批 —— 表里 18 家，其中「广源包装集团」上一批已推过
  （`AR105-202609-0005`，价税 301,512.10），它的出库单仍在区间里；
  若按日期筛就会**原样再推一遍**。
- **判据/预防**（下推前必做）：
  1. **能用 `--bill-nos` 就绝不按日期筛**。先用 `ExecuteBillQuery` 把目标单号查出来，
     再精确指定 —— 这样才有 `missing/extra` 保护。
  2. 按**创建时间**（不是业务日期）查一遍目标客户近期有没有已生成的应收单：
     `FCustomerId.FName in (...) and FCreateDate >= '<两周前>'`。
     只看业务日期会漏判（业务日期可能填的是上月末）。
  3. 用**金额对拍**交叉验证：`出库单明细金额合计 − 需分摊折扣 = 表里的开票价税合计`。
     对不上就说明区间没框住正确的单（见下条）。

### SF-39 出库单日期是「每月 25 日 / 月底」，日期区间差一天就整张漏掉 ★
- **现象**：表里填 `8/26 ~ 9/25`，结果该客户的出库单**一张都没筛出来**（或金额差很多）。
- **根因**：这类客户的出库单**日期 `FDate`（单据日期，「日期」列）**落在 **每月 25 日**和**月末**，
  `8/26 ~ 9/25` 的首尾**各差一天**，正好把两张单全排除。
  （★ 2026-10-29 校正：筛选口径是 `FDate` 单据日期，**不是** `FDate1` 账务日期；本条早前写的 `FDate1` 是错的）
- **判据**：拿 `ExecuteBillQuery` 查该客户 `FDate >= 起 - 5天 and FDate <= 止 + 5天` 的全部出库单，
  看**哪一张（或哪两张）的金额 = 目标 + 折扣**。
- **修法**：改区间，或直接用 `--bill-nos` 指定对上的那张。

---

### SF-40 标签累积挤爆 tabstrip → `activate_tab()` 点错页 → 整链报「找不到可见的列表网格」★★★

> ★★★ **2026-10-03 二次定标后重判**（本条早前写的"孤儿标签天生关不掉"**是错的**，务必按下面的读）
>
> **从干净态重测的事实**（`scripts/kd_tab_probe.py --dump`）：
> - **开一个功能页 = 替换当前标签，不是新增**：实测 `tabs()` `0 → 1`；
>   已开着「应收单列表」再开「销售出库单列表」→ `1 → 1`，前一个自动消失。
>   ⇒ **正常流程标签数稳定在 1~2 个**
> - tabstrip 宽 **1156px**，单个标签 **108px** ⇒ **只能装约 10 个**
> - 攒到几十个时每个被压到十几 px：**文字完全不可见、坐标互相错位**
> - 此状态下**连金蝶自带的右键「关闭其他页签」也失效** → 看起来就像"孤儿关不掉"
> - **但干净态下清场完全正常**：实测 4 个标签（含判为"孤儿"的 `凭证 - 修改`）
>   **全部关掉、只用 6 秒、零空转**
>
> ⇒ **真病根是「标签累积」这件事本身，不是某个标签天生关不掉。**
> ⇒ **预防 > 治疗：不让它累积。** 最有效的一招 = **每家/每轮任务开跑前先清场**
> （`clean_tabs --go --keep-name 销售出库单列表`）。
> ⚠️ 下文"点了没反应"的所有现象，都要读作"**在累积爆表的环境下**如此"，不可当通用结论。

- **现象**（2026-10-03 实测，13 家批次**前 2 家连续失败**，各烧 250s / 223s）：
  ```
  RuntimeError: 找不到可见的列表网格，无法勾选   ← push_chain.step_invoice → ui.select_rows
  ```
  失败前日志：`打开「应收单列表」前先关掉占单标签：[80 个「应收单 - 新增」]`。
  界面上确实堆着几十个同名标签，用户描述是"**一直在点删除标签、删不掉**"。
- **不是这些原因**（都已实测排除）：
  - ✕ 被遮挡？→ `elementFromPoint(✕中心)` **精准命中** `SPAN.k-i-close mainTabCloseButton`
  - 坐标在视口外？→ rect 正常（`[330,54,18,16]`）、`vis()` 为 true
  - 鼠标通道坏？→ **点标签本体切页是有效的**
  - 弹了确认框？→ `.k-window` 数为 **0**
- **失效机理**（为什么爆表后连 ✕ 都点不动）：金蝶用**自绘标签管理**（`kdMainTabs`），
  `jQuery(strip).data('kendoTabStrip') === null`，strip 上只挂了 `kendoSortable` / `kendoDraggable`。
  关闭事件绑在**内部 tab 对象**上；tabstrip 被挤爆后标签宽度退化成十几 px，
  `tx = r.x + min(40, w/3)` 之类的落点计算失去意义 → **点到的其实是隔壁标签** → 静默无响应。
  这类标签的 `li` **缺 `k-tab-on-top`**（活标签有 `k-tab-on-top k-state-active`，高 30px vs 26px）。
- **逐个试过的点击方式**（`scripts/kd_tab_probe.py --try-ways` 可复现）：

  | 方式 | 结果 |
  |---|---|
  | `locator.click()` | ✓ **首选** —— 会等元素**位置稳定**再算坐标，不受 hover 宽度漂移影响 |
  | `page.mouse.click(cx, cy)` | ✗ 用的是**提前取的旧坐标**，hover 后标签变宽即点空 |
  | `locator.click(force=True)` | ✗ |
  | `locator.dispatch_event("click")` | ✗ |
  | JS `el.click()` | ✗ |
  | 手动 `mousemove`→`down`→`up` | ✗ |
  | **金蝶自带右键菜单「关闭其他页签」** | ✗（**仅在爆表态**；干净态请优先用它） |

- **后果（这才是要命的）**：标签越积越多 → tabstrip 被挤爆 → 标签**坐标重叠错位** →
  `activate_tab()` 按坐标点到了**别的标签** → 该页面没有列表 →
  `select_rows()` 报「找不到可见的列表网格」→ **整链失败**。
- **修法**（已落到 `steps.py`）：
  1. `tabs()` 返回 **`id`**（`li_xxxFMAINTAB_c`），选标签一律用它（同名标签可以有几十个）
  2. `close_tab()` 用 **`locator.click()`**（会等元素**位置稳定**再算坐标）；旧坐标点击只作兜底
  3. `close_all_tabs()` 用**黑名单**：关不掉的记入 `failed` **跳过、继续关其他** ——
     ⚠️ 不要用"连续 N 次无效就 `break`"，那会把后面的**活**标签一起放弃 →
     占单标签漏关 → 报「该业务单据正在被 XX 使用，本次不生成凭证」
  4. ★★ **预防（最关键）**：**每轮任务开跑前先清场**，把标签数按回个位数。
     批量场景就写在批量脚本里（清场实测只要 6 秒）；单次场景在流程开头清一次。
  5. **完成使命的单据标签（保存/提交后）尽早关**，别留到最后一并清
  6. `open_menu_by_search` 末尾打印**标签数**，便于发现「标签在涨」这个早期信号
- **不要用刷新解决** → 见 SF-41。
- **诊断工具**：`scripts/kd_tab_probe.py`（`--dump` 标出孤儿 / `--try-ways` 逐种试 / `--close-orphans` 黑名单清场）

### SF-41 ★ 红线：**金蝶 `reload()` = 登出**，绝不要刷新用户的页面

- **实测**：对已登录的金蝶页面执行 `page.reload()` →
  **直接退到登录页**（用户名保留，需重输**密码 + 验证码**）。
- **含义**：
  - "标签堆太多就刷新一下"这条思路**在本系统不可用**
  - 刷新会**打断用户会话**，属于必须先征得同意的操作
- **替代**：靠 SF-40 的黑名单跳过 + 尽早关闭，而不是重置页面。

### SF-42 「关掉 N 个」是假的 —— `closed` 记录的是**尝试次数**

- **现象**：日志 `生成凭证前清占单标签：关掉 [79 个…]`，但下一轮清场那 79 个**还在**。
- **根因**：`close_all_tabs` 里 `closed.append(...)` 是**无条件**执行的（只要 `close_tab` 不抛异常）。
  点 ✕ 静默无效时不会抛异常 → 照样记一笔。
- **怎么看穿**：**用耗时反推** —— 79 次 × 固定 `wait_ms=900` ≈ 71s。
  若日志里这 79 个"关掉"只花了 3 秒，那才是真关掉了。
- **修法**：`closed` 只在**确认标签集合发生变化**后才 append（见 SF-40 第 3 条），
  并单独计数 `skipped`（关不掉的）打日志。
- ⚠️ **代价意识**：因为 `closed` 是假数字，**不能拿"关掉 N 个"判断流程健康**；
  要看**标签数是否在涨**（`open_menu_by_search` 已打点）和**耗时是否异常**。

### SF-43 Kendo 下拉「日志说选上了、回读却是空」—— 展开动画未收敛就点 ✗

- **现象**（2026-10-03 滁州远达实跑）：
  ```
  [01:13:23] 选择下拉项：姚玉平 @(549,545)
  RuntimeError: 下拉 F_PAEZ_COMBO 回填校验失败：期望 '姚玉平'，实际 {'text': '', 'value': ''}
  ```
  **日志明明白白说"选上了"，但值根本没落。**
- **定标**：同一张还开着的页面上手动复现 —— `open_combo()` 后**多等 1.2s** 再选，
  **一次成功**（`read_combo` 返回 `{'text':'姚玉平','value':'姚玉平'}`）。
- **根因**：`fill_combo` 是 `open_combo()`（内部固定 `wait_for_timeout(600)`）**立刻**调
  `pick_combo_option()`；而 Kendo 下拉有**展开动画**，动画期间 `li` 的
  `getBoundingClientRect()` 还在移动 —— 按那一刻的坐标 `page.mouse.click`
  会点到别的位置（甚至容器外），**静默失效、不报错**。
- **修法**（`steps.py`，已落地）：
  1. `pick_combo_option(text, tries=8, gap_ms=350)`：**轮询**，每轮做
     **`elementFromPoint` 命中检测**（坐标处最顶层元素必须 `li.contains(top)`），
     **命中才点**；选项集已渲染但没有该值 → 立刻报错，不做无谓等待
  2. `fill_combo(field, text, tries=3)`：**外层重试**，每轮 `open_combo → pick → read_combo` 校验，
     不符就 `Escape` 收起重开
- **通用规律**（对所有"取坐标 → 真实鼠标点"都成立）：
  **先确认 `elementFromPoint` 命中目标元素，再点**；不要"取一次坐标就点"。

### SF-44 单据页已是"半成品"时，**别重跑 push_chain**（会推重）

- **场景**：上一步失败，但应收单/专票已经建好（甚至在库），只是缺后半段。
- **错法**：直接重跑 `push_chain` → 它会**重新下推** → 产生第二张应收单/专票。
- **正确工具组合**（单据已开在界面上时）：

  | 场景 | 命令 |
  |---|---|
  | 专票页已开着，缺 复核人/保存/提交 | `act_invoice.py --combo --save --submit --yes`（`KdNet` 服务端反馈判定最可靠；`--peek` 只读看字段） |
  | 专票已审核，缺凭证 | `gy3_4_voucher.py --inv <专票号> --go` |
  | 凭证已生成，缺摘要 | `write_digest.py --ar-no <应收单号> --company <客户> --go` |

- **判断起点**：先用 `ExecuteBillQuery` 只读查"这家已到哪一步"（按来源出库单反查应收单 →
  按应收单查专票 → 按摘要原文查凭证），再决定从哪段续。
- ⚠️ **手工补做的单必须先写进断点文件**（批量脚本的 `*_result.json` 里 `ok=true`），
  否则批量会把这家**再推一遍**。

### SF-45 ★ `vis()` 判"可见"不可靠 —— 会点到**后台标签页**里的同名控件（静默无效）

- **现象**（2026-10-03 福建广源，连点 3 次都"成功"但前台纹丝不动）：
  ```
  [01:23:35] 选择下拉项：姚玉平 @(549,545)
  [01:23:36] WARN 第 1/3 次选上了但回读不符：期望 '姚玉平' 实际 {'text': '', 'value': ''}
  [01:23:37] 选择下拉项：姚玉平 @(549,545)      ← 坐标每次都一模一样
  [01:23:38] WARN 第 2/3 次 …
  RuntimeError: 下拉 F_PAEZ_COMBO 回填校验失败
  ```
  **同一段代码在江西广源却过了** ⇒ **偶发**，取决于当时 DOM 里后台表单的数量/顺序。
- **根因**：`vis()` 只看 `width/height + display/visibility`，**漏了**
  ① 祖先 `display:none`（`offsetParent === null`）
  ② 被祖先 `overflow` 裁掉 / 定位到屏幕外 / 被压在别的面板下层。
  金蝶 SPA 里**后台标签页的表单 DOM 依然在**，和前台表单**字段 id 完全相同**、
  下拉选项文本也一模一样（姚玉平/陈聪/张婷…）。
  `querySelectorAll('[id$="-F_PAEZ_COMBO-EDITOR"]').filter(vis)[0]`
  → 挑中**后台**那个 → 点它、点它的下拉项 → 前台表单毫无反应。
  （关键破绽：**坐标每次完全相同** —— 前台下拉每次展开都会因滚动/布局微调而略变。）
- **修法**（`scripts/kd_combo.py` 已落地）：
  1. **`svis()` 强化可见性**：加 `offsetParent !== null`（或 `position:fixed`）、
     `opacity !== '0'`
     ⚠️ **修正（见 SF-47）**：一开始这里还加了"与视口相交"，**这是错的** ——
     窗口矮时表单下半部分的**真控件**会被误杀，导致半成品单收不了尾。
     `svis()` 只答"渲染出来没"；"在不在视口内"用单独的 `inView()`，
     "在不在前台面板"用 `inActivePanel()`（`.k-content.k-state-active`）。
  2. **`onTop(e)`**：`elementFromPoint(中心)` 命中的元素必须是自己或自己的后代
  3. **`findEditor(f)`**：先 `svis` 过滤，**再优先 `onTop` 的那个**（= 前台那个）
  4. **`pick_combo_option(..., field=…)`**：只认**贴着该控件**的下拉容器
     （按控件中心 ±460px 横向、-80~620px 纵向过滤）——
     否则后台同名下拉选项文本一样，必然挑错
  5. `read_combo` 返回值加 `id` / `onTop` 便于诊断
- **通用规律**：**凡"按 id 找控件"，都要用 `svis`+`onTop`，不要用 `vis()`。**
  同理：界面已经显示目标值时就**别重选**（下推常常已带出正确值）。

### SF-46 截图 88 秒 + 并发探针把浏览器拖慢

- **现象**（2026-10-03 江西广源）：
  ```
  [01:26:17] [ OK ] 应收单已保存
  [01:27:45] [INFO] 截图 -> 012617_pushchain_receivable.png（CDP 降级成功）   ← 88 秒
  [01:27:50] [ OK ] 应收单已提交   ⇒ 该段累计 238.4s（正常约 85s）
  ```
- **根因 ①**：`page.screenshot(timeout=5000, animations="disabled")` 那句
  —— **`timeout` 管不住它**。`animations="disabled"` + 等字体那套逻辑，
  在页面主线程被长任务占住时会**卡在超时之外**。
- **修法**：**先 CDP、后 Playwright**（`common.shot`）。
  CDP `Page.captureScreenshot` 不做任何等待，实测 **1~2 秒**返回。
- **根因 ②（重要的工作方式教训）**：诊断时我**在批量驱动浏览器的同时**
  跑了 3 个只读探针（`page.evaluate`）。**CDP 命令在渲染器里是串行的** ——
  并发访问会把截图/点击一起拖慢。上一条的 88 秒很可能有此贡献。
  ⇒ **批量运行期间只读日志文件，不要连浏览器做探针。**
- **顺带澄清**：清场本身**已经不慢**了 —— 同一段日志里清场只用 **6 秒**
  （`locator.click(timeout=6000)` + 黑名单跳过）。"卡 80 秒"的观感真凶是截图。

### SF-47 `svis()` 的"必须在视口内"会误杀真控件（SF-45 的修法要收窄）

- **现象**（2026-10-03 福建广源续跑，`act_invoice.py --combo`）：
  ```
  [02:03:47] [INFO]   回填前：{}
  [02:03:47] [ERR ] 失败：RuntimeError: 找不到下拉控件：'F_PAEZ_COMBO'（或不可见）
  ```
  可是 `probe_combo_why` / `probe_editor` 明明看到控件在：
  ```
  视口 viewport = 1366 x 641
  rect=[471, 701, 156, 21]   center=[549, 711]
  分项 f1尺寸=True f2显隐=True f3offsetParent=True  f4在视口内=False   ← 就这一条挂了
  ⇒ svis=False  onTop=False
  elementFromPoint -> None
  所在面板: {'cls': 'k-content kd-tab-form-container-ui-fluid k-state-active', 'active': True}
  ```
- **根因**：SF-45 给 `svis()` 加的"与视口相交"太狠。**窗口矮（实测 1366x641）**时，
  表单下半部分的控件天然落在折叠下方 —— 它不是"不可见"，只是**没滚到**。
  更讽刺的是 `elementFromPoint(549,711)` 也返回 `null`（点落在视口外），
  即"在视口外"这件事**本来就该靠滚动解决**，而不是把控件判成不存在。
  ⇒ 后果不只是"选不上值"：**半成品单直接收不了尾**（`read_combo` 回 `None`，
  `fill_combo` 抛错，整链断在这里）。
- **修法**（`steps.py` 的 `_VIS_STRONG`，已验证）：
  1. **`svis()` 只管"真的渲染出来了"**：尺寸>0、非 `display:none/visibility:hidden/opacity:0`、
     `offsetParent !== null`（或 fixed）。**去掉视口相交判断**。
  2. 视口单独一个 `inView(e)`；再补一个更靠谱的前后台判据
     `inActivePanel(e)`：往上找最近的 `.k-content` / `.k-tabstrip-content`，
     看它是否带 `k-state-active`。
  3. `findEditor(f)` 优先级：
     `onTop` → `inView && inActivePanel` → `inActivePanel` → `inView` → `eds[0]`。
  4. **`open_combo()` 里开下拉前先 `box.scrollIntoView({block:'center'})`** ——
     否则 Kendo 的 popup 贴在控件下方（约 y=722）**整块在视口外**，
     选择项的 `elementFromPoint` 命中检测永远是 `false`，最后"按最后坐标点一次"
     落在窗口外，**静默无效**。
- **判据优先级补充**：**"inActivePanel" 比 "inView" 更能区分前台/后台同名控件** ——
  后台标签面板通常是 `display:none`（`svis` 已滤掉），而"前台但需要滚动"的控件
  只会在 `inView` 上失分。别拿 `inView` 当"是不是前台"用。

### SF-48 CDP 截图也可能单次阻塞 9 分钟 —— 批量必须能整段关掉截图

- **现象**（2026-10-03 江西广源写摘要）：
  ```
  [01:51:34] [ OK ] 回读校验：全部一致 ✓
  [02:00:42] [INFO] 截图 -> 015134_digest_AR105-202609-0095.png（CDP）   ← 9 分 08 秒
  ```
  **注意时间戳的反读**：文件名里的 `015134` 是 `shot()` **开头**取的时间，
  日志行是**结束**时打的 ⇒ 这 9 分钟**全耗在 `Page.captureScreenshot` 这一个调用里**。
- **根因**：CDP 截图**要等渲染器产出一帧新画面**。窗口最小化/被遮挡，或页面主线程
  被长任务（凭证页的 KdNet 校验）占住时，帧永远不来 → 一直等。
  ⚠️ **CDP 的 `session.send` 没有 timeout 参数**，Playwright 的 `timeout=` 也管不到它
  （所以 SF-46 的"先 CDP 后 Playwright"只解决了 88 秒那一档，解决不了这一档）。
- **影响**：一家 3 张截图 × 8 家 = 最坏几小时**纯等待**，且**对正确性零贡献**
  （截图只是存档；判据是「查询回读」，SF-01 的优先级表）。
- **修法**：**截图可关**。
  - `common.shot()` 开头：`KD_NO_SHOT=1`（1/true/yes/on）→ 直接 `return None`，
    不打图、不等帧、不写盘。
  - `run_batch_*.py` 在文件顶部 `os.environ["KD_NO_SHOT"] = "1"`，
    子进程（push_chain / write_digest / clean_tabs）全都继承。
  - 单张调试、需要取证时再把变量摘掉。
  - 实测收益：同一段「找凭证 → 改摘要 → 保存」从 **9m53s → 27s**。
- **通用规律**：**长跑批量的脚本里，任何"非判据必需"的 IO 都要有开关。**
  截图 / 录像 / DOM dump 属于这一类。卡住时没法 timeout 的东西，就别让它默认开着。

### SF-49 「搜到了却点不动」的真凶：残留浮动提示条 + 判重把锚点清掉（一条完整死锁链）

- **现象**（2026-10-03 本批剩余 8 家）：**第一家就挂，而且家家都挂**（连败熔断，8 家全废）：
  ```
  [02:10:23] [WARN] 发现重复标签，先清场避免筛选错乱：['销售出库单列表', '应收单 - 新增', '应收单 - 新增']
  [02:10:25] [WARN] 标签 '应收单 - 新增' 点了没反应 → 判定为关不掉的孤儿，跳过
  [02:10:30] [INFO] 关掉挡路弹窗 1 个：['处理结果']
  ... steps.py open_menu_by_search → item.first.click(timeout=6000)
  TimeoutError: Locator.click: Timeout 6000ms exceeded.
    - locator resolved to <li class="kd-fullsearch-item-container k-state-focused">…</li>
    - <div draggable="true" class="kd-form-collapse-container">…</div> intercepts pointer events
  ```
- **这是一条 3 环相扣的死锁**，任何一环单独看都不像病根：

  **① 界面永久驻留关不掉的孤儿草稿页**（根，无解，只能绕）
  两个「应收单 - 新增」内容面板已销毁、✕ 点了无反应（SF-42 / SF-40）。它们**删不掉**。

  **② 判重逻辑"一发现任何同名标签就清空全部"** → 把工作锚点也清掉了
  ```python
  # ❌ 原逻辑（push_chain.filter_outstock）
  tabs = [t.get("text") for t in ui.tabs()]
  if len(tabs) != len(set(tabs)):
      ui.close_all_tabs(keep_names=[], quiet=True)   # keep_names=[] ⇒ 一个都不留！
  ```
  两个孤儿造成了"有重复标签" → **把「销售出库单列表」一起关掉**。
  ⇒ 后面必须回到顶部搜索去重开，于是引出第 ③ 环。

  **③ 右上角的残留浮动提示条压住搜索结果列表** → `locator.click` 永远超时
  实测（`probe_search_click.py`，这条探针就是为它写的）：
  ```
  视口 1366x641  条目中心=(996,107)
  -- 该点上的遮挡链（从顶到底）--
    [0] DIV cls='kd-form-collapse-container' rect=[991,57,214,58] z=10010 pos=absolute draggable=true
    [1] BODY cls='kdmainformbg'
  -- 可见的 .kd-form-collapse-container：1 个 --
    rect=[991,57,214,58] z=10010 盖住目标点=True 文本='提交：保存：1837971/2 该字段需要您手动查找'
  ```
  ⇒ 那是**上一次失败保存留下的提示条**（`该字段需要您手动查找`），
    浮在右上角，**正好盖住搜索面板的结果列表**。
  ⚠️ 关键反直觉点：**剥掉它的 `pointer-events` 也没用** —— 它下面直接就是 `BODY`，
    条目本身**并不在那个坐标上**（`getBoundingClientRect()` 的中心与实际命中点不一致）。
    所以"真实鼠标点坐标"这条路**根本走不通**。

- **修法（三处，缺一不可）**
  1. **判重只看列表类标签，且永远保留锚点**（`push_chain.filter_outstock`）：
     ```python
     ANCHOR = "销售出库单列表"
     same_anchor = [t for t in tabs if (t.get("text") or "").strip() == ANCHOR]
     list_dupes = {x for x in names if x.endswith("列表") and names.count(x) > 1}
     if len(same_anchor) > 1:            # 同名锚点只留一个（逐个关多余的）
         for t in same_anchor[:-1]: ui.close_tab(t)
     elif list_dupes - {ANCHOR}:         # 只在**列表类**重复时才清，且保留锚点
         ui.close_all_tabs(keep_names=[ANCHOR], quiet=True)
     ```
     ⚠️ 孤儿草稿页（`- 新增/- 修改`）**不算重复**，不要为它们清场。
  2. **搜索前先把焦点从"单据表单页"挪开**（`open_menu_by_search`）：
     激活页是 `… - 新增 / … - 修改` 时，切到任一 `…列表` 标签（没有就切无题首页标签）。
     表单页的浮层不随标签显隐，所以这只是"减负"，不是万能药 —— 但成本极低。
  3. **★ 结果条目点击改用 JS `el.click()` 兜底**（真正的解药）：
     真实鼠标（带剥层）→ **`el.click()`** → 最后才 `locator.click()`。
     实测 `el.click()` **有效**（点完标签列表立刻多出 `销售出库单列表`）——
     说明该条目绑的是 **jQuery click 处理器**，不走 Kendo 指针事件，所以合成 click 能触发。
     ```js
     const vis = e => {...};
     const el = Array.from(document.querySelectorAll('.kd-fullsearch-item-container'))
                  .filter(vis).find(e => (e.innerText||'').includes(kw));
     if (el) { el.click(); return ...; }
     ```
  4. 另加 `dismiss_floating_hints()`：把文案命中
     `/(该字段需要您手动查找|提交：保存：|保存：提交：)/` 的
     `.kd-form-collapse-container` 直接 `display:none`。搜功能前调一次。
     （`close_stray_windows` 只管 `.k-window`，管不到这种条。）

- **副作用要堵**：剥层后若最终**没点成**，必须把 `pointer-events` **还回去** ——
  残留的 `pointer-events:none` 会把后续所有点击也废掉，比原问题更糟。
  ⇒ `else` 分支里也要 `unpeel`。

- **通用规律**：
  · 「**搜到了却点不动**」先查**右上角/浮层**，再怀疑坐标 —— 用
    `probe_search_click.py` 把"遮挡链 + 所有可见浮层的 rect/z-index"一次打出来。
  · **`locator.click` 的 `intercepts pointer events` 是有价值的信号**，别急着加
    `force=True` 绕过 —— 它明确告诉你有东西压在上面。
  · **判重/清场这类"安全逻辑"最容易制造死锁**：写的时候必须问
    "如果我关错了、把工作锚点也关了，流程还能自己恢复吗？"
    不能 → 就要**显式保护锚点**。

---

## 附：金额联动的"看不见的规则"（不是 bug，但不看就会算错）

| 改哪个字段 | 结果 |
|---|---|
| `FDISCOUNTAMOUNTFOR` 折扣额 | 价税合计 = 含税单价×数量 − 折扣额；再算 不含税 = round(价税合计/(1+税率),2)；税额 = 价税合计 − 不含税 |
| `FNOTAXAMOUNTFOR_D` 不含税金额 | 价税合计 = 不含税 + 税额（**税额保持不变**） |
| `FTAXAMOUNTFOR_D` 税额 | 价税合计 = 不含税 + 税额（**不含税保持不变**） |

⇒ **价税合计只受「折扣额 / 含税单价」控制**；不含税与税额是分厘级微调旋钮。
⇒ 顺序必须：先折扣额定住价税合计 → 再不含税 → 最后税额。
⇒ ⚠️ 金蝶的「不含税」**不是**简单 `折后含税 ÷ (1+税率)`，它走**单价反算**，差 1~2 分。
  所以对齐**只能用页面读数闭环收敛**，**不要用公式离线推算目标值**。
⇒ 分摊扣款时各行折扣按比例四舍五入会产生几分尾差，**把尾差补到金额最大的那一行**。

---

### SF-50 ★★ 「凭证生成报告列表」是**瞬时单槽**的 —— 关掉就再也回不去（摘要只能"生成后立刻写"）

- **现象**：批量下推 13 家，其中 2 家（马鞍山祥恒 转155 / 宁波日和 转156）整链成功、凭证也生成了，
  但**摘要没写上**。事后想补：`write_digest.py` 直接报
  `没有「凭证生成报告列表」标签 —— 凭证还没生成？`，而摘要确实没写。
- **根因**：
  1. `凭证生成报告列表`（BAS_VchBuildReport）**只保留"最近一次"凭证生成的报告** ——
     实测打开来永远只有 **1 行**（本批收尾时是最后一家浙江至优 SVINV00034022 / 转162）。
     它**不是**一个会累积的历史列表。
  2. 它**只能由「生成凭证」动作自动弹出**：顶部全功能搜索里搜
     「凭证生成报告列表」「业务凭证列表」「生成报告」**全部 0 结果**。
     → 用户手工/脚本把它关掉后，**没有任何入口能把它再打开**。
  3. push_chain 收尾 `clean_tabs` 又把它关了（已修 `KEEP_TABS`，但**存量**救不回来）。
- **判据**：`write_digest.py` 的入口就是这张列表 → 没它就写不了摘要。
- **修法 / 绕法**：
  - **预防（正路）**：清场 `KEEP_TABS` 必须含 `凭证生成报告列表`；
    并且**摘要要在同一轮里紧跟生成动作立刻写**（`run_batch` 的 `chain_done` 路径就是这么设计的）。
  - **补救（本批实际用的）**：走 **SF-52 的「凭证查询」路** —— 见 `scripts/`（项目侧
    `src/write_digest_by_vchno.py` + `src/gl_goto_voucher.py`），
    按 **凭证字 + 凭证号** 定位并双击打开「凭证 - 修改」补摘要。
- **通用规律**：金蝶里**"由动作自动弹出的列表"≠"能搜到的功能"**。
  凡脚本依赖这类列表当入口，都要先问：**它能不能被重新打开？**
  不能 → 就必须**在同一轮里把它用完再收尾**。

### SF-51 ★ SPA 标签上限 15；孤儿「应收单 - 新增」关不掉，会一路堆到顶满

> ## ★★★★★ 2026-10-27 三次定案（实测定案，覆盖前两次）：配额 = ctl.items()，释放靠 ctl.remove()
>
> 前两版结论（10-03 "摘 DOM 即可" / 10-27 "回主页能清零"）**都错了**。本轮实测（`probe_remove_effect.py`、
> `probe_quota_reset.py`、`probe_items_count.py`）拿到铁证：
> - **配额精确计数 = `kendomainTabControl.items().length`**（`ul.kdMainTabsHeaderExtend` 的 widget）。
>   `ui.tabs()` 数 `.k-item.contentHeadEL` **不可靠**（实测 ui.tabs()=2 时 items()=4，漏幽灵+后台表单）。
> - **释放配额唯一手段 = `ctl.remove($(li))`**：实测 items() 4→3，真释放。
> - **点 logo 回主页不能清零**：实测 4→4 不变（只是切激活页，tab 对象仍在）——10-27 二次定案那条是错的。
> - **2 个零宽幽灵 li 常驻**（空文本、width=0），永远占 2 配额 ⇒ 真实可用 = 13。
> - **根因**：`steps._force_remove_tab` 的 strip 选择器写成了 `ul.k-tabstrip-items`（错），
>   主标签栏实为 `ul.kdMainTabsHeaderExtend` ⇒ 孤儿 li 永远 `querySelector('#'+id)` 找不到 ⇒ 摘不掉。
> - **正确落地**（run_batch_20261031.deep_clean）：直接对 `ul.kdMainTabsHeaderExtend` 的 ctl 调
>   `ctl.remove($(li))` 摘除所有非锚点标签，再回读 items() 校验。护栏阈值 12（15-2 幽灵-1 余量）。
> - 表现仍是「勾选 0 行」「找不到可见的列表网格」「查看总账凭证后凭证-修改页打不开」。
>   `reload()`=登出（SF-41）始终不可用。

> ## ★★★ 2026-10-03 定案：孤儿**可以**摘除 —— 主标签栏的 widget 叫 `kendomainTabControl`
>
> 下文"五种点法全无效、别在这上面试新点法"**只对「用点击」成立**。
> 真正的入口是**直接调主标签栏自己的 widget API**，与点击事件毫无关系：
>
> ```js
> const strip = document.querySelector('ul.kdMainTabsHeaderExtend');      // 主标签栏（专属 class）
> const $ = window.jQuery;
> const ctl = $(strip).closest('.k-widget').data('kendomainTabControl');  // ★ 不是 kendoTabStrip
> ctl.remove($(li));                                                       // 摘掉这个标签
> ```
>
> - **为什么以前找不到入口**：主栏 `data()` 的键是 **`kendomainTabControl`** —— 金蝶自研的
>   Kendo TabStrip **子类**（有 `items / remove / dataSource / activateTab / deactivateTab`，
>   只是**注册名不同**）。实测整页 14 个 `ul.k-tabstrip-items` 里，
>   **只有主栏** `data('kendoTabStrip')` 取不到值，另外 13 个都能取到
>   （它们全是单据表单内的页组：「基本/客户/财务/其他」「明细/收款计划/量价信息/税务明细」）。
>   ⇒ 一切 `data('kendoTabStrip')` 的尝试**必然落空**，于是被误判成"框架没留入口"。
> - **实测**（`--go`，本机 6 个孤儿）**6/6 摘除成功**：标签数 7 → 1，
>   保留锚点「销售出库单列表」完好；`ctl.items()` 与 `.k-item.contentHeadEL` 计数**同步减一**。
> - **顺带解开两个老谜团**：
>   ① `ctl.items()` **恒定比真实标签数多 2** —— 主栏长期驻留 **2 个零宽幽灵 `li`**
>      （`text=''`、无关闭按钮、`getBoundingClientRect()` 宽高为 0）。
>      `steps.tabs()` 用 `svis()`（要求 `width>0`）正好滤掉它们；
>      而 `offsetParent!==null` 口径**会把它们算进来**（⇒ 自己写 dump 脚本时口径必须与
>      `steps.tabs()` 对齐，否则**序号错位**、按序号摘除会摘错）。
>   ② "jQuery 实例不一致导致取不到 widget"**不成立**：同一个 `$` 对另外 13 个 strip 都能取到。
> - **落地**：`close_all_tabs` 的黑名单分支已改为"先试 widget 摘除、失败才记黑名单"，
>   孤儿不再只增不减（项目侧 `steps._force_remove_tab()`，探针 `src/probe_tab_kill.py`）。
>   页面侧**不再需要 F5 腾位**（**F5 = 登出**，见 SF-41）。
> - ⚠️ **口径**：只摘 tabstrip 的 `li`，**不碰任何单据数据**（不保存/不提交/不删除）；
>   若该标签正打开着一张**未保存的草稿**，摘掉它 = 丢弃草稿（草稿本来就没落库，无损失），
>   但**正在被别的用户/别的链路使用的活单据标签**不要碰。

- **现象**：想打开「凭证查询」，报错弹窗：
  > 金蝶提示 —— 不能打开当前功能页签。原因：**超出了参数控制打开页签数的上限15个**。
  > 建议：关闭不需要的页签或联系管理员取消控制参数（参数设置->基础管理->BOS平台）。
  同时 `page.mouse.click` 到顶部搜索图标报 `<div class="k-overlay"></div> intercepts pointer events`
  （就是这个模态的遮罩在拦）。
- **根因**：SPA 内部 tabstrip 的**标签数**（不是浏览器页面数，CDP `/json/list` 只显示 1 个 page）
  达到 15 就拒绝再开新功能页。实测清场后仍**长期驻留 10~11 个孤儿「应收单 - 新增」标签**
  （`li` 缺 `k-tab-on-top`、内容面板已销毁，`✕` 与 `locator.click` 均静默无效），
  加上 `销售出库单列表` + 我们临时开的 `凭证查询`/`凭证 - 修改`，**很容易顶到 15**。
- **为什么会有这么多（成因链，2026-10-03 从日志实锤）**：
  1. **每下推一张应收单，必然新开一个「应收单 - 新增」草稿页标签** ——
     `下推 → 选目标「应收单」→ 确定` 打开的就是这个编辑页（日志：`填写 FDATE…` 就在这个页里）。
  2. **保存+提交完成后，这张草稿页标签不会自动关闭**（金蝶不自动回收草稿页），
     RPA 若没主动关，它就留在 tabstrip 上。
  3. **一旦内容面板被销毁，它就从"活标签"退化成"孤儿"** ——
     `li` 缺 `k-tab-on-top`、关闭事件绑在已失效的内部 tab 对象上，`✕` 点击**静默无响应**；
     实测 `locator.click()`、`mouse.click(坐标)`、`force=True`、`dispatch_event`、JS `el.click()`
     **五种全无效**，连金蝶自带「关闭其他页签」也点空。
  4. ⇒ **只增不减**。基本是**一家一个**，且清场永远减不掉它。本批实测轨迹（`run_batch_20261003_console.log`）：
     | 时刻 | 标签总数 | 「应收单 - 新增」孤儿 | 本轮清场关掉的 |
     |---|---|---|---|
     | 02:33:48 | 5 | 4 | 凭证查询 ×1 |
     | 02:42:40 | 7 | 6 | 凭证-修改/凭证生成报告列表/业务凭证列表 ×3 |
     | 02:51:02 | 9 | 8 | 同上 ×3（**每次都是这 3 个，孤儿一个没减**） |
     | 02:58:32 | 10 | 9 | 同上 ×3 |
     | 03:01:51 | 12 | 10 | —（已顶到 12/15） |
     → **曲线单调递增，跑一家 +1**；能关掉的永远只有我们自己临时开的那几个列表页。
  - **为什么五种点击方式全无效（关键推论）**：`locator.click` / `mouse.click(坐标)` / `force=True` /
    `dispatch_event` / JS `el.click()` 是在**换"怎么触发点击"**，而它对**活标签**确实有区别
    （活标签坐标会漂移 → `locator.click` 能关、`mouse.click` 点空）。
    对**孤儿**五种全灭 ⇒ 说明**点击事件确实送达了**，卡的是 handler **内部**：
    `✕` 的监听器闭包里捕获的是**已卸载的 tab 组件实例**，调用它的 close 时
    要么抛错被内部 try/catch 吞掉、要么判"无内容/未激活"直接 return → **静默无响应**。
    ⇒ **换触发方式、换坐标都救不了**；这不是"点不中"问题，是"点了也没用"问题。别在这上面浪费时间试新点法。
  - **本质**：**DOM 层还活着、组件层已经死了**（僵尸节点 / 悬空 handler）。
    页签栏 `li` 与内容面板是两套 DOM，由框架的 tab registry 协调；
    内容面板被回收（unmount）时 `li` 没同步摘除 → 两层状态脱钩。
    正常标签点 `li` 会**按需重建**面板；而草稿页（下推产生的实例）在保存后**已无稳定单据实体可重建**，
    所以点也点不出内容、关也关不掉 —— 这就是孤儿**清一色是「应收单 - 新增」**的原因。
  - **可减轻的做法**：下推完、提交后**立刻关掉这张草稿页标签**（趁它还"活"着能关）；
    做不到就接受它变孤儿 → 用 `clean_tabs` 关掉**其他**能关的，给它留位置。
- **判据**：`k-overlay` 拦截点击 + 弹窗文案含「上限15个」；用 `scripts/kd_tab_probe.py` 列标签数。
- **修法**：
  - **能关的先关**：`clean_tabs --go --keep-name 销售出库单列表`（实测一次关掉 4 个列表标签）。
  - ★ **孤儿也关得掉（2026-10-03 起重判）**：不要"关不掉就跳过"了 ——
    `close_all_tabs` 在检测到"点了没反应"后会**自动走 `kendomainTabControl.remove()` 摘除**
    （见本条目顶部定案）。**别再手动 F5**（F5 = 登出）。
  - **要腾位置**：一个功能页 = 1 个标签，双击列表行再开「凭证 - 修改」= 又要 1 个 →
    留够 **≥2 空位** 再开工。**主栏 `ctl.items()` 口径比真实标签数多 2**（两个零宽幽灵
    `li`），估算配额时以 `steps.tabs()` 为准。
  - 弹窗本身用「确定」关掉即可（只是提示，不改数据）。

### SF-52 ★★ 「凭证查询」列表：虚拟滚动 + 200行/页 → **双击前必须先 scrollIntoView**，否则点空

- **现象**：在「凭证查询」里定位到目标凭证（转155 在第 **5/5** 页第 **153** 行），
  `getBoundingClientRect()` 也算出了坐标，`mouse.dblclick` 却**毫无反应**（标签不变、无报错）。
- **根因**：
  1. 列表 **200 行/页**（实测期间 2026-9 共 **979 行 = 5 页**），我们的凭证（转155/156）
     **不在首屏**，要翻页。
  2. 列表是**虚拟滚动**：`.k-grid-content` 只露 ~10 行。第 153 行的 `<tr>` **在 DOM 里**，
     但它的 `getBoundingClientRect().y` **远在视口外** → 鼠标点在空白处 = 点空且**不报错**。
  3. `ui.activate_tab("凭证查询")` 对这种溢出/带空格的标签名**不可靠**
     （实测报 `切不到标签`，但 `peek_tabs` 明明列出来了）——
     要用**真实鼠标点 tabstrip 元素坐标**。
- **修法**：
  ```python
  # 翻页：用金蝶自定义 grid 的 dataSource（比找分页按钮可靠）
  gr = $(gridEl).data('kendoKDGrid'); gr.dataSource.page(n)   # 再等 ~2.6s
  # 打开前先滚到行，再取"视口内"坐标
  tr.scrollIntoView({block: 'center', inline: 'nearest'});
  const b = cell.getBoundingClientRect();
  if (b.y < 0 || b.y > window.innerHeight) → 放弃（别硬点）
  ```
  ⇒ 双击一行会打开标签 **「凭证 - 修改」**（凭证处于**暂存 A** 时工具栏「保存」可用，摘要格可编辑；
     若该凭证「已提交 B / 已审核 C」，摘要格带 `k-gridCellstate-disabled`，**改不了**）。
- **配套**：`write_digest_by_vchno.py --no 155 --ar-no … --company … --go`。

### SF-53 ★ 「业务凭证查询」看着对，其实**默认过滤是坏的**（账簿值是未选占位）

- **现象**：顶部搜索能搜到 **「业务凭证查询」(YWPZCX-YS，财务会计>应收款管理>账务处理)**，
  工具栏也确实有 **「查看总账凭证」**（正是打开「凭证 - 修改」的那颗按钮）——
  但**列表永远是 0 行**（数据源 `total=0`），默认条件点「确定」也 0 行。
- **根因**：它的「列表过滤」条件里 **账簿=105 那一格的值渲染成 `select 105`** ——
  **"select" 是未选中的占位**，等于账簿没选，所以查不出东西。点「重置」恢复默认方案也还是这个状态。
- **修法**：**别走这条路**（省时间）。补摘要用 **SF-52 的「凭证查询」**。
  若一定要用，得先在条件里把**账簿真正选上**（基础资料选择器，值不能靠塞文本）。
- **顺带（很有用的搜索情报）** —— 顶部全功能搜索实测：
  | 搜得到 | 搜不到 |
  |---|---|
  | 凭证查询、业务凭证查询、总账凭证查询、**凭证生成情况查询**、凭证生成 | **凭证生成报告列表**、**业务凭证列表**、生成报告 |


### SF-54 ★★ 「写入」报成功，**其实根本没生效** —— 明细折扣末行 / 对齐锚点行

**现象（浙江至优 AR105-202610-0007，2026-10-03 实跑日志逐行还原）**

| 日志行 | 内容 | 说明 |
|---|---|---|
| 折扣分摊 | 扣款 5,047.50，基数 100,950.00，**16 行** | 期望逐行落库 |
| 写入完成 | **「折扣额填写完成：写入 16 行（共 16 行）」** | **自称全写成功** |
| 紧接着读合计 | **折扣 4,282.50** / 不含税 85,780.98 / 税额 11,151.52 / 价税 96,932.50 | **已少 765.00** —— 末两行(seq 15/16)折扣=0.00 |
| 对齐第 1~4 轮 | 每轮都读到**完全相同**的 `85,780.98(+911.48) / 11,151.52(+118.52)` | 锚点 seq=11 写 `12,761.95 → 11,850.47` **四轮零效果** |
| 收尾 | `[WARN] 对齐 4 轮仍未完全达标…请人工复核` → **照样 save_bill()** | 错着落库，专票/凭证全继承 +765 |

**根因**
- `detail_set_cell(idx, field, val)` = `dblclick → Ctrl+A → Delete → type → Enter`，
  **一步都不校验**：坐标算得出来、`mouse.dblclick` 不抛、`keyboard.press` 不抛 → 一律视为成功。
- 写入实际会丢的场景（本批命中 2 类）：
  1. **末几行（最后一张来源单的尾巴）** —— 写完最后一行立刻 `detail_blur()`，
     而 `detail_blur()` 走的是 `detail_cell_pos(0, FMATERIALNAME)`，**内含 `scrollIntoView`**
     → 网格从**底部猛滚回顶部**；末两行的编辑器在这一下里没走完 commit，值就没了。
  2. **联动重算列** —— 折扣改动会触发不含税/税额重算；若目标列是**派生列**，
     手写的值会被公式覆盖 → 表现为"写了但读回来没变"（对齐循环 4 轮零效果即此）。
- **真正致命的不是"会丢"，而是"丢了没人拦"**：写端不校验，下游只 WARN 然后保存。

**修法（已落地 / 待落地）**
- ✅ `steps.detail_fill_discounts()`：写完**逐行回读**，不一致重填（≤3 轮），仍不一致 **抛错停止**。
- ✅ `push_chain` 尾差对齐：`if not s["ok"]` 由 `log.warn` 改 **`raise RuntimeError`**（不达标不保存）。
- ✅ `detail_align_totals()` **无进展即早停**（2026-10-03 落地）：比对**相邻两轮**的合计快照，
  一字未变 ⇒ 写入未生效 ⇒ 立即 `return`（不再空转满 `max_rounds`）。
  离线回归 `src/selftest_align.py`（三用例：正常收敛 / 零生效早停 / 部分生效）——
  **不连浏览器、不动金蝶**，改完随时跑。
- ✅ 对齐锚点写入**写后回读**（2026-10-03 落地）：`_fix()` 写完立刻读回该锚点行，
  没落到位就报「锚点写入**未生效**」。例：线上连写 4 轮 `12761.95 → 11850.47`，
  每轮读回**还是 12761.95** —— 加固后第 1 轮就会吼出来。
- ⬜ `detail_blur()`：批量写完后**别立刻滚回顶部**（先原地 blur，或先逐行回读确认）。
  **需真单据实测**，见下条工具。
- 🧪 **诊断工具** `src/probe_discount_write.py`：A/B/C/D **对照组**（唯一变量=写完怎么收尾），
  逐行写 + **每写一行立刻回读**，输出"第几行、第几次开始丢"。
  **从不保存/提交/删除**，默认实验后把原值写回。
  - B 保住 A 丢 ⇒ 元凶是 blur/滚回顶部；C 保住 ⇒ 确认是"滚回顶部"；D 保住 ⇒ 时序竞争；
    四个都丢 ⇒ 与收尾无关，问题在"写"本身。

**通用铁律（比本条更重要）**
> **凡"写"必"回读"。** `mouse.click / keyboard.type / press("Enter")` 全部不抛异常，
> 它们只证明"我发出了动作"，**不证明"值进去了"**。
> 判据永远在 **`detail_amounts()` 回读**（或服务端 `ExecuteBillQuery`）这一侧。

### SF-55 ★★★ 「写成功」但**写在了别人身上** —— 后台标签的表单同名控件（FDATE 落成"今天"）

**现象**（2026-10-03，13 家批量里**恰好 7 家**）
- 7 家的「应收单业务日期 + 到期日」落成了**今天**（2026-10-03）而不是 `--biz-date 2026-09-30`。
- 单号年月 = 业务日期年月 ⇒ 单号变成 `AR105-2026**10**-0001..0007`（另 6 家正确为 `-202609-0092..0097`）。
- **日志却在撒谎**：`填写 FDATE = 2026-09-30   [kendoDatePicker]` / `应收单业务日期 = 2026-09-30`，
  调用返回 `{how:'kendoDatePicker', val:'2026/9/30'}` —— 一切"正常"。

**根因**：`fill_bill_field` 用的是**弱可见性**过滤
`[id$="-FDATE-EDITOR_c"]` + `filter(vis)[0]`。
金蝶 SPA 里**后台标签页的表单 DOM 仍然在**（本批峰值并存 **11 个「应收单 - 新增」**），
这些控件"尺寸/display/visibility 都 OK" ⇒ 通过弱判定 ⇒ `[0]` 命中的是
**DOM 里最老的那张（孤儿草稿）**。
⇒ 写**确实写进去了**（所以 API 返回新值、日志打印成功），但**写在了别的表单上**；
当前这张单的 FDATE 仍是"今天"。
> 6 家侥幸正确 = 那几次 DOM 里恰好只有当前这张单 / 恰好排第一。
> ⇒ **表现是随机的**（同一批、同一份代码，mixed results），这最能骗人。

**实测证据**（`src/probe_date_write.py`，2026-10-03 05:15，祥恒创意（重庆）XSCKD-105-202609-0175）
| 观察 | 结果 |
|---|---|
| 下推后**草稿基线** | `FDATE 显示=2026/10/3`，而**到期日已 = 2026/9/30**（下推从源单带出） |
| S1 `kendoDatePicker` API（**线上原路径**） | 写 `2026-09-30` → 显示值 ✓ 且**到期日联动成 9/30** ⇒ **API 路径本身有效** |
| S2 API + 真实 blur | 写 `2026-09-29` → 到期日跟着变 9/29 ⇒ **`FENDDATE_H` 随 `FDATE` 联动成立** |
| S3 **真实鼠标点击 + 忠实键盘打字** | ✗ **反而更差**：掩码框只得到 `'20260930'`，widget=None，model 不入 |
| S4 S3 + blur | ✗ 同上 |

⇒ 结论：**问题不在"怎么写"，在"写给谁"**。
⇒ 附带铁律：**日期字段不要用键盘打字兜底**（Kendo 掩码输入解析不了 `2026/09/30`）。

**修法（已落地）**
- ✅ `steps.fill_bill_field()` 改用 `_VIS_STRONG` 的 `findEditor()`
  （`onTop` → `inView + inActivePanel` → `inActivePanel` → `inView` → `[0]`），**只认前台表单**。
- ✅ `steps.fill_bill_field()` **写后回读**（`read_foreground_field`）：日期型必须逐日相等，
  不一致重写 ≤3 轮，仍不一致 **抛错**；文本型回读为空即算失败。日志会打印
  「页面上有 N 个同名控件 → 已挑前台那个」。
- ✅ `push_chain.verify_ar_biz_date()`：**保存后**用 `ExecuteBillQuery` 回读 `FDate`
  （权威判据，界面回显/日志都不算），不符**在推专票之前抛错**，不让错误沿链扩散。
- ✅ `push_chain` 在同一步顺带回读 `FENDDATE_H`，不一致只 WARN（账期可能合法地不同）。

**通用铁律（与 SF-54 同源，但换了个面）**
> ① **凡"写"必"回读"**；② **凡"按 id 找控件"必挑前台那个**。
> 弱可见性过滤在 SPA 多标签下**必然**踩这坑：它只能证明"这个东西渲染出来了"，
> **不能证明"它是当前这一张单"**。
> ③ 单号里带年月时（`AR105-YYYYMM-####`），**年月 = 业务日期的年月** ——
> 单号月份不对，先回去查业务日期，别查编号规则。

#### SF-55 追加：**「联动字段跟没跟」是 model 进没进的免费见证**（2026-10-03 实跑 7/7 完全相关）

第一版修法（只认前台表单 + 回读 `val`/`widget`）**还不够**：重推 7 家时**仍有 2 家**落成
`-202610-`。看日志才发现回读是"骗人的"：

```
填写 FDATE = 2026-09-30 [kendoDatePicker] → 回读 '2026/9/30' widget=2026-09-30   ← 自我回读说"写对了"
应收单业务日期 = 2026-09-30  →  界面回读 FDATE='2026/9/30' / 到期日='2026/8/31'    ← 联动没跟！
```

| 家 | 到期日回读 | 服务端 `FDate` | 结果 |
|---|---|---|---|
| 济丰 / 日和 / 黎春 / 慧雄 / 重庆 | **2026-09-30** | 2026-09-30 | ✅ 单号 `-202609-` |
| **祥恒（常州）** | **2026/8/31**（源单带出值，未变） | **2026-10-03** | ✗ 单号 `-202610-0008` |
| **浙江至优** | **2026/10/3**（今天，未变） | **2026-10-03** | ✗ 单号 `-202610-0009` |

⇒ **"到期日有没有随业务日期联动" 100% 预测成败**（7/7）。
⇒ 机制：`dp.value()` / `widget` 只反映 **widget 自己的值**；
   表单 **model** 是否被更新，只能从"**联动副作用**"或**服务端**看出来。
   `dp.value(...)+trigger('change')` **偶尔**不落到 model（同一批 mixed），这就是随机性的来源。

**最终修法（已落地）**
- `fill_bill_field(field, value, linked=...)`：把联动字段当**硬判据**
  （自身回读 + 联动回读 都相等才通过）；不通过**重写 ≤3 轮**（第 2 轮起加**真实鼠标点空处 blur**），
  仍不通过**抛错**。
- **再加一道"稳定复验"**：通过后等 `settle_ms`(1.2s) 再读一次，防"写进去又被表单初始化覆盖"。
- `push_chain` 调用处改为 `ui.fill_bill_field("FDATE", biz_date, linked="FENDDATE_H")`。
- **失败模式变好了**：现在**在保存之前**就抛错 ⇒ **不会产生错单**（上一轮产生了 2 张，
  需人工反审核+删除；这一版不会再留下残留）。服务端回读 `verify_ar_biz_date()` 仍作为最后一道网。

> 教训：**回读要读"能反证的东西"**。读同一个控件自身的值，是**自证**（写端和读端犯同一个错时完全失效）；
> 要读**由它派生出来的、独立的一份数据**（联动字段 / 服务端 / 下游单据），才能反证。

#### SF-55 定案：**根因①（写给谁）是主因** —— 清掉后台孤儿草稿后 2/2 一次成功（2026-10-03 06:2x）

上一轮那 2 家（祥恒常州 / 浙江至优）**用的就是已加固代码**（前台 `findEditor` + `linked="FENDDATE_H"` +
稳定复验），当时**写不进去**、被判据拦下（好在拦在保存前，没再产生错单）。

用户清掉那 2 张残留错单后**原样重跑**（代码一行未改）→ **两家全部一次通过**：

| 家 | 应收单 | 到期日/业务日期 | 结果 |
|---|---|---|---|
| 祥恒（常州） | `AR105-202609-0103` | 2026-09-30 | ✅ 单号 `-202609-` |
| 浙江至优 | `AR105-202609-0104` | 2026-09-30 | ✅ 单号 `-202609-` |

**两组唯一的差别是环境**：上一轮失败时后台并存着十几张「应收单 - 新增」孤儿草稿；
本轮开跑前已用 `_force_remove_tab`（`kendomainTabControl.remove`）把它们摘干净（标签 7 → 1）。

⇒ **根因① `弱可见性 [0]` 命中后台同名字段 = "写给了别人" 是主因**；
   根因②（自身回读自证）是"为什么第一版没拦住"。
   **`三件套`合起来才闭环：① 先摘孤儿草稿（清干净环境）② 只认前台表单定位 ③ 用联动字段当判据。**

★ **可复用操作顺序（遇到同类先做这一步，别在脏环境里直接推）**
```bash
python kd_stray.py --close-tabs --keep 销售出库单列表   # 含孤儿 li 的 widget 摘除，7 → 1
# 再开跑下推/重推
```
- 判断"环境是否脏"：`steps.tabs()` 里出现多个同名「XX - 新增」= 脏，**先清再推**。
- 佐证：本轮两次补推各 206s / 260s，全链（应收单→专票→凭证→摘要）一次跑通，无重试。

#### ★★★ SF-55 判据二次修正：`linked` 既不能要求「== 目标值」，也不能要求「账期不变」

**时间**：2026-10-03 14:2x（第二批从出库单下推 11 家，江西新明峰包装科技翻车）

**现象**：在**已加固代码**（前台定位 + `linked` + 稳定复验）下，江西新明峰连续 3 轮被判
`★★ FDATE 写入**未生效**`，`RuntimeError` 拦在保存前 —— 但**它每次其实都写进去了**，
白跑一家（约 45s）。

**实测数据**（临时加一行 `[联动基准·写入前]` 日志才看清）：

| 时刻 | 业务日期 `FDATE` | 到期日 `FENDDATE_H` | 差值 |
|---|---|---|---|
| 下推后（写入前） | 2026-10-03 | **2027-01-29** | 118 天 |
| 写入后 | 2026-09-30 | **2026-12-29** | 90 天 |

- 旧判据「联动 == 目标值」要求到期日 == 2026-09-30 ⇒ **有账期的客户永远不通过**。
- 第一次修正「账期（联动−自身）不变」**也不成立** —— 这张单写入前后账期是
  **118 天 → 90 天**：**金蝶会按自己的规则重算到期日，账期并不守恒**。

**最终判据 = 联动字段的值「确实发生了变化」**（`steps.fill_bill_field` 内的 `_link_ok`）：
1. 联动值 == 目标值（账期 0 天的客户）→ 通过；
2. 写入前自身就已经是目标值 → 它本来就不该再变，放行；
3. 否则要求 **联动值 ≠ 写入前基准值**。
⇒ 仍然保住 SF-55 的护栏：**widget 骗人时 model 没进 ⇒ 联动毫无反应 ⇒ 判失败**。

**落地**
- `steps.fill_bill_field` 循环前抓 `base_self` / `base_link` 基准，并打日志
  `[联动基准·写入前] FDATE=... / FENDDATE_H=...`；判据换成 `_link_ok(link_val, self_val)`。
- 异常消息里"联动字段 = xx"此前误用了**自身**的 widget 值，已修正为真正的联动值。
- 佐证：改完后同一家原样重跑 → `AR105-202609-0114` / 专票 `SVINV00034068`，
  日志 `联动 FENDDATE_H='2026/12/29' ✓`，一次通过（210.7s）。

**教训**
- 「用联动字段做见证」是对的，但**见证的是"有没有反应"，不是"值等不等于目标"** ——
  派生字段取什么值由业务规则（账期 / 结算方式）决定，脚本不该假设它等于源字段。
- 排查这类问题，**把"写入前的基准值"直接打进日志**，比事后推断快得多。

**★ 验收口径（用户 2026-10-03 明确，从此固定）**：日期只认**业务日期 + 发票（开票）日期**两项，
两者都必须等于目标值；**到期日由金蝶按客户账期自行重算，不参与验收**
（有账期客户的到期日 ≠ 业务日期是**正常**的，不要去"修正"它）。
⇒ 对账/复核脚本里「到期日必须 == 业务日期」的判据要拿掉，换成
「应收单业务日期 == 目标」+「专票 `IV_SALESIC.FDate` == 目标」。

---

### SF-56 ★★★ 「从列表里取第 N 行」不可靠：**读成上一家的单号 → 凭证静默不生成** / 摘要把别人改了

**时间**：2026-10-03 08:00（从已有应收单续跑 4 家，第 4 家翻车）

**场景**：4 张已审核应收单（`AR105-202609-0105~0108`），用 `push_chain --ar-no` 只跑「专票→凭证」。
前 3 家全绿，**第 4 家（扬州祥恒 0108）**：

```
[OK] 全部结束：应收单 AR105-202609-0108 / 专票 SVINV00034032   ← ★ 34032 是**上一家（安徽广源）**的专票号
```
- 服务端实况：**自己的专票 `SVINV00034033` 生成得好好的**（11 行、合计 189,782.28 = 应收价税、状态 C），
  但**总账凭证根本没生成** —— 最近凭证 FID 到 `206815`（转165 = 安徽广源）就断了，`206816` 不存在。
- 「凭证生成」对话框**照样报「0 错误」**：因为它勾的是 34032，而 34032 早已有凭证，
  界面弹的是「所选单据已生成总账凭证」的**警告**而非错误 ⇒ **静默失败**。

**根因（两条独立缺陷叠加）**
1. **读端**：`read_bill_fields()` 用弱可见性 `[id$="-FBILLNO-EDITOR_c"] + filter(vis)[0]`。
   金蝶 SPA 把**后台标签页的整份表单 DOM 都留着**，字段 id 完全相同 ⇒ 读到**上一家的专票号**。
   —— **SF-55 的读端版本**：SF-55 是"写到别人身上"，这次是"读到别人身上"。
2. **判据端**：`step_voucher` 无条件信任界面读到的 `inv_no`；`_read_no` 的兜底只在"**读不到**"时触发，
   **读错不触发**。

**连带风险（同源，更危险）**：`write_digest.py` 走「凭证生成报告列表 → 查看业务凭证」打开的
「业务凭证列表」，然后**勾第 1 行**。实测这时列表带出的**不是自家那张**：
```
[08:01:10] 勾选第 1 行：… SVINV00034032 … 其他 警告 所选单据已生成总账凭证
```
⇒ 若这一步不报错，就会把**上一家的凭证摘要改成自家的文本**（污染别人的账）。
本次侥幸是因为随后「凭证 - 修改」明细网格为空、`select_row_by_index(0)` 抛 `out of range` 才停住。

**修法（已落地）**
- `push_chain._verify_iv_no(ui, inv_no, ar_no)`：拿到专票号后**用来源应收单反查**（服务端权威）——
  `IV_SALESIC.FSrcBillNo = <AR>`；界面值与反查不符 ⇒ **以服务端为准**并 WARN。
  接在 `step_invoice` 读号之后、提交/生成凭证之前。离线自测 5 分支全对。
- `steps.read_bill_fields()`：改为**只认前台**（复用 `read_foreground_field`：onTop → 激活面板），
  同名候选 >1 一律 WARN；读不到才退回旧 `-EDITOR_c` 写法。
- 通用铁律：**凡"从列表里取第 N 行 / 读界面上某个字段"的动作，都必须配一个不依赖界面的反证源**
  （来源单号反查 / 服务端金额 / 下游单据）。

**诊断口诀**：报告说「0 错误」≠ 真的生成了。**用"最近凭证 FID 是否连续"或按金额回查 `GL_VOUCHER`** 确认：
```python
kq.rows("GL_VOUCHER", "FBillNo,FDate,FVOUCHERGROUPNO,FVOUCHERGROUPID.FName,FEXPLANATION,FDEBIT",
        f"FDEBIT = {amt} and FDATE >= '2026-09-30'")
```

**补救路（本次实操，顺序不能反）**
1. `gy3_4_voucher.py --inv SVINV00034033 --go` → 生成凭证（得 **转166 / BizVch1357136**，报告 0 错误）；
2. `write_digest.py --ar-no AR105-202609-0108 --company 扬州祥恒包装有限公司 --go` → 写摘要。
   —— 摘要必须在"生成凭证"的同一轮紧跟写完（「凭证生成报告列表」是**瞬时单槽**，SF-50）。

---

### SF-57 ★★ 折扣「传不传 / 传多少」**不能用表里那一列** —— 要用出库单毛额对拍反推

**时间**：2026-10-03 15:05（第二批从源头出库单下推 11 家，郑州秉信）

**场景**：数据表里有一列「需分摊折扣额」（1860 / 22.5 / 789.36 / 4257 / …），
看着像"每家都要扣这笔钱"。

**现象（两种做法都错）**：
- 统一**不传** `--discount`：8 家对（它们出库单毛额本来就 == 开票价税），
  郑州秉信**少扣 300**（出库单 478,153.12 > 开票价税 477,853.12）；
- 统一**按表里那列传**：那 8 家**多扣**（浙江中包派克会变成 41,441.72 − 1,860），
  郑州秉信仍不对（表里写的是 1,652.87）。
- 两种错法都会被**下游完整继承**（专票金额、凭证借方、摘要数量全跟着错），
  而「凭证生成」照样报 **0 错误**。

**判据（唯一可靠）= 用「未下推出库单价税合计」对拍「表里开票价税合计」**：

| `未下推毛额 − 开票价税` | 结论 |
|---|---|
| ≈ 0（±0.05） | **不要传折扣**（出库单本身就是开票金额） |
| **> 0** | **差额就是应扣折扣**，传 `--discount <差额>` |
| < 0 | 挂起：区间内有已下推单（半成品）或被别的单占着 |

**实测（第二批 11 家）**：10 家差 0（表里那一列 1860 / 22.5 / 789.36 / 4257 / 65 / 708 /
147 / 6261.16 / 7407.85 **全是干扰项**）；**唯一**郑州秉信差 `+300`，而表里写 1652.87
⇒ 用户口径「折扣 300，之前传的表漏了」。

**写后必回读**（不看 UI，读明细 `FDiscountAmountFor` 合计）：
```
AR105-202609-0115 明细 7 行：含税 477,853.12 / 折扣 300.00 / 不含税 422,878.87 / 税额 54,974.25
出库单 478,153.12 − 300 = 477,853.12 ✓（= 专票合计 = 凭证借方 = 表里开票价税）
```

**落地**：`run_rest_20261003d.py` 的 `DISCOUNT_POLICY = {"郑州秉信包装有限公司": 300.0}`；
`verdict()` 用「期望毛额 = 开票价税 + 该家应扣折扣」判定；清单打印**同时**显示
「本批扣款」与「表里折扣列」两个数，避免再混。



### SF-58 ★★ 金额「三个数要精确到分」—— 旧的 `tol=0.01` 会放过 1 分尾差，价税合计跟着差

**时间**：2026-10-03 17:45（用户口径「应收单不含税/税额/价税合计都要和表里两位小数完全一致」）

**现象**：江西新明峰 AR105-202609-0114 的「价税合计」和表里差 **0.01**（276,921.44 vs 276,921.45），
其余家也有类似 1 分差。用户质疑"是不是对齐没顶到位"。

**根因（三层，一层套一层）**：
1. **旧 `tol=0.01` 放过了 1 分尾差**：`summarize()` 判定 `abs(d) <= 0.01` 就算"达标"，
   所以 notax 或 tax 差 1 分时，代码照样判 `ok=True` 并保存 → 价税合计跟着差 1 分。
2. **价税合计根本没参与校验**：`detail_align_totals` 只收 `target_notax` / `target_tax`，
   `summarize` 虽然算了 `all`，但从没拿它去比对目标。
3. **关键恒等式（决定怎么修）**：金蝶**行内** `价税合计 = 不含税 + 税额` 恒成立
   （见 `discount.py` 顶部「金额联动规则」），所以 `Σall ≡ Σnotax + Σtax`，即 `d_all ≡ d_notax + d_tax`。
   ⇒ 价税合计**不存在独立的尾差**，它差 1 分 = notax 或 tax 有 1 分没对齐。

**修法（正确的分层）**：
- `summarize()` 加 `target_all`（独立参与 `ok` 判定）；内部 `tol = to_d(tol)` 兜底。
- `detail_align_totals()` 加 `target_all` 参数；**默认 tol `0.01 → 0.00`**（精确相等，不再放过 1 分）；
  `_fix` 跳过条件 `< tol_d → <= tol_d`（d=0 时不写）。
- **价税合计不做第三段 `_fix` 修正**：它是派生值，直接改会被金蝶反算搅乱刚对齐的 notax/tax。
  只靠 notax/tax 精确对齐带动 all；`target_all` 仅作**交叉校验** ——
  notax/tax 都对齐而 all 仍差 ⇒ 表「不含税+税额 ≠ 价税合计」自相矛盾 ⇒ `ok=False` → `raise` 拦下。
- `push_chain.py` 加 `--target-all`；运行器补传表里的 `all` 列。

**判据口诀**：**「两位小数完全一致」= 差 == 0（Decimal 精确比较），不是 `<= 0.001`**。
因为金额都 quantize 到分，差只会是 0 或整分（0.01），不存在 0.001 中间态；用 `< 0.001` 和 `< 0.01` 效果一样（都放过整分差）。


### SF-59 ★★★ 专票 `FDATE` 写进后台孤儿草稿页 → 凭证 TOAV202「期间不对」失败（SF-55 在专票上的复现）

**时间**：2026-10-04 下午（13 家凭证失败，专票全部已生成审核）

**现象**：13 家凭证生成报「凭证生成失败，请检查凭证模版 TOAV202 设置」，但 63 家全过。
专票日志里**明确写着** `填写 FDATE = 2026-09-30 → 回读 '2026/9/30' widget=2026-09-30`，
看起来"写成功了"，服务端存的却是 **10-04（今天）**。

**根因（决定性证据：全量对拍，零例外）**：

| 专票业务日期 FDATE | 成功 | 失败 |
|---|---|---|
| 2026-09-30（正确） | 63 | 0 |
| 2026-10-04（今天） | 0 | **13** |

- `fill_bill_field("FDATE", biz_date)` 里 `pickBox` 的兜底 `b[0]`（DOM 顺序第一个）
  在长跑脏环境下命中了**后台残留的「销售增值税专用发票 - 新增」孤儿草稿页**，
  把 09-30 写进了那张孤儿页；当前真正要保存的单 FDATE 仍是默认「今天」。
- 界面回读用的**同一个 `pickBox`**，读的也是那张孤儿页 → **自证成功**，骗过所有检查。
  这正是 SF-55「写到别人身上」的专票版，且专票**没有到期日那种联动字段**做见证，
  `backfill_invoice_info` 填 FDATE 时没传 `linked`，所以 SF-55 的联动护栏也拦不住。
- 后果链：FDATE=10-04 → 凭证按「单据日期获取期间」取到 **10 月期间**，账簿只开 9 月 →
  TOAV202 报「凭证日期不在账簿期间」。**不是模板配置错，是专票数据错。**

**为什么 full10（重启浏览器后）全对**：重启清空了后台孤儿草稿页，`pickBox` 才命中当前单。

**修法（已落地 `run_ar_voucher_batch.py`）**：
- 新增 `_verify_inv_biz_date(ui, inv_no, want)`：专票 `save_bill()` 后立即用
  `ExecuteBillQuery` 服务端回读 `IV_SALESIC.FDATE`，不符 `want`（业务月底）就 **raise**，
  在**下推凭证之前**拦下，绝不让错误日期沿链扩散到凭证。
- 判据 = 服务端回读（权威），**不是界面 widget 回读**（自证，会被 SF-55 骗）。

**通用铁律（再次强化）**：
- **凡「写」必「服务端回读」**——界面回显、日志、widget 回读全是自证，会被
  「写错对象/读错对象」（SF-55/56）骗过；只有 `ExecuteBillQuery` 回读算数。
- 长跑（2+ 小时）浏览器会堆后台孤儿草稿页 + 最终崩溃，**分段跑 / 跑前重启浏览器**
  是让 `pickBox`/`filter(vis)[0]` 这类弱定位准确的前提。

**补救（已有 13 家专票 FDATE=10-04）**：反审核专票 → 改业务日期为 09-30 → 重新审核 →
  `gy3_4_voucher.py --inv <专票号>` 补凭证。**不要**去改 TOAV202 模板期间（那会让凭证
  错落到 10 月，账期不对）。

### SF-60 ★★ 摘要把「桶」并进水性油墨 —— 包装物（桶）要单独列「桶XX个」

- **现象**：含「桶」（存货类别=包装物）的客户，凭证摘要里桶的数量被并进了「水性油墨」，
  水性油墨数量虚高（差 = 桶数），且摘要里没有「桶」字样。
- **根因**：摘要口径 `classify_rows` 原本只拆「原材料(x) / 光油(y) / 其余(s)」，
  `s = z - x - y` 把「包装物」也当成了水性油墨；没有单独识别桶。
- **用户口径（2026-10-04 定）**：桶 = 存货类别(`F_PAEZ_BASEPROPERTY2`) = `包装物` 的行；
  单独列「桶{t}个」（单位「个」，不是公斤），**不计入水性油墨**：
  `s = z - x - y - t`；摘要 = `{客户}销售水性油墨{s}公斤，光油{y}公斤，原材料{x}公斤，桶{t}个`。
- **修法**（`src/digest_rule.py`，技能侧 `scripts/digest_rule.py` 已同步）：
  - `classify_rows(..., pack_category="包装物")` 识别桶 → 返回 `t` + `n_pack`，`s = z - x - y - t`。
  - `build_digest(..., t=..., t_unit="个")`：`t` 非空且 `>0` 时追加「桶{t}个」段；
    **t=0 一律不写**（与 omit_zero 无关），不传 t 向后兼容。
- **实测三家**（台州瑾晟/丹阳加美/镇海天雄，桶=包装物各 1 行，20KG/10KG白桶）：
  油墨 2110→2080、1520→1500、1970→1960，正是各差桶数（30/20/10）。
- **排查**：扫应收单明细 `F_PAEZ_BASEPROPERTY2 = '包装物'` 即知有没有桶；有桶的单位
  旧摘要都要按新口径重算重写。

### SF-61 ★★★ 「功能页点了，但它没开」—— 后续全在**上一个列表**上操作

- **现象**（河南华洋 2026-10-29，2026-10-31 批次第 13 家）：
  ```
  [16:54:42] 打开功能（真实鼠标 996,107）：YSDLB应收单列表 ...
  [16:54:46] 标签数 = 1 ['销售出库单列表']          ← 应收单列表根本没打开
  [16:54:47] 按单据编号过滤 1 张：['AR105-202610-0073']   ← 在出库单列表上过滤
  [16:55:28] 按单号过滤 AR105-202610-0073 → 列表 0 行
  RuntimeError: 找不到可见的列表网格，无法勾选
  ```
  同批次正常家是 `标签数 = 2 ['销售出库单列表','应收单列表']`。
- **根因**：`open_menu_by_search()` 点完搜索条目、`wait_ms` 一到就 **`return True`**，
  **从不校验目标页签是否真的出现**。一旦开页失败，后面所有"过滤/勾选/下推"都作用在
  **旧列表**上，报错点却出现在很远的地方（"找不到可见的网格"），极难定位。
- ★★ **别再归因于「页签配额满 15」—— 实测否掉了**（2026-10-29 华洋同批硬证据）：
  ```
  [⓪ 护栏] 清场后配额 3（达标）        ← 华洋开工前
  [收尾清场] 摘要写完后配额 6（达标）   ← 上一家完工
  [⓪ 护栏] 清场后配额 3（达标）        ← 华洋开工
  ```
  整批 `items()` 只在 **3~6** 之间，**从没接近 15**；华洋开「应收单列表」时配额就是 3。
  ⇒ "15 上限"是**当时的猜测**，把现象（开不了页）硬套到最熟悉的那条老 bug 上。
  真实原因是**开页动作本身没生效**（搜索面板被「处理结果」弹窗/浮层拦、或点空），
  与配额无关。**诊断时先看配额数字，再看标签。**
- ★★ **为什么当时看不出来：日志只打 `tabs()`（可见标签数，恒为 2），从不打 `items()`（真实配额）**。
  这两个数**差 2 个常驻幽灵 li**，而且真正卡上限的判据是 `items()`。
  只打可见标签 ⇒ 配额涨了也看不见 ⇒ 排查时误判"配额没问题"或"配额满了"都靠猜。
  ⇒ **凡涉及页签的日志必须两个数一起打**（已落地 `标签数 = N / 配额 = M`）。
- **判据**：**开完立刻数标签**，目标名不在 `tabs()` 里 = 没开。
  `active_tab()` 也必须是目标页（标签存在但未激活，同样会操作错页面）。
- **修法**（`src/steps.py` / `src/push_chain.py`，2026-10-29）：
  1. `open_menu_by_search()` 末尾轮询 6s 等目标标签出现；没出现 →
     `dismiss_quota_dialog()`（点「确定」关「上限15个」弹窗）→
     `release_tab_quota()`（`ctl.remove($(li))` 摘幽灵 li / 残留表单页）→
     `close_stray_windows()` + `dismiss_floating_hints()` → 重试 ≤2 次；
     仍不出现 **抛 RuntimeError**，绝不在旧页面继续。
  2. `step_invoice()` / `step_voucher()` 开列表后断言 `active_tab()` 是
     「应收单列表」/「销售增值税专用发票列表」，不是就 `activate_tab()`，切不过去即停。
  3. **配额释放从 run_batch 壳脚本下沉到 `steps.py`** —— 过去 `deep_clean` 只在
     `run_batch_*.py` 里，单跑 `push_chain.py` / `push_reminder` 时根本没有护栏，
     所以"上次修好了、这次又犯"。
- **口诀**：**凡"开页"必"数标签"**（同 凡"写"必"回读"）。

#### ★★★ SF-61 二次修正（2026-10-31 实测，推翻了上一版的"否掉配额"结论）

上一版说"别再归因配额满 15"——**方向对了一半：它不是每次开页失败的默认背锅侠，但它真的会发生，
而且一旦发生，前端把标签摘到 0 个也救不回来。**

- **现场**（本批 16 家，跑到第 14 家）：
  ```
  三环康泰：凭证已生成(209844)，写摘要时 RuntimeError: 等标签 '凭证 - 修改' 超时，当前='业务凭证列表'
  台州森恒：push_chain 失败 —— k-overlay 拦截 .kd-fullsearch-searchicon 的点击
  探针：tabs=0 / ctl.items()=0，弹窗文案「不能打开当前功能页签…上限15个」
  即使 items()=0，再点任何功能仍弹「上限15」 → 已开不了任何功能页
  ```
- **根因（两条，必须一起修）**：
  1. **页签计数有两个源**：`ctl.items()` 只是**前端 DOM 计数**，`ctl.remove()` 能把它减去，
     但减不掉**会话层那个"本会话已开过多少页签"的计数**。批量跑十几家后会话计数累到 15，
     即便界面标签数=0，金蝶照样拒绝开页。⇒ `ctl.remove()` 只当"界面卡死时的最后手段"，
     **不能当常规清场手段**（用它清得越欢，会话计数越虚高）。
     恢复的唯一办法：**重新登录**（登出→登录），会话计数归零。
  2. `dismiss_quota_dialog()` 的 `vis()` 原来用 `e.offsetParent !== null` 判可见 ——
     **`position:fixed` 的模态框 `offsetParent` 恒为 `null`** ⇒ 弹窗明明在屏幕上却永远判"不可见"
     ⇒ 返回 `[]`，弹窗关不掉，`k-overlay`（z-index 10058）长期盖住顶栏搜索图标，
     `locator.click` 报 `intercepts pointer events`。**已改为只看 rect + computed display/visibility/opacity**。
- **判据**：日志里出现 `标签数 = 0 / 配额 = 0` 但开页仍失败 ⇒ 不要再摘标签，**直接判定"会话页签计数满，
  需重新登录"**，别在 DOM 上反复折腾。
- **预防**：
  - 批量任务按 **8~10 家一个"登录窗口"** 规划；跑完一批就重登一次（成本 1 分钟，远小于卡死排查）。
  - 正常关页签**优先点 ✕**（走金蝶自己的关闭逻辑），`ctl.remove` 仅兜底。
  - 每批结束核对：`tabs()`、`ctl.items()`、能否开一个列表页 —— 三个都过才算干净。
- **不要做的事**：同 context **新开 page 访问首页 ≠ 新会话**（实测落到登录页，登录态在 sessionStorage
  不跨标签页共享）⇒ 想靠"开个新标签页"绕过计数是无效的，只会多一个登录页。

### SF-62 ★★ 「生成凭证」对话框偶发不弹 → 专票已审核的单**不要整链重推**

- **现象**：`push_chain.py` 走到 `点击菜单：生成凭证` 后抛
  `RuntimeError: 点「生成凭证」后没出现「凭证生成」对话框`，退出码 1，批次记 `ok=false`。
  这一步之前应收单、专票通常都已生成且**已审核**。
- **不是**页签配额问题（实测 `标签数=2 / 配额=4`，开页正常）；属按钮点击后对话框渲染慢/被前一个
  遮罩迟疑吞掉，**重试一次即成功**（2026-10-31 浙江广泉实测）。
- **正确处置（半成品续跑，禁止重跑 push_chain，否则专票推重）**：
  ```bash
  # ① 服务端确认专票存在且 FDocumentStatus=C
  #    IV_SALESIC：FBillNo like 'SVINV%' + FCUSTOMERID.FName like '%客户%'
  # ② 只补凭证（内部会开专票列表→勾选→凭证→生成）
  python src/gy3_4_voucher.py --inv <专票号> --go
  # ③ 报告页此时是新的「凭证生成报告列表」，趁热写摘要
  python src/write_digest.py --ar-no <应收单号> --company <客户> --go
  ```
- **收尾**：手工补做的单必须回写 `output/batch_*_result.json`（`ok=true` + `note`），
  否则下轮批次会把整条链再推一遍。

### SF-63 ★★★ 「金额对齐」是尾差工具，不是补数工具 —— 漏行会被它抹平（杰翙 AR-0018）

- **案例（2026-10-27 上海杰翙，AR105-202610-0018 / 专票 SVINV00034464 / 转100 FID 209196）**：
  1. 出库单筛出 **39 行 / 7 张单**，日志 `按单号勾选：命中 39 行……真实选中 38 行`，
     旧代码**只 WARN 就继续下推**（虚拟滚动截断 1 行 = SF-58 的出库单侧）；
  2. 应收单于是 **38 行、价税合计 148,351.50**（少 TLA8白浆 200kg / 2,400.00）；
  3. 当时还没有「来源出库单金额硬校验」（10-28 才加），直接进「金额对齐」：
     `[对齐第 1 轮] 价税合计 148,351.50(-2400.00)` → 锚点 seq=7 不含税 20,884.96 → 23,008.83；
     `[对齐第 2 轮] 150,751.50(+0.00)` → 日志报「对齐完成」，账面看起来完美；
  4. 结果：**单头金额与下推表分毫不差，明细数量少 200kg**，摘要也是 12,323kg（应为 12,523kg）。
- **自查口径（不依赖日志，随时可验）**：
  ```
  应收单 38 行 / 数量 12,323  →  Σ(数量×单价) = 131,284.51
  单头 FNOTAXAMOUNT = 133,408.41   ⇒ 差 2,123.90 = 被摊进去的那一行
  ```
  即 **Σ(数量×单价) ≠ 单头不含税 ⇒ 一定发生过「补数式对齐」**，必须查漏行。
- **三道防线（2026-10-31 已落地）**：
  | 位置 | 判据 | 动作 |
  |---|---|---|
  | `push_chain.select_outstock` 之后 | `真实选中 != 命中` | 表头全选重试 1 次，仍不等 → **raise**（不再只 WARN） |
  | `push_chain.step_receivable` | 金额精确相等（已有）；**新增**：明细行数 == 来源出库单明细行数、数量合计相等 | 不等 → **raise**，不保存 |
  | `steps.detail_align_totals(max_gap="1.00")` | 初始偏差 > 1.00 元 | **raise，一格都不改**（尾差只该是分位） |
- **口诀**：**金额对 ≠ 推进对**。凡"金额一分不差"的单，再核一遍 **行数 + 数量**；
  凡看到对齐日志里 `(-几百以上)` 的差额，基本就是漏行，不是舍入。

- **误报边界（2026-10-31 定，9-10 月 219 单实测）**：判据必须**跳过「单价 = 0」的行**。
  手工应收单 / 红字应收单 / 服务费行天生是「物料为空 + 单价 0 + 只有金额」，
  代入 `Σ(数量×单价) − 折扣 − Σ行金额` 必然算出 `gap = −金额`，纯误报。
  9-10 月 9 条告警里 **5 条**全是这种：
  AR105-202609-0116（4,000）、AR105-202609-0117（8,000）、AR105-202610-0073（8,000）、
  AR105-202609-0002（红字 −1,717.76）、AR105-202610-0082（红字 +395）。
  过滤后 9 条 → **1 条**（只剩杰翙），另有 3 条 0.06~0.11 的折扣反算舍入。
  阈值分层：`|gap| > 0.20` = 实质可疑；`≤ 0.20` = 舍入级（实测合法反算最大 0.12）。

### SF-64 ★★★ 「单价没被联动」是前端实现赠送的运气 —— 别把自查建在这上面

- **问题（2026-10-31 用户提问）**：「要是 JS 改金额时金蝶跟着变，是不是就找不出来了？」
  问到根子上了。SF-63 能查出杰翙，靠的恰恰是**当前前端不反算单价**
  （`FPrice` / `FTaxPrice` 原封不动，只有金额被改）。那是实现细节，不是设计保证。
- **反例已经在库里**：`AR105-202608-0088`（南京济丰）的含税单价是
  `16.500232 / 12.700073 / 21.900076` —— 正是「行金额 ÷ 数量」反算出来的 6 位小数。
  说明**联动反算的路径真实存在过**（真人双击「不含税金额」就是这条路径，
  见 `docs/界面结构速查.md`），只是那张单差额只有 0.08~0.12，被当成舍入放过了。
- **一旦走联动反算，账面上完全自洽**：
  `Σ(数量×单价) = Σ行金额`、`单头金额 = 来源出库合计`，
  SF-63 的指纹（`Σ(数量×单价) ≠ Σ行金额`）**归零**，事后查不出来。
- **唯一还站得住的判据 = 与来源单据勾稽，而不是单据内部自洽**：
  ```
  逐行：来源出库含税单价 × 本行数量 − 本行折扣 ≈ 本行价税合计（容差 0.50）
  ```
  改金额（摊平）与改单价（联动反算）**两条路径都被这一条覆盖**。
  容差 0.50 是实测标定：合法反算单最大 0.12；真摊平 / 真改价是 2,400 / 3,000 量级，
  差两个数量级，不会混淆。
- **判据阶梯（越往下越难自动化）**：

  | 层 | 被改的是什么 | 能抓住它的判据 |
  |---|---|---|
  | ① | 只改行金额（当前 `detail_set_cell` 路径） | `Σ(数量×单价) ≠ Σ行金额`（SF-63）+ 逐行金额守恒 |
  | ② | 连单价一起反算（联动） | **逐行金额守恒（SF-64）唯一** |
  | ③ | 连来源出库单一起改 | 出库单变更记录 / 原始单据留档 |
  | ④ | 实物本身就不对 | 客户对账单、物流签收（无自动化） |

- **落地（2026-10-31）**：
  - `push_chain.verify_ar_line_amount()` —— 保存后逐行回读，不符**在推专票前 raise**；
  - `scan_row_mismatch.py` v4 —— 同一判据的事后版本，一次扫历史。
    **v4 的关键修正：量差按「全历史累计已推」算**，不是只算 `--month` 范围内推的量。
    只聚合当月会把「7 月出库、8 月推一半、9 月推另一半」误报成对不上
    （实测 `XSCKD-105-202607-0143`、`XSCKD-105-202608-0180` 全历史累计后余量为 0）。
    修正后输出改为三类：**超推**（应收 > 出库，真错）/ **余量未推**（应收 < 出库，待推非错）/
    **行金额对不上**（SF-64）。行数差不参与判定 —— 一行出库拆成两行应收是合法的。
- **回测**：

  | 单 | 形态 | 结果 |
  |---|---|---|
  | AR-202610-0095 广源（48 行全带折扣） | 正常 | 48/48 通过 |
  | AR-202608-0088 南京济丰 | 折扣分摊 → 单价被反算（差 0.08~0.12） | 6/6 通过 |
  | AR-202607-0074 三环康泰 | 四色黑 19.5→16.5，改价 3,000 | 报出 1 行 |
  | AR-202610-0018 杰翙 | 漏行摊平 2,400（单价未变） | 报出 1 行 |

- **口诀**：**判据要建在「单据之间」的继承关系上，不要建在「单张单内部」的算术自洽上。**
  内部自洽只能证明「这单自己能解释自己」，证明不了「它和上游说的是同一件事」。

### SF-65 ★★★ 下推弹「单据转换网控提示」时，「继续」= 静默丢单（比报错危险一百倍）

- **案例（2026-10-31 杰翙重推，连撞三次）**：链路还原后重推 7 张出库单 / 39 行，
  点「下推 → 应收单 → 确定」后**没有出目标单据页**，页面浮着一个
  「单据转换网控提示」：
  ```
  “蒋一天”使用业务单据：“销售出库单”业务操作-“[销售出库单-XSCKD-105-202610-0032-修改]”冲突，请稍候再使用。
  部分单据存在用户冲突操作，继续将过滤被网络控制冲突占据的单据，转换携带剩余单据。
  ［继续］［取消］
  ```
  注意「继续」的准确含义：**把被占用的单据过滤掉，只转换剩下的** ——
  点下去就是 38 行，正好又少 `TLA8白浆 200kg / 2,400`，即 SF-63 那条错账的原样复现。
- **旧代码的偶然保护**：`activate_tab("应收单 - 新增")` 等不到标签 → raise，
  于是**没造成**错账。但报错信息是「切不到标签」，读日志的人根本看不出是网控冲突，
  容易被误判成「页签配额满」去清标签（浪费半小时）。
- **落地（2026-10-31）**：`steps.KingdeeUI.check_transfer_netlock()`，
  挂在 `push_to_receivable()` 里 `click_confirm()` 之后：
  1. 检测 `.k-window` 中标题含「单据转换网控提示」的可见弹窗；
  2. **只点 `a[id$="-FCANCEL_c"]`（取消）**，真实鼠标，绝不点 `-FCONTINUE_c`；
  3. 抛 `RuntimeError`，把**被占用单号 + 占用人 + 操作**原样带进异常文本。
- **怎么解**：网控锁挂在**服务端会话**上，本机页面关掉也不一定释放。按顺序：
  1. **退出登录 → 重新登录**（会话结束即释放该会话所有网控锁，同时顺便把页签配额归零）；
  2. 或 系统管理 → 网络控制 → 清除（别误清别人的锁）；
  3. 不要用「继续」绕过 —— 那是把错账重新做一遍。
- **口诀**：**下推后第一件事是看有没有网控提示，不是等标签。** 只要「命中行数 ≠ 实际转换行数」，
  就一定会在后面变成「金额对、数量错」。

### SF-66 ★★ 应收↔发票↔凭证三方对账的口径坑（2026-10-31 全量核查踩出）

- **`IV_SALESIC.FAllAmount` 是明细行金额，不是票面合计。** 只要 fieldKeys 里混进
  明细字段（`FSrcBillNo`/`FMaterialId.FName`），返回就是「一行一条」，`FAllAmount`
  变成该行金额；`top=1` 拿到的是第一行（实测一张 58 行的票，单头查只回 6120，实际 168,590.80）。
  ⇒ **票面 = 按 `FBillNo` 汇总全部明细行**；一张票可跨多张应收（按 `FSrcBillNo` 逐行分摊到各应收）。
- **凭证没有来源单字段。** `GL_VOUCHER` 试遍 `FSrcBillNo/FSourceBillNo/FVCHID/FVOUCHERID/
  FGLVCHID/FBUSINESSVOUCHERNO` 全部「字段不存在」，分录也没有客户辅助核算。
  ⇒ 只能按「账套 + 应收账款借方金额 + 摘要里的客户名」三元匹配，且**只对逐单做凭证的账套精确**。
- **★ 不同账套的凭证生成节奏不同，跨账套不能一刀切判「漏」。** 实测广东天龙油墨主账簿
  9-30 单日 211 行「内销客户销售收入（专票）」，是**月末一次性批量生成**；10-31 当晚只有
  8 行销售类分录 → 10 月销售凭证根本还没做。此时若按「查不到凭证=漏」会一次误报 300+ 张。
  ⇒ 判凭证前先看该账套**上月末**的销售凭证行数，确认是逐单做还是月末批做。
- **应收单-专票的硬锚点是 `IV_SALESIC.FSrcBillNo`**，这个可以放心精确匹配（本次 515 张
  全部匹配到，无空值）。

### SF-67 ★★ 专票漏行的连锁后果：凭证按票额入账 → 应收/收入同步少计（2026-10-31 杭州账套坐实）

- **现象**：应收单 60,926.48，专票只开 57,327.73（漏了两行物料），凭证 209182 的「应收账款」
  借方 = **57,327.73**，不是 60,926.48。即**凭证金额跟着票走**，票少开多少，账上应收就少挂多少。
- **判据**：按「应收单金额」去凭证池匹配会**全部 miss**，按「票面金额」匹配才对得上 ——
  这本身就是「凭证按票生成」的指纹。杭州账套 101 张单：92 张凭证全部「金额+客户」双命中，
  借方合计 5,386,916.82 与凭证池借方 92 行分毫不差，差额 1,692,537.38 可 100% 分解
  （6 张无票 1,628,023.13 + 8 张漏行 69,836.75 − 2 张红字 4,927.50 − 作废 395）。
- **推论**：查「票额不足」不能只写「少开票」，要一并说明**账务已按票额少计收入**，
  补开票后必须重新生成/调整凭证，否则应收单与总账永远差这一截。
- **漏行 ≠ 拆票**：判漏行前先查该客户当月**全部**专票（不限 `FSrcBillNo`），
  排除「剩余行另开一张票」。本次 8 家客户当月各只有 1 张票，才敢定性为漏行。
- **红字应收单**：`XSTHD` 退货红字（如 AR105-202610-0046 −4,851.50）可能既不开发票也不做凭证；
  判「漏」之前先看它的正数配对单金额是否已经扣掉这笔（本例 0050 = 83,583.50 正好等于票额，
  说明红字单是独立挂账，未参与冲减 → 应收多挂 4,851.50）。

### SF-68 ★★ `ExecuteBillQuery` 单次最多返回 10000 行，`TopRowCount` 调大无效

- 传 `top=30000` 仍只回 10000 行，且**按单据内部顺序取前 1 万条**（实测 9-11 月专票返回的是
  2023-06 ~ 2024-04 的历史数据，目标月份完全不在结果里 —— 比报错危险，因为「有数据」）。
- **对策**：按**日期分段**查（月/周），或加更窄的业务过滤（`FSrcBillNo LIKE 'AR105%'` 也要
  配合日期段，否则历史单据一样把 1 万行吃满）。查完必须打印「行数 + 日期范围」自检。

### SF-69 ★★★ 专票下推少行：护栏「口径错」+ 校验「只 warn」= 漏行一路存到凭证（2026-10-31 杭州 8 张）

- **现象**：杭州账套 10 月 101 张单里 8 张专票各少 1~2 行**整行物料**（0003 少 TLAZ8-BT白浆 60kg、
  156高抗磨剂 60kg；0014 少 POLO深蓝 1000kg…），合计少开 69,836.75；而
  `batch_20261031_result.json` 里这 8 家全是 `ok:true / chain_ok:true / digest_ok:true`。
- **不是「改日期/复核人」造成的**：`backfill_invoice_info()` 只写业务日期、发票日期、复核人三个
  表头字段，不碰明细行。漏行发生在**下推（选单→带出明细）**这一步。
- **根因 1：勾选护栏口径错了，等于空转。** `step_invoice` 里拿「应收单列表的选中行数 (real)」
  去比「应收单**明细**行数 (expected)」——列表里一张单只占 1 行，两者永远不该相等。
  若 `ExecuteBillQuery` 把混查字段去重成 1 行（SF-66），expected=1、real=1 → 护栏永远通过；
  若真返回明细行数 → 正常单也会被误拦。**已改成只卡 real < n（命中数）。**
- **根因 2：下推后只比「不含税 vs 批次目标」，且只 `log.warn`。** 漏行时票面合计必然对不上，
  但 warn 不拦，接着 backfill → save_bill → 生成凭证，账就跟着错了。
- **修复（本仓库 push_chain.py）**：新增 `_verify_invoice_vs_ar()` —— 下推后**保存前**用
  `ExecuteBillQuery` 回读应收单 `FAllAmountFor`，与专票明细价税合计比，差 > 0.05 **直接抛错、不保存**；
  不合并模式下再比行数（不等只 warn，因为 0 元/赠品行会被过滤）。
- **还没定论**：金蝶为什么少带这几行（勾选少行 / 按已开票数量过滤 / 重复物料去重）——
  需查专票明细 `FSrcEntryId` 反查应收单 `FEntryID`，见 `.tmp/hz_why_row.py`。
