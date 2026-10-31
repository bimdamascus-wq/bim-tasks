@echo off
title BIM Tasks - status report
cd /d "%~dp0"
echo BIM Tasks - status report. Please wait about one minute...
echo.
if not exist "%~dp0check_status.ps1" (
  echo ERROR: check_status.ps1 is not next to this file.
  echo Extract the whole zip first ^(right click - Extract All^), then run CHECK_STATUS.cmd from the extracted folder.
  echo.
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  echo Note: not running as administrator - some parts of the report may be empty.
  echo For the full report: right click CHECK_STATUS.cmd - Run as administrator.
  echo.
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_status.ps1"
echo.
echo Finished. Press any key to close.
pause >nul
