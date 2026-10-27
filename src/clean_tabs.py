# -*- coding: utf-8 -*-
"""
清空金蝶标签页（生成凭证之后的清场动作）

为什么需要：金蝶 SPA 把所有开过的页面 DOM 留在 document 里，
           标签开得越多，脚本越容易命中的**隐藏页面**的元素
           （工具栏按钮、列表 grid、快捷过滤面板、分页栏全都栽过）。

用户口径（2026-09-21）：**生成凭证之后**清场，**保留「销售出库单列表」**
（它是下一组的起点，留着省一次搜索）。

用法：
    python src/clean_tabs.py                       # 只列出当前标签，不关
    python src/clean_tabs.py --go                  # 关掉全部（含销售出库单列表）
    python src/clean_tabs.py --go --keep-name 销售出库单列表   # 保留指定标签
    python src/clean_tabs.py --go --keep 1         # 保留最后 1 个
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, Logger, shot, abs_path  # noqa: E402
from steps import KingdeeUI  # noqa: E402

CFG = load_config()


def main():
    ap = argparse.ArgumentParser(description="清空金蝶标签页")
    ap.add_argument("--go", action="store_true", help="真关（默认只列出来）")
    ap.add_argument("--keep", type=int, default=0, help="保留最后 N 个标签")
    ap.add_argument("--keep-name", action="append", default=[],
                    help="名字含该关键字的标签保留（可重复，用户口径：销售出库单列表）")
    a = ap.parse_args()

    log = Logger(abs_path("logs", CFG), name="clean_tabs")
    shot_dir = abs_path("screenshots", CFG)

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, None)

        tabs = ui.tabs()
        log.info(f"当前 {len(tabs)} 个标签：")
        for t in tabs:
            log.info(f"   [{t['i']}] {'★ ' if t['active'] else '  '}{t['text']!r}")

        if not a.go:
            log.warn("没给 --go，只列出不关闭")
            br.close()
            return 0

        closed = ui.close_all_tabs(keep=a.keep, keep_names=a.keep_name or None)
        shot(pg, shot_dir, "tabs_cleaned", log)
        left = ui.tabs()
        log.info(f"关掉 {len(closed)} 个：{closed}")
        log.info(f"剩余 {len(left)} 个：{[t['text'] for t in left]}")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
