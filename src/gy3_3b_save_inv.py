# -*- coding: utf-8 -*-
"""
广源包装集团 2026-09 链 · 第 3b 步：保存**已打开**的专票页（补填复核人后保存）
==============================================================================
背景（2026-09-21 实测）：
  之前 gy3_3_invoice.py 报「已保存」，其实是**假成功** —— 保存对话框弹了必录校验：
      [开票信息.复核人] 是必填项，不能为空！
  复核人 = 字段 `F_PAEZ_COMBO`（kdcombo，无 kendo 实例，必须真实鼠标点选），
  历史口径：广源包装集团 2026-08 那张专票用的是「姚玉平」。

本脚本对**当前已打开的专票页**做：
  ① 补填复核人（已填则跳过）② 复核 4 个数（行数/不含税/税额/价税）+ 两个日期
  ③ 点保存 ④ 捕获一切可见弹窗文本 ⑤ 用 ExecuteBillQuery 回读确认落库
不做下推、不重建单据 —— 只把页面上这张存下去。

用法：
    python src/gy3_3b_save_inv.py                 # 复核人=姚玉平
    python src/gy3_3b_save_inv.py --reviewer 刘启丽
    python src/gy3_3b_save_inv.py --no-save       # 只体检不保存
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from common import load_config, abs_path, Logger, shot  # noqa: E402
from steps import KingdeeUI, eval_all_frames  # noqa: E402
from kdquery import KdQuery, num  # noqa: E402

CFG = load_config()
log = Logger(abs_path("logs", CFG), name="gy3_3b_save_inv")
SHOT_DIR = abs_path("screenshots", CFG)

# 期望值（应收单 AR105-202609-0005 下推而来）
EXP_ROWS = 46
EXP_NOTAX = 266824.88
EXP_TAX = 34687.22
EXP_ALL = 301512.10
EXP_DATE = "2026-09-30"

JS_DIALOGS = """() => {
    const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden'; };
    const g = e => (e.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 400);
    return {
        windows: Array.from(document.querySelectorAll('.k-window')).filter(vis).map(g).filter(Boolean),
        notices: Array.from(document.querySelectorAll('.k-notification, .k-tooltip, .kd-notification'))
                    .filter(vis).map(g).filter(Boolean),
        errors:  Array.from(document.querySelectorAll('.kd_ui_validate_warnning, .k-state-error'))
                    .filter(vis).map(e => String(e.value || e.innerText || '').slice(0, 80)).filter(Boolean),
    };
}"""


def norm(v):
    s = str(v or "").strip().replace("/", "-")
    p = s.split("-")
    if len(p) == 3 and all(x.isdigit() for x in p):
        return f"{int(p[0]):04d}-{int(p[1]):02d}-{int(p[2]):02d}"
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviewer", default="姚玉平")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--expect-rows", type=int, default=EXP_ROWS)
    args = ap.parse_args()

    with sync_playwright() as p:
        br = p.chromium.connect_over_cdp(CFG["browser"]["cdp_url"])
        pg = br.contexts[0].pages[0]
        ui = KingdeeUI(pg, CFG, log, None)
        kq = KdQuery(pg)

        log.info(f"当前标签 = {ui.active_tab()!r}")

        # 0) 关掉可能残留的弹窗（否则挡住控件，点击会落空）
        ui.close_stray_windows(max_iter=3)

        # 1) 身体检查：明细行数 + 三项金额
        detail = ui.detail_amounts("invoice")
        n = len(detail)
        s_notax = sum(r["notax"] for r in detail)
        s_tax = sum(r["tax"] for r in detail)
        s_all = sum(r["all"] for r in detail)
        log.info(f"明细 {n} 行  不含税 {s_notax:,.2f} / 税额 {s_tax:,.2f} / 价税 {s_all:,.2f}")
        ok = True
        for name, got, exp in (("行数", n, args.expect_rows),
                               ("不含税", float(s_notax), EXP_NOTAX),
                               ("税额", float(s_tax), EXP_TAX),
                               ("价税", float(s_all), EXP_ALL)):
            hit = abs(float(got) - float(exp)) <= 0.05 if isinstance(exp, float) else got == exp
            (log.ok if hit else log.warn)(f"  {name}: {got} vs 期望 {exp} {'✓' if hit else '✗'}")
            ok = ok and hit

        # 2) 补填复核人（已填则跳过）
        cur = ui.read_combo("F_PAEZ_COMBO") or {}
        if (cur.get("text") or "").strip() == args.reviewer:
            log.ok(f"复核人已是 {args.reviewer}，跳过")
        else:
            log.info(f"复核人当前 {cur!r} → 填 {args.reviewer}")
            ui.fill_combo("F_PAEZ_COMBO", args.reviewer)

        # 3) 复核两个日期
        got = ui.read_bill_fields(["FBILLNO", "FDATE", "FINVOICEDATE", "FDRAWER", "FPAYEE"])
        log.info(f"单头：{got}")
        for fld in ("FDATE", "FINVOICEDATE"):
            hit = norm(got.get(fld)) == norm(EXP_DATE)
            (log.ok if hit else log.warn)(f"  {fld} = {got.get(fld)} {'✓' if hit else '✗ 期望 ' + EXP_DATE}")

        shot(pg, SHOT_DIR, "gy3b_before_save", log)

        if args.no_save:
            log.info("--no-save：体检结束，不保存")
            br.close()
            return 0

        # 4) 保存 + 捕获弹窗
        ui.save_bill()
        pg.wait_for_timeout(3500)
        d = eval_all_frames(pg, JS_DIALOGS) or {}
        for k, v in d.items():
            if v:
                log.warn(f"保存后可见 {k}: {v}")
        if not any(d.values()):
            log.ok("保存后无任何弹窗/提示 ✓")

        # 5) 回读确认
        got = ui.read_bill_fields(["FBILLNO", "FDATE", "FINVOICEDATE"])
        log.info(f"回读：{got}")
        inv_no = str(got.get("FBILLNO") or "").strip()
        if inv_no:
            log.ok(f"★ 专票号 = {inv_no}")
        else:
            log.warn("⚠️ 界面上 FBILLNO 仍为空 —— 保存可能又失败了，见上面的弹窗")

        # 6) 用查询核（唯一权威口径）
        # ⚠️ FCreateDate 的过滤串必须是 'YYYY-MM-DD HH:MM:SS'（带 T 会报
        #    Conversion failed when converting date and/or time from character string）
        pg.wait_for_timeout(1000)
        rs = kq.rows("IV_SALESIC",
                     "FBillNo,FDATE,FINVOICEDATE,FDocumentStatus,FCreateDate,"
                     "FCustomerId.FName,F_PAEZ_COMBO,FDRAWER,FPAYEE",
                     "FCreateDate > '2026-09-21 20:00:00'", top=50)
        for r in rs:
            log.ok(f"  落库：{r.get('FBillNo')} 客户={r.get('FCustomerId.FName')} "
                   f"业务日={r.get('FDATE')} 发票日={r.get('FINVOICEDATE')} "
                   f"状态={r.get('FDocumentStatus')} 开票人={r.get('FDRAWER')} "
                   f"收款人={r.get('FPAYEE')} 复核人={r.get('F_PAEZ_COMBO')} "
                   f"创建={r.get('FCreateDate')}")
        log.info(f"  共 {len(rs)} 张新专票")
        if rs and inv_no:
            hit = kq.rows("IV_SALESIC", "FBillNo,FAllAmount",
                          f"FBillNo = '{inv_no}'", top=5)
            for h in hit:
                log.ok(f"  金额核对 {h.get('FBillNo')} 价税={num(h.get('FAllAmount')):.2f} "
                       f"(期望 {EXP_ALL})")
                if rs and len(rs) >= 1:
                    break

        shot(pg, SHOT_DIR, "gy3b_after_save", log)
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
