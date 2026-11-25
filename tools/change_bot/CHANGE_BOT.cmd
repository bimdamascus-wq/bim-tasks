@echo off
chcp 65001 >nul
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath \"%~f0\" -Verb RunAs"
  exit /b
)
set ROOT=C:\BIM_Tasks_Server
set PYTHONIOENCODING=utf-8
set BIM_ROOT=%ROOT%
cd /d "%ROOT%"
echo لا تكبس جوّا هالشاشة السودا إلا لتلصق الرمز.
echo.
"%ROOT%\.venv\Scripts\python.exe" "%~dp0change_bot.py"
if errorlevel 1 goto end
echo.
echo عم أعيد تشغيل النظام (8797 و 8798)...
rem Same stop rule as the installer: only OUR ports, never the other app.main:app systems on this server.
schtasks /End /TN "\BIM Tasks\LAN" >nul 2>&1
schtasks /End /TN "BIM Tasks External 8798" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*.exe' -and $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app' -and $_.CommandLine -match '--port\s+(8797|8798)\b' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Start-Sleep -Seconds 5"
schtasks /Run /TN "\BIM Tasks\LAN" >nul 2>&1 || start "" /min "%ROOT%\RUN_LAN.cmd"
schtasks /Run /TN "BIM Tasks External 8798" >nul 2>&1 || start "" /min "%ROOT%\RUN_EXTERNAL.cmd"
echo.
echo خلص. بعد دقيقة افتح النظام وجرّب.
:end
echo.
pause
