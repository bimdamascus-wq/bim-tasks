$ErrorActionPreference='Continue'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$py=Join-Path $Root '.venv\Scripts\python.exe'
$log=Join-Path $Root 'logs\server-external.log'
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'logs')|Out-Null
('['+(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')+'] External starting')|Out-File $log -Append -Encoding utf8
$env:BIM_GATEWAY='external';$env:BIM_PORT='8798';$env:BIM_COOKIE_SECURE='1'
try{ & $py -m uvicorn app.main:app --host 127.0.0.1 --port 8798 *>> $log }catch{ ('START ERROR: '+$_.Exception.Message)|Out-File $log -Append -Encoding utf8 }
