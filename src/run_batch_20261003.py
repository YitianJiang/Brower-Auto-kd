# -*- coding: utf-8 -*-
"""
批量下推 2026-10-03 批次（13 家）。
每家三步：
  ⓪ clean_tabs 清场 —— 保证每家的起点都是"只剩出库单列表"的干净状态
     （防孤儿标签累积挤爆 tabstrip → activate_tab 点错页 → 「找不到可见的列表网格」）
  ① push_chain.py 全链 → 出库单→应收单→专票→凭证生成（业务日=发票日=2026-09-30）
  ② 按「来源出库单号」反查应收单号 → write_digest.py 写凭证摘要

断点续跑：成功的家记录在 output/batch_20261003_result.json，重跑自动跳过。
         （失败的家**不自动重试** —— 半成品可能已有应收单，重跑会推重。必须人工确认起点。）
失败策略：连续 2 家失败即停（避免系统性故障下越跑越乱）。

用法：python src/run_batch_20261003.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ★ 2026-10-03：批量运行**关掉截图**。
#   实测 CDP `Page.captureScreenshot` 在「凭证 - 修改」页面上单次阻塞 **9 分钟**
#   （窗口最小化/被遮挡 → 渲染器不产帧 → 截图永远等不到；CDP send 没有 timeout 参数）。
#   一家 3 张 × 8 家 = 最坏几小时纯等待，且截图只是存档，对正确性零贡献。
#   需要取证时把这里注释掉即可。
os.environ["KD_NO_SHOT"] = "1"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PY = sys.executable
RESULT = ROOT / "output" / "batch_20261003_result.json"
sys.path.insert(0, str(SRC))


def pre_clean():
    """⓪ 每家开跑前清场：只留「销售出库单列表」。返回一行摘要。"""
    try:
        r = subprocess.run([PY, str(SRC / "clean_tabs.py"),
                            "--go", "--keep-name", "销售出库单列表"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=240)
        lines = [x for x in (r.stdout or "").strip().splitlines() if x.strip()]
        return " | ".join(lines[-2:]) if lines else f"（无输出 rc={r.returncode}）"
    except Exception as e:  # noqa: BLE001
        return f"pre_clean 异常（继续）：{type(e).__name__}: {e}"


def tab_count():
    """只读：CDP 看浏览器真实页面数（粗粒度健康指标）。"""
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=4) as f:
            d = json.loads(f.read().decode("utf-8"))
        return len([t for t in d if t.get("type") == "page"])
    except Exception:  # noqa: BLE001
        return -1


def find_ar(bill_nos):
    """只读：按来源出库单号反查应收单号。"""
    from playwright.sync_api import sync_playwright
    from common import load_config
    from kdquery import KdQuery
    cfg = load_config()
    got = []
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        kq = KdQuery(pg)
        for b in bill_nos:
            got += [x for x in kq.ar_by_source_bill(b) if x]
        br.close()
    return sorted(set(got))


def main():
    prev = []
    if RESULT.exists():
        try:
            prev = json.loads(RESULT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    done = {r["customer"] for r in prev if r.get("ok")}
    # 下推成功但摘要没成功的家：续跑时**只补摘要，绝不重推**
    chain_done = {r["customer"]: r for r in prev if r.get("chain_ok") and not r.get("ok")}

    items = json.loads((ROOT / "output" / "batch_20261003.json").read_text(encoding="utf-8"))
    todo = [it for it in items if it["customer"] not in done and it["bills"]]
    # ★ 把「只补摘要」的家排到最后：它们失败的原因和其它家不同，
    #   混在中间会让"连续 2 家失败即停"误伤后面真正要推的家。
    todo.sort(key=lambda it: 1 if it["customer"] in chain_done else 0)
    print(f"本批共 {len(items)} 家；已完成/跳过 {len(items)-len(todo)} 家；**待处理 {len(todo)} 家**"
          f"（其中只补摘要 {sum(1 for it in todo if it['customer'] in chain_done)} 家，排在最后）",
          flush=True)

    results = list(prev)
    fails = 0
    t0 = time.time()
    for i, it in enumerate(todo, 1):
        c = it["customer"]
        print(f"\n{'=' * 88}\n[{i}/{len(todo)}] {c}\n     单 {','.join(it['bills'])}\n"
              f"     折扣 {it['discount']}  目标价税 {it['all']}\n{'=' * 88}", flush=True)

        ts = time.time()
        prev_rec = chain_done.get(c)
        if prev_rec:
            # ★★ 2026-10-03：上次 **push_chain 已成功、只是摘要没写上** →
            #   **绝对不能再推一遍**（应收单/专票/凭证都已生成，重推会全部重复，
            #   而凭证是不可删除的）。这里只补摘要。
            print(f"  [续跑] 该家上次下推已成功（chain_ok）→ 跳过下推，只补摘要", flush=True)
            rec = dict(prev_rec)
            rc = 0
        else:
            pc = pre_clean()
            print(f"  [⓪ 清场] {pc}", flush=True)

            cmd = [PY, str(SRC / "push_chain.py"),
                   "--customer", c, "--bill-nos", ",".join(it["bills"]),
                   "--discount", str(abs(float(it["discount"]))),
                   "--target-notax", it["notax"], "--target-tax", it["tax"],
                   "--biz-date", "2026-09-30", "--invoice-date", "2026-09-30",
                   "--no-invoice-merge", "--step", "voucher", "--yes"]
            rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
            rec = {"customer": c, "bills": it["bills"], "all": it["all"], "ok": rc == 0, "rc": rc}
            if rc == 0:
                rec["chain_ok"] = True      # 下推这一段已成功，续跑时只补摘要
        if rc == 0:
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
                        rec["ok"] = False
                        print(f"!! {c} 摘要写失败（rc={wd}）", flush=True)
                else:
                    rec["ok"] = False
                    print(f"!! {c} 反查不到应收单号，摘要未写", flush=True)
            except Exception as e:  # noqa: BLE001
                rec["ok"] = False
                rec["err"] = str(e)[:200]
                print(f"!! {c} 写摘要异常：{str(e)[:150]}", flush=True)
        else:
            print(f"!! {c} push_chain 失败（rc={rc}）—— 半成品，**不会自动重试**，"
                  f"请先只读核实已生成到哪一步（应收单/专票），再人工续跑", flush=True)
        rec["sec"] = round(time.time() - ts, 1)
        results = [r for r in results if r.get("customer") != c]   # 同客户覆盖，不留旧条目
        results.append(rec)
        RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-- {c} 用时 {rec['sec']}s  ok={rec['ok']}  浏览器页面数={tab_count()}", flush=True)
        if rec["ok"]:
            fails = 0
        else:
            fails += 1
            if fails >= 2:
                print("!! 连续 2 家失败，停止（避免系统性故障下越跑越乱）", flush=True)
                break

    ok_n = sum(1 for r in results if r.get("ok"))
    tot_all = sum(float(r["all"]) for r in results if r.get("ok"))
    print(f"\n{'=' * 88}\n===== 批量结束：成功 {ok_n} 家 / 本批 {len(items)} 家"
          f"（成功部分价税合计 {tot_all:,.2f}），本段用时 {(time.time()-t0)/60:.1f} 分钟 =====\n"
          f"{'=' * 88}", flush=True)


if __name__ == "__main__":
    main()
