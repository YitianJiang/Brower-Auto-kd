# -*- coding: utf-8 -*-
"""2026-11-02 重推批次：对已有应收单重跑「下推专票 → 提交(审核) → 生成凭证 → 写摘要」。

背景
----
10 月杭州账套 14 张应收单没有正确的专票/凭证：
  · 8 张（0003/0014/0015/0019/0022/0031/0033/0034）：原专票漏行（少开 69,836.75），
    用户已手工删除专票 + 凭证，需重推；
  · 6 张（0066/0076/0077/0078/0079/0080）：当时就没推过票/凭证，需补推。

不推的 4 张（本脚本不含）：
  · AR105-202610-0046 / 0047：红字应收单（-4,851.50 / -76.00），下推红字专票需另行确认口径
  · AR105-202610-0073：费用应收单（已有凭证 209728，本就不开销售专票）
  · AR105-202610-0082：费用应收单且状态 D（已作废）

分两批（用户口径：一批 10 张，批末弹窗提醒重登浏览器，绕开 15 页签上限）
----
  第 1 批 10 张：8 张重推 + 0066 + 0076
  第 2 批  4 张：0077 / 0078 / 0079 / 0080

用法
----
    python src/verify_repush_20261102.py          # 先只读核实（必须全 ✅ 再推）
    python src/repush_batch_20261102.py --batch 1
    python src/repush_batch_20261102.py --batch 2
    python src/repush_batch_20261102.py --batch 1 --dry-run   # 只打印将执行什么

每张成功 = push_chain(--ar-no, --step voucher) rc=0 且 write_digest rc=0。
失败不自动重试（半成品重跑会推重），记录后继续，连续 2 张失败即停。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

PY = sys.executable
TAB_MAX = 10                     # 主动闸门：开页前配额 >= 10 先释放
RESULT = ROOT / "output" / "repush_20261102_result.json"

ITEMS = [
    # (应收单, 客户, 专票业务日期/发票日期, 价税合计, 标记)
    ("AR105-202610-0003", "宁波日和新材料有限公司", "2026-10-31", "60926.48", "重推-漏行"),
    ("AR105-202610-0014", "常熟市双乐彩印包装有限公司", "2026-10-31", "26620.00", "重推-漏行"),
    ("AR105-202610-0015", "杭州嘉顺印务有限公司", "2026-10-31", "134300.00", "重推-漏行"),
    ("AR105-202610-0019", "杭州墨科包装材料有限公司", "2026-10-31", "15457.00", "重推-漏行"),
    ("AR105-202610-0022", "南通楚宏纸业有限公司", "2026-10-31", "10360.00", "重推-漏行"),
    ("AR105-202610-0031", "杭州奥科包装材料有限公司", "2026-10-31", "9000.00", "重推-漏行"),
    ("AR105-202610-0033", "南京盛溪印刷包装有限公司", "2026-10-31", "53034.00", "重推-漏行"),
    ("AR105-202610-0034", "浙江楼鑫包装科技有限公司", "2026-10-31", "1800.00", "重推-漏行"),
    ("AR105-202610-0066", "淮安秉信环保包装有限公司", "2026-10-31", "339903.20", "补推-原本无票"),
    ("AR105-202610-0076", "马鞍山品尚智包包装服务有限公司", "2026-10-30", "2065.59", "补推-原本无票"),
    ("AR105-202610-0077", "浙江赛欧包装有限公司", "2026-10-31", "115000.00", "补推-原本无票"),
    ("AR105-202610-0078", "浙江赛欧包装有限公司", "2026-10-30", "124000.00", "补推-原本无票"),
    ("AR105-202610-0079", "杭州秉信环保包装有限公司", "2026-10-30", "727708.55", "补推-原本无票"),
    ("AR105-202610-0080", "郑州秉信包装有限公司", "2026-10-30", "319345.79", "补推-原本无票"),
]

BATCHES = {1: ITEMS[:10], 2: ITEMS[10:]}


def notify(title, msg):
    """桌面弹窗（独立进程，不阻塞、脚本退出后仍在）。"""
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          f"[void][System.Windows.Forms.MessageBox]::Show('{msg}','{title}','OK','Information')")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=0x00000008)   # DETACHED_PROCESS
    except Exception as e:  # noqa: BLE001
        print(f"  !! 弹窗失败（{type(e).__name__}）—— 控制台提示仍然有效", flush=True)


def precheck(items):
    """只读核实：本批的每张必须「无票无凭证 + 状态 C + 金额一致」才允许继续。

    只核本批（--ars），否则第 2 批会被第 1 批已生成的票/凭证误拦。"""
    ars = ",".join(x[0] for x in items)
    r = subprocess.run([PY, str(SRC / "verify_repush_20261102.py"), "--ars", ars], cwd=str(ROOT))
    return r.returncode == 0


def clean():
    """清场 + 配额护栏（复用 10-31 批次已定案的实现）。"""
    from run_batch_20261031 import pre_clean, deep_clean
    msg = pre_clean()
    n, ok = deep_clean()
    return f"{msg}；配额 {n}{'' if ok else f'（仍 >{TAB_MAX}）'}"


def run_one(ar, cust, date, amt, tag):
    ts = time.time()
    print(f"\n{'=' * 100}\n[{tag}] {ar}  {cust}   {date}   价税 {amt}\n{'=' * 100}", flush=True)
    print(f"  [⓪ 清场] {clean()}", flush=True)

    cmd = [PY, str(SRC / "push_chain.py"),
           "--customer", cust, "--ar-no", ar,
           "--biz-date", date, "--invoice-date", date,
           "--step", "voucher", "--yes"]
    print("  [① 下推] " + " ".join(cmd[1:]), flush=True)
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    rec = {"ar": ar, "customer": cust, "date": date, "amount": amt, "tag": tag,
           "push_rc": rc, "ok": False}
    if rc != 0:
        rec["err"] = f"push_chain rc={rc}（半成品，不自动重试）"
        print(f"  !! push_chain 失败 rc={rc} —— 先只读核实生成到哪一步再人工续跑", flush=True)
        rec["sec"] = round(time.time() - ts, 1)
        return rec

    wd = subprocess.run([PY, str(SRC / "write_digest.py"),
                         "--ar-no", ar, "--company", cust, "--go"], cwd=str(ROOT)).returncode
    rec["digest_rc"] = wd
    if wd != 0:
        rec["err"] = f"write_digest rc={wd}"
        print(f"  !! 摘要写失败 rc={wd}", flush=True)
    else:
        rec["ok"] = True
    rec["sec"] = round(time.time() - ts, 1)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, choices=[1, 2], required=True)
    ap.add_argument("--dry-run", action="store_true", help="只打印清单，不核实不执行")
    ap.add_argument("--skip-precheck", action="store_true")
    a = ap.parse_args()

    items = BATCHES[a.batch]
    print("=" * 100)
    print(f"2026-11-02 重推 第 {a.batch} 批：{len(items)} 张，"
          f"价税 {sum(float(x[3]) for x in items):,.2f}")
    for ar, c, d, amt, tag in items:
        print(f"  {ar}  {d}  {c:<28} {float(amt):>13,.2f}  {tag}")
    print("=" * 100)
    if a.dry_run:
        print("--dry-run：未执行")
        return 0

    if not a.skip_precheck and not precheck(items):
        print("!! 预检未通过（有单子仍带票/带凭证或状态不对），已停止")
        return 2

    results = json.loads(RESULT.read_text(encoding="utf-8")) if RESULT.exists() else []
    done = {r["ar"] for r in results if r.get("ok")}
    fails = 0
    for ar, cust, date, amt, tag in items:
        if ar in done:
            print(f"-- 跳过 {ar}（上次已成功）")
            continue
        rec = run_one(ar, cust, date, amt, tag)
        results = [r for r in results if r["ar"] != ar] + [rec]
        RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-- {ar} 用时 {rec['sec']}s  ok={rec['ok']}", flush=True)
        fails = 0 if rec["ok"] else fails + 1
        if fails >= 2:
            print("!! 连续 2 张失败，停止（避免系统性故障下越跑越乱）")
            notify("金蝶重推 已中止",
                   f"第 {a.batch} 批连续 2 张失败，已自动停止。\\n请查看控制台日志再决定。")
            return 3

    this = [r for r in results if r["ar"] in {x[0] for x in items}]
    okn = sum(1 for r in this if r.get("ok"))
    print("\n" + "=" * 100)
    print(f"第 {a.batch} 批结束：成功 {okn}/{len(items)}")
    for r in this:
        if not r.get("ok"):
            print(f"  ✗ {r['ar']}  {r.get('err')}")
    print("=" * 100)

    if okn == len(items):
        tip = (f"第 {a.batch} 批 {len(items)} 张已全部推完（专票+凭证+摘要）。\\n\\n"
               + ("请重登浏览器（登出→登录）后再跑第 2 批（4 张）。"
                  if a.batch == 1 else "全部 14 张完成，可做收尾对账。"))
    else:
        tip = (f"第 {a.batch} 批只成功 {okn}/{len(items)}，有失败项。\\n"
               f"请先看控制台/日志，别直接重跑（会推重）。")
    notify(f"金蝶重推 第 {a.batch} 批完成", tip)
    print("★ 已弹桌面提示：" + tip.replace("\\n", " / "))
    return 0 if okn == len(items) else 1


if __name__ == "__main__":
    sys.exit(main())
