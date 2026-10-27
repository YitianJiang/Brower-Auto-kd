# -*- coding: utf-8 -*-
"""
现场采集器
----------
连接已登录的调试浏览器，把当前金蝶页面的界面结构抓下来，
作为后续编写选择器（selector）的原料。

用法：
    python src/capture.py                  # 采集当前激活标签页
    python src/capture.py --list           # 只列出所有标签页
    python src/capture.py --index 1        # 采集第 N 个标签页
"""
import argparse
import json
import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from common import load_config, abs_path, Logger, shot

CFG = load_config()
ROOT = Path(__file__).resolve().parent.parent


# 页面里执行的 JS：把界面结构抽成结构化数据
JS_EXTRACT = r"""
() => {
  const out = {url: location.href, title: document.title, frames: [], buttons: [],
               inputs: [], tables: [], texts: []};
  const vis = el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const nearLabel = el => {
    // 就近找 label / 前置文本，金蝶的控件常常靠相邻单元格定位
    let p = el.closest('td,th,div,li');
    if (!p) return '';
    let prev = p.previousElementSibling;
    let s = '';
    for (let i = 0; i < 3 && prev; i++) {
      const t = (prev.innerText || '').trim();
      if (t && t.length < 30) { s = t; break; }
      prev = prev.previousElementSibling;
    }
    return s;
  };

  // iframe 清单（金蝶大量用 iframe）
  document.querySelectorAll('iframe').forEach(f => {
    out.frames.push({src: f.src || '', id: f.id || '', name: f.name || '',
                     cls: f.className || ''});
  });

  // 按钮 / 链接 / 可点元素
  document.querySelectorAll('button, a, [role=button], [class*=btn], [class*=Btn]').forEach(el => {
    if (!vis(el)) return;
    const t = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 40) return;
    const r = el.getBoundingClientRect();
    out.buttons.push({
      text: t, tag: el.tagName, id: el.id || '', cls: el.className || '',
      title: el.getAttribute('title') || '',
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height)
    });
  });

  // 输入框 / 下拉
  document.querySelectorAll('input, select, textarea').forEach(el => {
    if (!vis(el)) return;
    const r = el.getBoundingClientRect();
    out.inputs.push({
      tag: el.tagName, type: el.type || '', id: el.id || '', name: el.name || '',
      cls: el.className || '',
      placeholder: el.getAttribute('placeholder') || '',
      aria: el.getAttribute('aria-label') || '',
      value: (el.value || '').slice(0, 80),
      label: nearLabel(el),
      readonly: !!el.readOnly || !!el.disabled,
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height)
    });
  });

  // 表格
  document.querySelectorAll('table').forEach(tb => {
    const rows = [];
    Array.from(tb.querySelectorAll('tr')).slice(0, 8).forEach(tr => {
      const cells = [];
      tr.querySelectorAll('th,td').forEach(td => {
        const t = (td.innerText || '').trim().replace(/\s+/g, ' ');
        if (t) cells.push(t.slice(0, 40));
      });
      if (cells.length) rows.push(cells);
    });
    if (rows.length) out.tables.push({id: tb.id || '', cls: tb.className || '', rows});
  });

  // 关键文本（菜单/标签/提示）
  const seen = new Set();
  document.querySelectorAll('span, div, li, label, td').forEach(el => {
    if (!vis(el)) return;
    if (el.children.length > 0) return;
    const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 25 || seen.has(t)) return;
    seen.add(t);
    out.texts.push(t);
  });

  return out;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列出标签页")
    ap.add_argument("--index", type=int, default=-1, help="采集第几个标签页，-1=当前激活")
    ap.add_argument("--tag", default="", help="本次采集的备注名")
    args = ap.parse_args()

    log = Logger(abs_path("logs", CFG), name="capture")
    try:
        with sync_playwright() as p:
            log.info(f"连接调试浏览器 {CFG['browser']['cdp_url']}")
            browser = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])

            ctxs = browser.contexts
            pages = []
            for c in ctxs:
                pages.extend(c.pages)

            if not pages:
                log.err("没找到任何标签页，确认调试浏览器已启动且已打开金蝶页面")
                return

            log.info(f"发现 {len(pages)} 个标签页：")
            for i, pg in enumerate(pages):
                log.info(f"  [{i}] {pg.title()[:40]!r}  {pg.url[:90]}")

            if args.list:
                browser.close()
                return

            idx = args.index if args.index >= 0 else None
            page = pages[idx] if idx is not None and idx < len(pages) else pages[0]
            if idx is not None and idx >= len(pages):
                log.warn(f"索引 {idx} 超出范围，改用第 0 个")
                page = pages[0]

            log.info(f"采集目标：[{pages.index(page)}] {page.title()}")

            cap_dir = abs_path("capture", CFG)
            cap_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            tag = args.tag or "cap"
            base = cap_dir / f"{ts}_{tag}"

            # 1. 截图
            shot(page, abs_path("screenshots", CFG), f"capture_{tag}", log, full=False)

            # 2. 主框架结构
            data = page.evaluate(JS_EXTRACT)
            (cap_dir / f"{base.name}_main.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            # 3. 每个 iframe 也抓一遍（金蝶的内容几乎都在 iframe 里）
            frame_data = []
            for i, fr in enumerate(page.frames):
                if fr == page.main_frame:
                    continue
                try:
                    d = fr.evaluate(JS_EXTRACT)
                    d["_frame_index"] = i
                    d["_frame_name"] = fr.name
                    frame_data.append(d)
                    log.info(f"  iframe[{i}] {fr.name[:20]!r} "
                             f"按钮{len(d['buttons'])} 输入{len(d['inputs'])} 表{len(d['tables'])}")
                except Exception as e:
                    log.warn(f"  iframe[{i}] 采集失败: {e}")
            (cap_dir / f"{base.name}_frames.json").write_text(
                json.dumps(frame_data, ensure_ascii=False, indent=2), encoding="utf-8")

            # 4. 完整 DOM 存档
            for i, fr in enumerate(page.frames):
                try:
                    html = fr.content()
                    (cap_dir / f"{base.name}_dom_{i}.html").write_text(html, encoding="utf-8")
                except Exception:
                    pass

            log.ok(f"采集完成 -> {cap_dir}")
            log.info(f"  主框架: 按钮{len(data['buttons'])} 输入{len(data['inputs'])} "
                     f"表{len(data['tables'])} iframe{len(data['frames'])}")
            log.info(f"  文件: {base.name}_main.json / _frames.json / _dom_*.html")

            browser.close()
    finally:
        log.close()


if __name__ == "__main__":
    main()
