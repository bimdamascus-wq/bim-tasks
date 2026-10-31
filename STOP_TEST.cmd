@echo off
rem Stops only the staging copy (port 8799). The real system on 8797/8798 is not touched.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app' -and $_.CommandLine -match '--port\s+8799' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
echo Test copy stopped.
