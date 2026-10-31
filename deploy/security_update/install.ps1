# BIM Tasks - security update installer (roles, scope, login confirmation, off-site backups)
# Continue: in Windows PowerShell 5.1 any text a native tool writes to stderr (pip warnings) would otherwise abort.
$ErrorActionPreference = 'Continue'
$PSDefaultParameterValues['Copy-Item:ErrorAction'] = 'Stop'
$PSDefaultParameterValues['New-Item:ErrorAction'] = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = 'C:\BIM_Tasks_Server'
if (-not (Test-Path "$Root\app\main.py")) {
    $Root = (Read-Host 'ما لقيت C:\BIM_Tasks_Server. اكتب مسار مجلد النظام').Trim('"', ' ')
    if (-not (Test-Path "$Root\app\main.py")) { Write-Host 'المسار غير صحيح. تم الإلغاء.' -ForegroundColor Red; exit 1 }
}
$Py = "$Root\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { Write-Host "ما لقيت $Py" -ForegroundColor Red; exit 1 }
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Bk = "$Root\_safe_patch_backups\security_$Stamp"
New-Item -ItemType Directory -Force -Path $Bk, "$Root\logs" | Out-Null
Start-Transcript -Path "$Root\logs\security_update_$Stamp.log" | Out-Null
# Task names exactly as registered on this server (LAN lives in the 'BIM Tasks' folder).
$LanTask = '\BIM Tasks\LAN'
$ExtTask = 'BIM Tasks External 8798'
$Tasks = @($LanTask, $ExtTask)
$Payload = "$Here\payload"
$Files = Get-ChildItem $Payload -Recurse -File | ForEach-Object { $_.FullName.Substring($Payload.Length + 1) }

function Step($t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }
function Stop-Bim {
    foreach ($t in $Tasks) { schtasks /End /TN $t 2>$null | Out-Null }
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
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   مهمة $LanTask غير موجودة؛ تشغيل RUN_LAN.cmd مباشرة" -ForegroundColor Yellow
        Start-Process -FilePath "$Root\RUN_LAN.cmd" -WorkingDirectory $Root -WindowStyle Minimized
    }
    schtasks /Run /TN $ExtTask 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Start-Process -FilePath "$Root\RUN_EXTERNAL.cmd" -WorkingDirectory $Root -WindowStyle Minimized }
}
function Wait-Health($port) {
    for ($i = 0; $i -lt 30; $i++) {
        try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$port/health" -ErrorAction Stop; if ($r.StatusCode -eq 200) { return $true } } catch {}
        Start-Sleep -Seconds 2
    }
    return $false
}
function New-CodeRunning($port) {
    # /api/me/security exists only in the updated code: it answers 401 there and 404 on the old code.
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$port/api/me/security" -ErrorAction Stop | Out-Null; return $true }
    catch { return ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) }
}
function Rollback($why) {
    Write-Host ''; Write-Host "!! فشل: $why" -ForegroundColor Red
    Write-Host '!! جاري إرجاع النظام لحالته السابقة...' -ForegroundColor Yellow
    Stop-Bim
    foreach ($f in $Files) {
        if (Test-Path "$Bk\files\$f") { Copy-Item "$Bk\files\$f" "$Root\$f" -Force }
        elseif (Test-Path "$Root\$f") { Remove-Item "$Root\$f" -Force }
    }
    if (Test-Path "$Bk\.env") { Copy-Item "$Bk\.env" "$Root\.env" -Force }
    foreach ($d in 'bim_tasks.db', 'bim_tasks.db-wal', 'bim_tasks.db-shm') {
        if (Test-Path "$Root\data\$d") { Remove-Item "$Root\data\$d" -Force }
        if (Test-Path "$Bk\data\$d") { Copy-Item "$Bk\data\$d" "$Root\data\$d" -Force }
    }
    Start-Bim
    Write-Host '!! رجع النظام متل ما كان. ابعت ملف السجل:' -ForegroundColor Yellow
    Write-Host "   $Root\logs\security_update_$Stamp.log"
    Stop-Transcript | Out-Null; exit 1
}

Write-Host "تحديث الصلاحيات والأمان — المجلد: $Root" -ForegroundColor Green

Step '1/7 إيقاف النظام'
Stop-Bim

Step '2/7 نسخة احتياطية'
foreach ($f in $Files) {
    if (Test-Path "$Root\$f") { $dst = "$Bk\files\$f"; New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null; Copy-Item "$Root\$f" $dst -Force }
}
if (Test-Path "$Root\.env") { Copy-Item "$Root\.env" "$Bk\.env" -Force }
New-Item -ItemType Directory -Force -Path "$Bk\data" | Out-Null
foreach ($d in 'bim_tasks.db', 'bim_tasks.db-wal', 'bim_tasks.db-shm') { if (Test-Path "$Root\data\$d") { Copy-Item "$Root\data\$d" "$Bk\data\$d" -Force } }
Write-Host "   $Bk"

try {
    Step '3/7 نسخ الملفات الجديدة'
    foreach ($f in $Files) { $dst = "$Root\$f"; New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null; Copy-Item "$Payload\$f" $dst -Force }
    Write-Host "   $($Files.Count) ملف"

    Step '4/7 تثبيت مكتبة التشفير'
    & $Py -m pip install --disable-pip-version-check --no-index --find-links "$Here\wheels" cryptography 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { & $Py -m pip install --disable-pip-version-check cryptography 2>&1 | Out-Host }
    if ($LASTEXITCODE -ne 0) { throw 'تعذر تثبيت cryptography' }

    Step '5/7 ضبط ملف .env'
    Push-Location $Root
    $out = & $Py tools\install_security_update.py env 2>&1; $out | Out-Host
    if (-not ($out -match 'ENV_OK')) { throw 'تعذر ضبط .env' }

    Step '6/7 فحص النظام قبل التشغيل'
    & $Py -c "import app.main; from app.seed import seed; seed(); print('IMPORT_OK')" 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'النظام الجديد ما اشتغل بالفحص' }
    Pop-Location
} catch { Pop-Location -ErrorAction SilentlyContinue; Rollback $_.Exception.Message }

Step '7/7 تشغيل النظام'
Start-Bim
if (-not (Wait-Health 8797)) { Rollback 'البوابة الداخلية 8797 ما ردّت بعد التشغيل' }
if (-not (New-CodeRunning 8797)) { Rollback 'البوابة 8797 عم تشغّل النسخة القديمة (عملية قديمة ما توقفت)' }
Write-Host '   البوابة الداخلية 8797: تعمل' -ForegroundColor Green
if (Wait-Health 8798) { Write-Host '   البوابة الخارجية 8798: تعمل' -ForegroundColor Green } else { Write-Host '   البوابة الخارجية 8798 ما ردّت — راجع logs\server-external.log' -ForegroundColor Yellow }

Push-Location $Root
$wh = & $Py tools\install_security_update.py webhook 2>&1
Pop-Location
if ($wh -match 'WEBHOOK_OK') { Write-Host '   Telegram: تم تفعيله بالسر الجديد' -ForegroundColor Green }
elseif ($wh -match 'WEBHOOK_FAILED') { Write-Host '   Telegram: ادخل مركز Telegram واضغط «إعداد Webhook»' -ForegroundColor Yellow }

@{ phase = 'success'; backup = $Bk; files = $Files } | ConvertTo-Json | Set-Content "$Root\_safe_patch_backups\security_last.json" -Encoding UTF8
Stop-Transcript | Out-Null

Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host ' تم التحديث بنجاح' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host ''
Write-Host ' مهم: انفتح هلق ملف فيه كلمة سر النسخ الاحتياطية.' -ForegroundColor Yellow
Write-Host ' اطبعه أو انسخه واحفظه برّا الشركة، بعدين احذفه.' -ForegroundColor Yellow
Write-Host ''
Write-Host ' بعدها من الموقع (من شبكة الشركة):'
Write-Host '  1) المستخدمون: حدّد فريق كل مسؤول'
Write-Host '  2) حسابي: فعّل تأكيد الدخول'
Write-Host '  3) الأمان والحماية: حدّد مجلد النسخ (Google Drive) واضغط «نسخة الآن»'
Start-Process notepad.exe "$Root\BACKUP_PASSWORD_KEEP_OUTSIDE.txt"
Start-Process 'http://127.0.0.1:8797/security'
