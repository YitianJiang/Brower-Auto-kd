# -*- coding: utf-8 -*-
"""
从源头出库单下推「剩余下推.xlsx」里 3 家金额口径特殊的客户（2026-10-03 第二批补推）。

为什么单独一个脚本
------------------
这 3 家的金额口径和 run_rest_20261003d.py 的自动「区间内未下推单对拍」不同：

1. 祥恒（常州）包装：表里金额 46,736.40 + 折扣 7,407.85 = 54,144.25，对应的是
   **8/25 的 XSCKD-105-202608-0141**（含税 54,144.26），但表里区间填 08-26 起 →
   自动对拍抓到的是 9/25 的新单 0174（69,391.46）。按用户口径：**推 8/25 的 0141**。

2. 淮安秉信环保：出库单含税 417,882.12 vs 表里 417,882.06，差 6 分 = 拆税四舍五入
   尾差。按用户口径：**放行，按表里三数精确对齐**（脚本 detail_align_totals 会把
   最大行不含税/税额顶到表里值）。

3. 杭州广鸿印刷：区间内 7 张出库单合计 69,479.00，但表里开票价税 67,719.00 是
   **6 张之和**（漏了 0075 的 1,760）。同时库里有一张手工录的**退货应收单**
   AR105-202609-0012（价税 -1,760 = 不含税 -1,557.52 + 税额 -202.48）。
   用户口径：**退货单 + 现在推的正数单，两者三数之和 = 表里实际开票**。
   即正数单对齐目标 = 表里 − 退货单：
     不含税 = 59,928.32 − (−1,557.52) = 61,485.84
     税额   =  7,790.68 − (−202.48)   =  7,993.16
     价税   = 67,719.00 − (−1,760.00) = 69,479.00  ✓（= 7 张出库单合计）

用法：
    python src/run_fix3_20261003.py            # 正式推 3 家
    python src/run_fix3_20261003.py --only 杭州广鸿印刷材料有限公司
断点续跑：output/fix3_20261003_result.json（ok=true 的家自动跳过）。
失败策略：连续 2 家失败即停。
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["KD_NO_SHOT"] = "1"          # SF-48：截图会阻塞数分钟，批量一律关掉

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PY = sys.executable
RESULT = ROOT / "output" / "fix3_20261003_result.json"
BIZ_DATE = "2026-09-30"                 # 业务日期 / 发票日期（用户口径，本批统一）

# ★ 3 家手工覆盖：精确出库单号 + 精确三数 + 折扣（折扣已 abs 为正数）。
#   all 是「开票价税合计」（= 表里第 8 列），不是出库单含税毛额。
MANUAL = {
    "祥恒（常州）包装有限公司": {
        "bills": ["XSCKD-105-202608-0141"],
        "notax": 41359.66, "tax": 5376.74, "all": 46736.40, "disc": 7407.85,
    },
    "淮安秉信环保包装有限公司": {
        "bills": ["XSCKD-105-202609-0163"],
        "notax": 369807.14, "tax": 48074.92, "all": 417882.06, "disc": 0.0,
    },
    "杭州广鸿印刷材料有限公司": {
        "bills": ["XSCKD-105-202609-0003", "XSCKD-105-202609-0062",
                  "XSCKD-105-202609-0073", "XSCKD-105-202609-0074",
                  "XSCKD-105-202609-0075", "XSCKD-105-202609-0134",
                  "XSCKD-105-202609-0142"],
        "notax": 61485.84, "tax": 7993.16, "all": 69479.00, "disc": 0.0,
    },
}


def pre_clean():
    try:
        r = subprocess.run([PY, str(SRC / "clean_tabs.py"), "--go",
                            "--keep-name", "销售出库单列表"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=240)
        lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        return " | ".join(lines[-2:]) if lines else f"（无输出 rc={r.returncode}）"
    except Exception as e:  # noqa: BLE001
        return f"pre_clean 异常（继续）：{type(e).__name__}: {e}"


def find_ar(bills):
    from playwright.sync_api import sync_playwright
    from common import load_config
    from kdquery import KdQuery
    cfg = load_config()
    got = []
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])
        for b in bills:
            got += [x for x in kq.ar_by_source_bill(b) if x]
        br.close()
    return sorted(set(got))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑指定客户")
    a = ap.parse_args()

    items = [{"customer": c, **v} for c, v in MANUAL.items()]
    if a.only:
        items = [x for x in items if x["customer"] == a.only]
        if not items:
            raise SystemExit(f"--only {a.only!r} 不在 MANUAL 清单里")

    print("=" * 104)
    print(f"补推 3 家（业务日期/发票日期 = {BIZ_DATE}，精确单号 + 三数精确对齐）")
    for it in items:
        print(f"  · {it['customer']:24s} 单 {','.join(it['bills'])}\n"
              f"      不含税 {it['notax']:>12,.2f} / 税额 {it['tax']:>10,.2f}"
              f" / 价税 {it['all']:>12,.2f}"
              + (f" / 折扣 {it['disc']:,.2f}" if it["disc"] else ""))
    print("=" * 104)

    prev = []
    if RESULT.exists():
        try:
            prev = json.loads(RESULT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    done = {r["customer"] for r in prev if r.get("ok")}
    pend = [it for it in items if it["customer"] not in done]
    print(f"已完成/跳过 {len(items) - len(pend)} 家，待推 {len(pend)} 家")

    results = list(prev)
    fails = 0
    t0 = time.time()
    for i, it in enumerate(pend, 1):
        c = it["customer"]
        print(f"\n{'=' * 104}\n[{i}/{len(pend)}] {c}\n"
              f"     单 {','.join(it['bills'])}\n"
              f"     不含税 {it['notax']:,.2f} / 税额 {it['tax']:,.2f} / 价税 {it['all']:,.2f}"
              + (f" / 折扣 {it['disc']:,.2f}\n" if it["disc"] else "\n")
              + f"{'=' * 104}", flush=True)
        ts = time.time()
        print(f"  [⓪ 清场] {pre_clean()}", flush=True)

        cmd = [PY, str(SRC / "push_chain.py"),
               "--customer", c, "--bill-nos", ",".join(it["bills"]),
               "--target-notax", f"{it['notax']}", "--target-tax", f"{it['tax']}",
               "--target-all", f"{it['all']}",
               "--biz-date", BIZ_DATE, "--invoice-date", BIZ_DATE,
               "--no-invoice-merge", "--step", "voucher", "--yes"]
        if it["disc"]:
            cmd += ["--discount", f"{it['disc']}"]
        rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
        rec = {"customer": c, "bills": it["bills"], "all": it["all"],
               "notax": it["notax"], "tax": it["tax"], "disc": it["disc"],
               "date": BIZ_DATE, "ok": False, "rc": rc}
        if rc == 0:
            rec["chain_ok"] = True
            try:
                ars = find_ar(it["bills"])
                rec["ar"] = ars
                if ars:
                    ar_no = ars[-1]
                    rec["ar_used"] = ar_no
                    wd = subprocess.run([PY, str(SRC / "write_digest.py"),
                                         "--ar-no", ar_no, "--company", c, "--go"],
                                        cwd=str(ROOT)).returncode
                    rec["digest_ok"] = (wd == 0)
                    if wd != 0:
                        print(f"!! {c} 摘要写失败（rc={wd}）", flush=True)
                    else:
                        rec["ok"] = True
                else:
                    print(f"!! {c} 反查不到应收单号", flush=True)
            except Exception as e:  # noqa: BLE001
                rec["err"] = f"{type(e).__name__}: {str(e)[:200]}"
                print(f"!! {c} 写摘要异常：{rec['err'][:150]}", flush=True)
        else:
            print(f"!! {c} push_chain 失败（rc={rc}）—— 半成品不自动重试，"
                  f"请先只读核实已生成到哪一步，再人工续跑", flush=True)
        rec["sec"] = round(time.time() - ts, 1)
        results = [r for r in results if r.get("customer") != c]
        results.append(rec)
        RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-- {c} 用时 {rec['sec']}s  ok={rec['ok']}  "
              f"应收单={rec.get('ar_used') or rec.get('ar') or '—'}", flush=True)
        if rec["ok"]:
            fails = 0
        else:
            fails += 1
            if fails >= 2:
                print("!! 连续 2 家失败，停止", flush=True)
                break

    okn = sum(1 for r in results if r.get("ok"))
    print(f"\n{'=' * 104}\n===== 补推结束：成功 {okn} / {len(items)} 家"
          f"，用时 {(time.time() - t0) / 60:.1f} 分钟 =====\n{'=' * 104}", flush=True)
    return 0 if okn == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
