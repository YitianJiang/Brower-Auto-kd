# -*- coding: utf-8 -*-
"""
提交「凭证 - 修改」页当前打开的那张凭证（业务/总账凭证）

背景（2026-09-21）：凭证已生成（业务凭证 BizVch1352515 / 总账 转 47），
但**还没点提交**，页面上「审核」按钮是灰的（= 暂存状态）。

用法：
    python src/submit_voucher.py --peek         # 只看当前状态，不点提交
    python src/submit_voucher.py --yes          # 真点提交（不可逆，放行安全护栏）

判定手段：
    1. 金蝶服务端 kdsvc 回包（showMessage「提交成功」/ UpdateMainMenuStates）
    2. 工具栏按钮禁用态翻转：提交(可用→禁用) + 审核(禁用→可用)
    两条都看上才算成功，任一不满足就报 ERR 停住，绝不假报成功。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, Guard, GuardError, shot, abs_path  # noqa: E402
from steps import KingdeeUI, eval_all_frames  # noqa: E402
from kdnet import KdNet, verdict  # noqa: E402

CFG = load_config()

MENU_JS = KingdeeUI._INVISIBLE + """() => {
    const want = ['保存', '提交', '审核', '还原'];
    const o = {};
    Array.from(document.querySelectorAll('.kdmenuitem-content'))
        .filter(e => vis(e) && e.offsetParent !== null).forEach(e => {
            const t = (e.textContent||'').trim();
            if (!want.includes(t)) return;
            const item = e.closest('.kdmenuitem');
            o[t] = !(e.className.includes('disabled')
                     || (item && item.className.includes('disabled')));
        });
    return o;
}"""


def menu_state(pg):
    """{'保存':True,'提交':True,'审核':False,...} True=可用"""
    r = eval_all_frames(pg, MENU_JS)
    return r if isinstance(r, dict) else {}


def main():
    ap = argparse.ArgumentParser(description="提交当前凭证")
    ap.add_argument("--peek", action="store_true", help="只看状态，不点提交")
    ap.add_argument("--yes", action="store_true", help="放行安全护栏（本次进程）")
    a = ap.parse_args()

    log = Logger(abs_path("logs", CFG), name="submit_vch")
    shot_dir = abs_path("screenshots", CFG)
    guard = Guard(CFG, log)

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, guard)
        kd = KdNet(pg, log)

        tab = ui.active_tab()
        log.info(f"当前标签 = {tab!r}")
        if "凭证" not in (tab or ""):
            log.err("当前不在凭证页（应为「凭证 - 修改」），先打开目标凭证再跑")
            br.close()
            return 1

        entries = ui.read_voucher_entries()
        log.info(f"分录 {len(entries)} 行：")
        for i, e in enumerate(entries):
            log.info(f"   行{i} | {e.get('FENTRYSEQ','')} | {e.get('FEXPLANATION','')}"
                     f" | {e.get('FACCOUNTID','')} {e.get('FACCTFULLNAME','')[:18]}"
                     f" | 借{e.get('FDEBIT','')} 贷{e.get('FCREDIT','')}")

        before = menu_state(pg)
        log.info(f"提交前工具栏（True=可用）：{json.dumps(before, ensure_ascii=False)}")
        shot(pg, shot_dir, "sv_1_before", log)

        if before.get("提交") is False:
            log.warn("「提交」按钮已是禁用态 —— 这张凭证很可能已经提交过了，不再重复点")
            br.close()
            return 0

        if a.peek:
            log.warn("--peek：只看状态，不点提交")
            br.close()
            return 0

        if a.yes:
            guard.s["auto_submit"] = True
            log.warn("已临时放行「提交」（auto_submit，本次进程）")
        guard.check("submit", "提交凭证后进入审核流，凭状态翻转与专票一致=提交即审核")

        # ---------- 点提交 ----------
        kd.clear()
        ui.click_menu("提交", timeout_ms=2500)
        kd.wait_idle(9000)
        kd.dump(log)

        # 可能弹二次确认（「是否提交？」）
        dlg = eval_all_frames(pg, KingdeeUI._INVISIBLE + """() => {
            const w = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(x => /确定|确认|提交/.test(x.innerText||''));
            if (!w) return null;
            return { text: (w.innerText||'').trim().replace(/\\s+/g,' ').slice(0, 200),
                     buttons: Array.from(w.querySelectorAll('a,button')).filter(vis)
                                .map(b => (b.innerText||'').trim()).filter(Boolean) };
        }""")
        if dlg:
            log.info(f"出现确认弹窗：{dlg['text'][:160]} 按钮={dlg['buttons']}")
            kd.clear()
            ui.click_confirm(timeout_ms=1500)
            kd.wait_idle(7000)
            kd.dump(log)

        v, msg = verdict(kd)
        log.info(f"服务端判定：{v} / {msg}")

        # 菜单禁用态由服务端 UpdateMainMenuStates 驱动，等它刷完
        after = {}
        for _ in range(10):
            pg.wait_for_timeout(1200)
            after = menu_state(pg)
            if after and after.get("审核") and not after.get("提交"):
                break
        log.info(f"提交后工具栏：{json.dumps(after, ensure_ascii=False)}")
        shot(pg, shot_dir, "sv_2_after", log)

        flipped = (before.get("提交") is True and after.get("提交") is False) or \
                  (before.get("审核") is False and after.get("审核") is True)
        log.info(f"状态翻转（提交禁用 / 审核可用）：{'是' if flipped else '否'}")

        # 服务端 UpdateMainMenuStates 原文里带 actionname，便于留痕
        acts = [x for x in kd.actions() if "Menu" in x or "State" in x]
        log.info(f"菜单状态相关 action：{acts[:10]}")

        if v == "bad":
            log.err("服务端报错，提交未成功")
            br.close()
            return 1
        if not flipped:
            log.err("按钮禁用态没有翻转 —— 视为提交未生效，请人工在金蝶界面确认")
            br.close()
            return 1
        log.ok("提交完成：按钮状态已翻转（提交→禁用，审核→可用）")

        # 回读单据状态字段（若页面有）
        st = eval_all_frames(pg, KingdeeUI._INVISIBLE + """() => {
            const el = Array.from(document.querySelectorAll('span[data-field]')).filter(vis)
                .filter(s => /STATUS/i.test(s.getAttribute('data-field')||''));
            return el.map(s => [(s.getAttribute('data-field')||''), (s.innerText||'').trim()]);
        }""")
        if st:
            log.info(f"状态字段：{st}")

        br.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GuardError as e:
        print(f"护栏拦截：{e}")
        sys.exit(2)
