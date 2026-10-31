@echo off
title BIM Tasks - fix stuck Google Sheet sync
cd /d "%~dp0"
if not exist "%~dp0fix_sheet_sync.ps1" (
  echo ERROR: Extract the whole zip first ^(right click - Extract All^), then run this file from the extracted folder.
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  echo Please close this window, then: right click FIX_SHEET_SYNC.cmd - Run as administrator.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_sheet_sync.ps1"
echo.
echo Finished. Press any key to close.
pause >nul
