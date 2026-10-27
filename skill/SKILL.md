---
name: kingdee-k3cloud-browser-rpa
description: 用 Playwright 接管已登录的 Chrome，操作金蝶云星空 8.x 网页端（Kendo UI）做单据自动化——下推、过滤、填字段、保存、提交、审核、生成凭证、写摘要、查服务端真实结果。当用户说「金蝶云星空」「k3cloud」「录单/开票/下推应收单/生成凭证」「ik3cloud」或需要在金蝶网页端做 RPA 时使用。含端到端整链 SOP、两条验收基线、红线清单（★不能删除凭证等禁止动作及代码级拦截 kd_guard.py）、60 条静默失败清单（现象→根因→修法）、字段与选择器总表、可直接拷走的 scripts/ 模块（kdcombo/过滤/保存判定/只读查询/全链核验 kd_verify/残留弹窗清场 kd_stray/摘要与折扣计算）。
agent_created: true
---

# 金蝶云星空 8.x 网页端 RPA

> 所有结论均为 2026-09-18 ~ 09-21 在真实站点（`https://<你的金蝶域名>.ik3cloud.com/k3cloud`，恒星版 8.2.0）实测得出。

## 资源导航

| 想做什么 | 看哪里 |
|---|---|
| **先看哪些动作绝对不能做**（红线） | 本文 **§〇** —— 首条：**不能删除凭证**；代码级拦截在 `scripts/kd_guard.py` |
| **标签点不动 / 删不掉** | **SF-40~42**；诊断 `scripts/kd_tab_probe.py`；**SF-51 定案：孤儿标签走 `kd_stray.force_remove_tab()`（主栏 widget = `kendomainTabControl`，不是 `kendoTabStrip`）即可摘除**；**切勿刷新页面（=登出）** |
| **搜到了却点不动**（`intercepts pointer events`） | **SF-49**；残留浮动提示条盖住搜索结果列表 → `scripts/kd_search.py`（JS `el.click()` 兜底），别用 `force=True` 绕 |
| **照着做跑通一单**（出库单 → 应收单 → 专票 → 凭证 → 摘要 → 审核） | `references/sop-end-to-end.md` |
| 遇到「脚本说成功但数据不对」 | `references/silent-failures.md`（49 条静默坑，按现象检索）+ 本文 §八 速查表 |
| 查字段名 / 选择器 / 菜单 id / FormId | `references/fields-and-selectors.md` |
| 应收单多出 `-00000N` 子单 | `references/sub-bills-00000N.md` |
| 改完脚本做回归对拍 | `references/acceptance-baseline.md`（**两条基线**：广源 2026-09 全字段 / 下推.xlsx 两家 2026-09 含 `omit_zero` 与并发环境） |
| 要一段能直接拷走的代码 | `scripts/`（**11 个**自包含模块，见 `scripts/README.md`） |
| **跑完整链后怎么确认真的成了** | `scripts/kd_verify.py`（一条命令出 ✓/✗ 结论，退出码 0/1） |
| **点击全被拦 / 标签点不动** | `scripts/kd_stray.py --dump` 先看是谁的窗，再 `--close` |

## 快速上手

```bash
# 1) 用调试端口开 Chrome 并**人工登录**（金蝶有验证码，别脚本登录）
chrome.exe --remote-debugging-port=9222 --user-data-dir=D:\kd-profile
curl -s http://127.0.0.1:9222/json/version        # 能返回 JSON = 浏览器在

# 2) 纯计算模块零依赖，可直接自测
python scripts/digest_rule.py        # 摘要口径 x/y/z/s
python scripts/discount_rule.py      # 扣款分摊 + 尾差落最大行
```

```python
# 3) 接管页面 → 只读核对（零副作用）→ 再动手
from playwright.sync_api import sync_playwright
from kd_query import KdQuery                     # scripts/
with sync_playwright() as p:
    page = p.chromium.connect_over_cdp("http://127.0.0.1:9222").contexts[0].pages[0]
    kq = KdQuery(page)
    print(kq.ar_head("AR105-202609-0005"))
    print("子单数 =", len(kq.ar_children("AR105-202609-0005")))   # 期望 0
```

> **一条铁律**：任何"保存/提交/审核"之后，判定成败一律走
> **查询回读（权威）> 界面 FBILLNO 非空 > 无弹窗**，别信"没抛异常"。

## 〇、★ 红线：绝对禁止的动作（不可放行）

> 下面这些**不是开关**：改 config、加 `--yes`、传任何参数都放不开。
> 代码级硬拦截见 `scripts/kd_guard.py`（`FORBIDDEN_ACTIONS`）与项目侧
> `src/common.py` 的 `Guard.FORBIDDEN`；点菜单前用 `Guard.assert_click_safe(文案)` 过一道体检。

### 1. ★★ 不能删除凭证（用户 2026-09-21 定，最高优先级）

| | |
|---|---|
| **禁止什么** | 点凭证列表的「删除」、在「凭证 - 修改」页删空凭证、对凭证调用任何 delete 类操作 |
| **为什么** | 凭证是「单据 → 专票 → 凭证」这条链路的**终态与唯一账务留痕**。删掉它，来源单据与总账的对应关系就断了，总账 / 报表 / 税务口径同时失真；金蝶**没有回收站**，删除不可逆，只能回滚数据库备份 |
| **更危险的点** | 删除是"一路通畅"的动作：**没有必录项校验、不弹错、kdsvc 正常返回**。脚本极易把「删除」当普通菜单项顺手点掉 —— **不报错但账乱了**（这正是本技能最怕的那类失败） |
| **✅ 改用** | 改摘要 / 改分录 → 「凭证 - 修改」页改完保存；想让它不进账 → 反审核或作废（**作废留痕，删除不留**）；真想重来 → 先**人工**确认，再走"反审核 → 修改" |
| **代码拦截** | `Guard.check("delete_voucher")` 永远抛 `GuardError`，**即使 config 里所有开关都为 true** |

```python
# 脚本里正确写法：删除类动作一律先过闸门（永远抛，用于"绝不允许走到这一步"的断言）
from kd_guard import assert_allowed, assert_click_safe, SafetyGuard   # scripts/

assert_allowed("delete_voucher")        # ★ 无条件抛 GuardError
g = SafetyGuard(cfg.get("safety", {}))
g.check("delete_voucher")               # ★ 同样抛，与开关无关

# 点菜单/按钮之前做一次文案体检（防止脚本顺手点到危险项）
assert_click_safe("删除凭证")            # 抛
assert_click_safe("保存")                # 放行
```

> ⚠️ 注意 `assert_click_safe` **只拦精确危险词**（`删除凭证` / `作废凭证` / `凭证删除`），
> 不拦单独的「删除」——因为过滤对话框的「删除行 / 全部删除」是合法日常操作。
> 判定语义是"这句话整体是针对凭证的删除"，不是"出现删除两个字就拦"。

### 2. 其他同样属"不可逆、须人工"的动作

| 动作 | 口径 |
|---|---|
| 删除已审核单据 / 先反审核再删 | 必须人工确认；脚本只做到"反审核"就停 |
| 作废凭证 / 作废发票 | 留痕但仍不可逆，**人工来点**，脚本不代劳 |
| 反审核已生成凭证的单据 | 会连带影响凭证，先查清关联再动 |

> 与 A 类开关式动作（提交 / 审核 / 生成凭证 / 保存凭证）的区别：
> 那四个**可以**靠 config 放行（默认 false），本节这些**永远不行**。


## 一、架构：CDP 接管，不要重新登录

金蝶有登录态 + 短信/验证码，**别用脚本登录**。让用户用调试端口开一个 Chrome 并人工登录，脚本接管：

```bat
chrome.exe --remote-debugging-port=9222 --user-data-dir=D:\kd-profile
```

```python
p.chromium.connect_over_cdp("http://127.0.0.1:9222")
pages = br.contexts[0].pages      # 取第一个 page（金蝶是 SPA，全程一个标签页）
```

前置自检：`curl -s http://127.0.0.1:9222/json/version`，能返回 JSON 说明浏览器在。

## 二、DOM 规律（Kendo UI + 自绘 kd-*）

### 1. 单文档，无 iframe
列表页/单据页都在同一个 document 里切换。但保留 `eval_all_frames` 兜底。

### 2. 控件 id
```
{pageId(GUID)}-{FIELD}-{后缀}
文本框/日期：  -{FIELD}-EDITOR_c      ← 注意小写 _c
下拉(combo)：  -{FIELD}-EDITOR        ← 没有 _c（如 F_PAEZ_COMBO）
```
`FIELD` 是金蝶内部字段名：`FBILLNO / FDATE(业务日期) / FDate1(账务日期，列表过滤用) /
FINVOICEDATE / FDRAWER(开票人) / FPAYEE(收款人) / F_PAEZ_COMBO(复核人)`。

### 3. ⚠️ 必须先叠可见性过滤
同一 document 里**同时挂着多个 pageId 的 DOM**（开过的页面都留着）。所有定位都要：

```js
const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
  return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'; };
```

### 4. ⚠️⚠️ 工具栏按钮：隐藏页面的同名按钮会吃掉点击
`保存/提交/审核` 在所有 pageId 的工具栏里文本完全一样。只按文本取第一个 →
**点到隐藏页面的按钮，静默失败，界面毫无变化，截图也看不出来**（曾因此白排查 1 小时）。

```js
// 错误：.find(b => b.textContent.trim()==='保存')
// 正确：可见 + offsetParent 非空，并优先单据工具栏（避免点到筛选栏的「保存方案」）
const cands = [...document.querySelectorAll('.kdmenuitem-content')]
    .filter(e => e.textContent.trim() === t && vis(e) && e.offsetParent !== null);
const btn = cands.find(e => e.id.includes('BILLMENU_TOOLBAR')) || cands[0];
```
点击用 **Playwright 真实鼠标点击** `page.locator(f'[id="{btn.id}"]').click()`，JS `el.click()` 只作兜底。

## 三、各类控件的正确操作方式

| 控件 | 操作 |
|---|---|
| 文本框 | `.value=...` + 派发 `input/change/keydown/keyup/blur` 事件序列 |
| **日期** | **必须**走 Kendo API：`jQuery(el).data('kendoDatePicker').value(new Date('2026/9/30')); .trigger('change')`。直接改 value 不同步模型 |
| **下拉(combo)** | **必须**走 `jQuery(inp).data('kendoDropDownList').open()`，再点 `.k-list-container/.k-animation-container` 里的 `li`。**合成 MouseEvent 点 `.k-select` 打不开**（Kendo 认真实输入） |
| 复选框 | `input` 带 `onclick="return false"` → 必须向 `label.k-checkbox-label` 派发 `mousedown+mouseup+click` |
| 过滤条件行 | 金蝶**没有「介于」**，用 `>=`(比较符值 39) + `<=`(值 16) 两行 |
| 弹窗「确定」 | ⚠️ **必须真实鼠标点** `page.mouse.click(x,y)`（先算按钮中心坐标）。合成 `el.dispatchEvent(new MouseEvent('click'))` **完全不触发** —— 对话框点了不关（下推对话框实测）。用对话框 innerText 关键字限定（如 '整单转换' / '销售专用发票'）防点到隐藏页同名按钮 |

## 四、★唯一可靠的成败判据：解码 kdsvc 响应

金蝶所有动作都 POST 到同一个地址：

```
/k3cloud/Kingdee.BOS.ServiceFacade.ServicesStub.DynamicForm.DynamicFormService.Call.common.kdsvc
```

请求 `ap0/ap1/ap2/ap3` 与响应体都是 **gzip + base64(+urlencode)**。响应解码后是 JSON 数组：

```json
[{"actionname":"setFormTitle","params":[[{"Key":2052,"Value":"…- 修改"}]]},
 {"actionname":"showConfirm","params":[{"msg":"…保存成功！"}]},
 {"actionname":"UpdateMainMenuStates","params":[{"key":"tbSplitSave","disabled":false}]}]
```

```python
import base64, gzip, urllib.parse
def kd_decode(s):
    try:
        return gzip.decompress(base64.b64decode(urllib.parse.unquote(s))).decode("utf-8","replace")
    except Exception:
        return str(s)

def on_resp(resp):
    if "kdsvc" not in resp.url.lower(): return
    for a in json.loads(kd_decode(resp.text()) or "[]"):
        if a.get("actionname") == "showConfirm":
            print(a["params"][0]["msg"])     # "…，保存成功！" / "…，审核成功！"
```

**为什么必须看它**：金蝶「保存成功」提示一闪而过，界面可能毫无变化，截图抓不到。
（真实教训：保存/提交"看起来没反应"，实际是点到了隐藏页面按钮；换成看服务端返回立刻定位。）

判据词：成功/已提交/已审核 → ok；失败/不能为空/不允许/校验不通过 → bad。

常见服务端消息语义：
- `保存成功！` → 已落库（标题可能仍显示"…- 修改"，正常）
- `提交` 的返回可能是 **`审核成功！`** → 该组织流程是「提交即审核」，单据直接变已审核态，
  此时**不要再点「审核」**（会报"单据已审核"）

**★ 本账套实测的「提交是否连带审核」三档（2026-09-21）**：

| 单据 | 触发动作 | 服务端返回 | 是否已审核 |
|---|---|---|---|
| 应收单 | **保存** | `审核成功！保存成功！` | ✅ 保存即审核 |
| 销售增值税专用发票 | **提交** | `审核成功！` | ✅ 提交即审核 |
| 总账凭证 | **提交** | `提交成功` | ❌ 审核要另点（工具栏「审核」才由灰转可用） |

⚠️ 别默认"提交=审核"套用到所有单据，也别默认"保存=暂存"套用到应收单——按上表逐类确认。

## 五、常用流程要点

**列表下推**：搜索框 `input[placeholder="请选择内容后回车查询"]` → 勾选行（见复选框规则，
真实选中数读分页栏「已选 N 行」，DOM 只渲染当前页）→ 工具栏「下推」→ 对话框选目标单据
（radio label，id 后缀 `-{FormId}-EDITOR`）→ 转换规则在**下拉**里选（如"应收单-销售专用发票-分录合并"）
→ `FBTNOK_c` 确定。
⚠️ 下推对话框里的下拉列表**只存活于同一次 evaluate**：打开下拉、选 li、点确定必须在**同一个 JS**里做完，
否则下拉已关闭 → 会静默用默认值（默认往往是"不合并"）。
（更稳的做法见下面「Kendo 下拉必须走 API」。）

**凭证生成** —— ⚠️ 只能从**列表页**选中单据后生成，**详情页没有入口**；且要选对来源单据：
```
发票列表 → 勾发票 → 工具栏「凭证」(split, id 尾 tbSplitButtonVch) → 下拉「生成凭证」
       → 「凭证生成」对话框【必须先勾账簿行】→ a[id$="-FVOUCHERGENE_c"]
       → 自动弹「凭证生成报告列表」，逐行读 检查项/提示类型/提示信息 判成败
```
- 不勾账簿行时点生成，服务端只回 `ShowWarnningMsg`「请选择账簿！」，界面看着像没反应。
- 来源单据**必须与账套凭证模板匹配**。实测本账套：从**销售增值税专用发票列表**生成 ✅；
  从**应收单列表**生成 ❌（报「模版编码 AR_receivable 第2行…取值为0」，属账套模板/核算配置）。
  遇到"生成失败"先换来源单据试，别急着改脚本。

**改凭证摘要** —— 唯一可写页面是标签名 **「凭证 - 修改」**：
```
（凭证生成报告列表 / 业务凭证列表）→ 勾行 →「查看总账凭证」→ 标签「凭证 - 修改」
```
- 「业务凭证 - 查看」（业务凭证自己的详情页）**只读**：摘要格 class 带
  `k-gridCellstate-disabled`，单击 / F2 / 直接键入都进不了编辑态；在这页按 **Esc 会直接关掉整个页面**。
- 「凭证 - 修改」页：双击摘要格 → `input[id$="-FENTITY_FEXPLANATION_c"]` → `fill` + `Enter` → 工具栏「保存」。
- 分录 `span[data-field]`：`FENTRYSEQ` 序号 / **`FEXPLANATION` 摘要** / `FACCOUNTID` 科目编码 /
  `FACCTFULLNAME` 科目全名 / `FDETAILID` 核算维度 / `FDEBIT` 借方 / `FCREDIT` 贷方。
- ⚠️ **单头字段（账簿/凭证字/凭证号/期间）不在 `span[data-field]` 里**，只能抓外层容器 innerText。

**凭证提交 —— 看工具栏禁用态判断状态（凭证 ≠ 专票，提交不连带审核）**
| 单据 | 点「提交」后服务端原文 | 连带审核 |
|---|---|---|
| 销售增值税专用发票 | 「审核成功」 | ✅ 提交即审核 |
| 总账凭证（凭证 - 修改页） | 「提交成功」 | ❌ 审核要另点 |

「凭证 - 修改」页工具栏四按钮的可用性 = 单据状态，这比读字段可靠：

| 状态 | 保存 | 提交 | 审核 |
|---|---|---|---|
| 暂存（已生成未提交） | 可用 | **可用** | **灰** |
| 已提交未审核 | 灰 | 灰 | **可用** |

- `提交=灰` → 已提交过，**直接跳过别重复点**；点完要**轮询 1~3s** 等
  kdsvc 的 `UpdateMainMenuStates` / `UpdateControlStates` 把禁用态翻过来，立刻读会读到旧值。
- 「业务凭证列表」**没有提交状态列**，列是：编码 / 是否有手工修改 / 账簿 / 日期 / 凭证字 /
  对应总账凭证号 / 借方总金额 / 贷方总金额 / 来源单据 / 来源单据号 / 异常凭证 / 作废状态 / 业务日期。
  「是否有手工修改」可佐证摘要有没有被手工改过。

## 五之二、三个通用技术要点（都会静默出错）

**Kendo 下拉必须走 API，别合成鼠标事件**
```js
const inp = box.querySelector('input[data-role=dropdownlist]');
const w = window.jQuery(inp).data('kendoDropDownList');
const idx = w.dataSource.view().findIndex(d => (d.name || d.value) === want);
w.select(idx); w.trigger('change');     // 只用真实鼠标点 arrow 会在下次调用时失效
```
⚠️ 切换 radio / 触发联动后，下拉是**异步重建**的，要等 ~2.5–3s 再读 `dataSource.view()`，否则拿到空列表。
⚠️ 同一字段常有**一份可见 + 一份隐藏**的控件（旧页面残留），先按可见性挑。

**读列表必须「列严格对齐」**
`td.innerText` 数组**不要** `filter(Boolean)` —— 只要某行有一格为空，整行就往前错位。
（发票列表的「发票号」常年为空，会把「发票日期」读成「开票方式」的值。）
用表头 `th` 与 `td` 同下标配对，空列保留为 `''`。

**切页/勾选是异步 + 切换语义**
- 点按钮切页后用「当前激活标签名」轮询确认，别立即查 DOM。
- 列表勾选是**切换**：重复勾会取消上一次。批量前先清空勾选（表头可能需要点**两次**才归零），
  并核对真实选中数再执行批量动作。

## 五之三、标签页：开得越多越容易出错（清场机制）

金蝶 SPA 把每个**开过的页面 DOM 都留在 document 里**，标签越堆越多，
`document.querySelector*` 命中隐藏页面元素的概率就越高（工具栏按钮 / 列表 grid /
快捷过滤面板 / 分页栏全都栽过）。**口径：下推链最后一步（生成凭证）做完后清标签，
只留「销售出库单列表」**。

★ **开工前也要清一次（比收工后那次更重要）——这是 SF-55 的根治手段**：
后台残留的「XX - 新增」草稿页与前台当前单**字段 id 完全相同**，只要它们还在 DOM 里，
按 `[id$=...]` / 弱可见性定位就有概率写到**别人身上**（FDATE 落成"今天"、单号年月错成 `-202610-`）。
实测：**同一套代码，脏环境判据拦下、清干净后 2/2 一次通过**。
```bash
python kd_stray.py --close-tabs --keep 销售出库单列表   # 含孤儿 li 的 widget 摘除
```
判"脏"：`steps.tabs()` 里出现**多个同名**「XX - 新增」。


**★★ `.k-item` 不只匹配标签页！** 工具栏的每个菜单项（新增/保存/提交…）也是
`li.k-item.kdmenuitem`，`querySelectorAll('.k-item')` 会捞出 20+ 个假标签。

| 对象 | 选择器 |
|---|---|
| 真标签页 | `ul.k-tabstrip-items > li.k-item.contentHeadEL` |
| **主标签栏本体** | **`ul.k-tabstrip-items.kdMainTabsHeaderExtend`**（专属 class，认它才不会捞到表单内的页组） |
| 主栏的 widget | **`$(strip).closest('.k-widget').data('kendomainTabControl')`** ★ **不是** `kendoTabStrip` |
| 关闭按钮 | 标签内 `span.k-i-close.mainTabCloseButton` |
| 标签本体（切页） | 标签 li 的左侧文字区（真实鼠标点） |

- 关标签用**真实鼠标**点关闭按钮坐标（更稳：`locator.click()`，等位置稳定再算坐标，
  见 SF-49）；关一个位置就变 → **每轮重新取标签列表**，
  先关非激活的（从右往左），非保留的激活标签最后关，关完把保留标签**切回前台**。
- ★ **"孤儿标签关不掉"已不成立（SF-51 定案）**：✕ 对已卸载面板的孤儿节点静默无响应
  （六种点法全无效），但 **`ctl.remove($(li))` 能摘** —— 主栏是金蝶自研的
  Kendo TabStrip 子类，注册名 `kendomainTabControl`。落地在
  `kd_stray.force_remove_tab()` / `steps._force_remove_tab()`：
  **先 `locator.click()` → 回读判定 → 失败就 widget 摘除 → 仍失败才记黑名单**。
  ⚠️ `ctl.items()` 恒定比真实标签数**多 2**（两个零宽幽灵 `li`），估配额以 `tabs()` 为准。
- **从顶部搜索打开功能是「替换当前标签」不是新增**（连开 3 个功能后仍只有 1 个标签）。
  所以「保留出库单列表」= 关掉其它 → 若它已不在 → **重新打开一个**。
- **⚠️ 勾选是「点表头复选框」的切换语义**：本来没勾选时点一下会变成「全选当前页」；
  表头状态与真实选中数还会不同步（实测首屏曾残留 **555 行**选中，可能要点两次才归零）。
  调 `clear_selection()` 前先读 `selected_count()`，为 0 就别点。
- 有些功能页**一打开就弹一个 `.k-window`「过滤」对话框**（实测：凭证查询），
  它会浮在上面把后续所有真实鼠标点击拦掉，报错形如
  `<a aria-label="Close"> … intercepts pointer events`。清场时顺带关掉
  （点 `[aria-label="Close"]`；弹窗文案命中「关闭/放弃/未保存」时才点「确定」）。

## 五之四、★顶部全功能搜索：在输入框里打字根本不触发搜索

实测：`type()` 期间 **0 个网络请求**，面板一直显示「最近搜索历史」；
按 Enter 只是关掉面板（发 5~7 个 kdsvc 请求，但不打开任何功能）。

**搜索只在「面板被打开的那一瞬间」发起**，用的是输入框里已有的值。正确流程：
```
Esc 关面板 → 点图标开面板 → 清空 + type(关键字) → ★Esc 关掉 → 再点图标重开
                                                    ↑ 这一步才真正去搜
→ 结果条目 .kd-fullsearch-item-container → 点中目标
```
| 元素 | 选择器 |
|---|---|
| 搜索图标 | `.kd-fullsearch-searchicon`（**面板打开时它被隐藏**，所以先 Esc） |
| 输入框 | `input.kd-appplat-searchinput`（placeholder「请输入内容进行搜索」） |
| 结果面板/条目 | `.kdfullsearchpopextend` / `.kd-fullsearch-item-container` |

**★ 重名必须用「菜单路径」消歧**：同名功能常有 2~3 条。实测 `销售出库单列表` 有 3 条
（供应链>销售管理>出货处理 ✅ / 供应链>库存管理>销售出入库 / 电商与分销>电商集成），
**正确的是「供应链>销售管理>出货处理」**。代码里固化成 `KingdeeUI.MENU_PATH_HINT`；
没提示且多条同名结果时**直接报错**，不许猜。

**★★ 2026-09-22 补两条（都会卡死或静默走错）**
1. **结果条目 `locator.click()` 会被折叠容器拦掉** → 报
   `<div class="kd-form-collapse-container"> … intercepts pointer events` 然后超时。
   必须走**真实鼠标**：先 `js_pos` 取条目中心坐标，再用 `elementFromPoint` 做**命中检测**
   （确认点上就是目标条目，不是被浮层盖住），最后 `page.mouse.click(x, y)`。
   坐标在视口外（实测 `y=-32`）时先 `scrollIntoView({block:'start'})` 再取。
2. **目标列表已经开着就直接激活，别重复搜**：重复搜会新开第二个同名标签，
   后续 `activate_tab` / 详情页操作就落到错的那个（对应 SF-29）。
   `open_menu_by_search()` 开头应先 `tabs()` 找有没有名字已命中的标签，有就 `activate_tab` 直接返回。
3. **搜之前先关掉占用焦点的同名标签**（`src/gy_closetab.py`），否则搜索面板点不开或结果点不中。

## 五之五、下推链三处静默坑（整链跑通后固化，2026-09-21）

**① 列表是虚拟滚动，DOM tbody 永远只有 ~200 行 —— 别从 DOM 读全量**
1067 行的出库单列表，`tbody tr` 只渲染前 200。要拿全量必须读**数据源**：
```js
const g = jQuery(gridEl).data('kendoKDGrid') || jQuery(gridEl).data('kendoGrid');
const rows = g.dataSource.data().map(d => d.toJSON ? d.toJSON() : d);   // 全量
```
数据源字段名 ≠ 表头文案，实测出库单列表：`FBILLNO/FCUSTOMERID_FNAME/FDATE1/FREALQTY/FARJOINQTY`。
固化成 `KingdeeUI.grid_all_rows()`。

**② 过滤条件：客户字段塞文本无效 → 改用「单据编号 + 多行或者」精确命中**
- 条件行值单元格定位**先按 id 含 `ppart_value`**（大小写混用，转小写再比），
  按 `[data-role=...]` 找会先命中「字段列」的 combobox → 值写不进去、过滤静默失效（界面仍是全部行）。
- `FCustomerID.FName` 是 **bosf7field 基础资料选择器**（值控件不是普通 input），
  直接塞文本**静默无效**。要筛指定单据时，用**单据编号** `FBillNo`（textfield，可直接写值）
  + **多行「或者」** 精确命中（比较符 包含=17 / 等于=67；行间逻辑列 `_row_Add_PART_Logic`
  value「0」=并且 /「1」=或者）。固化成 `KingdeeUI.set_bill_no_filter(bill_nos)`。
- 改完**必须回读过滤后的行数**确认生效，不要"点了确定就当过滤成功"。

**③ 生成凭证前，先关掉占用该单据的「修改」标签，否则报"正在被使用"**
实测报错：`该业务单据正在被蒋一天使用，本次不生成凭证` —— 原因是
「应收单 - 新增」「销售增值税专用发票 - 修改」两个标签页仍开着，把单据锁住了。
**关掉这些标签后重新生成 → 一次成功**（BizVch1352635 / 转66）。
→ 凭证生成前先 `close_all_tabs(keep_names=["销售出库单列表"])` 或至少关掉源单据的详情/修改页。

## 五之六、明细单元格编辑 + 关闭单据（2026-09-21 实证）

**编辑明细网格单元格**（应收单/发票等的分录行）：
```
双击单元格 → td 变 class="k-state-focused k-edit-cell kd-edit-cell"
          → 出现编辑器 input.k-input.kd-editor-input-bound（数字列 data-role=kdnumberfield）
Ctrl+A → Delete → type(值) → Enter        # Enter 提交并触发服务端重算
```
⚠️ **编辑后其它联动列会短暂显示为空**（例：改折扣额后「税额」格空白）——
必须**点同行的另一个非编辑列（如「物料名称」）移开焦点**，再读值，否则读到假空。
⚠️ 重算是**服务端异步**的，Enter 后等 **1.5~2.5s**。

**金额字段的联动方向（很关键，实测应收单）**：
| 改哪个字段 | 结果 |
|---|---|
| `FDISCOUNTAMOUNTFOR` 折扣额 | 价税合计 = 含税单价×数量 − 折扣额；再算 不含税 = round(价税合计/(1+税率),2)；税额 = 价税合计 − 不含税 |
| `FNOTAXAMOUNTFOR_D` 不含税金额 | 价税合计 = 不含税 + 税额（税额保持不变） |
| `FTAXAMOUNTFOR_D` 税额 | 价税合计 = 不含税 + 税额（不含税保持不变） |
→ **价税合计只受「折扣额 / 含税单价」控制**；想让 不含税/税额/价税合计 三者同时精确命中目标，
  先调**折扣额**把价税合计顶到目标，再用**不含税/税额**做分厘级微调。
（分摊扣款时：各行折扣按比例四舍五入会产生几分尾差，**把尾差补到金额最大的那一行**。）

**关闭单据标签（丢弃未保存改动）**：
1. **✕ 必须先激活该标签再点**：点未激活标签的 ✕ **只会切换过去、不会关闭**
   （表现成"点了没反应"，反复点会原地打转）。
   正确：`点标签本体(tx,ty) → 等 1.5s → 重新取标签列表(tabs 坐标会变) → 点该标签的 cx,cy`。
2. **确认框不是 `.k-window`！** 是**内嵌警告条** `.kd-el-popmessage.kd-message-question`
   （id 尾 `-POPFLOATEMESSAGE`），文案两种：
   `内容已经修改，是否保存？` / `关闭页签会丢失尚未保存的下推生成数据。想要保存单据吗？`
   按钮是 `<button class="k-button">`：**是 / 否 / 取消** → 丢弃就点「否」。
   （用 `.k-window` 找确认框会一无所获，这是"关闭没反应"的真因。）
3. **同名标签**（两张都叫「应收单 - 新增」）：`activate_tab(名字)` 只命中第一个，
   必须按**序号**：`tabs()[i]` → `tx/ty` 点击 → 重取 → 点 `cx/cy`。
4. 列表**不会自动刷新**刚保存的单据 → 要 `click_menu("刷新")`。

## 五之七、整链第二段：应收单 →（下推）专票 →（生成）凭证 → 提交（2026-09-21 两批实证）

### 字段名：专票 ≠ 应收单（极易写错，读完恒为 0）
| 含义 | 应收单（明细分录） | 销售增值税专用发票（明细分录） |
|---|---|---|
| 不含税金额 | `FNOTAXAMOUNTFOR_D` | `FNOTAXAMOUNT` |
| 税额 | `FTAXAMOUNTFOR_D` | **`FDETAILTAXAMOUNT`** |
| 价税合计 | `FALLAMOUNTFOR_D` | `FALLAMOUNT` |
| 折扣额 | `FDISCOUNTAMOUNTFOR` | `FDISCOUNTAMOUNTFOR` |

⚠️ 用应收单的字段名去读专票 → 合计恒为 0，会误判成"专票没金额"。

### 从「应收单列表」下推专票
```
open_receivable_list()                       # 打开列表（会替换当前标签）
set_bill_no_filter(["AR..."])                # 单据编号 包含 精确筛 1 张
clear_selection() if selected_count() else -  # ⚠️ 表头复选框是切换语义
select_all_rows()
push_to_invoice(merge=True)                  # TARGET_INVOICE=IV_SALESIC
                                             # RULE_MERGE="应收单 - 销售专用发票 - 分录合并"
```
- 列表是**单据级**（`FALLAMOUNTFOR` 是单据总额，没有 `_D` 明细字段）。
- 分录合并后**金额合计不变**（按金额加权合并），所以应收单调平后专票天然对上，不用再调。
- 回填：业务日期 `FDATE` / 发票日期 `FINVOICEDATE` / 开票人 `FDRAWER`（默认登录人）/
  复核人 `F_PAEZ_COMBO`（kdcombo，用 `fill_combo`；读回要用 `read_combo`，
  普通 `[id$="-EDITOR_c"]` 读不到它的值）。发票号不填。
  ⚠️ **复核人是必录项，不填保存会静默失败**（弹「字段必录提示」但脚本不报错）——
  详见 **五之十一 ① ②**。

### 生成凭证（路径唯一，别猜）
```
专票列表 → 勾选中标发票 → click_menu("凭证")(split, id 尾 tbSplitButtonVch)
  → click_menu("生成凭证")(id 尾 tbVoucherGenerate)
  → 「凭证生成」对话框 → check_voucher_book_row()  ★不勾会报「请选择账簿！」
  → 点生成按钮 a[id$="-FVOUCHERGENE_c"]（Playwright locator.click 有效）
  → 自动打开「凭证生成报告列表」→ **先 `activate_tab` 再** `read_voucher_report()` / `voucher_report_errors()`
     （不切标签就读到背后的列表，报告行数恒 0）
```
⚠️ **生成前必须先关掉占着该单据的「- 修改」页**，否则报
「该业务单据正在被 XXX 使用，本次不生成凭证」。
⚠️ **源单必须已审核**，否则对话框红字「所选单据未全部审核!」且**没有报告**——
此时去读"第一个可见 grid"会读到弹窗背后的列表、误判成"0 错误生成成功"（详见 **五之十一 ⑦**）。
⚠️ `activate_tab()` 是**包含匹配**：`activate_tab("销售增值税专用发票")` 会命中
「销售增值税专用发票 - 修改」页，别指望精确匹配。

### 打开总账凭证（改摘要/提交都在这一页）
```
凭证生成报告列表 → 勾行 → click_menu("查看业务凭证")(tbBizvchView) → 业务凭证列表
  → 勾行 → click_menu("查看总账凭证")(tbGlvchView) → ★标签「凭证 - 修改」
```

### 提交判据：kdsvc 可能为空 → 允许以界面为准
- 凭证提交：kdsvc 回 `账簿"…","转"字,"47"号凭证，提交成功！`，且按钮翻转
  `提交 可用→禁用` / `审核 禁用→可用` —— 两条都对才算成功。
- 专票提交：**kdsvc 有时抓不到消息（`messages()` 为空）**，但页面出**提示条**
  「…销售增值税专用发票，审核成功！」且「单据状态=已审核」→ 视为成功。
  双判定要留"界面为准"这条路，不要因为没有服务端原文就判失败重做。
- 三种单据三种审核口径：应收单=保存即审核；专票=提交即审核；
  **凭证=提交≠审核**（提交后「审核」按钮才变可用，审核是独立一步）。

## 五之八、折扣分摊与金额对齐（可复用，2026-09-21 通用化）

### 业务口径（用户定，写死进代码）
含扣款的发票：出库单原始价税合计 A，实际开票少收一笔扣款 D。要求三步：
1. **折扣额按行分摊**：`该行折扣 = round(D × 该行含税 ÷ 各行含税合计, 2)`
   —— 权重用**含税金额**（因为 D 是价税合计口径），不是不含税。
2. **尾差落最大行**：逐行舍入后合计常差几分，把尾差补到**含税金额最大的那一行**的折扣额。
3. **对齐实际开票**：折扣后总不含税/总税额若与实际开票差在 0.1 以内，
   改**金额最大的那一行**的不含税金额和税额把两个合计顶到目标值。

### ★ 定价机理（决定操作顺序，务必先懂再做）
```
改「折扣额」    → 价税合计 = 含税单价×数量 − 折扣额，再反算 不含税/税额
改「不含税金额」→ 价税合计 = 不含税 + 税额（税额不动）
改「税额」      → 价税合计 = 不含税 + 税额（不含税不动）
```
⇒ **价税合计只能靠折扣额/含税单价精确控制**；不含税与税额是分厘级微调旋钮。
⇒ 顺序必须：先折扣额定住价税合计 → 再不含税 → 最后税额，三步互不破坏。
⇒⚠️ 金蝶的「不含税」**不是**简单 `折后含税 ÷ (1+税率)`，它走**单价反算**，差 1~2 分。
  所以对齐**只能用页面读数闭环收敛**（读实际合计 → 算差 → 写 → 再读），
  **不要用公式离线推算目标值**，否则越算越偏。

### 代码位置（换客户只换参数，不要再写一次性脚本）
- `src/discount.py` —— 纯计算，无浏览器依赖：
  `allocate_by_amount(amounts, 扣款)` / `pick_anchor_row` / `plan_alignment` / `summarize`
  / **`plan_material_align(rows)`**（按物料掰平，避免分币子单——见五之十）
- `src/steps.py` —— `KingdeeUI` 上：
  - `detail_rows()` / `detail_amounts(kind)` 读明细（`kind="receivable"|"invoice"`）
  - `fill_discount_by_ratio(扣款)` ★ 分摊 + 填入
  - `detail_align_totals(target_notax, target_tax)` ★ 闭环对齐
  - `detail_set_cell(idx, field, value)` / `detail_blur()` 单元格读写
  - `amount_fields(kind)` 取字段名（config.yaml 的 `amount_fields` 可覆盖）
- `tests/test_discount.py` —— 用广源那单的真实 46 行做**回归测试**（18 项，改算法必跑）
- `src/push_chain.py` —— ★ **统一入口**，一条命令跑完整链（见下）
- `src/check_subbills.py` —— 只读核查某应收单名下 `-00000N` 子单数（避开子单的验收工具）

### 统一入口用法
```bash
# 只读验算（不下推、不保存）：看清分摊与对齐方案
python src/push_chain.py --customer 广源包装集团 --bill-nos 单号1,单号2 \
    --discount 2733.5 --target-notax 266824.88 --target-tax 34687.22 --dry-run

# 正式全链（--step voucher 是默认值；--step receivable/invoice 可分步停在中间）
python src/push_chain.py --customer 广源包装集团 \
    --date-from 2026-08-21 --date-to 2026-09-20 \
    --discount 2733.5 --target-notax 266824.88 --target-tax 34687.22 \
    --biz-date 2026-09-30 --invoice-date 2026-09-30 --yes

# ★ 不产生 -00000N 子单：下推专票用「分录不合并」
python src/push_chain.py ... --no-invoice-merge --yes
# ★ 对照实验：同一张已审核应收单，只重跑「下推专票」这一段
python src/push_chain.py --ar-no AR105-202609-0004 --step invoice --no-invoice-merge --yes
```
`run.py`（Excel 驱动、批量、可回写）与 `push_chain.py`（命令行驱动、临时一单）
共用同一套 `KingdeeUI` + `discount.py`。
`--no-invoice-merge` / `--ar-no` 见五之十「怎么不产生子单」。

### ★★ 三个"静默失败"的坑（2026-09-21 实测修复，都会不报错地做错事）
1. **过滤条件末行会被静默丢掉**：金蝶在 input 事件后**异步**把条件 commit 进内部模型。
   最后一行写完值**立刻点确定** → 那一行条件丢失 → 现象是「4 张单只筛出 3 张」，
   而日志里每行都显示"设置成功"。（前面的行没事，只因后面还有操作顺带等到了时间。）
   ⇒ 修法：新增行后等 450ms、点确定前再等一次，并 **`_read_condition_rows()` 回读自检**。
   ⚠️ 回读时值输入框 id 是全大写 `_ROW_ADD_PPART_VALUE_c`，
      `[id$="_row_Add_PPART_VALUE_c"]` 大小写敏感**命中不到**，要按 `ppart_value` 转小写找。
2. **行级复选框必须真实鼠标点**：行的复选框是
   `<input type="checkbox" onclick="return false;" class="k-checkbox gridCheck gridCheckCell">`，
   金蝶在**祖先容器**做事件委托，`onclick="return false"` 又挡了默认切换 →
   派发合成 MouseEvent **勾不上、还不报错**。⇒ `page.mouse.click(x, y)`。
   （且老实现扫 `document.querySelectorAll('tbody tr')` 会命中**隐藏页面**的列表，
    点了 46 个全在隐藏表格里，页面上「真实选中 0」。）
3. **表头复选框是三态语义**：`未选/部分选中` 点一下变「**全选**」，全选点一下才「全清」。
   老 `clear_selection()` 点一次了事 → 「本来只勾了 1 行」被点成 **565 行全选**，
   后续再勾选 → 选中状态全乱、可能推错单据。
   ⇒ 修法：**循环点表头并读 `selected_count()` 直到归零**（0 行时别点，点了反而全选）。

### 还有两条工程防线（push_chain.py 已内置）
- 开始前**清掉重复的同名标签**：两个「销售出库单列表」并存时，过滤条件会落到另一个
  标签的实例上 → 静默漏单。
- 筛选后**硬校验单据号集合**：期望单据必须一张不少、也不能多出别的单，不齐就停。

## 五之九、★只读查询通道 ExecuteBillQuery（核对/排障，零副作用）

**遇到"这单怎么变成这样了"的问题，不要靠打开单据去猜**（打开会锁单、会污染现场）。
直接在页面里调金蝶的查询接口，把 创建人 / 创建时间 / 来源单 / 明细行 全查出来。

```python
from kdquery import KdQuery, num
kq = KdQuery(page)                       # page = 已登录的金蝶页面
kq.ar_head("AR105-202609-0004")          # 应收单头（含创建时间/创建人/状态）
kq.ar_entries("AR105-202609-0004")       # 46 行明细（物料/数量/单价/折扣/金额/来源单）
kq.ar_children("AR105-202609-0004")      # 挂在它下面的 `-00000N` 子单
kq.iv_source_ar("SVINV00033667")         # 专票的来源应收单号（FSrcBillNo）
```

要点：
- 路径 `/K3Cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService
  .ExecuteBillQuery.common.kdsvc`，**在页面上下文 fetch** 即自动带登录态，不用拼 cookie。
- 返回是**明文 JSON**（浏览器已解 gzip），**不需要 `kd_decode`**；
  形如 `[[v1,v2,...],[...]]`，列序 = `FieldKeys` 顺序。出错时返回
  `[[{"Result":{"ResponseStatus":{"Errors":[{"Message":...}]}}}]]`。
- ⚠️ **头字段与明细字段混放会被去重成"单据头条数"**（46 行明细只回 1 行）
  ⇒ **要查明细就只放明细字段**。
- 已验证字段：
  - 应收单头 `FCreateDate/FModifyDate/FCreatorId.FName/FDocumentStatus/FBillTypeId.FName`
  - 应收单明细 `FMaterialId.FName/FPriceQty/FTaxPrice/FDiscountAmountFor/
    FNOTAXAMOUNTFOR_D/FTAXAMOUNTFOR_D/FALLAMOUNTFOR_D/FSourceBillNo`
    （`FEntryID / FEntity_FEntryID / FSEQ / FRemarks / FNote` **都不存在**）
  - 专票明细 `FNotaxAmount/FAllAmount/FAuxPrice`、来源 `FSrcBillNo`
    （专票**没有** `FAmount`，税额要用 `FAllAmount - FNotaxAmount` 倒算）
- FormId：应收单 `AR_receivable`、销售增值税专用发票 `IV_SALESIC`、总账凭证 `GL_VOUCHER`。

## 五之十、★应收单 `-00000N` 子单：别当成"多出来的单据"

> 📄 结构化归档（含四条路径对比、清场顺序、排查套路）：`references/sub-bills-00000N.md`

**现象**：走完"应收单 →下推 专票（分录合并）→ 提交"后，应收单列表里那张单后面
多出 N 张 `父单号-00000N` 的单，每张 1 行、数量/单价/金额全 0、无来源单、已审核。
用户会问"怎么多了这么多应收单"。

**定性（2026-09-21 实测，勿再误判）**：
它是**金蝶「应收单 ↔ 发票」差异的载体单**，由**发票提交（审核）**时的"开票回写"
自动产生（创建时间与点提交同秒、毫秒级连出）。**全库 286 张同类子单**，横跨
2021–2026、数十个客户 ⇒ **系统标准机制，不是脚本 bug、不是重复下推**。

**差异来源有两大类**（同一个载体）：
1. **分币舍入差**：应收单 46 行**逐行**四舍五入不含税；专票按「分录合并」成 16 行
   （= 物料种类数）后**按物料汇总**再算 → 两次舍入顺序不同 → 部分物料各差 1 分，净差 0。
   （广源实测：4 个物料 +0.01/+0.01/-0.01/-0.01，**恰好就是出子单的那 4 个物料**）
2. **实质金额差**：部分开票 / 改价 / 退货（历史子单里的 -504.33、-461538 等）

**排查套路**（全只读）：`kq.ar_children()` 拿子单 → `kq.ar_entries()` 拿父单按物料汇总
→ `kq.iv_entries()` 拿专票按物料汇总 → 比差异。**差异落在哪些物料，子单就在哪些物料**，
这条对应关系是有子单物料的判定铁证。

**影响评估**：子单金额合计为 0（广源 6 张 = 0.00/-0.01/-0.01/+0.02 合计 0），
**父单金额不变、应收余额不重复、账务无影响**，只是列表"看起来"多了几条。

**清场顺序（还原链路时）**：6 张子单（反审核→删除）→ 专票 → 凭证 → 父单应收单。
⚠️ 别先删父单，子单会挡住。

**★ 怎么不产生子单**（用户问过，三条路，详见 `docs/广源应收单子单-原因分析.md` 第八节）：

| 路径 | 做法 | 代价 | 可靠度 |
|---|---|---|---|
| **A（首选）** | 下推专票**不选「分录合并」**（规则下拉里就有「分录不合并」）→ 专票行数 = 应收单行数，逐行一一对应，没有"汇总后重新舍入"这一步 → 无分币差 | 专票行数变多（46 而非 16） | 高，但要实测（见下） |
| **B** | 保 16 行合并，但先把**应收单按物料掰平**到"汇总后舍入"口径（差 δ 时取该物料最大行 `不含税+δ / 税额−δ`，价税合计不变） | 需在下推**之前**做、可能要反审核一次；应收单有几行"不含税≠含税÷1.13" | 高（闭环取专票真值） |
| **C** | 接受它 | 列表多几条 | 100% |

- 路径 A 开关：`push_chain.py --no-invoice-merge`
- 路径 B 内核：`discount.plan_material_align(rows)`（纯计算，已单测）。
  ⚠️ **目标值必须取"专票真实逐物料金额"**（下推一次读出来再丢弃），不要用 `round(含税/1.13)` 猜——
  掰平会让**总不含税移动 `delta_total`**，猜错总账会对不上。
- ❌ **不要下推后手工删子单**：它是回写差异的载体，删了可能断掉「应收单↔发票」对应关系，且账面本来就是 0。
- 验收工具（幂等、只读）：`python src/check_subbills.py <应收单号> --detail` → 期望「子单数 = 0」。
- 治本方案（RPA 改不了）：BOS 里把「应收单→专票」转换规则的金额取数改为**直接携带不重算**，需 IT 介入。

## 五之十一、整链第三段：专票「假保存」→ 复核人 → 提交审核 → 凭证 → 摘要（2026-09-21 广源包装集团 2026-09 实证）

> 📄 这些坑已按「现象 → 根因 → 判据 → 修法」重新编号归档，**条目更多**（49 条，
> 含本节的 ①~⑪）：`references/silent-failures.md`。本节保留最原始的实测叙述与代码。
> 🧩 可直接拷走的实现：`scripts/kd_save.py`（假保存判定）、`scripts/kd_combo.py`（复核人）。

> 这一节全部是**会静默出错**的坑：脚本报"成功"、日志全绿、金额全对，但数据其实没落库。

### ① ★★「保存成功」可能是假的：保存对话框里的必录校验
现象：`save_bill()` 不抛异常，截图看着正常，但
- 界面 `FBILLNO` 恒为空（**这是最灵敏的告警信号**）
- `ExecuteBillQuery` 查 `IV_SALESIC` 查不到这张单

真因：保存时金蝶弹出「**字段必录提示**」对话框，`共有1处必录项未填 / [开票信息.复核人] 是必填项，不能为空！`。
**必须把"可见弹窗文本"当成保存的判据之一**，不能只看有没有异常：

```js
// 保存后立刻读一切可见弹窗/提示；有内容就是失败
Array.from(document.querySelectorAll('.k-window')).filter(vis).map(e => e.innerText)
```
> 判据优先级：**查询回读（权威）> 界面 FBILLNO 非空 > 无弹窗**。
> 只信"没抛异常"＝一定会踩。

### ② 复核人 = `F_PAEZ_COMBO`，是 kdcombo，必须**真实鼠标**点
```
控件： [id$="-F_PAEZ_COMBO-EDITOR"]   ← 注意没有 _c，且内部**没有** input[data-role="dropdownlist"]、
                                        也拿不到 kendo 实例 → widget.open() / el.click() 全部无效
正确路径：① JS scrollIntoView({block:'center'}) 把控件滚进视口
          ② page.mouse.click(控件中心) 展开
          ③ JS 取目标 li 的 getBoundingClientRect 中心 → page.mouse.click 选中
          ④ read_combo() 回读校验（读 .k-input 的 textContent）
```
⚠️ **不 scrollIntoView 会直接失败且很难查**：控件 y=711 而视口高只有 641，
`document.elementFromPoint(cx, cy)` 返回 `null`（点在视口外），点击落到窗口外 → 下拉根本不开。
用 `elementFromPoint` 返回 null / `!box.contains(top)` 判断"被遮挡或不可见"，**重试而不是硬点**。

实测选项（9 人）：蒙晓芳 / 刘启丽 / 陈聪 / 张婷 / 杨蓉 / **姚玉平** / 区志婷 / 廖惠娇 / 李佩瑶。
用户口径（`config.yaml: rules.invoice_checker`）**复核人 = 姚玉平**（与该客户 2026-08 那张专票一致）。

### ③ ★★ `eval_all_frames` 只返回「真值」→ JS 返回 `0` 会被当成"没拿到结果"
`eval_all_frames` 的实现是 `for fr: r = fr.evaluate(...); if r: return r`，最后 `return None`。
**`0` / `''` / `false` 都是假值** → 会被丢弃，继续试下一个 frame，最终返回 `None`。
现象：轮询"条件行数"时，对话框里明明是 0 行（正常初始态），却拿到 `None`，
被判成 `-1`（找不到窗口），死等 12 秒后报错，而对话框看起来完全正常。

⇒ **凡是要取值（尤其是可能为 0 的计数）的 JS，一律返回对象**：`return {n: count}` / `return {ok:true, ...}`。

### ④ 过滤对话框的「新增行 / 全部删除」是 `li.k-link`，`el.click()` **无效**
```html
<li class="k-link kdFilterAdd">新增行</li>      <!-- 真实鼠标点坐标才生效 -->
```
现象：`el.click()` 返回 true、不报错，但条件行数永远是 0 → 死等超时。
实测对比：`page.mouse.click(中心坐标)` → 条件行 **0→1 立即生效**。
⇒ `_filter_click(cls)` 已改为「JS 取坐标 → `page.mouse.click`」。

### ⑤ 过滤对话框定位必须**按标题文本**，不能用 `.k-window[0]`
SPA 会把关掉的窗口留在 DOM 里（实测同时挂着 3 个过滤窗口：2 个隐藏的「列表过滤-[销售出库单]」+ 1 个可见的「列表过滤-[销售增值税专用发票]」），
`.k-window` 的**顺序会变** → 拿到隐藏窗口，`querySelectorAll('tr')` 为 0 → 误判"新增行没生效"。
```js
const win = [...document.querySelectorAll('.k-window')].filter(vis)
    .find(w => (w.innerText || '').includes('列表过滤'))
    || [...document.querySelectorAll('.k-window')].filter(vis).find(w => w.querySelector('li.kdFilterAdd'));
```
> 「新增行」本身是异步渲染的：点完要**轮询等到行数 > 目标行号**（实测曾 12ms 就绪，也曾等到超时），
> 不能只靠固定 `sleep`。

### ⑥ 专票列表的过滤对话框**默认没有任何条件行**（"暂无新增条件数据"）
出库单/应收单的对话框自带一行空白条件行，专票的不带 → 必须显式点「新增行」。

### ⑦ ★ 生成凭证的前置条件：源单必须**已审核**
未审核时点「凭证生成」→ 对话框顶部红字「**所选单据未全部审核!**」，**不产生报告**。
⚠️⚠️ 此时若仍去读"第一个可见的 grid"，读到的是**弹窗背后的专票列表**——
字段里同样有 `单据编号 / 价税合计 / 客户 / 单据状态`，极像报告行，
会得出「报告 0 错误、生成成功」的错误结论（本次真踩了）。
⇒ 生成后**先在对话框内部**判断：`txt.includes('未全部审核')` / `hasGrid` / 有无「确定」按钮；
再 `activate_tab("凭证生成报告列表")` 之后才读报告。
> 三种单据三种审核口径：应收单=保存即审核；**专票=提交即审核（`提交`→状态B，再 `审核`→状态C）**；凭证=提交≠审核。

### ⑧ 凭证生成成功后**会新开一个标签**「凭证生成报告列表」
`read_voucher_report()` 读的是"第一个可见 grid"，**不先 activate 那个标签就会读到背后的列表** → 报告行数 0。
标签顺序实测：
`[... , '销售增值税专用发票列表', '凭证生成报告列表']` → `查看业务凭证` → `业务凭证列表` → `查看总账凭证` → `凭证 - 修改`。

### ⑨ 摘要口径（用户 2026-09-21 定，2026-10-04 补「桶」，已实现进 `src/digest_rule.py`）
在**应收单详情页明细**里：
- x = 存货类别(`F_PAEZ_BASEPROPERTY2`) = `原材料` 的行，计价数量(`FPriceQty`)求和
- y = 物料名称(`FMaterialId.FName`) **包含**「光油」的行，计价数量求和
- t = 存货类别 = `包装物` 的行，计价数量求和（**桶**，单位「个」）
- z = 所有行计价数量求和；**s = z − x − y − t**
- 摘要 = `{当前单位名称}销售水性油墨{s}公斤，光油{y}公斤，原材料{x}公斤，桶{t}个`
  （单位名 = 客户名，如 `广源包装集团股份有限公司`；数量去末尾 0，`9580.00→9580`）
- ★ **桶（存货类别「包装物」）单独列「桶{t}个」**，单位「个」不是「公斤」，**不计入水性油墨**
  （用户 2026-10-04 定）。`classify_rows` 用 `pack_category="包装物"` 识别，返回 `t`+`n_pack`；
  `build_digest(..., t=..., t_unit="个")` 在 t>0 时追加桶段，omit_zero 下 t=0 不写，**不传 t 向后兼容**。

广源 2026-09 实算：`x=358`（原材料 4 行）/ `y=11200`（光油 7 行）/ `z=21138` / `s=9580`
→ `广源包装集团股份有限公司销售水性油墨9580公斤，光油11200公斤，原材料358公斤`

**★ 措辞与省略零项（2026-09-22 用户定稿，已实现）**
- 措辞统一用 **「水性油墨」**，**不要写「水墨」**（历史凭证里见过「水墨」写法，**不采用**）
- `build_digest(..., omit_zero=True)` → **省略数量为 0 的段**；三段全 0 时回退只写「水性油墨{总量}」
  ```python
  build_digest("慈溪市润科包装材料有限公司", x=0, y=0, s=1700, omit_zero=True)
  # → 慈溪市润科包装材料有限公司销售水性油墨1700公斤        （x=y=0，两段都省）
  build_digest("杭州小章印刷材料有限公司", x=400, y=0, s=880, omit_zero=True)
  # → 杭州小章印刷材料有限公司销售水性油墨880公斤，原材料400公斤   （y=0，只省光油段）
  build_digest("台州瑾晟涂料有限公司", x=0, y=0, s=2080, omit_zero=True, t=30)
  # → 台州瑾晟涂料有限公司销售水性油墨2080公斤，桶30个   （桶单独列，单位「个」）
  ```
- 历史写法（不省略零项）→ `...销售水性油墨9580公斤，光油11200公斤，原材料358公斤`。
  两种都存在过，**新客户默认 `omit_zero=True`**（用户 2026-09-22 选定）。
- 写摘要入口：`src/write_digest.py --ar-no <应收单号> --company <客户名> --go`（只放行 `voucher_save`）

**铺几行**：查历史凭证确认——同一张凭证的**全部分录行铺同一句摘要**（广源凭证 196462 的应收账款/销项税额/集团外客户×2 四行文本完全相同），
与 `config.yaml: rules.digest_scope=all_rows` 一致。空行（占位行）不动。
> 历史里「销项税额」那行原本是 `销售发票`，也要一并覆盖成统一摘要。

### ⑩ 两个查询口径的坑
- **`FSrcBillNo` 存的是来源单的 FID（数字），不是单号**：`FSrcBillNo like 'AR105-202609-%'` 永远 0 行；
  `FSrcBillNo = 'AR105-202609-0005'` 会报 `Conversion failed … to data type int`。
  查来源关系要先用 `AR_receivable.FID` 换出数字，或直接按客户/创建时间筛。
- **`FCreateDate` 过滤串格式必须是 `'YYYY-MM-DD HH:MM:SS'`**，写 `'2026-09-21T20:00'`（带 T）会报
  `Conversion failed when converting date and/or time from character string`。
- **`IV_SALESIC` 的 `FAllAmount / FNotaxAmount / FTaxAmount` 是明细级字段**，和单头字段混在一条 FieldKeys 里会退化/串行
  （打印出来的是"某一行的值"而不是整单合计）→ 单头只查单头字段，金额单独查明细再求和。
- **★ `GL_VOUCHER.FBillNo` 是内部 FID（数字字符串），不是凭证字号「转 66」**
  （2026-09-22 实测：`转66` 查回来的 `FBillNo` 是 `205495`，`转67` 是 `205499`）。
  凭证字号只在**界面/凭证生成报告**里，服务端查询拿不到 → **定位凭证要按「摘要原文 + 日期」筛**，
  别拿 FID 当「转XX」做等值匹配（会 0 行或匹配错）。核验脚本 `src/verify_units.py` 就是这么做的：
  ```python
  exp = build_digest(cust, c["x"], c["y"], c["s"], omit_zero=True)   # 先重算摘要
  kq.rows("GL_VOUCHER", "FBillNo,FDocumentStatus,FACCOUNTID.FName,FDEBIT,FCREDIT",
          f"FEXPLANATION = '{exp}' and FDATE >= '2026-09-29 00:00:00'")   # 再用摘要定位
  ```
  ⚠️ 同一摘要命中 **>1 张凭证**时必须报错人工确认（可能重跑过）。
- **专票明细税额用行级 `FDETAILTAXAMOUNT`**（= `FDETAILTAXAMOUNTFOR`）；
  误用单头 `FTaxAmount` 会**按行重复**（5 行合计变成 `2358.41 × 5`，2026-09-22 实测踩过）。
- **查应收单明细要「只放明细字段」**，混入单头字段会被去重成单据头条数（见 SF-20）。

### ⑪ 本段脚本（可直接复用）
| 脚本 | 作用 |
|---|---|
| `gy3_3_invoice.py` | 应收单 → 下推专票（`--no-merge` 分录不合并）→ 回填两日期 → 保存 |
| `gy3_3b_save_inv.py` | 给**已打开**的专票页补填复核人 + 体检 + 保存 + 弹窗捕获 + 落库回读 |
| `gy3_4_voucher.py` | 专票列表 → 凭证 → 凭证生成 → 报告（`--go` 才真生成） |
| `gy3_5_digest.py` | 报告列表 → 业务凭证 → 总账凭证 → 写摘要（`--assume-open` 复用已开页） |
| `gy3_6_submit_inv.py` | 专票提交 + 审核（`--audit`），跑完自动核查子单是否仍为 0 |
| `gy3_verify_all.py` | **最终核验**：应收单 / 子单数 / 专票 / 凭证摘要 一次跑完 |
| `recompute_digest.py` | 从应收单明细重算 x/y/z/s，与写进凭证的文本**对拍** |

## 五之十二、★并发环境：账套里可能同时有别人在跑单（2026-09-22 实测）

**现象**：脚本一切正常，但真实鼠标点击全部被拦、提示某元素 `intercepts pointer events`。

**根因**：同一账套（`<你的金蝶域名>.ik3cloud.com`）下午有**另一个用户/另一个组织**在同时操作——
实测撞到成都批次在跑 `AR109-*` / `SVINV0003366x~3368x`，残留了一个
**「处理结果」失败弹窗**（`183306=XSCKD-101-202512-S0283`，2025-12、组织 101，是别人的单）
浮在最上层，把所有点击吃掉。

**处理**：
1. 先 dump 弹窗内容确认**不是自己的单**（别把别人的失败当成自己的失败去"补救"）；
2. 用 `src/close_result_dlg.py` **真实鼠标点 ✕** 关掉（合成 click 对 `.k-window` 无效，见 SF-26）；
3. 关完再跑，并**用 ExecuteBillQuery 回读确认自己那批单据独立、完整**，没被混淆。

**推论（重要）**：单据号会出现**跳号**（本次 `AR105-...-0001` 之后直接 `-0003`）。
**跳号不等于失败**——不要因为"少了一个号"就去补单。以服务端回读为准，不要按号段推断。

## 五之十三、★核算维度（辅助账）修改：点「三个点」走 F7 ComboBox，不是直接填编码（2026-10-05 攻克）

**场景**：凭证「其他业务收入」(6051.\*) 行的核算维度默认生成错值「CDTL-001/成都天龙凭证用」
（`FDETAILID=106284`），要改成「0101001-009/原材料」（`FDETAILID=103198`）。

**★ 关键结论**：**直接往核算维度单元格里填编码 `0101001-009` 是行不通的**（之前各种方案都失败）。
正确交互是「点三个点 → 弹维度数据录入对话框 → 在 F7 ComboBox 里填编码 → 下拉选匹配项 → 确定 → 保存」：

1. **双击**凭证明细「核算维度」单元格（显示态是 `span[data-field=FDETAILID][data-rowid=N]`）
   → 进入编辑态，出现 `input.k-textbox`（值=当前名称）+ 旁边 `input.ui-cloumn-editor-icon`
   （`value="..."`，即用户说的「三个点」按钮）。
2. **点三个点** `input.ui-cloumn-editor-icon`（真实鼠标）→ 弹「维度数据录入」对话框
   （表单 `data-kdformid="BD_ACCTITEMEDITMETA"`，字段「项目」）。
3. 对话框里「项目」字段是 **Kendo ComboBox**（`span.k-dropdown-wrap`）：
   - `input.k-input` 是**隐藏输入框**（`opacity:0; color:transparent`），
   - `span.k-select>.k-icon.k-i-search` 是下拉按钮，
   - `span.ui-poplistedit-displayname` 显示当前名称（如「成都天龙凭证用」）。
4. 点 `input.k-input` 聚焦 → `Ctrl+A` → 键盘输入编码 `0101001-009` → 弹下拉 **grid 表格**，
   唯一匹配行 `0101001-009 / 原材料`（`td[role=gridcell]`，虚拟列表只渲染 1 行）。
5. **点该行**（td 文本「原材料」）→ `ui-poplistedit-displayname` 变「原材料」。
6. 点对话框「确定」按钮（`a[id$=_FDETAILID__BD_ACCTITEMEDITMETA____BTNOK_c]`；
   ★ class 是 `k-button` 不是 `kd-button`，`querySelector('a.kd-button')` 匹配不到，用
   `querySelectorAll('a')` 按文本「确定」找最稳）→ 弹窗关闭，凭证单元格变 `0101001-009/原材料`。
7. **点凭证「保存」**（`span[id$=-tbSplitSave]`），无报错弹窗即成功。
8. **服务端回读铁证**：`ExecuteBillQuery GL_VOUCHER` 按摘要+`FACCOUNTID.FNumber='6051.02'`
   过滤，读 `FDETAILID` 应为 **103198**（错值是 106284）。

**代码位置**：项目侧旧 `fix_other_income_detail()`（弱可见性 `filter(vis)[0]`，会命中残留
成都天龙凭证表 → 已撤销）。**现在交互摸清，可据此重写一个 `fix_other_income_dim.py`**
（按摘要/应收单定位凭证 → 逐行双击 FDETAILID → 走上述 F7 流程）。

## 六、工程约定

- ⚠️ **`submit_bill()` / `audit_bill()` / `generate_voucher()` 必须传 guard 实例**。
  写一次性脚本时传 `None` 会直接 `AttributeError: 'NoneType' object has no attribute 'check'`。
  放行写法（不改 config）：
  ```python
  _CFG = dict(CFG); _CFG["safety"] = dict(CFG.get("safety", {}) or {})
  _CFG["safety"].update({"auto_submit": True, "auto_audit": True,
                         "auto_voucher": True, "auto_voucher_save": True})
  ui = KingdeeUI(pg, CFG, log, Guard(_CFG, log))
  ```
- 关标签统一用通用脚本 `gy_closetab.py <标签名关键字>`（内部已实现"先激活再点 ✕ + 点否"）。
- 安全护栏：不可逆动作（提交/审核/生成凭证）默认在 config 里关掉，只做到「保存」为止；
  放行用命令行 `--yes` 临时开，**不要**直接改 config 里的开关。
- ★★ **红线动作（删除凭证等）没有开关、也不吃 `--yes`**：硬编码在
  `common.Guard.FORBIDDEN` / `scripts/kd_guard.py: FORBIDDEN_ACTIONS` 里，永远抛 `GuardError`。
  写任何"删除/作废"路径时，先在入口断言一次：
  ```python
  from kd_guard import assert_allowed, assert_click_safe
  assert_allowed("delete_voucher")          # ★ 无条件抛 —— 用于"绝不允许走到这"的断言
  assert_click_safe(menu_text)              # 点菜单前体检，命中「删除凭证」即抛
  ```
  理由与替代做法见 **§〇 红线**（核心：凭证删除不可逆、无回收站、且删除动作一路无校验极易被误点）。
- 每步截图 + 日志留痕。
- 现场采到的 selector 一律写回 `docs/界面结构速查.md`，别只留在脑子里。

## 七、参考实现

本项目（含完整可跑代码）：
`C:\Users\Administrator\WorkBuddy\2026-09-18-10-11-38\kingdee-rpa\`
- `src/kdnet.py` —— kdsvc 监听 + 解码 + 成败判定（**建议直接复用**）
- `src/steps.py` —— `KingdeeUI` 类：定位/填字段/下拉/下推/过滤/保存/提交
  - `grid_all_rows()` 读列表**全量数据源**（绕过虚拟滚动）
  - `set_bill_no_filter(bill_nos)` 用「单据编号 包含 + 多行或者」精确筛单
  - `click_confirm(dialog_kw=…)` **真实鼠标点**弹窗「确定」（合成 click 无效）
  - `close_all_tabs(keep_names=[...])` 清标签并保留指定页
  - `select_rows(bill_nos)` 按单号勾行（**真实鼠标**逐行点，合成事件无效）
  - `clear_selection()` 取消全部选中（**循环点表头到归零**，注意三态语义）
  - `detail_*` 系列 + `fill_discount_by_ratio` / `detail_align_totals`（折扣分摊与金额对齐）
- `src/discount.py` —— 折扣分摊/尾差/对齐的**纯计算**模块（无浏览器依赖，可离线单测）
- `src/push_chain.py` —— ★ **一条命令跑完整下推链**（命令行驱动；临时一单、现场补做用它）
- `tests/test_discount.py` —— 折扣算法回归测试（用广源真实 46 行数据，13 项，改算法必跑）
- `src/probe.py` —— 即时 DOM 探测器（`--js / --file / --preset`），调选择器必用
- `src/kdquery.py` —— ★ **只读查询通道**（`ExecuteBillQuery`，零副作用、不锁单）。
  核对单据、定位"这单怎么变成这样"、查子单/来源单/创建时间，都先用它，别去打开单据。
  配套探测：`probe_ar_dupes.py`（列表定位）、`probe_ar_subentry.py`（子单明细）、
  `probe_ar_census.py`（全库普查）、`probe_ar_compare.py`（逐物料差异比对）
- `src/verify_units.py` —— ★ **全链只读核验**（应收单 / 子单数 / 专票 / 凭证 / 摘要对拍），
  `sys.exit(0/1)`。**凭证按摘要原文定位**（见 SF-30）。技能侧泛化版 = `scripts/kd_verify.py`
- `src/write_digest.py` —— 写凭证摘要：`--ar-no <应收单号> --company <客户名> [--peek|--go]`，
  自动从应收单明细算 x/y/z/s 并写入（只放行 `voucher_save`，`--peek` 先预览）
- `src/dump_receipt_detail.py` —— 只读采集「链路回单」所需全字段 → JSON
  （应收单头+明细+子单数、专票头+明细、凭证按摘要定位的 8 字段）；
  ⚠️ `json.dumps` 遇 `Decimal` 要加 `default=str`
- `src/find_ar.py` / `find_inv.py` / `find_inv2.py` —— 只读**按客户+日期/按来源单号找单号**；
  **专票号在界面读不到时用它取权威单号再续推**（别退化成全选）
- `src/close_result_dlg.py` —— 关掉别人残留的「处理结果」窗（**真实鼠标**点 ✕，见 SF-31）；
  技能侧泛化版 = `scripts/kd_stray.py --close`
- `src/probe_filter_select.py` —— 列表「过滤 → 勾选」全链路诊断（出现漏单/勾不上时先跑它）
- ⚠️ `src/gy_*.py` 一批是**历史一次性脚本**（广源那次现场写的），扣款金额与目标值
  都硬编码在文件里，只作过程留痕，**不要拿它当模板**；要复用请用 `push_chain.py`。
- `src/act_invoice.py` —— 单张发票收尾：`--peek / --combo / --save / --submit`
- `src/verify_voucher.py` —— 发票/应收单 → 生成凭证 → 改摘要：
  `--list invoice|receivable --customer X --month 2026/9 [--digest 文本 --rows all|0,1 --save] [--peek|--go] [--yes]`
- `src/verify_merge.py` —— 零副作用验证「分录合并」（开对话框选好规则但不点确定）
- `src/submit_voucher.py` —— 提交「凭证 - 修改」页当前那张凭证：`--peek`（只读状态）/ `--yes`；
  判定 = 服务端 kdsvc 原文 **且** 工具栏禁用态翻转，两条都过才算成功
- `src/peek_tab.py <标签名>` —— 只读：切到已打开的标签页并 dump 可见网格（列头 + 行）
- `src/clean_tabs.py` —— 清空标签页：`--go [--keep-name 销售出库单列表] [--keep N]`；
  默认只列出不关闭（先看清再动手）
- `src/probe_now.py` / `src/probe_hdr.py` / `src/probe_tabs.py` / `src/probe_search*.py`
  —— 现场探测器（当前页签/工具栏/网格/字段/标签条/搜索面板），调选择器前先跑这些
- `src/gy_fill.py` —— 出库单全选下推应收单 + 按含税比例把扣款分摊到各行**折扣额**
- `src/gy_adjust.py` / `gy_adjust2.py` —— 把分摊尾差落到**金额最大行**（调折扣额→不含税/税额）
- `src/gy_save.py` —— 保存单据并用 kdsvc 原文判定（含 `--go/--save` 开关的同类脚本）
- `src/gy_close5.py` —— **丢弃**关闭单据标签（激活→点✕→警告条点「否」）
- `src/gy_closetab.py <标签名关键字>` —— **通用**关标签（推荐，gy_close5 的泛化版）
- `src/gy_inv1.py` —— 应收单列表筛单+勾选+`push_to_invoice()`；纯筛选（无 `--go`）/ `--go` 真下推
- `src/gy_inv2.py` —— dump 专票单据页（表头字段 + 明细全量行 + 合计，已用专票字段名）
- `src/gy_inv3.py` —— 专票回填（业务日期/发票日期/复核人）+ 保存 + 提交；已保存则自动跳过保存
- `src/gy_vch.py` —— 专票列表筛单+勾选 → 凭证生成对话框（`--go` 则一路生成）
- `src/gy_vch_go.py` —— 在**已打开**的凭证生成对话框上点最终「凭证生成」并读报告
- `src/gy_vch2.py` —— 报告列表 → 业务凭证列表 → 「凭证 - 修改」页（提交/改摘要入口）
- `docs/界面结构速查.md` —— 全部实测 selector 与字段名

## 八、静默失败速查表（按"你正在做什么"定位）

> 金蝶 RPA 的**头号风险不是报错，而是"不报错但做错事"**：日志全绿、截图正常、
> 金额看着对，数据却没落库 / 落到别的行 / 落到隐藏页面。
> 逐条的「现象 → 根因 → 判据 → 修法 + 代码」见 `references/silent-failures.md`（**SF-01 ~ SF-60**）。

### 2026-10-03 新增的四条（都是"批量跑到一半才炸、且一炸就炸全部"的类型）

| 症状 | 编号 | 一句话根因 | 修法 |
|---|---|---|---|
| 续跑半成品报 **「找不到下拉控件：'F_PAEZ_COMBO'（或不可见）」** | **SF-47** | `svis()` 里那条"必须在视口内"，把**窗口矮时落在折叠下方的真控件**误杀了（实测视口只有 1366×641，控件在 y=701） | `svis` 只管"渲染出来没"；视口单列 `inView`，前后台用 `inActivePanel`；开下拉前先 `scrollIntoView` |
| 一次**截图卡 9 分钟**（不是 88 秒那一档） | **SF-48** | CDP `Page.captureScreenshot` 要**等渲染器出一帧**，窗口最小化/被遮挡时永远等；**CDP send 没有 timeout 参数** | `KD_NO_SHOT=1` 整段关掉截图（批量脚本默认开）。实测 9m53s → 27s |
| 批量**第一家就挂、家家都挂**：`intercepts pointer events` | **SF-49** | 三环死锁：孤儿草稿页 → 判重 `close_all_tabs(keep_names=[])` **把锚点「销售出库单列表」也清掉** → 只能回搜索 → 结果列表被右上角**残留浮动提示条**压住 | 判重只看`…列表`且**显式保锚点**；搜索前挪焦点 + `dismiss_floating_hints()`；结果条目点击**退 JS `el.click()`** |
| 搜到了却点不动（单独看） | **SF-49** | 同上第三环；⚠️ 剥浮层 `pointer-events` **无效**（浮层下面直接是 BODY，条目不在那个坐标上） | 同上 |
| 凭证生成了却**写不上摘要**，想补却发现**「凭证生成报告列表」没了** | **SF-50** | 那张列表是**瞬时单槽**（只留最近一次生成的报告），**搜不到、关掉不可再打开**（本批马鞍山祥恒/宁波日和就这么丢的） | 清场 `KEEP_TABS` 必须含「凭证生成报告列表」，且**摘要要在同一轮紧跟生成动作写完**；补救走「凭证查询」，见 SF-52 |
| 开新功能页报**「超出了参数控制打开页签数的上限15个」**（点击被 `k-overlay` 拦） | **SF-51** | SPA 标签数达 15 就拒开（CDP 只看得到 1 个 page）；**10+ 个孤儿「应收单 - 新增」关不掉**长期占位 | 先 `clean_tabs --go --keep-name 销售出库单列表` 腾位；开「凭证查询」+「凭证 - 修改」要留 **≥2 空位** |
| 「凭证查询」里已定位到目标行、**双击毫无反应** | **SF-52** | 列表 **200 行/页（期间9共979行=5页）+ 虚拟滚动**，目标行 `<tr>` 在 DOM 但 **y 在视口外** → 点空且不报错 | 双击前 `scrollIntoView({block:'center'})` 再取坐标；翻页用 `kendoKDGrid.dataSource.page(n)`；**双击行 → 打开「凭证 - 修改」**（暂存 A 可编辑） |
| 「业务凭证查询」能搜到、也有「查看总账凭证」，但列表**永远 0 行** | **SF-53** | 默认过滤里**账簿值是未选占位**（渲染成 `select 105`）→ 等于没选账簿；「重置」也回不来 | **别走这条路**，补摘要改用 SF-52 的「凭证查询」 |
| 日志说「**折扣额填写完成：写入 16 行**」，实际**末两行没落库**（折扣少 765）；或「对齐」跑了 4 轮**每轮读到同一组数** | **SF-54** | `detail_set_cell`（双击→全选→输入→回车）**全程零校验**；末行写完 `detail_blur()` 把网格**从底部滚回顶部**，编辑器没走完 commit → 值静默丢 | **凡"写"必"回读"**：写完逐行 `detail_amounts()` 对账，不一致重填 ≤3 轮，仍不一致**抛错停止**（对齐循环亦然，勿只 WARN） |
| 应收单**业务日期/到期日落成「今天」**（单号年月跟着错成 `-202610-`），而日志写着「填写 FDATE = 2026-09-30」成功 | **SF-55** | `fill_bill_field` 用**弱可见性**过滤 `-FDATE-EDITOR_c` + `[0]` → SPA 里后台标签的表单 DOM 还在，命中的是**最老那张孤儿草稿**：写**真写进去了**，只是**写在别人身上**（所以日志与返回值都看似正常） | `fill_bill_field` 改用 `findEditor()`（onTop / 激活面板）**只认前台表单** + **写后回读**（≤3 轮，不符**抛错**）；`linked` 判据 = 联动值**有没有变化**（★ **不能**写「== 目标值」——有账期的客户到期日 = 业务日期 + 账期，会被误判成"写入失败"，见 SF-55 二次修正）；保存后 `verify_ar_biz_date()` 用 `ExecuteBillQuery` 回读 `FDate`。**日期字段禁用键盘打字兜底**（掩码框解析不了） |
| 日志「全部结束：应收单 …/ 专票 X」里的 X 是**上一家的专票号**；「凭证生成」报告写**0 错误**，但**凭证根本没生成**（下游摘要无处可写） | **SF-56** | `read_bill_fields` 同为**弱可见性** `filter(vis)[0]` → 读到**后台标签页**里上一家的 `-FBILLNO-EDITOR_c`（SF-55 的**读端**版本）→ `step_voucher` 勾了别人的单 → 弹「已生成总账凭证」**警告**（非错误）⇒ 静默失败 | `_verify_iv_no()`：专票号必须能用 `IV_SALESIC.FSrcBillNo = <AR>` **反查到**，不符**以服务端为准**；`read_bill_fields` 改为只认前台。**凡"读界面/取列表第 N 行"都要配不依赖界面的反证源** |
| 应收单「价税合计」和表里差 **0.01**（不含税/税额看着都对） | **SF-58** | 旧 `tol=0.01` 放过 1 分尾差；`all` 从没参与校验；金蝶行内 `all=notax+tax` 恒成立 ⇒ `d_all≡d_notax+d_tax`，价税合计差 1 分 = notax/tax 有 1 分没顶到 | `summarize`/`detail_align_totals` 加 `target_all`（**仅交叉校验**，不单独 `_fix` 改派生列）；**tol `0.01→0.00`**（精确到分）；`push_chain` 加 `--target-all` |
| 专票日志写「填写 FDATE=2026-09-30 回读成功」，但**凭证 TOAV202 报「期间不对」**（13 家凭证失败、63 家成功） | **SF-59** | 专票 `FDATE` 被 `pickBox` 的 `b[0]` 兜底写进**后台孤儿草稿页**（SF-55 在专票上复现）；界面回读是自证（写读同一错对象）骗过检查；服务端 FDATE=今天 → 凭证取到 10 月期间、账簿只开 9 月 | 专票 `save_bill()` 后 `_verify_inv_biz_date()` 用 `ExecuteBillQuery` 回读 `IV_SALESIC.FDATE`，不符业务月底**抛错**（在下推凭证前拦下）；**判据只用服务端回读，不信界面 widget** |
| 含「桶」客户的摘要把桶数量**并进了水性油墨**（油墨虚高、无「桶」字样） | **SF-60** | 摘要口径 `classify_rows` 只拆「原材料/光油/其余」，`s = z-x-y` 把「包装物(桶)」也算成水性油墨，没单独识别桶 | 桶=存货类别「包装物」；`classify_rows(pack_category="包装物")` 返回 `t`，`s = z-x-y-t`；`build_digest(..., t=..., t_unit="个")` 追加「桶{t}个」（t=0 不写，单位「个」不是公斤） |

### 第 0 条：红线（不是"容易错"，是"绝不允许"）

**不能删除凭证。** 凭证是账务链路的终态留痕，金蝶无回收站、删除不可逆，
且删除动作**没有校验、不弹错、kdsvc 正常返回** —— 脚本很容易顺手点掉而不自知。
该类动作硬编码禁止（`Guard.FORBIDDEN` / `kd_guard.FORBIDDEN_ACTIONS`），**任何开关和 `--yes` 都放不开**；
要改凭证请走「凭证 - 修改」页。详见 **§〇**。

### 三条铁律

1. **判据优先级**：**查询回读（权威）> 界面 FBILLNO 非空 > 无弹窗**。
   `save_bill()` 不抛异常 **≠** 保存成功 —— 实测踩过：必录项弹窗挡着，数据根本没进库，
   而截图一切正常。
2. **凡取值的 JS 一律返回对象**（`return {n: count}`）——
   `eval_all_frames` 只回第一个**非 None**；JS 返回裸 `0` 极易被当成"没拿到结果"
   （真实后果：条件行数 0 被误判成找不到窗口，死等 12 秒）。
3. **关键点击一律真实鼠标**（`page.mouse.click(x, y)`）——
   合成 MouseEvent 在 **Kendo 下拉、`li.k-link` 按钮、行复选框、弹窗「确定」、kdcombo**
   上**全部无效且不报错**。JS `el.click()` 只作兜底。
4. **凡"读界面 / 取列表第 N 行"，必配一个不依赖界面的反证源**（**SF-56**）——
   后台标签页的 DOM 仍然在，`filter(vis)[0]` 会**读到别的单据**的值（读端版 SF-55）；
   列表第 1 行也未必是刚处理的那张。反证源 = **来源单号反查 / 服务端金额 / 下游单据**，
   对不上就**以服务端为准**。
5. **判"派生字段（联动）生效"要看「有没有变化」，不是「等不等于目标值」**（**SF-55 二次修正**）——
   应收单「到期日」随「业务日期」联动，但**取值由业务规则决定**：有账期的客户
   到期日 = 业务日期 + 账期（实测江西新明峰 09-30 → `2026-12-29`），
   且金蝶**会自己重算**（写入前后账期 118 → 90 天，连"账期不变"都不成立）。
   ⇒ 判据只能是「**联动值 ≠ 写入前基准值**」；写前自身已等于目标值时放行。
   仍保住护栏：**widget 骗人 ⇒ 联动毫无反应 ⇒ 判失败**。排查时**把"写入前基准值"打进日志**。

### 按所在环节定位

| 我正在… | 症状 | 去哪条 |
|---|---|---|
| 点工具栏按钮 | 点了没反应、截图看不出 | **SF-01** 隐藏页面的同名按钮吃掉点击 |
| 保存单据 | 不报错但 FBILLNO 空 / 查不到 | **SF-02** 必录项弹窗（"假保存"） |
| 判断提交/审核成没成 | 提示一闪而过 | **SF-03** 读 kdsvc 服务端原文（唯一判据） |
| 操作下拉框 | 打不开 / 选完又变回默认 | **SF-04**（普通）、**SF-09**（kdcombo） |
| 过滤列表 | 筛漏单 / 条件静默失效 | **SF-05**（末行丢失）、**SF-08**（新增行）、**SF-11**（窗口定位）、**SF-25**（大小写） |
| 勾选行 | 勾不上 / 一点变全选 | **SF-06**（真实鼠标）、**SF-07**（三态语义） |
| 读列表 | 数据不全 / 整行错位 | **SF-24**（虚拟滚动）、**SF-21**（列严格对齐） |
| 改明细金额 | 相邻列读到空、越修越远 | **SF-14**（要移开焦点）+ §五之六 联动方向 |
| 下推 | 选了规则却用了默认值 | **SF-22**（下拉只活在同一次 evaluate） |
| 生成凭证 | 报告 0 行 / "0 错误生成成功" | **SF-12**、**SF-13**、**SF-23** |
| 改凭证摘要 | 双击进不了编辑态 | §五：认准标签「**凭证 - 修改**」；「业务凭证 - 查看」是只读页 |
| 关闭标签 | 点了没反应 | **SF-15**（确认框是内嵌警告条）、**SF-16**（先激活再点 ✕） |
| 打开功能页 | 搜索框打了字搜不出 | **SF-17**（搜索只在开面板那一瞬间发起） |
| 写查询 | 报类型转换错 / 明细只回 1 行 | **SF-18**、**SF-19**、**SF-20** |
| 读单据字段 | 组合控件读到空 | **SF-25** + `references/fields-and-selectors.md` §一 |
| 写日期字段 | 提交后日期没变 | **SF-27** 必须走 Kendo DatePicker API，直接改 value 不同步 |
| 点弹窗「确定」 | 点了不关 | **SF-26** 必须真实鼠标点坐标 |
| 保存之后 | 列表里看不到刚存的单 | **SF-28** 列表不自动刷新，要手动「刷新」 |
| 操作同名标签 | 操作落到了错的那个 | **SF-29** 必须按序号定位，`activate_tab(名字)` 只命中第一个 |
| **点击突然全被拦**（`intercepts pointer events`） | 明明是自己的脚本却点不动 | **SF-31** 别人残留的「处理结果」窗压在最上层 → 先 dump 确认不是自己的单，再 `kd_stray.py --close` |
| **搜索结果点不中** | `locator.click()` 超时 / 报折叠容器拦截 | **SF-32** `.kd-form-collapse-container` 拦截 → 真实鼠标 + `elementFromPoint` 命中检测 |
| **核验凭证对不上号** | 查到 `205495`，界面却是「转 66」 | **SF-30** `GL_VOUCHER.FBillNo` 是**内部 FID** → 按**摘要原文**定位（`kd_query.voucher_by_digest`）；`FVOUCHERGROUPNO` 凭证号**跨字组重复**（转196/记196…都是196），必须配 `FVOUCHERGROUPID.FName` 字组一起查 |
| **单据号跳号** | `-0001` 后直接 `-0003` | **SF-33** 同账套别人在并发跑单，正常跳号 → **不要补单**，以回读为准 |
| **标签点 ✕ 删不掉、越积越多** | 最后报「找不到可见的列表网格」，整链失败 | **SF-40** 孤儿标签（`li` 缺 `k-tab-on-top`）→ 任何点击方式都无效；用 `close_all_tabs` 的**黑名单跳过**，别硬刚 | 
| **想刷新页面清标签** | 直接退到登录页、打断用户会话 | **SF-41 ★红线**：金蝶 `reload()` = **登出**，**绝不要刷新用户的页面** |
| 日志说「关掉 79 个」但标签没少 | 白等 70 秒 | **SF-42** `closed` 记录的是**尝试次数**；用耗时反推真伪（79×0.9s≈71s） |
| **一条链跑得比预期慢** | 每次截图都报 `Timeout 30000ms` | **SF-34** 截图必须 `timeout=5000` + `animations="disabled"`，失败降级 CDP 直取 → 单家白扔 90 秒 |
| **保存后 `(单号未读到)`** | 下一步拿不到单号直接中断 | **SF-35** 用只读查询反查：优先「来源出库单号」精确关联，再退时间窗；**命中要 WARN** |
| 想删凭证 / 脚本点到「删除」 | 不报错但账乱了 | **§〇 红线** —— 不能删除凭证，改用「凭证 - 修改」页；代码拦截见 `kd_guard.py` |

### 换账套 / 换版本前的自检顺序

1. `probe`（现场 DOM 探测器）确认 id 后缀规律没变；
2. `KdQuery` 只读核对现状 —— **先查有没有上次跑了一半的残留单据**，否则后面按日期筛会多带东西；
3. 先用 `--dry-run` / 只读模式跑一遍，与 `references/acceptance-baseline.md` 对量级；
4. 再放行不可逆动作（`--yes`），**不要**去改 config 里的安全开关。
