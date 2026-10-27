# -*- coding: utf-8 -*-
"""
用顶部全功能搜索打开一个列表页（只做"打开"，不动数据）。
用于把锚点标签「销售出库单列表」重新开回来，或在续跑前手工准备起点。

用法：
    python src/open_list.py --name 销售出库单列表
    python src/open_list.py --name 应收单列表
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright          # noqa: E402
from common import load_config, Logger, abs_path          # noqa: E402
from steps import KingdeeUI                               # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="功能名，如 销售出库单列表")
    a = ap.parse_args()

    cfg = load_config()
    log = Logger(abs_path("logs", cfg), name="open_list")
    # ★ 必须 try/finally 把 close 兜住 —— 抛异常时不 close 的话，
    #   Logger 缓冲的日志会整段丢掉（只剩第一行），排查时完全看不到中间过程。
    try:
        with sync_playwright() as p:
            br = p.chromium.connect_over_cdp(cfg["browser"]["cdp_url"])
            pg = br.contexts[0].pages[0]
            ui = KingdeeUI(pg, cfg, log, None)
            log.info(f"打开功能：{a.name}")
            ui.open_menu_by_search(a.name)
            log.info(f"当前标签：{[t['text'] for t in ui.tabs()]}")
            try:
                ui.activate_tab(a.name, timeout_ms=8000)
                log.ok(f"已切到 {a.name}")
            except Exception as e:  # noqa: BLE001
                log.warn(f"切标签失败：{str(e)[:100]}")
            br.close()
    except Exception as e:  # noqa: BLE001
        log.err(f"失败：{type(e).__name__}: {str(e)[:300]}")
        raise
    finally:
        log.close()


if __name__ == "__main__":
    main()
