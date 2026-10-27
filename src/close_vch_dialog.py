# -*- coding: utf-8 -*-
"""关掉「凭证生成」对话框（不生成）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(r"C:/Users/Administrator/WorkBuddy/2026-09-18-10-11-38/kingdee-rpa/src")))
from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, abs_path, shot  # noqa: E402
from steps import KingdeeUI, eval_all_frames  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="close_vch_dialog")

with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    pg = br.contexts[0].pages[0]
    ui = KingdeeUI(pg, CFG, log, None)
    r = eval_all_frames(pg, """() => {
        const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
            return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
        const w = Array.from(document.querySelectorAll('.k-window')).filter(vis)
            .find(x => (x.innerText||'').includes('凭证生成'));
        if (!w) return null;
        const x = w.querySelector('.k-i-close, a.k-window-action');
        if (x) { x.click(); return 'closed'; }
        return 'no close btn';
    }""")
    log.info(f"关窗：{r}")
    pg.wait_for_timeout(1500)
    log.info(f"标签：{[t['text'] for t in ui.tabs()]}")
    log.info(f"激活：{ui.active_tab()!r}")
    shot(pg, abs_path("screenshots", CFG), "after_close_vch_dialog", log)
    br.close()
