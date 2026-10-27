# 金蝶云星空 8.2.0 开票流程自动化（RPA）

销售出库单 → 下推应收单 → 下推增值税专用发票（分录合并）→ 生成凭证 → 改摘要

路线：**网页端浏览器自动化**（WebAPI 未开放）。数据由 Excel 驱动，Playwright 接管**已登录的 Chrome**（CDP）。

---

## 仓库说明

本仓库 = **项目代码 + skill 包**，一次 clone 全拿到，换机器直接复用。

| 目录 | 内容 |
|---|---|
| `src/` | 核心生产脚本（下推/填单/开票/凭证/摘要/批量编排/自检） |
| `skill/` | 通用 skill 包（SKILL.md + references/ 文档 + scripts/ 的 kd_* 通用脚本） |
| `docs/` | 界面结构速查、下推链复用指南、静默失败清单 |
| `tests/` | 单元测试（摘要规则、折扣） |
| `setup_new_machine.py` | 新机器一键部署（含自动装 Python） |
| `新机器一键部署.bat` | Windows 双击入口 |

---

## 系统架构

### 分层结构

```
┌─────────────────────────────────────────────────────────────────┐
│  编排层（run_batch_*.py / run_ar_voucher_batch.py）              │
│  · 读「下推.xlsx」→ 逐家调度下推链 → 写批次结果 JSON             │
│  · --precheck 开跑前自检 / --xl 指定上传文件为数据源             │
└───────────────────────────────┬─────────────────────────────────┘
                                │ 逐家调用
┌───────────────────────────────▼─────────────────────────────────┐
│  业务链路层（push_chain.py / push_return.py）                    │
│  · push_chain：出库单→应收单→专票→凭证→摘要（蓝字）             │
│  · push_return：退货单→红字应收单（有退货客户单独冲减）          │
└───────┬───────────────────────────────┬─────────────────────────┘
        │ 界面操作                        │ 服务端只读核查
┌───────▼────────────────────────┐  ┌────▼───────────────────────┐
│  界面层（steps.py / KingdeeUI）│  │  查询层（kdquery.py）        │
│  · 多 iframe 查找 / 点文本      │  │  ExecuteBillQuery 只读查询   │
│  · 填字段 / 勾选 / 下推 / 保存  │  │  不锁单、不写数据            │
│  · 主标签栏选择器 kd_tab_sel    │  │  用于对账 / 反查 / 回读验证  │
└────────────────────────────────┘  └────────────────────────────┘
        │                                 │
┌───────▼────────────────────────┐  ┌────▼───────────────────────┐
│  领域层（纯函数，可离线单测）   │  │  公共层（common.py）         │
│  · digest_rule.py 摘要口径     │  │  · 配置加载 / 日志 / 截图    │
│  · discount.py 折扣分摊+尾差    │  │  · Guard 安全护栏(不可逆)    │
└────────────────────────────────┘  │  · Excel 读写               │
                                     └────────────────────────────┘
```

### 核心设计原则

| 原则 | 说明 | 落地 |
|---|---|---|
| **单一真相源** | 主标签栏选择器/ widget 键名只在 `kd_tab_sel.py` 一处定义，其余 `import`，禁止手写 | `kd_tab_sel.MAIN_TAB_SELECTOR` / `WIDGET_KEY` |
| **凡写必回读** | 写动作不抛异常 ≠ 值进去了，判据以**查询回读**（kdquery）为准，界面 FBILLNO 非空次之 | SF-54 |
| **不依赖界面的反证源** | 读界面/取列表行不可信（后台标签页整份 DOM 残留），必配服务端金额/源单号反查兜底 | SF-56、SF-58 |
| **安全护栏默认全关** | 提交/审核/凭证/保存凭证默认 false，触发不可逆动作即中断 | `common.Guard` |
| **凭证红线** | 凭证一律不删（金蝶无回收站），代码级硬拦截与任何开关无关 | `Guard.FORBIDDEN` |

---

## 下推链逻辑详解（push_chain）

一条完整链路：**出库单 → 应收单 → 专票 → 凭证 → 摘要**，每步都有对应护栏。

```
出库单筛选 ──► 应收单（折扣分摊+金额对齐）──► 专票（勾选护栏）──► 凭证 ──► 摘要
 filter_outstock   step_receivable             step_invoice      step_voucher  write_digest
```

### ① 出库单筛选（`filter_outstock`）

- 按**账务日期区间**（`FDate1` 上月26~当月25）+ 客户名，过滤销售出库单列表
- 跳过 `SKIP` 字典里的特殊客户（有退货/金额对不上，需单独处理）
- `--dry-run` 只探测不写，输出每家可推状态

### ② 应收单（`step_receivable`，含折扣分摊 + 金额对齐）

- 下推应收单，填业务日期 `FDATE`（到期日 `FENDDATE_H` 自动联动，SF-55 用「联动值确实变了」做见证）
- **折扣分摊**（`discount.py`）：`--discount <金额>` 按含税占比分摊到明细行的「折扣额」，尾差校正到分
- **金额对齐**：`--target-notax/--target-tax/--target-all` 指定目标，对齐不达标**抛错不保存**（SF-54）
- 保存 → 提交（本组织「保存即审核」）

### ③ 专票（`step_invoice`，含勾选护栏 SF-58）

- 打开应收单列表 → **先按单号过滤**成只这一张（避免虚拟滚动翻页扫不到，SF-57）→ 勾选 → 下推专票
- **勾选护栏（SF-58）**：勾选后拿 `kdquery` 读**服务端应收单明细行数**作权威基准，
  `真实选中 ≠ 服务端行数` 时先降级「表头全选」重勾，仍不符**抛错停止**——绝不让漏行的专票落库
- 填业务日期/发票日期/复核人（开票人保留默认，发票号不填）→ 保存 → 提交

### ④ 凭证（`step_voucher`）

- 打开专票列表勾选 → 生成凭证，读「凭证生成报告」确认 **0 错误**

### ⑤ 摘要（`write_digest.py` / `write_digest_by_vchno.py`）

- `write_digest.py`：`--ar-no <应收单>` + `--company <客户>`，走「应收单→业务凭证列表」
- `write_digest_by_vchno.py`：凭证已生成、列表已关时，`--no <凭证号>` 按号精确定位（走 `gl_goto_voucher.open_voucher`）
- 摘要口径（`digest_rule.py`）：按存货类别/物料名拆「水性油墨/光油/原材料/桶」，详见下文

### 半成品续跑（不要重跑整链，会推重）

| 场景 | 命令 |
|---|---|
| 专票页已开着，缺复核人/保存/提交 | `act_invoice.py --combo --save --submit --yes` |
| 专票已审核，缺凭证 | `gy3_4_voucher.py --inv <专票号> --go` |
| 凭证已生成，缺摘要 | `write_digest.py --ar-no <应收单号> --company <客户> --go` |
| 应收单已在库，缺整条「专票→凭证→摘要」 | `push_chain.py --ar-no <AR> --step voucher --yes` |

---

## 退货红字下推（push_return）

有退货的客户（如桐乡华淳、江西广源），退货单要**单独下推红字应收**，与蓝字出库单→应收单分开，二者净额才对得上开票价税合计。

```
销售退货单（XSTHD） ──► 下推「应收单」──► 红字应收单（金额为负）──► 保存 ──► 提交
```

- 只做「退货单 → 红字应收单」，**不做专票/凭证**（退货红字另走）
- 退货单列表功能名「销售退货单列表」，路径 `供应链>销售管理>退货处理`
- 退货单客户字段界面 `FRETCUSTID_FNAME`（服务端 `FReceiveCustId.FName`）

**对账公式**：`蓝字出库单合计 − 折扣 − 退货红字 = 开票价税合计`

---

## 核心脚本速查

| 脚本 | 职责 |
|---|---|
| `common.py` | 配置/日志/截图/安全护栏/Excel 读写 |
| `steps.py` | 界面操作封装（KingdeeUI：多 iframe 查找、填字段、勾选、下推、保存） |
| `kdquery.py` | 服务端只读查询（ExecuteBillQuery，对账/反查/回读） |
| `kd_tab_sel.py` | 主标签栏选择器**唯一真相源** |
| `digest_rule.py` | 摘要口径（拆水性油墨/光油/原材料/桶） |
| `discount.py` | 折扣分摊 + 尾差校正（纯计算，可离线单测） |
| `push_chain.py` | 蓝字下推链（出库单→应收单→专票→凭证→摘要） |
| `push_return.py` | 退货单→红字应收单 |
| `run_batch_*.py` | 批量编排（读 xlsx → 逐家调度 → 写结果 JSON） |
| `write_digest.py` | 按应收单补写摘要 |
| `write_digest_by_vchno.py` | 按凭证号补写摘要（列表已关时） |
| `clean_tabs.py` | 清场（保留工作锚点） |

```bash
cd kingdee-rpa
python start_browser.py                                        # 启动调试浏览器（登录金蝶用）
python src/run_batch_20261031.py --precheck                    # 开跑前自检（页签护栏/选择器）
python src/run_batch_20261031.py                               # 整批下推（读默认数据源）
python src/run_batch_20261031.py --xl <上传的xlsx路径>          # 指定数据源（对话框上传的文件用这个）
python src/push_chain.py --ar-no <应收单号> --step voucher --yes   # 半成品续跑：补专票→凭证→摘要
python src/push_chain.py --customer <客户> --bill-nos <出库单号> --discount <折扣> --target-all <价税> --step voucher --yes  # 单家完整下推
python src/push_return.py --customer <客户> --bill-nos <退货单号> --biz-date <日期> --yes  # 退货红字下推
python src/write_digest.py --ar-no <应收单号> --company <客户> --go  # 补写摘要
python src/write_digest_by_vchno.py --no <凭证号> --ar-no <应收单号> --company <客户> --go  # 凭证已生成只补摘要
python src/clean_tabs.py --go --keep-name 销售出库单列表       # 清场（保留工作锚点）
```

---

## 新机器一键部署（换机器看这里）

**双击 `新机器一键部署.bat`**（或命令行 `python setup_new_machine.py --all`），脚本自动完成：

1. **检测并自动安装 Python**（没有就装到本仓库 `.python/` 下，不污染系统）
2. 装依赖（playwright / openpyxl / PyYAML + Chromium 内核，走清华镜像）
3. 补 `config.yaml`（从 `config.example.yaml` 复制，`base_url` 已带默认值）
4. 装 skill 包（拷到 `~/.workbuddy/skills/`，WorkBuddy 自动加载）
5. 启动调试浏览器（独立 Chrome，端口 9222）
6. 开跑前自检（`--precheck`）

### 部署后你只需手动做两件事（换机器必然要做，脚本替不了）

1. **在弹出的 Chrome 里登录金蝶**，登录后保持窗口开着（登录态不随 git 走）
2. **在对话框里直接上传本期的「下推.xlsx」**（业务数据不进 git、不落 data/，交给 WorkBuddy 接手）

做完这两步，对 WorkBuddy 说「**金蝶批量下推**」并 @ 这个 skill，剩下交给它跑批 + 排障。
WorkBuddy 拿到你上传的文件后会用它作为数据源跑批（`run_batch_*.py --xl <你上传的路径>`），无需你手动放文件。

> 若运行报错「python 不是内部或外部命令」，说明 Python 自动安装没成功，手动装一个
> Python 3.10+ 并勾选「Add to PATH」后重跑本脚本即可。

---

## 手动部署（可选，不推荐）

已有 Python 3.10+ 时，可跳过一键脚本手动做：

```bash
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
python -m playwright install chromium
cp config.example.yaml config.yaml   # Windows: copy config.example.yaml config.yaml
```

---

## 安全护栏（默认全关）

`config.yaml` → `safety`：

```yaml
auto_submit: false          # 不自动点「提交」
auto_audit: false           # 不自动点「审核」
auto_voucher: false         # 不自动生成凭证
auto_voucher_save: false    # 不自动保存凭证
stop_on_error: true         # 出错立刻停
```

脚本默认只做到**保存/暂存**就停，触发不可逆动作直接中断并提示改哪个开关。

**放行顺序**：`--limit 1` 跑一条 → 进金蝶逐项核对 → 再开 `auto_submit`/`auto_audit` → 小批量 → 全量。
（临时放行用 `--yes`，**不改 config**。）

### ★★ 红线：凭证一律不删

凭证是「单据→专票→凭证」的终态与唯一账务留痕，金蝶无回收站，删除不可逆。
代码级硬拦截在 `src/common.py` 的 `Guard.FORBIDDEN` 和 `skill/scripts/kd_guard.py`，**与任何 config 开关、`--yes` 都无关，永远抛错**。
要改凭证走「凭证 - 修改」页；要留痕不进账走**作废**。

---

## 关键口径（已实测）

| 项 | 口径 |
|---|---|
| 开票方式 | 手工（票在电子税务局开好，金蝶事后补录，不填发票号） |
| 发票分录 | 合并（同组应收单合并生成 1 张发票） |
| 接口 | WebAPI 未开放 → 浏览器自动化（接管已登录 Chrome） |
| 出库单筛选 | 按账务日期区间（`FDate1`）上月26~当月25，不按单号 |
| 明细行粒度 | 每个物料一行，合并在「应收单→下推发票」这步做 |
| 复核人 | 姚玉平（专票必填项，kdcombo） |
| 改业务日期 | 只写 `FDATE`，`FENDDATE_H` 到期日自动联动 |
| 折扣额 | 下推表给负数，录金蝶必须正数（`push_chain` 底层 abs 兜底） |
| 单号月份 | `AR105-YYYYMM-####` 的 YYYYMM 取业务日期，与创建时间无关 |

### 摘要口径（`digest_rule.py`）

形如 `{客户}销售水性油墨{数量}公斤，光油{数量}公斤，原材料{数量}公斤`（措辞统一「水性油墨」，不写「水墨」）：

| 符号 | 含义 | 单位 |
|---|---|---|
| x | 存货类别「原材料」的计价数量之和 | 公斤 |
| y | 物料名含「光油」的计价数量之和 | 公斤 |
| t | 存货类别「包装物」（桶）的计价数量之和 | 个（不计入水性油墨） |
| s | 总量 − x − y − t（水性油墨） | 公斤 |

`omit_zero=True` 省略零项；全 0 时回退只写「水性油墨{总量}」。

---

## 历史经验沉淀（避坑）

- 详细静默失败清单（SF-01~SF-58）：`skill/references/silent-failures.md`
- 界面结构现场记录：`docs/界面结构速查.md`
- 页签选择器**唯一真相源**：`src/kd_tab_sel.py`（禁止在别处手写 `ul.kdMainTabsHeaderExtend`）
- 页签配额上限 15：列表页替换式不占配额，表单孤儿页累加，`ctl.remove()` 才能释放，点 logo 回主页不能清零
- 凡写必回读：写动作不抛异常 ≠ 值进去了，判据以**查询回读**为准
- 读界面/取列表行不可信：必配服务端反证源（SF-56、SF-58）
