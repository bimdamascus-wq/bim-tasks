@echo off
rem ASCII only on purpose: Arabic text in a .cmd breaks cmd.exe and the window closes at once.
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
if not exist "%~dp0uptime_watch.ps1" (
  echo.
  echo  ERROR: extract the ZIP first: right click the ZIP - Extract All - then run INSTALL_UPTIME_WATCH.cmd
  pause
  exit /b
)
set DEST=C:\BIM_Tasks_Server\tools\uptime_watch
if not exist "%DEST%" mkdir "%DEST%"
copy /Y "%~dp0uptime_watch.ps1" "%DEST%\uptime_watch.ps1" >nul
if exist "%~dp0check_status.ps1" copy /Y "%~dp0check_status.ps1" "C:\BIM_Tasks_Server\tools\check_status\check_status.ps1" >nul
schtasks /Create /F /TN "\BIM Tasks\Uptime Watch" /SC MINUTE /MO 1 /RU SYSTEM /RL HIGHEST /TR "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%DEST%\uptime_watch.ps1\""
schtasks /Run /TN "\BIM Tasks\Uptime Watch" >nul
echo.
echo  Waiting 20 seconds for the first check...
timeout /t 20 /nobreak >nul
echo.
echo  ---- C:\BIM_Tasks_Server\logs\uptime.log ----
type "C:\BIM_Tasks_Server\logs\uptime.log"
echo.
echo  DONE. The watch now checks the system every minute.
echo  Press any key to close.
pause >nul
