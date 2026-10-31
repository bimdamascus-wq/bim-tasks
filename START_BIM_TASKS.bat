@echo off
cd /d %~dp0
start "BIM Tasks LAN" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_lan.ps1"
timeout /t 2 >nul
start "BIM Tasks External" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_external.ps1"
echo BIM Tasks started: LAN 8797 + External 8798
