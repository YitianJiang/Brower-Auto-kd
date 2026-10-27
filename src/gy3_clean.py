# -*- coding: utf-8 -*-
"""清场：只留「销售出库单列表」（config.safety.clean_tabs_after_voucher 的行为）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, abs_path  # noqa: E402
from steps import KingdeeUI  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_clean")
with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    pg = br.contexts[0].pages[0]
    ui = KingdeeUI(pg, CFG, log, None)
    log.info(f"清场前：{[t['text'] for t in ui.tabs()]}")
    ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=False)
    log.info(f"清场后：{[t['text'] for t in ui.tabs()]}")
    br.close()
