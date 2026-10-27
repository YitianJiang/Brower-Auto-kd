# -*- coding: utf-8 -*-
"""关掉残留的「处理结果」窗口（用真实鼠标点「退出/✕」）。

背景（2026-09-22 实测）：账套里飘着一个「处理结果」批处理结果窗，
报的是别的单据（XSCKD-101-202512-S0283，2025-12、组织101）的失败，
但它浮在最上层，会拦掉后续所有真实鼠标点击，导致「标签点不动」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger  # noqa: E402
from steps import eval_all_frames, KingdeeUI  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="close_result_dlg")


def main():
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, None)
        for attempt in range(1, 6):
            js = r"""() => {
                const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'; };
                const ws = Array.from(document.querySelectorAll('.k-window')).filter(vis);
                if (!ws.length) return null;
                const w = ws[ws.length - 1];
                const title = (() => { const t = w.querySelector('.k-window-title');
                                       return t ? (t.innerText||'').trim() : ''; })();
                const btns = Array.from(w.querySelectorAll('a,button,span.k-button'))
                    .filter(vis).map(x => (x.innerText||'').trim());
                // 优先「退出」「取消」「关闭」，其次右上角 ✕
                let el = Array.from(w.querySelectorAll('a,button,span.k-button')).filter(vis)
                    .find(x => ['退出', '取消', '关闭', '确定'].includes((x.innerText||'').trim()));
                if (!el) el = w.querySelector('[aria-label="Close"]');
                if (!el) return {title, btns, noClose: true};
                el.scrollIntoView({block: 'center'});
                const r = el.getBoundingClientRect();
                return {title, btns, x: Math.round(r.x + r.width / 2),
                        y: Math.round(r.y + r.height / 2),
                        label: (el.innerText||'').trim()};
            }"""
            info = eval_all_frames(pg, js)
            if not info:
                log.ok("已无可见弹窗")
                break
            log.info(f"第 {attempt} 个窗：title={info.get('title')!r} 按钮={info.get('btns')}")
            if info.get("noClose"):
                log.warn("没有可点的关闭按钮，留给人工")
                break
            pg.mouse.click(info["x"], info["y"])
            log.info(f"  已点 {info.get('label') or '✕'} @({info['x']},{info['y']})")
            pg.wait_for_timeout(1200)
        log.info(f"剩余标签：{[t['text'] for t in ui.tabs()]}")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
