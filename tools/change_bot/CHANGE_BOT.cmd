@echo off
rem ASCII only on purpose: Arabic text in a .cmd broke cmd.exe and the window closed at once.
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
if not exist "%~dp0change_bot.py" (
  echo.
  echo  ERROR: extract the ZIP first: right click the ZIP - Extract All - then run CHANGE_BOT.cmd
  pause
  exit /b
)
set PYTHONIOENCODING=utf-8
set BIM_ROOT=C:\BIM_Tasks_Server
cd /d C:\BIM_Tasks_Server
"C:\BIM_Tasks_Server\.venv\Scripts\python.exe" "%~dp0change_bot.py"
if errorlevel 9009 pause
