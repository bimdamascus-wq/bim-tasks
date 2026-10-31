# BIM Tasks - generic update installer. Reads update.json next to this file.
# Lessons kept here on purpose: see CLAUDE.md "How updates reach the server".
# -Test (INSTALL_TO_TEST.cmd): install into the staging copy C:\BIM_Tasks_Test on port 8799 instead.
param([switch]$Test)
# Continue: in Windows PowerShell 5.1 any stderr text from a native tool (pip warnings) would otherwise abort.
$ErrorActionPreference = 'Continue'
$PSDefaultParameterValues['Copy-Item:ErrorAction'] = 'Stop'
$PSDefaultParameterValues['New-Item:ErrorAction'] = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$U = Get-Content "$Here\update.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$Root = 'C:\BIM_Tasks_Server'
if ($Test) {
    $Root = 'C:\BIM_Tasks_Test'
    if (-not (Test-Path "$Root\TEST_COPY.txt")) { Write-Host 'ما في نسخة تجريبية. شغّل أول TEST_COPY_CREATE.cmd من C:\BIM_Tasks_Server.' -ForegroundColor Red; exit 1 }
}
if (-not (Test-Path "$Root\app\main.py")) {
    $Root = (Read-Host 'ما لقيت C:\BIM_Tasks_Server. اكتب مسار مجلد النظام').Trim('"', ' ')
    if (-not (Test-Path "$Root\app\main.py")) { Write-Host 'المسار غير صحيح. تم الإلغاء.' -ForegroundColor Red; exit 1 }
}
$Py = "$Root\.venv\Scripts\python.exe"
$Marks = "$Root\_safe_patch_backups"
New-Item -ItemType Directory -Force -Path $Marks, "$Root\logs" | Out-Null
$InProgress = "$Marks\$($U.name).inprogress"
$Done = "$Marks\$($U.name).done"
if (Test-Path $Done) { Write-Host "هالتحديث ($($U.title)) منزّل من قبل. ما في شي لازم ينعمل." -ForegroundColor Green; exit 0 }
if (Test-Path $InProgress) {
    Write-Host 'في محاولة سابقة لهالتحديث ما كملت. ما رح كمّل لحتى ما تخرب النسخة الاحتياطية.' -ForegroundColor Red
    Write-Host "النسخة الأصلية: $(Get-Content $InProgress)" -ForegroundColor Yellow
    Write-Host 'ابعت هالرسالة وملف السجل الأخير من مجلد logs.' -ForegroundColor Yellow
    exit 1
}
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Bk = "$Marks\$($U.name)_$Stamp"
New-Item -ItemType Directory -Force -Path $Bk | Out-Null
Set-Content $InProgress $Bk -Encoding UTF8
Start-Transcript -Path "$Root\logs\update_$($U.name)_$Stamp.log" | Out-Null
$LanTask = '\BIM Tasks\LAN'
$ExtTask = 'BIM Tasks External 8798'
# Each port must answer with the new commit and its own gateway (only 'lan' runs the workers).
if ($Test) { $Gates = @(@{ Port = 8799; Gateway = 'test'; Must = $true }) }
else { $Gates = @(@{ Port = 8797; Gateway = 'lan'; Must = $true }, @{ Port = 8798; Gateway = 'external'; Must = $false }) }
$PortRe = ($Gates | ForEach-Object { $_.Port }) -join '|'
$Payload = "$Here\payload"
$Files = @()
if (Test-Path $Payload) { $Files = @(Get-ChildItem $Payload -Recurse -File | ForEach-Object { $_.FullName.Substring($Payload.Length + 1) }) }
$Deletes = @($U.delete | Where-Object { $_ })

function Step($t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }
function Stop-Bim {
    if (-not $Test) { foreach ($t in $LanTask, $ExtTask) { schtasks /End /TN $t 2>$null | Out-Null } }
    # The venv launcher starts a child python whose command line may not contain $Root, so match on uvicorn app.main.
    # The test copy (8799) and the real system never stop each other.
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app' -and
                       (($Test -and $_.CommandLine -match '--port\s+8799') -or (-not $Test -and $_.CommandLine -notmatch '--port\s+8799')) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 15; $i++) {
        if (-not (netstat -ano | Select-String -Pattern ":($PortRe)\s.*LISTENING")) { return }
        Start-Sleep -Seconds 2
    }
    Write-Host "   تنبيه: المنفذ $PortRe لسا محجوز" -ForegroundColor Yellow
}
function Start-Bim {
    if ($Test) { Start-Process -FilePath "$Root\RUN_TEST.cmd" -WorkingDirectory $Root -WindowStyle Minimized; return }
    schtasks /Run /TN $LanTask 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Start-Process -FilePath "$Root\RUN_LAN.cmd" -WorkingDirectory $Root -WindowStyle Minimized }
    schtasks /Run /TN $ExtTask 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Start-Process -FilePath "$Root\RUN_EXTERNAL.cmd" -WorkingDirectory $Root -WindowStyle Minimized }
}
function Answers($port, $path) {
    # Any HTTP answer except 404 means the route exists, i.e. the new code is running.
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 "http://127.0.0.1:$port$path" -ErrorAction Stop | Out-Null; return $true }
    catch { $r = $_.Exception.Response; return ($r -and [int]$r.StatusCode -ne 404) }
}
function Release($port) {
    # Local-only route; returns the commit and gateway of the process that answers on $port.
    for ($i = 0; $i -lt 10; $i++) {
        try { $c = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 "http://127.0.0.1:$port/api/release-health" -ErrorAction Stop).Content; return ($c | ConvertFrom-Json) }
        catch { Start-Sleep -Seconds 2 }
    }
    return $null
}
function Check-Gate($g) {
    # Returns an error text, or '' when the port runs the new commit with the right gateway.
    $r = Release $g.Port
    if (-not $r) { return "البوابة $($g.Port) ما ردّت على فحص النسخة" }
    if ($U.commit -and $r.commit -ne $U.commit) { return "البوابة $($g.Port) عم تشغّل نسخة قديمة ($($r.commit)) بدل $($U.commit)" }
    if ($r.gateway -ne $g.Gateway) { return "البوابة $($g.Port) شغّالة بوضع '$($r.gateway)' بدل '$($g.Gateway)' (ملف التشغيل أو المهمة المجدولة ما عم يمرر BIM_GATEWAY)" }
    return ''
}
function Wait-Health($port) {
    for ($i = 0; $i -lt 40; $i++) { if (Answers $port '/health') { return $true }; Start-Sleep -Seconds 2 }
    return $false
}
function Rollback($why) {
    Write-Host ''; Write-Host "!! فشل: $why" -ForegroundColor Red
    Write-Host '!! جاري إرجاع النظام لحالته السابقة...' -ForegroundColor Yellow
    Stop-Bim
    foreach ($f in $Files + $Deletes) {
        if (Test-Path "$Bk\files\$f") { New-Item -ItemType Directory -Force -Path (Split-Path "$Root\$f") | Out-Null; Copy-Item "$Bk\files\$f" "$Root\$f" -Force }
        elseif (Test-Path "$Root\$f") { Remove-Item "$Root\$f" -Force }
    }
    if (Test-Path "$Bk\.env") { Copy-Item "$Bk\.env" "$Root\.env" -Force }
    foreach ($d in 'bim_tasks.db', 'bim_tasks.db-wal', 'bim_tasks.db-shm') {
        if (Test-Path "$Root\data\$d") { Remove-Item "$Root\data\$d" -Force }
        if (Test-Path "$Bk\data\$d") { Copy-Item "$Bk\data\$d" "$Root\data\$d" -Force }
    }
    Start-Bim
    $back = Wait-Health $Gates[0].Port
    Remove-Item $InProgress -Force -ErrorAction SilentlyContinue
    if ($back) { Write-Host '!! رجع النظام متل ما كان وشغّال.' -ForegroundColor Yellow } else { Write-Host '!! الملفات رجعت، بس النظام ما ردّ. شغّل START_BIM_NOW.cmd' -ForegroundColor Red }
    Write-Host "!! ابعت ملف السجل: $Root\logs\update_$($U.name)_$Stamp.log"
    Stop-Transcript | Out-Null; exit 1
}

Write-Host "تحديث: $($U.title)" -ForegroundColor Green
if ($Test) { Write-Host 'على النسخة التجريبية C:\BIM_Tasks_Test (النظام الحقيقي ما بيتأثر)' -ForegroundColor Yellow }
Write-Host "المجلد: $Root   (لا تكبس بالماوس جوّا هالنافذة وقت التثبيت)" -ForegroundColor DarkGray

Step '1/6 إيقاف النظام'
Stop-Bim

Step '2/6 نسخة احتياطية'
foreach ($f in $Files + $Deletes) {
    if (Test-Path "$Root\$f") { $dst = "$Bk\files\$f"; New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null; Copy-Item "$Root\$f" $dst -Force }
}
if (Test-Path "$Root\.env") { Copy-Item "$Root\.env" "$Bk\.env" -Force }
New-Item -ItemType Directory -Force -Path "$Bk\data" | Out-Null
foreach ($d in 'bim_tasks.db', 'bim_tasks.db-wal', 'bim_tasks.db-shm') { if (Test-Path "$Root\data\$d") { Copy-Item "$Root\data\$d" "$Bk\data\$d" -Force } }
Write-Host "   $Bk"

$step = ''
try {
    $step = 'نسخ الملفات'
    Step "3/6 نسخ الملفات ($($Files.Count))"
    foreach ($f in $Files) { $dst = "$Root\$f"; New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null; Copy-Item "$Payload\$f" $dst -Force }
    foreach ($f in $Deletes) { if (Test-Path "$Root\$f") { Remove-Item "$Root\$f" -Force } }

    $step = 'تثبيت المكتبات'
    Step '4/6 المكتبات'
    foreach ($pkg in @($U.pip | Where-Object { $_ })) {
        & $Py -m pip install --disable-pip-version-check --no-index --find-links "$Here\wheels" $pkg 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { & $Py -m pip install --disable-pip-version-check $pkg 2>&1 | Out-Host }
        if ($LASTEXITCODE -ne 0) { throw "تعذر تثبيت $pkg" }
    }

    $step = 'الفحص قبل التشغيل'
    Step '5/6 فحص النظام قبل التشغيل'
    Push-Location $Root
    if (Test-Path "$Here\post_install.py") {
        & $Py "$Here\post_install.py" 2>&1 | Out-Host
        if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'خطوة الإعداد بعد النسخ فشلت' }
    }
    & $Py -c "import app.main; from app.seed import seed; seed(); print('IMPORT_OK')" 2>&1 | Out-Host
    $ok = $LASTEXITCODE -eq 0
    Pop-Location
    if (-not $ok) { throw 'النظام الجديد ما اشتغل بالفحص' }
} catch { Rollback "$step — $($_.Exception.Message)" }

Step '6/6 تشغيل النظام'
Start-Bim
foreach ($g in $Gates) {
    $p = $g.Port
    if (-not (Wait-Health $p)) {
        if ($g.Must) { Rollback "البوابة $p ما ردّت بعد التشغيل" }
        Write-Host "   البوابة $p ما ردّت — راجع مجلد logs" -ForegroundColor Yellow; continue
    }
    if ($U.check_path -and -not (Answers $p $U.check_path)) { Rollback "البوابة $p عم تشغّل النسخة القديمة (عملية قديمة ما توقفت)" }
    $err = Check-Gate $g
    if ($err) { Rollback $err }
    Write-Host "   البوابة $p ($($g.Gateway)): تعمل بالنسخة الجديدة $($U.commit)" -ForegroundColor Green
}

# Status report tool lives in tools\check_status; give the owner a desktop shortcut to it (never fatal).
$Tool = "$Root\tools\check_status\CHECK_STATUS.cmd"
if (-not $Test -and (Test-Path $Tool)) {
    try {
        # Session 3: the shortcut with an Arabic name did not appear. WScript.Shell is saved under an ASCII name
        # (it can fail on non-ANSI paths), then renamed with .NET; it goes on the public AND the current desktop.
        $made = @()
        foreach ($desk in @([Environment]::GetFolderPath('CommonDesktopDirectory'), [Environment]::GetFolderPath('Desktop')) | Select-Object -Unique) {
            if (-not $desk -or -not (Test-Path $desk)) { continue }
            $tmp = Join-Path $desk 'BIM_Status_Report.lnk'
            $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($tmp)
            $sc.TargetPath = $Tool; $sc.WorkingDirectory = Split-Path $Tool
            $sc.Description = 'BIM Tasks status report (read only)'; $sc.Save()
            # Byte 0x15 bit 0x20 = "Run as administrator" in the shortcut's advanced properties.
            $b = [IO.File]::ReadAllBytes($tmp); $b[0x15] = $b[0x15] -bor 0x20; [IO.File]::WriteAllBytes($tmp, $b)
            $lnk = Join-Path $desk 'تقرير حالة BIM.lnk'
            try { if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk -Force }; [IO.File]::Move($tmp, $lnk) } catch { $lnk = $tmp }
            $made += $lnk
        }
        if (-not $made) { throw 'no desktop folder found' }
        foreach ($l in $made) { Write-Host "   اختصار تقرير الحالة: $l" -ForegroundColor Green }
    } catch { Write-Host "   تنبيه: ما قدرت أعمل اختصار تقرير الحالة ($($_.Exception.Message)). شغّله من $Tool" -ForegroundColor Yellow }
}

Move-Item $InProgress $Done -Force
Stop-Transcript | Out-Null
Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host " تم التحديث بنجاح: $($U.title)" -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
foreach ($line in @($U.after | Where-Object { $_ })) { Write-Host " - $line" }
