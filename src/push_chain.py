# -*- coding: utf-8 -*-
"""
一条命令跑完「销售出库单 → 应收单 → 增值税专用发票 → 凭证」整链
=================================================================
把 2026-09-21 广源包装集团那次手工下推的全过程参数化，以后换客户只换参数。

用法示例
--------
# 只看不写（筛选+读明细+算分摊，验证口径对不对）
python src/push_chain.py --customer 广源包装集团 \
    --bill-nos XSCKD-105-202608-0121,XSCKD-105-202609-0020 --dry-run

# 按日期区间筛（FDate），分摊扣款 2733.5 并对齐到实际开票金额，全链跑完
python src/push_chain.py --customer 广源包装集团 \
    --date-from 2026-08-21 --date-to 2026-09-20 \
    --discount 2733.5 --target-notax 266824.88 --target-tax 34687.22 \
    --biz-date 2026-09-30 --invoice-date 2026-09-30 \
    --step voucher --yes

# ★ 不产生 -00000N 子单：下推专票时用「分录不合并」（专票行数 = 应收单行数）
python src/push_chain.py ... --no-invoice-merge --yes

# ★ 对照实验：同一张已审核应收单，只重跑「下推专票」这一段（不建应收单）
python src/push_chain.py --ar-no AR105-202609-0004 --step invoice --no-invoice-merge --yes

参数速查
--------
  --bill-nos      指定出库单号（逗号分隔）；给了它就不按日期筛
  --date-from/--date-to  按【日期 FDate】区间筛（用户口径：单据日期，非账务日期）
  --discount      扣款总额（价税合计口径）。给了就按行含税占比分摊到折扣额
                  ★ 录金蝶为正数；下推表里给的负数会自动取绝对值
  --target-notax / --target-tax  实际开票的不含税/税额。给了就闭环对齐到这两个值
  --biz-date / --invoice-date    业务日期 / 发票日期（专票），如 2026-09-30
  --checker       复核人（默认取 config.rules.invoice_checker）
  --step          跑到哪一步为止：receivable | invoice | voucher(默认，全链)
  --no-invoice-merge  下推专票用「分录不合并」（见 docs/广源应收单子单-原因分析.md）
  --ar-no         复用已有应收单号（跳过筛选/建单，只跑 invoice/voucher）
  --yes           放行不可逆动作（保存/提交/审核/生成凭证）
  --dry-run       只读不写：筛选 + 读明细 + 打印分摊结果，不点任何保存/下推
  --no-clean-tabs 结束后不清标签页

与 run.py 的区别
----------------
  run.py         —— 走 Excel 驱动（一张表多组客户，批量、可回写结果）
  push_chain.py  —— 走命令行参数（临时一单、现场补做、验算口径），
                    不依赖 Excel，适合"就这一家客户、就这一笔扣款"的场景
两者共用同一套 KingdeeUI 方法与 discount.py 计算模块。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, Guard, GuardError, shot  # noqa: E402
from steps import KingdeeUI  # noqa: E402
from discount import to_d  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="push_chain")
SHOT_DIR = abs_path("screenshots", CFG)
# ★★ 2026-10-03 修复：**必须把「凭证生成报告列表」一起保留**。
#   为什么：改摘要那一步（`write_digest.py`）的入口就是这张报告列表：
#     报告列表 → 选行「查看业务凭证」→ 业务凭证列表 →「查看总账凭证」→「凭证 - 修改」。
#   而这张列表**不能由搜索打开**（实测搜「凭证生成报告列表」「业务凭证列表」都 0 结果），
#   它**只能由「生成凭证」动作自动弹出**。
#   ⇒ 一旦这里把它关掉，摘要就再也改不了（重跑批量还会把单据再推一遍，绝对不能这么干）。
#   `run.py` 的顺序是对的（先改摘要、后清场）；`push_chain` 里改摘要发生在**它返回之后**，
#   所以只能靠"别关"来保证。下一家的 pre_clean 会把它清掉，不会累积。
KEEP_TABS = ["销售出库单列表", "凭证生成报告列表"]


def parse_args():
    ap = argparse.ArgumentParser(description="金蝶下推链一条命令（出库单→应收单→专票→凭证）")
    ap.add_argument("--customer", default="", help="客户名（用于日志与核对，不参与筛选）")
    ap.add_argument("--bill-nos", default="", help="出库单号，逗号分隔")
    ap.add_argument("--date-from", default="", help="日期 FDate 起（YYYY-MM-DD）")
    ap.add_argument("--date-to", default="", help="日期 FDate 止（YYYY-MM-DD）")
    ap.add_argument("--discount", type=float, default=None, help="扣款总额（分摊到折扣额；录金蝶为正数，负数自动取绝对值）")
    ap.add_argument("--target-notax", default="", help="实际开票不含税金额")
    ap.add_argument("--target-tax", default="", help="实际开票税额")
    ap.add_argument("--target-all", default="", help="实际开票价税合计（★ 三个数精确到分时必传，用于交叉校验）")
    ap.add_argument("--biz-date", default="", help="业务日期 YYYY-MM-DD")
    ap.add_argument("--invoice-date", default="", help="发票日期 YYYY-MM-DD")
    ap.add_argument("--checker", default="", help="复核人")
    ap.add_argument("--step", choices=["receivable", "invoice", "voucher"],
                    default="voucher", help="跑到哪一步为止")
    ap.add_argument("--no-invoice-merge", action="store_true",
                    help="★ 下推专票时用「分录不合并」：专票 46 行与应收单逐行对应，"
                         "舍入口径一致 → 不产生 -00000N 子单（代价：专票行数 = 应收单行数）")
    ap.add_argument("--ar-no", default="",
                    help="复用已有应收单号（只跑 --step invoice/voucher 时用，跳过出库单筛选与建单）")
    ap.add_argument("--yes", action="store_true", help="放行不可逆动作")
    ap.add_argument("--dry-run", action="store_true", help="只读不写")
    ap.add_argument("--no-clean-tabs", action="store_true", help="结束后不清标签")
    return ap.parse_args()


def _money(x):
    return f"{to_d(x):,.2f}"


# ------------------------------------------------------------------
# ★ 单号回读兜底（2026-09-22 提速）
#   现象：保存后 `read_bill_fields(["FBillNo"])` 常读不到 →「(单号未读到)」
#        → step_voucher 拿不到专票号直接 RuntimeError，整链断掉，
#          只能人工跑 find_ar.py 查号、再 --ar-no 续跑（一次来回 1~2 分钟）。
#   修法：读不到就用**只读查询**（ExecuteBillQuery，零副作用）反查。
#        优先级：来源出库单号精确关联 > 客户+时间窗最近一条。
#        兜底命中一律 WARN 留痕 —— 多人共用账套时不静默。
# ------------------------------------------------------------------
def _read_no(ui, field="FBillNo"):
    try:
        return str((ui.read_bill_fields([field]) or {}).get(field) or "").strip()
    except Exception as e:  # noqa: BLE001
        log.warn(f"界面读取 {field} 异常：{str(e)[:60]}")
        return ""


def _backfill_no(ui, kind, since, customer=None, src_bills=None, tries=4, gap_ms=1500):
    """界面读不到单号时的兜底反查。返回单号或空串。"""
    from kdquery import KdQuery
    for i in range(tries):
        try:
            kq = KdQuery(ui.page)
            if kind == "ar" and src_bills:
                for b in src_bills:
                    hits = [h for h in kq.ar_by_source_bill(b) if h]
                    if hits:
                        log.warn(f"★ 界面未读到应收单号 → 按来源出库单 {b} 精确反查到 {hits}")
                        return hits[-1]
            rs = kq.latest_ar(customer, since, top=5) if kind == "ar" \
                else kq.latest_iv(since, top=5)
            if rs:
                n = str(rs[0].get("FBillNo") or "")
                if n:
                    log.warn(f"★ 界面未读到单号 → 按创建时间反查（{since} 之后）得 {n}"
                             f"；候选 {[str(x.get('FBillNo')) for x in rs]} —— 请核对确认是本次这张")
                    return n
        except Exception as e:  # noqa: BLE001
            log.warn(f"反查单号第 {i+1} 次失败：{str(e)[:80]}")
        if i < tries - 1:
            ui.page.wait_for_timeout(gap_ms)
        else:
            log.err("反查单号失败：界面读不到、服务端也查不到 —— 停止，请人工核对后再续跑")
    return ""


def _since_str(minutes=3):
    """给反查用的时间窗起点（比"现在"往前推几分钟，容忍保存与落库的延迟）。"""
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------
# ★★ 2026-10-03：**专票号必须能被服务端反证**（SF-55 家族的读端 bug）
#   现象（AR105-202609-0108 扬州祥恒）：step_invoice 保存后 `_read_no` 读到的是
#   **上一家**的专票号（SVINV00034032，实际自己是 …033）——`read_bill_fields` 用弱可见性
#   `filter(vis)[0]`，命中了**后台标签页**里同名 `-FBILLNO-EDITOR_c` 控件（DOM 里上一家的
#   专票页还在）。于是 step_voucher 勾的是别人的单 → 界面弹「所选单据已生成总账凭证」→
#   **凭证根本没生成，报告却照样是「0 错误」**（静默失败，只有服务端金额反查才看得出来）。
#   修法：拿到专票号后**用来源应收单反查专票**（权威判据），不符就改用反查结果。
#   —— 与 SF-55 同一课：**回读要读能反证的东西，不要读被写对象/被读对象自己**。
# ------------------------------------------------------------------
def _verify_iv_no(ui, inv_no, ar_no, tries=3, gap_ms=1200):
    """用服务端反查校验专票号：必须能从来源应收单 `FSrcBillNo` 反查到。不符则纠正。"""
    if not ar_no:
        return inv_no
    from kdquery import KdQuery
    hits = []
    for i in range(1, max(1, tries) + 1):
        try:
            hits = [str(x.get("FBillNo")) for x in
                    (KdQuery(ui.page).rows("IV_SALESIC", "FBillNo",
                                           f"FSrcBillNo = '{ar_no}'", top=10) or [])]
        except Exception as e:  # noqa: BLE001
            log.warn(f"专票号反查（来源应收单 {ar_no}）第 {i} 次失败：{str(e)[:90]}")
            hits = []
        if hits:
            break
        if i < tries:
            ui.page.wait_for_timeout(gap_ms)
    if not hits:
        log.warn(f"★ 反查不到 {ar_no} 下推的专票（界面读到 {inv_no!r}）—— 保留界面值，"
                 f"但下游勾单前请人工确认")
        return inv_no
    if inv_no and inv_no not in hits:
        log.warn(f"★★ 界面读到的专票号 {inv_no} 与来源应收单 {ar_no} **对不上**"
                 f"（服务端反查 = {hits}）→ 以服务端为准，改用 {hits[-1]}")
    elif not inv_no:
        log.warn(f"★ 界面未读到专票号 → 按来源应收单 {ar_no} 反查到 {hits[-1]}")
    return hits[-1]


# ------------------------------------------------------------------
# ★★ 2026-10-03：保存后**服务端回读**应收单的业务日期 / 到期日（权威判据）
#   为什么必须有：SF-55 那次 7 家的业务日期落在"今天"（10-03），
#   界面日志却写着「填写 FDATE = 2026-09-30 [kendoDatePicker]」——
#   **界面回显与日志都不算数**，只有 `ExecuteBillQuery` 回读算数。
#   单号年月 = 业务日期年月（已实测：`AR105-202609-0092..0097` 的 FDate 全 = 2026-09-30），
#   所以这条校验还能解释/预防"单号月份不对"。
# ------------------------------------------------------------------
def verify_ar_biz_date(ui, ar_no, want, tries=3, gap_ms=1500):
    """回读刚保存的应收单日期。不符 → 抛错（在推专票之前拦下，避免错误沿链扩散）。"""
    if not ar_no or not want:
        return None
    from kdquery import KdQuery
    k_want = str(want).strip().replace("/", "-")
    kq = KdQuery(ui.page)
    got = {}
    for i in range(1, max(1, tries) + 1):
        try:
            rows = kq.rows("AR_receivable", "FBillNo,FDate", f"FBillNo = '{ar_no}'", top=5)
        except Exception as e:  # noqa: BLE001
            log.warn(f"服务端回读 {ar_no} 业务日期第 {i} 次失败：{str(e)[:110]}")
            rows = []
        if rows:
            got["FDate"] = str(rows[0].get("FDate") or "")[:10]
            break
        ui.page.wait_for_timeout(gap_ms)
    if not got.get("FDate"):
        log.warn(f"服务端回读 {ar_no} 未取到行（可能刚落库）—— 已跳过，批量末尾用 "
                 f"verify_batch 兜底复核")
        return None

    # 到期日：字段名不确定，逐个候选试（失败不致命，仅记录）
    for fld in ("FENDDATE_H", "FENDDATE", "FDUEDATE"):
        try:
            r2 = kq.rows("AR_receivable", f"FBillNo,{fld}", f"FBillNo = '{ar_no}'", top=5)
            if r2:
                got[fld] = str(r2[0].get(fld) or "")[:10]
                break
        except Exception:  # noqa: BLE001
            continue

    due = next((v for k, v in got.items() if k != "FDate" and v), "")
    if got["FDate"] == k_want:
        log.ok(f"服务端回读 {ar_no}：业务日期 {got['FDate']} ✓"
               + (f" / 到期日 {due}" if due else "（到期日字段未取到）"))
        return got["FDate"]
    raise RuntimeError(
        f"★ 服务端回读**日期不符**：{ar_no} 业务日期 = {got['FDate']}，应为 {k_want}"
        f"（到期日 {due or '未取到'}）。这会让单号年月变成 -{got['FDate'][:4]}{got['FDate'][5:7]}-，"
        f"**已停止，不再下推专票/凭证**（SF-55）。请人工确认后处理这张应收单，再重跑。")


# ------------------------------------------------------------------
# 步骤 0：出库单列表筛选 + 勾选
# ------------------------------------------------------------------
def filter_outstock(ui, args):
    # ★ 先清掉重复的同名**列表**标签。金蝶 SPA 里同名标签会互相干扰：实测两个
    #   「销售出库单列表」并存时，过滤条件被读到另一个标签的实例上，
    #   结果 4 张单只筛出 3 张（静默漏单，最容易造成账实不符）。
    #
    # ★★ 2026-10-03 修复（本批剩余 8 家「第一家就挂、且家家都挂」的死锁根因）：
    #   原逻辑是 `if len(tabs) != len(set(tabs)): close_all_tabs(keep_names=[])`
    #   —— **一发现任何同名标签就清空全部标签**。可界面上会**永久驻留**
    #   两个关不掉的孤儿草稿页「应收单 - 新增」（内容面板已销毁、✕ 点了无反应，
    #   实测 `locator.click` 也无效），于是：
    #     ① 孤儿造成"有重复标签" → 清空全部标签，**把「销售出库单列表」也关掉**
    #     ② 只能退回顶部搜索重开 → 而此刻**激活页是一张单据表单**，
    #        它的 `.kd-form-collapse-container` 浮层正好盖住搜索结果条目
    #        → `locator.click` 报 `intercepts pointer events` 超时
    #     ③ ⇒ 第一家就失败；下一家重复同样的循环 → 连败熔断
    #   修法：
    #     ① 判重只看**列表类**标签（`…列表`），孤儿草稿页（`- 新增/- 修改`）不算；
    #     ② 无论如何**永远保留**「销售出库单列表」这个锚点；
    #     ③ 同名锚点只留一个（逐个关掉多余的），而不是清空全部。
    ANCHOR = "销售出库单列表"
    tabs = ui.tabs()
    names = [(t.get("text") or "").strip() for t in tabs]
    same_anchor = [t for t in tabs if (t.get("text") or "").strip() == ANCHOR]
    list_dupes = {x for x in names if x.endswith("列表") and names.count(x) > 1}

    if len(same_anchor) > 1:
        log.warn(f"有 {len(same_anchor)} 个「{ANCHOR}」标签，只留最后 1 个，其余关掉")
        for t in same_anchor[:-1]:
            try:
                ui.close_tab(t)
            except Exception as e:  # noqa: BLE001
                log.warn(f"关重复锚点标签失败：{type(e).__name__}: {str(e)[:80]}")
        ui.page.wait_for_timeout(1200)
    elif list_dupes - {ANCHOR}:
        log.warn(f"发现重复的列表标签 {sorted(list_dupes)} —— "
                 f"清掉非锚点标签（**保留 {ANCHOR}**）")
        ui.close_all_tabs(keep_names=[ANCHOR], quiet=True)
        ui.page.wait_for_timeout(1200)

    ui.open_outstock_list()
    bill_nos = [b.strip() for b in (args.bill_nos or "").split(",") if b.strip()]
    if bill_nos:
        log.info(f"按单据编号筛选 {len(bill_nos)} 张：{bill_nos}")
        ui.set_bill_no_filter(bill_nos)
        want = bill_nos
    elif args.date_from and args.date_to:
        log.info(f"按日期(FDate)筛选 {args.date_from} ~ {args.date_to}")
        ui.set_date_filter(args.date_from, args.date_to, field="FDate")
        want = None
    else:
        raise SystemExit("必须给 --bill-nos 或 --date-from/--date-to 之一")

    rows = ui.grid_all_rows() or []
    # 注意：出库单列表是**明细级扁平表**（一张单 N 个物料 = N 行），行数 ≠ 单据数
    bills = {str(r.get("FBILLNO") or "").strip() for r in rows if r.get("FBILLNO")}
    log.info(f"筛选后 {len(rows)} 行明细，覆盖 {len(bills)} 张单据")
    if not rows:
        raise SystemExit("筛选后没有任何出库单，停止")

    # ★ 硬校验：期望单据必须一张不少、也不能多出别的单
    if want:
        missing = [b for b in want if not any(b in x for x in bills)]
        extra = [x for x in bills if not any(w in x for w in want)]
        if missing or extra:
            log.err(f"筛选结果与预期不符 —— 缺 {missing}，多 {extra}")
            log.err("停止：少推/多推单据都会造成账实不符，请人工核对后再跑")
            raise SystemExit(3)
        log.ok(f"单据核对通过：{len(bills)} 张全部命中")

    if args.customer:
        custs = {str(r.get("FCUSTOMERID_FNAME") or "") for r in rows}
        if custs != {args.customer}:
            # ★★ 2026-10-28 硬拦截（苏州山鹰错账根因）：以前这里只 warn 不拦，
            #   --customer 和 --bill-nos 错配时（customer 写苏州山鹰、bill-no 却是
            #   安徽济丰的 0129），金额被 target 强制对齐、客户跟着出库单走，
            #   结果生成了「客户=安徽济丰、金额=苏州山鹰」的错账应收单。
            #   ⇒ 改成 SystemExit 硬停，客户对不上绝不往下推。
            shot(ui.page, SHOT_DIR, "customer_mismatch", log)
            log.err(f"客户核对失败：筛选结果客户是 {sorted(custs)}，"
                    f"与 --customer {args.customer!r} 不一致")
            log.err("停止：--customer 与 --bill-nos/日期筛出的单据客户不符，"
                    "可能单号写错。请核对后重跑（宁可挂起，不推错账）。")
            raise SystemExit(4)
        log.ok(f"客户核对一致：{args.customer}")

    log.info(f"清空选中前，当前已选 {ui.selected_count()} 行")
    ui.clear_selection()
    if want:
        sel = ui.select_rows(want)
    else:
        sel = ui.select_all_rows()
    real = ui.selected_count()
    log.info(f"勾选结果：命中 {sel} 行；真实选中 {real}")
    # ★★ 2026-10-31 硬护栏（SF-58 出库单侧，杰翙 AR-0018 复盘）：
    #   7 张单 39 行明细，日志实测「命中 39 行 / 真实选中 38 行」，旧代码只 WARN 就继续下推
    #   → 应收单少 1 行（TLA8白浆 200kg / 2,400.00），随后「金额对齐」把这 2,123.87
    #     摊进 seq=7 那行不含税 → 表面金额与下推表一致、数量却少 200kg（静默错账）。
    #   ⇒ 勾选不齐一律先「表头全选」重试，仍不齐就**抛错停下**，绝不带着漏行往下推。
    if real != sel:
        log.warn(f"逐行勾选漏行（{real}/{sel}，疑虚拟滚动截断），改用表头全选重勾")
        ui.clear_selection()
        sel2 = ui.select_all_rows()
        real = ui.selected_count()
        log.info(f"全选后：本期选中 {sel2} 行，真实选中 {real}")
        sel = sel2
    if real != sel:
        shot(ui.page, SHOT_DIR, "outstock_select_truncated", log)
        log.err(f"出库单勾选行数不符：命中 {sel} 行，真实选中 {real} 行（漏 {sel - real} 行）")
        log.err("停止：下推会漏行；漏行后「金额对齐」会把差额摊到别的行，"
                "造成金额对、数量错。请人工核对后重跑。")
        raise RuntimeError(
            f"出库单勾选不齐：命中 {sel} 行，真实选中 {real} 行 —— 拒绝下推")
    return rows, sel


# ------------------------------------------------------------------
# 步骤 1：下推应收单（含折扣分摊 / 金额对齐）
# ------------------------------------------------------------------
def step_receivable(ui, args, outstock_rows):
    ui.push_to_receivable(merge=True)
    if not ui.activate_tab("应收单 - 新增", timeout_ms=10000):
        raise RuntimeError("没等到「应收单 - 新增」标签，下推可能失败")
    ui.page.wait_for_timeout(2500)

    # ★ 2026-10-03（防御性兜底，非根因）：实测界面上明细每行都有物料、无空行，
    #   报错「第1行分录 物料必录」实为金蝶数据层分录/金额联动重算未收敛所致。
    #   这里仍删一遍空行作兜底（万一有脏行），但真正防「物料必录」靠的是
    #   save_bill(check_error=True) 在保存后检测报错弹窗并 raise（不再静默错账）。
    ui.detail_drop_empty_rows("receivable")

    if args.biz_date:
        # ★ 2026-10-03（SF-55）：此调用**只认前台表单 + 写后回读**，不一致会重写/抛错。
        #   `linked="FENDDATE_H"` 是**关键**：实测 7/7 完全相关 ——
        #   到期日跟着变 → 保存后服务端业务日期正确；到期日不跟 → 保存后落成"今天"、
        #   单号年月错成 -202610-（widget 回读此时**看不出来**，它总是显示新值）。
        ui.fill_bill_field("FDATE", args.biz_date, linked="FENDDATE_H")
        d1 = (ui.read_foreground_field("FDATE") or {}).get("val")
        d2 = (ui.read_foreground_field("FENDDATE_H") or {}).get("val")
        log.info(f"应收单业务日期 = {args.biz_date}  →  界面回读 FDATE={d1!r} / 到期日={d2!r}")

    detail = ui.detail_amounts("receivable")
    log.info(f"应收单明细 {len(detail)} 行，价税合计 {_money(sum(r['all'] for r in detail))}")
    for i, r in enumerate(detail[:3], 1):
        log.info(f"  行{i}: seq={r['seq']} 价税合计={_money(r['all'])} 不含税={_money(r['notax'])}")

    base_amt = sum(r["all"] for r in detail)
    if args.customer and outstock_rows:
        # ★★ 2026-10-28 金额一致性硬护栏（安徽济丰错账根因 2/2）：
        #   安徽济丰出库单 119906.17，下推应收单明细却只有 49035.38（差 70870.79），
        #   旧代码这里只打日志不拦 → 错账落库。
        #
        #   「来源出库单价税合计」权威口径 = 服务端 SAL_OUTSTOCK.FBillAllAmount（单头）。
        #   ⚠️ 不能用界面列表的 FALLAMOUNTFOR / FALLAMOUNTFOR_D：
        #      - docs/界面结构速查.md 记「SAL_OUTSTOCK.FALLAMOUNTFOR 不存在」；
        #      - grid_all_rows() 的出库单列表是明细级扁平表，dataSource 里根本没有
        #        价税合计列（实测字段仅 FDATE/FBILLNO/FCUSTOMERID_FNAME/物料等）。
        #   所以从 outstock_rows 提取去重单据号，逐张查 FBillAllAmount 再求和。
        try:
            from kdquery import KdQuery
            _kq = KdQuery(ui.page)
            src_nos = sorted({str(r.get("FBILLNO") or "").strip()
                              for r in outstock_rows if r.get("FBILLNO")})
            ob = to_d(0)
            missing_src = []
            for _no in src_nos:
                _sr = _kq.rows("SAL_OUTSTOCK", "FBillNo,FBillAllAmount",
                               f"FBillNo = '{_no}'", top=5)
                if _sr and _sr[0].get("FBillAllAmount") is not None:
                    ob += to_d(_sr[0].get("FBillAllAmount") or 0)
                else:
                    missing_src.append(_no)
            if missing_src:
                log.warn(f"服务端查不到这些出库单的价税合计（跳过该部分核对）：{missing_src}")
            if ob:
                log.info(f"来源出库单价税合计 {_money(ob)} vs 应收单明细 {_money(base_amt)}"
                         f"（差 {_money(base_amt - ob)}）")
                # ★ 容差 = 0：正常下推应收单明细价税合计应**精确等于**来源出库单价税合计
                #   （明细 1:1 继承，四舍五入已在源头做过）。有差 = 漏行/错单，直接停。
                if base_amt != ob:
                    log.err(f"金额核对失败：来源出库单价税合计 {_money(ob)} "
                            f"vs 应收单明细 {_money(base_amt)}，差 {_money(base_amt - ob)}")
                    log.err("停止：下推后应收单金额与来源出库单不一致（可能漏行/错单），"
                            "不保存、不下推。请人工核对后重跑。")
                    raise RuntimeError(
                        f"应收单金额核对不通过：出库单 {ob:.2f} vs 应收单 {base_amt:.2f} "
                        f"（差 {base_amt - ob:+.2f}）")

                # ★★ 2026-10-31 追加（SF-63）：金额相等 ≠ 行数相等。
                #   杰翙 AR-0018：出库单 39 行，勾选漏 1 行 → 应收单 38 行、少 TLA8白浆 200kg，
                #   但「金额对齐」把差额 2,123.87 摊给 seq=7 → 价税合计分毫不差、数量少 200kg。
                #   ⇒ 行数与数量必须与来源出库单同源；不等即停（对齐之前就该拦住）。
                _cnt, _qty = 0, 0.0
                for _no in src_nos:
                    _er = _kq.rows("SAL_OUTSTOCK", "FBillNo,FRealQty",
                                   f"FBillNo = '{_no}'", top=500)
                    _cnt += len(_er)
                    _qty += sum(float(x.get("FRealQty") or 0) for x in _er)
                _ar_qty = 0.0
                for _r in detail:
                    _raw = _r.get("raw") or {}
                    for _k in ("FPRICEQTY", "FQTY", "FPRICEQTY_D"):
                        if _raw.get(_k) not in (None, ""):
                            try:
                                _ar_qty += float(str(_raw[_k]).replace(",", ""))
                                break
                            except (TypeError, ValueError):
                                continue
                log.info(f"来源出库单 {_cnt} 行 / 数量 {_qty:g} vs 应收单 {len(detail)} 行"
                         f" / 数量 {_ar_qty:g}")
                if _cnt and len(detail) != _cnt:
                    log.err(f"明细行数不符：出库单 {_cnt} 行 vs 应收单 {len(detail)} 行"
                            f"（漏 {_cnt - len(detail)} 行）")
                    raise RuntimeError(
                        f"应收单明细行数不符：来源出库单 {_cnt} 行 vs 应收单 {len(detail)} 行 "
                        f"—— 勾选漏行，拒绝保存（金额对齐会把差额摊到别的行）")
                if _qty and _ar_qty and abs(_ar_qty - _qty) > 0.001:
                    log.err(f"数量合计不符：出库单 {_qty:g} vs 应收单 {_ar_qty:g}"
                            f"（差 {_ar_qty - _qty:+g}）")
                    raise RuntimeError(
                        f"应收单数量合计不符：来源出库单 {_qty:g} vs 应收单 {_ar_qty:g} "
                        f"（差 {_ar_qty - _qty:+g}）—— 拒绝保存")
        except RuntimeError:
            raise
        except Exception as _e:  # noqa: BLE001
            log.warn(f"金额核对服务端查询异常，本次跳过核对：{str(_e)[:120]}")

    # ---- 折扣分摊 ----
    if args.discount is not None:
        log.info(f"★ 折扣分摊：扣款 {args.discount:,.2f}，基数 {_money(base_amt)}")
        discs, info = ui.fill_discount_by_ratio(args.discount, kind="receivable")
        s = ui.detail_summary("receivable")
        log.info(f"分摊后合计：折扣 {_money(s['disc'])} / 不含税 {_money(s['notax'])}"
                 f" / 税额 {_money(s['tax'])} / 价税合计 {_money(s['all'])}")
        assert info["sum_after_fix"] == to_d(args.discount), "折扣合计没落到扣款值"

    # ---- 尾差对齐 ----
    tn = args.target_notax or None
    tt = args.target_tax or None
    ta = args.target_all or None
    if tn or tt or ta:
        log.info(f"★ 金额对齐：不含税→{tn or '(不校)'}  税额→{tt or '(不校)'}"
                 f"  价税合计→{ta or '(不校)'}")
        s = ui.detail_align_totals(target_notax=tn, target_tax=tt, target_all=ta,
                                   kind="receivable", max_gap="1.00")
        if not s["ok"]:
            shot(ui.page, SHOT_DIR, "align_not_ok", log)
            # ★★ 2026-10-03 修复（SF-01 变体）：以前这里只 warn 然后照样 save_bill()，
            #   实测浙江至优 AR105-202610-0007 折扣末两行没落库（少填 765.00），
            #   价税 96667.50 ≠ 目标 95902.50，4 轮对齐不达标却仍然保存 → 账静默做错。
            #   ⇒ 硬护栏：对齐不达标 = **停止，不保存**。宁可这一家失败挂起等人工，
            #     也绝不让带错金额的单据落库（下推专票/凭证都是基于它，错了会一路错下去）。
            #   2026-10-03 追加「价税合计」到报错里（三个数精确到分口径）。
            raise RuntimeError(
                f"金额对齐未达标，**已停止保存**（不允许带错金额落库）："
                f"不含税 {s['notax']:.2f} / 税额 {s['tax']:.2f}"
                f" / 价税 {s['all']:.2f}"
                f"  目标 不含税 {tn or '(不校)'} / 税额 {tt or '(不校)'}"
                f" / 价税 {ta or '(不校)'}"
                f"  —— 请检查折扣额是否逐行落库（detail_amounts 回读），人工复核后重跑")

    if args.dry_run:
        log.warn("--dry-run：不保存、不下推，到此为止")
        return None

    ui.save_bill()
    ui.page.wait_for_timeout(3000)
    ar_no = _read_no(ui, "FBillNo")
    if not ar_no:
        src = [str(r.get("FBILLNO") or "").strip() for r in (outstock_rows or [])]
        ar_no = _backfill_no(ui, "ar", since=_since_str(3),
                             customer=args.customer or None,
                             src_bills=[b for b in src if b] or None)
    log.ok(f"应收单已保存：{ar_no or '(单号未读到)'}")
    # ★ 2026-10-03（SF-55）：**服务端**回读业务日期。界面回显/日志都不算数，
    #   ExecuteBillQuery 才算数。不符就在推专票之前拦下，别让错误沿链扩散。
    verify_ar_biz_date(ui, ar_no, args.biz_date)
    shot(ui.page, SHOT_DIR, "pushchain_receivable", log)
    return ar_no


# ------------------------------------------------------------------
# 步骤 2：应收单下推专票（分录合并）
# ------------------------------------------------------------------
def step_invoice(ui, args, ar_no):
    if not ar_no:
        raise RuntimeError("没有应收单号，无法下推专票（请用 --step receivable 先做，或 --ar-no 指定）")
    merge = not args.no_invoice_merge
    log.info(f"★ 下推专票转换规则 = {'分录合并' if merge else '分录不合并（逐行对应，不产生子单）'}")
    # ★ 2026-10-03 修复：先把「应收单 - 新增」这类占单标签关掉再打开应收单列表。
    #   否则那张单的 `.kd-form-collapse-container` 会盖在功能搜索面板上面，
    #   点搜索结果时被拦成 `intercepts pointer events` 超时（SF-32 的变体）——
    #   实测 2026-10-03 首家就卡在这里，报：
    #     Locator.click: Timeout 6000ms / <div class="kd-form-collapse-container"> intercepts
    #   应收单此刻已保存并提交，标签留着也没用；销售出库单列表要保留（下一组起点）。
    closed = ui.close_all_tabs(keep_names=["销售出库单列表"], quiet=True)
    if closed:
        log.info(f"打开「应收单列表」前先关掉占单标签：{closed}")
        ui.page.wait_for_timeout(800)
    ui.open_receivable_list()
    # ★★ 2026-10-29（河南华洋事故）：**开页 ≠ 激活页**。
    #   若当前激活的还是「销售出库单列表」，后面的「按单号过滤 AR…」会作用在
    #   **出库单列表**上 → 0 行 → 后面报「找不到可见的列表网格」。
    #   这里显式确认激活页，不是就切过去，切不过去直接停。
    _cur = ui.active_tab() or ""
    if "应收单列表" not in _cur:
        log.warn(f"激活页不是应收单列表（当前 {_cur!r}）→ 强制切换")
        ui.activate_tab("应收单列表", timeout_ms=8000)
        ui.page.wait_for_timeout(1200)
        _cur = ui.active_tab() or ""
        if "应收单列表" not in _cur:
            raise RuntimeError(
                f"应收单列表未激活（当前 {_cur!r}），不能在错误页面上过滤/勾选，已停止")
    ui.clear_selection()
    # ★ 2026-10-27 修复：应收单列表是虚拟滚动（total 可能上千行、分页 200 行/页），
    #   --ar-no 复用的目标单号大概率不在第一页 → select_rows 扫不到会抛「找不到可见网格」。
    #   先按单据编号过滤成只这一张，让它排到第一页再勾。
    try:
        n_f = ui.set_bill_no_filter([ar_no], field="FBillNo", verify=True)
        log.info(f"按单号过滤 {ar_no} → 列表 {n_f} 行")
    except Exception as e:  # noqa: BLE001
        log.warn(f"按单号过滤失败（{str(e)[:100]}），退回直接勾选（可能翻不到页）")
    n = ui.select_rows([ar_no])
    real = ui.selected_count()
    log.info(f"应收单列表勾选 {ar_no} → 命中 {n} 行，真实选中 {real} 行")
    # ★★ 2026-10-28 护栏（华淳蓝字错账 SF-58）：应收单明细多行时，select_rows 勾选
    #   会被虚拟滚动截断（77 行只真实选中 76 行）→ 专票漏 1 行、金额少 520 元。
    #   判据用「服务端应收单明细行数」作权威基准（不依赖界面的反证源，见 MEMORY SF-56）：
    #   界面的 select_rows 命中数 n 可能因虚拟滚动漏扫而偏小（n=76 且 real=76 也能漏），
    #   所以必须拿 ExecuteBillQuery 的真实明细行数 expected 来卡。
    try:
        from kdquery import KdQuery
        _kq = KdQuery(ui.page)
        _detail = _kq.rows("AR_receivable", "FBillNo,FMATERIALID.FNumber",
                           f"FBillNo = '{ar_no}'", top=2000)
        expected = len(_detail)
    except Exception as e:  # noqa: BLE001
        expected = None
        log.warn(f"读应收单服务端明细行数失败（{str(e)[:100]}），退回只卡命中数")
    log.info(f"应收单 {ar_no} 服务端明细行数 = {expected}，命中 {n}，真实选中 {real}")
    if expected is not None and real != expected:
        # 已按单号过滤成只这一张单，全选 = 选中其全部明细行，天然不漏（不依赖逐行滚动点击）。
        log.warn(f"逐行勾选漏行（{real}/{expected}），改用表头全选重勾")
        ui.clear_selection()
        real = ui.select_all_rows()
        log.info(f"全选后真实选中 {real} 行")
    if expected is not None and real != expected:
        shot(ui.page, SHOT_DIR, "select_truncated", log)
        raise RuntimeError(
            f"应收单 {ar_no} 勾选行数不符：服务端明细 {expected} 行，命中 {n} 行，真实选中 {real} 行 —— "
            f"虚拟滚动/分页导致漏勾，专票会少行错账。已停止，请人工核对后重试。")
    if expected is None and (real is None or real < n):
        shot(ui.page, SHOT_DIR, "select_truncated", log)
        raise RuntimeError(
            f"应收单 {ar_no} 勾选被截断：命中 {n} 行，真实选中 {real} 行 —— "
            f"虚拟滚动/分页导致漏勾，专票会少行错账。已停止，请人工检查应收单明细行数后重试。")
    ui.push_to_invoice(merge=merge)
    if not ui.activate_tab("销售增值税专用发票 - 新增", timeout_ms=10000):
        raise RuntimeError("没等到专票标签，下推可能失败")
    ui.page.wait_for_timeout(2500)

    detail = ui.detail_amounts("invoice")
    tot = sum(r["all"] for r in detail)
    log.info(f"专票明细 {len(detail)} 行，价税合计 {_money(tot)}"
             f"（不含税 {_money(sum(r['notax'] for r in detail))}"
             f" / 税额 {_money(sum(r['tax'] for r in detail))}）")
    if args.target_notax and abs(sum(r["notax"] for r in detail) - to_d(args.target_notax)) > to_d("0.05"):
        log.warn("专票不含税与目标差 > 0.05，检查应收单是否正确")
    else:
        log.ok("专票金额与目标一致（合并按物料汇总舍入，不合并逐行继承，应收单调平则专票天然对上）")
    if merge and len(detail) < 20:
        log.info(f"（分录合并生效：46 行应收 → {len(detail)} 行专票；"
                 f"若出现 -00000N 子单，改用 --no-invoice-merge）")

    ui.backfill_invoice_info(
        drawer=None, biz_date=args.biz_date or None,
        invoice_date=args.invoice_date or None,
        checker=args.checker or (CFG.get("rules", {}) or {}).get("invoice_checker"),
    )
    log.info("已回填 业务日期/发票日期/复核人（开票人保留系统默认，发票号不填）")

    if args.dry_run:
        log.warn("--dry-run：不保存、不提交，到此为止")
        return ""
    ui.save_bill()
    ui.page.wait_for_timeout(3000)
    inv_no = _read_no(ui, "FBillNo")
    if not inv_no:
        inv_no = _backfill_no(ui, "iv", since=_since_str(3))
    inv_no = _verify_iv_no(ui, inv_no, ar_no)   # ★ 服务端反证：读数必须能被来源应收单反查到
    log.ok(f"专票已保存：{inv_no or '(单号未读到)'}")
    shot(ui.page, SHOT_DIR, "pushchain_invoice", log)
    return inv_no


# ------------------------------------------------------------------
# 步骤 3：生成凭证 + 清场
# ------------------------------------------------------------------
def step_voucher(ui, inv_no):
    # ★ 必须先关掉占着这张单据的标签，否则报「该业务单据正在被 XX 使用，本次不生成凭证」
    closed = ui.close_all_tabs(keep_names=["销售增值税专用发票列表", "销售出库单列表"], quiet=True)
    log.info(f"生成凭证前清占单标签：关掉 {closed}")

    ui.open_invoice_list()
    # ★ 2026-10-29：同 step_invoice，开页 ≠ 激活页；在错误的列表上勾专票号会勾空。
    _cur = ui.active_tab() or ""
    if "销售增值税专用发票列表" not in _cur:
        log.warn(f"激活页不是专票列表（当前 {_cur!r}）→ 强制切换")
        ui.activate_tab("销售增值税专用发票列表", timeout_ms=8000)
        ui.page.wait_for_timeout(1200)
        _cur = ui.active_tab() or ""
        if "销售增值税专用发票列表" not in _cur:
            raise RuntimeError(f"专票列表未激活（当前 {_cur!r}），已停止（不在错误页面上勾单）")
    ui.clear_selection()
    if not inv_no:
        # ★ 不能退化到 select_all_rows()：专票列表里可能还有别的未审核/已审核单据，
        #   一旦全勾选就会给**所有**专票生成凭证（实测踩过界面读不到 FBILLNO 的情况）。
        #   宁可停下报错，也不要生成无关凭证。
        raise RuntimeError(
            "专票号读不到，拒绝在专票列表上「全选」生成凭证 —— "
            "请用 --step invoice 先保存专票，再用查询拿到单号后指定 --inv 生成，"
            "或改用 src/gy3_4_voucher.py --inv <专票号> --go")
    ui.select_rows([inv_no])
    log.info(f"勾选专票 {inv_no}")

    ui.generate_voucher()
    errs = ui.voucher_report_errors()
    if errs:
        for e in errs:
            log.err(f"凭证生成失败 {e.get('单据编号')} [{e.get('检查项')}] {e.get('提示信息')}")
        shot(ui.page, SHOT_DIR, "pushchain_voucher_error", log)
        raise RuntimeError("凭证生成报告有错误，停止")
    log.ok("凭证生成报告 0 错误")


# ------------------------------------------------------------------
# dry-run：只读验算（不产生任何单据）
# ------------------------------------------------------------------
def dry_run_report(ui, args):
    """只读模式：打印筛选结果；若页面上正好开着「应收单 - 新增」，
    就顺手把折扣分摊与对齐方案算一遍给你看，**不写任何单元格、不保存**。"""
    tab = next((t for t in ui.tabs()
                if "应收单" in (t.get("text") or "") and "新增" in (t.get("text") or "")), None)
    if not tab:
        log.info("（当前没有打开的「应收单 - 新增」页，跳过分摊验算。）")
        log.info("  想看分摊结果：把一张应收单开在页面上再跑 --dry-run，"
                 "或直接不带 --dry-run 走正式流程。")
        return
    ui.activate_tab(tab["text"], timeout_ms=8000)
    ui.page.wait_for_timeout(2000)
    rows = ui.detail_amounts("receivable")
    if not rows:
        log.warn("应收单明细读不到行")
        return

    base = sum(r["all"] for r in rows)
    log.info(f"[验算] 应收单明细 {len(rows)} 行，价税合计 {_money(base)}")

    if args.discount is not None:
        from discount import allocate_by_amount
        discs, info = allocate_by_amount([r["all"] for r in rows], to_d(args.discount))
        log.info(f"[验算] 扣款 {info['want']:.2f}  逐行舍入合计 {info['sum_before_fix']:.2f}"
                 f"  尾差 {info['gap']:+.2f}"
                 + (f" → 补到最大行 seq={rows[info['anchor']]['seq']}" if info["anchor_fixed"] else ""))
        for i, (r, d) in enumerate(zip(rows, discs), 1):
            if i <= 5 or i == info["anchor"] + 1:
                log.info(f"   行{i} seq={r['seq']:>3} 含税={_money(r['all']):>12}"
                         f" 折扣={_money(d):>9}")

    if args.target_notax or args.target_tax:
        from discount import plan_alignment
        plan = plan_alignment(rows, target_notax=args.target_notax or None,
                              target_tax=args.target_tax or None)
        log.info(f"[验算] 当前 不含税 {_money(plan['cur']['notax'])}"
                 f" / 税额 {_money(plan['cur']['tax'])}"
                 f" / 价税合计 {_money(plan['cur']['all'])}")
        for k, label in (("notax", "不含税"), ("tax", "税额")):
            if k in plan["diff"]:
                log.info(f"[验算] {label} 差 {plan['diff'][k]:+.2f}"
                         f" → 锚点行 seq={plan['anchor_seq']} 建议改成 {_money(plan['suggest'][k])}")
        log.info(f"[验算] 是否需要校正：{plan['need_fix']}")
        log.info("       注：金蝶反算走单价会差 1~2 分，正式跑时以页面读数为准做闭环收敛。")
    log.warn("--dry-run：全程只读，没有下推、没有保存")


def run_invoice_stage(ui, args, ar_no, guard):
    """专票 → (可选)提交 → 凭证。--ar-no 复用已有应收单时也从这里进。"""
    inv_no = step_invoice(ui, args, ar_no)
    if args.step == "invoice":
        log.ok(f"按 --step invoice 停止（专票 {inv_no or '单号未读到'} 已保存，未提交）")
        return inv_no
    guard.check("submit", "提交专票（本组织实测：提交即审核）")
    ui.submit_bill()
    ui.page.wait_for_timeout(2500)
    log.ok("专票已提交（提交即审核）")

    guard.check("voucher", "生成总账凭证")
    step_voucher(ui, inv_no)
    return inv_no


def main():
    args = parse_args()
    if args.dry_run and args.yes:
        log.warn("--dry-run 与 --yes 同时给了：dry-run 优先，不会写任何东西")

    # ★ 折扣额口径（2026-10-03 用户定）：下推表里折扣给**负数**，但金蝶「折扣额」字段
    #   必须录**正数**（正=打折）。这里统一 abs，作为最底层兜底——即使运行器漏转负号，
    #   录进金蝶的也一定是正数，不会把「折扣」录成「加价」。
    if args.discount is not None and args.discount < 0:
        log.warn(f"--discount 收到负数 {args.discount}，按口径取绝对值（录金蝶为正数）")
        args.discount = abs(args.discount)

    t0 = time.time()

    def _tick(label):
        log.info(f"⏱ {label}：累计 {time.time() - t0:.1f}s")

    guard = Guard(CFG, log)
    if args.yes and not args.dry_run:
        for k in ("auto_submit", "auto_audit", "auto_voucher", "auto_voucher_save"):
            guard.s[k] = True
        log.warn("已用 --yes 放行全部不可逆动作（提交 / 审核 / 生成凭证）")

    with sync_playwright() as p:
        log.info(f"连接调试浏览器 {CFG['browser']['cdp_url']}")
        browser = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pages = [pg for c in browser.contexts for pg in c.pages]
        if not pages:
            raise SystemExit("调试浏览器里没有页面，请先跑「启动调试浏览器.bat」并登录金蝶")
        page = pages[0]
        ui = KingdeeUI(page, CFG, log, guard)
        log.info(f"接管页面：{page.title()[:40]!r}")
        _tick("接管浏览器")

        ar_no = (args.ar_no or "").strip()
        if ar_no:
            log.info(f"★ --ar-no 复用已有应收单 {ar_no}：跳过筛选与建单")
        else:
            rows, sel = filter_outstock(ui, args)
            _tick("出库单筛选+勾选")

            if args.dry_run:
                dry_run_report(ui, args)
                browser.close()
                log.close()
                return 0

            # ---- 应收单（含折扣分摊 + 金额对齐）----
            ar_no = step_receivable(ui, args, rows)
            if args.step == "receivable":
                log.ok("按 --step receivable 停止（应收单已保存，未提交）")
                _tick("出库单→应收单")
                browser.close()
                log.close()
                return 0

            guard.check("submit", "提交应收单（本组织实测：保存即审核）")
            ui.submit_bill()
            ui.page.wait_for_timeout(2000)
            log.ok("应收单已提交（保存即审核）")
            _tick("出库单→应收单(含提交)")

        # ---- 专票 → 提交 → 凭证 + 清场 ----
        inv_no = run_invoice_stage(ui, args, ar_no, guard)
        _tick("应收单→专票→凭证")
        if not args.no_clean_tabs:
            closed = ui.close_all_tabs(keep_names=KEEP_TABS, quiet=True)
            if not any("销售出库单列表" in (t.get("text") or "") for t in ui.tabs()):
                ui.open_outstock_list()
            log.ok(f"清场：关掉 {closed} 个标签，剩下 {[t.get('text') for t in ui.tabs()]}")

        log.ok(f"全部结束：应收单 {ar_no or '-'} / 专票 {inv_no or '-'} / 总耗时 {time.time() - t0:.1f}s")
        browser.close()
    log.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GuardError as e:
        log.err(f"护栏拦截：{e}")
        sys.exit(2)
