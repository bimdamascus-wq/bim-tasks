@echo off
chcp 65001 >nul
rem ASCII only: Arabic text in a .cmd makes cmd.exe close the window at once.
rem Check BEFORE asking for admin: run from inside the ZIP, the files next to this one do not exist.
if not exist "%~dp0install.ps1" (
  echo.
  echo  ERROR: the ZIP is not extracted.
  echo  Close this window. Right click the ZIP - Extract All - open the new folder - run INSTALL_UPDATE.cmd again.
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
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
