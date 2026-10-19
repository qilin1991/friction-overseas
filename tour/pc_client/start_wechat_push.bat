@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 杭州亲子活动汇 - 公众号采集（保持窗口开着）

where python >nul 2>&1
if errorlevel 1 (
  echo [!] 未检测到 Python。
  echo     请先到 https://www.python.org/downloads/ 安装 Python 3，
  echo     安装第一步务必勾选 "Add python.exe to PATH"，装好后重新双击本文件。
  pause
  exit /b 1
)

python -c "import requests" >nul 2>&1
if errorlevel 1 (
  echo [*] 首次运行，正在安装依赖 requests ...
  python -m pip install requests -i https://pypi.tuna.tsinghua.edu.cn/simple
)

:loop
echo.>> push_wechat.log
echo ========== %date% %time% 开始一轮 ========== >> push_wechat.log
python push_wechat.py --delay 14 >> push_wechat.log 2>&1
echo ========== %date% %time% 本轮结束，休息 50 分钟 ========== >> push_wechat.log
timeout /t 3000 /nobreak >nul
goto loop
