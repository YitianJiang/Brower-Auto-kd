@echo off
set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0start_browser.py"
pause
