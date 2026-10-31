$ErrorActionPreference='SilentlyContinue'
$TaskPath='\BIM Tasks\'
try{Start-ScheduledTask -TaskPath $TaskPath -TaskName 'LAN' -ErrorAction SilentlyContinue}catch{}
for($i=0;$i -lt 40;$i++){
  Start-Sleep -Milliseconds 500
  try{
    $r=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8797/health' -TimeoutSec 1
    if($r.StatusCode -eq 200){Start-Process 'http://127.0.0.1:8797';exit 0}
  }catch{}
}
Start-Process 'http://127.0.0.1:8797'
