$ErrorActionPreference='Continue'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$py=Join-Path $Root '.venv\Scripts\python.exe'
$log=Join-Path $Root 'logs\server-lan.log'
New-Item -ItemType Directory -Force -Path (Join-Path $Root 'logs')|Out-Null
('['+(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')+'] LAN starting')|Out-File $log -Append -Encoding utf8
$env:BIM_GATEWAY='lan';$env:BIM_PORT='8797';$env:BIM_COOKIE_SECURE='0'
try{ & $py -m uvicorn app.main:app --host 0.0.0.0 --port 8797 *>> $log }catch{ ('START ERROR: '+$_.Exception.Message)|Out-File $log -Append -Encoding utf8 }
