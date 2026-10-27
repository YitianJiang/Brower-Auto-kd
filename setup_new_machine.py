# -*- coding: utf-8 -*-
"""
新机器一键部署脚本
--------------------
把「clone 仓库 → 装依赖 → 补 config → 装 skill → 启动浏览器 → 自检」这套
换机流程自动化。在**刚 clone 下来的新机器**上运行一次即可。

用法：
    python setup_new_machine.py            # 只做「装依赖 + 补 config + 装 skill」
    python setup_new_machine.py --all      # 上面三步 + 启动浏览器 + 跑 precheck
    python setup_new_machine.py --skip-skill   # 跳过 skill 安装（已装过）

不强制 utf-8 输出：cmd / Git Bash 按 GBK 解码控制台，强制 utf-8 会乱码。
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_SRC = ROOT / "skill"
SKILL_DST = Path.home() / ".workbuddy" / "skills" / "kingdee-k3cloud-browser-rpa"


def log(tag, msg):
    print(f"[{tag}] {msg}")


def step(n, title):
    print("\n" + "=" * 68)
    print(f"  步骤 {n}：{title}")
    print("=" * 68)


def find_python():
    """找一个能用的 python 解释器，优先 WorkBuddy 托管的。"""
    # 1) WorkBuddy 托管的 python（Windows 常见路径）
    managed = [
        Path.home() / ".workbuddy" / "binaries" / "python" / "versions",
    ]
    for base in managed:
        if base.exists():
            # 找 versions 下任意 python.exe
            for p in base.rglob("python.exe"):
                return str(p)
    # 2) 系统 python
    for cmd in ["python", "python3"]:
        try:
            r = subprocess.run([cmd, "--version"], capture_output=True, timeout=10)
            if r.returncode == 0:
                return cmd
        except Exception:
            continue
    return None


def run(cmd, desc=""):
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=False, cwd=str(ROOT))
    return r.returncode == 0


def install_deps(py):
    step(1, "安装 Python 依赖（playwright / openpyxl / PyYAML）")
    req = ROOT / "requirements.txt"
    if not req.exists():
        log("警告", "没有 requirements.txt，跳过依赖安装")
        return True
    # 优先用清华镜像（国内直连 pypi 常失败）
    ok = run([py, "-m", "pip", "install", "-r", str(req),
              "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
    if not ok:
        log("警告", "清华镜像装依赖失败，改用默认源重试...")
        ok = run([py, "-m", "pip", "install", "-r", str(req)])
    # playwright 还需要装浏览器内核（Chromium）
    if ok:
        log("提示", "安装 Playwright 的 Chromium 内核（首次较慢）...")
        run([py, "-m", "playwright", "install", "chromium"])
    return ok


def make_config():
    step(2, "补本地配置 config.yaml")
    cfg = ROOT / "config.yaml"
    ex = ROOT / "config.example.yaml"
    if cfg.exists():
        log("提示", f"config.yaml 已存在，跳过。如需重置请先删除：{cfg}")
        return
    if not ex.exists():
        log("错误", "找不到 config.example.yaml，请手动创建 config.yaml")
        return
    shutil.copyfile(ex, cfg)
    log("完成", f"已从 config.example.yaml 生成 config.yaml")
    log("提示", "请打开 config.yaml，把 base_url 填成你们的金蝶云星空地址（登录页地址）")


def install_skill(skip=False):
    step(3, "安装 skill 包（供 WorkBuddy 加载）")
    if skip:
        log("提示", "--skip-skill，跳过 skill 安装")
        return
    if not SKILL_SRC.exists():
        log("警告", f"仓库里没有 skill/ 目录，跳过（{SKILL_SRC}）")
        return
    if SKILL_DST.exists():
        log("提示", f"skill 已存在：{SKILL_DST}，覆盖更新...")
        shutil.rmtree(SKILL_DST)
    shutil.copytree(SKILL_SRC, SKILL_DST)
    log("完成", f"skill 已安装到 {SKILL_DST}")
    log("提示", "重启 WorkBuddy / 新开会话后，「金蝶 RPA」相关任务会自动加载此 skill")


def start_browser(py):
    step(4, "启动调试浏览器（独立 Chrome，端口 9222）")
    sb = ROOT / "start_browser.py"
    if not sb.exists():
        log("错误", "找不到 start_browser.py")
        return
    log("提示", "会弹出一个独立 Chrome 窗口，请在窗口里登录金蝶后保持开启")
    run([py, str(sb)])


def precheck(py):
    step(5, "开跑前自检（验证页签选择器 / 主标签栏可达）")
    # 优先跑 run_batch 的 --precheck；没有就用 selftest_force_remove
    rb = ROOT / "src" / "run_batch_20261031.py"
    st = ROOT / "src" / "selftest_force_remove.py"
    if rb.exists():
        run([py, str(rb), "--precheck"])
    elif st.exists():
        run([py, str(st)])
    else:
        log("警告", "找不到自检脚本，跳过")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="含启动浏览器 + 自检（完整流程）")
    ap.add_argument("--skip-skill", action="store_true", help="跳过 skill 安装")
    args = ap.parse_args()

    print("=" * 68)
    print("  金蝶 RPA · 新机器一键部署")
    print("=" * 68)

    py = find_python()
    if not py:
        log("错误", "没找到可用的 Python。请先安装 Python 3.10+，或")
        log("错误", "确认 WorkBuddy 托管的 python 在 ~/.workbuddy/binaries/ 下。")
        input("\n按回车键退出...")
        return 1
    log("Python", py)

    install_deps(py)
    make_config()
    install_skill(args.skip_skill)

    if args.all:
        start_browser(py)
        precheck(py)

    print("\n" + "=" * 68)
    print("  部署完成")
    print("=" * 68)
    print("\n你只需手动做两件事（换机器必然要做的）：")
    print("  1. 在弹出的 Chrome 里【登录金蝶】，登录后保持窗口开着")
    print("  2. 把本期的「开票数据.xlsx」放进 data/ 目录")
    print("\n然后剩下的交给 WorkBuddy：")
    print("  对 WB 说：「金蝶批量下推」，WB 会读取 data/ 的数据、")
    print("  跑 src/run_batch_20261031.py 完成整条链。")
    if not args.all:
        print("\n提示：想一次做完（含启动浏览器+自检），下次用 --all")
    print("=" * 68)
    input("\n按回车键退出...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
