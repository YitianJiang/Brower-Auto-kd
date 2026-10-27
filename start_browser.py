# -*- coding: utf-8 -*-
"""
启动调试浏览器
--------------
开一个独立的 Chrome（或 Edge），带远程调试端口 9222，供自动化脚本接管。
用的是独立配置目录，和你日常用的浏览器完全隔离，互不影响。

用法（双击「启动调试浏览器.bat」即可，或直接）：
    python start_browser.py
    python start_browser.py --browser edge
"""
import argparse
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# 不强制 utf-8 输出：cmd / Git Bash 按 GBK 解码控制台，强制 utf-8 会乱码

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "chrome-profile"
PORT = 9222

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser(kind="chrome"):
    cands = CHROME_CANDIDATES if kind == "chrome" else EDGE_CANDIDATES
    for p in cands:
        if p and os.path.exists(p):
            return p
    return None


def port_alive(port=PORT, timeout=1.0):
    """调试端口是否已有浏览器在监听"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            data = r.read().decode("utf-8", "ignore")
            return data
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="chrome", choices=["chrome", "edge"])
    args = ap.parse_args()

    print("=" * 68)
    print("  金蝶 RPA · 调试浏览器启动器")
    print("=" * 68)

    # 1) 端口已经有浏览器在监听 -> 直接复用，不重复启动
    info = port_alive()
    if info:
        print(f"\n[提示] 端口 {PORT} 上已经有一个调试浏览器在跑了，直接复用，不重复启动。")
        print(f"       浏览器信息: {info[:120]}")
        print("\n如果这个浏览器不是你想要的，先把它整个关掉再重新运行本脚本。")
        print("=" * 68)
        input("\n按回车键关闭本窗口...")
        return 0

    # 2) 找浏览器
    exe = find_browser(args.browser)
    if not exe:
        print(f"\n[错误] 没找到 {args.browser}。")
        print("如果你装了别的浏览器或在别的路径，请手动改 start_browser.py 里的路径列表。")
        print("=" * 68)
        input("\n按回车键关闭本窗口...")
        return 1
    print(f"\n浏览器: {exe}")

    PROFILE.mkdir(parents=True, exist_ok=True)
    print(f"配置目录: {PROFILE}")
    print("（独立目录，与你日常浏览器完全隔离，不会互相影响）")

    cmd = [
        exe,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        "--start-maximized",
    ]

    print("\n启动中...")
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"\n[错误] 启动失败: {e}")
        input("\n按回车键关闭本窗口...")
        return 1

    # 3) 等待调试端口就绪
    ok = None
    for i in range(30):
        time.sleep(0.5)
        ok = port_alive(timeout=0.8)
        if ok:
            break
        if i == 0:
            print("等待浏览器就绪", end="", flush=True)
        else:
            print(".", end="", flush=True)
    print()

    if not ok:
        print(f"\n[警告] 浏览器已启动，但 {PORT} 端口没响应。")
        print("常见原因：Chrome 已经在运行且占用了默认配置目录。")
        print("解决办法：把正在用的 Chrome 全部关掉，再重新运行本脚本。")
        print("=" * 68)
        input("\n按回车键关闭本窗口...")
        return 1

    print("\n" + "=" * 68)
    print("  启动成功！远程调试端口 9222 已就绪")
    print("=" * 68)
    print("\n接下来请在刚打开的浏览器里：")
    print("  1. 打开你们的金蝶云星空网址")
    print("  2. 登录（只需登录一次，之后会自动记住）")
    print("  3. 登录后保持窗口开着，把地址栏的网址发给 WB")
    print("\n然后就可以录制操作了：")
    print("  python src/record.py")
    print("\n提示：这个浏览器窗口不要关，关了脚本就连不上了。")
    print("=" * 68)
    input("\n按回车键关闭本窗口（浏览器会继续运行）...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
