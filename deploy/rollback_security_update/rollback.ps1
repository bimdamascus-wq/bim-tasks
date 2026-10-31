# Restore BIM Tasks to the state before the first security-update attempt, then collect diagnostics.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$R = 'C:\BIM_Tasks_Server'
$Diag = "$env:USERPROFILE\Desktop\bim_diag.txt"
# The first attempt (07:38) holds the true original files; later attempts backed up already-updated files.
$Bk = "$R\_safe_patch_backups\security_20261031_073843"
if (-not (Test-Path $Bk)) { $Bk = (Get-ChildItem "$R\_safe_patch_backups" -Directory -Filter 'security_*' | Sort-Object Name | Select-Object -First 1).FullName }
if (-not $Bk -or -not (Test-Path "$Bk\files")) { Write-Host 'ما لقيت النسخة الاحتياطية. ما تغيّر شي.' -ForegroundColor Red; exit 1 }

Write-Host "== 1/4 جمع سبب الفشل (قبل أي تغيير)" -ForegroundColor Cyan
& {
  "=== backup used: $Bk"
  '=== server-lan.err.log'; Get-Content "$R\logs\server-lan.err.log" -Tail 120 -ErrorAction SilentlyContinue
  '=== server-lan.out.log'; Get-Content "$R\logs\server-lan.out.log" -Tail 40 -ErrorAction SilentlyContinue
  '=== server-external.log'; Get-Content "$R\logs\server-external.log" -Tail 60 -ErrorAction SilentlyContinue
  foreach ($l in Get-ChildItem "$R\logs" -Filter 'security_update_*.log' -ErrorAction SilentlyContinue) { "=== $($l.Name)"; Get-Content $l.FullName }
  '=== LAN task'; schtasks /Query /TN "LAN" /V /FO LIST
  '=== External task'; schtasks /Query /TN "BIM Tasks External 8798" /V /FO LIST
  '=== port 8797/8798'; netstat -ano | findstr ":8797 :8798"
  '=== python processes'; Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId,CommandLine | Format-List
  '=== python version'; & "$R\.venv\Scripts\python.exe" --version
} *>&1 | Out-File $Diag -Encoding utf8

Write-Host "== 2/4 إيقاف النظام" -ForegroundColor Cyan
schtasks /End /TN "LAN" 2>$null | Out-Null
schtasks /End /TN "BIM Tasks External 8798" 2>$null | Out-Null
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'uvicorn' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 3

Write-Host "== 3/4 إرجاع الملفات القديمة من $Bk" -ForegroundColor Cyan
$New = 'app\access.py','app\backup_offsite.py','app\security_center.py','app\templates\account.html','app\templates\login_verify.html','app\templates\permissions.html','app\templates\security.html','tools\restore_bim_tasks.py','tools\install_security_update.py','RESTORE_BACKUP.cmd','tests\test_security.py'
Copy-Item "$Bk\files\*" $R -Recurse -Force
foreach ($f in $New) { Remove-Item "$R\$f" -Force -ErrorAction SilentlyContinue }
if (Test-Path "$Bk\.env") { Copy-Item "$Bk\.env" "$R\.env" -Force }

Write-Host "== 4/4 تشغيل النظام" -ForegroundColor Cyan
schtasks /Run /TN "LAN" | Out-Host
schtasks /Run /TN "BIM Tasks External 8798" | Out-Host
$ok = $false
for ($i = 0; $i -lt 45; $i++) {
  try { if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 'http://127.0.0.1:8797/health' -ErrorAction Stop).StatusCode -eq 200) { $ok = $true; break } } catch {}
  Start-Sleep 2
}
"=== after rollback: LAN health = $ok" | Out-File $Diag -Append -Encoding utf8
if (-not $ok) {
  '=== server-lan.err.log after rollback' | Out-File $Diag -Append -Encoding utf8
  Get-Content "$R\logs\server-lan.err.log" -Tail 60 -ErrorAction SilentlyContinue | Out-File $Diag -Append -Encoding utf8
}
Write-Host ''
if ($ok) { Write-Host ' النظام رجع يشتغل متل قبل.' -ForegroundColor Green }
else { Write-Host ' النظام لسا ما ردّ. جرّب START_BIM_TASKS.bat، وابعتلي الملف.' -ForegroundColor Red }
Write-Host " ابعتلي محتوى الملف اللي رح ينفتح: $Diag" -ForegroundColor Yellow
notepad $Diag
