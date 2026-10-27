# 整链端到端 SOP —— 出库单 → 应收单 → 专票 → 凭证 → 摘要 → 审核

> 这是**照着做就行**的操作剧本。每一步都给了「动作 / 关键字段 / 验收判据 / 失败怎么办」。
> 配套代码见 `scripts/`（自包含）与 SKILL.md §七（完整项目实现）。
> 实测来源：广源包装集团 2026-09 整链，2026-09-21 跑通（含子单 0 张）。

## 0. 现场准备（每次开工必做）

| 项 | 做法 | 验收 |
|---|---|---|
| 调试端口 Chrome | `chrome.exe --remote-debugging-port=9222 --user-data-dir=D:\kd-profile`，**人工登录** | `curl -s http://127.0.0.1:9222/json/version` 返回 JSON |
| 接管页面 | `p.chromium.connect_over_cdp(cdp_url)` → `br.contexts[0].pages[0]` | 拿到 page |
| 安全护栏 | 不可逆动作（提交/审核/生成凭证）默认在 config 里 `false` | 放行用命令行 `--yes` 临时开，**别改 config** |
| 现场清场 | 先 `close_all_tabs(keep_names=["销售出库单列表"])` | 标签只剩 1 个，避免"重复同名标签导致过滤落到另一个实例" |
| 只读摸底 | `KdQuery(page)` 先查一遍现状 | 见 `scripts/kd_query.py` |

⚠️ 开工前**必查是否有之前跑了一半的残留单据**（用只读查询按客户 + 创建日期筛），
不然后面"按日期筛出库单"会多带出上一次没清干净的东西。

## 1. 阶段一：销售出库单 → 应收单

**动作序列**（`KingdeeUI`，详见 SKILL.md §五 / §五之八）：
```
open_menu_by_search("销售出库单列表")          # 重名要用菜单路径消歧（供应链>销售管理>出货处理）
set_bill_no_filter([单号...])                 # 或按 FDate1 日期区间
clear_selection() if selected_count() else -  # 表头复选框是三态语义，先归零
select_all_rows()                             # 真实鼠标逐行勾
click_menu("下推") → 选目标「应收单」 → FBTNOK_c
→ 进入「应收单 - 新增」
fill_discount_by_ratio(扣款)                  # 折扣额按含税比例分摊，尾差落最大行
detail_align_totals(target_notax, target_tax) # 闭环对齐（读实际合计→算差→写→再读）
fill_bill_field("FDATE", 月底)                 # 业务日期
fill_bill_field("FENDDATE_H", 月底)            # 到期日
save_bill()                                   # ★ 应收单是「保存即审核」
```

**关键字段**：`FDATE`(业务日期) / `FENDDATE_H`(到期日) / 明细 `FDISCOUNTAMOUNTFOR`(折扣额)
/ `FNOTAXAMOUNTFOR_D`(不含税) / `FTAXAMOUNTFOR_D`(税额) / `FALLAMOUNTFOR_D`(价税合计)

**验收判据（三条都要过）**：
1. 单号非空（`AR105-YYYYMM-NNNN`），状态 = **C（已审核）**
2. 明细行数与出库单一致；三项金额精确命中目标（不含税 / 税额 / 价税合计）
3. **子单数 = 0**（`kq.ar_children(单号)` 返回空）

**失败怎么办**：
- 金额差几分 → 金蝶「不含税」走单价反算，**必须闭环收敛**，不要用 `含税/1.13` 离线推算
- 筛不出全部单 → 过滤条件末行被静默丢弃（见 `silent-failures.md` #5）
- 勾不上行 → 行级复选框必须真实鼠标（见 `silent-failures.md` #6）

## 2. 阶段二：应收单 → 增值税专用发票

**动作序列**：
```
open_receivable_list()
set_bill_no_filter([应收单号])
clear_selection() if selected_count() else -
select_all_rows()
push_to_invoice(merge=False)                  # ★ merge=False = 「分录不合并」= 不产生子单
→ 进入「销售增值税专用发票 - 新增」
fill_bill_field("FDATE", 月底)                 # 业务日期
fill_bill_field("FINVOICEDATE", 月底)          # 发票日期
fill_combo("F_PAEZ_COMBO", "姚玉平")           # ★ 复核人是必录项，不填保存必定静默失败
save_bill()                                   # 保存到「创建」态即可，提交在下一阶段
```

**为什么必须 `merge=False`**：分录合并会把 46 行按物料汇总成 16 行再重新算不含税，
与应收单的逐行舍入口径不同 → 发票提交回写时产生 `-00000N` 差异子单。
不合并则**逐行一一对应、无二次舍入** → 子单 0 张（已实测）。

**验收判据**：
1. 专票号非空（`SVINVNNNNNNNN`）——**界面 FBILLNO 恒空就是保存失败的信号**
2. `ExecuteBillQuery` 能查到这张单（**唯一权威口径**）
3. 明细行数 = 应收单行数（46 = 46）；三项金额与应收单完全一致
4. `FDATE = FINVOICEDATE = 月底`
5. `F_PAEZ_COMBO = 姚玉平`、开票人默认登录人、收款人沿用历史

**失败怎么办**：
- 保存后界面无单号、查询查不到 → **一定有可见弹窗**（十有八九是「[开票信息.复核人] 是必填项」），
  按 `silent-failures.md` #9 处理
- 复核人下拉点不开 → kdcombo 必须真实鼠标 + scrollIntoView（`scripts/kd_combo.py`）

## 3. 阶段三：专票提交（= 审核）

```
select_rows([专票号]) → click_menu("提交")
```
**本账套口径：专票「提交即审核」**，服务端回 `审核成功！`，状态直接变 C。

**验收判据**：
1. 「单据状态」字段 = **C**（已审核）
2. 服务端 kdsvc 回 `审核成功！`；若 kdsvc 抓不到（`messages()` 为空），
   **允许以界面提示条 + 状态字段为准**（不要因为没有服务端原文就判失败重做）
3. **★ 立刻复查子单数仍为 0**（提交才是回写触发点）——这是 `merge=False` 的最终验证

> ⚠️ 应收单是「保存即审核」，专票是「提交即审核」，凭证是「提交≠审核」。
> 三档口径**逐类确认**，别互相套用。

## 4. 阶段四：生成凭证

**前置条件（缺一不可）**：
- 源单**必须已审核**（否则对话框红字「所选单据未全部审核!」且**不产生报告**）
- **必须关掉占着该单据的「- 修改」/「- 新增」标签**（否则报「该业务单据正在被 XXX 使用」）
- 从**列表页**发起（详情页没有入口）
- 来源单据要与账套凭证模板匹配：本账套**从专票列表生成 ✅**，从应收单列表生成 ❌

**动作序列**：
```
专票列表 → 勾选中标发票
click_menu("凭证")(split, 尾 tbSplitButtonVch) → click_menu("生成凭证")(尾 tbVoucherGenerate)
→ 「凭证生成」对话框
check_voucher_book_row()                      # ★ 不勾账簿行会只回「请选择账簿！」且界面像没反应
→ 点 a[id$="-FVOUCHERGENE_c"]
→ 自动打开标签「凭证生成报告列表」
activate_tab("凭证生成报告列表")               # ★ 必须先切标签再读，否则读到背后列表
read_voucher_report() / voucher_report_errors()
```

**验收判据**：
1. **对话框内部**先判断：出现过 `未全部审核` / `请选择账簿` 文本 → 根本没生成，直接停
2. 新开标签「凭证生成报告列表」存在，报告行**错误数 = 0**
3. 报告里的「提示类型」列没有"错误"

**失败怎么办**：
- 报告 0 行 → 八成是读到了弹窗背后的列表（见 `silent-failures.md` #10）
- 生成失败报模板错 → 换来源单据试，别急着改脚本

## 5. 阶段五：写凭证摘要

**摘要口径（用户定，已实现进 `scripts/digest_rule.py`）**：
在**应收单详情页明细**里：
- `x` = 存货类别(`F_PAEZ_BASEPROPERTY2`) = `原材料` 的行的计价数量(`FPRICEQTY`)之和
- `y` = 物料名称(`FMaterialId.FName`) **包含**「光油」的行的计价数量之和
- `z` = 全部行计价数量之和；**s = z − x − y**
- 摘要 = `{当前单位名称}销售水性油墨{s}公斤，光油{y}公斤，原材料{x}公斤`
  （单位名 = 客户名；数量去末尾 0，`9580.00 → 9580`）

**动作序列**：
```
凭证生成报告列表 → 勾行 → click_menu("查看业务凭证") → 业务凭证列表
→ 勾行 → click_menu("查看总账凭证") → ★标签「凭证 - 修改」（唯一可写页）
→ 逐个分录行：双击摘要格 → input[id$="-FENTITY_FEXPLANATION_c"] → fill + Enter
→ click_menu("保存")   # 凭证：保存 ≠ 提交 ≠ 审核
```

**铺几行**：历史凭证**全部有效分录用同一句摘要**（广源凭证 196462 四行文本完全相同），
与 `config.yaml: rules.digest_scope=all_rows` 一致。空行（占位行）不动。
「销项税额」那行原本是「销售发票」，也要一并覆盖。

**验收判据**：
1. 每行摘要回读 = 目标文本
2. 借贷仍平衡（改摘要不动金额）
3. 凭证已保存

**失败怎么办**：
- 摘要格进不了编辑态 → 你很可能在「业务凭证 - 查看」（只读页），
  必须切到「凭证 - 修改」；且在该只读页**按 Esc 会直接关掉整个页面**
- 单头字段（账簿/凭证字/凭证号/期间）不在 `span[data-field]` 里，只能抓容器 innerText

## 6. 阶段六：全链核验 + 清场

> ★★ **红线（先读）**：本阶段允许"清场"关标签页，但**绝不允许删除凭证**。
> 凭证是这条链路的终态留痕，金蝶无回收站、删除不可逆；且删除动作一路无校验、不弹错，
> 脚本极易误点。要改凭证 → 「凭证 - 修改」页改完保存；想重来 → 先人工确认，再走"反审核 → 修改"。
> 代码拦截：`Guard.check("delete_voucher")` / `assert_allowed("delete_voucher")` 永远抛错。

**一次跑完的核验**（只读，不改任何数据）：
```
应收单：单号 / 状态 / 行数 / 三项金额 / 子单数
专票  ：单号 / 状态 / 行数 / 三项金额 / 双日期 / 三人 / FDATE=FINVOICEDATE
凭证  ：凭证号 / 日期 / 摘要逐行 / 借贷合计 / 是否平衡
```
再跑一次**摘要重算对拍**：从应收单明细重算 x/y/z/s，与写进凭证的文本逐字比对。

**清场**：`close_all_tabs(keep_names=["销售出库单列表"])`（= `clean_tabs_after_voucher`）。
只保留出库单列表；若它已被替换掉，重新打开一个。

## 7. 一键复用：`push_chain.py`（命令行驱动）

```bash
# ① 先只读验算（不下推、不保存）：看清分摊与对齐方案
python src/push_chain.py --customer 广源包装集团 --bill-nos 单号1,单号2 \
    --discount 2733.5 --target-notax 266824.88 --target-tax 34687.22 --dry-run

# ② 正式全链（--step voucher 是默认值）
python src/push_chain.py --customer 广源包装集团 \
    --date-from 2026-08-21 --date-to 2026-09-20 \
    --discount 2733.5 --target-notax 266824.88 --target-tax 34687.22 \
    --biz-date 2026-09-30 --invoice-date 2026-09-30 --yes

# ③ 不产生 -00000N 子单：下推专票用「分录不合并」
python src/push_chain.py ... --no-invoice-merge --yes
# ④ 对照实验：同一张已审核应收单，只重跑「下推专票」这一段
python src/push_chain.py --ar-no AR105-202609-0004 --step invoice --no-invoice-merge --yes
```

参数：`--step receivable|invoice|voucher` / `--ar-no` / `--no-invoice-merge` /
`--checker 姚玉平` / `--yes` / `--dry-run` / `--no-clean-tabs`。
`run.py`（Excel 驱动、批量、可回写）与 `push_chain.py` 共用同一套 `KingdeeUI` + `discount.py`。

## 8. 仍需人来定的四件事（系统里取不到）

1. **扣款金额 D**（少收多少）
2. **实际开票的不含税 / 税额**（目标对齐值）
3. **复核人**（人名，写错污染正式发票）
4. **月底日期**（业务日期 / 到期日 / 发票日期）

拿到这四项，其余可全自动。

## 9. 绝不自动化的动作（红线）

自动化到"提交/审核/生成凭证"为止可以用开关放行；下面这些**永远交给人**：

| 动作 | 口径 |
|---|---|
| ★★ **删除凭证** | **绝对禁止**。凭证不可逆留存，删了来源单据与总账的对应关系就断了，且无回收站。要改就走「凭证 - 修改」页 |
| 作废凭证 / 作废发票 | 留痕但仍不可逆 —— 人工点 |
| 删除已审核单据 | 先反审核，再由人工确认删除 |
| 反审核已生成凭证的单据 | 会连带影响凭证，先查清关联 |

```python
# 入口断言（scripts/kd_guard.py）：任何"删除"路径都先过这一道
from kd_guard import assert_allowed, assert_click_safe
assert_allowed("delete_voucher")   # ★ 无条件抛 GuardError，与 config / --yes 无关
assert_click_safe(menu_text)       # 点菜单前体检，命中「删除凭证」即抛
```
