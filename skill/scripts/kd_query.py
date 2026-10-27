# -*- coding: utf-8 -*-
"""
★ 只读查询通道 ExecuteBillQuery —— 核对 / 排障 / 验收的**唯一权威口径**。

为什么必须有它：
  * 打开单据会**锁单**、会污染现场；
  * 界面读数不可靠（FBILLNO 可能为空、列表可能没刷新）；
  * "这单怎么变成这样了"（创建人/创建时间/来源单/子单）只有服务端知道。

它不写任何数据、不加锁 —— 想查就查，随便查。

依赖：kd_core.py
用法：
    from kd_query import KdQuery
    kq = KdQuery(page)
    kq.ar_head("AR105-202609-0005")        # 应收单头
    kq.ar_entries("AR105-202609-0005")     # 明细
    kq.ar_children("AR105-202609-0005")    # `-00000N` 子单（期望空）
    kq.iv_source_ar("SVINV00033668")       # 专票来源应收单号

⚠️ 规则：
  1. 要查明细就**只放明细字段** —— 头字段+明细字段混放会被去重成"单据头条数"（46 行只回 1 行）。
  2. `FSrcBillNo` 存的是来源单的 **FID（数字）**，不是单号 → 用单号筛会报类型转换错或 0 行。
  3. 日期过滤串必须是 `'YYYY-MM-DD HH:MM:SS'`，带 `T` 会报转换错。
  4. 返回是**明文 JSON**（浏览器已解 gzip），不需要 kd_decode。
"""
from __future__ import annotations

from kd_core import eval_all_frames

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

# 已验证字段（2026-09-21）
AR_HEAD_FIELDS = ("FBillNo,FDATE,FCreateDate,FModifyDate,FCreatorId.FName,"
                  "FModifierId.FName,FDocumentStatus,FBillTypeId.FName,"
                  "FBUSINESSTYPE,FCustomerId.FName,FAllAmountFor,"
                  "FNOTAXAMOUNT,FTaxAmount")
AR_ENTRY_FIELDS = ("FBillNo,FMaterialId.FName,FMaterialId.FNumber,FPriceQty,"
                   "FTaxPrice,FDiscountAmountFor,FNOTAXAMOUNTFOR_D,"
                   "FTAXAMOUNTFOR_D,FALLAMOUNTFOR_D,FSourceBillNo,"
                   "FSourceBillTypeId.FName,FLot.FNumber")
IV_ENTRY_FIELDS = ("FBillNo,FMaterialId.FName,FPriceQty,FNotaxAmount,"
                   "FAllAmount,FAuxPrice")
IV_LINK_FIELDS = "FBillNo,FSrcBillNo"
IV_HEAD_FIELDS = ("FBillNo,FDate,FCreateDate,FDocumentStatus,"
                  "FAllAmount,FNOTAXAMOUNT,FTaxAmount")
GL_VOUCHER_FIELDS = ("FBillNo,FDate,FDocumentStatus,FEXPLANATION,"
                     "FACCOUNTID.FName,FACCOUNTID.FNumber,FDEBIT,FCREDIT")

FORMIDS = {"ar": "AR_receivable", "iv": "IV_SALESIC", "gl": "GL_VOUCHER"}


class KdQueryError(RuntimeError):
    pass


class KdQuery:
    def __init__(self, page):
        self.page = page

    def query(self, form_id, field_keys, filter_string="", top=0):
        """返回「行 × 列」二维列表；列顺序 = field_keys 顺序。失败抛 KdQueryError。"""
        r = eval_all_frames(self.page, FETCH_JS, [form_id, field_keys, filter_string, top])
        if not isinstance(r, dict) or "data" not in r:
            raise KdQueryError(f"查询失败：{r}")
        d = r["data"]
        if d and isinstance(d[0], list) and d[0] and isinstance(d[0][0], dict):
            try:
                errs = d[0][0]["Result"]["ResponseStatus"]["Errors"]
                raise KdQueryError("; ".join(str(e.get("Message")) for e in errs))
            except (KeyError, TypeError):
                raise KdQueryError(f"未知返回结构：{str(d)[:300]}")
        return d

    def rows(self, form_id, field_keys, filter_string="", top=0):
        """返回 dict 列表（带列名），按字段名取值。"""
        data = self.query(form_id, field_keys, filter_string, top)
        keys = [k.strip() for k in field_keys.split(",")]
        return [dict(zip(keys, row)) for row in data]

    # ---- 便捷封装 ----
    def ar_head(self, bill_no):
        return self.rows("AR_receivable", AR_HEAD_FIELDS, f"FBillNo = '{bill_no}'")

    def ar_entries(self, bill_no, like=False):
        return self.rows("AR_receivable", AR_ENTRY_FIELDS,
                         f"FBillNo {'like' if like else '='} '{bill_no}'")

    def ar_children(self, parent_bill_no):
        """查挂在某张应收单下面的 `-00000N` 子单（**期望返回空列表**）。"""
        return self.rows("AR_receivable", AR_HEAD_FIELDS,
                         f"FBillNo like '{parent_bill_no}-%'")

    def iv_entries(self, bill_no):
        return self.rows("IV_SALESIC", IV_ENTRY_FIELDS, f"FBillNo = '{bill_no}'")

    def iv_source_ar(self, bill_no):
        """专票的来源应收单号。⚠️ FSrcBillNo 存的是 FID 数字，不是单号。"""
        return self.rows("IV_SALESIC", IV_LINK_FIELDS, f"FBillNo = '{bill_no}'")

    def voucher_by_digest(self, digest_text, since="2026-09-01 00:00:00", top=200):
        """★ 按**摘要原文**定位凭证分录（SF-30 的标准解法）。

        为什么不按凭证字号查：`GL_VOUCHER.FBillNo` 是**内部 FID**（如 205495），
        不是界面上的「转 66」——拿字号做等值匹配会 0 行或匹配错。
        摘要文本是唯一写进服务端、又能精确重算出来的锚点。

        Returns:
            dict {内部FID: [分录dict, ...]}；命中 >1 张时调用方应报错人工确认。
        """
        rows = self.rows("GL_VOUCHER", GL_VOUCHER_FIELDS,
                         f"FEXPLANATION = '{digest_text}' and FDATE >= '{since}'", top=top)
        out = {}
        for x in rows:
            out.setdefault(str(x.get("FBillNo")), []).append(x)
        return out

    # ---- ★ 保存后「反查单号」：界面读不到 FBILLNO 时别让流程断（SF-35）----
    #   `read_bill_fields(["FBillNo"])` 找 `[id$="-FBillNo-EDITOR_c"]` 且要求可见，
    #   保存后经常读不到 →「(单号未读到)」→ 下一步拿不到单号直接 RuntimeError。
    #   这里用零副作用的 ExecuteBillQuery 反查，流程就不必断。

    def ar_by_source_bill(self, outstock_bill_no, top=50):
        """★ **精确关联**：按来源出库单号找应收单（比"最近一条"可靠）。

        明细字段 `FSourceBillNo` = 来源出库单号；只放单头+来源两个字段时
        服务端按单头去重，正好拿到应收单号列表。
        """
        rows = self.rows("AR_receivable", "FBillNo,FSourceBillNo",
                         f"FSourceBillNo = '{outstock_bill_no}'", top=top)
        return sorted({str(r.get("FBillNo")) for r in rows if r.get("FBillNo")})

    def latest_ar(self, customer=None, since=None, top=5):
        """按创建时间倒序取最近应收单（可选按客户过滤）。

        ⚠️ **兜底**用途：只在界面读不到单号、且拿不到来源出库单号时才用。
           必须配 `since` 时间窗（如"3 分钟前"），并在日志里 WARN ——
           多人共用账套时，光靠"最近一条"可能拿到别人的单。
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
        """按创建时间倒序取最近专票。⚠️ 同上：兜底用途，须配时间窗 + WARN。"""
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
