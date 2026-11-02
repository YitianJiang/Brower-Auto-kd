# -*- coding: utf-8 -*-
"""11-02 重推批次收尾核验：14 张应收单 → 专票 → 凭证 → 摘要，逐项服务端回读。

判据（全部必须成立）：
  1) 应收明细行数 == 专票行数
  2) 不含税 / 税额 / 价税合计 三项各自精确相等（Decimal，无容差）
  3) 凭证 1122 借方合计 == 价税合计
  4) 该客户摘要里已写入「销售水性油墨…」

用法：
    python src/xcheck_repush_20261102.py                # 控制台 + CSV + HTML
    python src/xcheck_repush_20261102.py --quiet        # 只写文件

注意（踩过的坑）：
  · AR_receivable 的 FAllAmountFor / FNOTAXAMOUNTFOR 是**单头字段**，按行求和会翻倍，
    明细必须用 FNOTAXAMOUNTFOR_D / FTAXAMOUNTFOR_D / FALLAMOUNTFOR_D。
  · 只取 FBillNo 一个字段查明细，金蝶会把 N 行去重成 1 行，必须带 FMaterialId.FName 等明细字段。
"""
import argparse
import csv
import html
import json
import sys
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config  # noqa: E402
from kdquery import KdQuery, num  # noqa: E402

RESULT = ROOT / "output" / "repush_20261102_result.json"
OUT_CSV = ROOT / "output" / "repush_final_20261102.csv"
OUT_HTML = ROOT / "output" / "repush_final_20261102.html"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    items = json.load(open(RESULT, encoding="utf-8"))
    ars = [x["ar"] for x in items]
    q = ",".join(f"'{x}'" for x in ars)

    cfg = load_config()
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])
        ar_n, ar_all, ar_nt, ar_tx = {}, {}, {}, {}
        for r in kq.rows("AR_receivable",
                         "FBillNo,FMaterialId.FName,FPriceQty,"
                         "FNOTAXAMOUNTFOR_D,FTAXAMOUNTFOR_D,FALLAMOUNTFOR_D",
                         f"FBillNo in ({q})", top=500):
            k = r["FBillNo"]
            ar_n[k] = ar_n.get(k, 0) + 1
            ar_nt[k] = ar_nt.get(k, D(0)) + D(str(num(r["FNOTAXAMOUNTFOR_D"])))
            ar_tx[k] = ar_tx.get(k, D(0)) + D(str(num(r["FTAXAMOUNTFOR_D"])))
            ar_all[k] = ar_all.get(k, D(0)) + D(str(num(r["FALLAMOUNTFOR_D"])))

        iv_n, iv_no, iv_nt, iv_tx, iv_all = {}, {}, {}, {}, {}
        for r in kq.rows("IV_SALESIC",
                         "FBillNo,FSrcBillNo,FMATERIALID.FName,FPriceQty,"
                         "FNOTAXAMOUNT,FDETAILTAXAMOUNT,FALLAMOUNT",
                         f"FSrcBillNo in ({q})", top=500):
            k = str(r["FSrcBillNo"])
            iv_n[k] = iv_n.get(k, 0) + 1
            iv_no[k] = str(r["FBillNo"])
            iv_nt[k] = iv_nt.get(k, D(0)) + D(str(num(r["FNOTAXAMOUNT"])))
            iv_tx[k] = iv_tx.get(k, D(0)) + D(str(num(r["FDETAILTAXAMOUNT"])))
            iv_all[k] = iv_all.get(k, D(0)) + D(str(num(r["FALLAMOUNT"])))

        vrows = kq.rows("GL_VOUCHER",
                        "FBillNo,FDate,FVOUCHERGROUPNO,FEXPLANATION,"
                        "FACCOUNTID.FNumber,FDEBIT",
                        "FDATE >= '2026-10-28' and FEXPLANATION like '%销售水性油墨%'",
                        top=3000)
        br.close()

    by_amt, digest = {}, {}
    for r in vrows:
        e = str(r["FEXPLANATION"])
        if "销售水性油墨" in e:
            digest[e.split("销售水性油墨")[0]] = e
        if str(r["FACCOUNTID.FNumber"]) == "1122":
            amt = D(str(num(r["FDEBIT"])))
            by_amt.setdefault(amt, {})[(str(r["FBillNo"]), r["FVOUCHERGROUPNO"])] = None

    rows, bad = [], []
    for x in items:
        k, cust = x["ar"], x["customer"]
        n_a, n_i = ar_n.get(k, 0), iv_n.get(k, 0)
        t = ar_all.get(k, D(0))
        ok_rows = n_a == n_i and n_a > 0
        ok_amt = (ar_nt.get(k) == iv_nt.get(k) and ar_tx.get(k) == iv_tx.get(k)
                  and t == iv_all.get(k, D(0)))
        vlist = sorted((by_amt.get(t, {}) or {}).keys())
        ok_vch = bool(vlist)
        ok_dig = bool(digest.get(cust))
        if not (ok_rows and ok_amt and ok_vch and ok_dig):
            bad.append(k)
        rows.append({
            "应收单": k, "客户": cust, "业务日期": x["date"],
            "价税合计": f"{t:,.2f}", "应收行": n_a, "专票号": iv_no.get(k, ""),
            "专票行": n_i, "行数一致": "是" if ok_rows else "否",
            "票面价税": f"{iv_all.get(k, D(0)):,.2f}",
            "三项一致": "是" if ok_amt else "否",
            "凭证": ",".join(f"{f} 转{g}" for f, g in vlist) or "无",
            "凭证一致": "是" if ok_vch else "否",
            "摘要": digest.get(cust, ""), "摘要已写": "是" if ok_dig else "否",
            "标记": x["tag"],
        })

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    tot = sum(ar_all.get(x["ar"], D(0)) for x in items)
    h = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<title>11-02 重推 14 张收尾对账</title>',
         '<style>body{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#f6f7f9;',
         'color:#1c2024;margin:0;padding:28px}h1{font-size:20px;margin:0 0 4px}',
         '.sub{color:#6b7280;font-size:13px;margin-bottom:18px}',
         'table{border-collapse:collapse;width:100%;background:#fff;font-size:12.5px;',
         'box-shadow:0 1px 3px rgba(0,0,0,.08);border-radius:8px;overflow:hidden}',
         'th{background:#eef1f5;text-align:left;padding:9px 10px;font-weight:600;',
         'white-space:nowrap;border-bottom:1px solid #dde2e8}',
         'td{padding:8px 10px;border-bottom:1px solid #f0f2f5;vertical-align:top}',
         'tr:last-child td{border-bottom:none}',
         '.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}',
         '.ok{color:#0b7a4b;font-weight:600}.sum{margin-top:14px;font-size:13px;line-height:1.7}',
         '.d{color:#4b5563;font-size:11.5px;max-width:330px}',
         '.bad{background:#fff1f0}</style></head><body>',
         f'<h1>2026-11-02 重推批次 · 收尾对账</h1><div class="sub">杭州天龙油墨有限公司 · '
         f'{len(items)} 张应收单 → 专票 → 凭证 → 摘要　全部服务端回读　合计价税 ¥{tot:,.2f}</div>',
         '<table><tr><th>应收单</th><th>客户</th><th class="num">价税合计</th>',
         '<th class="num">应收/专票行</th><th>专票号</th><th>凭证</th><th>摘要</th></tr>']
    for r in rows:
        cls = "" if r["应收单"] not in bad else " class='bad'"
        h.append(f"<tr{cls}><td>{html.escape(r['应收单'])}</td>"
                 f"<td>{html.escape(r['客户'])}</td>"
                 f"<td class='num'>{r['价税合计']}</td>"
                 f"<td class='num'>{r['应收行']} / {r['专票行']} "
                 f"{'<span class=ok>✓</span>' if r['行数一致'] == '是' else '✗'}</td>"
                 f"<td>{html.escape(r['专票号'])}</td>"
                 f"<td>{html.escape(r['凭证'])}</td>"
                 f"<td class='d'>{html.escape(r['摘要'])}</td></tr>")
    verdict = (f"<b>{len(rows) - len(bad)} / {len(rows)} 全部一致</b>" if not bad
               else f"<b style='color:#c02929'>异常：{', '.join(bad)}</b>")
    h.append("</table><div class='sum'>" + verdict +
             "：① 应收明细行数 = 专票行数；② 不含税 / 税额 / 价税三项精确相等；"
             "③ 凭证 1122 借方 = 价税合计；④ 摘要已写入并保存。</div></body></html>")
    OUT_HTML.write_text("\n".join(h), encoding="utf-8")

    if not a.quiet:
        print(f"{'应收单':<20}{'票号':<18}{'凭证':<16}{'行数':<6}{'三项':<6}{'摘要':<6}")
        for r in rows:
            print(f"{r['应收单']:<20}{r['专票号']:<18}{r['凭证']:<16}"
                  f"{r['行数一致']:<6}{r['三项一致']:<6}{r['摘要已写']:<6}")
        print(f"\n合计价税 {tot:,.2f}")
        print("结论：" + ("14/14 全部一致 ✅" if not bad else f"异常 {bad}"))
        print(f"已写 {OUT_CSV.name} / {OUT_HTML.name}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
