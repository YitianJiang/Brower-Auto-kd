# -*- coding: utf-8 -*-
"""
操作录制器
----------
连接你已登录的调试浏览器，注入事件监听，把你后续的操作完整记录下来：
  - 点了哪个按钮（文本 / id / class / xpath / 祖先链文本）
  - 在哪个输入框填了什么（字段名 + 值）
  - 每步自动截图
  - 结束自动生成操作序列摘要

用法：
    python src/record.py                 # 开始录制，操作完按 Ctrl+C 停止
    python src/record.py --tag 下推应收单  # 给本次录制起个名

产出：
    output/capture/trace_<ts>.jsonl      完整事件流
    output/screenshots/seq_<n>.png       按步截图
    output/capture/summary_<ts>.md       操作序列摘要（我会看这个总结 SOP）
"""
import argparse
import json
import sys
import time
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright

from common import load_config, abs_path, Logger, shot

CFG = load_config()

# ============================================================
# 注入到页面里的录制脚本
# ============================================================
RECORDER_JS = r"""
(function () {
  if (window.__kdRec) return;
  window.__kdRec = true;
  window.__kdEvents = [];
  window.__kdSeq = 0;
  window.__kdInst = Math.random().toString(36).slice(2, 8);

  function esc(s) { return (s || '').trim().replace(/\s+/g, ' '); }

  function ancestors(el, n) {
    // 往上取 n 层的文本+标签，金蝶的字段标签常挂在父/祖先节点上
    const out = [];
    let p = el;
    for (let i = 0; i < n && p && p.nodeType === 1; i++) {
      const t = esc(p.innerText);
      if (t && t.length < 60) out.push({ tag: p.tagName, text: t.slice(0, 60) });
      p = p.parentElement;
    }
    return out;
  }

  function siblingsText(el) {
    // 同层前置兄弟的文本（表格里字段名常在左边单元格）
    const out = [];
    let prev = el.previousElementSibling;
    for (let i = 0; i < 3 && prev; i++) {
      const t = esc(prev.innerText);
      if (t && t.length < 40) out.push(t);
      prev = prev.previousElementSibling;
    }
    return out;
  }

  function xpath(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return "//*[@id='" + el.id + "']";
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && parts.length < 12) {
      let idx = 1, sib = cur.previousElementSibling;
      while (sib) { if (sib.tagName === cur.tagName) idx++; sib = sib.previousElementSibling; }
      const cls = (cur.className && typeof cur.className === 'string')
        ? '.' + cur.className.trim().split(/\s+/).slice(0, 2).join('.') : '';
      parts.unshift(cur.tagName.toLowerCase() + (cls && !cls.includes(' ') ? cls : '') +
                    '[' + idx + ']');
      cur = cur.parentElement;
    }
    return '/' + parts.join('/');
  }

  function describe(el) {
    if (!el || el.nodeType !== 1) return null;
    const r = el.getBoundingClientRect();
    let text = esc(el.innerText);
    if (!text) text = esc(el.value) || esc(el.getAttribute('title')) ||
                      esc(el.getAttribute('placeholder')) || '';
    return {
      tag: el.tagName,
      id: el.id || '',
      cls: (typeof el.className === 'string' ? el.className : '').slice(0, 120),
      name: el.getAttribute('name') || '',
      type: el.getAttribute('type') || '',
      title: el.getAttribute('title') || '',
      placeholder: el.getAttribute('placeholder') || '',
      aria: el.getAttribute('aria-label') || '',
      text: text.slice(0, 80),
      value: (el.value !== undefined && el.tagName !== 'BUTTON') ? String(el.value).slice(0, 120) : '',
      checked: el.checked === undefined ? null : !!el.checked,
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
      xpath: xpath(el),
      ancestors: ancestors(el, 4),
      prevSiblings: siblingsText(el),
      docTitle: document.title.slice(0, 60),
      frameUrl: location.href.slice(0, 160),
      frameName: window.name || '',
      framePath: (function () {
        try { let n = [], w = window; while (w !== w.parent) { n.unshift(w.name || '?'); w = w.parent; } return n; }
        catch (e) { return ['cross-origin']; }
      })()
    };
  }

  function push(type, el, extra) {
    const d = describe(el);
    if (!d) return;
    window.__kdSeq++;
    const ev = {
      seq: window.__kdSeq, inst: window.__kdInst, type: type,
      ts: Date.now(), wall: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
      d: d
    };
    if (extra) Object.assign(ev, extra);
    try { window.__kdEvents.push(ev); } catch (e) {}
    if (window.__kdEvents.length > 2000) window.__kdEvents.shift();
  }

  // 用捕获阶段，保证能拿到最深层的目标元素
  document.addEventListener('click', function (e) { push('click', e.target); }, true);
  document.addEventListener('mousedown', function (e) {
    // 只记右键和可能的下拉触发，避免噪音
    if (e.button === 2) push('rightclick', e.target);
  }, true);
  document.addEventListener('change', function (e) { push('change', e.target); }, true);
  document.addEventListener('focusin', function (e) { push('focus', e.target); }, true);
  document.addEventListener('input', function (e) {
    // input 太密集，节流：只保留最后一次
    const el = e.target;
    clearTimeout(el.__kdT);
    el.__kdT = setTimeout(function () { push('input', el); }, 400);
  }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === 'Escape' || e.key === 'F8') {
      push('key', e.target, { key: e.key });
    }
  }, true);
  // 双击（金蝶列表常用双击打开单据）
  document.addEventListener('dblclick', function (e) { push('dblclick', e.target); }, true);
})();
"""


def ensure_injected(fr, log=None):
    """确保某个 frame 已注入录制脚本"""
    try:
        ok = fr.evaluate("() => !!window.__kdRec")
        if not ok:
            fr.evaluate(RECORDER_JS)
            return True
    except Exception:
        pass
    return False


def collect(page, log=None):
    """从所有 frame 收集事件"""
    events = []
    for fr in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
        try:
            has = fr.evaluate("() => !!window.__kdEvents")
            if not has:
                continue
            evs = fr.evaluate("() => { const e = window.__kdEvents.slice(); window.__kdEvents = []; return e; }")
            if evs:
                events.extend(evs)
        except Exception:
            continue
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="", help="本次录制备注名")
    ap.add_argument("--seconds", type=int, default=0, help="录制多少秒后自动停，0=一直录到 Ctrl+C")
    args = ap.parse_args()

    log = Logger(abs_path("logs", CFG), name="record")
    cap_dir = abs_path("capture", CFG)
    cap_dir.mkdir(parents=True, exist_ok=True)
    shot_dir = abs_path("screenshots", CFG)

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = args.tag or "rec"
    trace_fp = cap_dir / f"trace_{ts}_{tag}.jsonl"

    all_events = []
    seen = set()
    shot_n = 0

    try:
        with sync_playwright() as p:
            log.info(f"连接调试浏览器 {CFG['browser']['cdp_url']}")
            try:
                browser = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
            except Exception as e:
                log.err(f"连接失败：{e}")
                log.err("请先运行「启动调试浏览器.bat」并登录金蝶")
                return 1

            # 对现有 context 注册初始化脚本：新页面/新 iframe 自动注入
            for c in browser.contexts:
                try:
                    c.add_init_script(RECORDER_JS)
                except Exception as e:
                    log.warn(f"add_init_script 失败：{e}")

            pages = [pg for c in browser.contexts for pg in c.pages]
            if not pages:
                log.err("没有打开的标签页")
                return 1

            log.info(f"发现 {len(pages)} 个标签页，将对【所有】标签页录制：")
            for i, pg in enumerate(pages):
                log.info(f"  [{i}] {pg.title()[:40]!r}  {pg.url[:80]}")

            # 对已存在的页面和 frame 立即注入
            for pg in pages:
                for fr in [pg.main_frame] + list(pg.frames):
                    ensure_injected(fr)

            log.ok("=" * 70)
            log.ok("录制已开始！现在请在浏览器里正常操作金蝶")
            log.ok("建议：完整地走一遍「出库单 → 应收单 → 发票 → 凭证」全流程")
            log.ok("操作完成后回到这里按 Ctrl+C 停止")
            log.ok("=" * 70)

            t0 = time.time()
            last_shot = 0

            try:
                while True:
                    if args.seconds and (time.time() - t0) > args.seconds:
                        log.info(f"已录满 {args.seconds} 秒，自动停止")
                        break

                    new_batch = []
                    active_page = None
                    for pg in pages:
                        try:
                            if pg.is_closed():
                                continue
                        except Exception:
                            continue
                        # 动态新出现的 iframe 补注入
                        for fr in [pg.main_frame] + list(pg.frames):
                            ensure_injected(fr)
                        for e in collect(pg):
                            key = (e.get("inst"), e.get("seq"))
                            if key in seen:
                                continue
                            seen.add(key)
                            new_batch.append(e)
                            all_events.append(e)
                            active_page = pg

                    if new_batch:
                        # 增量写盘（整批，不丢事件）
                        with open(trace_fp, "a", encoding="utf-8") as f:
                            for e in new_batch:
                                f.write(json.dumps(e, ensure_ascii=False) + "\n")
                        log.info(f"  捕获 {len(new_batch)} 条事件（累计 {len(all_events)}）")

                        # 节流截图（500ms 内不重复），只截产生事件的那个页面
                        now = time.time()
                        if now - last_shot > 0.5 and active_page is not None:
                            try:
                                shot_n += 1
                                shot(active_page, shot_dir, f"seq{shot_n:03d}_{tag}", None)
                            except Exception:
                                pass
                            last_shot = now

                    time.sleep(0.4)

            except KeyboardInterrupt:
                log.info("\n收到停止信号，正在保存...")

            browser.close()

    finally:
        # 排序 + 去重 + 写完整文件
        all_events.sort(key=lambda e: e.get("ts", 0))
        with open(trace_fp, "w", encoding="utf-8") as f:
            for e in all_events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        summary = build_summary(all_events)
        sum_fp = cap_dir / f"summary_{ts}_{tag}.md"
        sum_fp.write_text(summary, encoding="utf-8")

        log.ok(f"事件流 -> {trace_fp}  ({len(all_events)} 条)")
        log.ok(f"摘要   -> {sum_fp}")
        log.ok(f"截图   -> {shot_dir}  ({shot_n} 张)")
        print("\n" + "=" * 70)
        print(summary)
        log.close()

    return 0


def build_summary(events):
    """把事件流整理成人能读的操作序列"""
    lines = ["# 操作录制摘要", ""]
    lines.append(f"共 {len(events)} 条事件")
    lines.append("")
    lines.append("| # | 时间 | 动作 | 元素文本 | 值 | 定位(xpath) |")
    lines.append("|---|---|---|---|---|---|")

    n = 0
    for e in events:
        t = e.get("type")
        # focus 噪音大，单独降级处理
        d = e.get("d", {})
        txt = d.get("text", "") or d.get("placeholder", "") or d.get("title", "")
        val = d.get("value", "")
        # 找最近的字段名线索
        label = ""
        for a in d.get("ancestors", []):
            if a.get("text") and a["text"] != txt:
                label = a["text"][:30]
                break
        if not label and d.get("prevSiblings"):
            label = d["prevSiblings"][0][:30]
        if d.get("aria"):
            label = d["aria"][:30]

        act = {"click": "点击", "dblclick": "双击", "input": "输入",
               "change": "改变", "focus": "聚焦", "key": "按键",
               "rightclick": "右键"}.get(t, t)
        if t == "key":
            txt = f"按 {e.get('key')}"

        n += 1
        xp = d.get("xpath", "")
        if len(xp) > 60:
            xp = xp[:57] + "..."
        lines.append(f"| {n} | {e.get('wall','')} | {act} | {txt[:34]} | {str(val)[:26]} | `{xp}` |")

    # 单独列一份「填了什么字段」
    lines.append("")
    lines.append("## 输入过的字段")
    lines.append("")
    seen_f = set()
    found = False
    for e in events:
        if e.get("type") not in ("input", "change"):
            continue
        d = e.get("d", {})
        label = ""
        for a in d.get("ancestors", []):
            if a.get("text"):
                label = a["text"][:30]
                break
        if not label and d.get("prevSiblings"):
            label = d["prevSiblings"][0][:30]
        if not label:
            label = d.get("placeholder") or d.get("aria") or d.get("name") or "(未识别)"
        key = (label, str(d.get("value", "")))
        if key in seen_f:
            continue
        seen_f.add(key)
        found = True
        lines.append(f"- **{label}** = `{d.get('value','')}`  "
                     f"（xpath `{d.get('xpath','')[:70]}`）")
    if not found:
        lines.append("（本次录制没捕捉到输入事件）")

    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
