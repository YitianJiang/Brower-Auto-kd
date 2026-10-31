# -*- coding: utf-8 -*-
"""页签配额急救：关掉「上限15」模态框 + 用 ctl.remove() 真正释放配额。

用法：python src/reset_quota.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, abs_path  # noqa: E402
from steps import KingdeeUI  # noqa: E402

cfg = load_config()
log = Logger(abs_path("logs", cfg), name="reset_quota")
try:
    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, cfg, log, None)
        log.info(f"清前：标签数 = {len(ui.tabs())} / 配额 = {ui.tab_quota()}")
        log.warn(f"关掉上限提示框：{ui.dismiss_quota_dialog() or '无'}")
        for i in range(3):
            before, after, removed = ui.release_tab_quota(keep_names=[], force=True)
            log.info(f"第 {i + 1} 轮释放：{before} → {after}（摘除 {removed}）")
            if after <= 2 or removed == 0:
                break
        log.info(f"清后：标签数 = {len(ui.tabs())} / 配额 = {ui.tab_quota()}")
        br.close()
finally:
    log.close()
