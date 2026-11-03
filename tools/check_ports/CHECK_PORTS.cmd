@echo off
title BIM Tasks - who runs the extra copy
cd /d "%~dp0"
if not exist "%~dp0check_ports.ps1" (
  echo ERROR: extract the whole zip first ^(right click - Extract All^), then run CHECK_PORTS.cmd from the extracted folder.
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  echo Please close this window, then right click CHECK_PORTS.cmd - Run as administrator.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_ports.ps1"
echo.
echo Finished. Press any key to close.
pause >nul
