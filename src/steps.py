# -*- coding: utf-8 -*-
"""
界面步骤
--------
金蝶云星空 8.2.0 的界面操作封装。

【状态说明】
  - 通用工具（多 iframe 查找 / 点文本 / 填字段）已实现，可直接用。
  - 具体业务步骤（下推、过滤、填发票号…）目前是 TODO，
    要等现场采集（src/capture.py）拿到真实界面结构后才能填 selector。
    在此之前调用会抛 NotImplementedError，防止脚本瞎跑。
"""
import json
import time
from playwright.sync_api import Page, TimeoutError as PWTimeout

# ★ 单一真相源：金蝶主标签栏选择器 / widget 键名（SF-51 根因，见 kd_tab_sel.py 顶部说明）
from kd_tab_sel import MAIN_TAB_SELECTOR, WIDGET_KEY


# ============================================================
# 通用工具：金蝶大量使用 iframe，几乎所有操作都要跨 frame 查找
# ============================================================
def all_frames(page):
    """返回主框架 + 所有 iframe"""
    return [page.main_frame] + [f for f in page.frames if f != page.main_frame]


def find_by_text(page, text, tags=("button", "a", "span", "div", "li", "td", "input"),
                 exact=True, timeout=3000, visible_only=True):
    """
    在所有 frame 里按可见文本找元素，返回 (frame, locator) 或 (None, None)
    exact=True 要求完全相等，False 则为包含匹配
    """
    for fr in all_frames(page):
        try:
            if exact:
                loc = fr.get_by_text(text, exact=True)
            else:
                loc = fr.get_by_text(text, exact=False)
            # 限定标签
            loc = loc.first
            if loc.count() == 0:
                continue
            loc.wait_for(state="visible", timeout=timeout)
            return fr, loc
        except PWTimeout:
            continue
        except Exception:
            continue
    return None, None


def click_text(page, text, log=None, exact=True, timeout=3000, shot_dir=None, tag=""):
    """在所有 frame 里找文本并点击"""
    fr, loc = find_by_text(page, text, exact=exact, timeout=timeout)
    if fr is None:
        if log:
            log.err(f"找不到可点击元素：{text!r}")
        raise RuntimeError(f"找不到可点击元素：{text!r}")
    if log:
        log.info(f"点击 {text!r}")
    loc.click()
    return True


def click_if_exists(page, text, log=None, exact=True, timeout=1500):
    """能找到就点，找不到返回 False（用于可选按钮）"""
    try:
        fr, loc = find_by_text(page, text, exact=exact, timeout=timeout)
        if fr is None:
            return False
        loc.click()
        if log:
            log.info(f"点击(可选) {text!r}")
        return True
    except Exception:
        return False


def wait_busy(page, log=None, ms=800):
    """等金蝶服务端处理完（简单等待，后续可按加载遮罩优化）"""
    page.wait_for_timeout(ms)


def dump_visible_texts(page, limit=120):
    """调试用：打印当前页面所有可见文本，帮助定位"""
    texts = []
    for fr in all_frames(page):
        try:
            ts = fr.evaluate("""() => {
                const out = [];
                document.querySelectorAll('span,div,li,label,td,button,a').forEach(el=>{
                    if (el.children.length) return;
                    const s = getComputedStyle(el);
                    if (s.display==='none'||s.visibility==='hidden') return;
                    const t = (el.innerText||'').trim().replace(/\\s+/g,' ');
                    if (t && t.length<30) out.push(t);
                });
                return out;
            }""")
            texts.extend(ts)
        except Exception:
            pass
    # 去重保序
    seen = set()
    uniq = []
    for t in texts:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:limit]


# ============================================================
# Kendo / 金蝶 8.2 已验证的选择器
# 来源：E:\蒋一天\git\Pushdown\*.js（用户已实测跑通下推链路）
# ============================================================
SEL_SEARCH_BOX = 'input[placeholder="请选择内容后回车查询"]'
SEL_ROW_CHECKBOX = 'input[data-field="SELECTED"]'
SEL_MENU_ITEM = '.kdmenuitem-content'          # 下推 / 保存 / 提交 / 审核
SEL_CONFIRM_BTN = '[id*="FBTNOK_c"]'           # 弹窗「确定」
SEL_DATEPICKER = '[data-role="datepicker"]'
SEL_ACTIVE_TAB = '.k-item.k-state-active'
SEL_TAB_CONTENT = '.k-content, .k-tabstrip-content > div'


def eval_all_frames(page, js, arg=None):
    """
    在所有 frame 里执行 JS，返回第一个「真值」结果。
    金蝶主内容可能嵌在 iframe 里，逐个尝试最稳。
    """
    for fr in all_frames(page):
        try:
            r = fr.evaluate(js, arg)
            if r:
                return r
        except Exception:
            continue
    return None


def _norm_menu_id(js):
    """下推/保存这类菜单项的 id 带 -__definesub__ 后缀，真按钮 id 要去掉它"""
    return js


# ============================================================
# 业务步骤
# ============================================================
class KingdeeUI:
    _INVISIBLE = (
        "const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);"
        "return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'; };"
    )

    # ★★ 2026-10-03：**强化版可见性判定** —— 专门用于「定位控件」。
    #   `_INVISIBLE` 的 vis() 只看 width/height + display/visibility，会漏掉：
    #     ① 祖先 `display:none`（此时 `offsetParent === null`）
    #     ② 被祖先 `overflow` 裁掉 / 定位到屏幕外 / 被压在别的面板下面
    #   金蝶 SPA 里**后台标签页的表单 DOM 依然在**，这些"伪可见"元素会被
    #   `filter(vis)[0]` 挑中 → 脚本去点后台表单的控件或其下拉项 → **静默无效**。
    #   实测（2026-10-03 福建广源）：日志连续 3 次打印
    #     `选择下拉项：姚玉平 @(549,545)` 且命中检测通过，
    #   但当前（前台）表单回读**始终是空的** —— 点的是后台那个同名下拉。
    #   ⇒ 凡"按 id 找控件/选项"的地方一律用 svis，并用 onTop 优先挑前台那个。
    # ★★ 2026-10-03 第 3 修：**视口条件必须从 svis 里拿掉**（`inView` 单列）。
    #   实测（福建广源续跑，02:04）：视口只有 **1366 x 641**（Chrome 窗口矮），
    #   而「复核人」F_PAEZ_COMBO 的 EDITOR 落在 `rect=[471,701,156,21]` —— **在视口下方**。
    #   它所在的 `k-content` 是 `k-state-active`（就是当前前台那张单！），
    #   尺寸/显隐/offsetParent 全 OK，**唯独** `r.top(701) >= innerHeight(641)`
    #   → 旧的 svis 判 False → findEditor 返回 null
    #   → `open_combo` 抛「找不到下拉控件：'F_PAEZ_COMBO'（或不可见）」，
    #     半成品单直接收不了尾（`read_combo` 也回 None）。
    #   ⚠️ 讽刺的是：`elementFromPoint(549,711)` 也返回 **null**（点落在视口外），
    #      所以"在视口外"这件事**恰恰要靠滚动解决**，而不是把控件判成不存在。
    #   ⇒ 拆开：`svis` 只答"真的渲染出来了没"；`inView` 单独答"在不在视口内"。
    #     findEditor 的优先级：onTop（真前台）→ 在视口内且在**激活面板**里
    #     → 在激活面板里（哪怕在折叠下方，调用方会 scrollIntoView）→ 在视口内 → 兜底第 0 个。
    _VIS_STRONG = (
        "const svis = e => { if (!e) return false;"
        "const r = e.getBoundingClientRect(); const s = getComputedStyle(e);"
        "if (!(r.width>0 && r.height>0)) return false;"
        "if (s.display==='none' || s.visibility==='hidden' || s.opacity==='0') return false;"
        "if (e.offsetParent === null && s.position !== 'fixed') return false;"
        "return true; };"
        "const inView = e => { if (!e) return false;"
        "const r = e.getBoundingClientRect();"
        "return !(r.right<=0 || r.bottom<=0 || r.left>=innerWidth || r.top>=innerHeight); };"
        # 是否落在**当前激活的标签面板**里 —— 比"在视口内"更能区分前台/后台同名控件
        "const inActivePanel = e => { let p = e;"
        "while (p) { const c = p.classList;"
        "if (c && (c.contains('k-content') || c.contains('k-tabstrip-content')))"
        "return c.contains('k-state-active'); p = p.parentElement; } return false; };"
        "const onTop = e => { if (!e) return false;"
        "const r = e.getBoundingClientRect();"
        "const x = Math.round(r.x + r.width/2), y = Math.round(r.y + r.height/2);"
        "if (x<0||y<0||x>=innerWidth||y>=innerHeight) return false;"
        "const t = document.elementFromPoint(x, y);"
        "return !!(t && (e===t || e.contains(t))); };"
        # 找"当前真正在前台"的那个控件：svis 过滤（不再要求落在视口内），再按前台程度排序
        "const findEditor = f => {"
        "const eds = Array.from(document.querySelectorAll('[id$=\"-' + f + '-EDITOR\"]')).filter(svis);"
        "if (!eds.length) return null;"
        "return eds.find(onTop) || eds.filter(inView).find(inActivePanel)"
        "    || eds.find(inActivePanel) || eds.find(inView) || eds[0]; };"
    )

    def __init__(self, page: Page, cfg, log, guard):
        self.page = page
        self.cfg = cfg
        self.log = log
        self.guard = guard

    # ---------- 导航 ----------
    def goto(self, url):
        self.log.info(f"打开 {url}")
        self.page.goto(url)
        self.page.wait_for_load_state("networkidle", timeout=30000)

    def open_menu(self, *path):
        """按菜单路径逐级点开，如 open_menu('财务会计','应收款管理','销售应收','应收单列表')"""
        # 未采集：金蝶主界面功能树结构未知。
        # 【临时做法】先在浏览器里手动打开对应列表，脚本从列表开始接管即可。
        self.log.warn("open_menu 未实现 —— 请先在浏览器里手动打开目标列表，脚本从列表步骤接管")
        return False

    def open_menu_by_search(self, name):
        """用金蝶顶部搜索框打开功能"""
        # 未采集：顶部搜索框选择器未知，需现场确认
        self.log.warn(f"open_menu_by_search 未实现，请手动打开 {name}")
        return False

    def open_outstock_list(self):
        return self.open_menu_by_search("销售出库单列表")

    def open_receivable_list(self):
        return self.open_menu_by_search("应收单列表")

    def open_invoice_list(self):
        return self.open_menu_by_search("销售增值税专用发票列表")

    # ---------- 顶部「全功能搜索」导航（2026-09-21 实测） ----------
    #   搜索图标  .kd-fullsearch-searchicon
    #   输入框    input.kd-appplat-searchinput   (placeholder「请输入内容进行搜索」)
    #   结果条目  .kd-fullsearch-item-container  (文本 = 编码+名称 换行 菜单路径，
    #              如「YSDLB应收单列表 / 财务会计>应收款管理>销售应收」)
    #
    # ★★ 最大的坑：**在输入框里打字根本不触发搜索！**
    #   实测：type() 期间 0 个网络请求，面板一直显示「最近搜索历史」。
    #   搜索只在**面板被打开的那一瞬间**发起（用输入框里已有的值）。
    #   ⇒ 所以流程必须是「打字 → Esc 关面板 → 再点图标重开」，这时才真正去搜。
    #   （同理：上次的搜索值会留在输入框里，重开面板会顺手重搜，看到"旧结果"别以为是缓存问题。）
    #   Enter 也不行：回车只是关掉面板，不打开任何功能。
    #
    # ★ 重名条目：同一个名字常有多条（销售出库单列表有 3 条，分属销售/库存/电商），
    #   必须靠「菜单路径」消歧，见 MENU_PATH_HINT。
    MENU_PATH_HINT = {
        "销售出库单列表": "供应链>销售管理>出货处理",
        "应收单列表": "财务会计>应收款管理>销售应收",
        "销售增值税专用发票列表": "税务管理>发票管理>销售发票",
        "凭证查询": "财务会计>总账>凭证管理",
    }

    def _search_panel_open(self):
        """面板是否已打开（打开时搜索图标会被隐藏）"""
        js = self._INVISIBLE + """() => !!(Array.from(
            document.querySelectorAll('input.kd-appplat-searchinput')).filter(vis)[0])"""
        return bool(eval_all_frames(self.page, js))

    def open_search_panel(self):
        """打开顶部全功能搜索面板（已开着就原样返回）"""
        if self._search_panel_open():
            return True
        icon = self.page.locator(".kd-fullsearch-searchicon:visible").first
        if icon.count() == 0:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(800)
            if icon.count() == 0:
                raise RuntimeError("找不到顶部搜索图标 .kd-fullsearch-searchicon")
        icon.click(timeout=5000)
        self.page.wait_for_timeout(1400)
        if not self._search_panel_open():
            raise RuntimeError("搜索面板没弹出来")
        return True

    def search_results(self, limit=30):
        """当前面板里可见的结果条目原文（含「最近搜索历史」）"""
        js = self._INVISIBLE + """(n) => Array.from(
            document.querySelectorAll('.kd-fullsearch-item-container')).filter(vis)
            .slice(0, n).map(e => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80))"""
        return eval_all_frames(self.page, js, limit) or []

    def open_menu_by_search(self, name, path=None, wait_ms=4000, tries=3):
        """
        用顶部全功能搜索打开功能页（列表/报表等）。
        name: 功能名，如「销售出库单列表」
        path: 菜单路径片段（重名时消歧），不给就用 MENU_PATH_HINT 里的默认值
        """
        want = path or self.MENU_PATH_HINT.get(name)

        # ★ 2026-09-22 补：目标列表**已经开着**就直接切过去，别再走搜索。
        #   实测踩坑：页面上开着「应收单 - 新增」时，它的 .kd-form-collapse-container
        #   会浮在最上层拦截搜索条目的点击（报错 `... intercepts pointer events`），
        #   表现为"搜到了但点不动"。已有标签时切标签既稳又快。
        for t in self.tabs():
            if (t.get("text") or "").strip() == name:
                self.log.info(f"「{name}」标签已存在，直接激活（跳过搜索）")
                self.page.mouse.click(t["tx"], t["ty"])
                self.page.wait_for_timeout(2000)
                return True

        # ★★ 2026-10-03 补：开搜索面板前，**先把焦点从「单据表单页」挪开**。
        #   实测（本批 8 家死锁）：当前激活页是「应收单 - 新增 / 销售增值税专用发票 - 新增」
        #   这类表单页时，它的 `.kd-form-collapse-container` 浮层会盖住搜索结果条目，
        #   `locator.click` 报 `intercepts pointer events` 超时 —— 搜到了却点不动。
        #   而表单页常常还是**关不掉的孤儿**（内容面板已销毁，✕ 无反应），
        #   所以只能"挪焦点"，不能靠"关掉它"。
        try:
            tbs = self.tabs()
            cur = next((t for t in tbs if t.get("active")), None)
            txt = (cur or {}).get("text") or ""
            if ("- 新增" in txt) or ("- 修改" in txt):
                safe = (next((t for t in tbs
                              if (t.get("text") or "").strip().endswith("列表")), None)
                        or next((t for t in tbs
                                 if not (t.get("text") or "").strip()), None))
                if safe and safe.get("tx") is not None:
                    self.log.warn(f"当前激活页是表单「{txt}」，先切到"
                                  f"「{safe.get('text') or '(无题)'}」再搜索"
                                  f"（表单浮层会拦住搜索结果）")
                    self.page.mouse.click(safe["tx"], safe["ty"])
                    self.page.wait_for_timeout(1200)
        except Exception as e:  # noqa: BLE001
            self.log.warn(f"切离表单页失败（继续）：{str(e)[:80]}")

        # ★ 2026-09-21 补：打开功能前先清掉挡路的弹窗。实测场景——上一步的
        #   「过滤」对话框没关 / 某功能页一打开就自弹过滤框，会浮在最上层拦截
        #   真实鼠标点击（报错 `... _Filter-FORGPANEL_fill ... intercepts pointer events`），
        #   表现为"搜索到了但点不动"。这里先扫一遍，代价很小。
        try:
            self.close_stray_windows(max_iter=3)
        except Exception as e:  # noqa: BLE001
            self.log.warn(f"清挡路弹窗异常（继续）：{e}")

        # ★ 2026-10-03：再清一次**残留浮动提示条**（`close_stray_windows` 只管 `.k-window`）。
        #   它浮在右上角、正压着搜索面板的结果列表，是"搜到了却点不动"的真凶。
        try:
            self.dismiss_floating_hints()
        except Exception as e:  # noqa: BLE001
            self.log.warn(f"隐藏浮动提示条异常（继续）：{str(e)[:80]}")

        last = []
        for attempt in range(1, tries + 1):
            # ① 关掉可能开着的面板（面板开着时图标不可见）
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(800)
            # ② 开面板 → 清空 → 输入
            self.open_search_panel()
            box = self.page.locator("input.kd-appplat-searchinput:visible").first
            box.click()
            box.press("Control+a")
            box.press("Backspace")
            box.fill("")
            box.type(name, delay=70)
            self.page.wait_for_timeout(500)
            # ③ ★ Esc 关掉再重开 —— 搜索只在「开面板」时发起
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(800)
            self.open_search_panel()

            # ④ 轮询等结果
            t0 = time.time()
            cands = []
            while (time.time() - t0) * 1000 < 8000:
                items = self.search_results()
                cands = [t for t in items if name in t]
                if cands and (want is None or any(want in t for t in cands)):
                    break
                self.page.wait_for_timeout(600)
            if cands:
                last = cands
                break
            self.log.warn(f"第 {attempt} 次没搜到 {name!r}，重试")

        if not last:
            raise RuntimeError(f"搜索结果里没有 {name!r}（尝试 {tries} 次）")

        # ⑤ 选条目：有路径提示就按提示挑，多条同名且没提示就报错让人工指定
        pick = next((t for t in last if want and want in t), None)
        if pick is None:
            if len(last) > 1:
                raise RuntimeError(
                    f"{name!r} 有 {len(last)} 条同名结果，需要指定 path：{last}")
            pick = last[0]

        # ★ 2026-09-22：结果条目改用**真实鼠标**点。
        #   实测：locator.click() 会被页面上别的容器（如「应收单 - 新增」的
        #   .kd-form-collapse-container）拦成 `intercepts pointer events` 超时；
        #   真实鼠标点坐标不受这层拦截影响。
        # ★★ 2026-10-03 补（本批 8 家死锁的关键一环）：**结果条目被浮层盖住时，
        #   先把挡路那层的 `pointer-events` 临时关掉，点完立刻还原**。
        #   背景：界面上会永久驻留关不掉的孤儿草稿页「应收单 - 新增」，
        #   它的 `.kd-form-collapse-container` 正好压在搜索结果条目上 ——
        #   此时 `hit()` 为 false，真实鼠标点下去也只会打在那层浮层上（点空），
        #   `locator.click` 更是直接超时。光靠"挪焦点"（SF-49）解决不了，
        #   因为那层浮层是 `draggable` 的独立容器、并不随标签切换显隐。
        #   ⇒ 剥层：从坐标处的顶层元素往上找，逐个把挡路的
        #     `.kd-form-collapse-container`（或实在找不到就直接禁掉顶层元素）
        #     的 pointer-events 设为 none，直到命中我们的条目为止；记得还原。
        js_pos = self._INVISIBLE + """(kws) => {
            // 还原上一次可能残留的剥离（幂等，防跨次污染）
            const restore = () => {
                const rec = window.__kdPeel || [];
                for (const [el, v] of rec) { try { el.style.pointerEvents = v; } catch (e) {} }
                window.__kdPeel = [];
            };
            restore();
            const items = Array.from(
                document.querySelectorAll('.kd-fullsearch-item-container')).filter(vis);
            for (const kw of kws) {
                const el = items.find(e => (e.innerText || '').includes(kw));
                if (!el) continue;
                el.scrollIntoView({block: 'center'});
                let r = el.getBoundingClientRect();
                let cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
                // ★ 命中检测：坐标必须在视口内、且该点的顶层元素确实属于这一项。
                //   实测踩过：条目 y=-32（在视口上方），mouse.click 点了个空，
                //   面板没关、功能也没打开 —— 后面报"找不到可见列表网格"。
                const hit = () => {
                    const t = document.elementFromPoint(cx, cy);
                    return t && el.contains(t);
                };
                if (cy < 0 || cy > innerHeight || !hit()) {
                    el.scrollIntoView({block: 'start'});
                    r = el.getBoundingClientRect();
                    cx = Math.round(r.x + r.width / 2);
                    cy = Math.round(r.y + r.height / 2);
                }
                if (cy < 0 || cy > innerHeight) return null;
                // ★ 剥掉挡路浮层（最多 5 层）
                let peeled = 0;
                for (let k = 0; k < 5 && !hit(); k++) {
                    const top = document.elementFromPoint(cx, cy);
                    if (!top || top === document.body || top === document.documentElement) break;
                    let victim = null, p = top;
                    while (p && p !== document.body) {
                        if (p.classList && p.classList.contains('kd-form-collapse-container')) {
                            victim = p; break;
                        }
                        p = p.parentElement;
                    }
                    if (!victim) victim = top;          // 兜底：禁掉顶层元素本身
                    if (victim === el || el.contains(victim)) break;
                    (window.__kdPeel = window.__kdPeel || []).push(
                        [victim, victim.style.pointerEvents || '']);
                    victim.style.pointerEvents = 'none';
                    peeled++;
                }
                if (!hit()) return null;
                return {x: cx, y: cy, peeled: peeled,
                        text: (el.innerText || '').trim().slice(0, 60)};
            }
            return null;
        }"""
        js_unpeel = """() => { const rec = window.__kdPeel || [];
            for (const [el, v] of rec) { try { el.style.pointerEvents = v; } catch (e) {} }
            window.__kdPeel = []; return rec.length; }"""
        pos = eval_all_frames(self.page, js_pos, [h for h in ([want, name] if want else [name]) if h])
        if pos:
            if pos.get("peeled"):
                self.log.warn(f"搜索结果被浮层挡住 → 临时剥掉 {pos['peeled']} 层 "
                              f"pointer-events 再点（点完还原）")
            self.page.mouse.click(pos["x"], pos["y"])
            self.page.wait_for_timeout(300)
            eval_all_frames(self.page, js_unpeel)
            self.log.info(f"打开功能（真实鼠标 {pos['x']},{pos['y']}）：{pick}")
        else:
            # ★ 剥层了却没点成 → **必须把 pointer-events 还回去**，
            #   否则残留的 `pointer-events:none` 会把后续点击也废掉（比原问题更糟）。
            try:
                eval_all_frames(self.page, js_unpeel)
            except Exception:  # noqa: BLE001
                pass
            # ★★ 2026-10-03 关键修复（本批 8 家死锁的最后一环）：
            #   **改用 JS `el.click()` 兜底，不要再退回 `locator.click()`**。
            #   实测（`probe_search_click.py`，2026-10-03）：
            #     · 挡路的是一个 `z-index:10010` 的**残留浮动提示条**
            #       `.kd-form-collapse-container`（`draggable=true`），
            #       文本 `提交：保存：1837971/2 该字段需要您手动查找` ——
            #       是**之前一次失败保存留下的提示**，它正好压住右上角搜索面板的结果列表
            #       （结果条目中心 (996,107) 就在它的 rect [991,57,214,58] 内）。
            #     · 该点上"剥掉浮层"也没用：浮层**下面直接就是 BODY**，
            #       条目本身并不在那个坐标上（rect 与实际命中点不一致）
            #       ⇒ 真实鼠标点、`locator.click` **都必然失败**（后者还白等 6 秒 ×N 次重试）。
            #     · 而 **JS 合成点击 `el.click()` 是有效的**（实测点完标签列表立刻多出
            #       `销售出库单列表`）—— 说明该条目绑的是 jQuery click 处理器，
            #       不走 Kendo 的指针事件，所以合成 click 能触发。
            #   ⇒ 兜底顺序：真实鼠标（带剥层） → **JS el.click()** → 最后才 locator。
            js_click = self._INVISIBLE + """(kws) => {
                const items = Array.from(
                    document.querySelectorAll('.kd-fullsearch-item-container')).filter(vis);
                for (const kw of kws) {
                    const el = items.find(e => (e.innerText || '').includes(kw));
                    if (!el) continue;
                    el.click();
                    return (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
                }
                return null;
            }"""
            got = eval_all_frames(
                self.page, js_click,
                [h for h in ([want, name] if want else [name]) if h])
            if got:
                self.log.warn(f"搜索结果被残留浮动条挡住，真实鼠标点不到 → "
                              f"改用 JS el.click() 兜底：{got}")
            else:
                item = self.page.locator(".kd-fullsearch-item-container:visible").filter(
                    has_text=name).filter(has_text=want) if want else \
                    self.page.locator(".kd-fullsearch-item-container:visible").filter(has_text=name)
                item.first.click(timeout=6000)
                self.log.info(f"打开功能（locator 兜底）：{pick}")
        self.page.wait_for_timeout(wait_ms)
        # ★ 2026-10-03：标签数打点。tabstrip 只有 1156px，一个标签 108px
        #   → **超过约 10 个就会被压扁、互相错位**，`activate_tab()` 会点到别的页
        #   （现象：下一步报「找不到可见的列表网格」）。把每次开功能页前后的
        #   标签数记下来，才能定位标签是在哪一步涨起来的。
        try:
            ts = self.tabs()
            self.log.info(f"标签数 = {len(ts)} {[t['text'][:14] for t in ts]}")
        except Exception as e:  # noqa: BLE001
            self.log.warn(f"读标签数失败：{str(e)[:60]}")
            ts = []

        # ★★ 2026-10-29 根治（河南华洋事故）：**「点了」≠「开了」**。
        #   实测（2026-10-29 16:54:42 华洋）：真实鼠标点了「YSDLB应收单列表」条目，
        #   紧接着 `标签数 = 1 ['销售出库单列表']` —— 应收单列表**根本没打开**。
        #   旧代码不看这个、直接 return True，于是后面在**出库单列表的 grid** 上
        #   按 AR 单号过滤（当然 0 行）→ 报「找不到可见的列表网格」，
        #   看着像勾选/虚拟滚动问题，实际是页面没开。同类还有：
        #   「超出页签上限15个」模态框挡在最上层时，点条目不生效但也不报错。
        #   ⇒ 开完必须校验目标页签真的出现；没出现 → 关弹窗 + 清浮层后重试 ≤2 次；
        #     仍不出现 → **直接抛错**，绝不在旧页面上继续操作（宁可停，不推错账）。
        t0 = time.time()
        while (time.time() - t0) * 1000 < 6000:
            if any(name in (t.get("text") or "") for t in ts):
                break
            self.page.wait_for_timeout(500)
            try:
                ts = self.tabs()
            except Exception:  # noqa: BLE001
                ts = ts or []
        if not any(name in (t.get("text") or "") for t in ts):
            depth = getattr(self, "_open_retry", 0)
            if depth < 2:
                self.dismiss_quota_dialog()
                # ★ 关键：先真正释放配额（幽灵 li / 残留表单页），否则重试也是点空。
                #   保留工作锚点「销售出库单列表」与目标页。
                self.release_tab_quota(keep_names=("销售出库单列表", name), force=True)
                try:
                    self.close_stray_windows(max_iter=2)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.dismiss_floating_hints()
                except Exception:  # noqa: BLE001
                    pass
                self.log.warn(f"打开 {name!r} 未生效（标签数={len(ts)}）→ "
                              f"清弹窗后第 {depth + 1} 次重试")
                self._open_retry = depth + 1
                try:
                    return self.open_menu_by_search(name, path=path,
                                                   wait_ms=wait_ms, tries=1)
                finally:
                    self._open_retry = 0
            raise RuntimeError(
                f"打开功能页 {name!r} 失败：点击后标签栏没有出现该页"
                f"（当前 {[t.get('text', '')[:14] for t in ts]}）。"
                f"常见原因：① 页签配额满 15（弹窗被关但仍未释放，需 ctl.remove 摘孤儿）"
                f"② 登录态失效 ③ 弹窗/浮层挡住。已停止，不在旧页面上继续操作。")
        return True

    # ---------- 列表操作（已验证） ----------
    # ⚠️⚠️ 又一个同类坑（2026-09-21）：`document.querySelector('table.k-selectable')` 取到的是
    #     **隐藏页面**那张列表（如销售出库单列表），不是屏幕上这张应收单列表 ——
    #     跟工具栏按钮是同一个根源：SPA 里每个开过的页面 DOM 都留着。
    #     所以：读列表/勾行前，必须先按可见性筛出「当前这张」grid。
    GRID_JS_VISIBLE = None  # 见方法内联实现（常量移到类顶部 _INVISIBLE）

    def visible_grid_heads(self):
        """读当前可见列表的列头（表头在 .k-grid-header 里，不在数据表的 thead 里）"""
        js = self._INVISIBLE + """() => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return null;
            const wrap = g.closest('.k-grid') || g.parentElement.parentElement;
            return Array.from(wrap.querySelectorAll('th')).filter(vis)
                .map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))
                .map(t => t.replace(/^设置\\s*/, '')).filter(Boolean);
        }"""
        return eval_all_frames(self.page, js)

    def visible_grid_rows(self, limit=5):
        """读当前可见列表前几行的单元格文本"""
        js = self._INVISIBLE + """(n) => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return null;
            const trs = Array.from(g.querySelectorAll('tbody tr'));
            return {
                rowCount: trs.length,
                pages: 1,
                rows: trs.slice(0, n).map(tr => Array.from(tr.querySelectorAll('td'))
                    .map(td => (td.innerText || '').trim().replace(/\\s+/g, ' ')).filter(Boolean)),
            };
        }"""
        return eval_all_frames(self.page, js, limit)

    def visible_grid_table(self, limit=200):
        """
        读当前可见列表 = {heads, rows}，**列严格对齐**。

        ⚠️ 不要用 visible_grid_rows 做解析：它 filter(Boolean) 把空单元格吃掉了，
           只要某行有一列为空（发票列表的「发票号」常年为空），整行就往前错位一格。
           实测（2026-09-21）发票列表因此把「发票日期」读成了「开票方式」那一格的值。

        返回 rows 为等长 list，空列保留为 ''，与 heads 下标一一对应。
        """
        js = self._INVISIBLE + """(n) => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return null;
            const wrap = g.closest('.k-grid') || g.parentElement.parentElement;
            // 表头第 1 列是勾选列（文本空），不能 filter ，否则列头少一项 → 错位
            const heads = Array.from(wrap.querySelectorAll('th')).filter(vis)
                .map(e => (e.innerText || '').trim().replace(/^设置\\s*/, '').replace(/\\s+/g, ' '));
            const trs = Array.from(g.querySelectorAll('tbody tr'));
            const rows = trs.slice(0, n).map(tr => {
                const tds = Array.from(tr.querySelectorAll('td'));
                return tds.map(td => (td.innerText || '').trim().replace(/\\s+/g, ' '));
            });
            return { rowCount: trs.length, heads, rows };
        }"""
        return eval_all_frames(self.page, js, limit)

    def grid_all_rows(self, min_rows=1, timeout_ms=20000):
        """
        读当前可见列表的**【全量数据源】**（不是 DOM）。

        为什么需要（2026-09-21 实测）：金蝶列表是**虚拟滚动**，tbody 永远只渲染 200 行
        （`.k-grid-content` clientHeight 318 / scrollHeight 5400），分页栏写「当前1-200行,
        总共1067行」。DOM 抓不到后面几百行 → 只能从数据源取。

        实现：可见 `table.k-selectable` → 最近的 `.kd-grid-element` →
        `$(el).data('kendoKDGrid')`（⚠️ 金蝶是自定义 `kendoKDGrid`，不是标准 `kendoGrid`）
        → `.dataSource.data()`。

        返回 [{字段: 标量}]，只保留字符串/数字/布尔（丢掉 Kendo 的 _handlers/__value 等噪音）。
        出库单列表实测字段：FDATE / FDATE1(账务日期) / FBILLNO(单据编号) /
        FCUSTOMERID_FNAME(客户) / FDOCUMENTSTATUS(单据状态) / FMATERIALID_FNUMBER /
        FMATERIALNAME / FUNITID_FNAME(单位) / FREALQTY(实发数量) / FARJOINQTY(关联应收数量)。
        """
        js = self._INVISIBLE + """() => {
            const $ = window.jQuery;
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return null;
            const gridEl = g.closest('.kd-grid-element');
            if (!gridEl) return {err: 'no .kd-grid-element'};
            const grid = $(gridEl).data('kendoKDGrid');
            if (!grid) return {err: 'no kendoKDGrid'};
            const flat = row => {
                const o = {};
                for (const k in row) {
                    if (k.startsWith('_') || k.endsWith('__value')) continue;
                    const v = row[k], t = typeof v;
                    if (v === null) { o[k] = null; continue; }
                    if (t === 'string' || t === 'number' || t === 'boolean') o[k] = v;
                }
                return o;
            };
            const data = grid.dataSource.data().map(flat);
            return {n: data.length, domRows: g.querySelectorAll('tbody tr').length, rows: data};
        }"""
        r = eval_all_frames(self.page, js)
        # ★ 2026-09-21 补：点完「过滤确定」后列表在异步重载，这一瞬间
        #   可见 grid 会短暂不存在（eval 回 None），或者 grid 已出现但数据还没回来
        #   （读到 0 行）→ 直接返回会把"还在加载"误判成"真的没有单据"。
        #   所以：轮询等待到 min_rows 行（或超时），不要一枪定生死。
        waited = 0
        warned = False
        while waited < timeout_ms:
            ok = isinstance(r, dict) and not r.get("err") and r.get("n", 0) >= min_rows
            if ok:
                break
            if not warned:
                why = r if not isinstance(r, dict) else (r.get("err") or f"仅 {r.get('n')} 行")
                self.log.info(f"列表数据源未就绪（{why}），等待加载…")
                warned = True
            self.page.wait_for_timeout(700)
            waited += 700
            r = eval_all_frames(self.page, js)
        if not isinstance(r, dict) or r.get("err"):
            raise RuntimeError(f"读列表数据源失败：{r}")
        self.log.info(f"列表数据源：{r['n']} 行（DOM 只渲染 {r['domRows']} 行）")
        return r["rows"]

    def grid_find_rows(self, col, match, limit=200):
        """
        在可见列表里按某列的值做包含匹配，返回 [(行下标, 行数据(dict))]。
        col 用列名（如 '客户' / '单据编号' / '业务日期' ），match 为子串或函数式正则字符串。
        """
        t = self.visible_grid_table(limit=limit)
        if not isinstance(t, dict):
            return []
        heads = t.get("heads") or []
        if col not in heads:
            raise RuntimeError(f"列表里没有列 {col!r}，现有列：{heads}")
        ci = heads.index(col)
        out = []
        for i, row in enumerate(t.get("rows") or []):
            val = row[ci] if ci < len(row) else ""
            if isinstance(match, str) and match in val:
                out.append((i, {h: (row[j] if j < len(row) else "") for j, h in enumerate(heads)}))
        return out

    def select_row_by_index(self, idx=0):
        """勾选当前可见列表的第 idx 行（0 基）。idx=1 表示第 1 行。"""
        js = self._INVISIBLE + """(i) => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return {err: 'no visible grid'};
            const trs = Array.from(g.querySelectorAll('tbody tr'));
            if (i >= trs.length) return {err: 'out of range', n: trs.length};
            const tr = trs[i];
            // 实测（2026-09-21）行首结构：div.kd-grid-selector(data-field=SELECTED,data-rowid=N)
            //   > input.k-checkbox.gridCheck[onclick="return false"] + label.k-checkbox-label
            // 表头那个 input 多一个 gridCheckHeader class，别混进来
            const cb = tr.querySelector('input.gridCheck:not(.gridCheckHeader)')
                    || tr.querySelector('.kd-grid-selector input[type="checkbox"]')
                    || tr.querySelector('input[data-field="SELECTED"]')
                    || tr.querySelector('input[type="checkbox"]');
            if (!cb) return {err: 'no checkbox in row'};
            // 幂等：已经勾上就别再点（点第二次会取消勾选）
            if (cb.checked) return { ok: true, already: true, text: (tr.innerText||'').trim().replace(/\\s+/g,' ').slice(0, 160) };
            const lab = cb.nextElementSibling;
            const target = (lab && lab.classList.contains('k-checkbox-label')) ? lab : cb;
            ['mousedown','mouseup','click'].forEach(t =>
                target.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window})));
            return { ok: true, text: (tr.innerText||'').trim().replace(/\\s+/g,' ').slice(0, 160) };
        }"""
        r = eval_all_frames(self.page, js, idx)
        if not isinstance(r, dict) or r.get("err"):
            raise RuntimeError(f"勾选第 {idx} 行失败：{r}")
        self.page.wait_for_timeout(1000)
        self.log.info(f"勾选第 {idx + 1} 行（真实选中 {self.selected_count()}）：{r.get('text')[:80]}")
        return r

    # 现场采集确认：金蝶 8.2 快捷过滤面板里有两个值输入框，placeholder 不同
    #   「请输入关键字后回车查询」= 文本类字段（单据编号）
    #   「请选择内容后回车查询」  = 基础资料类字段（客户）
    def search(self, keyword, field=None, wait_ms=2500):
        """
        在快捷过滤面板输入关键字并回车。
        field=None      -> 用第一个可见的输入框
        field='单据编号' -> 用「请输入关键字后回车查询」
        field='客户'    -> 用「请选择内容后回车查询」
        """
        js = self._INVISIBLE + """(a) => {
            const [kw, field] = a;
            // ⚠️ 必须先锁定【当前可见的快捷过滤面板】——
            //    document 里同时存在出库单列表/应收单列表等多个 filterquicker，
            //    直接全局找 input 会填到隐藏页面的框里（2026-09-21 实测踩过）
            const panels = Array.from(document.querySelectorAll('.kd-filterquicker-filtertop')).filter(vis);
            const panel = panels[0];
            const root = panel ? (panel.closest('[class*=filterquicker]') || panel.parentElement) : null;
            let boxes = (root ? Array.from(root.querySelectorAll('input[placeholder]')) : [])
                .filter(e => e.offsetParent !== null
                          && (e.getAttribute('placeholder') || '').includes('回车查询'));
            if (!boxes.length) {
                boxes = Array.from(document.querySelectorAll('input[placeholder]'))
                    .filter(e => e.offsetParent !== null
                              && (e.getAttribute('placeholder') || '').includes('回车查询'));
            }
            if (!boxes.length) return false;
            let el = null;
            if (field === '单据编号' || field === '文本')
                el = boxes.find(e => e.getAttribute('placeholder').includes('请输入关键字'));
            else if (field === '客户' || field === '资料')
                el = boxes.find(e => e.getAttribute('placeholder').includes('请选择内容'));
            el = el || boxes[0];
            el.click(); el.focus();
            el.value = '';
            el.value = kw;
            ['input','change','keydown','keyup'].forEach(t =>
                el.dispatchEvent(new Event(t, {bubbles:true})));
            el.dispatchEvent(new KeyboardEvent('keydown',
                {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}));
            return el.getAttribute('placeholder') + ' | 共' + boxes.length + '个可见框';
        }"""
        ph = eval_all_frames(self.page, js, [str(keyword), field])
        if not ph:
            raise RuntimeError("未找到快捷过滤输入框（placeholder 含「回车查询」）")
        self.log.info(f"搜索[{ph}]：{keyword}")
        self.page.wait_for_timeout(wait_ms)
        return True

    def select_all_rows(self):
        """
        点表头全选框。
        现场采集确认的结构：
          <div data-role="checkbox" class="kd-grid-celldiv kd-grid-selector" data-field="SELECTED" data-rowid="-99">
            <input type="checkbox" onclick="return false;" class="k-checkbox gridCheck gridCheckHeader" ...>
            <label class="k-checkbox-label"></label>
          </div>
        注意 input 带 onclick="return false"，必须点旁边的 label 才生效。
        表头 rowid = -99，数据行 rowid = 1..N
        """
        js = self._INVISIBLE + """() => {
            // 只在【当前可见 grid】的表头里找（隐藏页面的同名表头会先被命中）
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            const root = g ? (g.closest('.k-grid') || g.parentElement.parentElement) : document;
            const sels = [
                'input[data-field="SELECTED"][data-rowid="-99"]',   // 现场确认：表头固定 -99
                'input.gridCheckHeader',
                '.gridCheckHeader',
                'th input[data-field="SELECTED"]',
                '.k-grid-header input[data-field="SELECTED"]',
                'thead input[data-field="SELECTED"]',
                'input[data-field="SELECTED"]'
            ];
            let cb = null;
            for (const s of sels) { cb = root.querySelector(s); if (cb) break; }
            if (!cb) return 0;
            // 关键：label.click() 无效！金蝶的事件是委托绑定的，
            // 必须向 label 派发完整的 mousedown+mouseup+click 序列才会触发。
            const lab = cb.nextElementSibling;
            const target = (lab && lab.classList.contains('k-checkbox-label')) ? lab : cb;
            ['mousedown','mouseup','click'].forEach(t =>
                target.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window})));
            return 1;
        }"""
        if not eval_all_frames(self.page, js):
            raise RuntimeError("全选失败：找不到表头复选框")
        self.page.wait_for_timeout(1500)
        n = self.selected_count()
        if not n:
            raise RuntimeError("全选失败：选中数为 0")
        self.log.ok(f"全选完成，已选 {n} 行")
        return n

    def selected_count(self):
        """
        读真实选中行数。
        注意：不能用 DOM 里 :checked 的数量 —— 列表分页时只渲染当前页，
        DOM 里最多只有当前页的行。金蝶把真实选中数显示在分页栏
        div.kd-grid-pager-col（形如「已选 599 行」），以它为准。

        ⚠️ 2026-09-21 修复：分页栏也必须限定在**当前可见 grid** 内查，
           否则会读到隐藏页面（销售出库单列表）的「已选 0 行」，
           表现为「勾选明明成功、selected_count() 却永远返回 0」。
        """
        js = self._INVISIBLE + """() => {
            const match = t => { const m = (t||'').match(/已选\\s*(\\d+)\\s*行/); return m ? parseInt(m[1], 10) : null; };
            const grids = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis);
            // 1) 当前可见 grid 的分页栏
            for (const g of grids) {
                const root = g.closest('.k-grid') || g.parentElement.parentElement;
                if (!root) continue;
                for (const e of Array.from(root.querySelectorAll('[class*=pager-col]'))) {
                    const n = match((e.innerText || '').trim());
                    if (n !== null) return n;
                }
            }
            // 2) 全页面兜底（仅可见）
            for (const e of Array.from(document.querySelectorAll('[class*=pager-col]')).filter(vis)) {
                const n = match((e.innerText || '').trim());
                if (n !== null) return n;
            }
            // 3) 最后兜底：数可见 grid 里勾上的
            for (const g of grids) {
                const c = g.querySelectorAll('input.gridCheck:not(.gridCheckHeader):checked').length;
                if (c) return c;
            }
            return 0;
        }"""
        return eval_all_frames(self.page, js) or 0

    def clear_selection(self, max_clicks=4):
        """取消全部选中（只在当前可见 grid 里操作表头复选框）。

        ⚠️ 血泪坑（2026-09-21 实测修复）：表头复选框是**三态**语义 ——
             未选 / 部分选中 → 点一下变「**全选**」；全选 → 点一下才「全清」。
           老实现直接点一次就完事，于是「本来只勾了 1 行」被点成「46 行全选」，
           后续 select_rows 再勾选 → 选中状态整个乱掉（日志形如
           `已取消选择，剩余 46 行`，本该是 0）。
        ⇒ 正确做法：循环点表头并读 selected_count 直到归零（最多 max_clicks 次）。
             0 行         → 不点（点了反而全选）
             全选         → 1 次归零
             部分选中     → 2 次归零（先全选、再全清）
        ⚠️ 表头复选框状态与真实选中数会不同步，所以判据只看 selected_count()。
        """
        n = self.selected_count()
        if n == 0:
            self.log.info("当前没有选中行，无需取消")
            return True

        js = self._INVISIBLE + """() => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            const root = g ? (g.closest('.k-grid') || g.parentElement.parentElement) : document;
            const cb = root.querySelector('input[data-field="SELECTED"][data-rowid="-99"]')
                    || root.querySelector('input.gridCheckHeader')
                    || root.querySelector('.gridCheckHeader');
            if (!cb) return false;
            const lab = cb.nextElementSibling;
            const target = (lab && lab.classList.contains('k-checkbox-label')) ? lab : cb;
            ['mousedown','mouseup','click'].forEach(t =>
                target.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window})));
            return 1;
        }"""
        for k in range(1, max_clicks + 1):
            if not eval_all_frames(self.page, js):
                self.log.warn("找不到表头复选框，无法取消选择")
                return False
            self.page.wait_for_timeout(1300)
            n2 = self.selected_count()
            self.log.info(f"取消选择第 {k} 次：{n} → {n2} 行")
            if n2 == 0:
                return True
            n = n2
        self.log.warn(f"连续点表头 {max_clicks} 次仍剩 {n} 行未取消，请人工核对")
        return False

    # ---------- 列表过滤对话框（现场采集确认） ----------
    # 工具栏「过滤」-> 弹出 .k-window「列表过滤-[XXX]」
    #   Tab:      li#..._Filter-FTAB_P01_c  (条件)
    #   行操作:   LI.kdFilterAdd 新增行 / .kdFilterDelete 删除行 / .kdFilterDeleteAll 全部删除
    #   底部:     ..._Filter-FBTNCANCEL 取消 / ..._Filter-FBTNOK 确定
    #   条件行字段: [id$="_row_Add_PART_Fileds"]      kendoComboBox  -> 字段名
    #              [id$="_row_Add_PPART_CompareType"] kendoDropDownList -> 比较符代码
    #              [id$="_row_Add_PPART_VALUE_c"]     kendoDatePicker   -> 值
    #              [id$="_row_Add_PART_Logic"]        kendoDropDownList -> 并且=0
    # 账务日期 = FDate1（注意 FDate 是「日期」，别混）
    # 比较符没有「介于」-> 用两行：39(大于等于) 且 16(小于等于)
    COMPARE_GE = "39"   # 大于等于
    COMPARE_LE = "16"   # 小于等于
    FIELD_ACCT_DATE = "FDate1"   # 账务日期
    FIELD_DATE = "FDate"         # 日期

    def open_filter_dialog(self):
        """
        点工具栏「过滤」，打开列表过滤对话框。

        ★★ 2026-10-04 修正（`row out of range` 根因）：
            早先"已经开着就复用"是祸根 —— 复用的可能是**上一个单位/上一个列表**
            残留的过滤对话框（金蝶点「确定」后异步关闭，窗口会残留一小段时间）。
            复用它时：`_count_condition_rows` 数到残留窗口的旧行、`_set_condition_row`
            命中另一个空窗口 → `row out of range, have: 0`。跨单位偶发复现
            （常熟双乐/诸暨德升/杭州秉创/浙江之江金鼠都栽过）。
            ⇒ 改为「先点取消关掉残留的过滤对话框，再重新打开干净的」。
        """
        # 1) 关闭所有残留的「列表过滤」对话框（点取消，真实鼠标，确保关干净）
        js_close = """() => {
            const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
            const wins = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .filter(w => (w.innerText || '').includes('列表过滤'));
            const out = [];
            for (const w of wins) {
                let b = w.querySelector('a[id$="_Filter-FBTNCANCEL_c"]')
                     || Array.from(w.querySelectorAll('a,button')).filter(vis)
                          .find(e => (e.innerText||'').trim() === '取消');
                if (!b) continue;
                const r = b.getBoundingClientRect();
                out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
            }
            return out;
        }"""
        for pos in (eval_all_frames(self.page, js_close) or []):
            self.page.mouse.click(pos["x"], pos["y"])
            self.page.wait_for_timeout(800)
        # 等窗口真正关闭（金蝶异步关闭有 1~2s 延迟，轮询确认关干净）
        import time as _t
        _t0 = _t.time()
        while (_t.time() - _t0) < 6:
            left = eval_all_frames(
                self.page,
                "() => Array.from(document.querySelectorAll('.k-window'))"
                ".filter(w => (w.innerText||'').includes('列表过滤')"
                "        && w.getBoundingClientRect().width > 0).length") or 0
            if not left:
                break
            self.page.wait_for_timeout(400)

        # 2) 重新打开（此时应该是干净的）
        self.click_menu("过滤", timeout_ms=2500)
        # ★ 2026-10-27 修复：判定放宽 —— 不是所有过滤面板 title 都叫「列表过滤」。
        #   「凭证查询」的过滤面板 title 是「凭证查询」（含 保存/另存/复制/重置 工具栏），
        #   但同样有「确定/取消」按钮 + 新增行。原判定只认「列表过滤」会误报"没弹出"。
        ok = eval_all_frames(
            self.page,
            "() => Array.from(document.querySelectorAll('.k-window'))"
            ".filter(w => w.getBoundingClientRect().width > 0)"
            ".some(w => (w.innerText||'').includes('列表过滤')"
            "        || w.querySelector('a[id$=\"_Filter-FBTNOK_c\"]'))")
        if not ok:
            raise RuntimeError("点了「过滤」但没弹出过滤对话框")
        self.log.info("过滤对话框已打开")
        return True

    def dismiss_residual_dialogs(self):
        """
        关掉所有残留的可见弹窗（过滤对话框/处理结果/提示框等）。
        用**真实鼠标**点「取消 / 关闭」——上一个单位异常时残留的弹窗会浮在最上层，
        拦截下一个单位的点击（实测龙游金伦→杭州强立「新增行」12s 无反应就是这个原因）。
        """
        js = """() => {
            const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
            const wins = Array.from(document.querySelectorAll('.k-window')).filter(vis);
            const out = [];
            for (const w of wins) {
                // 取消 / 关闭 按钮
                let b = w.querySelector('a[id$="_Filter-FBTNCANCEL_c"]')
                     || Array.from(w.querySelectorAll('a,button,span')).filter(vis)
                          .find(e => ['取消','关闭'].includes((e.innerText||'').trim()));
                if (!b) continue;
                const r = b.getBoundingClientRect();
                out.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                          txt: (w.innerText||'').trim().replace(/\\s+/g,' ').slice(0,30)});
            }
            return out;
        }"""
        btns = eval_all_frames(self.page, js) or []
        for b in btns:
            self.page.mouse.click(b["x"], b["y"])
            self.page.wait_for_timeout(600)
            self.log.info(f"  清理残留弹窗：{b['txt']}")
        if btns:
            self.page.wait_for_timeout(800)
        return len(btns)

    def close_filter_dialog(self, ok_button=False):
        """关掉过滤对话框：ok_button=True 点确定，False 点取消。用真实鼠标点坐标。"""
        js = """(isOk) => {
            const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
            const win = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(w => (w.innerText || '').includes('列表过滤'))
                || Array.from(document.querySelectorAll('.k-window')).filter(vis)[0];
            if (!win) return false;
            let b = win.querySelector(isOk ? 'a[id$="_Filter-FBTNOK_c"]'
                                           : 'a[id$="_Filter-FBTNCANCEL_c"]');
            if (!b) {
                const t = isOk ? '确定' : '取消';
                b = Array.from(win.querySelectorAll('a[id*="FBTNOK"], a[id*="FBTNCANCEL"]'))
                     .filter(vis).find(e => (e.innerText||'').trim() === t)
                  || Array.from(win.querySelectorAll('a,button'))
                     .filter(vis).find(e => (e.innerText||'').trim() === t);
            }
            if (!b) return false;
            const r = b.getBoundingClientRect();
            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                    id: (b.id || '').slice(-30)};
        }"""
        pos = eval_all_frames(self.page, js, ok_button)
        if not pos:
            raise RuntimeError("过滤对话框里点不到「确定」")
        self.page.mouse.click(pos["x"], pos["y"])   # ★ 真实鼠标（JS click 会静默失效，弹窗残留）
        self.log.info(f"已点「{'确定' if ok_button else '取消'}」（{pos['id']}）")
        self.page.wait_for_timeout(2500)
        return True

    # ★2026-09-21 实测：`.k-window` 里**第一个可见的窗口不一定是过滤对话框**
    #   （SPA 会把关掉的窗口留在 DOM 里，顺序会变）→ 曾导致
    #   `_count_condition_rows` 返回 -1（找不到窗口无 tr）而误判成"新增行没生效"。
    #   统一口径：**按标题文本「列表过滤」定位**，退化用「含 li.kdFilterAdd 的窗口」。
    _FILTER_WIN_JS = """const vis = e => { const r = e.getBoundingClientRect();
        const s = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
    const win = Array.from(document.querySelectorAll('.k-window')).filter(vis)
        .find(w => (w.innerText || '').includes('列表过滤'))
        || Array.from(document.querySelectorAll('.k-window')).filter(vis)
        .find(w => w.querySelector('li.kdFilterAdd'));"""

    def _filter_click(self, cls, timeout_ms=6000):
        """
        点对话框里的行操作按钮（新增行/全部删除…）。

        ⚠️⚠️ **必须用真实鼠标点坐标**（2026-09-21 实测修正）：
           这些按钮是 `li.k-link.kdFilterAdd` 这类 Kendo 组件，
           JS 的 `el.click()` **完全不触发** —— 现象：`_add_condition_row` 死等 12 秒
           条件行数始终为 0（对话框看着正常，按钮也"点"了，就是不加行）。
           实测对比：`page.mouse.click(中心坐标)` → 条件行 0→1，立刻生效。
        ★2026-10-04 加固：对话框刚打开时按钮是**异步渲染**的，立即定位会找不到
           （嵊州申红第②步「找不到 kdFilterDeleteAll」）→ 轮询等到按钮出现。
        """
        import time
        js = """(c) => {
            const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
            const win = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(w => (w.innerText || '').includes('列表过滤'))
                || Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(w => w.querySelector('li.kdFilterAdd'));
            if (!win) return false;
            const el = win.querySelector('li.' + c) || win.querySelector('.' + c);
            if (!el || !vis(el)) return false;
            const r = el.getBoundingClientRect();
            return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                     cls: c };
        }"""
        t0 = time.time()
        while (time.time() - t0) * 1000 < timeout_ms:
            pos = eval_all_frames(self.page, js, cls)
            if pos:
                break
            self.page.wait_for_timeout(300)
        if not pos:
            raise RuntimeError(f"过滤对话框里找不到按钮：{cls}（等了 {timeout_ms}ms）")
        self.page.mouse.click(pos["x"], pos["y"])
        self.page.wait_for_timeout(900)

    def _count_condition_rows(self):
        """
        数「列表过滤」对话框里已有的条件行（含字段 ComboBox 的 tr）。

        ⚠️⚠️ 必须返回**对象**而不是数字（2026-09-21 踩坑）：
           本项目所有取数都走 `eval_all_frames`，它的语义是
           **「返回第一个真值结果」** —— JS 返回 `0` 时 `0` 是假值，
           会被当成"没拿到结果"继续试下一个 frame，最终返回 None。
           现象：`_add_condition_row` 死等 12 秒后报
           「仍是 -1 行」，而实际上对话框里就是 0 行（正常状态）。
           ⇒ 一律返回 `{n: …}` 这种对象，恒为真。
        """
        js = """() => {
            const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
            const win = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(w => (w.innerText || '').includes('列表过滤'))
                || Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(w => w.querySelector('li.kdFilterAdd'));
            if (!win) return {n: -1};
            return {n: Array.from(win.querySelectorAll('tr')).filter(tr =>
                tr.querySelector('[id$="_row_Add_PART_Fileds"]')
             || tr.querySelector('[id$="_ROW_ADD_PART_FIELDS"]')).length};
        }"""
        r = eval_all_frames(self.page, js)
        if isinstance(r, dict) and isinstance(r.get("n"), (int, float)):
            return int(r["n"])
        return -1


    def _add_condition_row(self, want_row_idx, timeout_ms=12000):
        """
        点「新增行」并**轮询等到第 want_row_idx 行真的渲染出来**。
        ★2026-09-21 实测（专票列表的过滤对话框）：点完「新增行」后新行是**异步**
          渲染的，`_filter_click` 内置的 900ms + settle 450ms 在对话框刚打开时
          可能不够 → `_set_condition_row(0,…)` 直接报
          `row out of range, have: 0`。所以这里必须等到「行数 > want_row_idx」。
        ★2026-10-04 加固：偶发「点一次不动」——点后 3s 行数仍不增加就**重定位重点**，
          最多 3 次（杭州强立曾「新增行 12s 无反应」而手动点立即生效）。
        """
        import time
        t0 = time.time()
        for attempt in range(1, 4):
            self._filter_click("kdFilterAdd")
            # 等 3s 看行数是否增加（否则重试点击）
            while (time.time() - t0) * 1000 < timeout_ms:
                n = self._count_condition_rows()
                if n > want_row_idx:
                    self.log.info(f"  新增行就绪：当前 {n} 行（第 {attempt} 次点击，"
                                  f"等 {(time.time()-t0)*1000:.0f}ms）")
                    return n
                if (time.time() - t0) * 1000 >= 3000 * attempt:
                    break
                self.page.wait_for_timeout(250)
            self.log.warn(f"  第 {attempt} 次点「新增行」后行数未达标，重试")
        raise RuntimeError(f"点「新增行」后等了 {timeout_ms}ms 仍是 {self._count_condition_rows()} 行")

    def _set_condition_row(self, idx, field, compare, value):
        """
        给第 idx 行条件赋值（0 起）。
        必须用 Kendo 控件 API 设值，直接改 input.value 不会触发内部状态。
        """
        # 注意：金蝶生成的 id 大小写混用 —— 字段/比较符是小写后缀，
        # 值输入框是【全大写】_ROW_ADD_PPART_VALUE_c。CSS 属性选择器大小写敏感，
        # 所以这里改为「按行容器 <tr> 定位」，行内用 data-role 找控件，不依赖 id 大小写。
        js = """(a) => {
            const [idx, field, compare, value] = a;
            const $ = window.jQuery;
            const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
            // ★ 2026-10-04 修正：窗口定位必须与 `_count_condition_rows` 完全一致，
            //   否则「数到 1 行」但「写值时报 0 行」（两函数命中不同窗口）。
            //   fallback 用 find(li.kdFilterAdd) 而不是 [0]，避免命中残留的别的弹窗。
            const win = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(w => (w.innerText || '').includes('列表过滤'))
                || Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(w => w.querySelector('li.kdFilterAdd'));
            const scope = win || document;

            // 按行定位：含字段 ComboBox 的 tr 才是条件行
            const rows = Array.from(scope.querySelectorAll('tr'))
                .filter(tr => tr.querySelector('[id$="_row_Add_PART_Fileds"]')
                           || tr.querySelector('[id$="_ROW_ADD_PART_FIELDS"]'));
            if (idx >= rows.length)
                return {err: 'row out of range', have: rows.length, want: idx};
            const row = rows[idx];

            const fld = row.querySelector('[id$="_row_Add_PART_Fileds"]')
                     || row.querySelector('input[data-role=combobox]');
            const cmp = row.querySelector('[id$="_row_Add_PPART_CompareType"]')
                     || row.querySelector('input[data-role=dropdownlist]');
            // ⚠️ 值单元格必须**先按 id 找**（id 含 ppart_value，大小写混用 → 转小写比对）：
            //    按 data-role 找会先命中【字段】那个 combobox（行内第一个 combobox 就是字段列），
            //    实测（2026-09-21）导致客户/日期条件被写到字段控件上、过滤静默不生效。
            const findVal = () => {
                const all = Array.from(row.querySelectorAll('input'));
                const byId = all.filter(i => (i.id || '').toLowerCase().includes('ppart_value'));
                const hit = byId.find(i => i.offsetParent !== null) || byId[0];
                if (hit) return hit;
                return all.find(i => i !== fld && i !== cmp
                    && ['datepicker','datetimepicker','combobox','dropdownlist','text']
                        .includes(i.getAttribute('data-role')));
            };
            const isDate = x => !!($(x).data('kendoDatePicker') || $(x).data('kendoDateTimePicker'));
            // ★ 字段切换后值单元格会**异步重建**。实测（2026-09-21）：若在重建完成前
            //   抓值控件，会抓到上一轮遗留的 DatePicker，值被写到日期控件上 →
            //   该条件静默丢失（日志里表现为 how=datePicker，明明是文本字段）。
            //   ⇒ 对非日期字段，必须等到抓到「非日期」控件才写值。
            const wantDate = /date/i.test(field);
            if (!fld || !cmp) return {err: 'row cells not found', hasFld: !!fld, hasCmp: !!cmp};

            // 1) 字段
            const fw = $(fld).data('kendoComboBox') || $(fld).data('kendoDropDownList');
            if (fw) { fw.value(field); fw.trigger('change'); }
            else { fld.value = field; $(fld).trigger('change'); }

            return new Promise(res => setTimeout(() => {
                // 2) 比较符
                const cw = $(cmp).data('kendoDropDownList') || $(cmp).data('kendoComboBox');
                if (cw) { cw.value(compare); cw.trigger('change'); }
                else { cmp.value = compare; $(cmp).trigger('change'); }

                setTimeout(() => {
                    // 3) 值：字段切换后控件可能重建 —— 轮询等到「类型正确」的值控件再写，
                    //    否则会写到遗留的日期控件上（该条件静默丢失）。
                    const attempt = (n) => {
                        const v = findVal();
                        if (n < 5 && (!v || (wantDate !== isDate(v)))) {
                            return setTimeout(() => attempt(n + 1), 450);
                        }
                        if (!v) { res({err: 'no value cell', rowHtml: row.innerHTML.slice(0,200)}); return; }
                        let how = 'raw';
                        const dw = $(v).data('kendoDatePicker');
                        const dtw = $(v).data('kendoDateTimePicker');
                        const cbw = $(v).data('kendoComboBox');
                        const ddw = $(v).data('kendoDropDownList');
                        if (dw) { dw.value(new Date(String(value).replace(/-/g,'/'))); dw.trigger('change'); how='datePicker'; }
                        else if (dtw) { dtw.value(new Date(String(value).replace(/-/g,'/'))); dtw.trigger('change'); how='dateTimePicker'; }
                        else if (cbw) { cbw.value(value); cbw.trigger('change'); how='comboBox'; }
                        else if (ddw) { ddw.value(value); ddw.trigger('change'); how='dropDownList'; }
                        else {
                            v.value = value;
                            ['input','change','keydown','keyup'].forEach(t =>
                                v.dispatchEvent(new Event(t, {bubbles:true})));
                            v.dispatchEvent(new KeyboardEvent('keydown',
                                {key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
                        }
                        res({ok: true, how, tries: n, field, compare, value,
                             valId: (v.id||'').slice(-28)});
                    };
                    attempt(0);
                }, 600);
            }, 700));
        }"""
        # ★ 2026-10-04 加固：`row out of range` 是「新增行异步渲染未稳定」的竞态
        #   （`_count_condition_rows` 数到 1 行的瞬间，写值时那行 DOM 还没 attach 完）。
        #   碰到 `row out of range` 就等 600ms 重抓，最多 3 次。
        r = eval_all_frames(self.page, js, [idx, field, compare, str(value)])
        for _retry in range(3):
            if r and r.get("err") == "row out of range":
                self.log.warn(f"  条件行{idx} 写值时行未就绪（{r}），等 600ms 重试")
                self.page.wait_for_timeout(600)
                r = eval_all_frames(self.page, js, [idx, field, compare, str(value)])
            else:
                break
        if not r or r.get("err"):
            raise RuntimeError(f"设置第 {idx} 行条件失败：{r}")
        self.log.info(f"  条件行{idx}: 字段={field} 比较符={compare} 值={value} [{r.get('how')}]")
        return True

    def _read_condition_rows(self):
        """回读「列表过滤」对话框里每个条件行的 (字段, 比较符, 值)，用于写入后自检。

        ⚠️ 值输入框的 id 是**全大写** `_ROW_ADD_PPART_VALUE_c`：用
           `[id$="_row_Add_PPART_VALUE_c"]` 这种 CSS 属性选择器（大小写敏感）
           根本命中不到，会误判成"值没写进去"。所以统一按 `ppart_value`
           转小写查找，与 `_set_condition_row` 的 findVal 保持同一口径。
        """
        js = """() => {
            const $ = window.jQuery;
            const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
            const win = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(w => (w.innerText || '').includes('列表过滤'))
                || Array.from(document.querySelectorAll('.k-window')).filter(vis)[0];
            const scope = win || document;
            const rows = Array.from(scope.querySelectorAll('tr'))
                .filter(tr => tr.querySelector('[id$="_row_Add_PART_Fileds"]')
                           || tr.querySelector('[id$="_ROW_ADD_PART_FIELDS"]'));
            const val = x => {
                if (!x) return null;
                const w = $(x).data('kendoDatePicker') || $(x).data('kendoDateTimePicker')
                       || $(x).data('kendoComboBox') || $(x).data('kendoDropDownList')
                       || $(x).data('kendoTextBox');
                if (w && typeof w.value === 'function') return String(w.value());
                return String(x.value);
            };
            return rows.map((row, i) => {
                const fld = row.querySelector('[id$="_row_Add_PART_Fileds"]')
                         || row.querySelector('input[data-role=combobox]');
                const cmp = row.querySelector('[id$="_row_Add_PPART_CompareType"]')
                         || row.querySelector('input[data-role=dropdownlist]');
                const all = Array.from(row.querySelectorAll('input'));
                const byId = all.filter(x => (x.id || '').toLowerCase().includes('ppart_value'));
                const v = byId.find(x => x.offsetParent !== null) || byId[0];
                const lg = row.querySelector('[id$="_row_Add_PART_Logic"]');
                return { i, field: val(fld), cmp: val(cmp), value: val(v), logic: val(lg) };
            });
        }"""
        return eval_all_frames(self.page, js) or []

    def set_bill_no_filter(self, bill_nos, field="FBillNo", settle_ms=450, verify=True):
        """
        用「单据编号 包含」+ **多行「或者」**条件，把列表精确筛成指定的几张单。

        为什么需要（2026-09-21 实测广源包装集团那批）：列表是**虚拟滚动**，
        tbody 永远只渲染 200 行（1067 行数据只看得见前 200 行），
        跨页勾选不可靠；而**客户字段是 bosf7field（基础资料选择器），
        值控件不是普通 input，塞文本进去静默无效** —— 所以只能绕到「单据编号」。

        ⚠️ 单据编号 FBillNo 的 Editor.xtype = **textfield**，值能直接写（这是关键区别）。
        ⚠️ 行间逻辑列 _row_Add_PART_Logic：value「0」=并且 /「1」=或者。
           **每一行都要设成「或者」——包括最后一行**。
           2026-09-21 二次实测更正：早先记的"最后一行不用设（它后面没行了）"是**错的**。
           对话框底部常驻一行空白行，最后一行条件的逻辑若不设，它就变成
           「… AND (空白行)」，**该条件被静默吃掉**——现象：4 个或者条件里
           最后一张单永远筛不出来（实际只命中 3 张），而回读每个条件的
           字段/比较符/值全都"正常"，极难查。

        ⚠️⚠️ 时序坑（2026-09-21 实测修复）：**最后一行条件写完值不能立刻点确定**。
           金蝶是在 input 事件之后**异步**把条件 commit 进内部模型的；紧接着点确定，
           那一行的条件会被**静默丢掉** —— 现象是「4 张单只筛出 3 张」，
           而日志里每一行都显示"设置成功"，完全看不出问题（最难查的那种）。
           前面的行之所以没事，只是因为后面还有"设逻辑 / 加下一行"的操作，
           顺带等到了 commit 时间。
           ⇒ 修法：新增行后等 settle_ms、点确定前再等一次，并**回读自检**。

        返回过滤后的行数。
        """
        bill_nos = [str(b).strip() for b in bill_nos if str(b).strip()]
        if not bill_nos:
            raise ValueError("set_bill_no_filter 需要至少一个单号")
        self.log.info(f"按单据编号过滤 {len(bill_nos)} 张：{bill_nos}")
        # ★ 先清残留弹窗：上个单位异常/未关干净的过滤对话框会浮在最上层，
        #   让本单位的「新增行」点击失效（实测 12s 无反应）。
        self.dismiss_residual_dialogs()
        self.open_filter_dialog()
        self._filter_click("kdFilterDeleteAll")
        self.page.wait_for_timeout(settle_ms)
        for i, no in enumerate(bill_nos):
            self._add_condition_row(i)
            # ★ 等新行渲染出来再写值，否则可能写到上一行 / 不存在的行上
            self.page.wait_for_timeout(settle_ms)
            self._set_condition_row(i, field, "17", no)   # 17 = 包含
            # ★ 每一行（含最后一行）都设「或者」——最后一行不设会被底部空白行吃掉，
            #   导致"最后一张单永远筛不出来"，见 docstring 的实测说明。
            js = self._INVISIBLE + """(a) => {
                    const [i, v] = a;
                    const $ = window.jQuery;
                    const w0 = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                        .find(w => (w.innerText||'').includes('列表过滤'))
                        || Array.from(document.querySelectorAll('.k-window')).filter(vis)
                        .find(w => w.querySelector('li.kdFilterAdd'));
                    const rows = Array.from(w0.querySelectorAll('tr'))
                        .filter(tr => tr.querySelector('[id$="_row_Add_PART_Fileds"]'));
                    const el = rows[i] && rows[i].querySelector('[id$="_row_Add_PART_Logic"]');
                    if (!el) return null;
                    const w = $(el).data('kendoDropDownList');
                    if (!w) return null;
                    w.value(v); w.trigger('change');
                    return w.text();
                }"""
            got = eval_all_frames(self.page, js, [i, "1"])
            self.log.info(f"  行{i} 逻辑 -> {got}")
            if got != "或者":
                raise RuntimeError(f"第 {i} 行逻辑没设成「或者」（当前 {got!r}）")

        # ★ 点确定前留出 commit 时间，并回读自检（专防"最后一行静默丢失"）
        self.page.wait_for_timeout(1200)
        if verify:
            # 自愈：回读 → 哪一行没写进去就重写哪一行（最多 3 轮）。
            # 实测（2026-09-21）会出现**随机某一行**静默丢失（行0 的 how 变成了
            # datePicker —— 值被写到遗留的日期控件上），一次写完就点确定必翻车。
            got, vals = [], []
            for attempt in range(1, 4):
                got = self._read_condition_rows()
                # ⚠️ 金蝶过滤对话框底部常驻**一行空白行**（值读出来是 ''/'null'），
                #    它不算条件。4 个条件 + 1 空白行 → 自检会误报 5 行。
                got = [r for r in got
                       if str(r.get("value") or "").strip().lower()
                       not in ("", "null", "none")]
                vals = [str(r.get("value") or "") for r in got]
                miss = [n for n in bill_nos if not any(n in v for v in vals)]
                if not miss and len(got) == len(bill_nos):
                    break
                self.log.warn(f"[第 {attempt} 轮回读] 缺 {miss}（当前 {len(got)} 行）→ 重写该行")
                for i, no in enumerate(bill_nos):
                    if no in miss:
                        self._set_condition_row(i, field, "17", no)
                        self.page.wait_for_timeout(settle_ms)
                self.page.wait_for_timeout(1200)
            if miss or len(got) != len(bill_nos):
                raise RuntimeError(
                    f"过滤条件自检不通过：期望 {len(bill_nos)} 行 {bill_nos}，"
                    f"对话框实际 {len(got)} 行 -> {vals}；缺 {miss}")
            self.log.ok(f"过滤条件自检通过：{len(got)} 行条件全部就位")

        self.close_filter_dialog(ok_button=True)
        self.page.wait_for_timeout(3500)
        return len(self.grid_all_rows() or [])

    def set_date_filter(self, start, end, field=None):
        """
        用「列表过滤」对话框设日期区间：field >= start 且 field <= end
        field 默认 FDate1（账务日期）；传 FDate 则按「日期」过滤。
        """
        field = field or self.FIELD_ACCT_DATE
        self.log.info(f"设置日期过滤：{field} 介于 {start} ~ {end}")

        self.open_filter_dialog()
        # 先清空已有条件，避免和旧条件叠加
        self._filter_click("kdFilterDeleteAll")
        self.page.wait_for_timeout(450)
        # 行0: >= start
        self._filter_click("kdFilterAdd")
        self.page.wait_for_timeout(450)
        self._set_condition_row(0, field, self.COMPARE_GE, start)
        # 行1: <= end（同一行的「并且」默认就是 0）
        self._filter_click("kdFilterAdd")
        self.page.wait_for_timeout(450)
        self._set_condition_row(1, field, self.COMPARE_LE, end)
        # ★ 同 set_bill_no_filter：末行写完不能立刻确定，否则该条件会被静默丢掉
        self.page.wait_for_timeout(1200)

        # 确定（点 a[id$=_Filter-FBTNOK_c]，不是外层 DIV）
        self.close_filter_dialog(ok_button=True)
        self.log.ok(f"日期过滤已应用：{field} {start} ~ {end}")
        return True

    def select_rows(self, bill_nos, settle_ms=320):
        """
        按单号勾选指定行（该单号下的**全部明细行**都会被勾上）。
        bill_nos 为空则退化为全选。

        ⚠️ 血泪坑 1（2026-09-21 修复）：老实现直接扫
           `document.querySelectorAll('tbody tr')` —— 命中的是 SPA 里**隐藏页面**
           的列表（金蝶把开过的页面 DOM 全留着）。结果：复选框确实点了 46 个，
           但全在隐藏表格里，页面上「真实选中 0」，下推时自然推不出东西。
           ⇒ 凡扫 DOM 必先过可见性 + 限定当前可见 grid。
        ⚠️ 血泪坑 2（2026-09-21 修复）：行复选框的 HTML 是
             `<input type="checkbox" onclick="return false;" class="k-checkbox gridCheck gridCheckCell">`
           金蝶在**祖先容器**上做事件委托，而 `onclick="return false"` 又把默认切换挡了，
           所以派发合成 MouseEvent **勾不上、还不报错**（静默失败最难查）。
           ⇒ 必须 `page.mouse.click(x, y)` 真实鼠标点坐标
             （与下推对话框「确定」按钮、凭证生成按钮是同一个坑）。
        """
        if not bill_nos:
            return self.select_all_rows()

        find_js = self._INVISIBLE + """(nos) => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return null;
            const idx = [];
            Array.from(g.querySelectorAll('tbody tr')).forEach((tr, i) => {
                // 优先只看单据号列，避免物料名/备注里恰好含同样字符串造成误勾
                const cell = tr.querySelector('[data-field="FBILLNO"]');
                const key = cell ? (cell.innerText || '') : (tr.innerText || '');
                if (nos.some(n => n && key.includes(n))) idx.push(i);
            });
            return idx;
        }"""
        idxs = eval_all_frames(self.page, find_js, list(bill_nos))
        if idxs is None:
            raise RuntimeError("找不到可见的列表网格，无法勾选")
        if not idxs:
            raise RuntimeError(f"没有匹配到任何行：{bill_nos}")

        pos_js = self._INVISIBLE + """(i) => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            const tr = Array.from(g.querySelectorAll('tbody tr'))[i];
            if (!tr) return {err: 'no row ' + i};
            const cb = tr.querySelector('input.gridCheck');
            if (!cb) return {err: 'no checkbox on row ' + i};
            cb.scrollIntoView({block: 'nearest'});
            const r = cb.getBoundingClientRect();
            const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
            const top = document.elementFromPoint(x, y);
            const onCb = top && (top === cb || top.closest('.kd-grid-selector') || top.closest('td'));
            return {x, y, checked: !!cb.checked, blocked: !onCb,
                    top: top ? (top.tagName + '.' + String(top.className || '').slice(0, 40)) : null};
        }"""

        clicked = already = 0
        for i in idxs:
            if not eval_all_frames(self.page, pos_js, [i]):
                continue
            info = eval_all_frames(self.page, pos_js, [i])
            if not isinstance(info, dict) or info.get("err"):
                self.log.warn(f"第 {i} 行定位失败：{info}")
                continue
            if info.get("checked"):
                already += 1
                continue
            if info.get("blocked"):
                self.log.warn(f"第 {i} 行复选框被遮挡（命中 {info.get('top')}），仍尝试点击")
            self.page.mouse.click(info["x"], info["y"])
            self.page.wait_for_timeout(settle_ms)
            clicked += 1

        self.page.wait_for_timeout(900)
        real = self.selected_count()
        self.log.info(f"按单号勾选：命中 {len(idxs)} 行（新勾 {clicked}、原已勾 {already}）"
                      f"，真实选中 {real} 行")
        if real is None or real < len(idxs):
            self.log.warn(f"真实选中数（{real}）少于命中行数（{len(idxs)}），"
                          f"请核对是否被虚拟滚动/分页截断")
        return len(idxs)

    # ---------- 菜单按钮（已验证） ----------
    def _menu_button_id(self, text):
        """
        找工具栏菜单项**当前可见**的那个按钮的 id。
        ⚠️ 血泪坑（2026-09-18）：金蝶 SPA 里同时挂着多个 pageId 的工具栏（列表页/详情页/其他标签页），
           隐藏页面的「保存/提交」按钮 DOM 一直在。
           若只按文本匹配取第一个 → 命中的是别的页面隐藏按钮，点击静默无效、也不报错。
           实测：只有当前页(a60ea65a)的 tbSave/tbSubmit/tbApprove 是 vis=True，
                 其余 pageId 的全部 vis=False 且 offsetParent=null。
        """
        js = self._INVISIBLE + """(t) => {
            const cands = Array.from(document.querySelectorAll('.kdmenuitem-content'))
                .filter(e => (e.textContent||'').trim() === t && vis(e) && e.offsetParent !== null);
            if (!cands.length) return null;
            // 优先单据工具栏，避免点到筛选栏的「保存方案」
            const bill = cands.find(e => e.id.includes('BILLMENU_TOOLBAR'));
            const btn = bill || cands[0];
            return { id: btn.id, x: Math.round(btn.getBoundingClientRect().x),
                     y: Math.round(btn.getBoundingClientRect().y) };
        }"""
        return eval_all_frames(self.page, js, text)

    def click_menu(self, text, timeout_ms=1500):
        """
        点工具栏菜单项：保存 / 提交 / 审核 / 下推 …
        两步：先在可见按钮里定位 id → 再用真实鼠标点击（合成 click 对部分按钮无效）
        """
        info = self._menu_button_id(text)
        if not info:
            raise RuntimeError(f"找不到可见的菜单项：{text!r}")
        bid = info["id"]
        ok_real = False
        try:
            loc = self.page.locator(f'[id="{bid}"]').first
            loc.click(timeout=5000)
            ok_real = True
        except Exception as e:
            self.log.warn(f"真实点击失败（{type(e).__name__}），退回 JS click")
        if not ok_real:
            js = """(i) => { const el = document.getElementById(i);
                              if (!el) return false;
                              el.scrollIntoView({block:'center'});
                              el.click(); return true; }"""
            if not eval_all_frames(self.page, js, bid):
                raise RuntimeError(f"菜单项点击失败：{text!r} ({bid})")
        self.log.info(f"点击菜单：{text}（{'真实鼠标' if ok_real else 'JS'}，id={bid.split('-')[-1]}）")
        self.page.wait_for_timeout(timeout_ms)
        return True

    def click_confirm(self, timeout_ms=800, dialog_kw=None):
        """
        点弹窗「确定」。

        ⚠️ 必须用**真实鼠标点坐标**（2026-09-21 实测踩坑）：
           金蝶的按钮是 Kendo/kd 组件，用 `el.dispatchEvent(new MouseEvent('click'))`
           **完全不触发** —— 出库单下推对话框点了「确定」界面毫无反应、对话框不关，
           换成 `page.mouse.click(x, y)` 立刻生效。
        dialog_kw: 用对话框 innerText 限定，防点到隐藏页面里的同名按钮
                   （下推对话框用 '整单转换'）
        """
        js = """(kw) => {
            const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
            const wins = Array.from(document.querySelectorAll('.k-window')).filter(vis);
            // 优先按关键字命中的对话框；命中不到则退回最上层可见窗口（避免误扫整篇文档）
            const win = (kw ? wins.find(w => (w.innerText || '').includes(kw)) : null) || wins[0];
            const scope = win || document;
            const b = Array.from(scope.querySelectorAll('a,button')).filter(vis)
                .find(e => (e.innerText || '').trim() === '确定');
            if (!b) return null;
            const r = b.getBoundingClientRect();
            return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
                     id: (b.id || '').slice(-26) };
        }"""
        pos = eval_all_frames(self.page, js, dialog_kw)
        if not pos:
            raise RuntimeError(f"找不到可见的「确定」按钮（对话框关键字={dialog_kw!r}）")
        self.page.mouse.click(pos["x"], pos["y"])
        self.log.info(f"点击确定（真实鼠标 {pos['x']},{pos['y']} id=…{pos.get('id')}）")
        self.page.wait_for_timeout(timeout_ms)
        return True

    def choose_push_target(self, target_text):
        """
        在下推对话框里选目标单据类型。
        例：'应收单' / '销售增值税专用发票' / '销售增值税专用发票(分录合并)'
        """
        js = """(t) => {
            const labels = Array.from(document.querySelectorAll('label'))
                .filter(l => (l.textContent||'').includes(t));
            if (!labels.length) return false;
            const target = labels.find(l => l.classList.contains('k-radio-label')) || labels[0];
            target.scrollIntoView({block:'center'});
            target.click();
            target.dispatchEvent(new MouseEvent('click', {bubbles:true}));
            return target.textContent.trim();
        }"""
        got = eval_all_frames(self.page, js, target_text)
        if not got:
            raise RuntimeError(f"下推目标里找不到：{target_text!r}")
        self.log.info(f"选择下推目标：{got}")
        self.page.wait_for_timeout(500)
        return True

    # ---------- 步骤 1：下推应收单 ----------
    def push_to_receivable(self, merge=True):
        self.click_menu("下推")
        self.choose_push_target("应收单")
        # ⚠️ 下推对话框的「确定」必须真实鼠标点（合成 click 无效，见 click_confirm）
        self.click_confirm(dialog_kw="整单转换")

    def fill_receivable_fields(self, row):
        """补填应收单字段（立账类型等）—— 字段名未采集，用标签定位兜底"""
        self.log.warn("fill_receivable_fields 未实现（立账类型口径待确认）")
        return False

    def _check_save_error(self, wait_ms=2500):
        """点保存后检测是否弹了「必录/平衡」校验报错弹窗。

        ★ 2026-10-03：以前 save_bill 点完「保存」就完事，**从不看结果**。
        金蝶弹校验报错（如「第一行分录 物料必录」「明细价税合计本位币汇总≠表头」）
        时，脚本照样往下走 → 报错弹窗一直浮在屏上、读单号读不到就去反查，
        **账没保存成功却假装成功了**（SF-57）。这里把「点保存」改成「点保存 + 看结果」。
        返回报错文本（None = 没报错）。
        """
        self.page.wait_for_timeout(wait_ms)
        js = self._INVISIBLE + """() => {
            const pat = /(必录|不能为空|价税合计|汇总|不等于|不相等|不允许|未通过|校验|错误|失败|必须|无效)/;
            const wins = Array.from(document.querySelectorAll('.k-window')).filter(vis);
            const hit = wins.filter(w => pat.test(w.innerText || ''));
            if (hit.length) return hit.map(w => (w.innerText || '').replace(/\\s+/g, ' ').trim()).join(' || ');
            // 金蝶校验失败有时不是 .k-window，而是浮动提示条 / messagebox
            const box = Array.from(document.querySelectorAll(
                '.kd-messagebox, .kd-el-popmessage, [class*="message"], [class*="toast"], .k-notification'))
                .filter(vis).filter(e => pat.test(e.innerText || ''));
            if (box.length) return box.map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim()).join(' || ');
            return null;
        }"""
        try:
            return eval_all_frames(self.page, js)
        except Exception:  # noqa: BLE001
            return None

    def save_bill(self, check_error=True):
        self.click_menu("保存", timeout_ms=2500)
        if check_error:
            err = self._check_save_error()
            if err:
                raise RuntimeError(
                    f"保存后出现校验报错弹窗（单据未成功保存）：{err}  "
                    f"—— 常见根因：明细含空行（物料未带出）导致「物料必录 + 价税合计汇总≠表头」。"
                    f"请人工检查明细首行/中间是否有空行。")

    def submit_bill(self):
        self.guard.check("submit", "提交后单据进入审批流，不可随意修改")
        self.click_menu("提交", timeout_ms=2500)

    def audit_bill(self):
        self.guard.check("audit", "审核后单据生效，下游可下推")
        self.click_menu("审核", timeout_ms=2500)

    # ---------- 步骤 2：应收单下推增值税专用发票 ----------
    # 实测（2026-09-18）「选择单据」对话框：
    #   目标单据 = radio label，id 后缀 -{FormId}-EDITOR
    #     应收单 AR_RECEIVABLE / 销售普通发票 IV_SALESOC /
    #     ★销售增值税专用发票 IV_SALESIC / 销项费用普通发票 IV_SALEEXINV /
    #     销项费用增值税发票 IV_SALEEXVATIN / 收款单 AR_RECEIVEBILL /
    #     收款退款单 AR_REFUNDBILL / 付款申请单 CN_PAYAPPLY
    #   【分录合并不在 radio 上】，而在「转换规则」下拉里：
    #     应收单 - 销售专用发票 - 分录合并      <-- merge=True
    #     应收单 - 销售专用发票 - 分录不合并    <-- 默认
    #   确定/取消/转换测试 = a[id$="_FBTNOK_c"] / FBTNCANCEL_c / FBTNTEST_c
    # ⚠️ 下拉列表只在同一次 evaluate 里存在，下次调用已关闭
    #    → 点开下拉、选选项、点确定必须在【同一个 JS】里一次做完
    #   ★2026-09-21 实测修正（原实现错了两处）：
    #     1) 转换规则控件是 **[id$="-FCONVERTRULE-EDITOR"]**（FSCHEMELIST 是另一个隐藏字段）
    #        对话框字段：FCONVERTRULE=转换规则  FBILLTYPE=单据类型（只读，跟着 radio 走）
    #     2) 用合成 MouseEvent 点 .k-dropdown-wrap **打不开下拉**（Kendo 认真实输入）
    #        → 改成 Kendo API：w.select(idx) + w.trigger('change')
    #        （radio 切换后下拉会异步重载，实测要等 ~2.5s 数据才就位）
    #     实测下拉数据源（选「销售增值税专用发票」后）恰好两项，文本与本类常量完全一致：
    #        ["应收单 - 销售专用发票 - 分录合并", "应收单 - 销售专用发票 - 分录不合并"]
    TARGET_INVOICE = "IV_SALESIC"
    RULE_MERGE = "应收单 - 销售专用发票 - 分录合并"
    RULE_NO_MERGE = "应收单 - 销售专用发票 - 分录不合并"

    def push_to_invoice(self, merge=True, click_ok=True):
        """
        点「下推」→ 选「销售增值税专用发票」→ 在转换规则下拉选合并/不合并 → (可选)确定。
        merge=True  -> 「应收单 - 销售专用发票 - 分录合并」（用户已确认用这个）
        merge=False -> 「分录不合并」
        click_ok=False 时停在对话框不点确定（先看结构用）
        """
        want = self.RULE_MERGE if merge else self.RULE_NO_MERGE
        js = self._INVISIBLE + """(a) => {
            const [targetId, wantRule] = a;
            const fire = (el, ts) => ts.forEach(t =>
                el.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window})));
            const menus = Array.from(document.querySelectorAll('.kdmenuitem-content')).filter(vis);
            const btn = menus.find(e => (e.innerText||'').trim() === '下推');
            if (!btn) return {err:'no push button'};
            fire(btn, ['mouseover','mouseenter','mousemove','mousedown','mouseup','click']);

            return new Promise(res => setTimeout(() => {
                const win = Array.from(document.querySelectorAll('.k-window')).filter(vis)[0];
                if (!win) return res({err:'no dialog after clicking 下推'});
                // 1) 选目标单据 radio
                const lab = Array.from(win.querySelectorAll('label.k-radio-label')).filter(vis)
                    .find(e => (e.id||'').includes('-' + targetId + '-EDITOR'));
                if (!lab) return res({err:'no radio for ' + targetId,
                    have: Array.from(win.querySelectorAll('label.k-radio-label')).map(e=>e.id.slice(-22))});
                fire(lab, ['mouseover','mousedown','mouseup','click']);

                // 2) 等转换规则下拉异步重载，再用 Kendo API 选中目标规则
                setTimeout(() => {
                    const box = win.querySelector('[id$="-FCONVERTRULE-EDITOR"]');
                    if (!box) return res({err:'no FCONVERTRULE-EDITOR'});
                    const $ = window.jQuery;
                    const inp = box.querySelector('input[data-role=dropdownlist]');
                    const w = ($ && inp) ? $(inp).data('kendoDropDownList') : null;
                    if (!w) return res({err:'no kendo widget on FCONVERTRULE'});
                    const view = w.dataSource.view();
                    const opts = view.map(d => d.name || d.value);
                    let idx = -1;
                    view.forEach((d, i) => { if ((d.name || d.value) === wantRule) idx = i; });
                    if (idx < 0) return res({err:'rule not found', want: wantRule, opts: opts});
                    w.select(idx);
                    w.trigger('change');
                    const applied = w.text();

                    setTimeout(() => {
                        // ⚠️ 确定按钮**不走合成事件**（无效）。JS 只负责选规则；
                        // 点确定由 Python 侧 click_confirm 用真实鼠标完成。
                        res({ok:true, picked: applied, clickedOk:false, opts: opts});
                    }, 800);
                }, 2800);
            }, 1800));
        }"""
        r = eval_all_frames(self.page, js, [self.TARGET_INVOICE, want, False])
        if not isinstance(r, dict) or r.get("err"):
            raise RuntimeError(f"下推销售增值税专用发票失败：{r}")
        if r.get("picked") != want:
            raise RuntimeError(f"转换规则没选上：期望 {want!r}，实际 {r.get('picked')!r}；可选 {r.get('opts')}")
        self.log.info(f"下推目标=销售增值税专用发票，转换规则已确认={r.get('picked')}")
        if click_ok:
            # 真实鼠标点对话框「确定」（对话框含规则文本，用 '销售专用发票' 限定）
            self.click_confirm(dialog_kw="销售专用发票")
            self.page.wait_for_timeout(3500)
            self.log.info("已点确定（真实鼠标），等待专票单据生成")
        else:
            self.log.info("未确定，停在对话框")
        return True

    def read_push_dialog(self):
        """
        读「选择单据」下推对话框的当前状态。
        关键控件（2026-09-21 实测）：FCONVERTRULE=转换规则下拉  FBILLTYPE=单据类型（只读）
        """
        js = self._INVISIBLE + """() => {
            const win = Array.from(document.querySelectorAll('.k-window')).filter(vis)[0];
            if (!win) return {err: 'no dialog'};
            const $ = window.jQuery;
            const readDD = (field) => {
                const box = win.querySelector('[id$="-' + field + '-EDITOR"]');
                if (!box) return null;
                const inp = box.querySelector('input[data-role=dropdownlist]');
                const w = ($ && inp) ? $(inp).data('kendoDropDownList') : null;
                if (!w) return { raw: (box.innerText||'').trim() };
                let opts = [];
                try { opts = w.dataSource.view().map(d => d.name || d.value); } catch (e) {}
                return { text: w.text(), value: w.value(), opts: opts, visible: vis(box) };
            };
            const radios = Array.from(win.querySelectorAll('label.k-radio-label')).filter(vis)
                .map(e => {
                    const prev = e.previousElementSibling;
                    const inp = (prev && prev.tagName === 'INPUT') ? prev
                              : (e.htmlFor ? win.querySelector('#' + CSS.escape(e.htmlFor)) : null);
                    return { target: (e.id||'').split('-').slice(-2)[0],
                             text: (e.innerText||'').trim().slice(0, 40),
                             checked: !!(inp && inp.checked) };
                });
            return { radios, convertRule: readDD('FCONVERTRULE'), billType: readDD('FBILLTYPE'),
                     text: (win.innerText||'').trim().replace(/\\s+/g,' ').slice(0, 300) };
        }"""
        return eval_all_frames(self.page, js)

    def click_push_cancel(self):
        """点下推对话框的「取消」"""
        js = self._INVISIBLE + """() => {
            const win = Array.from(document.querySelectorAll('.k-window')).filter(vis)[0];
            if (!win) return false;
            let b = win.querySelector('[id*="FBTNCANCEL_c"]');
            if (!b) b = Array.from(win.querySelectorAll('a,button')).filter(vis)
                          .find(e => (e.innerText||'').trim() === '取消');
            if (!b) return false;
            b.scrollIntoView({block:'center'});
            b.click();
            return true;
        }"""
        ok = eval_all_frames(self.page, js)
        if not ok:
            self.log.warn("没找到取消按钮，可能对话框已关闭")
        else:
            self.log.info("已点「取消」，对话框关闭")
        self.page.wait_for_timeout(1200)
        return bool(ok)

    # 发票单据字段（实测销售增值税专用发票 - 新增）    #   注意：发票明细的价税合计是 FALLAMOUNTFOR / FALLAMOUNT，
    #         应收单是 FALLAMOUNTFOR_D ——【两者不同，别混用】
    INV_GRID_SUFFIX = "-FENTRYPAGE_FSITEM"
    INV_AMOUNT_FIELD = "FALLAMOUNTFOR"

    def invoice_amount_total(self):
        """读发票明细网格的价税合计合计"""
        return self.grid_amount_total(self.INV_GRID_SUFFIX, self.INV_AMOUNT_FIELD)

    def fill_field_by_label(self, label_text, value):
        """
        通用字段填写：按左侧标签文本找同行/同容器的输入框。
        金蝶表单是 label + input 结构，字段名(id)未知时靠这个兜底。
        """
        js = """(a) => {
            const [labelText, val] = a;
            const labs = Array.from(document.querySelectorAll('label, .kd-field-label, th, .k-header'))
                .filter(l => (l.textContent||'').trim().replace(/[:：]/g,'') === labelText
                          || (l.textContent||'').includes(labelText));
            for (const lab of labs) {
                let box = lab.closest('td')?.parentElement
                       || lab.closest('.kd-field') || lab.parentElement?.parentElement
                       || lab.parentElement;
                if (!box) continue;
                let inp = box.querySelector('input[data-role="datepicker"], input:not([type="checkbox"]), textarea');
                if (!inp) {
                    // 退而求其次：找 label 后面最近的 input
                    inp = lab.parentElement?.querySelector('input:not([type="checkbox"])');
                }
                if (!inp) continue;
                inp.click(); inp.focus();
                inp.value = val;
                ['input','change','keydown','keyup','blur'].forEach(t =>
                    inp.dispatchEvent(new Event(t,{bubbles:true})));
                inp.dispatchEvent(new KeyboardEvent('keydown',
                    {key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));
                return true;
            }
            return false;
        }"""
        ok = eval_all_frames(self.page, js, [label_text, str(value)])
        if not ok:
            raise RuntimeError(f"找不到字段：{label_text!r}")
        self.log.info(f"填写 {label_text} = {value}")
        self.page.wait_for_timeout(300)
        return True

    # ---------- 单据编辑视图：按字段名精确定位 ----------
    # 金蝶 8.2 表单控件 id 规律：{pageId}-{FIELD}-EDITOR_c
    # 实测（应收单-新增）：
    #   单据编号 FBILLNO(kdtextfield)  业务日期 FDATE(datepicker)  到期日 FENDDATE_H(datepicker)
    #   备注 FAR_REMARK(kdtextarea)    明细网格 gridId 后缀 -FENTITYDETAIL-c
    #   明细列：序号FSEQ 物料FMATERIALID 数量FPRICEQTY 含税单价FTAXPRICE 税率FENTRYTAXRATE
    #          不含税金额FNOTAXAMOUNTFOR_D 税额FTAXAMOUNTFOR_D 价税合计FALLAMOUNTFOR_D
    # （可见性过滤片段 _INVISIBLE 已上移到类顶部，方便所有方法复用）

    def _visible_editors(self, field):
        """找当前可见表单里某个字段的控件（SPA 多标签，必须过滤隐藏 DOM）"""
        js = self._INVISIBLE + """(f) => Array.from(document.querySelectorAll('[id$="-' + f + '-EDITOR_c"]')).filter(vis)"""
        return eval_all_frames(self.page, js, field)

    # ★★ 2026-10-03 第 4 修（SF-55）：`fill_bill_field` **从前用弱可见性过滤**，
    #   在 SPA 里多个「应收单 - 新增」表单并存时（本批实测最多 11 个标签），
    #   `[id$="-FDATE-EDITOR_c"]` 会命中**后台那张单**的控件：
    #     `filter(vis)[0]` 挑 DOM 里第一个"尺寸/显隐 OK"的 → 恰是**最老的那张孤儿草稿**
    #   ⇒ 写是**真的写进去了**（所以日志打印「填写 FDATE = 2026-09-30 [kendoDatePicker]」，
    #      API 也返回新值），但**写在了别人身上**；当前这张单的 FDATE 仍是默认的"今天"。
    #   ⇒ 保存后业务日期 = 今天(2026-10-03) → 单号年月变 `-202610-`，
    #      且**到期日 FENDDATE_H 随业务日期联动**，一起变成 10-03。
    #   本批 13 家里 7 家中招（`AR105-202610-0001..0007`），6 家侥幸正确。
    #   实测（`src/probe_date_write.py`，2026-10-03 05:15）：
    #     · 下推后草稿基线 = `FDATE 2026/10/3`，而**到期日已 = 2026/9/30**（下推带出）
    #     · kendoDatePicker API 路径**本身是有效的**（写 9/29 → 到期日联动成 9/29）
    #     · 「真实鼠标点击 + 忠实键盘打字」**反而更差**：掩码输入框只得到 `'20260930'`，
    #       widget=None、model 不入 ⇒ **日期字段不要用键盘打字兜底**
    #   ⇒ 正确修法只有两条：① **只认前台表单的控件**（复用 `_VIS_STRONG` 的 `findEditor`）；
    #                       ② **写后回读**，不一致重写（≤3 轮），仍不一致**抛错**（凡写必回读）。
    _FIELD_WRITE_JS = (
        "const svis = e => { if (!e) return false;"
        "const r = e.getBoundingClientRect(); const s = getComputedStyle(e);"
        "if (!(r.width>0 && r.height>0)) return false;"
        "if (s.display==='none' || s.visibility==='hidden' || s.opacity==='0') return false;"
        "if (e.offsetParent === null && s.position !== 'fixed') return false;"
        "return true; };"
        "const onTop = e => { if (!e) return false;"
        "const r = e.getBoundingClientRect();"
        "const x = Math.round(r.x + r.width/2), y = Math.round(r.y + r.height/2);"
        "if (x<0||y<0||x>=innerWidth||y>=innerHeight) return false;"
        "const t = document.elementFromPoint(x, y);"
        "return !!(t && (e===t || e.contains(t))); };"
        "const inActivePanel = e => { let p = e; while (p) { const c = p.classList;"
        "if (c && (c.contains('k-content') || c.contains('k-tabstrip-content')))"
        "return c.contains('k-state-active'); p = p.parentElement; } return false; };"
        "const inView = e => { if (!e) return false; const r = e.getBoundingClientRect();"
        "return !(r.right<=0 || r.bottom<=0 || r.left>=innerWidth || r.top>=innerHeight); };"
        # 只会命中外层 BOX（...-FIELD-EDITOR），再往盒子里找真正的 input
        "const boxes = f => Array.from(document.querySelectorAll('[id$=\"-' + f + '-EDITOR\"]'))"
        ".filter(e => svis(e) && e.querySelector('input'));"
        "const pickBox = f => { const b = boxes(f); if (!b.length) return null;"
        "return b.find(onTop) || b.filter(inView).find(inActivePanel)"
        "    || b.find(inActivePanel) || b.find(inView) || b[0]; };"
        "const pickInput = box => box.querySelector('input') || box;"
    )

    _FIELD_READ_JS = (
        "const p = n => String(n).padStart(2,'0');"
        "(f) => {"
        "  const box = pickBox(f); if (!box) return null;"
        "  const inp = pickInput(box);"
        "  const $ = window.jQuery;"
        "  let w = null;"
        "  try { const d = $ && ($(inp).data('kendoDatePicker') || $(box).data('kendoDatePicker'));"
        "        if (d && d.value()) { const v = d.value();"
        "          w = v.getFullYear() + '-' + p(v.getMonth()+1) + '-' + p(v.getDate()); } } catch (e) {}"
        "  return {val: inp.value, widget: w, box: box.id, cand: boxes(f).length,"
        "          onTop: onTop(box), active: inActivePanel(box)};"
        "}"
    )

    @staticmethod
    def _norm_field_v(v):
        """把 2026/9/30、2026-09-30、2026/09/30 00:00 之类归一成 YYYY-MM-DD；非日期原样返回。"""
        import re as _re
        s = str(v if v is not None else "").strip()
        m = _re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
        if m:
            return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return s

    @staticmethod
    def _gap_days(a, b):
        """两个 YYYY-MM-DD 相差的天数（a − b）；任一无解析则 None。

        ★ 用途：判断「账期」（到期日 − 业务日期）有没有变化 ——
          见 `fill_bill_field` 的 `linked` 判据（有账期的客户不能被误判成写入失败）。
        """
        from datetime import date
        try:
            ya = [int(x) for x in str(a or "").split("-")]
            yb = [int(x) for x in str(b or "").split("-")]
            if len(ya) != 3 or len(yb) != 3:
                return None
            return (date(*ya) - date(*yb)).days
        except Exception:  # noqa: BLE001
            return None

    def read_foreground_field(self, field):
        """读**前台表单**里某字段的当前值（含 kendo widget 值）。找不到返回 None。"""
        js = self._FIELD_WRITE_JS + self._FIELD_READ_JS
        r = eval_all_frames(self.page, js, field)
        return r if isinstance(r, dict) else None

    def fill_bill_field(self, field, value, wait_ms=300, tries=3, verify=True,
                        linked=None, settle_ms=1200):
        """
        按字段名精确填写**前台**表单控件（优先于 fill_field_by_label）。
        field 为金蝶内部字段名，如 'FDATE' / 'FBILLNO' / 'FAR_REMARK'。
        日期控件走 kendoDatePicker API，文本控件走 value + 事件序列。

        ★ 2026-10-03（SF-55）：**只认前台表单 + 写后回读**。
          回读判据：
            · 日期型（YYYY-MM-DD）→ 必须与目标**逐日相等**；
            · 文本型 → 回读为空视为失败（非空但与目标不同只 WARN）；
            · `linked`（★ 关键）：日期联动字段（如 应收单 `FDATE` → 到期日 `FENDDATE_H`）
              **必须一起变成目标值**。

        ★★ 为什么 `linked` 是必需的判据（2026-10-03 实测，7/7 完全相关）：
          `dp.value()` + `widget` 回读**只能证明 widget 的值变了，不能证明表单 model 变了**。
          实测同一次批量：7 家里 5 家 `到期日` 跟着变成 9/30 → 保存后服务端也是 9/30；
          另 2 家 `到期日` **原地不动**（还是 8/31 / 10/3）→ 保存后服务端业务日期 = **今天(10-03)**，
          单号年月随之错成 `-202610-`。
          ⇒ **"联动有没有跟" = "model 有没有进" 的免费见证**，比读 widget 可靠得多。
          ⚠️ 该判据依赖"该字段确实有联动规则"；无联动规则的字段不要传 `linked`。

        重试策略（第 1 轮失败才换，逐轮更"重"）：
          1) kendoDatePicker API（原路径）
          2) 同 1 + **真实鼠标点空处 blur**（trusted blur 促发提交）
          3) 同 2 + 再写一次并等待
        ⚠️ **不要**用"真实鼠标 + keyboard.type"写 Kendo 日期：掩码框只会得到
          `'20260930'`，widget=None，model 不入（实测）。

        返回 True；不达标抛 RuntimeError（**宁可不保存，也不让错值落库**）。
        """
        tgt = self._norm_field_v(value)
        is_date = bool(tgt) and tgt.count("-") == 2 and tgt[:4].isdigit()
        write_js = self._FIELD_WRITE_JS + """(a) => {
            const [f, val] = a;
            const box = pickBox(f);
            if (!box) return {err: 'no such field', field: f, cand: boxes(f).length};
            const el = pickInput(box);
            const cand = boxes(f).length;
            el.click(); el.focus();
            const $ = window.jQuery;
            const dp = $ && ($(el).data('kendoDatePicker') || $(box).data('kendoDatePicker'));
            let how = 'raw';
            if (dp) {
                // Kendo DatePicker 必须走 API，直接改 value 不会同步到 model
                dp.value(new Date(String(val).replace(/-/g, '/')));
                dp.trigger('change');
                el.dispatchEvent(new KeyboardEvent('keydown',
                    {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}));
                how = 'kendoDatePicker';
            } else {
                el.value = String(val);
                ['input','change','keydown','keyup','blur'].forEach(t =>
                    el.dispatchEvent(new Event(t, {bubbles:true})));
                el.dispatchEvent(new KeyboardEvent('keydown',
                    {key:'Enter', code:'Enter', keyCode:13, which:13, bubbles:true}));
            }
            return {how: how, val: el.value, box: box.id, cand: cand,
                    onTop: onTop(box), active: inActivePanel(box)};
        }"""
        # ★★ 2026-10-03（SF-55 **两次修正**）：`linked` 判据不能要求「联动 == 目标值」——
        #   有账期的客户，业务日期写对之后到期日并不等于业务日期（江西新明峰实测：
        #   写入前 10-03 / 2027-01-29 → 写入后 09-30 / 2026-12-29，金蝶会自己重算到期日，
        #   连"账期不变"都不成立）。详见下面 `_link_ok` 的判据说明。
        #   这里先抓"写入前的基准值"，用于判"联动到底有没有反应"。
        base_self = self._norm_field_v((self.read_foreground_field(field) or {}).get("val"))
        base_link = (self._norm_field_v((self.read_foreground_field(linked) or {}).get("val"))
                     if linked else None)
        if linked:
            self.log.info(f"[联动基准·写入前] {field}={base_self!r} / {linked}={base_link!r}")

        def _link_ok(link_val, self_val):
            """联动判据（★ 2026-10-03 第二次修正）。

            **不能**要求「联动 == 目标值」：有账期的客户，到期日 = 业务日期 + 账期，
            业务日期**写对之后**到期日读到的是 `2026-12-29` 而不是 `2026-09-30`
            ⇒ 旧判据把**写成功**误判成失败。
            **也不能**要求「账期（联动−自身）不变」：实测江西新明峰这张单
            写入前 `10-03 / 2027-01-29`（账期 118 天）、写入后 `09-30 / 2026-12-29`（账期 90 天）
            —— 金蝶会按自己的规则**重算**到期日，账期并不守恒。

            **正确判据 = 联动字段的值"确实发生了变化"**（= 表单 model 收到了 change）：
              · 联动值恰好等于目标值（账期 0 天）→ 通过；
              · 写入前自身就已经是目标值 → 它本来就不该再变，直接放行；
              · 否则要求「联动值 ≠ 写入前基准值」。
            这样既支持有账期的客户，又保留 SF-55 的护栏
            （widget 骗人时 model 没进 ⇒ 联动毫无反应 ⇒ 判失败）。
            """
            if not linked:
                return True
            lv = self._norm_field_v(link_val)
            if lv and lv == tgt:                 # 账期 0 天：联动即目标值
                return True
            if base_self == tgt:                 # 写前自身已正确 → 联动本就不该变
                return True
            return bool(lv) and lv != base_link

        last = None
        last_link = None
        for i in range(1, max(1, tries) + 1):
            r = eval_all_frames(self.page, write_js, [field, str(value)])
            if not r:
                raise RuntimeError(f"页面上找不到字段 {field!r}（或不可见）")
            if isinstance(r, dict) and r.get("err"):
                raise RuntimeError(f"填字段失败 {field}: {r['err']}（候选控件 {r.get('cand')} 个）")
            self.page.wait_for_timeout(wait_ms)
            # 第 2 轮起：真实鼠标点空处触发 trusted blur（合成事件 Kendo 可能不认）
            if i >= 2 and is_date:
                try:
                    self.page.mouse.click(6, 6)
                    self.page.wait_for_timeout(350)
                except Exception:  # noqa: BLE001
                    pass

            def _snap():
                b = self.read_foreground_field(field) or {}
                lk = (self.read_foreground_field(linked) or {}) if linked else {}
                return b, lk

            if not verify:
                self.log.info(f"填写 {field} = {value}   [{r.get('how')}]")
                return True

            back, linkb = _snap()
            got, wid = back.get("val"), back.get("widget")
            g_norm = self._norm_field_v(got)
            ok_self = (g_norm == tgt) if is_date else bool(str(got or "").strip())
            ok_link = _link_ok(linkb.get("val"), g_norm)
            tag = f"[{r.get('how')}]"
            if ok_self and ok_link:
                # 稳定复验：等一会儿再读一次，防"写进去又被框架初始化覆盖"
                self.page.wait_for_timeout(settle_ms)
                back2, linkb2 = _snap()
                if is_date and self._norm_field_v(back2.get("val")) != tgt:
                    self.log.warn(f"★ {field} 写进去又被改回 {back2.get('val')!r}（第 {i} 轮）"
                                  f"—— 疑似表单初始化覆盖，重写")
                    last = back2
                    continue
                if not _link_ok(linkb2.get("val"), self._norm_field_v(back2.get("val"))):
                    self.log.warn(f"★ {linked} 联动值不稳定（{linkb2.get('val')!r}），重写")
                    last = back2
                    continue
                extra = ""
                if back.get("cand", 1) > 1:
                    extra = (f"（页面上有 {back['cand']} 个同名控件 → 已挑前台那个 "
                             f"{back.get('box')}）")
                if linked:
                    extra += f"；联动 {linked}={linkb2.get('val')!r} ✓"
                if i > 1:
                    self.log.warn(f"★ {field} 第 1 次写入**没生效**，第 {i} 次才写进去（凡写必回读）")
                self.log.info(f"填写 {field} = {value} {tag} → 回读 {got!r}"
                              + (f" widget={wid}" if wid else "") + extra)
                return True

            last = back
            last_link = linkb
            why = []
            if not ok_self:
                why.append(f"自身回读 {got!r}")
            if not ok_link:
                why.append(f"★ 联动字段 {linked} 未跟上（读到 {linkb.get('val')!r}）"
                           f"[基准 {base_link!r}→ 账期 {self._gap_days(base_link, base_self)} 天；"
                           f"现值账期 {self._gap_days(self._norm_field_v(linkb.get('val')), g_norm)} 天]")
            self.log.warn(f"★★ {field} 写入**未生效**（第 {i}/{tries} 轮）：目标 {tgt!r}；"
                          + "；".join(why)
                          + f"；控件 {back.get('box')} 候选 {back.get('cand')} 个，"
                            f"onTop={back.get('onTop')} 在激活面板={back.get('active')}")
        raise RuntimeError(
            f"字段 {field} 写入 {tries} 轮后回读仍不符（目标 {tgt!r}，"
            f"读到 {(last or {}).get('val')!r}"
            + (f"，联动字段 {linked} = {(last_link or {}).get('val')!r}"
               f"（基准 {base_link!r}）" if linked else "")
            + "）—— **已停止**，绝不带着错值继续保存"
              "（SF-55：写端零校验会让错值静默落库；联动字段不跟 = 表单 model 没进）")

    # ---------- Kendo 下拉字段（kdcombo）----------
    # 实测（2026-09-18 增值税专用发票 SVINV00033649）：
    #   「复核人」= F_PAEZ_COMBO，控件 id 后缀 -F_PAEZ_COMBO-EDITOR（注意：不是 EDITOR_c）
    #   结构：span.k-widget.k-dropdown > span.k-dropdown-wrap > span.k-input（显示文本）
    #                                                > span.k-select（小箭头，点它才展开）
    #         另有一个 input[data-role="dropdownlist"]（隐藏）持有值
    #   ⚠️ 展开后的 ul.k-list 在 .k-animation-container/.k-list-container 里，且是**按需渲染**
    #      → 必须先点 .k-select，等动画结束再去找 li
    #   ⚠️ 直接改 input.value 不会同步模型，必须真的点选 li
    # ★2026-09-21 实测补充（专票「复核人」F_PAEZ_COMBO）：
    #   该 kdcombo **内部没有** `input[data-role="dropdownlist"]`，也没有可用的 kendo 实例，
    #   所以 widget.open() 这条路走不通；合成 MouseEvent 点 .k-select 同样无效。
    #   **唯一可靠路径 = 真实鼠标点击**：
    #     ① JS `scrollIntoView` 把控件滚进视口（曾踩坑：控件 y=711 > 视口高 641，
    #        elementFromPoint 返回 null，点击落到窗口外 → 弹窗根本没开）
    #     ② `page.mouse.click(控件中心)` 展开
    #     ③ JS 取目标 li 的中心坐标 → 再 `page.mouse.click` 选中
    #   展开后的选项（实测 9 项）：蒙晓芳/刘启丽/陈聪/张婷/杨蓉/姚玉平/区志婷/廖惠娇/李佩瑶
    def open_combo(self, field):
        """
        点开某个下拉控件。
        优先 kendo widget.open()；该控件没有 widget 时退回**真实鼠标点击**。
        ⚠️ 合成 MouseEvent 点 .k-select 不生效（Kendo 认真实输入）。
        """
        js = self._INVISIBLE + self._VIS_STRONG + """(f) => {
            const box = findEditor(f);
            if (!box) return false;
            // ★ 2026-10-03：**开下拉前必须先把控件滚进视口**。
            //   视口矮（实测 641px）时表单下半部分的控件在折叠下方 →
            //   ① Kendo 的 popup 会贴在控件下方（约 722px），**整块在视口外**；
            //   ② `elementFromPoint` 对视口外的点直接返回 null → 选择项的命中检测永远 false，
            //      最后"按最后坐标点一次"落在窗口外，静默无效（回读为空）。
            //   scrollIntoView 是同步的，之后重取 rect/popup 位置就是准的。
            box.scrollIntoView({block: 'center', inline: 'nearest'});
            const inp = box.querySelector('input[data-role="dropdownlist"]');
            const $ = window.jQuery;
            if ($ && inp) {
                const w = $(inp).data('kendoDropDownList');
                if (w) {
                    try { w.open(); }
                    catch (e) { w.toggle(); }
                    return true;
                }
            }
            // 没有 kendo widget → 交给 Python 侧真实鼠标点击（见下）
            return 'needs_mouse';
        }"""
        ok = eval_all_frames(self.page, js, field)
        if not ok:
            raise RuntimeError(f"找不到下拉控件：{field!r}（或不可见）")
        if ok == "needs_mouse":
            self._mouse_open_combo(field)
        self.page.wait_for_timeout(600)
        return True

    def _mouse_open_combo(self, field, tries=3):
        """真实鼠标点开下拉：先滚进视口，再点控件中心。返回点击坐标。"""
        js = self._INVISIBLE + self._VIS_STRONG + """(f) => {
            const box = findEditor(f);
            if (!box) return null;
            box.scrollIntoView({block: 'center', inline: 'nearest'});
            const r = box.getBoundingClientRect();
            const cx = Math.round(r.x + r.width * 0.5), cy = Math.round(r.y + r.height * 0.5);
            const top = document.elementFromPoint(cx, cy);
            return { cx, cy, vw: innerWidth, vh: innerHeight,
                     inView: r.top >= 0 && r.bottom <= innerHeight,
                     hit: top ? box.contains(top) : false };
        }"""
        for i in range(tries):
            p = eval_all_frames(self.page, js, field)
            if not p:
                raise RuntimeError(f"找不到下拉控件：{field!r}")
            if p["hit"]:
                self.page.mouse.click(p["cx"], p["cy"])
                self.log.info(f"真实鼠标点开下拉 {field} @({p['cx']},{p['cy']})")
                return p
            self.page.wait_for_timeout(400)
        raise RuntimeError(f"下拉 {field} 被遮挡或不可见，点不开：{p}")

    def list_combo_options(self, limit=60):
        """列出当前展开下拉里的所有选项文本（调试/兜底用）"""
        js = """(n) => {
            const cs = Array.from(document.querySelectorAll('.k-list-container,.k-animation-container'))
                .filter(e => { const r = e.getBoundingClientRect();
                               return r.width>0 && r.height>0 && getComputedStyle(e).visibility!=='hidden'; });
            const out = [];
            for (const c of cs) {
                for (const li of c.querySelectorAll('li')) {
                    const t = (li.textContent||'').trim();
                    if (t) out.push(t);
                }
            }
            return out.slice(0, n);
        }"""
        return eval_all_frames(self.page, js, limit) or []

    def pick_combo_option(self, text, exact=True, field=None, tries=8, gap_ms=350):
        """
        在已展开的下拉里按文本点选项，返回实际点中的文本。
        ⚠️ 合成 MouseEvent 对 Kendo 列表项**大概率无效**（实测 kdcombo 的下拉），
           所以这里先取 li 的中心坐标，再用 `page.mouse.click` 真点。

        ★ 2026-10-03 两次修复：
          ① **必须轮询到"命中稳定"再点**。`open_combo()` 固定等 600ms 后立刻取 li
             坐标点击时，Kendo 下拉**展开动画**还没结束、坐标还在漂移 → 点空。
             （现象：日志`选择下拉项：姚玉平 @(549,545)`但回读为空）
          ② **必须限定"贴着本控件"的下拉容器**（传 `field`）。金蝶 SPA 里后台标签页
             的表单 DOM 仍在，它们的下拉选项**文本完全一样**；只按文本找会挑中后台
             那个 → 连点 3 次都"选上了"，前台表单始终为空（2026-10-03 福建广源实测）。
        """
        js = self._VIS_STRONG + """(a) => {
            const [t, ex, fld] = a;
            const box = fld ? findEditor(fld) : null;
            const br = box ? box.getBoundingClientRect() : null;
            let cs = Array.from(document.querySelectorAll('.k-list-container,.k-animation-container'))
                       .filter(svis);
            if (br) {
                // ★★ 只认"贴着本控件"的那个下拉容器（下落菜单就在控件正下方）。
                //   为什么必须加这一层：金蝶 SPA 里**后台标签页的表单 DOM 还在**，
                //   它们的下拉容器连同"姚玉平/陈聪/张婷…"这堆选项**文本完全一样**，
                //   只按文本找会挑中后台那个 → 点上去静默无效。
                //   实测（2026-10-03 福建广源）：连点 3 次都"选上了"，前台表单始终为空。
                const cx0 = br.x + br.width / 2, cy0 = br.y + br.height / 2;
                const near = cs.filter(c => {
                    const r = c.getBoundingClientRect();
                    const dx = Math.abs((r.x + r.width / 2) - cx0);
                    const dy = (r.y + r.height / 2) - cy0;
                    return dx <= 460 && dy >= -80 && dy <= 620;
                });
                if (near.length) cs = near;
            }
            for (const c of cs) {
                const lis = Array.from(c.querySelectorAll('li')).filter(svis);
                const li = lis.find(x => {
                    const s = (x.textContent||'').trim();
                    return ex ? s === t : s.includes(t);
                });
                if (li) {
                    li.scrollIntoView({block:'center'});
                    const r = li.getBoundingClientRect();
                    const x = Math.round(r.x + r.width / 2), y = Math.round(r.y + r.height / 2);
                    // ★ 命中检测：该坐标处的**最顶层元素**必须落在这个 li 里。
                    //   动画中坐标会漂移，此刻 hit=false，等下一轮再试。
                    const top = document.elementFromPoint(x, y);
                    return { text: (li.textContent||'').trim(), x, y,
                             hit: !!(top && (li === top || li.contains(top))),
                             all: lis.map(x => (x.textContent||'').trim()).slice(0, 30) };
                }
                if (lis.length) {
                    return { err: 'no such option', text: t, exact: ex,
                             all: lis.map(x => (x.textContent||'').trim()).slice(0, 30) };
                }
            }
            return { err: 'no visible list container' };
        }"""
        got = None
        for i in range(tries):
            got = eval_all_frames(self.page, js, [str(text), bool(exact), field or ""])
            if not got:
                got = {"err": "no visible list container"}
            elif got.get("err") == "no such option":
                # 列表已渲染且没有这个值 —— 再等也不会出现
                raise RuntimeError(f"下拉里找不到选项：{text!r} —— {got}")
            elif got.get("hit"):
                break
            if i < tries - 1:
                self.page.wait_for_timeout(gap_ms)
        if not got or got.get("err"):
            raise RuntimeError(f"下拉里找不到选项：{text!r} —— {got}")
        if not got.get("hit"):
            # 兜底：轮询到超时仍未命中，仍按最后坐标点一次（旧行为），让上层校验兜住
            self.log.warn(f"下拉选项 {text!r} 坐标一直未命中（@{got['x']},{got['y']}），仍尝试点击")
        self.page.mouse.click(got["x"], got["y"])
        self.log.info(f"选择下拉项：{got['text']} @({got['x']},{got['y']})"
                      f"{'' if got.get('hit') else '  ⚠️未命中'}")
        self.page.wait_for_timeout(500)
        return got["text"]

    def read_combo(self, field):
        """读下拉控件当前显示文本（只认**前台**那个控件，见 _VIS_STRONG）"""
        js = self._INVISIBLE + self._VIS_STRONG + """(f) => {
            const box = findEditor(f);
            if (!box) return null;
            const k = box.querySelector('.k-input');
            const inp = box.querySelector('input[data-role="dropdownlist"]');
            return { text: k ? (k.textContent||'').trim() : null,
                     value: inp ? (inp.value||'').trim() : null,
                     id: box.id, onTop: onTop(box) };
        }"""
        return eval_all_frames(self.page, js, field)

    def fill_combo(self, field, text, exact=True, tries=3):
        """
        按显示文本选择下拉值（一步到位）。
        例：fill_combo('F_PAEZ_COMBO', '姚玉平')   # 增值税专用发票-复核人

        ★ 2026-10-03：加**外层重试**。取值失败的原因常常是一次性的
          （下拉动画未收敛 / 坐标漂移 / 上一次点击落在了别处），
          重开一次下拉再选基本就中。重试前先 Esc 收起可能残留的面板。
        """
        last = None
        for i in range(1, tries + 1):
            self.open_combo(field)
            try:
                self.pick_combo_option(text, exact=exact, field=field)
            except RuntimeError as e:
                last = e
                opts = self.list_combo_options()
                self.log.warn(f"第 {i}/{tries} 次没选上 {text!r}（{e}），可见选项：{opts}")
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(700)
                continue
            cur = self.read_combo(field) or {}
            if cur.get("text") == str(text):
                self.log.info(f"填写 {field} = {text}（校验通过，第 {i} 次）")
                return True
            last = RuntimeError(f"下拉 {field} 回填校验失败：期望 {text!r}，实际 {cur!r}")
            self.log.warn(f"第 {i}/{tries} 次选上了但回读不符：期望 {text!r} 实际 {cur!r}")
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(700)
        raise RuntimeError(str(last))

    def read_bill_fields(self, fields=None):
        """
        读当前**前台**单据的关键字段值（★ SF-55 读端修复：只认前台）。
        fields 为空则读默认集合：单据编号/业务日期/到期日/备注

        ★ 2026-10-03（AR105-202609-0108 事故）：
          旧实现用 `[id$="-FIELD-EDITOR_c"]` + **弱可见性** `filter(vis)[0]`。金蝶 SPA 把
          后台标签页的整份表单 DOM 都留在 document 里、字段 id 完全相同 ⇒ 会**读到别的单据
          的值**。实测：扬州祥恒的专票号被读成上一家的 `SVINV00034032`（实际 `…033`）→
          step_voucher 勾错单 → 界面弹「所选单据已生成总账凭证」→ **凭证压根没生成，
          报告仍显示「0 错误」**（静默失败）→ 下游摘要也无处可写。
          现在先走 `read_foreground_field`（onTop → 激活面板，只认前台），读不到才退回旧写法；
          同名候选 >1 时一律 WARN 留痕。
        """
        want = fields or ["FBILLNO", "FDATE", "FENDDATE_H", "FAR_REMARK"]
        out = {}
        for f in want:
            r = self.read_foreground_field(f)
            v = (r or {}).get("val") if isinstance(r, dict) else None
            if v in (None, ""):
                # 兜底：旧的 `-EDITOR_c` 写法（个别字段只有这种 id 形态）
                v = eval_all_frames(self.page, self._INVISIBLE + """(f) => {
                    const el = Array.from(document.querySelectorAll('[id$="-' + f + '-EDITOR_c"]')).filter(vis)[0];
                    return el ? el.value : null;
                }""", f)
            out[f] = v
            if isinstance(r, dict) and int(r.get("cand") or 0) > 1:
                self.log.warn(f"读 {f}：页面并存 {r['cand']} 个同名控件，已取前台那个"
                              f"（onTop={r.get('onTop')} active={r.get('active')}）= {r.get('val')!r}")
        return out

    def grid_amount_total(self, grid_suffix="-FENTITYDETAIL-c", field="FALLAMOUNTFOR_D"):
        """
        读明细网格某列的合计行/所有行合计。
        用 Kendo Grid 的 dataSource 取（不受分页和虚拟化限制），取不到再退化为扫 DOM。
        """
        js = self._INVISIBLE + """(a) => {
            const [gsuf, fld] = a;
            const gridEl = Array.from(document.querySelectorAll('[id$="' + gsuf + '"]')).filter(vis)[0];
            if (!gridEl) return {err: 'no grid', suffix: gsuf};
            const $ = window.jQuery;
            const g = $ ? $(gridEl).data('kendoGrid') : null;
            let sum = 0, n = 0, rows = [];
            if (g) {
                g.dataSource.data().forEach(d => {
                    const v = parseFloat(String(d[fld] ?? '').replace(/[¥,\\s]/g, ''));
                    if (!isNaN(v)) { sum += v; n++; }
                });
                rows = g.dataSource.view().map(d => String(d[fld] ?? '')).slice(0, 5);
                return { how: 'kendoGrid', sum, n, sample: rows, total: g.dataSource.total() };
            }
            // 退化：扫可见行
            const ths = Array.from(gridEl.querySelectorAll('th')).map(x => x.getAttribute('data-field'));
            const ci = ths.indexOf(fld);
            const tb = gridEl.querySelector('table.k-selectable');
            if (ci < 0 || !tb) return {err: 'cannot locate column', fields: ths.slice(0,10)};
            Array.from(tb.querySelectorAll('tbody tr')).forEach(tr => {
                const td = tr.querySelectorAll('td')[ci];
                const v = parseFloat((td ? td.innerText : '').replace(/[¥,\\s]/g, ''));
                if (!isNaN(v)) { sum += v; n++; }
            });
            return { how: 'dom', sum, n };
        }"""
        return eval_all_frames(self.page, js, [grid_suffix, field])

    def verify_amount(self, expect, grid_suffix="-FENTITYDETAIL-c", tol=0.05):
        """
        核对单据价税合计与期望值（Excel / 电子税务局）一致。
        超出容差抛异常，阻断后续不可逆操作。
        """
        r = self.grid_amount_total(grid_suffix, "FALLAMOUNTFOR_D")
        if not isinstance(r, dict) or r.get("err"):
            raise RuntimeError(f"读不到金额合计：{r}")
        got = float(r.get("sum") or 0)
        n = int(r.get("n") or 0)
        self.log.info(f"金额核对：期望 {float(expect):,.2f}  实际 {got:,.2f}（{n} 行明细，来源 {r.get('how')}）")
        if abs(got - float(expect)) > tol:
            raise RuntimeError(
                f"金额不一致！期望 {float(expect):,.2f}，单据实际 {got:,.2f}，差 {got - float(expect):,.2f}"
            )
        return got

    # ==========================================================
    # 明细网格通用读写 + 金额闭环对齐（2026-09-21 固化）
    # ----------------------------------------------------------
    # 背景：下推链里常要「把一笔扣款按比例分摊成每行折扣额，再把总不含税/
    #      总税额顶到实际开票值」。这段以前是三个一次性脚本
    #      （gy_fill / gy_adjust / gy_adjust2），扣款金额与目标值全硬编码
    #      → 换个客户就得重写。现在收编成本类方法 + discount.py 纯计算模块。
    #
    # ★ 字段名陷阱（实测，写错会静默读 0）：
    #     应收单明细 不含税 FNOTAXAMOUNTFOR_D / 税额 FTAXAMOUNTFOR_D
    #                价税合计 FALLAMOUNTFOR_D   / 折扣额 FDISCOUNTAMOUNTFOR
    #     专票明细   不含税 FNOTAXAMOUNT     / 税额 FDETAILTAXAMOUNT
    #                价税合计 FALLAMOUNT       ← ★没有 FTAXAMOUNT
    #
    # ★ 行内联动规律（实测，决定校正顺序）：
    #     改「折扣额」  → 价税合计 = 含税单价×数量 − 折扣额，再反算不含税/税额
    #     改「不含税」  → 价税合计 = 不含税 + 税额（税额不动）
    #     改「税额」    → 价税合计 = 不含税 + 税额（不含税不动）
    #     且金蝶反算走「单价」，不是简单 ÷(1+税率)，会差 1~2 分
    #     ⇒ 对齐必须「读页面实际合计 → 算差 → 写 → 再读」闭环，
    #       不能拿公式离线推算（否则越校越偏）
    # ==========================================================

    AMOUNT_FIELDS = {
        "receivable": {"notax": "FNOTAXAMOUNTFOR_D", "tax": "FTAXAMOUNTFOR_D",
                       "all": "FALLAMOUNTFOR_D", "discount": "FDISCOUNTAMOUNTFOR"},
        "invoice": {"notax": "FNOTAXAMOUNT", "tax": "FDETAILTAXAMOUNT",
                    "all": "FALLAMOUNT", "discount": None},
    }
    # 明细网格里用来「移开焦点」触发重算的安定格（实测：点它不产生副作用）
    _BLUR_FIELD = "FMATERIALNAME"

    def amount_fields(self, kind="receivable"):
        """取某类单据的明细金额字段名（config.yaml 的 amount_fields 可覆盖）"""
        base = dict(self.AMOUNT_FIELDS.get(kind) or {})
        override = ((self.cfg or {}).get("amount_fields") or {}).get(kind) or {}
        base.update({k: v for k, v in override.items() if v})
        missing = [k for k in ("notax", "tax", "all") if not base.get(k)]
        if missing:
            raise KeyError(f"kind={kind!r} 缺金额字段配置：{missing}；"
                           f"可选 kind={list(self.AMOUNT_FIELDS)}")
        return base

    # ---- 读 ----

    def detail_rows(self, fields=None, limit=None):
        """读当前**可见**明细网格的所有渲染行，返回 [{data-field: 文本}, ...]。

        注意：明细网格（应收单 -FENTITYDETAIL-c / 专票 -FENTRYPAGE_FSITEM）
        实测 46 行都能读到，不像列表那样虚拟滚动只渲染 200 行。
        必须先按可见性过滤 —— SPA 里隐藏页面的表格同样能被 querySelector 命中。
        """
        js = self._INVISIBLE + """() => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return {err: 'no visible grid'};
            const trs = Array.from(g.querySelectorAll('tbody tr'));
            const rows = trs.map(tr => {
                const o = {};
                Array.from(tr.querySelectorAll('td')).forEach(td => {
                    const s = td.querySelector('[data-field]');
                    if (s) o[s.getAttribute('data-field')] = (s.innerText || '').trim();
                });
                return o;
            });
            return {n: trs.length, rows: rows};
        }"""
        r = eval_all_frames(self.page, js)
        if not isinstance(r, dict) or r.get("err"):
            raise RuntimeError(f"明细网格读取失败：{r}")
        rows = r.get("rows") or []
        if fields:
            rows = [{k: v for k, v in row.items() if k in fields} for row in rows]
        if limit:
            rows = rows[:limit]
        return rows

    def dump_columns(self):
        """★ 现场字段清单（只读）：列出当前页「单头字段」与「可见明细表头」的
        标识(data-field) ↔ 中文标题。用于发现「到期日」「存货类别」「计价数量」
        这类**字段标识未知**的列 —— 不要再靠猜。

        返回 {"header": [{"id","field","tag","label"}], "grid": [{"field","label"}]}
        """
        js = self._INVISIBLE + """() => {
            const out = {header: [], grid: []};
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (g) {
                Array.from(g.querySelectorAll('thead th')).forEach(th => {
                    const f = th.getAttribute('data-field') || '';
                    const t = (th.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (t) out.grid.push({field: f, label: t});
                });
            }
            const seen = new Set();
            Array.from(document.querySelectorAll('[id$="_c"]')).filter(vis).forEach(el => {
                const id = el.id || '';
                const parts = id.split('-');
                const fld = (parts[parts.length - 1] || '').replace(/_c$/, '');
                if (!fld || seen.has(fld)) return;
                let lab = '';
                const l = document.querySelector('label[for="' + id + '"]');
                if (l) lab = (l.innerText || '').trim();
                if (!lab) {
                    let p = el.parentElement;
                    for (let k = 0; k < 4 && p; k++, p = p.parentElement) {
                        const c = p.querySelector('.kd-caption, label, .kd-fieldtitle');
                        if (c && (c.innerText || '').trim()) { lab = c.innerText.trim(); break; }
                    }
                }
                seen.add(fld);
                out.header.push({id: id.slice(-46), field: fld,
                                 tag: el.tagName, label: lab});
            });
            return out;
        }"""
        r = eval_all_frames(self.page, js)
        return r if isinstance(r, dict) else {"header": [], "grid": []}

    def detail_empty_row_indexes(self, kind="receivable"):
        """找出明细里「物料为空」的空行（0-based 行号列表）。

        ★ 2026-10-03 修正（SF-57 根因已推翻）：之前误判为「多张出库单下推合并
        会插空行」，但实测（9-22 probe_dlg_full + 10-03 全批日志）证明：
          · 界面上明细**每行都有物料**，根本没有空行；
          · 报错文本「第1行分录 物料必录」指的是金蝶**数据层**的首条分录
            FMATERIALID 为空，与界面网格显示无关；
          · 「明细价税合计 ≠ 表头」是**金额联动重算未收敛**（脚本机器速度点保存
            撞上金蝶重算中间态），不是空行破坏合计。
        ⇒ 本函数保留作「防御性兜底」（万一真有脏行），但**不把它当根因**。
        空行判据：物料编号(FMATERIALID / FMATERIALID_FNUMBER) 和物料名称(FMATERIALNAME)
        都为空。
        """
        rows = self.detail_rows()
        out = []
        for i, row in enumerate(rows):
            mid = (row.get("FMATERIALID") or row.get("FMATERIALID_FNUMBER") or "").strip()
            mname = (row.get("FMATERIALNAME") or "").strip()
            seq = (row.get("FSEQ") or "").strip()
            if not mid and not mname:
                out.append(i)
        return out

    def detail_drop_empty_rows(self, kind="receivable", max_rounds=5):
        """删除明细里的空行（物料为空），返回删除的个数。

        ★ 金蝶明细网格删行：真实鼠标选中该行 → 点工具栏「删除行」。
        实测「删除行」按钮 id 形如 tbDeleteRow / tbDelete / -FDELETEROW-。
        若找不到按钮则走 Kendo Grid dataSource 删（删完刷新）。
        删空行是**只针对物料为空的垃圾行**，不动任何有物料的正常行，安全。
        """
        removed = 0
        for _ in range(max_rounds):
            idxs = self.detail_empty_row_indexes(kind)
            if not idxs:
                break
            # 每次只删第一个空行（删完行号会重排，逐轮重读最稳）
            i = idxs[0]
            ok = self._delete_detail_row(i)
            if not ok:
                self.log.warn(f"删除空行 idx={i} 未成功，停止继续删")
                break
            removed += 1
        if removed:
            self.log.warn(f"已删除明细空行 {removed} 行（物料为空，下推多单合并时产生）")
        return removed

    def _delete_detail_row(self, idx):
        """删除明细第 idx 行（真实鼠标选中 → 点「删除行」；失败退回 dataSource.remove）。"""
        # 1) 真实鼠标点该行任一单元格选中它
        try:
            pos = self.detail_cell_pos(idx, "FSEQ")
        except RuntimeError:
            pos = None
        if pos:
            self.page.mouse.click(pos["x"], pos["y"])
            self.page.wait_for_timeout(300)
        # 2) 找工具栏「删除行」按钮
        js = self._INVISIBLE + """() => {
            const cands = Array.from(document.querySelectorAll('a, button, span, li'))
                .filter(vis);
            const btn = cands.find(e => /^(删除行|删除分录|删行)$/.test((e.innerText||'').trim()))
                || document.querySelector('[id*="DeleteRow" i], [id*="DeleteEntry" i]');
            if (!btn) return {err:'no delete button'};
            const r = btn.getBoundingClientRect();
            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
        }"""
        r = eval_all_frames(self.page, js)
        if isinstance(r, dict) and not r.get("err"):
            self.page.mouse.click(r["x"], r["y"])
            self.page.wait_for_timeout(800)
            # 删除常弹确认框，点「确定」
            self._confirm_delete_if_any()
            return True
        return False

    def _confirm_delete_if_any(self):
        """删行后若弹出「确认删除」对话框，点确定（真实鼠标）。"""
        js = self._INVISIBLE + """() => {
            const wins = Array.from(document.querySelectorAll('.k-window')).filter(vis);
            const w = wins.find(x => /(删除|确定要|确认)/.test(x.innerText || ''));
            if (!w) return null;
            const ok = Array.from(w.querySelectorAll('a, button')).filter(vis)
                .find(x => (x.innerText||'').trim() === '确定');
            if (!ok) return null;
            const r = ok.getBoundingClientRect();
            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
        }"""
        r = eval_all_frames(self.page, js)
        if isinstance(r, dict) and r.get("x") is not None:
            self.page.mouse.click(r["x"], r["y"])
            self.page.wait_for_timeout(500)

    def detail_amounts(self, kind="receivable"):
        """读明细各行的金额，返回 [{seq, all, notax, tax, discount, raw}]（Decimal）。"""
        from discount import to_d
        f = self.amount_fields(kind)
        out = []
        for row in self.detail_rows():
            out.append({
                "seq": row.get("FSEQ", ""),
                "all": to_d(row.get(f["all"])),
                "notax": to_d(row.get(f["notax"])),
                "tax": to_d(row.get(f["tax"])),
                "discount": to_d(row.get(f["discount"])) if f.get("discount") else None,
                "raw": row,
            })
        return out

    def detail_summary(self, kind="receivable", **kw):
        """汇总明细 4 个合计并与目标比差（Decimal）。"""
        from discount import summarize
        rows = self.detail_amounts(kind)
        return summarize(rows, all_field="all", disc_field="discount", **kw)

    # ---- 写 ----

    def detail_cell_pos(self, idx, field):
        """定位明细第 idx 行 field 列的坐标（会先 scrollIntoView）。"""
        js = self._INVISIBLE + """(a) => {
            const [idx, fld] = a;
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return {err: 'no visible grid'};
            const tr = Array.from(g.querySelectorAll('tbody tr'))[idx];
            if (!tr) return {err: 'no row ' + idx};
            const td = Array.from(tr.querySelectorAll('td'))
                .find(t => t.querySelector('[data-field="' + fld + '"]'));
            if (!td) return {err: 'no cell ' + fld};
            td.scrollIntoView({block: 'center'});
            const r = td.getBoundingClientRect();
            return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
        }"""
        r = eval_all_frames(self.page, js, [idx, field])
        if not isinstance(r, dict) or r.get("err"):
            raise RuntimeError(f"定位明细单元格失败 idx={idx} field={field}：{r}")
        return r

    def detail_set_cell(self, idx, field, value, settle_ms=1500):
        """双击明细单元格 → 全选 → 输入 → 回车。value 用字符串（保留 2 位小数）。"""
        self.page.wait_for_timeout(300)
        pos = self.detail_cell_pos(idx, field)
        self.page.mouse.dblclick(pos["x"], pos["y"])
        self.page.wait_for_timeout(400)
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Delete")
        self.page.keyboard.type(str(value), delay=25)
        self.page.wait_for_timeout(150)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(settle_ms)

    def detail_blur(self, settle_ms=2000):
        """把焦点移开（点物料名称格），触发联动列重算。

        ★ 实测坑：明细单元格编辑后如果不移开焦点，联动列（如税额）
          在 DOM 里**读出来是空的**，会误判成"没填上"。
        """
        try:
            pos = self.detail_cell_pos(0, self._BLUR_FIELD)
        except RuntimeError:
            self.page.mouse.click(700, 583)
        else:
            self.page.mouse.click(pos["x"], pos["y"])
        self.page.wait_for_timeout(settle_ms)

    # ---- 业务：分摊折扣 + 闭环对齐 ----

    def detail_fill_discounts(self, discs, kind="receivable", skip_equal=True,
                              progress_every=10):
        """把逐行折扣额写进明细网格。discs = [Decimal/int/float, ...] 按行序。

        返回实际写入的行数。
        """
        from discount import to_d
        f = self.amount_fields(kind)
        if not f.get("discount"):
            raise KeyError(f"kind={kind!r} 没有折扣额字段，无法分摊")
        cur = self.detail_amounts(kind)
        if len(cur) != len(discs):
            raise RuntimeError(f"行数不匹配：页面 {len(cur)} 行，待填 {len(discs)} 行")
        written = 0
        for i, want in enumerate(discs):
            want = to_d(want)
            if skip_equal and cur[i]["discount"] is not None \
                    and abs(cur[i]["discount"] - want) < to_d("0.005") and cur[i]["discount"] > 0:
                continue
            self.detail_set_cell(i, f["discount"], f"{want:.2f}")
            written += 1
            if progress_every and written % progress_every == 0:
                self.log.info(f"  折扣额已填 {i + 1}/{len(discs)}")
        self.detail_blur()
        self.log.info(f"折扣额填写完成：写入 {written} 行（共 {len(discs)} 行）")

        # ★★ 2026-10-03 新增：**写完必须回读校验，不一致就重填，还不一致就抛错**。
        #   实测（浙江至优 AR105-202610-0007，本批第 13 家）：
        #     日志明明说「写入 16 行（共 16 行）」，但服务端回读第 **15、16 行折扣额 = 0.00**
        #     （这两行正好是**最后一个来源单**的末两行）→ 折扣少填 **765.00**
        #     → 整单价税合计比 Excel 目标多 765.00。
        #   而下游只把"金额对齐不达标"打成一条 WARN，**然后照样保存**（SF-01 的变体）
        #     → 账就这么静默做错了。
        #   成因：明细网格是**虚拟滚动**的，末几行可能还没渲染/单元格没提交，
        #     `detail_set_cell` 不报错但值没落进去。
        #   ⇒ 所以"写"之后一定要"读回来对"——**判据永远在回读这一侧**。
        f_d = f["discount"]
        tol = to_d("0.005")
        bad = []
        for rd in range(1, 4):
            cur2 = self.detail_amounts(kind)
            if len(cur2) != len(discs):
                raise RuntimeError(f"行数不匹配（回读校验）：页面 {len(cur2)} 行，"
                                   f"待填 {len(discs)} 行")
            bad = [i for i, want in enumerate(discs)
                   if abs((cur2[i].get("discount") or to_d(0)) - to_d(want)) >= tol]
            if not bad:
                self.log.info(f"折扣额回读校验通过：{len(discs)} 行全部一致")
                return written
            self.log.warn(f"折扣额回读不一致 {len(bad)} 行（第 "
                          f"{[i + 1 for i in bad]} 行）→ 第 {rd} 轮重填")
            for i in bad:
                self.detail_set_cell(i, f_d, f"{to_d(discs[i]):.2f}")
            self.detail_blur()
        cur2 = self.detail_amounts(kind)
        raise RuntimeError(
            f"折扣额回填**始终写不进去**：第 {[i + 1 for i in bad]} 行不符"
            f"（期望 {[str(to_d(discs[i])) for i in bad]}，"
            f"实际 {[cur2[i].get('discount') for i in bad]}）"
            f" —— **已抛错停止，绝不带着错金额去保存**")

    def fill_discount_by_ratio(self, total_discount, kind="receivable"):
        """★ 主入口：把扣款 total_discount 按行含税金额占比分摊到明细折扣额。

        = discount.allocate_by_amount + detail_fill_discounts
        返回 (discs, info)：每行折扣额 + 诊断信息（含锚点行 / 尾差）。
        """
        from discount import allocate_by_amount, to_d
        rows = self.detail_amounts(kind)
        if not rows:
            raise RuntimeError("明细网格没有行，无法分摊折扣")
        amounts = [r["all"] for r in rows]
        discs, info = allocate_by_amount(amounts, to_d(total_discount))
        self.log.info(
            f"折扣分摊（按含税占比）：扣款 {info['want']:.2f}  含税合计 {info['base']:.2f}  "
            f"{len(amounts)} 行  → 逐行舍入合计 {info['sum_before_fix']:.2f}"
            + (f"  尾差 {info['gap']:+.2f} 已补到最大行 seq={rows[info['anchor']]['seq']}"
               if info["anchor_fixed"] else ""))
        self.detail_fill_discounts(discs, kind=kind)
        return discs, info

    def detail_align_totals(self, target_notax=None, target_tax=None,
                            target_all=None, kind="receivable", tol="0.00",
                            max_rounds=4, anchor=None):
        """★ 闭环对齐：把总不含税 / 总税额 / 总价税合计顶到目标值（默认落在金额最大的那行）。

        每一步都**读页面实际合计**再算差，所以对金蝶的取整怪癖免疫。
        写顺序有讲究：先写不含税（不动税额），再写税额（不动不含税），
        最后价税合计自然 = 两者之和。

        ★ 2026-10-03（用户口径「三个数都精确到分」）：
          新增 `target_all`。价税合计在行内是「不含税 + 税额」的派生值，**不能直接改**，
          所以这里只在「不含税、税额都对齐后」读页面价税合计做**交叉校验**：
          若 notax/tax 都对了而 all 仍差，说明表里「不含税+税额 ≠ 价税合计」自相矛盾，
          或金蝶行级舍入累积了尾差 —— 此时靠改锚点行不含税把 all 也顶到位
          （改不含税会同时带动 all，不破坏税额）。

        ⚠️⚠️ 符号约定（2026-09-21 实测踩坑）：
           `summarize()` 给的 `d_notax` / `d_tax` / `d_all` 是 **「当前 − 目标」**，
           所以写入值必须是 `当前值 − d`，**不是 + d**。
           早期实现写成 `+ d` → 越修越远（每轮偏差翻倍：+0.02 → +0.04 → +0.08 …），
           4 轮下来直接跑飞。`plan_alignment()` 里的 `diff` 是「目标 − 当前」，
           两者符号相反，别混用。

        返回最终 detail_summary()。不达标会 warn（不抛异常，便于人工复核）；
        但**写入零生效（无进展）会提前停止**，不再空转满 max_rounds（SF-54）。

        ★ 2026-10-03 加固（SF-54）：
          · `_fix()` 写完**回读该锚点行**，没落到位立刻 warn（写动作不抛异常 ≠ 值进去了）；
          · 循环里比对**相邻两轮的合计快照**，一模一样 ⇒ 写入未生效 ⇒ 提前 return。
        """
        from discount import to_d, summarize, pick_anchor_row
        f = self.amount_fields(kind)
        t_notax = to_d(target_notax) if target_notax is not None else None
        t_tax = to_d(target_tax) if target_tax is not None else None
        t_all = to_d(target_all) if target_all is not None else None
        tol_d = to_d(tol)

        def _fix(rows, s, key, fld):
            """按「当前 − 目标」的反向修正锚点行。

            返回 (wrote, ok, rows_after)：
              wrote      —— 是否真的写了
              ok         —— **写后回读**是否落到位（SF-54）
              rows_after —— 写后回读的明细，供调用方复用（省一次全量读）
            """
            if key not in s or abs(s[key]) <= tol_d:
                return False, False, None
            ai = pick_anchor_row([r["all"] for r in rows]) if anchor is None else anchor
            field_key = key.replace("d_", "")
            cur = rows[ai][field_key]
            want = cur - s[key]          # ← 减，不是加
            self.log.info(f"  锚点 seq={rows[ai]['seq']} {fld} {cur:.2f} → {want:.2f}"
                          f"（当前合计偏差 {s[key]:+.2f}）")
            self.detail_set_cell(ai, f[fld], f"{want:.2f}")
            self.detail_blur()

            # ★★ 2026-10-03 新增（SF-54）：**写后必须回读**。
            #   实测浙江至优 AR105-202610-0007：这里连续 4 轮写「12761.95 → 11850.47」，
            #   每轮读回来**还是 12761.95** —— 写入零生效，却没人察觉，
            #   最后只打一条 WARN 就照常保存。判据永远在"读回来"这一侧。
            after = self.detail_amounts(kind)
            got = after[ai][field_key] if ai < len(after) else None
            # ★ 2026-10-03 修复：tol 默认 "0.00" 太严 → Decimal 减法产生 1e-10 级残留，
            #   即使「期望==读回」（值其实写对了）也误报「未生效」。
            #   改用 0.005（半分）容差：金额对齐本来就是精确到分，半分内即视为一致。
            ok = got is not None and abs(got - want) < to_d("0.005")
            if not ok:
                self.log.warn(
                    f"  锚点写入**未生效**：期望 {want:.2f}，读回 "
                    + ("读不到该行" if got is None else f"{got:.2f}")
                    + "（SF-54：写动作不抛异常 ≠ 值进去了）")
            return True, ok, after

        prev_sig = None
        for rnd in range(1, max_rounds + 1):
            rows = self.detail_amounts(kind)
            s = summarize(rows, all_field="all", disc_field="discount",
                          target_notax=t_notax, target_tax=t_tax,
                          target_all=t_all, tol=tol_d)
            self.log.info(
                f"[对齐第 {rnd} 轮] 不含税 {s['notax']:.2f}"
                + (f"({s.get('d_notax'):+.2f})" if "d_notax" in s else "")
                + f"  税额 {s['tax']:.2f}"
                + (f"({s.get('d_tax'):+.2f})" if "d_tax" in s else "")
                + f"  价税合计 {s['all']:.2f}"
                + (f"({s.get('d_all'):+.2f})" if "d_all" in s else ""))
            if s["ok"]:
                self.log.ok(f"金额对齐完成（第 {rnd} 轮达标：三个数均精确到分）")
                return s

            # ★ 2026-10-03 新增（SF-54）：**无进展即早停**。
            #   本轮读到的合计与上一轮**一模一样** ⇒ 上一轮的两次写入都没生效，
            #   再跑下去只会重复同一个无效动作。实测浙江至优就是空转 4 轮
            #   （每轮都读到 85780.98 / 11151.52），白花约 40 秒 + 8 次点击。
            #   宁可立刻交出 s（ok=False，由调用方 raise 拦住保存）。
            sig = (str(s["notax"]), str(s["tax"]), str(s["all"]))
            if prev_sig is not None and sig == prev_sig:
                self.log.warn(
                    f"对齐无进展：第 {rnd} 轮与上一轮合计**完全相同**"
                    f"（不含税 {s['notax']:.2f} / 税额 {s['tax']:.2f}"
                    f" / 价税 {s['all']:.2f}）"
                    f" → 判定写入未生效（SF-54），提前停止，不再空转")
                return s
            prev_sig = sig

            # ★ 价税合计交叉校验（2026-10-03）：金蝶行内 all = notax + tax 恒成立，
            #   故 d_all ≡ d_notax + d_tax。若 notax/tax 都对齐了而 all 仍差，
            #   只能是「表里 不含税+税额 ≠ 价税合计」自相矛盾 → 不硬改，交给下方
            #   `s["ok"]=False` 让 step_receivable 的护栏 raise 拦下（避免越改越乱）。
            _, _, after_a = _fix(rows, s, "d_notax", "notax")
            # 写不含税后税额本身不受影响，但**重新读一次**更安全（不依赖这个前提）
            rows = after_a if after_a is not None else self.detail_amounts(kind)
            s = summarize(rows, all_field="all", disc_field="discount",
                          target_notax=t_notax, target_tax=t_tax,
                          target_all=t_all, tol=tol_d)
            if s["ok"]:
                self.log.ok(f"金额对齐完成（第 {rnd} 轮，写完不含税即达标）")
                return s
            _fix(rows, s, "d_tax", "tax")

        rows = self.detail_amounts(kind)
        s = summarize(rows, all_field="all", disc_field="discount",
                      target_notax=t_notax, target_tax=t_tax,
                      target_all=t_all, tol=tol_d)
        self.log.warn(f"对齐 {max_rounds} 轮仍未完全达标："
                      f"不含税 {s['notax']:.2f}({s.get('d_notax')}) "
                      f"税额 {s['tax']:.2f}({s.get('d_tax')}) "
                      f"价税 {s['all']:.2f}({s.get('d_all')})，请人工复核")
        return s

    def backfill_invoice_info(self, drawer, biz_date, invoice_date, checker=None):
        """
        填开票人 / 复核人 / 业务日期 / 发票日期
        （票在电子税务局已开好 → 金蝶为事后补录；发票号码不填）
        业务日期优先字段名 FDATE 精确命中，失败再退化为标签匹配。
        """
        # 实测（2026-09-18）销售增值税专用发票 - 新增：
        #   FDRAWER 开票人 —— 会自动取登录用户，已是「蒋一天」
        #   F_PAEZ_COMBO 复核人 —— kdcombo 下拉，必填，下推时默认带出「姚玉平」
        #   FDATE   业务日期 —— 下推后默认是【今天】，必须改
        #   FINVOICEDATE 发票日期 —— 会自带应收单的业务日期
        #   发票号 FINVOICENO / 发票号码 FIVNUMBER 都不填
        if biz_date:
            try:
                self.fill_bill_field("FDATE", biz_date)
            except Exception:
                self.log.warn("FDATE 精确命中失败，退化为标签匹配")
                self.fill_field_by_label("业务日期", biz_date)
        if invoice_date:
            try:
                self.fill_bill_field("FINVOICEDATE", invoice_date)
            except Exception:
                self.fill_field_by_label("发票日期", invoice_date)
        if drawer:
            cur = (self.read_bill_fields(["FDRAWER"]) or {}).get("FDRAWER")
            if cur and str(cur).strip() == str(drawer).strip():
                self.log.info(f"开票人已是「{drawer}」，无需修改")
            else:
                try:
                    self.fill_bill_field("FDRAWER", drawer)
                except Exception:
                    self.fill_field_by_label("开票人", drawer)
        if checker:
            # 复核人 = kdcombo 下拉（F_PAEZ_COMBO），必填。
            # ★ 2026-10-03：**界面已经显示目标值就直接放过**，不再重选。
            #   下推时金蝶通常已默认带出「姚玉平」；此时"再点选一次"不但没必要，
            #   还是整链最脆弱的一步（下拉动画漂移 + 后台表单同名下拉，
            #   见 `_VIS_STRONG` / `pick_combo_option` 的实测记录）。
            #   若模型真的没同步，保存时服务端会报「复核人必填」——不会静默出错。
            cur = (self.read_combo("F_PAEZ_COMBO") or {}).get("text")
            if cur and str(cur).strip() == str(checker).strip():
                self.log.info(f"复核人已是「{checker}」（界面已带出），跳过重选")
            else:
                self.log.info(f"复核人当前为 {cur!r} → 重选为「{checker}」")
                self.fill_combo("F_PAEZ_COMBO", checker)

    # ---------- 步骤 3-4：凭证 + 摘要 ----------
    # ---------- 步骤 3：生成凭证（2026-09-21 实测跑通入口） ----------
    # 用户口径 + 实测：**凭证只能在【应收单列表】里做**，单据详情页没有这个入口。
    #   列表工具栏「凭证」是 split 按钮 (id 尾 tbSplitButtonVch)，点开有三项：
    #     tbVoucherGenerate 生成凭证 / tbViewBizVoucher 查看业务凭证 / tbViewGlVoucher 查看总账凭证
    #   → 点「生成凭证」弹出「凭证生成」对话框：
    #       网格列：账簿 | 期间 | 执行方式 | 总账凭证生成方式 | 匹配字段
    #       默认一行：杭州天龙油墨有限公司 | 2026.9 | 单据同时生成业务和总账凭证 | 匹配汇总 | 客户
    #       底部复选框：按单据日期获取期间 / 按过滤方案获取单据
    #       ★ 必须【先勾选账簿行】，否则点生成只报「请选择账簿！」（服务端 ShowWarnningMsg）
    #       最终按钮：a[id$="-FVOUCHERGENE_c"]
    #   → 生成后自动关对话框并打开「凭证生成报告列表」(BAS_VchBuildReport)
    VOUCHER_GEN_BTN = 'a[id$="-FVOUCHERGENE_c"]'

    def _voucher_dialog(self):
        """返回可见的「凭证生成」对话框元素信息（JS 内用）"""
        return """Array.from(document.querySelectorAll('.k-window')).filter(vis)
                    .find(w => (w.innerText||'').includes('凭证生成'))"""

    def open_voucher_dialog(self):
        """列表页 → 凭证 → 生成凭证，打开对话框"""
        self.click_menu("凭证", timeout_ms=2500)
        self.click_menu("生成凭证", timeout_ms=5000)
        win = eval_all_frames(self.page, self._INVISIBLE + f"""() => {{
            const w = {self._voucher_dialog()};
            return w ? (w.innerText||'').trim().replace(/\\s+/g,' ').slice(0,200) : null;
        }}""")
        if not win:
            raise RuntimeError("点「生成凭证」后没出现「凭证生成」对话框")
        self.log.info(f"凭证生成对话框已打开：{win[:120]}")
        return win

    def check_voucher_book_row(self, idx=0):
        """勾选「凭证生成」对话框里的账簿行（不勾 → 点生成会报「请选择账簿！」）"""
        js = self._INVISIBLE + """(i) => {
            const w = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                        .find(x => (x.innerText||'').includes('凭证生成'));
            if (!w) return {err:'no voucher dialog'};
            const g = Array.from(w.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return {err:'no grid in dialog'};
            const trs = Array.from(g.querySelectorAll('tbody tr'));
            if (i >= trs.length) return {err:'row out of range', n: trs.length};
            const tr = trs[i];
            const cb = tr.querySelector('input.gridCheck:not(.gridCheckHeader)')
                    || tr.querySelector('input[type=checkbox]');
            if (!cb) return {err:'no checkbox'};
            if (cb.checked) return {ok:true, already:true,
                row: Array.from(tr.querySelectorAll('td')).map(td=>(td.innerText||'').trim()).filter(Boolean).slice(0,5)};
            const lab = cb.nextElementSibling;
            const target = (lab && lab.classList.contains('k-checkbox-label')) ? lab : cb;
            ['mousedown','mouseup','click'].forEach(t =>
                target.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window})));
            return {ok:true, checkedNow:cb.checked,
                row: Array.from(tr.querySelectorAll('td')).map(td=>(td.innerText||'').trim()).filter(Boolean).slice(0,5)};
        }"""
        r = eval_all_frames(self.page, js, idx)
        if not isinstance(r, dict) or r.get("err"):
            raise RuntimeError(f"勾选账簿行失败：{r}")
        self.page.wait_for_timeout(1200)
        if not r.get("checkedNow", True):
            raise RuntimeError(f"账簿行没勾上：{r}")
        self.log.info(f"已勾选账簿行：{r.get('row')}")
        return r

    def generate_voucher(self, click_final=True):
        """
        完整生成凭证：开对话框 → 勾账簿行 → 点「凭证生成」。
        ⚠️ 只能在【应收单列表】页调用（详情页无此入口）。
        click_final=False 时停在勾完账簿、未点最终按钮的状态。
        """
        self.guard.check("voucher", "生成凭证会写入总账，且不勾账簿会报错")
        self.open_voucher_dialog()
        self.check_voucher_book_row()
        if not click_final:
            self.log.info("已停在凭证生成对话框（未点最终按钮）")
            return True
        btn = self.page.locator(f'{self.VOUCHER_GEN_BTN}:visible').first
        if btn.count() == 0:
            raise RuntimeError("找不到「凭证生成」按钮")
        btn.click(timeout=10000)
        self.page.wait_for_timeout(10000)
        self.log.info("已点「凭证生成」，等待报告")
        return True

    def read_voucher_report(self):
        """
        读「凭证生成报告列表」的内容（生成后会自动打开这个列表）。
        返回 [{单据编号, 检查项, 提示类型, 提示信息, ...}]

        ⚠️ 2026-10-04 重写（两轮踩坑）：
        - 这个报告列表**没有 thead/th**，数据单元格是 `span[data-field]`，`data-field`
          就是字段名（FSOURCEBILLNO/FCHECKITEM/FERRLEVEL/FMESSAGE/...）。
        - 生成凭证后「凭证生成报告列表」可能还**没切到前台**，`.filter(vis)[0]` 会读到
          别的可见表格（销售增值税专用发票列表）→ 漏判。
        ⇒ 现在遍历**所有**表格（含后台），按「行里有 FMESSAGE 字段」定位报告列表表格，
          不依赖可见性 / 标签激活状态。
        """
        js = self._INVISIBLE + """() => {
            const grids = Array.from(document.querySelectorAll('table.k-selectable'));
            for (const g of grids) {
                const row0 = g.querySelector('tbody tr');
                if (!row0) continue;
                // 报告列表特征：行里有 FMESSAGE / FERRLEVEL 字段
                if (!row0.querySelector('span[data-field="FMESSAGE"]')) continue;
                const rows = Array.from(g.querySelectorAll('tbody tr')).map(tr => {
                    const o = {};
                    tr.querySelectorAll('span[data-field]').forEach(s => {
                        o[s.getAttribute('data-field')] = (s.innerText||'').trim();
                    });
                    return o;
                });
                return { rows };
            }
            return null;
        }"""
        r = eval_all_frames(self.page, js)
        if not isinstance(r, dict):
            return None
        out = []
        for row in r.get("rows") or []:
            rec = {
                "单据编号": row.get("FSOURCEBILLNO", ""),
                "检查项": row.get("FCHECKITEM", ""),
                "提示类型": row.get("FERRLEVEL", ""),
                "提示信息": row.get("FMESSAGE", ""),
                "期间": f"{row.get('FYEAR','')}.{row.get('FPERIOD','')}",
            }
            rec.update(row)
            out.append(rec)
        return out

    def voucher_report_errors(self):
        """挑出凭证生成失败的行。

        ⚠️ 2026-10-04 修正：金蝶对「凭证生成失败（模板期间不对）」给的提示类型是
        **「警告」** 而不是「错误」，过去只认「错误」导致把失败误判成「0 错误→成功」，
        继续往下跑，最后在「查看业务凭证」时勾不到行报 out of range（静默漏单）。
        ⇒ 现在「错误」，或任意字段值里含「凭证生成失败」，都算失败。
        """
        rep = self.read_voucher_report() or []
        out = []
        for r in rep:
            t = str(r.get("提示类型", ""))
            msg = str(r.get("提示信息", ""))
            if "错误" in t:
                out.append(r)
            elif "凭证生成失败" in msg or "凭证生成失败" in str(r.values()):
                out.append(r)
        return out

    # ---------- 凭证分录：改摘要（2026-09-21 实测打通） ----------
    #
    # 入口只有一条，认准标签名「凭证 - 修改」：
    #   销售增值税专用发票列表 → 勾发票 → 凭证(split) → 生成凭证
    #     → 对话框【必须先勾账簿行】→ 凭证生成 → 弹「凭证生成报告列表」
    #   报告列表 → 勾行 →「查看业务凭证」→ 业务凭证列表
    #   业务凭证列表 → 勾行 →「查看总账凭证」→ 标签「凭证 - 修改」★ 这里改摘要
    #
    # 反面教材（都实测过，改不了，别再试）：
    #   - 业务凭证自己的详情页（标签「业务凭证 - 查看」）：摘要格 class 带
    #     k-gridCellstate-disabled，单击 / F2 / 直接键入都进不了编辑态，只读
    #   - 在详情页按 Esc 会**直接关掉当前页**（不是取消编辑），别拿它当撤销
    #
    # 可编辑判定：双击摘要格 → 生成 INPUT id = "{pageId}-FENTITY_FEXPLANATION_c"
    # 分录字段（span[data-field]）：
    #   FENTRYSEQ 序号 / FEXPLANATION 摘要 / FACCOUNTID 科目编码 /
    #   FACCTFULLNAME 科目全名 / FDETAILID 核算维度 / FDEBIT 借方 / FCREDIT 贷方
    BIZVCH_BTN = "查看业务凭证"      # 凭证生成报告列表 → 业务凭证列表
    GLVCH_BTN = "查看总账凭证"       # 业务凭证列表 → 凭证 - 修改
    DIGEST_INPUT = '[id$="-FENTITY_FEXPLANATION_c"]'

    def active_tab(self):
        """当前激活的页签名（金蝶每个打开过的页面都是一个 .k-item，只有一个是 active）"""
        js = self._INVISIBLE + """() => Array.from(document.querySelectorAll('.k-item.k-state-active'))
            .filter(vis).map(e => (e.innerText||'').trim()).filter(Boolean)[0] || null"""
        return eval_all_frames(self.page, js)

    def expect_tab(self, name, timeout_ms=15000):
        """等当前激活标签名包含 name（金蝶切页是异步的，不能点完就查）"""
        t0 = time.time()
        while (time.time() - t0) * 1000 < timeout_ms:
            t = self.active_tab() or ""
            if name in t:
                return t
            self.page.wait_for_timeout(400)
        raise RuntimeError(f"等标签 {name!r} 超时，当前 = {self.active_tab()!r}")

    # ---------- ★ 标签页清理（2026-09-21） ----------
    # 为什么要清：金蝶 SPA 把每个开过的页面 DOM 都留在 document 里，
    #   开得越多，`document.querySelector*` 命中隐藏页面的概率越高
    #   （工具栏按钮、列表 grid、快捷过滤面板、分页栏都栽过）。
    #   每次下推链开始前关掉所有标签 = 把污染源清干净。
    #
    # ★★ 定位坑：`.k-item` 不只匹配标签页！工具栏的每个菜单项
    #    (新增/保存/提交…) 也是 `li.k-item.kdmenuitem`，
    #    直接 `querySelectorAll('.k-item')` 会捞到 20+ 个假标签。
    #    真正的标签页 = `ul.k-tabstrip-items` 下的 `.k-item.contentHeadEL`。
    def tabs(self):
        """
        当前所有标签页（只算真标签，不含工具栏菜单项）。
        返回 [{i, text, active, cx, cy, tx, ty}]，
        cx/cy = 关闭按钮中心坐标；tx/ty = 标签本体（文字区）坐标，用于切页。
        """
        js = self._INVISIBLE + """() => {
            // ★★ 主标签栏选择器走单一真相源 kd_tab_sel.MAIN_TAB_SELECTOR（SF-51）。
            const strip = document.querySelector('__MAIN_TAB__')
                       || Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0]
                       || Array.from(document.querySelectorAll('.k-tabstrip-items')).filter(vis)[0];
            if (!strip) return null;
            return Array.from(strip.querySelectorAll('.k-item.contentHeadEL')).filter(vis)
                .map((e, i) => {
                    const c = e.querySelector('.mainTabCloseButton, .k-i-close');
                    const rc = c ? c.getBoundingClientRect() : null;
                    const r = e.getBoundingClientRect();
                    return { i,
                             id: e.id || null,
                             text: (e.innerText||'').trim().replace(/\\s+/g,' ').slice(0, 30),
                             active: e.className.toString().includes('k-state-active'),
                             cx: rc ? Math.round(rc.x + rc.width/2) : null,
                             cy: rc ? Math.round(rc.y + rc.height/2) : null,
                             // ⚠️ 2026-10-03：`hit` = ✕ 坐标是否在视口内且 elementFromPoint 命中。
                             //   **仅供 probe_tab_hit 诊断，禁止拿它做"能不能关"的过滤依据！**
                             //   实测：8 个坐标完好（全在视口内）的标签会**全部**判成 false ——
                             //   因为 Kendo 的 ✕ 是 hover 才显形的，而 elementFromPoint 求值时没有 hover。
                             //   真实鼠标点击会先 mousemove（触发 hover）再按下，所以点得到。
                             //   拿它过滤 → 能关的被跳掉 → 占单标签漏关 → 整链卡在"单据正在被 XX 使用"。
                             hit: (() => {
                                 if (!rc) return false;
                                 const x = rc.x + rc.width / 2, y = rc.y + rc.height / 2;
                                 if (x < 1 || y < 1 || x > innerWidth - 1 || y > innerHeight - 1) return false;
                                 const el = document.elementFromPoint(x, y);
                                 if (!el) return false;
                                 return el === c || c.contains(el) || el.contains(c);
                             })(),
                             tx: Math.round(r.x + Math.min(40, r.width / 3)),
                             ty: Math.round(r.y + r.height / 2) };
                });
        }"""
        js = js.replace("__MAIN_TAB__", MAIN_TAB_SELECTOR)
        return eval_all_frames(self.page, js) or []

    def activate_tab(self, name, timeout_ms=8000):
        """切到名字含 name 的标签页（真实鼠标点标签本体）"""
        t0 = time.time()
        while (time.time() - t0) * 1000 < timeout_ms:
            tabs = self.tabs()
            if tabs:
                cur = next((t for t in tabs if t["active"]), None)
                if cur and name in (cur["text"] or ""):
                    return cur["text"]
                tgt = next((t for t in tabs if name in (t["text"] or "")), None)
                if tgt and tgt.get("tx") is not None:
                    self.page.mouse.click(tgt["tx"], tgt["ty"])
                    self.page.wait_for_timeout(1200)
                    continue
            self.page.wait_for_timeout(400)
        raise RuntimeError(f"切不到标签 {name!r}，当前 = {[t['text'] for t in self.tabs()]}")

    def _dismiss_close_confirm(self):
        """
        关标签时若弹「有未保存的修改，是否关闭」→ 点「确定」。
        ⚠️ 只在弹窗文案命中关闭/放弃/未保存时才点，避免误点到业务对话框。
        """
        js = self._INVISIBLE + """() => {
            const w = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .find(x => /关闭|放弃|未保存|不保存/.test(x.innerText||''));
            if (!w) return null;
            const b = Array.from(w.querySelectorAll('a,button')).filter(vis)
                .find(x => /^(确定|是|关闭|放弃)$/.test((x.innerText||'').trim()));
            if (!b) return null;
            const t = (w.innerText||'').trim().replace(/\\s+/g,' ').slice(0, 100);
            b.click();
            return t;
        }"""
        r = eval_all_frames(self.page, js)
        if r:
            self.log.info(f"关标签确认弹窗：{r}")
            self.page.wait_for_timeout(1200)
        return r

    def close_tab(self, t):
        """关标签。

        ★ 2026-10-03 关键修复：**必须用 `locator.click()`，不能用 `page.mouse.click(cx, cy)`**。

        实测（`src/probe_close_ways.py`，同一个 ✕ 上逐种试）：
          · `locator.click()`                   → ✓ 关掉
          · `mouse.click(提前取好的坐标)`         → ✗ 静默无效（不报错、不弹窗）
          · `locator.click(force=True)`          → ✗
          · `dispatch_event('click')` / JS click → ✗
        根因：鼠标移上去后标签进入 hover 态、**宽度会变**，✕ 位置随之漂移；
        `locator.click()` 会先等元素**位置稳定**再重新算坐标，所以点得中，
        而 `mouse.click(cx, cy)` 用的是漂移**之前**的旧坐标，必然点空。
        这就是"一直在点删除、标签却删不掉"的真因 —— 连带金蝶自己的
        「关闭其他页签」看起来也没反应（其实是菜单项同样点空）。

        定位用元素 id（`li_xxxFMAINTAB_c`），比文字可靠（同名标签可以有几十个）。
        """
        if t.get("cx") is None and not t.get("id"):
            raise RuntimeError(f"标签 {t.get('text')!r} 没有关闭按钮，无法关闭")
        clicked = False
        if t.get("id"):
            try:
                self.page.locator(f'[id="{t["id"]}"] span.mainTabCloseButton').first.click(timeout=6000)
                clicked = True
            except Exception as e:  # noqa: BLE001
                self.log.warn(f"locator 关标签失败（{t.get('text')!r}）："
                              f"{type(e).__name__}: {str(e)[:90]}")
        if not clicked:
            if t.get("cx") is None:
                raise RuntimeError(f"标签 {t.get('text')!r} 坐标与 id 都取不到")
            self.page.mouse.click(t["cx"], t["cy"])   # 兜底（坐标可能已漂移）
        self.page.wait_for_timeout(700)
        self._dismiss_close_confirm()
        return True

    def _force_remove_tab(self, t):
        """孤儿标签的**最后手段** —— 走主标签栏自己的 widget API 摘除它。

        ★ 为什么 `close_tab()` 会「点 ✕ 静默无响应」（2026-10-03 定案，SF-51 收敛）：
          主标签栏（`ul.kdMainTabsHeaderExtend`）的数据**没有**绑在 `kendoTabStrip` 上，
          金蝶在同一个 `.k-widget` 根上注册的是**自研控件 `kendomainTabControl`**
          （Kendo TabStrip 的子类，有 `items/remove/dataSource/activateTab`，
           但 `data()` 键名不同）。于是：
            · 任何 `data('kendoTabStrip')` 的尝试**必然拿不到它**（实测 14 个 tabstrip 里
              主栏是**唯一** `withWidget=False` 的，另外 13 个都是单据表单内的页组）；
            · ✕ 的关闭事件挂在已失效的内部 tab 对象上 → 点了不报错也不关；
            · 连金蝶自带的右键「关闭其他页签」同样失效（走的是同一条死路）。
          实测（`src/probe_tab_kill.py --go`）：`ctl.remove($(li))` **6/6 摘除成功**，
          标签数 7 → 1，保留锚点「销售出库单列表」完好无损。

        只操作 tabstrip 的 DOM，**不碰任何单据数据**（不保存/不提交/不删除）。
        返回 True 只表示"摘除请求发出去了"，**是否真摘掉必须由调用方回读标签列表判定**。
        """
        if not t.get("id"):
            return False
        js = self._INVISIBLE + """() => {
            const id = __TAB_ID__;
            // ★★ 主标签栏选择器走单一真相源 kd_tab_sel.MAIN_TAB_SELECTOR（SF-51）。
            const strip = document.querySelector('__MAIN_TAB__')
                       || Array.from(document.querySelectorAll('ul.k-tabstrip-items')).filter(vis)[0]
                       || Array.from(document.querySelectorAll('.k-tabstrip-items')).filter(vis)[0];
            if (!strip) return false;
            const $ = window.jQuery || window.$;
            if (!$) return false;
            const root = $(strip).closest('.k-widget')[0];
            const ctl = $(root).data('__WIDGET_KEY__');
            if (!ctl || typeof ctl.remove !== 'function') return false;
            let el = null;
            try { el = strip.querySelector('#' + CSS.escape(id)); } catch (e) { el = null; }
            if (!el) return false;
            try { ctl.remove($(el)); } catch (e) { return false; }
            return true;
        }"""
        js = js.replace("__TAB_ID__", json.dumps(t["id"]))
        js = js.replace("__MAIN_TAB__", MAIN_TAB_SELECTOR)
        js = js.replace("__WIDGET_KEY__", WIDGET_KEY)
        try:
            return bool(self.page.evaluate(js))
        except Exception as e:  # noqa: BLE001
            self.log.warn(f"kendomainTabControl 摘标签失败：{type(e).__name__}: {str(e)[:80]}")
            return False

    # ★★ 2026-10-29（河南华洋事故后补）：**页签配额自愈**。
    #   配额真值 = 主标签栏控件 `kendomainTabControl.items().length`（含零宽幽灵 li），
    #   不是 `tabs()` 数的可见标签数 —— 实测 items()=4 时 tabs()=2。
    #   攒到 15 时金蝶弹「超出了参数控制打开页签数的上限15个」，**之后任何功能页都打不开**，
    #   而且点搜索条目既不报错也不生效（正是华洋那单的现场）。
    #   唯一能真正释放配额的手段 = `ctl.remove($(li))`（点 logo 回主页不清零，实测 4→4）。
    #   过去这段逻辑只在 run_batch 壳脚本里（deep_clean），单跑 push_chain 时没有 ⇒ 复现。
    JS_QUOTA_COUNT = """() => {
        const $ = window.jQuery;
        const strip = document.querySelector('__MAIN_TAB__');
        if (!strip) return -1;
        const ctl = $(strip).closest('.k-widget').data('__WIDGET_KEY__');
        if (!ctl) return -2;
        try { return ctl.items().length; } catch (e) { return -3; }
    }"""

    def tab_quota(self):
        """读金蝶真实页签配额 items().length（含幽灵 li）。读不到返回 -1。"""
        js = (self.JS_QUOTA_COUNT
              .replace("__MAIN_TAB__", MAIN_TAB_SELECTOR)
              .replace("__WIDGET_KEY__", WIDGET_KEY))
        try:
            return int(self.page.evaluate(js))
        except Exception as e:  # noqa: BLE001
            self.log.warn(f"读页签配额失败：{type(e).__name__}: {str(e)[:60]}")
            return -1

    def release_tab_quota(self, keep_names=(), force=False, max_free=100):
        """配额接近上限时用 `ctl.remove()` 摘掉「幽灵 li / 残留表单页」，真正释放配额。

        keep_names: 必须保留的标签（通常是工作锚点，如「销售出库单列表」）
        保留规则：名字命中 keep_names 的、以及所有以「列表」结尾的功能页
                  （列表页重新打开只要几秒，且里面没有未保存数据）。
        返回 (释放前配额, 释放后配额, 摘除个数)。"""
        js = """(args) => {
            const [keep, maxFree] = args;
            const $ = window.jQuery;
            const strip = document.querySelector('__MAIN_TAB__');
            if (!strip) return [-1, -1, 0];
            const ctl = $(strip).closest('.k-widget').data('__WIDGET_KEY__');
            if (!ctl || typeof ctl.remove !== 'function') return [-2, -2, 0];
            const arr = ctl.items();
            const before = arr.length;
            let removed = 0;
            for (let i = arr.length - 1; i >= 0; i--) {
                if (removed >= maxFree) break;
                const li = arr[i];
                const txt = (li.innerText || '').trim().replace(/\\s+/g, ' ');
                const w = (li.getBoundingClientRect ? li.getBoundingClientRect().width : 0);
                const ghost = !txt;                       // 零宽幽灵 li
                const keepIt = keep.some(k => k && txt.includes(k));
                const isList = txt.endsWith('列表');
                if (!ghost && (keepIt || isList)) continue;
                try { ctl.remove($(li)); removed++; } catch (e) {}
            }
            let after = before;
            try { after = ctl.items().length; } catch (e) {}
            return [before, after, removed];
        }"""
        js = js.replace("__MAIN_TAB__", MAIN_TAB_SELECTOR).replace("__WIDGET_KEY__", WIDGET_KEY)
        try:
            before, after, removed = self.page.evaluate(js, [list(keep_names), max_free])
        except Exception as e:  # noqa: BLE001
            self.log.warn(f"释放页签配额失败：{type(e).__name__}: {str(e)[:60]}")
            return -1, -1, 0
        if removed:
            self.page.wait_for_timeout(600)
            self.log.warn(f"页签配额 {before} → {after}（摘除 {removed} 个幽灵/残留页）")
        elif force:
            self.log.warn(f"页签配额 {before}，无可摘除项（保留 {list(keep_names)}）")
        return before, after, removed

    def dismiss_floating_hints(self):
        """
        隐藏**残留浮动提示条** —— 金蝶失败保存/校验后会留一条
        `z-index:10010`、`draggable=true` 的 `.kd-form-collapse-container`，
        浮在右上角，文本形如 `提交：保存：1837971/2 该字段需要您手动查找`。

        ★ 为什么要专门处理它（2026-10-03 实测）：它**正好压住顶部搜索面板的结果列表**
          （结果条目中心 (996,107) 落在它的 rect [991,57,214,58] 内），
          导致"搜到了却点不动"—— 真实鼠标点会被它截走，`locator.click` 报
          `intercepts pointer events`，而剥 `pointer-events` 也没用
          （它下面直接就是 BODY，条目根本不在那个坐标上）。
        做法：只隐藏**文案命中提示特征**的那种，不动正常的表单容器。
        返回被动过的条数。
        """
        js = self._INVISIBLE + """() => {
            const pat = /(该字段需要您手动查找|提交：保存：|保存：提交：)/;
            const hit = Array.from(document.querySelectorAll('.kd-form-collapse-container'))
                .filter(vis).filter(e => pat.test(e.innerText || ''));
            hit.forEach(e => { e.style.display = 'none'; });
            return hit.length;
        }"""
        try:
            n = eval_all_frames(self.page, js) or 0
        except Exception:  # noqa: BLE001
            return 0
        if n:
            self.log.warn(f"隐藏残留浮动提示条 {n} 个（会挡住右上角搜索结果/工具栏）")
        return n

    # ★★ 2026-10-29（河南华洋事故后补）：金蝶「页签上限 15」这类模态框必须先关掉，
    #   否则它会一直挡在页面上，之后所有的功能页都打不开（点搜索结果 = 点空）。
    JS_DISMISS_QUOTA = """() => {
        const vis = e => { const b = e.getBoundingClientRect();
            return b.width > 0 && b.height > 0 && e.offsetParent !== null; };
        const out = [];
        Array.from(document.querySelectorAll('.k-window, .k-dialog, .kd-dialog'))
            .filter(vis).forEach(w => {
                const t = (w.innerText || '');
                if (t.includes('金蝶提示') || t.includes('上限15个')
                    || t.includes('不能打开当前功能页签') || t.includes('页签数')) {
                    const btn = Array.from(w.querySelectorAll('a,button,span'))
                        .find(b => vis(b) && (b.innerText || '').trim() === '确定');
                    if (btn) { btn.click(); out.push(t.replace(/\\s+/g, ' ').slice(0, 60)); }
                }
            });
        return out;
    }"""

    def dismiss_quota_dialog(self):
        """点「确定」关掉「超出了参数控制打开页签数的上限15个」这类模态框。
        返回被关掉的弹窗文案列表（空 = 没有这类弹窗）。"""
        try:
            out = eval_all_frames(self.page, self.JS_DISMISS_QUOTA) or []
        except Exception as e:  # noqa: BLE001
            self.log.warn(f"关页签上限弹窗异常：{str(e)[:60]}")
            return []
        if out:
            self.page.wait_for_timeout(600)
            self.log.warn(f"已点「确定」关掉提示框：{out}")
        return out

    def close_stray_windows(self, max_iter=6):
        """
        关掉挡在前面的弹窗（`.k-window`）。
        为什么需要：金蝶有些功能页**一打开就弹一个「过滤」对话框**（实测：凭证查询），
        这个弹窗会一直浮在上面，后续所有真实鼠标点击都被它拦掉
        （报错长这样：`<a aria-label="Close"> … intercepts pointer events`）。
        只在清场时调用，避免误关流程中的正常对话框。
        """
        done = []
        for _ in range(max_iter):
            js = self._INVISIBLE + """() => {
                const ws = Array.from(document.querySelectorAll('.k-window')).filter(vis);
                if (!ws.length) return null;
                const w = ws[ws.length - 1];
                const btn = w.querySelector('[aria-label="Close"], .k-window-action.k-link')
                    || Array.from(w.querySelectorAll('a,button')).filter(vis)
                         .find(x => (x.innerText||'').trim() === '取消');
                const title = (() => { const t = w.querySelector('.k-window-title');
                                       return t ? (t.innerText||'').trim() : ''; })();
                if (!btn) return {title, noClose: true};
                btn.click();
                return {title};
            }"""
            r = eval_all_frames(self.page, js)
            if not r:
                break
            if r.get("noClose"):
                self.log.warn(f"弹窗 {r.get('title')!r} 没有关闭按钮，留着不动")
                break
            done.append(r.get("title") or "(无标题)")
            self.page.wait_for_timeout(700)
        if done:
            self.log.info(f"关掉挡路弹窗 {len(done)} 个：{done}")
        return done

    def close_all_tabs(self, keep=0, keep_names=None, max_iter=80, quiet=False, activate_kept=True):
        """
        关掉标签页。
        keep_names: 名字里含这些关键字的标签**保留**（用户口径：保留「销售出库单列表」）
        keep      : 没给 keep_names 时，保留最后 N 个
        策略：先关非激活的、从右往左（关一个位置就变，每轮重新取列表），
              非保留的激活标签最后关；关完把保留的标签切回前台。
        顺带把浮在上面的弹窗（如「凭证查询」的过滤框）一起关掉。
        返回关掉的标签名列表。
        """
        self.close_stray_windows()

        def is_keeper(t):
            return bool(keep_names) and any(k in (t.get("text") or "") for k in keep_names)

        closed = []
        failed = set()       # 关不掉的标签（孤儿）—— 跳过而非停下，见下方 ★ 修复②
        skipped = 0          # 关不掉的标签计数
        for _ in range(max_iter):
            tabs = self.tabs()
            if not tabs:
                break
            targets = [t for t in tabs if not is_keeper(t)]
            if not keep_names and len(tabs) <= keep:
                break
            if not targets:
                break
            # ★★ 2026-10-03 修复③（顺序 bug）：**先剔黑名单，再选"最该关的那个"**。
            #   原实现是 `非激活池 → 剔黑名单`，实测踩坑（本批江西广源）：
            #     [01:28:53] 标签 '应收单 - 新增' 点了没反应 → 跳过
            #     [01:28:56] 标签 '销售增值税专用发票 - 新增' 点了没反应 → 跳过
            #     [01:28:58] 标签 '应收单 - 新增' 点了没反应 → 跳过
            #     [01:29:00] 剩 3 个标签关不掉，已全部跳过          ← 循环到此结束
            #   而当时**真正占着专票的**是「销售增值税专用发票 - 修改」（它是 active 的），
            #   它压根没被尝试过（因为"非激活池"被三个孤儿占满后剔成空 → break）
            #   → 下一步报「该业务单据正在被蒋一天使用，本次不生成凭证」，整链失败。
            #   修法：**黑名单先剔**，剩下的里面再优先「- 新增 / - 修改」占单标签
            #   —— 哪怕它是 active 的那个也必须试。
            cand = [t for t in targets
                    if (t.get("id") or t.get("text") or f"#{t.get('i')}") not in failed]
            cand = [t for t in cand if t.get("cx") is not None]
            if not cand:
                if failed:
                    self.log.warn(f"剩 {len(failed)} 个标签关不掉（孤儿/新增页），已全部跳过")
                else:
                    self.log.warn("剩余标签都没有关闭按钮，停止清理")
                break
            # ★ 2026-10-03 修复：**优先关「- 新增 / - 修改」这类占单标签**。
            #   实测（10-03 首家）：并发用户 + 历史遗留会让 SPA 里堆积几十个**隐藏**标签
            #   （界面 tabs 只显示 5 个，clean_tabs 却关出了 40 个），逐个关会**耗尽 max_iter**，
            #   结果真正占着单据的「销售增值税专用发票 - 修改」没被关掉 →
            #   下一步报「该业务单据正在被蒋一天使用，本次不生成凭证」，整链卡死。
            #   占单标签就那么几个，先关它们，列表类标签关不完也不影响。
            busy = [t for t in cand
                    if any(k in (t.get("text") or "") for k in ("- 新增", "- 修改"))]
            nonact = [t for t in cand if not t.get("active")]
            t = (busy or nonact or cand)[-1]

            # ★ 2026-10-03 修复②：**关不掉的标签记入黑名单跳过，而不是整个停下**。
            #   为什么不能"连续 N 次无效就 break"：金蝶会留下**孤儿标签**
            #   （实测：「应收单 - 新增」的 li 缺 k-tab-on-top、内容面板已销毁，
            #     关闭事件绑在已失效的内部 tab 对象上 → 点 ✕ 静默无响应；
            #     连 `locator.click()` / 金蝶自带的「关闭其他页签」都无效）。
            #   若直接 break，后面的**活标签**（专票列表等）就永远轮不到，占单标签关不掉
            #   → 报「该业务单据正在被 XX 使用，本次不生成凭证」。
            #   所以逐个识别、跳过、继续关剩下的。
            sig_before = tuple(sorted((x.get("text") or "") for x in self.tabs()))
            key = t.get("id") or t.get("text") or f"#{t.get('i')}"
            try:
                self.close_tab(t)
            except Exception as e:
                self.log.warn(f"关标签 {t.get('text')!r} 失败：{type(e).__name__}: {e}")
                failed.add(key)
                continue
            if tuple(sorted((x.get("text") or "") for x in self.tabs())) == sig_before:
                # ★ 2026-10-03 新增兜底：✕ 点不动 ≠ 无解 —— 走主标签栏自己的
                #   `kendomainTabControl.remove()` 摘掉它（见 `_force_remove_tab` 文档）。
                #   实测 6/6 成功，从此不再有"清不掉的孤儿标签"占着页签配额（上限 15）。
                if self._force_remove_tab(t):
                    self.page.wait_for_timeout(600)
                    if tuple(sorted((x.get("text") or "") for x in self.tabs())) != sig_before:
                        closed.append(t.get("text") or "")
                        self.log.ok(f"标签 {t.get('text')!r} 点 ✕ 无反应 → 走 "
                                    f"kendomainTabControl.remove() 摘除成功")
                        continue
                failed.add(key)
                skipped += 1
                self.log.warn(f"标签 {t.get('text')!r} 点了没反应 → 判定为关不掉的孤儿，跳过")
                if len(failed) >= 40:
                    self.log.warn("关不掉的标签已达 40 个，停止清理（疑似 SPA 状态异常）")
                    break
            else:
                closed.append(t.get("text") or "")
        self.close_stray_windows()

        left = self.tabs()
        if keep_names and not any(is_keeper(t) for t in left):
            self.log.warn(f"要保留的标签 {keep_names} 没在了，剩余={[t['text'] for t in left]}")
        elif keep_names and activate_kept:
            try:
                got = self.activate_tab(keep_names[0])
                self.log.info(f"已切回保留的标签：{got!r}")
            except Exception as e:
                self.log.warn(f"切回保留标签失败：{e}")

        if not quiet:
            tail = f"，{skipped} 个关不掉已跳过" if skipped else ""
            if closed:
                self.log.ok(f"清场完成：关掉 {len(closed)} 个{tail}，剩 {len(left)} 个"
                            f"（{[(t['text'] or '')[:14] for t in left]}）")
            elif not left:
                self.log.info("清场：本来就没有标签")
            elif skipped:
                self.log.warn(f"清场：{skipped} 个标签关不掉（孤儿/新增页），已跳过")
        return closed

    def _reset_selection_and_pick_first(self):
        """清空勾选（金蝶是切换语义）后只勾第 1 行 —— 切页后残留勾选会误伤"""
        for _ in range(3):
            try:
                self.clear_selection()
            except Exception:
                pass
            if self.selected_count() == 0:
                break
            self.page.wait_for_timeout(700)
        r = self.select_row_by_index(0)
        self.page.wait_for_timeout(600)
        self.log.info(f"已选中第 1 行：{(r or {}).get('text', '')[:60]}")
        return r

    def open_biz_voucher_list(self):
        """报告列表 → 选中行 →「查看业务凭证」（→ 业务凭证列表）"""
        self._reset_selection_and_pick_first()
        self.click_menu(self.BIZVCH_BTN, timeout_ms=6000)
        return self.expect_tab("业务凭证列表")

    def open_gl_voucher_for_edit(self):
        """业务凭证列表 → 选中行 →「查看总账凭证」（→「凭证 - 修改」，改摘要处）

        ★ 2026-10-29（衢州驰星摘要写失败）：点「查看总账凭证」后 `凭证 - 修改` **没打开**，
          旧代码直接 `expect_tab` 超时报 `等标签 '凭证 - 修改' 超时` —— 摘要写不进去。
          真因还是 SF-61 那一类：页签配额满 15 时点按钮既不生效也不报错。
          这里加自愈：超时 → 关「上限15」弹窗 + `release_tab_quota()` → 重新选行再点，≤2 次。
        """
        last = None
        for att in range(1, 4):
            self._reset_selection_and_pick_first()
            self.click_menu(self.GLVCH_BTN, timeout_ms=6000)
            try:
                return self.expect_tab("凭证 - 修改", timeout_ms=15000)
            except RuntimeError as e:
                last = e
                if att >= 3:
                    break
                self.log.warn(f"「凭证 - 修改」未打开（第 {att} 次）：{str(e)[:80]} "
                              f"→ 关弹窗+释放页签配额后重试")
                self.dismiss_quota_dialog()
                self.release_tab_quota(
                    keep_names=("销售出库单列表", "业务凭证列表", "凭证生成报告列表"),
                    force=True)
                self.page.wait_for_timeout(1200)
        raise last

    def read_voucher_entries(self):
        """读「凭证 - 修改」页的分录行，返回 [{FENTRYSEQ, FEXPLANATION, ..., __rowid__, __editable__}]"""
        js = self._INVISIBLE + """() => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return null;
            return Array.from(g.querySelectorAll('tbody tr')).map(tr => {
                const o = {};
                tr.querySelectorAll('span[data-field]').forEach(s => {
                    o[s.getAttribute('data-field')] = (s.innerText||'').trim();
                });
                const ex = tr.querySelector('span[data-field="FEXPLANATION"]');
                if (ex) {
                    o.__rowid__ = ex.getAttribute('data-rowid');
                    const d = ex.closest('td').querySelector('div');
                    o.__editable__ = !(d && d.className.toString().includes('disabled'));
                }
                return o;
            });
        }"""
        return eval_all_frames(self.page, js) or []

    def _digest_cell(self, idx):
        """拿第 idx 行摘要格的坐标与可编辑性"""
        js = self._INVISIBLE + """(i) => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return {err:'no visible grid'};
            const tr = Array.from(g.querySelectorAll('tbody tr'))[i];
            if (!tr) return {err:'row out of range'};
            const s = tr.querySelector('span[data-field="FEXPLANATION"]');
            if (!s) return {err:'no digest cell'};
            const d = s.closest('td').querySelector('div');
            const r = s.closest('td').getBoundingClientRect();
            return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
                     v: (s.innerText||'').trim(),
                     editable: !(d && d.className.toString().includes('disabled')) };
        }"""
        return eval_all_frames(self.page, js, idx)

    def set_one_digest(self, idx, text):
        """改第 idx 行的摘要（双击 → fill → Enter），返回是否真的改了"""
        info = self._digest_cell(idx)
        if not isinstance(info, dict) or info.get("err"):
            raise RuntimeError(f"第 {idx} 行摘要格定位失败：{info}")
        if not info.get("editable"):
            raise RuntimeError(f"第 {idx} 行摘要格不可编辑（在写「查看」页而不是「凭证 - 修改」页？）")
        if info.get("v") == text:
            self.log.info(f"第 {idx} 行摘要已是 {text!r}，跳过")
            return False
        self.page.mouse.dblclick(info["x"], info["y"])
        self.page.wait_for_timeout(700)
        inp = self.page.locator(self.DIGEST_INPUT + ":visible").first
        if inp.count() == 0:
            raise RuntimeError(f"第 {idx} 行双击后没出现摘要编辑框（{self.DIGEST_INPUT}）")
        inp.fill(text)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(600)
        after = (self._digest_cell(idx) or {}).get("v")
        if after != text:
            self.log.warn(f"第 {idx} 行摘要回读不一致：期望 {text!r}，实得 {after!r}")
        else:
            self.log.info(f"改摘要 行{idx}：{info.get('v')!r} → {after!r}")
        return True

    def set_entry_digest(self, text, rows=None):
        """
        批量改分录摘要（需已在「凭证 - 修改」页）。
        rows=None → 所有「摘要非空」的行；也可传行下标列表（0 基）。
        返回真正改过的行下标。
        """
        entries = self.read_voucher_entries()
        if not entries:
            raise RuntimeError("读不到凭证分录（当前不在「凭证 - 修改」页？）")
        todo = []
        for i, e in enumerate(entries):
            if rows is not None and i not in rows:
                continue
            if rows is None and not (e.get("FEXPLANATION") or "").strip():
                continue
            todo.append(i)
        if not todo:
            self.log.warn("没有需要改摘要的行")
            return []
        changed = [i for i in todo if self.set_one_digest(i, text)]
        self.log.info(f"共改 {len(changed)} 行摘要：{changed}")
        return changed

    def edit_digest(self, digest_text, rows=None):
        """改凭证分录摘要（兼容旧名）"""
        return self.set_entry_digest(digest_text, rows=rows)

    def fix_other_income_detail(self, target="0101001-009/原材料", log=None, entries=None):
        """
        修正「其他业务收入」行的核算维度（FDETAILID）。

        背景（2026-10-04 用户定）：凭证里若有一行科目是「其他业务收入」（编码 6051 开头，
        如 6051.02），该行的核算维度不能用默认值（默认是「CDTL-001/成都天龙凭证用」之类的
        错误值），必须统一改成「0101001-009/原材料」。

        entries: 可选，传入已读好的分录（run_one 里写摘要前已读一次，避免二次读的时序问题）。
        """
        log = log or self.log
        if entries is None:
            entries = self.read_voucher_entries()
        if not entries:
            raise RuntimeError("读不到凭证分录")
        # 诊断：打印每行科目编码
        log.info(f"  分录科目序列：{[ (e.get('FACCOUNTID') or '') for e in entries ]}")
        targets = []
        for i, e in enumerate(entries):
            acct = (e.get("FACCOUNTID") or "").strip()
            if acct.startswith("6051"):
                targets.append(i)
        if not targets:
            log.info("  无「其他业务收入」行，跳过核算维度修正")
            return []

        log.info(f"  其他业务收入行 {targets}，需改核算维度 → {target!r}")
        changed = []
        for i in targets:
            cur = (entries[i].get("FDETAILID") or "").strip()
            if cur == target:
                log.info(f"  第 {i} 行核算维度已是 {target!r}，跳过")
                continue
            if self._set_entry_detail_id(i, target):
                changed.append(i)
        return changed

    def _set_entry_detail_id(self, idx, target):
        """双击第 idx 行 FDETAILID 单元格 → 清空 → 输入名称 → 选中目标。返回是否改成功。"""
        code = target.split("/")[0]   # "0101001-009"
        name = target.split("/")[1] if "/" in target else ""   # "原材料"
        # 取 FDETAILID 单元格坐标
        pos = eval_all_frames(self.page, self._INVISIBLE + """(i) => {
            const g = Array.from(document.querySelectorAll('table.k-selectable')).filter(vis)[0];
            if (!g) return {err:'no grid'};
            const tr = Array.from(g.querySelectorAll('tbody tr'))[i];
            if (!tr) return {err:'no row '+i};
            const dt = tr.querySelector('span[data-field="FDETAILID"]');
            if (!dt) return {err:'no detail cell'};
            dt.scrollIntoView({block:'center'});
            const b = dt.getBoundingClientRect();
            return {x: Math.round(b.x + b.width/2), y: Math.round(b.y + b.height/2),
                    v: (dt.innerText||'').trim()};
        }""", idx)
        if not isinstance(pos, dict) or pos.get("err"):
            self.log.warn(f"第 {idx} 行 FDETAILID 定位失败：{pos}")
            return False
        self.log.info(f"  第 {idx} 行 FDETAILID 当前={pos.get('v')!r} → 双击 @{pos['x']},{pos['y']}")
        self.page.mouse.dblclick(pos["x"], pos["y"])
        self.page.wait_for_timeout(1500)

        # 定位双击后出现的编辑框（k-textbox，id 尾 -FENTITY_FDETAILID_c）
        box = eval_all_frames(self.page, self._INVISIBLE + """() => {
            const inp = Array.from(document.querySelectorAll('input[id$="-FENTITY_FDETAILID_c"]')).filter(vis)[0];
            if (!inp) return null;
            const b = inp.getBoundingClientRect();
            return {x: Math.round(b.x + b.width/2), y: Math.round(b.y + b.height/2),
                    v: inp.value, cls: (inp.className||'').toString()};
        }""")
        if not box:
            self.log.warn("  双击后没出现 FDETAILID 编辑框")
            return False
        self.log.info(f"  编辑框出现：value={box.get('v')!r} cls={box.get('cls')!r}")

        # 清空 + 输入名称，观察联想
        self.page.mouse.click(box["x"], box["y"])
        self.page.wait_for_timeout(300)
        self.page.keyboard.press("Control+a")
        self.page.keyboard.press("Delete")
        self.page.wait_for_timeout(200)
        self.page.keyboard.type(name, delay=120)
        self.page.wait_for_timeout(3500)
        # 探测联想列表 / 输入后的状态（更宽选择器 + 全 input + 所有可见弹窗/浮层）
        after = eval_all_frames(self.page, self._INVISIBLE + """() => {
            const inp = Array.from(document.querySelectorAll('input[id$="-FENTITY_FDETAILID_c"]')).filter(vis)[0];
            const wins = Array.from(document.querySelectorAll('.k-window')).filter(vis)
                .map(w => (w.innerText||'').trim().replace(/\\s+/g,' ').slice(0,120));
            const items = Array.from(document.querySelectorAll('li')).filter(vis)
                .map(li => (li.innerText||'').trim()).filter(t => t && t.length < 40).slice(0,30);
            const panels = Array.from(document.querySelectorAll('.k-animation-container, .k-popup, .k-list-container')).filter(vis)
                .map(p => (p.innerText||'').trim().replace(/\\s+/g,' ').slice(0,120));
            const allInputs = Array.from(document.querySelectorAll('input')).filter(vis)
                .map(e => ({id: e.id.slice(-40), v: e.value})).slice(0,20);
            return {val: inp ? inp.value : null, wins, items, panels, allInputs};
        }""")
        self.log.info(f"  输入名称 {name!r} 后：val={after.get('val')!r}")
        self.log.info(f"    wins={after.get('wins')}")
        self.log.info(f"    items={after.get('items')}")
        self.log.info(f"    panels={after.get('panels')}")
        self.log.info(f"    allInputs={after.get('allInputs')}")
        return False

    def save_voucher(self):
        self.guard.check("voucher_save", "凭证保存后进入总账")
        self.click_menu("保存", timeout_ms=2500)

    # ---------- 清理 ----------
    def close_bill(self):
        """关闭当前标签页，回到列表"""
        js = """() => {
            const tab = document.querySelector('.k-item.k-state-active');
            if (!tab) return false;
            const btn = tab.querySelector('.k-i-close, .mainTabCloseButton');
            if (!btn) return false;
            btn.click();
            return true;
        }"""
        ok = eval_all_frames(self.page, js)
        if not ok:
            self.log.warn("未找到标签页关闭按钮")
        else:
            self.log.info("已关闭当前标签页")
        self.page.wait_for_timeout(1200)
        return bool(ok)

    def wait_form_ready(self, timeout_ms=15000):
        """等新开的表单页加载完成（出现日期控件 / tab / 网格任一即可）"""
        js = """() => !!(document.querySelector('[data-role="datepicker"]')
                     || document.querySelector('.k-tabstrip-items')
                     || document.querySelector('.k-grid'))"""
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            if eval_all_frames(self.page, js):
                return True
            self.page.wait_for_timeout(300)
        self.log.warn("表单加载等待超时，继续执行")
        return False
