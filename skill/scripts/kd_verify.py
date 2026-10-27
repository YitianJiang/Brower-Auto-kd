# -*- coding: utf-8 -*-
"""
全链核验（应收单 → 专票 → 凭证 + 摘要）—— **权威判据：ExecuteBillQuery 回读**。

把"跑完整链之后怎么确认真的成了"固化成代码。核验项与期望值来自经验教训：
  ① 应收单：状态 C / 行数 / 三项金额 / 业务日=到期日 / **子单数 = 0**
  ② 专票：状态 C / 行数 / 三项金额与应收单一致 / 业务日=发票日 / 复核人
  ③ 凭证：借贷平衡且 = 价税合计 / 状态 B（只保存未提交）/ 摘要逐行一致
  ④ 摘要：从应收单明细**重算** x/y/z/s，与写进凭证的文本**对拍**

★★ 两个必须记住的字段坑（否则核验结果本身就是错的）
  1. 凭证不能用 `FBillNo` 找 —— 服务端 `GL_VOUCHER.FBillNo` 是**内部 FID**（如 205495），
     不是界面上的「转 66」。**按摘要原文 + 日期**定位（`KdQuery.voucher_by_digest`）。
  2. 专票税额用行级 **`FDETAILTAXAMOUNT`**。误用单头 `FTaxAmount` 会**按行重复**
     （5 行合计变成 `2358.41 × 5`）。

依赖：kd_core.py + kd_query.py + digest_rule.py
用法（命令行）：
    python kd_verify.py --spec spec.json
    python kd_verify.py --ar AR105-202609-0001 --inv SVINV00033675 \
        --cust 慈溪市润科包装材料有限公司 --vch 转66 \
        --notax 18141.59 --tax 2358.41 --all 20500
spec.json 形如：
    [{"cust": "...", "ar": "AR...", "inv": "SVINV...", "vch": "转66",
      "notax": 18141.59, "tax": 2358.41, "all": 20500, "rows": 5}]
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from digest_rule import build_digest, classify_rows

D = lambda v: Decimal(str(v or 0))                    # noqa: E731
DAY = lambda v: str(v or "")[:10]                     # noqa: E731


class Checks:
    """收集 ✓/✗ 的核验器（不抛异常，全部跑完再汇总结论）。"""

    def __init__(self, logger=None):
        self.items = []
        self.log = logger or (lambda m: None)

    def check(self, name, cond, got=""):
        self.items.append({"name": name, "ok": bool(cond), "got": str(got)})
        self.log(f"   {'✓' if cond else '✗'} {name}" + (f"（实得 {got}）" if got != "" else ""))
        return bool(cond)

    @property
    def ok(self):
        return all(i["ok"] for i in self.items)

    def failed(self):
        return [i for i in self.items if not i["ok"]]


def verify_ar(kq, bill_no, spec=None, chk=None, top=200):
    """① 应收单：头 + 明细三项金额 + 子单数。"""
    spec = spec or {}
    chk = chk or Checks()
    head = kq.rows("AR_receivable",
                   "FBillNo,FDATE,FENDDATE_H,FDocumentStatus,FCustomerId.FName",
                   f"FBillNo = '{bill_no}'", top=3)[:1]
    if not head:
        chk.check(f"应收单 {bill_no} 存在", False, "查不到")
        return {"head": None, "amt": None}
    h = head[0]
    chk.check("应收单状态 = C", str(h.get("FDocumentStatus")) == "C",
              h.get("FDocumentStatus"))
    if spec.get("date"):
        chk.check(f"业务日 = {spec['date']}", DAY(h.get("FDATE")) == spec["date"], DAY(h.get("FDATE")))
        chk.check(f"到期日 = {spec['date']}", DAY(h.get("FENDDATE_H")) == spec["date"],
                  DAY(h.get("FENDDATE_H")))

    ent = kq.rows("AR_receivable", "FNOTAXAMOUNTFOR_D,FTAXAMOUNTFOR_D,FALLAMOUNTFOR_D",
                  f"FBillNo = '{bill_no}'", top=top)
    nt = sum(D(e.get("FNOTAXAMOUNTFOR_D")) for e in ent)
    tx = sum(D(e.get("FTAXAMOUNTFOR_D")) for e in ent)
    al = sum(D(e.get("FALLAMOUNTFOR_D")) for e in ent)
    if spec.get("rows"):
        chk.check(f"明细行数 = {spec['rows']}", len(ent) == spec["rows"], len(ent))
    if spec.get("notax") is not None:
        chk.check("不含税金额一致", nt == D(spec["notax"]), nt)
    if spec.get("tax") is not None:
        chk.check("税额一致", tx == D(spec["tax"]), tx)
    if spec.get("all") is not None:
        chk.check("价税合计一致", al == D(spec["all"]), al)

    kids = kq.rows("AR_receivable", "FBillNo", f"FBillNo like '{bill_no}-%'", top=20)
    chk.check("★ 子单数 = 0（未产生 -00000N）", not kids,
              f"{len(kids)} 张 {[k.get('FBillNo') for k in kids]}")
    return {"head": h, "amt": {"notax": nt, "tax": tx, "all": al, "rows": len(ent)}}


def verify_invoice(kq, bill_no, spec=None, ar_amt=None, chk=None, top=200):
    """② 专票：头 + 明细三项金额（★ 税额用 FDETAILTAXAMOUNT）。"""
    spec = spec or {}
    chk = chk or Checks()
    head = kq.rows("IV_SALESIC",
                   "FBillNo,FDATE,FINVOICEDATE,FDocumentStatus,FDRAWER,FPAYEE,F_PAEZ_COMBO",
                   f"FBillNo = '{bill_no}'", top=3)[:1]
    if not head:
        chk.check(f"专票 {bill_no} 存在", False, "查不到")
        return {"head": None, "amt": None}
    h = head[0]
    chk.check("专票状态 = C", str(h.get("FDocumentStatus")) == "C", h.get("FDocumentStatus"))
    if spec.get("date"):
        chk.check(f"业务日 = {spec['date']}", DAY(h.get("FDATE")) == spec["date"], DAY(h.get("FDATE")))
        chk.check(f"发票日 = {spec['date']}", DAY(h.get("FINVOICEDATE")) == spec["date"],
                  DAY(h.get("FINVOICEDATE")))
    if spec.get("checker"):
        chk.check(f"复核人 = {spec['checker']}", str(h.get("F_PAEZ_COMBO")) == spec["checker"],
                  h.get("F_PAEZ_COMBO"))

    ent = kq.rows("IV_SALESIC", "FNOTAXAMOUNT,FDETAILTAXAMOUNT,FALLAMOUNT",
                  f"FBillNo = '{bill_no}'", top=top)
    nt = sum(D(e.get("FNOTAXAMOUNT")) for e in ent)
    tx = sum(D(e.get("FDETAILTAXAMOUNT")) for e in ent)
    al = sum(D(e.get("FALLAMOUNT")) for e in ent)
    if spec.get("rows"):
        chk.check(f"专票行数 = {spec['rows']}（未合并）", len(ent) == spec["rows"], len(ent))
    if ar_amt:                                        # 与应收单对拍，比跟 Excel 对更硬
        chk.check("不含税 = 应收单", nt == ar_amt["notax"], nt)
        chk.check("税额 = 应收单", tx == ar_amt["tax"], tx)
        chk.check("价税合计 = 应收单", al == ar_amt["all"], al)
    return {"head": h, "amt": {"notax": nt, "tax": tx, "all": al, "rows": len(ent)}}


def verify_voucher(kq, cust, ar_no, spec=None, chk=None, since="2026-09-01 00:00:00"):
    """③ 凭证 + ④ 摘要：**从应收单明细重算摘要**，再用它定位凭证分录。

    Returns: {"digest": str, "calc": {...}, "vouchers": {FID: {...}}}
    """
    spec = spec or {}
    chk = chk or Checks()
    rows = kq.rows("AR_receivable", "FMaterialId.FName,FPriceQty,F_PAEZ_BASEPROPERTY2",
                   f"FBillNo = '{ar_no}'", top=200)
    calc = classify_rows([{"material": e.get("FMaterialId.FName"),
                           "qty": e.get("FPriceQty"),
                           "category": e.get("F_PAEZ_BASEPROPERTY2")} for e in rows])
    digest = build_digest(cust, calc["x"], calc["y"], calc["s"], omit_zero=True)
    chk.log(f"   重算 x={calc['x']} y={calc['y']} z={calc['z']} s={calc['s']}")
    chk.log(f"   摘要 = {digest!r}")

    byfid = kq.voucher_by_digest(digest, since=since)
    if not byfid:
        chk.check("按摘要原文定位到凭证", False, "0 张")
        return {"digest": digest, "calc": calc, "vouchers": {}}

    out = {}
    for fid, hit in byfid.items():
        dr = sum(D(x.get("FDEBIT")) for x in hit)
        cr = sum(D(x.get("FCREDIT")) for x in hit)
        st = sorted({str(x.get("FDocumentStatus")) for x in hit})
        out[fid] = {"debit": dr, "credit": cr, "status": st, "lines": hit,
                    "accts": [str(x.get("FACCOUNTID.FName")) for x in hit]}
    chk.check("★ 摘要只命中 1 张凭证", len(byfid) == 1,
              f"{len(byfid)} 张 {[(fid, len(h)) for fid, h in byfid.items()]}")

    fid, v = next(iter(out.items()))
    chk.check("借贷平衡", v["debit"] == v["credit"], f"借{v['debit']} 贷{v['credit']}")
    if spec.get("all") is not None:
        chk.check("借方 = 价税合计", v["debit"] == D(spec["all"]), v["debit"])
    chk.check("状态 = [B]（只保存未提交）", v["status"] == ["B"], v["status"])
    chk.log(f"   凭证（内部FID={fid}）分录 {len(v['lines'])} 行；科目={v['accts']}")
    if spec.get("vch"):
        chk.log(f"   ⚠️ 凭证字号「{spec['vch']}」请到凭证生成报告/凭证页人工核对，服务端不给")
    return {"digest": digest, "calc": calc, "vouchers": out}


def verify_unit(kq, spec, logger=None, since="2026-09-01 00:00:00", top=200):
    """一次跑完 ① ② ③④，返回 {"ok": bool, "checks": [...], "ar":..., "inv":..., "vch":...}。"""
    log = logger or (lambda m: None)
    chk = Checks(log)
    log(f"【{spec['cust']}】")
    ar = verify_ar(kq, spec["ar"], spec=spec, chk=chk, top=top)
    inv = verify_invoice(kq, spec["inv"], spec=spec, ar_amt=ar["amt"], chk=chk, top=top)
    vch = verify_voucher(kq, spec["cust"], spec["ar"], spec=spec, chk=chk, since=since)
    return {"ok": chk.ok, "checks": chk.items, "failed": chk.failed(),
            "ar": ar, "inv": inv, "vch": vch}


def main():
    ap = argparse.ArgumentParser(description="全链核验（只读 ExecuteBillQuery）")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--spec", help="JSON 文件：unit 列表")
    ap.add_argument("--ar", help="应收单号")
    ap.add_argument("--inv", help="专票号")
    ap.add_argument("--cust", help="客户名（摘要前缀）")
    ap.add_argument("--vch", help="凭证字号，仅用于打印提示（服务端不给）")
    ap.add_argument("--notax"), ap.add_argument("--tax"), ap.add_argument("--all")
    ap.add_argument("--rows", type=int, help="期望明细行数")
    ap.add_argument("--date", default="", help="期望业务日/到期日/发票日，如 2026-09-30")
    a = ap.parse_args()

    if a.spec:
        units = json.load(open(a.spec, encoding="utf-8"))
    else:
        units = [{"cust": a.cust, "ar": a.ar, "inv": a.inv, "vch": a.vch,
                  "notax": a.notax, "tax": a.tax, "all": a.all, "rows": a.rows,
                  "date": a.date or None}]

    from playwright.sync_api import sync_playwright  # noqa: E402
    from kd_query import KdQuery                      # noqa: E402
    say = lambda m: print(m, flush=True)               # noqa: E731
    ok_all = True
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(a.cdp)
        pg = br.contexts[0].pages[0]
        kq = KdQuery(pg)
        for u in units:
            r = verify_unit(kq, u, logger=say)
            ok_all = ok_all and r["ok"]
            say("")
        br.close()
    say("★★ 全部核验通过" if ok_all else "★★ 有核验未通过项，见上面 ✗")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
