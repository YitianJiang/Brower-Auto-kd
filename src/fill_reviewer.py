# -*- coding: utf-8 -*-
"""在已打开的专票页面上填「复核人」并回读校验（暂不保存）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(r"C:/Users/Administrator/WorkBuddy/2026-09-18-10-11-38/kingdee-rpa/src")))
from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, abs_path, shot  # noqa: E402
from steps import KingdeeUI  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="fill_reviewer")
REVIEWER = "姚玉平"

with sync_playwright() as p:
    br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
    pg = br.contexts[0].pages[0]
    ui = KingdeeUI(pg, CFG, log, None)
    log.info(f"标签 = {ui.active_tab()!r}")

    log.info(f"填前：{ui.read_combo('F_PAEZ_COMBO')}")
    ui.fill_combo("F_PAEZ_COMBO", REVIEWER)
    pg.wait_for_timeout(600)
    log.info(f"填后：{ui.read_combo('F_PAEZ_COMBO')}")

    shot(pg, abs_path("screenshots", CFG), "fill_reviewer", log)
    br.close()
