@echo off
chcp 65001 >nul
rem ASCII only: Arabic text in a .cmd makes cmd.exe close the window at once.
rem Check BEFORE asking for admin: run from inside the ZIP, the files next to this one do not exist.
if not exist "%~dp0uptime_watch.ps1" (
  echo.
  echo  ERROR: the ZIP is not extracted.
  echo  Close this window. Right click the ZIP - Extract All - open the new folder - run INSTALL_UPTIME_WATCH.cmd again.
  echo.
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "try { Start-Process -FilePath '%~f0' -Verb RunAs -ErrorAction Stop } catch { exit 1 }"
  if errorlevel 1 (
    echo.
    echo  Administrator permission was not given. Run it again and press Yes.
    echo.
    pause
  )
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
