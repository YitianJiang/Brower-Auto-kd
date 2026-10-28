# -*- coding: utf-8 -*-
"""
公共模块：配置加载 / 日志 / 截图 / 安全护栏 / Excel 读写
"""
import os
import sys
import time
import datetime
from pathlib import Path

import yaml
from openpyxl import load_workbook

# 注意：不要在这里 reconfigure 成 utf-8。
# Windows 的 cmd / Git Bash 按 GBK 解码控制台输出，强制 utf-8 会让中文变乱码。
# 让 Python 沿用系统 locale（cp936）即可；文件写入处都显式指定了 encoding="utf-8"。

ROOT = Path(__file__).resolve().parent.parent


# ============================ 配置 ============================
def load_config(path=None):
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def abs_path(cfg_key, cfg):
    """把 config 里的相对路径转成绝对路径"""
    return ROOT / cfg["paths"][cfg_key]


# ============================ 日志 ============================
class Logger:
    def __init__(self, log_dir: Path, name=None):
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.file = log_dir / f"{(name + '_') if name else ''}{ts}.log"
        self.fp = open(self.file, "a", encoding="utf-8")

    def _w(self, level, msg):
        line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [{level}] {msg}"
        print(line, flush=True)
        self.fp.write(line + "\n")
        self.fp.flush()

    def info(self, msg):  self._w("INFO", msg)
    def ok(self, msg):    self._w(" OK ", msg)
    def warn(self, msg):  self._w("WARN", msg)
    def err(self, msg):   self._w("ERR ", msg)

    def close(self):
        try:
            self.fp.close()
        except Exception:
            pass


# ============================ 截图 ============================
# ★ 2026-09-22 提速修复（原本每次截图卡 30 秒，实锤 3 次/单）
#   现象：日志里反复出现
#     WARN 截图失败 ...: Page.screenshot: Timeout 30000ms exceeded.
#     Call log: taking page screenshot / waiting for fonts to load... / fonts loaded
#   根因：金蝶 SPA 把开过的页面 DOM 全留在 document 里（标签越多节点越多），
#         且页面上有常驻 CSS 动画/轮询 → Playwright 截图前的「等字体 + 等渲染稳定」
#         永远等不到，只能耗满默认 30s 超时。
#   修法：① 显式 timeout（快速失败，不再拖 30s）
#         ② animations="disabled" 把常驻动画停掉再截
#         ③ 仍失败则用 CDP Page.captureScreenshot 直取 —— 完全不经过
#            Playwright 的等待逻辑，实测最快最稳
_CDP_SHOT_SESSION = {}


def _cdp_screenshot(page, fp: Path, full=False):
    """绕过 Playwright 等待逻辑，直接走 CDP 取图。返回是否成功。"""
    import base64
    try:
        sess = _CDP_SHOT_SESSION.get(id(page))
        if sess is None:
            sess = page.context.new_cdp_session(page)
            _CDP_SHOT_SESSION[id(page)] = sess
        params = {"format": "png", "captureBeyondViewport": bool(full)}
        r = sess.send("Page.captureScreenshot", params)
        fp.write_bytes(base64.b64decode(r["data"]))
        return True
    except Exception:
        return False


def shot(page, shot_dir: Path, tag: str, logger=None, full=False, timeout_ms=5000):
    """截图存档。★ 已加超时与动画抑制；**CDP 直取优先**。

    ★ 2026-10-03 再修：把顺序反过来 —— **先 CDP，再 Playwright**。
      实测（本批江西广源）：`page.screenshot(timeout=5000)` 后置降级 CDP 的写法，
      单次截图从 01:26:17 拖到 **01:27:45 = 88 秒**（日志只有一行"CDP 降级成功"）。
      原因：`animations="disabled"` + 等字体那套逻辑在重页面上会**卡在超时之外**
      （页面主线程被长任务占住时，`timeout` 参数管不住它）。
      CDP 的 `Page.captureScreenshot` 不做任何等待，实测 1~2 秒返回。
      ⇒ **先 CDP**；CDP 不行再退回 Playwright（带 5s 超时）。

    ⚠️ 另一条教训：**批量运行期间不要用别的脚本连同一个浏览器做探针** ——
      CDP 命令在渲染器里是串行的，并发访问会把截图/点击一起拖慢。

    ★★ 2026-10-03 再再修（第 3 次）：**批量跑时必须能整段关掉截图（环境变量 `KD_NO_SHOT=1`）**。
      实测（江西广源写摘要）：`Page.captureScreenshot` **单次阻塞 9 分 08 秒**
      （日志 `截图 -> 015134_digest_AR105-202609-0095.png` 打印于 02:00:42，
        而文件名 `015134` 是 shot() 开头取的时间戳 → 9 分钟全耗在这个调用里）。
      原因：CDP 截图**要等渲染器产出一帧新画面**；窗口最小化/被遮挡、
      或页面主线程被长任务（凭证页的 KdNet 校验）占住时，帧永远不来 → 一直等。
      注意 **CDP 的 send 没有 timeout 参数**，Playwright 的 `timeout=` 也管不到它。
      ⇒ 影响：一家 3 张截图 × 8 家 = 最多几小时纯等待，且**对正确性零贡献**
        （截图只是存档；判据是「查询回读」）。
      ⇒ 修法：截图**可关**。`KD_NO_SHOT=1` 时 shot() 直接返回 None，
        不打图、不等帧、不写盘。批量脚本 / 长链路续跑默认都该开着它。
        单张调试、需要取证时再摘掉这个变量。
    """
    import os as _os
    # ★★ 2026-10-28 默认禁用截图（根治「又去截图卡数分钟」）
    #   历史坑：KD_NO_SHOT=1 是各 run_batch*.py 入口自己在文件头 os.environ 设的，
    #   单独跑 push_chain.py / push_return.py 时没有这层 → shot() 正常截图 →
    #   CDP Page.captureScreenshot 等渲染器产帧 → 卡 9 分钟+（对正确性零贡献）。
    #   改法：默认不开截图；只有显式 KD_SHOT=1 才截（单张调试/取证时用）。
    _force = _os.environ.get("KD_SHOT", "").strip() in ("1", "true", "yes", "on")
    if not _force:
        if logger:
            logger.info(f"截图已跳过（默认关闭，KD_SHOT=1 可强制开启）：{tag}")
        return None
    if _os.environ.get("KD_NO_SHOT", "").strip() in ("1", "true", "yes", "on"):
        if logger:
            logger.info(f"截图已禁用（KD_NO_SHOT=1）：{tag}")
        return None

    shot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%H%M%S")
    safe_tag = "".join(c for c in tag if c not in r'\/:*?"<>|')[:40]
    fp = shot_dir / f"{ts}_{safe_tag}.png"

    if _cdp_screenshot(page, fp, full=full):
        if logger:
            logger.info(f"截图 -> {fp.name}（CDP）")
        return fp
    try:
        page.screenshot(path=str(fp), full_page=full, timeout=timeout_ms,
                        animations="disabled", caret="hide")
        if logger:
            logger.info(f"截图 -> {fp.name}（Playwright 兜底）")
        return fp
    except Exception as e:  # noqa: BLE001
        if logger:
            logger.warn(f"截图失败 {tag}（已快速跳过，不影响流程）: {str(e)[:80]}")
        return None


# ============================ 安全护栏 ============================
class GuardError(Exception):
    """触发安全护栏：必须由人工确认后才能继续"""
    pass


class Guard:
    """
    不可逆动作的统一闸门，分两类：

      A. **开关式**（提交/审核/生成凭证/保存凭证）：config.safety 对应开关为 False 时拦截。
      B. ★ **绝对禁止**（删除凭证等）：硬编码，**不受 config 开关 / --yes 影响**，永远拦截。

    红线：**不能删除凭证**（用户 2026-09-21 定）。凭证是账务链路的终态留痕，
    删了不可逆、总账报表同时失真，且删除动作一路无校验、极易被脚本顺手点掉。
    要改凭证就走「凭证 - 修改」页，或反审核后重做 —— 别删。
    """

    MAP = {
        "submit": "auto_submit",
        "audit": "auto_audit",
        "voucher": "auto_voucher",
        "voucher_save": "auto_voucher_save",
    }

    # B 类：绝对禁止。action → (中文名, 替代做法)
    FORBIDDEN = {
        "delete_voucher": ("删除凭证", "改摘要/改分录后保存；确需重来走「反审核 → 修改」"),
        "delete_vouchers": ("批量删除凭证", "同上，逐张走「反审核 → 修改」"),
        "batch_delete_voucher": ("批量删除凭证", "同上"),
        "delete_gl_voucher": ("删除总账凭证", "同上"),
        "void_voucher": ("作废凭证", "作废留痕但仍不可逆，必须人工确认后手动操作"),
        "delete_bill": ("删除单据", "先反审核，再由人工确认删除"),
        "delete_audited_bill": ("删除已审核单据", "先反审核，再由人工确认删除"),
    }

    # 点菜单前先体检：文案命中这些词就别点
    DANGER_WORDS = ("删除凭证", "作废凭证", "凭证删除")

    def __init__(self, cfg, logger):
        self.s = cfg.get("safety", {})
        self.log = logger

    # 日志是可选依赖：logger=None 时也必须能正常抛 GuardError，
    # 否则拦不住的动作会退化成 AttributeError（= 护栏形同失效）。
    def _err(self, m):
        if self.log:
            self.log.err(m)

    def _warn(self, m):
        if self.log:
            self.log.warn(m)

    @classmethod
    def forbidden_reason(cls, action):
        name, alt = cls.FORBIDDEN.get(str(action), ("未知动作", ""))
        return f"「{name}」是**绝对禁止**的动作。\n  → 正确做法：{alt}"

    @classmethod
    def assert_click_safe(cls, text):
        """点菜单/按钮之前调一次：文案命中危险词就抛 GuardError。"""
        t = str(text or "")
        for w in cls.DANGER_WORDS:
            if w in t:
                raise GuardError(
                    f"安全闸门拦截：要点的项文案为 {t!r}，含危险词 {w!r}。\n"
                    f"  → 凭证不可删除（红线）。请改用「凭证 - 修改」页修改后保存。")
        return True

    def check(self, action, hint=""):
        # ★ 先判红线，再看开关 —— 免得有人以为"把开关打开就能删"
        if action in self.FORBIDDEN:
            msg = ("安全闸门拦截：" + self.forbidden_reason(action) +
                   "\n  → 该规则**硬编码在代码里**，不受 config.yaml 开关、--yes 或任何命令行参数影响。")
            self._err(msg)
            raise GuardError(msg)

        key = self.MAP.get(action)
        if key is None:
            return
        if not self.s.get(key, False):
            msg = (f"安全护栏拦截：需要执行「{action}」，"
                   f"但 config.yaml 的 safety.{key} = false。\n"
                   f"  → 请先人工在金蝶里核对上一步生成的单据，确认无误后把该开关改为 true 再跑。")
            if hint:
                msg += f"\n  → {hint}"
            self._err(msg)
            raise GuardError(msg)
        self._warn(f"已放行不可逆动作「{action}」（{key}=true）")


# ============================ Excel ============================
# 输入列（表头名必须与这里一致）
COL_CUSTOMER = "客户名称"
COL_OUTSTOCK_NO = "出库单号"
COL_OUTSTOCK_DATE = "出库日期"
COL_AMOUNT = "价税合计"
COL_DRAWER = "开票人"          # 要填进金蝶发票
COL_BIZ_DATE = "业务日期"      # 要填进金蝶发票
COL_INVOICE_DATE = "发票日期"  # 要填进金蝶发票
COL_RANGE_EXCEPT = "日期区间例外"
COL_DIGEST = "摘要"
COL_REMARK = "备注"
COL_INVOICE_NO = "发票号"      # 可选：不填也行，填了就按它精确分组

# 输出列（脚本回写，不存在则自动追加）
COL_AR_NO = "应收单号"
COL_INV_BILL_NO = "发票单据号"
COL_VOUCHER_NO = "凭证号"
COL_STATUS = "处理状态"
COL_ERROR = "错误信息"

INPUT_COLS = [
    "序号", COL_CUSTOMER, COL_OUTSTOCK_NO, COL_OUTSTOCK_DATE,
    COL_AMOUNT, COL_DRAWER, COL_BIZ_DATE, COL_INVOICE_DATE,
    COL_RANGE_EXCEPT, COL_DIGEST, COL_REMARK, COL_INVOICE_NO,
]
OUTPUT_COLS = [COL_AR_NO, COL_INV_BILL_NO, COL_VOUCHER_NO, COL_STATUS, COL_ERROR]


def read_excel(path: Path, sheet=None):
    """
    读取开票数据，返回 (表头list, 数据行list[dict])
    """
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    data = []
    for r in rows[1:]:
        if all(v is None or str(v).strip() == "" for v in r):
            continue
        d = {}
        for i, h in enumerate(header):
            d[h] = r[i] if i < len(r) else None
        d["__row__"] = len(data) + 2  # Excel 实际行号（含表头）
        data.append(d)
    return header, data


def write_back(path: Path, results: dict, sheet=None):
    """
    把结果回写进 Excel。results: {excel_row(int): {列名: 值}}
    """
    wb = load_workbook(path)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
    header = [c.value for c in ws[1]]

    # 补上不存在的输出列
    for col in OUTPUT_COLS:
        if col not in header:
            ws.cell(row=1, column=len(header) + 1, value=col)
            header.append(col)

    col_idx = {h: i + 1 for i, h in enumerate(header)}
    for row, kv in results.items():
        for col, val in kv.items():
            if col in col_idx:
                ws.cell(row=row, column=col_idx[col], value=val)
    wb.save(path)


# ============================ 日期区间 ============================
def default_range_for(today: datetime.date = None):
    """
    默认区间：上月26号 ~ 当月25号
    返回 (start_date, end_date)
    """
    today = today or datetime.date.today()
    # 当月25号为右端点
    end = datetime.date(today.year, today.month, 25)
    # 左端点 = 上月26号
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    start = datetime.date(y, m, 26)
    return start, end


def to_date(v):
    """
    把各种写法的单元格值转成 datetime.date。
    支持 datetime/date 对象、"2026-08-31"、"2026/8/31"、"2026-8-31 00:00:00"
    空值返回 None。
    """
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v).strip().replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    head = s.split(" ")[0]
    y, m, d = [int(x) for x in head.split("-")]
    return datetime.date(y, m, d)


def month_last_day(today: datetime.date = None) -> datetime.date:
    """当月最后一天：下月1号减1天"""
    t = today or datetime.date.today()
    y, m = (t.year, t.month + 1) if t.month < 12 else (t.year + 1, 1)
    return datetime.date(y, m, 1) - datetime.timedelta(days=1)


def prev_month_last_day(today: datetime.date = None) -> datetime.date:
    """上个月最后一天：本月1号减1天"""
    t = today or datetime.date.today()
    return t.replace(day=1) - datetime.timedelta(days=1)


def resolve_biz_date(cfg, today: datetime.date = None):
    """
    应收单「业务日期」字段(FDATE)的取值规则。

    业务口径（2026-09-18 用户确认）：
        每月初处理【上月】的账 → 业务日期填【上月末】
        例：10月初处理9月单据 → 2026-09-30

    config 可选值：
        "上月最后一天"  正式投产用（月初作业）
        "本月最后一天"  演示/提前处理用（现在9月做9月的账）
        "manual"        用 receivable_biz_date_fixed
        ""              不改业务日期，返回 None

    返回 (date, 说明)。
    实测：改 FDATE 后 FENDDATE_H(到期日) 会自动联动，不用单独设。
    """
    rule = str((cfg.get("rules", {}) or {}).get("receivable_biz_date", "") or "").strip()
    today = today or datetime.date.today()

    if not rule or rule == "manual":
        fixed = str((cfg.get("rules", {}) or {}).get("receivable_biz_date_fixed", "") or "").strip()
        if not fixed:
            return None, "未设置业务日期规则（保持不变）"
        _y, _m, _d = [int(x) for x in fixed.replace("/", "-").split("-")]
        return datetime.date(_y, _m, _d), f"固定值({fixed})"

    if rule == "本月最后一天":
        d = month_last_day(today)
        return d, f"本月最后一天({d:%Y-%m-%d})"

    if rule == "上月最后一天":
        d = prev_month_last_day(today)
        return d, f"上月最后一天({d:%Y-%m-%d})"

    # 已经是显式日期
    _y, _m, _d = [int(x) for x in rule.replace("/", "-").replace(".", "-").split("-")]
    return datetime.date(_y, _m, _d), f"显式指定({rule})"


def resolve_range(row: dict, cfg):
    """
    决定某行使用的日期区间：
    Excel「日期区间例外」列非空则优先用它，否则用默认规则。
    例外列支持写法：
        2026-08-26~2026-09-25
        2026/8/26-2026/9/25
    """
    raw = row.get(COL_RANGE_EXCEPT)
    if raw is None or str(raw).strip() == "":
        s, e = default_range_for()
        return s, e, "默认规则(上月26~当月25)"

    txt = str(raw).strip().replace("/", "-").replace("~", "|").replace("—", "|")
    parts = [p.strip() for p in txt.split("|")]
    if len(parts) != 2:
        raise ValueError(
            f"日期区间例外格式不对（行 {row.get('__row__')}）：{raw!r}，"
            f"应写成 2026-08-26~2026-09-25"
        )

    def _d(s):
        y, m, d = [int(x) for x in s.split("-")]
        return datetime.date(y, m, d)

    return _d(parts[0]), _d(parts[1]), f"例外({raw})"


# ============================ 分组 ============================
def group_by_invoice(rows: list):
    """
    分组依据（组成一张金蝶发票 = 一个客户的一张票）：
      1. 填了「发票号」→ 按发票号精确分组
      2. 否则 → 按「客户名称 + 发票日期」（同客户同一天开的算一张票）
      3. 都没有 → 按「客户名称 + 出库单号」单独成组，避免误合并

    返回 [(key, [row, ...]), ...]，key 形如 ('发票号', 'xxx') / ('客户+日期', 'A客户|2026-09-05')
    """
    buckets = {}
    order = []

    def norm_date(v):
        if v is None or str(v).strip() == "":
            return ""
        if isinstance(v, (datetime.datetime, datetime.date)):
            return v.strftime("%Y-%m-%d")
        return str(v).strip()[:10]

    for r in rows:
        inv = str(r.get(COL_INVOICE_NO) or "").strip()
        cust = str(r.get(COL_CUSTOMER) or "").strip()
        idate = norm_date(r.get(COL_INVOICE_DATE))

        if inv:
            key = ("发票号", inv)
        elif cust and idate:
            key = ("客户+发票日期", f"{cust}|{idate}")
        else:
            key = ("单张", f"{cust}|{r.get(COL_OUTSTOCK_NO)}")

        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(r)

    return [(k, buckets[k]) for k in order]
