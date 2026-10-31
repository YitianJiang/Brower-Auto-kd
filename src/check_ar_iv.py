# -*- coding: utf-8 -*-
"""应收单 ↔ 专票 三项金额对账（只读）

用法：
    python src/check_ar_iv.py --org 杭州天龙油墨有限公司 --month 2026-09
    python src/check_ar_iv.py --org 杭州天龙油墨有限公司 --month 2026-10 --out-prefix oct

做什么：
  1. 按「销售组织 + 业务日期区间」取应收单（单头 + 明细）；
  2. 用 `IV_SALESIC.FSrcBillNo` 反查专票（票可能开在本月或更晚，按票号反查不受日期窗口限制）；
  3. 逐单比对 **不含税 / 税额 / 价税合计 三项**（Decimal 精确到分，差 1 分也报）；
  4. 有差时按物料聚合定位「漏开了哪几行」；
  5. 输出 `<out-prefix>_ar_iv.csv`（逐单）+ 终端摘要。

判据要点（踩过的坑，见 skill/references/silent-failures.md）：
  * 金额只用**明细行原币字段**求和（FNOTAXAMOUNTFOR_D / FTAXAMOUNTFOR_D / FALLAMOUNTFOR_D），
    不用单头 FNOTAXAMOUNT/FTaxAmount（可能本位币）；FBillAllAmount/FALLAMOUNTFOR 不存在。
  * 专票一张可覆盖多张应收 → 按 (票号, FSrcBillNo) 逐行分摊，不能拿整张票额比单张应收。
  * 查明细不能混单头字段（会被去重成表头条数，SF-66）。
  * ExecuteBillQuery 单次最多 1 万行且静默取最早数据（SF-68）→ 必须按日期分段并打印行数自检。
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import io
import os
import sys
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright  # noqa: E402

from common import load_config  # noqa: E402
from kdquery import KdQuery  # noqa: E402

D = lambda x: Decimal(str(x or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)  # noqa: E731
M = lambda x: f"{D(x):,.2f}"  # noqa: E731

AR_HEAD = "FBillNo,FBillTypeId.FName,FDATE,FDocumentStatus,FCustomerId.FName,FAllAmountFor"
AR_DET = ("FBillNo,FMaterialId.FNumber,FMaterialId.FName,FPriceQty,"
          "FNOTAXAMOUNTFOR_D,FTAXAMOUNTFOR_D,FALLAMOUNTFOR_D")
IV_DET = ("FBillNo,FSrcBillNo,FDate,FDocumentStatus,FCustomerId.FName,"
          "FMaterialId.FName,FPriceQty,FNotaxAmount,FDETAILTAXAMOUNT,FAllAmount")

KEYS = ("notax", "tax", "all")
NAMES = {"notax": "不含税", "tax": "税额", "all": "价税合计"}


def month_range(month: str) -> tuple[str, str]:
    y, m = (int(x) for x in month.split("-")[:2])
    start = dt.date(y, m, 1)
    end = (dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1) - dt.timedelta(days=1))
    return start.isoformat(), end.isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True, help="销售组织名称，如 杭州天龙油墨有限公司")
    ap.add_argument("--month", required=True, help="业务月份 YYYY-MM")
    ap.add_argument("--out-prefix", default=None, help="输出文件名前缀（默认用 month）")
    args = ap.parse_args()

    m_start, m_end = month_range(args.month)
    prefix = args.out_prefix or args.month.replace("-", "")
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
    os.makedirs(out_dir, exist_ok=True)

    cfg = load_config()
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])

        ar = kq.rows("AR_receivable", AR_HEAD,
                     f"FDATE>='{m_start}' and FDATE<='{m_end}' and FSaleOrgId.FName='{args.org}'",
                     top=5000)
        if not ar:
            print(f"[!] {args.org} {args.month} 没有应收单")
            br.close()
            return 1
        print(f"AR 单头 {len(ar)} 张：{collections.Counter(r['FBillTypeId.FName'] for r in ar)}")
        print(f"  状态 {collections.Counter(r['FDocumentStatus'] for r in ar)}")
        print(f"  日期 {min(str(r['FDATE'])[:10] for r in ar)} ~ {max(str(r['FDATE'])[:10] for r in ar)}")

        bills = [r["FBillNo"] for r in ar]
        det_rows = []
        for i in range(0, len(bills), 40):
            chunk = bills[i:i + 40]
            fs = "FBillNo in (" + ",".join(f"'{b}'" for b in chunk) + ")"
            rs = kq.rows("AR_receivable", AR_DET, fs, top=5000)
            det_rows += rs
            print(f"  明细批次 {i // 40 + 1}: {len(chunk)} 单 → {len(rs)} 行")
        print(f"AR 明细合计 {len(det_rows)} 行 / 覆盖 {len({r['FBillNo'] for r in det_rows})} 单")

        iv = []
        # ★ 不能按 `FSrcBillNo LIKE 'AR%' + 日期段` 拉票：别的账套前缀也是 AR，
        #   一段能塞进上万行 → 被 ExecuteBillQuery 的 1 万行上限截断（SF-68），
        #   结果「有票单大面积变无票」。改成两阶段、按单号 IN 精确取：
        #   ① 用本批应收单号反查票号；② 按票号拉整票明细（含跨单分摊的全部行）。
        cand = []
        for i in range(0, len(bills), 40):
            chunk = bills[i:i + 40]
            fs = "FSrcBillNo in (" + ",".join(f"'{b}'" for b in chunk) + ")"
            rs = kq.rows("IV_SALESIC", "FBillNo,FSrcBillNo", fs, top=8000)
            cand += rs
            print(f"  反查票号 {i // 40 + 1}: {len(chunk)} 单 → {len(rs)} 行")
        iv_nos = sorted({r["FBillNo"] for r in cand})
        print(f"  候选专票 {len(iv_nos)} 张")
        for i in range(0, len(iv_nos), 30):
            chunk = iv_nos[i:i + 30]
            fs = "FBillNo in (" + ",".join(f"'{b}'" for b in chunk) + ")"
            rs = kq.rows("IV_SALESIC", IV_DET, fs, top=8000)
            iv += rs
            print(f"  票明细 {i // 30 + 1}: {len(chunk)} 票 → {len(rs)} 行")
        print(f"  专票明细合计 {len(iv)} 行 / {len({r['FBillNo'] for r in iv})} 票")
        br.close()

    det = collections.defaultdict(list)
    for r in det_rows:
        det[r["FBillNo"]].append(r)

    ars = {}
    for r in ar:
        b = r["FBillNo"]
        rows = det.get(b, [])
        ars[b] = {
            "bill": b, "type": r["FBillTypeId.FName"], "date": str(r["FDATE"])[:10],
            "status": r["FDocumentStatus"], "cust": r["FCustomerId.FName"],
            "head_amt": D(r["FAllAmountFor"]), "rows": len(rows),
            **{k: sum(D(x[f]) for x in rows) for k, f in zip(KEYS, (
                "FNOTAXAMOUNTFOR_D", "FTAXAMOUNTFOR_D", "FALLAMOUNTFOR_D"))},
        }

    # 专票：按 (票号, 来源应收) 分摊
    iv_ar = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"notax": Decimal("0"), "tax": Decimal("0"), "all": Decimal("0"),
                 "lines": 0, "mats": {}}))
    iv_bill_lines = collections.Counter()
    iv_bill_date = {}
    for r in iv:
        b, src = r["FBillNo"], r["FSrcBillNo"] or ""
        iv_bill_lines[b] += 1
        iv_bill_date.setdefault(b, str(r["FDate"])[:10])
        s = iv_ar[b][src]
        nt, tx, al = D(r["FNotaxAmount"]), D(r["FDETAILTAXAMOUNT"]), D(r["FAllAmount"])
        s["notax"] += nt
        s["tax"] += tx
        s["all"] += al
        s["lines"] += 1
        m = s["mats"].setdefault(r["FMaterialId.FName"], [Decimal("0")] * 3)
        m[0] += D(r["FPriceQty"])
        m[1] += nt
        m[2] += al

    ar2iv = collections.defaultdict(list)
    for b, by_ar in iv_ar.items():
        for src, s in by_ar.items():
            if src:
                ar2iv[src].append((b, s))

    out = []
    for b, e in ars.items():
        hits = ar2iv.get(b, [])
        ivv = {k: sum((s[k] for _, s in hits), Decimal("0")) for k in KEYS}
        diff = {k: ivv[k] - e[k] for k in KEYS}
        miss = []
        if hits and any(v != 0 for v in diff.values()):
            am = {}
            for x in det[b]:
                k = x["FMaterialId.FName"]
                m = am.setdefault(k, [Decimal("0")] * 3)
                m[0] += D(x["FPriceQty"])
                m[2] += D(x["FALLAMOUNTFOR_D"])
            im = {}
            for _, s in hits:
                for k, v in s["mats"].items():
                    m = im.setdefault(k, [Decimal("0")] * 3)
                    for i in range(3):
                        m[i] += v[i]
            for k, v in am.items():
                got = im.get(k, [Decimal("0")] * 3)
                if v[0] - got[0] > Decimal("0.0001") or v[2] != got[2]:
                    miss.append({"mat": k, "ar_qty": str(v[0]), "iv_qty": str(got[0]),
                                 "gap": str(v[2] - got[2])})
        out.append({**e, "iv_bills": ",".join(x[0] for x in hits),
                    "iv_lines": sum(iv_bill_lines[x[0]] for x in hits),
                    **{"iv_" + k: ivv[k] for k in KEYS},
                    "diff_all": diff["all"], "diff_notax": diff["notax"],
                    "diff_tax": diff["tax"], "miss": miss})

    has = [r for r in out if r["iv_bills"]]
    no = [r for r in out if not r["iv_bills"]]
    bad = [r for r in has if any(r["diff_" + k] != 0 for k in KEYS)]

    print(f"\n有票 {len(has)} / 无票 {len(no)} / 三项有差 {len(bad)}")
    if no:
        print("无票：")
        for r in no:
            print(f"   {r['bill']} {r['cust']} {r['type']} {M(r['all'])} 状态={r['status']}")
    if bad:
        print("三项有差：")
        for r in sorted(bad, key=lambda x: x["diff_all"]):
            print(f"   {r['bill']} {r['cust']} 应收 {M(r['all'])} / 票 {M(r['iv_all'])}"
                  f"  差 不含税 {M(r['diff_notax'])} 税额 {M(r['diff_tax'])} 价税 {M(r['diff_all'])}"
                  f"  票={r['iv_bills']}")
            for m in r["miss"]:
                print(f"      少: {m['mat']} AR {m['ar_qty']} / 票 {m['iv_qty']} 差 {M(m['gap'])}")

    s_ar = {k: sum(r[k] for r in has) for k in KEYS}
    s_iv = {k: sum(r["iv_" + k] for r in has) for k in KEYS}
    print("\n三项合计（有票单）:")
    for k in KEYS:
        print(f"  {NAMES[k]:<6} 应收 {M(s_ar[k]):>16}  专票 {M(s_iv[k]):>16}"
              f"  差 {M(s_iv[k] - s_ar[k]):>12}")

    path = os.path.join(out_dir, f"{prefix}_ar_iv.csv")
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["应收单", "类型", "业务日期", "状态", "客户", "明细行",
                    "应收不含税", "应收税额", "应收价税", "专票", "票明细行",
                    "票不含税", "票税额", "票价税", "差_不含税", "差_税额", "差_价税",
                    "漏开物料"])
        for r in out:
            w.writerow([r["bill"], r["type"], r["date"], r["status"], r["cust"], r["rows"],
                        r["notax"], r["tax"], r["all"], r["iv_bills"], r["iv_lines"],
                        r["iv_notax"], r["iv_tax"], r["iv_all"],
                        r["diff_notax"], r["diff_tax"], r["diff_all"],
                        "；".join(f"{m['mat']} 缺{m['ar_qty']}/{m['iv_qty']}kg {m['gap']}"
                                 for m in r["miss"])])
    print(f"\n-> {path}")
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
