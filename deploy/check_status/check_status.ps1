# BIM Tasks - status report (read only: changes nothing). Started by CHECK_STATUS.cmd.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = 'C:\BIM_Tasks_Server'
$Stamp = Get-Date -Format 'yyyyMMdd_HHmm'
$Out = Join-Path ([Environment]::GetFolderPath('Desktop')) "BIM_STATUS_$Stamp.txt"
Write-Host 'عم افحص النظام... (دقيقة تقريباً، لا تكبس جوّا النافذة)' -ForegroundColor Cyan

function Mask($t) { ($t -replace '\d{6,}:[A-Za-z0-9_-]{25,}', '<token>') -replace '(?i)(secret|passphrase|password)=\S+', '$1=<hidden>' }
$report = & {
    "BIM Tasks status  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  computer: $env:COMPUTERNAME"
    ''
    '[updates installed]'
    Get-ChildItem "$Root\_safe_patch_backups" -Filter '*.done' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | ForEach-Object { "  done: $($_.BaseName)  $($_.LastWriteTime)" }
    Get-ChildItem "$Root\_safe_patch_backups" -Filter '*.inprogress' -ErrorAction SilentlyContinue | ForEach-Object { "  NOT FINISHED: $($_.BaseName)" }
    ''
    '[ports]'
    foreach ($p in 8797, 8798, 8799) {
        try { $r = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$p/api/release-health" -ErrorAction Stop).Content | ConvertFrom-Json
              "  $p : gateway=$($r.gateway) commit=$($r.commit) workers=$($r.worker_alive)" }
        catch { $code = $_.Exception.Response; "  $p : no answer $(if ($code) { [int]$code.StatusCode })" }
    }
    ''
    '[server processes]'
    Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app' } | ForEach-Object {
        $port = if ($_.CommandLine -match '--port\s+(\d+)') { $Matches[1] } else { '?' }
        "  pid $($_.ProcessId)  port $port  started $($_.CreationDate)  $($_.ExecutablePath)" }
    ''
    '[scheduled tasks]'
    foreach ($t in '\BIM Tasks\LAN', 'BIM Tasks External 8798', '\BIM Tasks\Backup Hourly', '\BIM Tasks\Backup Daily Full', 'BIM_Tasks_Full_Google_Sheets_Sync') {
        "  -- $t"
        $q = schtasks /Query /TN $t /V /FO LIST 2>&1
        if ($LASTEXITCODE -ne 0) { '     NOT FOUND'; continue }
        $q | Select-String -Pattern '^(Status|Last Run Time|Last Result|Task To Run|Run As User|Next Run Time):' | ForEach-Object { '     ' + (Mask $_.Line.Trim()) }
    }
    ''
    '[test copy]'
    if (Test-Path 'C:\BIM_Tasks_Test\TEST_COPY.txt') { '  ' + ((Get-Content 'C:\BIM_Tasks_Test\TEST_COPY.txt' -Encoding UTF8) -join ' | ') } else { '  none' }
    ''
    if (Test-Path "$Root\.venv\Scripts\python.exe") { & "$Root\.venv\Scripts\python.exe" -I "$Here\check_status.py" $Root 2>&1 | ForEach-Object { Mask "$_" } }
    else { 'python not found' }
    ''
    foreach ($log in 'server-lan.err.log', 'server-external.log', 'server-lan.log') {
        $f = "$Root\logs\$log"
        if (Test-Path $f) { "[last lines of $log]"; Get-Content $f -Tail 15 -Encoding UTF8 -ErrorAction SilentlyContinue | ForEach-Object { '  ' + (Mask $_) }; '' }
    }
}
$report | Out-File $Out -Encoding utf8
Write-Host ''
Write-Host "خلص. انحفظ التقرير على سطح المكتب: $Out" -ForegroundColor Green
Write-Host 'افتحه وانسخ كل يلي فيه وابعته بالمحادثة. ما فيه كلمات سر.' -ForegroundColor Green
Start-Process notepad.exe $Out
