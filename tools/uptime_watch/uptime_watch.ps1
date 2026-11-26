# BIM Tasks uptime watch. Runs every minute as the scheduled task "\BIM Tasks\Uptime Watch" (SYSTEM).
# Read only: it never stops or starts anything. It writes:
#   logs\uptime.log         one line per change: DOWN (with clues), UP (with how long it was down), GAP (watcher did not run)
#   logs\uptime_state.txt   last state + time of the last check
# ASCII only on purpose (PowerShell 5.1 reads BOM-less files as ANSI).
$ErrorActionPreference = 'Continue'
$Root = 'C:\BIM_Tasks_Server'
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force $Logs | Out-Null
$Log = Join-Path $Logs 'uptime.log'
$StateFile = Join-Path $Logs 'uptime_state.txt'
$Fmt = 'yyyy-MM-dd HH:mm:ss'
$now = Get-Date

function Write-Log($text) { Add-Content -Path $Log -Value "$($now.ToString($Fmt))  $text" -Encoding ASCII }

function Test-Port8797 {
    # Two tries 5 s apart, so a single slow answer is not logged as an outage.
    $err = ''
    foreach ($i in 1, 2) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 'http://127.0.0.1:8797/health' -ErrorAction Stop
            if ($r.StatusCode -eq 200) { return '' }
            $err = "HTTP $($r.StatusCode)"
        } catch {
            $resp = $_.Exception.Response
            $err = if ($resp) { "HTTP $([int]$resp.StatusCode)" } else { $_.Exception.Message -replace '\s+', ' ' }
        }
        if ($i -eq 1) { Start-Sleep -Seconds 5 }
    }
    return $err
}

# previous state: "UP|<since>|<last check>" or "DOWN|<since>|<last check>"
$prev = $null; $since = $null; $last = $null
if (Test-Path $StateFile) {
    $p = (Get-Content $StateFile -TotalCount 1) -split '\|'
    if ($p.Count -ge 3) {
        $prev = $p[0]
        try { $since = [datetime]::ParseExact($p[1], $Fmt, [Globalization.CultureInfo]::InvariantCulture); $last = [datetime]::ParseExact($p[2], $Fmt, [Globalization.CultureInfo]::InvariantCulture) } catch { $prev = $null }
    }
}
if (-not $prev) { Write-Log 'WATCH STARTED' }

# The watcher itself did not run for a while = server off/restarting or the task was stopped.
if ($last -and ($now - $last).TotalMinutes -gt 3) {
    $boot = $(try { (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString($Fmt) } catch { "?" })
    Write-Log ("GAP  no check from {0} to now ({1:N0} min). Windows last started {2}" -f $last.ToString($Fmt), ($now - $last).TotalMinutes, $boot)
}

$err = Test-Port8797
$state = if ($err) { 'DOWN' } else { 'UP' }

if ($state -ne $prev) {
    if ($state -eq 'DOWN') {
        $listen = if (Get-NetTCPConnection -LocalPort 8797 -State Listen -ErrorAction SilentlyContinue) { 'yes' } else { 'no' }
        $task = ((schtasks /Query /TN '\BIM Tasks\LAN' /V /FO LIST 2>$null) | Select-String '^(Status|Last Result):' | ForEach-Object { $_.Line -replace '\s+', ' ' }) -join ', '
        $mem = $(try { $os = Get-CimInstance Win32_OperatingSystem; '{0:N0}% used' -f (100 - 100 * $os.FreePhysicalMemory / $os.TotalVisibleMemorySize) } catch { "?" })
        $disk = $(try { '{0:N1} GB free' -f ((Get-PSDrive C).Free / 1GB) } catch { "?" })
        $net = if (Test-Connection -ComputerName 1.1.1.1 -Count 1 -Quiet -ErrorAction SilentlyContinue) { 'yes' } else { 'no' }
        Write-Log "DOWN 8797 not answering: $err | port listening: $listen | task: $task | memory: $mem | disk C: $disk | internet: $net"
        $errLog = Join-Path $Logs 'server-lan.err.log'
        if (Test-Path $errLog) { Get-Content $errLog -Tail 5 -ErrorAction SilentlyContinue | ForEach-Object { Write-Log ("     err.log: " + ($_ -replace '[^\x20-\x7E]', '?')) } }
    } elseif ($prev -eq 'DOWN') {
        Write-Log ("UP   8797 answers again after {0:N0} min down (down since {1})" -f ($now - $since).TotalMinutes, $since.ToString($Fmt))
    } else {
        Write-Log 'UP   8797 answers'
    }
    $since = $now
}
Set-Content -Path $StateFile -Value "$state|$($since.ToString($Fmt))|$($now.ToString($Fmt))" -Encoding ASCII
