# -*- coding: utf-8 -*-
"""
金蝶云星空 **只读查询** 通道（ExecuteBillQuery）。

用途：不打开任何单据、不产生锁、不写任何数据，直接从服务端把单据数据查出来。
用于对账、核查、定位"某张单为什么变成这样"。

用法：
    from kdquery import KdQuery
    kq = KdQuery(page)                       # page = 已登录的金蝶页面
    rows = kq.query("AR_receivable",          # 表单标识
                    "FBillNo,FMaterialId.FName,FNOTAXAMOUNTFOR_D",
                    "FBillNo = 'AR105-202609-0004'")
    for r in rows: print(r)

要点（2026-09-21 实测）：
  * 接口路径 `/K3Cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService
    .ExecuteBillQuery.common.kdsvc`，在**页面上下文里 fetch** 即可自动带登录态，
    无需自己拼 cookie / header。
  * 返回是**明文 JSON**（浏览器已解 gzip），不需要 kd_decode。
  * 返回形如 `[[v1, v2, ...], [...]]`，列顺序 = FieldKeys 顺序；
    出错时返回 `[[{"Result": {"ResponseStatus": {"Errors": [...]}}}]]`。
  * 字段名用**金蝶字段标识**，不是界面 label。带 `.FName` 的是基础资料名称。
  * ⚠️ 输出字段里混入"单据头"和"单据体（明细）"字段时，
    返回行数会被去重成"单据头条数"（例：应收单 46 行明细只回 1 行）。
    ⇒ **要查明细就只放明细字段**（如 `FMaterialId.FName,FNOTAXAMOUNTFOR_D`）。
  * 常用 FormId：应收单 `AR_receivable`、销售增值税专用发票 `IV_SALESIC`、
    总账凭证 `GL_VOUCHER`。

⚠️ 本模块**只读**：ExecuteBillQuery 是查询接口，不会改任何数据。
"""

from __future__ import annotations

FETCH_JS = """async (a) => {
    const [formId, fieldKeys, filter, top] = a;
    const url = '/K3Cloud/Kingdee.BOS.WebApi.ServicesStub.DynamicFormService'
              + '.ExecuteBillQuery.common.kdsvc';
    const body = { parameters: [ JSON.stringify({
        FormId: formId, FieldKeys: fieldKeys, FilterString: filter,
        TopRowCount: top || 0, StartRow: 0 })] };
    const r = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const t = await r.text();
    try { return { status: r.status, data: JSON.parse(t) }; }
    catch (e) { return { status: r.status, raw: t.slice(0, 600), err: String(e) }; }
}"""

# 已验证可用的字段（应收单 AR_receivable）
AR_HEAD_FIELDS = ("FBillNo,FDATE,FCreateDate,FModifyDate,FCreatorId.FName,"
                  "FModifierId.FName,FDocumentStatus,FBillTypeId.FName,"
                  "FBUSINESSTYPE,FCustomerId.FName,FAllAmountFor,"
                  "FNOTAXAMOUNT,FTaxAmount")
AR_ENTRY_FIELDS = ("FBillNo,FMaterialId.FName,FMaterialId.FNumber,FPriceQty,"
                   "FTaxPrice,FDiscountAmountFor,FNOTAXAMOUNTFOR_D,"
                   "FTAXAMOUNTFOR_D,FALLAMOUNTFOR_D,FSourceBillNo,"
                   "FSourceBillTypeId.FName,FLot.FNumber")
# 已验证可用的字段（销售增值税专用发票 IV_SALESIC）
IV_ENTRY_FIELDS = ("FBillNo,FMaterialId.FName,FPriceQty,FNotaxAmount,"
                   "FAllAmount,FAuxPrice")
IV_LINK_FIELDS = "FBillNo,FSrcBillNo"     # FSrcBillNo = 来源应收单号
IV_HEAD_FIELDS = ("FBillNo,FDate,FCreateDate,FDocumentStatus,"
                  "FAllAmount,FNOTAXAMOUNT,FTaxAmount")
# 总账凭证（GL_VOUCHER）
# ⚠️ 服务端回来的 FBillNo 是**内部 FID**（纯数字，如 205495），**不是界面上的「转 66」**。
#    所以下面 voucher_by_digest() 用「摘要原文 + 日期」定位，不要拿 FBillNo 当凭证字号做等值匹配。
GL_VOUCHER_FIELDS = ("FBillNo,FDate,FDocumentStatus,FEXPLANATION,"
                     "FACCOUNTID.FName,FACCOUNTID.FNumber,FDEBIT,FCREDIT")


class KdQueryError(RuntimeError):
    pass


class KdQuery:
    def __init__(self, page):
        self.page = page

    def query(self, form_id, field_keys, filter_string="", top=0):
        """
        只读查询。返回「行 × 列」的二维列表；列顺序 = field_keys 顺序。
        失败抛 KdQueryError（带服务端原文）。
        """
        from steps import eval_all_frames  # 延迟导入，避免循环依赖

        r = eval_all_frames(self.page, FETCH_JS,
                            [form_id, field_keys, filter_string, top])
        if not isinstance(r, dict):
            raise KdQueryError(f"查询无返回：{r}")
        if "data" not in r:
            raise KdQueryError(f"查询失败：{r}")
        d = r["data"]
        # 出错结构：[[{"Result": {...Errors...}}]]
        if d and isinstance(d[0], list) and d[0] and isinstance(d[0][0], dict):
            try:
                errs = d[0][0]["Result"]["ResponseStatus"]["Errors"]
                raise KdQueryError("; ".join(str(e.get("Message")) for e in errs))
            except (KeyError, TypeError):
                raise KdQueryError(f"未知返回结构：{str(d)[:300]}")
        return d

    def rows(self, form_id, field_keys, filter_string="", top=0):
        """返回 dict 列表（带列名），方便按字段名取值。"""
        data = self.query(form_id, field_keys, filter_string, top)
        keys = [k.strip() for k in field_keys.split(",")]
        return [dict(zip(keys, row)) for row in data]

    # ---- 便捷封装 ----

    def ar_head(self, bill_no):
        return self.rows("AR_receivable", AR_HEAD_FIELDS, f"FBillNo = '{bill_no}'")

    def ar_entries(self, bill_no, like=False):
        op = "like" if like else "="
        return self.rows("AR_receivable", AR_ENTRY_FIELDS,
                         f"FBillNo {op} '{bill_no}'")

    def ar_children(self, parent_bill_no):
        """查某张应收单下面挂的 `-00000N` 子单。"""
        return self.rows("AR_receivable", AR_HEAD_FIELDS,
                         f"FBillNo like '{parent_bill_no}-%'")

    def iv_entries(self, bill_no):
        return self.rows("IV_SALESIC", IV_ENTRY_FIELDS, f"FBillNo = '{bill_no}'")

    def iv_source_ar(self, bill_no):
        """查专票的**来源应收单号**（FSrcBillNo）。"""
        return self.rows("IV_SALESIC", IV_LINK_FIELDS, f"FBillNo = '{bill_no}'")

    def voucher_by_digest(self, digest_text, since="2026-09-01 00:00:00", top=200):
        """★ 按**摘要原文**定位总账凭证分录。

        为什么不能按凭证字号查：`GL_VOUCHER.FBillNo` 是**内部 FID**（如 205495），
        不是界面上的「转 66」——拿字号做等值匹配会 0 行或匹配错。
        摘要文本是唯一"写进服务端、又能从应收单明细精确重算出来"的锚点。

        Returns:
            {内部FID: [分录dict, ...]}；命中 >1 张时调用方应报错人工确认（可能重跑过）。
        """
        rows = self.rows("GL_VOUCHER", GL_VOUCHER_FIELDS,
                         f"FEXPLANATION = '{digest_text}' and FDATE >= '{since}'", top=top)
        out = {}
        for x in rows:
            out.setdefault(str(x.get("FBillNo")), []).append(x)
        return out

    # ---- ★ 保存后「反查单号」：解决界面读不到 FBILLNO 导致整链断掉（2026-09-22 提速）----
    #   背景：`read_bill_fields(["FBillNo"])` 找的是 `[id$="-FBillNo-EDITOR_c"]` 且要求可见，
    #   实测保存后经常读不到 → 日志「(单号未读到)」→ push_chain 拿不到专票号就直接抛错，
    #   只能人工另跑 find_ar.py 查号再 `--ar-no` 续跑（一次来回约 1~2 分钟）。
    #   这里用零副作用的 ExecuteBillQuery 反查，流程就不用断。

    def ar_by_source_bill(self, outstock_bill_no, top=50):
        """★ **精确关联**：按来源出库单号找应收单（比时间窗猜靠谱得多）。

        明细字段 FSourceBillNo 存的是来源出库单号；只放单头+来源两个字段时
        服务端按单头去重，正好拿到应收单号列表。
        """
        rows = self.rows("AR_receivable", "FBillNo,FSourceBillNo",
                         f"FSourceBillNo = '{outstock_bill_no}'", top=top)
        return sorted({str(r.get("FBillNo")) for r in rows if r.get("FBillNo")})

    def latest_ar(self, customer=None, since=None, top=5):
        """按创建时间倒序取最近应收单（可选按客户名过滤）。

        ⚠️ 这是**兜底**：只在界面读不到单号、且拿不到来源出库单号时才用。
           必须配合 since 时间窗（如"3 分钟前"）缩小范围，并在日志里显式告警 ——
           多人共用同一账套时，光靠"最近一条"可能拿到别人的单。
        """
        f = []
        if customer:
            f.append(f"FCustomerId.FName = '{customer}'")
        if since:
            f.append(f"FCreateDate >= '{since}'")
        rows = self.rows("AR_receivable", AR_HEAD_FIELDS,
                         " and ".join(f) if f else "", top=top)
        return sorted(rows, key=lambda x: str(x.get("FCreateDate") or ""), reverse=True)

    def latest_iv(self, since=None, top=5):
        """按创建时间倒序取最近专票。⚠️ 同上，兜底用途，须配时间窗 + 告警。"""
        f = [f"FCreateDate >= '{since}'"] if since else []
        rows = self.rows("IV_SALESIC", IV_HEAD_FIELDS,
                         " and ".join(f) if f else "", top=top)
        return sorted(rows, key=lambda x: str(x.get("FCreateDate") or ""), reverse=True)


def num(x, nd=2):
    """把查询出的金额安全转 float。"""
    try:
        return round(float(x or 0), nd)
    except (TypeError, ValueError):
        return 0.0
