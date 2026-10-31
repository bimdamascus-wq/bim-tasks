$Host.UI.RawUI.WindowTitle='BIM Tasks - Status'
Write-Host '============================================' -ForegroundColor Cyan
Write-Host '           BIM TASKS - STATUS' -ForegroundColor Cyan
Write-Host '============================================' -ForegroundColor Cyan
foreach($x in @(@('LAN','http://127.0.0.1:8797/health'),@('External','http://127.0.0.1:8798/health'))){
  try{
    $r=Invoke-WebRequest -UseBasicParsing -Uri $x[1] -TimeoutSec 2
    if($r.StatusCode -eq 200){Write-Host ($x[0]+': RUNNING') -ForegroundColor Green}
    else{Write-Host ($x[0]+': ERROR') -ForegroundColor Red}
  }catch{Write-Host ($x[0]+': STOPPED') -ForegroundColor Red}
}
Write-Host ''
Write-Host 'LAN: http://127.0.0.1:8797/' -ForegroundColor Yellow
Write-Host 'External internal endpoint: http://127.0.0.1:8798/' -ForegroundColor Yellow
Write-Host 'Public domain: https://task.bimdamascus.com' -ForegroundColor Yellow
Write-Host ''
pause
