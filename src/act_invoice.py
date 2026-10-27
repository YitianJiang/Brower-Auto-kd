# -*- coding: utf-8 -*-
"""
单张增值税专用发票的收尾动作
----------------------------
场景：界面上已经挂着一张（已下推生成、待保存/提交的）增值税专用发票，
      补填「复核人」→ 保存 → 提交。

用法：
    python src/act_invoice.py --combo              # 只填复核人（姚玉平）并校验
    python src/act_invoice.py --combo --save       # 填完再保存
    python src/act_invoice.py --combo --save --submit   # 全流程（Submit 受护栏控制）
    python src/act_invoice.py --peek               # 只读：打印当前单据关键字段

安全：--submit 时若 config.yaml 的 safety.auto_submit 为 false，
      加 --yes 才临时放行（不改 config 文件，只在本次进程内生效）。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, Guard, GuardError, shot, abs_path  # noqa: E402
from steps import KingdeeUI, eval_all_frames  # noqa: E402
from kdnet import KdNet, verdict  # noqa: E402

CFG = load_config()

# 本流程要关注的字段
FIELDS = ["FBILLNO", "FDATE", "FINVOICEDATE", "FINVOICENO", "FDRAWER"]
COMBOS = {"复核人": "F_PAEZ_COMBO"}
DEFAULT_CHECKER = "姚玉平"


def connect(p, log):
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    pages = br.contexts[0].pages
    if not pages:
        raise RuntimeError("没有可连接的标签页")
    pg = pages[0]
    log.info(f"接管页面：{pg.title()}")
    return br, pg


def peek(ui, log, page):
    """只读：打印当前单据关键字段 + 下拉现值 + 明细合计"""
    vals = ui.read_bill_fields(FIELDS) or {}
    for f in FIELDS:
        log.info(f"  {f:14s} = {vals.get(f)}")
    for name, fld in COMBOS.items():
        log.info(f"  {name:14s} = {ui.read_combo(fld)}")
    try:
        tot = ui.invoice_amount_total()
        log.info(f"  明细价税合计 = {tot}")
    except Exception as e:
        log.warn(f"  读取明细合计失败：{type(e).__name__}: {e}")
    return vals


def dialog_text(page):
    """读当前页面上所有可见对话框（k-window / messagebox）的文本"""
    js = """() => {
        const out = [];
        const cs = Array.from(document.querySelectorAll('.k-window, .kd-messagebox, .k-dialog, .modal'));
        for (const c of cs) {
            const r = c.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            if (getComputedStyle(c).visibility === 'hidden') continue;
            const btns = Array.from(c.querySelectorAll('a,button')).map(b => (b.textContent||'').trim())
                              .filter(Boolean);
            out.push({ text: (c.innerText||'').trim().slice(0, 400), buttons: btns });
        }
        // 右上角 toast 提示
        const toasts = Array.from(document.querySelectorAll('.k-notification, .k-toast, .messagebox'))
            .map(e => (e.innerText||'').trim()).filter(Boolean);
        return { dialogs: out, toasts };
    }"""
    return eval_all_frames(page, js) or {"dialogs": [], "toasts": []}


def main():
    ap = argparse.ArgumentParser(description="发票复核人回填 + 保存 + 提交")
    ap.add_argument("--combo", action="store_true", help="填复核人")
    ap.add_argument("--checker", default=DEFAULT_CHECKER, help=f"复核人姓名（默认 {DEFAULT_CHECKER}）")
    ap.add_argument("--save", action="store_true", help="保存")
    ap.add_argument("--submit", action="store_true", help="提交（受护栏）")
    ap.add_argument("--yes", action="store_true", help="临时放行护栏（不改 config）")
    ap.add_argument("--peek", action="store_true", help="只读取字段")
    a = ap.parse_args()

    log = Logger(abs_path("logs", CFG), name="act_invoice")
    shot_dir = abs_path("screenshots", CFG)
    guard = Guard(CFG, log)
    if a.yes:
        guard.s["auto_submit"] = True
        guard.s["auto_audit"] = True
        log.warn("已临时放行不可逆动作（本次进程）")

    if not (a.peek or a.combo or a.save or a.submit):
        ap.error("至少要给一个动作：--peek / --combo / --save / --submit")

    try:
        with sync_playwright() as p:
            br, pg = connect(p, log)
            ui = KingdeeUI(pg, CFG, log, guard)
            kd = KdNet(pg, log)          # 服务端反馈监听（判定成败的唯一可靠依据）
            rc = 0

            if a.peek:
                peek(ui, log, pg)
                shot(pg, shot_dir, "peek", log)

            if a.combo:
                log.info(f"回填复核人 = {a.checker}")
                cur = ui.read_combo(COMBOS["复核人"]) or {}
                log.info(f"  回填前：{cur}")
                ui.fill_combo(COMBOS["复核人"], a.checker)
                shot(pg, shot_dir, "combo_filled", log)

            if a.save:
                kd.clear()
                ui.save_bill()
                kd.wait_idle(3000)
                kd.dump(log)
                v, msg = verdict(kd)
                shot(pg, shot_dir, "saved", log)
                if v == "bad":
                    log.err(f"保存失败：{msg}")
                    rc = 1
                elif v == "ok":
                    log.ok(f"保存已确认：{msg}")
                else:
                    log.warn("保存结果未知（服务端无明确消息），请人工看一眼界面")
                    rc = 3
                peek(ui, log, pg)

            if a.submit:
                kd.clear()
                ui.submit_bill()
                kd.wait_idle(4000)
                kd.dump(log)
                v, msg = verdict(kd)
                shot(pg, shot_dir, "submitted", log)
                if v == "bad":
                    log.err(f"提交失败：{msg}")
                    rc = 1
                elif v == "ok":
                    log.ok(f"提交已确认：{msg}")
                else:
                    log.warn("提交结果未知（服务端无明确消息），请人工看一眼界面")
                    rc = 3
                peek(ui, log, pg)

            br.close()
        log.ok("完成")
        return rc
    except GuardError as e:
        log.err(str(e))
        return 2
    except Exception as e:
        log.err(f"失败：{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
