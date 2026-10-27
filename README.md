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
2. **把本期的「开票数据.xlsx」放进 `data/`**（业务数据不进 git）

做完这两步，对 WorkBuddy 说「**金蝶批量下推**」即可，剩下的交给它跑批 + 排障。

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

## 核心脚本速查

```bash
cd kingdee-rpa
python start_browser.py                                        # 启动调试浏览器（登录金蝶用）
python src/run_batch_20261031.py --precheck                    # 开跑前自检（页签护栏/选择器）
python src/run_batch_20261031.py                               # 整批下推（读 data/ 里的单据）
python src/push_chain.py --ar-no <应收单号> --step voucher --yes   # 半成品续跑：补专票→凭证→摘要
python src/write_digest.py --ar-no <应收单号> --company <客户> --go  # 补写摘要
python src/write_digest_by_vchno.py --no <凭证号> --ar-no <应收单号> --company <客户> --go  # 凭证已生成只补摘要
python src/clean_tabs.py --go --keep-name 销售出库单列表       # 清场（保留工作锚点）
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

---

## 历史经验沉淀（避坑）

- 详细静默失败清单（SF-01~SF-56）：`skill/references/silent-failures.md`
- 界面结构现场记录：`docs/界面结构速查.md`
- 页签选择器**唯一真相源**：`src/kd_tab_sel.py`（禁止在别处手写 `ul.kdMainTabsHeaderExtend`）
- 页签配额上限 15：列表页替换式不占配额，表单孤儿页累加，`ctl.remove()` 才能释放，点 logo 回主页不能清零
- 凡写必回读：写动作不抛异常 ≠ 值进去了，判据以**查询回读**为准
