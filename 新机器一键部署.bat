@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

REM ============================================
REM  金蝶 RPA · 新机器一键部署（含自动装 Python）
REM ============================================

REM 1) 先找可用的 python（仓库本地 > WorkBuddy 托管 > 系统）
set "PY="
if exist "%~dp0.python\python.exe" set "PY=%~dp0.python\python.exe"
if not defined PY if exist "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PY (
    where python >nul 2>nul
    if !errorlevel!==0 set "PY=python"
)

REM 2) 都没有 → 用 PowerShell 下载 Python 安装器并静默装到 .python\
if not defined PY (
    echo [提示] 未检测到 Python，自动下载并安装到仓库本地 .python\ ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Location '%~dp0'; if (-not (Test-Path 'python_installer.exe')) { $u='https://mirrors.huaweicloud.com/python/3.12.8/python-3.12.8-amd64.exe'; try { Invoke-WebRequest -Uri $u -OutFile 'python_installer.exe' -UseBasicParsing } catch { $u='https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe'; Invoke-WebRequest -Uri $u -OutFile 'python_installer.exe' -UseBasicParsing } }; Start-Process -FilePath 'python_installer.exe' -ArgumentList '/quiet','InstallAllUsers=0','TargetDir=%~dp0.python','Include_pip=1','Include_test=0','PrependPath=0','Shortcuts=0' -Wait"
    if exist "%~dp0.python\python.exe" (
        set "PY=%~dp0.python\python.exe"
        echo [完成] Python 已安装
    ) else (
        echo [错误] Python 自动安装失败。请手动安装 Python 3.10+ 并勾选 Add to PATH，然后重跑本脚本。
        pause
        exit /b 1
    )
)

echo [Python] !PY!

REM 3) 跑部署脚本（完整流程：装依赖+补config+装skill+启动浏览器+自检）
"!PY!" "%~dp0setup_new_machine.py" --all
pause
