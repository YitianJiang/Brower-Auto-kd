# -*- coding: utf-8 -*-
"""
2026-10-31 批次批量下推（43 家纯出库单客户）。
数据源：C:\\Users\\Administrator\\Desktop\\10月\\下推.xlsx

口径（2026-10-27 用户定）：
  * 业务日期/到期日 = 2026-10-31（第0列序列号 46326）
  * 出库单日期区间每家不同（第1/2列序列号）
  * 折扣列（第4列）负数=少收，取 abs 为正
  * ★ 排除 109 组织的单（单号含 -109-）
  * ★ 排除有退货的 2 家（桐乡华淳、江西广源，退货单独下推红字应收）
  * ★ 排除先不推的 3 家（浙江祥恒、湖北合冠、山东英彩）

只推：区间内未下推的 105 组织出库单，金额对拍 = 开票价税 + 折扣 的家。
断点续跑：output/batch_20261031_result.json（ok=true 自动跳过）。
失败策略：连续 2 家失败即停。

用法：
    python src/run_batch_20261031.py --precheck   # 只预检
    python src/run_batch_20261031.py --probe      # 只探测
    python src/run_batch_20261031.py              # 正式推
    python src/run_batch_20261031.py --only 客户名
    python src/run_batch_20261031.py --limit N
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["KD_NO_SHOT"] = "1"

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PY = sys.executable
RESULT = ROOT / "output" / "batch_20261031_result.json"
XL = r"C:\Users\Administrator\Desktop\10月\下推.xlsx"
TOL = 0.05

# ★ 先不推 / 有退货单独处理的客户（用户 2026-10-27 口径）
SKIP = {
    "浙江祥恒包装有限公司": "先不推（金额对不上，缺大量单）",
    "湖北合冠包装有限公司": "先不推（缺 24990）",
    "山东英彩印刷科技有限公司": "先不推（差 +346.27）",
    "桐乡市华淳贸易有限公司": "有退货，单独下推红字应收",
    "江西广源包装有限公司": "有退货，单独下推红字应收",
}
sys.path.insert(0, str(SRC))

# ★ 单一真相源：主标签栏选择器 / widget 键名（SF-51，见 kd_tab_sel.py）
from kd_tab_sel import MAIN_TAB_SELECTOR, WIDGET_KEY  # noqa: E402


def sn2date(s):
    if s in (None, ""):
        return None
    return (datetime(1899, 12, 30) + timedelta(days=int(s))).strftime("%Y-%m-%d")


def load_items(xl=XL):
    import openpyxl
    wb = openpyxl.load_workbook(xl, data_only=True)
    ws = wb["Sheet1"]
    items = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[3]:
            continue
        items.append({
            "customer": str(r[3]).strip(),
            "date": sn2date(r[0]),
            "date_from": sn2date(r[1]),
            "date_to": sn2date(r[2]),
            "discount": abs(float(r[4] or 0)),
            "notax": float(r[5] or 0),
            "tax": float(r[6] or 0),
            "all": float(r[7] or 0),
        })
    return items


def probe(items):
    from playwright.sync_api import sync_playwright
    from common import load_config
    from kdquery import KdQuery, KdQueryError, num
    cfg = load_config()
    out = {}
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        kq = KdQuery(br.contexts[0].pages[0])
        try:
            kq.rows("AR_receivable", "FBillNo", "FBillNo = 'AR105-202609-0105'")
        except KdQueryError as e:
            print(f"!! 会话不可用：{str(e)[:150]}")
            br.close()
            return None
        for it in items:
            c = it["customer"]
            f = (f"FCustomerID.FName = '{c}' and FDocumentStatus = 'C' "
                 f"and FDate >= '{it['date_from']}' and FDate <= '{it['date_to']}'")
            rows = kq.rows("SAL_OUTSTOCK",
                           "FBillNo,FDate,FDocumentStatus,FCustomerID.FName,FBILLALLAMOUNT",
                           f, top=200)
            un = []
            for r in sorted(rows, key=lambda x: str(x.get("FDate") or "")):
                b = str(r.get("FBillNo") or "")
                if "-109-" in b:
                    continue
                amt = num(r.get("FBILLALLAMOUNT"))
                try:
                    ars = kq.ar_by_source_bill(b)
                except KdQueryError:
                    ars = []
                if not ars:
                    un.append({"bill": b, "date": str(r.get("FDate"))[:10], "amt": amt})
            out[c] = {"un": un, "un_amt": round(sum(x["amt"] for x in un), 2)}
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


# ★ 2026-10-27 标签数护栏（SF-51：金蝶页签硬上限 15 个）。
#   ★★★ 2026-10-27 三次根因纠正（实测定案）：金蝶页签配额的真实计数不是「可见标签数」
#   （ui.tabs() 数 .k-item.contentHeadEL，会漏幽灵 li 和后台保留表单），而是主标签栏控件
#   `kendomainTabControl.items().length`。探针实测：ui.tabs()=2 时 items()=4。
#   ⇒ 护栏必须读 items()，否则"表面标签清了、后台配额仍满"→ 攒到 15 报「超上限」。
#   ★ 释放配额的**唯一**手段 = `ctl.remove($(li))`（见 deep_clean）。
#     **点 logo 回主页不能清零**（4→4 实测不变，只是切激活页，tab 对象仍在），勿再依赖。
TAB_MAX = 12  # 安全线：金蝶上限 15，留 3 个余量；items() 达到 12 就 deep_clean 释放

QUOTA_JS = """() => {
    const $ = window.jQuery;
    const strip = document.querySelector('__MAIN_TAB__');
    if (!strip) return -1;
    const ctl = $(strip).closest('.k-widget').data('__WIDGET_KEY__');
    if (!ctl) return -2;
    try { return ctl.items().length; } catch(e) { return -3; }
}""".replace("__MAIN_TAB__", MAIN_TAB_SELECTOR).replace("__WIDGET_KEY__", WIDGET_KEY)

HOME_JS = """() => {
    const loc = document.querySelector('div.kd_top_k3cloudlogo_img');
    if (!loc) return false;
    const r = loc.getBoundingClientRect();
    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
}"""


def _quota():
    """读金蝶真实页签配额 items().length。断连/异常返回 -1。"""
    from playwright.sync_api import sync_playwright
    from common import load_config
    cfg = load_config()
    try:
        with sync_playwright() as p:
            br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
            pg = br.contexts[0].pages[0]
            n = pg.evaluate(QUOTA_JS)
            br.close()
        return n
    except Exception as e:  # noqa: BLE001
        print(f"  [护栏] 读配额失败（{type(e).__name__}），返回 -1", flush=True)
        return -1


def _go_home():
    """点 logo 回主页（SPA 内部导航，非 reload，不登出）。返回回主页后配额数。"""
    from playwright.sync_api import sync_playwright
    from common import load_config
    cfg = load_config()
    try:
        with sync_playwright() as p:
            br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
            pg = br.contexts[0].pages[0]
            before = pg.evaluate(QUOTA_JS)
            pos = pg.evaluate(HOME_JS)
            if not pos:
                print(f"  [护栏] 找不到 logo，回主页失败（配额 {before}）", flush=True)
                return before
            pg.mouse.click(pos["x"], pos["y"])
            pg.wait_for_timeout(2500)
            after = pg.evaluate(QUOTA_JS)
            br.close()
        print(f"  [护栏] 回主页：配额 {before} → {after}", flush=True)
        return after
    except Exception as e:  # noqa: BLE001
        print(f"  [护栏] 回主页异常（{type(e).__name__}），返回 -1", flush=True)
        return -1


def deep_clean(rounds=3):
    """深度清场（2026-10-27 四次定案版）：按**真实配额 items()** 判断 + 用 ctl.remove() 真正释放。

    实测定案（probe_remove_effect.py / probe_quota_reset.py）：
    - 配额 = `kendomainTabControl.items().length`（ul.kdMainTabsHeaderExtend 的 widget）
    - `ctl.remove($(li))` 能真正让 items() 减 1（4→3），是**唯一**释放配额的手段
    - 点 logo 回主页**不能**清零（4→4 不变，只是切激活页，tab 对象仍在）
    - 有 2 个零宽幽灵 li 常驻（永远占 2 配额），真实可用 = 13
    策略：超线时直接 ctl.remove() 摘掉除「销售出库单列表」锚点外的所有 tab（含孤儿/幽灵）。
    返回 (最终配额, 是否达标)。"""
    from playwright.sync_api import sync_playwright
    from common import load_config
    from steps import KingdeeUI
    cfg = load_config()
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, cfg, None, None)

        # 直接对主标签栏 ctl 摘除所有非锚点标签（含幽灵 li）
        REMOVE_ALL_BUT_ANCHOR = """() => {
            const $ = window.jQuery;
            const strip = document.querySelector('__MAIN_TAB__');
            if (!strip) return -1;
            const ctl = $(strip).closest('.k-widget').data('__WIDGET_KEY__');
            if (!ctl || typeof ctl.remove !== 'function') return -2;
            const arr = ctl.items();
            let removed = 0;
            // 从后往前摘，避免下标错位
            for (let i = arr.length - 1; i >= 0; i--) {
                const li = arr[i];
                const txt = (li.innerText || '').trim();
                if (txt.includes('销售出库单列表')) continue;  // 保留锚点
                try { ctl.remove($(li)); removed++; } catch(e) {}
            }
            return removed;
        }""".replace("__MAIN_TAB__", MAIN_TAB_SELECTOR).replace("__WIDGET_KEY__", WIDGET_KEY)

        n = pg.evaluate(QUOTA_JS)
        for rnd in range(rounds):
            if n <= TAB_MAX:
                break
            try:
                removed = pg.evaluate(REMOVE_ALL_BUT_ANCHOR)
                pg.wait_for_timeout(800)
                n = pg.evaluate(QUOTA_JS)
                print(f"  [护栏] ctl.remove 摘除 {removed} 个 → 配额 {n}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"  [护栏] ctl.remove 异常（{type(e).__name__}），退回 close_all_tabs", flush=True)
                ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=True)
                pg.wait_for_timeout(800)
                n = pg.evaluate(QUOTA_JS)

        br.close()
    ok = n <= TAB_MAX
    if not ok:
        print(f"  [护栏] ⚠️ 深度清场 {rounds} 轮后配额仍 {n}（>{TAB_MAX}）", flush=True)
    return n, ok


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


def selftest_force_remove():
    """开跑前自检（SF-51 护栏）：验证主标签栏选择器 / widget 可达性。

    返回 True = 通过；False = 选择器可能已失效，务必先修再跑正式批。
    """
    from playwright.sync_api import sync_playwright
    from common import load_config
    from selftest_force_remove import run_selftest
    cfg = load_config()
    try:
        with sync_playwright() as p:
            br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
            pg = br.contexts[0].pages[0]
            ok, detail = run_selftest(pg)
            br.close()
        print(detail, flush=True)
        return ok
    except Exception as e:  # noqa: BLE001
        print(f"  [护栏自检] 异常（{type(e).__name__}: {str(e)[:120]}），视为未通过", flush=True)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--precheck", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--xl", default="", help="开票数据 xlsx 路径（默认读桌面写死的下推.xlsx；从对话框上传的文件用这个指向）")
    a = ap.parse_args()

    xl_path = a.xl or XL
    items = [it for it in load_items(xl_path) if it["customer"] not in SKIP]
    if a.only:
        items = [x for x in items if x["customer"] == a.only]
        if not items:
            raise SystemExit(f"--only {a.only!r} 不在可推清单里")
    if a.limit:
        items = items[:a.limit]

    print("=" * 110)
    print(f"2026-10-31 批次（{len(items)} 家，业务日期 = {items[0]['date'] if items else '-'}）")
    print(f"跳过：{', '.join(SKIP)}")
    print("=" * 110)

    pr = probe(items)
    if pr is None:
        return 2
    todo, blocked = [], []
    for it in items:
        c = it["customer"]
        p = pr.get(c) or {}
        disc = it["discount"]
        want = round(it["all"] + disc, 2)
        got = p.get("un_amt", 0)
        near = abs(got - want) <= TOL and p.get("un")
        if near:
            todo.append(it)
            print(f"  ✓ {c:24s} 出库单 {got:,.2f} = 开票 {it['all']:,.2f}"
                  + (f" + 折扣 {disc:,.2f}" if disc else "") + f"  [{len(p['un'])} 张]")
        else:
            blocked.append(it)
            print(f"  ✗ {c:24s} 出库单 {got:,.2f} vs 期望 {want:,.2f}（差 {got-want:+,.2f}）")
    print(f"\n→ 可推 {len(todo)} 家；挂起 {len(blocked)} 家")
    if blocked:
        print("   挂起：" + "、".join(x["customer"] for x in blocked))

    if a.probe:
        print("\n--probe：到此为止。")
        return 0
    if a.precheck:
        print("\n[护栏自检] 验证主标签栏选择器 / widget 可达性（SF-51）…")
        if not selftest_force_remove():
            print("⚠️ 自检未通过：主标签栏选择器可能已失效，务必先修再跑正式批。")
            return 3
        print("\n--precheck：到此为止。")
        return 0
    if not todo:
        print("没有可推的家，退出。")
        return 1

    prev = []
    if RESULT.exists():
        try:
            prev = json.loads(RESULT.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = []
    done = {r["customer"] for r in prev if r.get("ok")}
    pend = [it for it in todo if it["customer"] not in done]
    print(f"\n已完成/跳过 {len(todo) - len(pend)} 家，待推 {len(pend)} 家")

    results = list(prev)
    fails = 0
    t0 = time.time()
    for i, it in enumerate(pend, 1):
        c = it["customer"]
        bills = [x["bill"] for x in pr[c]["un"]]
        print(f"\n{'=' * 110}\n[{i}/{len(pend)}] {c}   业务日期={it['date']}\n"
              f"     出库单 {','.join(bills)}\n"
              f"     开票价税 {it['all']:,.2f}（不含税 {it['notax']:,.2f} / 税额 {it['tax']:,.2f}）"
              + (f"   折扣 {it['discount']:,.2f}\n" if it["discount"] else "\n")
              + f"{'=' * 110}", flush=True)
        ts = time.time()
        print(f"  [⓪ 清场] {pre_clean()}", flush=True)
        # ★ 标签配额护栏：pre_clean 后按真实配额 items() 判读，超线回主页重置
        _tn, _tok = deep_clean()
        print(f"  [⓪ 护栏] 清场后配额 {_tn}（{'达标' if _tok else '仍超' + str(TAB_MAX)}）", flush=True)

        cmd = [PY, str(SRC / "push_chain.py"),
               "--customer", c, "--bill-nos", ",".join(bills),
               "--target-notax", f"{it['notax']}", "--target-tax", f"{it['tax']}",
               "--target-all", f"{it['all']}",
               "--biz-date", it["date"], "--invoice-date", it["date"],
               "--no-invoice-merge", "--step", "voucher", "--yes"]
        if it["discount"]:
            cmd += ["--discount", f"{it['discount']}"]
        rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
        rec = {"customer": c, "bills": bills, "all": it["all"], "date": it["date"],
               "discount": it["discount"], "ok": False, "rc": rc}
        if rc == 0:
            rec["chain_ok"] = True
            try:
                ars = find_ar(bills)
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
                        # ★ 收尾清场：write_digest 会留下「业务凭证列表」「凭证 - 修改」
                        #   等孤儿标签，若不及时回收会攒到 15 上限（SF-51）。这里每成功一家
                        #   就按真实配额 items() 清一次，超线回主页重置。
                        _tn, _tok = deep_clean()
                        print(f"  [收尾清场] 摘要写完后配额 {_tn}（{'达标' if _tok else '仍超' + str(TAB_MAX)}）", flush=True)
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
                print("!! 连续 2 家失败，停止（避免系统性故障下越跑越乱）", flush=True)
                break

    this_customers = {it["customer"] for it in pend}
    okn = sum(1 for r in results if r.get("ok") and r.get("customer") in this_customers)
    tot = sum(float(r["all"]) for r in results
              if r.get("ok") and r.get("customer") in this_customers)
    print(f"\n{'=' * 110}\n===== 2026-10-31 批次结束：成功 {okn} / {len(pend)} 家"
          f"（成功部分开票价税合计 {tot:,.2f}），用时 {(time.time() - t0) / 60:.1f} 分钟 =====\n"
          f"{'=' * 110}", flush=True)
    return 0 if okn == len(pend) else 1


if __name__ == "__main__":
    sys.exit(main())
