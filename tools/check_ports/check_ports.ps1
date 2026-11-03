# BIM Tasks - read only (changes nothing): every running copy of the app outside 8797/8798/8799, what starts it,
# which folder/database it uses and whether it runs the workers (Telegram, reminders, backups).
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Stamp = Get-Date -Format 'yyyyMMdd_HHmm'
$Out = Join-Path ([Environment]::GetFolderPath('Desktop')) "BIM_PORTS_$Stamp.txt"
Write-Host 'عم افحص... (أقل من دقيقة، لا تكبس جوّا النافذة)' -ForegroundColor Cyan
function Mask($t) { ((("$t" -replace '\d{6,}:[A-Za-z0-9_-]{25,}', '<token>') -replace '(?i)(secret|passphrase|password|token)=\S+', '$1=<hidden>') -replace '[A-Fa-f0-9]{32,}', '<hex>') }
$Known = '8797|8798|8799'
$procs = @(Get-CimInstance Win32_Process)
$byId = @{}; foreach ($p in $procs) { $byId[[int]$p.ProcessId] = $p }
function Chain($p) {
    $lines = @(); $cur = $p; $n = 0
    while ($cur -and $n -lt 6) {
        $lines += "     pid $($cur.ProcessId)  $($cur.Name)  started $($cur.CreationDate)"
        $lines += "        cmd: $(Mask $cur.CommandLine)"
        $lines += "        exe: $($cur.ExecutablePath)"
        $cur = $byId[[int]$cur.ParentProcessId]; $n++
    }
    $lines
}
$report = & {
    "BIM Tasks - extra copies  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  computer: $env:COMPUTERNAME"
    ''
    '[python processes that run the app outside 8797/8798/8799] (with the processes that started them)'
    $extra = $procs | Where-Object { $_.CommandLine -match 'uvicorn|app\.main' -and $_.CommandLine -notmatch "--port\s+($Known)\b" -and $_.Name -match 'python' }
    if (-not $extra) { '  none' }
    foreach ($p in $extra) { "  -- pid $($p.ProcessId)"; Chain $p }
    ''
    '[listening ports of python processes]'
    $pyIds = @($procs | Where-Object { $_.Name -match 'python' } | ForEach-Object { [int]$_.ProcessId })
    netstat -ano | Select-String 'LISTENING' | ForEach-Object {
        $f = ($_.Line -split '\s+') | Where-Object { $_ }
        if ($f.Count -ge 5 -and $pyIds -contains [int]$f[-1]) { "  $($f[1])  pid $($f[-1])" } }
    ''
    '[what each extra port answers] (release-health is local only: commit, gateway, workers)'
    $ports = netstat -ano | Select-String 'LISTENING' | ForEach-Object { $f = ($_.Line -split '\s+') | Where-Object { $_ }; if ($f.Count -ge 5 -and $pyIds -contains [int]$f[-1]) { ($f[1] -split ':')[-1] } } | Sort-Object -Unique
    foreach ($port in $ports) {
        if ($port -match "^($Known)$") { continue }
        try { $r = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$port/api/release-health" -ErrorAction Stop).Content; "  $port : $r" }
        catch { $c = $_.Exception.Response; "  $port : release-health $(if ($c) { [int]$c.StatusCode } else { 'no answer' })" }
        try { $h = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$port/health" -ErrorAction Stop).Content; "         /health: $(Mask $h)" } catch {}
    }
    ''
    '[scheduled tasks that start python, uvicorn or a .cmd/.bat/.vbs] (outside the known BIM tasks)'
    $tasks = schtasks /Query /FO CSV /V 2>$null | ConvertFrom-Csv
    $tasks | Where-Object { $_.TaskName -ne 'TaskName' -and $_.'Task To Run' -match 'python|uvicorn|\.cmd|\.bat|\.vbs|\.ps1' -and $_.TaskName -notmatch '^\\BIM Tasks\\(LAN|Backup Hourly|Backup Daily Full)$|BIM Tasks External 8798|BIM_Tasks_Full_Google_Sheets_Sync' } |
        Sort-Object TaskName -Unique | ForEach-Object { "  $($_.TaskName)  | status $($_.Status) | last $($_.'Last Run Time') | next $($_.'Next Run Time')"; "     run: $(Mask $_.'Task To Run')"; "     as: $($_.'Run As User')  start in: $($_.'Start In')" }
    ''
    '[started with Windows: Startup folders and Run keys]'
    foreach ($d in "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp", "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup", 'C:\Users\admin\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup') {
        if (Test-Path $d) { Get-ChildItem $d -ErrorAction SilentlyContinue | ForEach-Object { "  $($_.FullName)  ($($_.LastWriteTime))" } } }
    foreach ($k in 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run') {
        $v = Get-ItemProperty $k -ErrorAction SilentlyContinue
        if ($v) { $v.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' } | ForEach-Object { "  $k : $($_.Name) = $(Mask $_.Value)" } } }
    ''
    '[windows services that run python or a .cmd/.bat]'
    Get-CimInstance Win32_Service | Where-Object { $_.PathName -match 'python|uvicorn|\.cmd|\.bat|nssm' } | ForEach-Object { "  $($_.Name) | $($_.State) | $($_.StartMode) | $(Mask $_.PathName)" }
    ''
    '[folders that look like a copy of the task system] (have app\main.py)'
    foreach ($base in 'C:\', 'C:\Users\admin\Desktop', 'C:\Users\admin\Documents', 'C:\Users\admin\Downloads', 'D:\') {
        if (-not (Test-Path $base)) { continue }
        Get-ChildItem $base -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            if (Test-Path (Join-Path $_.FullName 'app\main.py')) {
                $db = Join-Path $_.FullName 'data\bim_tasks.db'
                $envf = Join-Path $_.FullName '.env'
                "  $($_.FullName)  | data\bim_tasks.db: $(if (Test-Path $db) { (Get-Item $db).LastWriteTime } else { 'none' }) | .env: $(if (Test-Path $envf) { 'yes' } else { 'no' })"
                if (Test-Path $envf) { Get-Content $envf -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*(TELEGRAM_BOT_TOKEN|BIM_GATEWAY|BIM_SYNC_SECRET)\s*=' } | ForEach-Object { $n = ($_ -split '=')[0].Trim(); $val = ($_ -split '=', 2)[1].Trim(); "       $n : $(if ($n -eq 'BIM_GATEWAY') { $val } elseif ($val) { 'set' } else { 'empty' })" } }
            } } }
}
$report | Out-File -FilePath $Out -Encoding UTF8
$report | ForEach-Object { Write-Host $_ }
Write-Host ''
Write-Host "انحفظ التقرير على سطح المكتب: $Out" -ForegroundColor Green
Write-Host 'افتح الملف، انسخ كل اللي فيه، والصقه بالمحادثة.' -ForegroundColor Green
