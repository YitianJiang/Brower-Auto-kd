# -*- coding: utf-8 -*-
"""
重推 2026-10-03 批次里「业务日期/到期日错落成 10 月」的 7 家。

背景：下推.xlsx 里这 7 家的应收单业务日期本该是 **2026-09-30**，实际落成了今天(10-03)，
     单号年月随之变成 `AR105-202610-0001..0007`（单号年月 = 业务日期年月，已实测）。
     用户已人工「还原」这 7 条链路（旧应收单/专票/凭证均已不存在，已只读确认）。

本脚本做的事：
  ⓪ 只读预检：① 按来源出库单反查应收单必须为空（否则会推重）
              ② 出库单价税合计 ≈ 目标价税 + 折扣额（金额对拍，防止拿错单）
  ① 每家清场（只留「销售出库单列表」）
  ② push_chain 全链（**业务日=发票日=2026-09-30**，--no-invoice-merge）
     —— 本批已加固：FDATE 只写前台表单 + 写后回读；保存后服务端回读业务日期
  ③ 反查应收单号 → write_digest.py 写凭证摘要
断点续跑：成功的家记进 output/repush_20261003b_result.json，重跑自动跳过。
失败策略：连续 2 家失败即停。

用法：python src/run_repush_20261003b.py            # 正式跑
      python src/run_repush_20261003b.py --precheck # 只跑预检，不推任何单
      python src/run_repush_20261003b.py --only 宁波日和新材料有限公司
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

os.environ["KD_NO_SHOT"] = "1"          # SF-48：截图会阻塞数分钟，批量一律关掉

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PY = sys.executable
RESULT = ROOT / "output" / "repush_20261003b_result.json"
XL = r"C:\Users\Administrator\Desktop\下推.xlsx"
sys.path.insert(0, str(SRC))


def parse_date_txt(t):
    """'9月30号' → '2026-09-30'（年份按 2026 处理，本批上下文固定）。"""
    m = re.match(r"^\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]\s*$", str(t))
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", str(t).strip()):
        return str(t).strip()
    raise SystemExit(f"无法解析日期：{t!r}")


def load_items():
    import openpyxl
    wb = openpyxl.load_workbook(XL, data_only=True)
    ws = wb["Sheet1"]
    plan = {x["customer"]: x for x in json.loads(
        (ROOT / "output" / "batch_20261003.json").read_text(encoding="utf-8"))}
    items = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[3]:
            continue
        c = str(r[3]).strip()
        p = plan.get(c)
        if not p:
            raise SystemExit(f"下推.xlsx 里的客户 {c!r} 在 batch_20261003.json 里找不到，请人工核对")
        items.append({"customer": c, "date": parse_date_txt(r[0]),
                      # ★ 折扣额录金蝶必须为正数（下推表里给负数，此处统一 abs）
                      "discount": abs(float(p["discount"])), "notax": p["notax"], "tax": p["tax"],
                      "all": p["all"], "bills": p["bills"]})
    return items


def precheck(items):
    """只读预检。返回 {customer: (ok, msg)}"""
    from playwright.sync_api import sync_playwright
    from common import load_config
    from kdquery import KdQuery, KdQueryError, num
    cfg = load_config()
    out = {}
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])
        try:
            kq.rows("AR_receivable", "FBillNo", "FBillNo = 'AR105-202610-0001'")
        except KdQueryError as e:
            print(f"!! 会话不可用：{str(e)[:150]}")
            br.close()
            return None
        for it in items:
            c = it["customer"]
            hit = sorted({x for b in it["bills"] for x in kq.ar_by_source_bill(b) if x})
            if hit:
                out[c] = (False, f"★ 已有下游应收单 {hit} —— 会推重，停止")
                continue
            tot = 0.0
            miss = []
            for b in it["bills"]:
                rs = kq.rows("SAL_OUTSTOCK",
                             "FBillNo,FDocumentStatus,FDate1,FCustomerID.FName,FBILLALLAMOUNT",
                             f"FBillNo = '{b}'", top=5)
                if not rs:
                    miss.append(b); continue
                tot += num(rs[0].get("FBILLALLAMOUNT"))
            want = float(it["all"]) + float(it["discount"])
            ok = (not miss) and abs(tot - want) <= 0.05
            msg = (f"出库单 FBILLALLAMOUNT {tot:,.2f} vs 目标(价税 {it['all']}+折扣 {it['discount']})"
                   f" = {want:,.2f}  差 {tot - want:+,.2f}")
            if miss:
                msg = f"★ 出库单查不到：{miss}"
            out[c] = (ok, msg)
        br.close()
    return out


def pre_clean():
    try:
        r = subprocess.run([PY, str(SRC / "clean_tabs.py"), "--go", "--keep-name", "销售出库单列表"],
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
    ap.add_argument("--precheck", action="store_true", help="只预检，不推单")
    ap.add_argument("--only", default="", help="只跑指定客户")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    items = load_items()
    if a.only:
        items = [x for x in items if x["customer"] == a.only]
        if not items:
            raise SystemExit(f"--only {a.only!r} 不在下推.xlsx 里")
    if a.limit:
        items = items[:a.limit]

    print("=" * 104)
    print(f"重推清单（{len(items)} 家，业务日期/到期日 = 下推.xlsx 第 A 列）")
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it['customer']:30s} 日期={it['date']}  折扣={it['discount']:>9}  "
              f"目标价税={it['all']:>12}  出库单={len(it['bills'])} 张")
    print("=" * 104)

    print("\n---- 只读预检 ----")
    pc = precheck(items)
    if pc is None:
        return 2
    blocked = []
    for it in items:
        c = it["customer"]
        ok, msg = pc.get(c, (False, "预检未返回"))
        print(("  ✓ " if ok else "  ✗ ") + f"{c[:26]:26s} {msg}")
        if not ok:
            blocked.append(c)
    if blocked:
        print(f"\n!! {len(blocked)} 家预检未通过：{blocked}")
        print("   预检不过一律**不推**（宁可不做，也不能推重/拿错单）。请人工确认后再跑。")
        if not a.only:
            items = [x for x in items if x["customer"] not in blocked]
    if a.precheck:
        print("\n--precheck：到此为止，没有推任何单。")
        return 0
    if not items:
        print("没有可推的家，退出。")
        return 1

    prev = []
    if RESULT.exists():
        try:
            prev = json.loads(RESULT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    done = {r["customer"] for r in prev if r.get("ok")}
    todo = [it for it in items if it["customer"] not in done]
    print(f"\n已完成/跳过 {len(items) - len(todo)} 家，**待推 {len(todo)} 家**")

    results = list(prev)
    fails = 0
    t0 = time.time()
    for i, it in enumerate(todo, 1):
        c = it["customer"]
        print(f"\n{'=' * 104}\n[{i}/{len(todo)}] {c}   日期={it['date']}\n"
              f"     单 {','.join(it['bills'])}\n"
              f"     折扣 {it['discount']}  目标价税 {it['all']}\n{'=' * 104}", flush=True)
        ts = time.time()
        print(f"  [⓪ 清场] {pre_clean()}", flush=True)

        cmd = [PY, str(SRC / "push_chain.py"),
               "--customer", c, "--bill-nos", ",".join(it["bills"]),
               "--discount", str(it["discount"]),
               "--target-notax", it["notax"], "--target-tax", it["tax"],
               "--target-all", it["all"],
               "--biz-date", it["date"], "--invoice-date", it["date"],
               "--no-invoice-merge", "--step", "voucher", "--yes"]
        rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
        rec = {"customer": c, "bills": it["bills"], "all": it["all"], "date": it["date"],
               "ok": False, "rc": rc}
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
        rec["ar_number"] = None
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
                print("!! 连续 2 家失败，停止（避免系统性故障下越跑越乱）", flush=True)
                break

    okn = sum(1 for r in results if r.get("ok"))
    tot = sum(float(r["all"]) for r in results if r.get("ok"))
    print(f"\n{'=' * 104}\n===== 重推结束：成功 {okn} / {len(items)} 家"
          f"（成功部分价税合计 {tot:,.2f}），用时 {(time.time() - t0) / 60:.1f} 分钟 =====\n"
          f"{'=' * 104}", flush=True)
    return 0 if okn == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
