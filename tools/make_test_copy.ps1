# BIM Tasks - make or refresh the staging copy in C:\BIM_Tasks_Test (port 8799, this computer only).
# Started by TEST_COPY_CREATE.cmd in the real system folder. The real system keeps running and is only read.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Src = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Dst = 'C:\BIM_Tasks_Test'
$Port = 8799
function Fail($t) { Write-Host ''; Write-Host "!! $t" -ForegroundColor Red; exit 1 }
function Step($t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }

Write-Host 'نسخة تجريبية من BIM Tasks' -ForegroundColor Green
Write-Host '(لا تكبس بالماوس جوّا هالنافذة لحتى تخلص)' -ForegroundColor DarkGray
if (-not (Test-Path "$Src\app\main.py")) { Fail "ما لقيت النظام بـ $Src" }
if (Test-Path "$Src\TEST_COPY.txt") { Fail 'هاد الملف لازم يتشغّل من مجلد النظام الحقيقي C:\BIM_Tasks_Server، مو من النسخة التجريبية.' }
if ($Src.TrimEnd('\') -ieq $Dst) { Fail 'مجلد النسخة التجريبية هو نفسه مجلد النظام. تم الإلغاء.' }

Step '1/4 إيقاف النسخة التجريبية القديمة (النظام الحقيقي ما بيتوقف)'
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app' -and $_.CommandLine -match "--port\s+$Port" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
for ($i = 0; $i -lt 15; $i++) { if (-not (netstat -ano | Select-String -Pattern ":$Port\s.*LISTENING")) { break }; Start-Sleep -Seconds 2 }
if (Test-Path $Dst) {
    if (-not (Test-Path "$Dst\TEST_COPY.txt")) { Fail "المجلد $Dst موجود بس مو نسخة تجريبية. ما رح امسحه. غيّر اسمه أو امسحه بإيدك وارجع جرّب." }
    Remove-Item $Dst -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $Dst) { Fail "ما قدرت امسح النسخة القديمة $Dst (في ملف مفتوح؟). سكّر أي نافذة منها وارجع جرّب." }
}

Step '2/4 نسخ البرنامج (بدون البيانات والأسرار)'
$xd = @("$Src\data", "$Src\logs", "$Src\backups", "$Src\_safe_patch_backups", "$Src\_mytask_update", "$Src\ui_backups", "$Src\sync_full_logs", "$Src\dist", "$Src\.git", '__pycache__', '.pytest_cache')
robocopy $Src $Dst /E /R:1 /W:1 /NFL /NDL /NJH /NP /XD $xd /XF '.env' '.env.*' '*.bimbak' 'BACKUP_PASSWORD_KEEP_OUTSIDE.txt' | Out-Host
if ($LASTEXITCODE -ge 8) { Fail "النسخ فشل (robocopy $LASTEXITCODE)" }
$Py = "$Dst\.venv\Scripts\python.exe"
$prefix = (& $Py -c "import sys, fastapi, uvicorn; print(sys.prefix)" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail "بايثون بالنسخة التجريبية ما اشتغل: $prefix" }
if (-not $prefix.StartsWith($Dst, [StringComparison]::OrdinalIgnoreCase)) { Write-Host "   تنبيه: بايثون عم يستعمل $prefix" -ForegroundColor Yellow }

Step '3/4 نسخ البيانات وفصل التلغرام والشيت والنسخ الاحتياطية'
& $Py "$Dst\tools\make_test_copy.py" --src $Src --dst $Dst 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { Fail 'نسخ البيانات فشل. النظام الحقيقي ما تأثر.' }

Step "4/4 تشغيل النسخة التجريبية على $Port"
Start-Process -FilePath "$Dst\RUN_TEST.cmd" -WorkingDirectory $Dst -WindowStyle Minimized
$r = $null
for ($i = 0; $i -lt 40 -and -not $r; $i++) {
    Start-Sleep -Seconds 2
    try { $r = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 "http://127.0.0.1:$Port/api/release-health" -ErrorAction Stop).Content | ConvertFrom-Json } catch { }
}
if (-not $r) { Fail "النسخة التجريبية ما ردّت. شوف $Dst\logs\server-test.log" }
if ($r.gateway -ne 'test') { Fail "النسخة عم تشتغل بوضع $($r.gateway) بدل test. ما تستعملها وابعت هالرسالة." }

Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host ' النسخة التجريبية جاهزة: http://127.0.0.1:8799' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host ' - بتفتح من هالسيرفر بس. نفس أسماء المستخدمين وكلمات السر.'
Write-Host ' - التلغرام والشيت والنسخ الاحتياطية الخارجية مفصولة: ما رح يوصل شي للموظفين.'
Write-Host ' - تأكيد الدخول بالتلغرام مطفى بالنسخة التجريبية بس.'
Write-Host ' - لتجريب تحديث: INSTALL_TO_TEST.cmd من مجلد التحديث.'
Write-Host ' - لإيقافها: C:\BIM_Tasks_Test\STOP_TEST.cmd   لتحديث بياناتها: شغّل هالملف مرة تانية.'
Start-Process "http://127.0.0.1:$Port"
