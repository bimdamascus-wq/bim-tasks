# BIM Tasks - stop a Google Sheet full-sync run that is stuck (older than 20 minutes). Touches nothing else.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Task = 'BIM_Tasks_Full_Google_Sheets_Sync'
$limit = (Get-Date).AddMinutes(-20)
$all = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'RUN_FULL_SYNC\.cmd|full_sync\\sync_all\.py' }
$old = @($all | Where-Object { $_.CreationDate -lt $limit })
if ($old.Count -eq 0) {
    Write-Host 'ما في مزامنة عالقة. ما عملت شي.' -ForegroundColor Green
} else {
    foreach ($p in $old) {
        Write-Host "عم وقّف مزامنة عالقة من $($p.CreationDate)  (pid $($p.ProcessId))" -ForegroundColor Yellow
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
    Write-Host 'تم. المزامنة الجاية بتبلّش لحالها خلال 5 دقايق.' -ForegroundColor Green
}
Write-Host ''
schtasks /Query /TN $Task /V /FO LIST 2>&1 | Select-String -Pattern '^(Status|Last Run Time|Last Result|Next Run Time):' | ForEach-Object { '  ' + $_.Line.Trim() }
Write-Host ''
Write-Host 'بعد ربع ساعة شغّل CHECK_STATUS.cmd مرة تانية وابعت التقرير.' -ForegroundColor Cyan
