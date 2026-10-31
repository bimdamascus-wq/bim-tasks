@echo off
set OUT=%USERPROFILE%\Desktop\BIM_Tasks_Diagnostics.txt
echo BIM Tasks Diagnostics > "%OUT%"
echo ===================== >> "%OUT%"
echo Date: %date% %time% >> "%OUT%"
echo. >> "%OUT%"
echo --- INSTALL LOG --- >> "%OUT%"
type C:\BIM_Tasks_Server\logs\install.log >> "%OUT%" 2>nul
echo. >> "%OUT%"
echo --- SERVER LAN LOG --- >> "%OUT%"
type C:\BIM_Tasks_Server\logs\server-lan.log >> "%OUT%" 2>nul
echo. >> "%OUT%"
echo --- PYTHON --- >> "%OUT%"
C:\BIM_Tasks_Server\.venv\Scripts\python.exe --version >> "%OUT%" 2>&1
echo. >> "%OUT%"
echo --- PORT 8797 --- >> "%OUT%"
netstat -ano | findstr :8797 >> "%OUT%" 2>&1
start notepad "%OUT%"
