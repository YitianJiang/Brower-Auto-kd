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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_SRC = ROOT / "skill"
SKILL_DST = Path.home() / ".workbuddy" / "skills" / "kingdee-k3cloud-browser-rpa"

# 仓库本地的 Python 目录（自动安装时用，不污染系统）
PY_DIR = ROOT / ".python"
PY_EXE = PY_DIR / "python.exe"

# 要安装的 Python 版本与下载源（Windows x64 安装器，走国内镜像更快）
PY_VERSION = "3.12.8"
PY_DOWNLOAD_URLS = [
    # 华为云镜像（国内直连快）
    f"https://mirrors.huaweicloud.com/python/{PY_VERSION}/python-{PY_VERSION}-amd64.exe",
    # 官方源兜底
    f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-amd64.exe",
]


def log(tag, msg):
    print(f"[{tag}] {msg}")


def step(n, title):
    print("\n" + "=" * 68)
    print(f"  步骤 {n}：{title}")
    print("=" * 68)


def find_python():
    """找一个能用的 python 解释器，优先级：仓库本地 > WorkBuddy 托管 > 系统。"""
    # 0) 仓库本地的 .python（上次自动装过）
    if PY_EXE.exists():
        return str(PY_EXE)
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


def _download(url, dest, timeout=120):
    """下载文件，带进度提示。返回是否成功。"""
    print(f"  下载 {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kd-rpa-setup"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    print(f"\r  {pct}% ({done // 1024 // 1024}MB / {total // 1024 // 1024}MB)", end="", flush=True)
            print()
        return True
    except Exception as e:
        print(f"\n  下载失败：{e}")
        return False


def install_python():
    """无 Python 时，下载官方安装器静默装到仓库本地 .python/（不进 PATH、不污染系统）。"""
    step(0, "未检测到 Python，自动下载并安装到仓库本地 .python/")
    installer = ROOT / "python_installer.exe"
    ok = False
    for url in PY_DOWNLOAD_URLS:
        if _download(url, installer):
            ok = True
            break
        log("提示", "换下一个源重试...")
    if not ok:
        log("错误", "Python 安装包下载失败。请手动安装 Python 3.10+ 并勾选 Add to PATH 后重跑。")
        return None

    log("提示", f"静默安装 Python {PY_VERSION} 到 {PY_DIR}（约 1~2 分钟，请稍候）...")
    # 静默安装：/quiet 无界面；TargetDir 装到仓库本地；Include_pip 带 pip
    cmd = [
        str(installer),
        "/quiet", "InstallAllUsers=0",
        f"TargetDir={PY_DIR}",
        "Include_pip=1", "Include_test=0",
        "PrependPath=0", "Shortcuts=0",
    ]
    r = subprocess.run(cmd, capture_output=True)
    try:
        installer.unlink()  # 删掉安装包
    except Exception:
        pass
    if not PY_EXE.exists():
        log("错误", f"安装后没找到 {PY_EXE}，请手动安装 Python 3.10+ 后重跑。")
        return None
    # 确保 pip 可用（个别精简安装没有 pip）
    r = subprocess.run([str(PY_EXE), "-m", "pip", "--version"], capture_output=True)
    if r.returncode != 0:
        log("提示", "pip 不可用，尝试 ensurepip 初始化...")
        subprocess.run([str(PY_EXE), "-m", "ensurepip", "--upgrade"], capture_output=True)
    log("完成", f"Python 已装到 {PY_DIR}")
    return str(PY_EXE)


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
        # 没 Python → 自动下载安装到仓库本地 .python/
        py = install_python()
    if not py:
        log("错误", "仍没找到可用 Python。请手动安装 Python 3.10+ 并勾选 Add to PATH 后重跑本脚本。")
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
