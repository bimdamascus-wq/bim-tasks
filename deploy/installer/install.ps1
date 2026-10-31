# BIM Tasks - generic update installer. Reads update.json next to this file.
# Lessons kept here on purpose: see CLAUDE.md "How updates reach the server".
# Continue: in Windows PowerShell 5.1 any stderr text from a native tool (pip warnings) would otherwise abort.
$ErrorActionPreference = 'Continue'
$PSDefaultParameterValues['Copy-Item:ErrorAction'] = 'Stop'
$PSDefaultParameterValues['New-Item:ErrorAction'] = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$U = Get-Content "$Here\update.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$Root = 'C:\BIM_Tasks_Server'
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
$Payload = "$Here\payload"
$Files = @()
if (Test-Path $Payload) { $Files = @(Get-ChildItem $Payload -Recurse -File | ForEach-Object { $_.FullName.Substring($Payload.Length + 1) }) }
$Deletes = @($U.delete | Where-Object { $_ })

function Step($t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }
function Stop-Bim {
    foreach ($t in $LanTask, $ExtTask) { schtasks /End /TN $t 2>$null | Out-Null }
    # The venv launcher starts a child python whose command line may not contain $Root, so match on uvicorn app.main.
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 15; $i++) {
        if (-not (netstat -ano | Select-String -Pattern ':(8797|8798)\s.*LISTENING')) { return }
        Start-Sleep -Seconds 2
    }
    Write-Host '   تنبيه: المنفذ 8797/8798 لسا محجوز' -ForegroundColor Yellow
}
function Start-Bim {
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
    $back = Wait-Health 8797
    Remove-Item $InProgress -Force -ErrorAction SilentlyContinue
    if ($back) { Write-Host '!! رجع النظام متل ما كان وشغّال.' -ForegroundColor Yellow } else { Write-Host '!! الملفات رجعت، بس النظام ما ردّ. شغّل START_BIM_NOW.cmd' -ForegroundColor Red }
    Write-Host "!! ابعت ملف السجل: $Root\logs\update_$($U.name)_$Stamp.log"
    Stop-Transcript | Out-Null; exit 1
}

Write-Host "تحديث: $($U.title)" -ForegroundColor Green
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
if (-not (Wait-Health 8797)) { Rollback 'البوابة الداخلية 8797 ما ردّت بعد التشغيل' }
if ($U.check_path -and -not (Answers 8797 $U.check_path)) { Rollback 'البوابة 8797 عم تشغّل النسخة القديمة (عملية قديمة ما توقفت)' }
Write-Host '   البوابة الداخلية 8797: تعمل بالنسخة الجديدة' -ForegroundColor Green
if (Wait-Health 8798) { Write-Host '   البوابة الخارجية 8798: تعمل' -ForegroundColor Green } else { Write-Host '   البوابة الخارجية 8798 ما ردّت — راجع logs\server-external.log' -ForegroundColor Yellow }

Move-Item $InProgress $Done -Force
Stop-Transcript | Out-Null
Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host " تم التحديث بنجاح: $($U.title)" -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
foreach ($line in @($U.after | Where-Object { $_ })) { Write-Host " - $line" }
