@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -and $_.CommandLine -match '(?i)C:\\BIM_Tasks_Server' -and $_.CommandLine -match 'uvicorn app\.main:app'} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}"
