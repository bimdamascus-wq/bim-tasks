@echo off
chcp 65001 >nul
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath \"%~f0\" -Verb RunAs"
  exit /b
)
schtasks /Run /TN "\BIM Tasks\LAN"
schtasks /Run /TN "BIM Tasks External 8798"
echo.
echo انتظر دقيقة وافتح http://127.0.0.1:8797
pause
