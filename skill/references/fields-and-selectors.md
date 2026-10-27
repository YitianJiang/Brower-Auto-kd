# 字段与选择器总表（金蝶云星空 8.2 网页端）

> 全部为 2026-09-18 ~ 09-21 在 `<你的金蝶域名>.ik3cloud.com`（恒星版 8.2.0）实测所得。
> **换账套/换版本可能变**，但命名规律稳定。调选择器前先跑 `probe.py` 确认。

## 一、控件 id 构成

```
{pageId(GUID)}-{FIELD}-{后缀}
```
| 控件类型 | 后缀 | 例子 |
|---|---|---|
| 文本框 | `-EDITOR_c` | `FBILLNO` → `[id$="-FBILLNO-EDITOR_c"]` |
| 日期 | `-EDITOR_c` | `FDATE` → `[id$="-FDATE-EDITOR_c"]`（走 kendoDatePicker API 写） |
| kdcombo（复核人） | **`-EDITOR`（没有 `_c`）** | `-F_PAEZ_COMBO-EDITOR` |
| kdtextarea | `-EDITOR_c` | 如 `FAR_REMARK` |
| 明细编辑框 | 动态生成 | 如 `input[id$="-FENTITY_FEXPLANATION_c"]` |

⚠️ **大小写混用**：条件行值框是全大写 `_ROW_ADD_PPART_VALUE_c`；
用 `[id$="_row_Add_PPART_VALUE_c"]` **命不中**，要转小写比（见 SF-25）。

⚠️ pageId 每次打开页面都会变，**永远用 `[id$="-FIELD-后缀"]` 后缀匹配**，不要写死完整 id。

## 二、工具栏菜单 id 后缀

### 列表页（销售出库单 / 应收单 / 专票）
| 文字 | id 后缀 |
|---|---|
| 过滤 | `-tbReadFilter` / `-tbFilter` |
| 刷新 | `-tbRefresh` |
| 新增 / 删除 | `-tbNew` / `-tbDelete` |
| 提交 / 审核 / 反审核 | `-tbSubmit` / `-tbApprove` / `-tbUnApprove` |
| **下推** | `-tbBusinessPush` |
| 凭证（split） | `-tbSplitButtonVch` / `-tbVoucher` |

### 单据详情页（应收单 - 新增）
| 文字 | id 后缀 |
|---|---|
| 新增 | `-tbSNew` |
| **保存**（split） | `-tbSplitSave` |
| 提交 / 审核 | `-tbSplitSubmit` / `-tbSplitApprove` |
| 选单 / 下推 | `-tbSelectBill` / `-tbSplitPush` |
| 关联查询 / 业务操作 | `-tbSplitRelaQuery` / `-tbSplitOperate` |
| 列表 / 选项 / 退出 | `-tbCallList` / `-tbOptionList` / `-tbClose` |
| 收款核销 | `-tbQuickMatchD` |

> id 里的 `-__definesub__` 要 replace 成 `-` 才是可见版 id。

### 凭证相关（下拉三项）
| 文字 | id 后缀 |
|---|---|
| 生成凭证 | `tbVoucherGenerate` |
| 查看业务凭证 | `tbBizvchView` / `tbViewBizVoucher` |
| 查看总账凭证 | `tbGlvchView` / `tbViewGlVoucher` |

## 三、单头字段（按页面）

### 应收单（`AR_receivable`）
| 字段 | data-role | 含义 |
|---|---|---|
| `FBILLNO` | kdtextfield | 单据编号（**保存后才生成**） |
| `FDATE` | datepicker | 业务日期（默认今天） |
| `FENDDATE_H` | datepicker | 到期日（默认今天；改 FDATE 会联动） |
| `FAR_REMARK` | kdtextarea | 备注（未切到对应 tab 读不到） |
| `FISPRICEEXCLUDETAX` / `FISTAX` | checkbox | 价外税 / 按含税单价录入 |
| `F_PAEZ_BASEPROPERTY` / `F_PAEZ_TEXT` / `F_PAEZ_DATE` | — | 自定义字段 |

> 客户 / 部门 / 业务员 / 结算币种 / 单据类型是**下拉或组合控件**，**不带 `-EDITOR_c`**。

### 销售增值税专用发票（`IV_SALESIC`）
| 字段 | data-role | 含义 |
|---|---|---|
| `FDATE` | datepicker | **业务日期**（下推后默认=今天，**必须改**） |
| `FINVOICEDATE` | datepicker | 发票日期（会自动带应收单的业务日期） |
| `FDRAWER` | kdtextfield | **开票人**（自动取登录用户） |
| `FPAYEE` | — | 收款人 |
| **`F_PAEZ_COMBO`** | **kdcombo** | **复核人**（**必录**，见 SF-09） |
| `FINVOICENO` / `FIVNUMBER` | kdtextfield | 发票号 / 发票号码（**不填**） |
| `FINVOICETITLE` / `FTAXREGISTERCODE` | — | 发票抬头 / 纳税人识别号 |
| `FTEL` / `FBANKCODE` / `FOPENBANKNAME` | — | 电话 / 银行账号 / 开户银行 |
| `FBILLNO` / `FREMARK` | — | 单据编号（保存后生成）/ 备注 |

## 四、明细字段（★ 应收单 ≠ 专票，混用会读到 0）

| 含义 | 应收单明细分录 | 销售增值税专用发票明细分录 |
|---|---|---|
| 不含税金额 | `FNOTAXAMOUNTFOR_D` | **`FNOTAXAMOUNT`**（= `FAMOUNTFOR`） |
| 税额 | `FTAXAMOUNTFOR_D` | **`FDETAILTAXAMOUNT`**（= `FDETAILTAXAMOUNTFOR`） |
| 价税合计 | `FALLAMOUNTFOR_D` | **`FALLAMOUNT`**（= `FALLAMOUNTFOR`） |
| 折扣额 | `FDISCOUNTAMOUNTFOR` | `FDISCOUNTAMOUNTFOR` |

⚠️ **专票没有 `FTAXAMOUNT` / `FTAXAMOUNTFOR` / `FNOTAXAMOUNTFOR` 字段**，
用这些名字读税额/不含税 **恒为 0**，会误判成"专票没金额"。

### 公共明细列
| data-field | 标题 |
|---|---|
| `FSEQ` | 序号 |
| `FMATERIALID` / `FMATERIALNAME` | 物料编码 / **物料名称** |
| `FPRICEUNITID` / `FPRICEQTY` | 计价单位 / **计价数量** |
| `FTAXPRICE` / `FPRICE` | 含税单价 / 单价 |
| `FENTRYTAXRATE` / `FENTRYDISCOUNTRATE` | 税率(%) / 折扣率(%) |
| **`F_PAEZ_BASEPROPERTY2`** | **存货类别**（摘要口径用） |
| `FLOT` / `FISFREE` | 批号 / 是否赠品 |
| `FSALUNITID` / `FSALQTY` | 销售单位 / 销售数量 |
| `FCOSTAMTSUM` | 成本金额 |
| `FORDERENTRYSEQ` | 销售订单行号 |

### 明细网格定位
| 页面 | grid 后缀 | 取数方式 |
|---|---|---|
| 应收单 | `-FENTITYDETAIL-c` | **DOM 扫描**（`data('kendoGrid')` 取不到） |
| 专票 | `-FENTRYPAGE_FSITEM` | 同上 |
| 列表页 | — | `data('kendoKDGrid')` 走数据源（见 SF-24） |

- 明细列索引：用 `th[data-field]` 拿；金额格带 `¥` 和千分位，解析前 `replace(/[¥,\s]/g,'')`。

### 凭证明细（`GL_VOUCHER`）
| `span[data-field]` | 含义 | 可编辑 |
|---|---|---|
| `FENTRYSEQ` | 序号 | 否 |
| **`FEXPLANATION`** | **摘要** | ✅ 是（编辑框 `input[id$="-FENTITY_FEXPLANATION_c"]`） |
| `FACCOUNTID` | 科目编码 | 是 |
| `FACCTFULLNAME` | 科目全名 | 否 |
| `FDETAILID` | 核算维度 | 是 |
| `FDEBIT` / `FCREDIT` | 借方 / 贷方 | 是 |
| `FSETTLETYPEID` / `FSETTLENO` | 结算方式 / 结算号 | 否 |

⚠️ **单头字段（账簿/凭证字/凭证号/期间）不在 `span[data-field]` 里**，只能抓外层容器 innerText。

⚠️⚠️ **凭证字号 ≠ `GL_VOUCHER.FBillNo`**（2026-09-22 实测，见 SF-30）：
服务端 `ExecuteBillQuery` 查回来的 `FBillNo` 是**内部 FID**（纯数字，如 `205495`），
而界面上的凭证字号是「转 66」这种。**两者对不上，不要做等值匹配。**
- 想核验凭证 → **按摘要原文 + 日期**定位（`KdQuery.voucher_by_digest()`）；
- 想要"转66"这个字号 → 只能从**界面 / 凭证生成报告**里读，服务端不给。

## 五、列表数据源字段（`grid_all_rows()` 读到的键名 ≠ 表头文案）

| 列表 | 字段 |
|---|---|
| 销售出库单列表 | `FBILLNO` / `FCUSTOMERID_FNAME` / `FDATE1`(账务日期) / `FREALQTY` / `FARJOINQTY` |
| 专票列表列头 | 红蓝字｜价税合计｜单据编号｜业务日期｜客户｜币别｜单据状态｜开票方式｜发票号｜发票日期｜结算组织｜销售组织… |

> 出库单列表**关键列索引**：日期=3 / 单据编号=4 / 账务日期=5 / 客户=7。
> 表头 20 个 `th`（带 `data-title`）；数据体是 `table.k-selectable`。

## 六、对话框与弹窗选择器

### 「选择单据」（下推）
| 元素 | 选择器 / 值 |
|---|---|
| 容器 | `.k-window.kd-dynamicform-active`（588×604，标题「选择单据」） |
| 目标单据 radio | `label.k-radio-label`，id 后缀 `-{FormId}-EDITOR` |
| 确定 / 取消 / 转换测试 | `_FBTNOK_c` / `_FBTNCANCEL_c` / `_FBTNTEST_c`（都是 **A 标签**） |

**可下推目标**：销售增值税专用发票 `IV_SALESIC`（本项目用）/ 销售普通发票 `IV_SALESOC` /
销项费用普通发票 `IV_SALEEXINV` / 销项费用增值税发票 `IV_SALEEXVATIN` /
应收单 `AR_RECEIVABLE` / 收款单 `AR_RECEIVEBILL` / 收款退款单 `AR_REFUNDBILL` / 付款申请单 `CN_PAYAPPLY`

**转换规则下拉两项**：`应收单 - 销售专用发票 - 分录合并` / `应收单 - 销售专用发票 - 分录不合并`（默认）

### 「列表过滤」对话框
| 元素 | 选择器 |
|---|---|
| 对话框本体 | 按标题文本含「列表过滤」定位（**别用 `.k-window[0]`**，见 SF-11） |
| 字段 ComboBox | `[id$="_row_Add_PART_Fileds"]`，data item 键 `Caption` / `FieldName` |
| 比较符 DropDownList | `[id$="_row_Add_PPART_CompareType"]`，键 `ID` / `Name` |
| **值单元格** | 按 id 含 `ppart_value`（小写比），见 SF-25 |
| 行间逻辑列 | `_row_Add_PART_Logic`：`0`=并且 / `1`=或者（语义 = **本行与下一行**） |
| 新增行 / 全部删除 | `li.kdFilterAdd` / 同类 `li.k-link`（**真实鼠标**，见 SF-08） |
| 确定 / 取消 | `[id$="_Filter-FBTNOK_c"]` / `[id$="_Filter-FBTNCANCEL_c"]`（**A 标签**，点外层 DIV 无反应） |

**比较符 ID**（共 32 个，**没有「介于/区间」**）：等于 `67` / 包含 `17` / 大于等于 `39` / 小于等于 `16` / 本月 `63`。
→ 区间日期写**两行**：`FDate1 >= start` 且 `FDate1 <= end`。

### 「凭证生成」对话框
| 元素 | 说明 |
|---|---|
| 网格列 | 账簿｜期间｜执行方式｜总账凭证生成方式｜匹配字段 |
| 默认行 | 杭州天龙油墨有限公司｜2026.9｜单据同时生成业务和总账凭证｜匹配汇总｜客户 |
| 行勾选框 | `tr > input.gridCheck.gridCheckCell` + `label.k-checkbox-label` |
| **最终按钮** | **`a[id$="-FVOUCHERGENE_c"]`**（文本「凭证生成」） |

⚠️ 必须先勾账簿行，否则只回 `ShowWarnningMsg`「请选择账簿！」。

### 凭证生成报告列表（`BAS_VchBuildReport`）
```
""(勾选列) | 系统 | 单据 | 单据编号 | 单据类型 | 账簿 | 业务凭证编码 | 总账凭证字 | 总账凭证号
           | 检查项 | 提示类型 | 提示信息 | 年 | 期 | 操作人 | 操作日期
```
- 成功：凭证号列有值、提示类型空/非"错误"；失败：`提示类型=错误`，`检查项` 指位置，`提示信息` 是原因。

## 七、标签页与勾选

| 对象 | 选择器 |
|---|---|
| 真标签页 | `ul.k-tabstrip-items > li.k-item.contentHeadEL` |
| 关闭按钮 | 标签内 `span.k-i-close.mainTabCloseButton` |
| 标签本体（切页） | 标签 li 左侧文字区（**真实鼠标**点） |
| 表头全选 | `input.gridCheckHeader`（`data-rowid="-99"`，**无** `data-field`） |
| 数据行勾选 | `input.gridCheck.gridCheckCell`（`data-field="SELECTED"` + `data-rowid="1..N"`） |
| 真实选中数 | 分页栏 `.kd-grid-pager-col` → 「已选 N 行」 |
| 行容器 | `table.k-selectable`（**必须叠可见性**，别用 `tbody tr`） |

⚠️ `.k-item` 会同时匹配工具栏菜单项 → 必须用 `li.k-item.contentHeadEL` 才是真标签。

## 八、顶部全功能搜索
| 元素 | 选择器 |
|---|---|
| 搜索图标 | `.kd-fullsearch-searchicon`（**面板打开时被隐藏**，先 Esc） |
| 输入框 | `input.kd-appplat-searchinput`（placeholder「请输入内容进行搜索」） |
| 结果面板 / 条目 | `.kdfullsearchpopextend` / `.kd-fullsearch-item-container` |

⚠️ 重名用**菜单路径**消歧：`销售出库单列表` 有 3 条，正确的是「供应链 > 销售管理 > 出货处理」。

⚠️⚠️ **点结果条目必须真实鼠标**（2026-09-22 实测，见 SF-32）：
`locator.click()` / `get_by_text(...).click()` 会超时，报
`<div class="kd-form-collapse-container"> … intercepts pointer events`
——条目 DOM 可见，但指针事件被功能页外壳的折叠容器接走。正确姿势：
```js
el.scrollIntoView({block:'start'});                  // 先防坐标在视口外（实测出现过 y=-32）
const r = el.getBoundingClientRect();
const cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
document.elementFromPoint(cx, cy) === el || el.contains(document.elementFromPoint(cx, cy));  // 命中检测
// → 再 page.mouse.click(cx, cy)
```
另外：**目标列表已经开着就直接 `activate_tab`，别再搜**（会开出第二个同名标签 → SF-29）；
搜之前先关掉占用焦点的同名标签。

## 九、ExecuteBillQuery 字段（只读查询）

**FormId**：应收单 `AR_receivable`、销售增值税专用发票 `IV_SALESIC`、总账凭证 `GL_VOUCHER`。

| 用途 | 已验证字段 |
|---|---|
| 应收单头 | `FBillNo,FDATE,FCreateDate,FModifyDate,FCreatorId.FName,FModifierId.FName,FDocumentStatus,FBillTypeId.FName,FBUSINESSTYPE,FCustomerId.FName,FAllAmountFor,FNOTAXAMOUNT,FTaxAmount` |
| 应收单明细 | `FBillNo,FMaterialId.FName,FMaterialId.FNumber,FPriceQty,FTaxPrice,FDiscountAmountFor,FNOTAXAMOUNTFOR_D,FTAXAMOUNTFOR_D,FALLAMOUNTFOR_D,FSourceBillNo,FSourceBillTypeId.FName,FLot.FNumber` |
| 专票明细 | `FBillNo,FMaterialId.FName,FPriceQty,FNotaxAmount,FAllAmount,FAuxPrice` |
| 专票来源 | `FBillNo,FSrcBillNo`（⚠️ 存的是 **FID 数字**，见 SF-18） |

**不存在的字段**（写了会报错/返空）：`FEntryID / FEntity_FEntryID / FSEQ / FRemarks / FNote / FCheckDate / FAmount`（专票）。

⚠️ 头字段与明细字段**混放会被去重**（见 SF-20）。
