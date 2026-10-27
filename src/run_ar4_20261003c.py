# -*- coding: utf-8 -*-
"""从**已有应收单**继续跑后半段：专票 → 提交 → 生成凭证 → 写摘要（4 家，2026-10-03 07:4x）

背景：用户已自行/另一进程建好这 4 张应收单（业务日期 2026-09-30、状态 C 已审核、
      无下游专票）。要求只完成「应收单之后」的流程，**不要重推应收单**。

入口：`push_chain.py --ar-no <AR>`（跳过出库单筛选/建单/折扣分摊）。
      ★ 折扣已在应收单里分摊好，故不传 --discount / --target-*。

防推重：只读预检按 `IV_SALESIC.FSrcBillNo = <AR>` 反查，已有下游专票即停。

★ 每家开跑前先清场（保留「销售出库单列表」）—— 后台残留的「XX - 新增」草稿与前台当前单
  字段 id 相同，是 SF-55「写到别人身上」的根因，必须先摘干净。

断点续跑：output/ar4_20261003c_result.json（成功家跳过）
失败策略：连续 2 家失败即停。

用法：
  python src/run_ar4_20261003c.py --precheck     # 只读预检
  python src/run_ar4_20261003c.py --only AR105-202609-0105
  python src/run_ar4_20261003c.py
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
RESULT = ROOT / "output" / "ar4_20261003c_result.json"
sys.path.insert(0, str(SRC))

DATE = "2026-09-30"                     # 本批统一：业务日期 = 发票日期
ITEMS = [
    {"ar": "AR105-202609-0105", "customer": "浙江三阳包装产业股份有限公司"},
    {"ar": "AR105-202609-0106", "customer": "上海通天包装材料有限公司"},
    {"ar": "AR105-202609-0107", "customer": "安徽广源包装有限公司"},
    {"ar": "AR105-202609-0108", "customer": "扬州祥恒包装有限公司"},
]


def precheck(items):
    """只读预检：应收单在否 / 客户对不对 / 有无下游专票。返回 {ar: (ok, msg)}"""
    from playwright.sync_api import sync_playwright
    from common import load_config
    from kdquery import KdQuery, KdQueryError
    cfg = load_config()
    out = {}
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])
        try:
            kq.rows("AR_receivable", "FBillNo", f"FBillNo = '{items[0]['ar']}'")
        except KdQueryError as e:
            print(f"!! 会话不可用：{str(e)[:150]}")
            br.close()
            return None
        for it in items:
            ar, want_cust = it["ar"], it["customer"]
            hs = kq.ar_head(ar) or []
            if not hs:
                out[ar] = (False, "★ 应收单不存在")
                continue
            h = hs[0]
            cust = str(h.get("FCustomerId.FName") or "")
            iv = kq.rows("IV_SALESIC", "FBillNo,FDocumentStatus",
                         f"FSrcBillNo = '{ar}'", top=10) or []
            if iv:
                out[ar] = (False, f"★ 已有下游专票 {[x.get('FBillNo') for x in iv]} —— 会推重，停止")
                continue
            ok = (cust == want_cust)
            out[ar] = (ok, f"客户={cust} 状态={h.get('FDocumentStatus')} "
                           f"价税={h.get('FAllAmountFor')} 业务日期={str(h.get('FDATE'))[:10]}"
                           + ("" if ok else f"  ★ 与预期客户 {want_cust!r} 不符"))
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


def find_inv(ar_no):
    """按来源应收单反查专票号。"""
    from playwright.sync_api import sync_playwright
    from common import load_config
    from kdquery import KdQuery
    cfg = load_config()
    got = []
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])
        rs = kq.rows("IV_SALESIC", "FBillNo,FDocumentStatus,FAllAmount",
                     f"FSrcBillNo = '{ar_no}'", top=10) or []
        got = [(x.get("FBillNo"), x.get("FDocumentStatus"), x.get("FAllAmount")) for x in rs]
        br.close()
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--precheck", action="store_true", help="只预检，不推")
    ap.add_argument("--only", default="", help="只跑指定应收单号")
    a = ap.parse_args()

    items = [x for x in ITEMS if (not a.only or x["ar"] == a.only)]
    if a.only and not items:
        raise SystemExit(f"--only {a.only!r} 不在清单里")

    print("=" * 108)
    print(f"从已有应收单继续（{len(items)} 家）—— 专票 → 凭证 → 摘要；业务日/发票日 = {DATE}")
    for i, it in enumerate(items, 1):
        print(f"  {i}. {it['ar']}  {it['customer']}")
    print("=" * 108)

    print("\n---- 只读预检 ----")
    pc = precheck(items)
    if pc is None:
        return 2
    blocked = []
    for it in items:
        ok, msg = pc.get(it["ar"], (False, "预检未返回"))
        print(("  ✓ " if ok else "  ✗ ") + f"{it['ar']}  {msg}")
        if not ok:
            blocked.append(it["ar"])
    if blocked:
        print(f"\n!! {len(blocked)} 家预检未通过：{blocked}")
        print("   预检不过一律**不推**（宁可不做，也不能推重/做错单）。")
        if not a.only:
            items = [x for x in items if x["ar"] not in blocked]
    if a.precheck:
        print("\n--precheck：到此为止，没有写任何东西。")
        return 0
    if not items:
        print("没有可跑的家，退出。")
        return 1

    prev = []
    if RESULT.exists():
        try:
            prev = json.loads(RESULT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    done = {r["ar"] for r in prev if r.get("ok")}
    todo = [it for it in items if it["ar"] not in done]
    print(f"\n已完成/跳过 {len(items) - len(todo)} 家，**待跑 {len(todo)} 家**")

    results = list(prev)
    fails = 0
    t0 = time.time()
    for i, it in enumerate(todo, 1):
        ar, c = it["ar"], it["customer"]
        print(f"\n{'=' * 108}\n[{i}/{len(todo)}] {c}   {ar}\n{'=' * 108}", flush=True)
        ts = time.time()
        print(f"  [⓪ 清场] {pre_clean()}", flush=True)

        cmd = [PY, str(SRC / "push_chain.py"),
               "--customer", c, "--ar-no", ar,
               "--biz-date", DATE, "--invoice-date", DATE,
               "--no-invoice-merge", "--step", "voucher", "--yes"]
        rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
        rec = {"ar": ar, "customer": c, "ok": False, "rc": rc}

        if rc == 0:
            rec["chain_ok"] = True
            try:
                ivs = find_inv(ar)
                rec["invoices"] = ivs
                if ivs:
                    wd = subprocess.run([PY, str(SRC / "write_digest.py"),
                                         "--ar-no", ar, "--company", c, "--go"],
                                        cwd=str(ROOT)).returncode
                    rec["digest_ok"] = (wd == 0)
                    if wd != 0:
                        print(f"!! {c} 摘要写失败（rc={wd}）", flush=True)
                    else:
                        rec["ok"] = True
                else:
                    print(f"!! {c} 反查不到专票号", flush=True)
                    rec["err"] = "反查不到专票号"
            except Exception as e:  # noqa: BLE001
                rec["err"] = f"{type(e).__name__}: {str(e)[:200]}"
                print(f"!! {c} 写摘要异常：{rec['err'][:150]}", flush=True)
        else:
            print(f"!! {c} push_chain 失败（rc={rc}）—— 半成品不自动重试，"
                  f"请先只读核实已生成到哪一步，再人工续跑", flush=True)

        rec["sec"] = round(time.time() - ts, 1)
        results = [r for r in results if r.get("ar") != ar]
        results.append(rec)
        RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-- {c} 用时 {rec['sec']}s  ok={rec['ok']}  专票={rec.get('invoices') or '—'}", flush=True)
        if rec["ok"]:
            fails = 0
        else:
            fails += 1
            if fails >= 2:
                print("!! 连续 2 家失败，停止（避免系统性故障下越跑越乱）", flush=True)
                break

    okn = sum(1 for r in results if r.get("ok"))
    print(f"\n{'=' * 108}\n===== 结束：成功 {okn} / {len(items)} 家，"
          f"用时 {(time.time() - t0) / 60:.1f} 分钟 =====\n{'=' * 108}", flush=True)
    return 0 if okn == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
