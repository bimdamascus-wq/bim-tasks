$InstallRoot='C:\BIM_Tasks_Server'
$LogDir=Join-Path $InstallRoot 'logs'
$Desktop=[Environment]::GetFolderPath('Desktop')
$Out=Join-Path $Desktop 'BIM_Tasks_Diagnostics.txt'
$x=@('BIM Tasks Diagnostics','=====================','Date: '+(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),'')
foreach($f in @('install.log','preflight.out.log','preflight.err.log','admin-verify.out.log','admin-verify.err.log','server-lan.out.log','server-lan.err.log','pip-install.log')){
  $x+=('--- '+$f+' ---')
  $p=Join-Path $LogDir $f
  if(Test-Path $p){$x+=Get-Content $p -Tail 200 -ErrorAction SilentlyContinue}else{$x+='(missing)'}
  $x+=''
}
$x+='--- PORT 8797 ---'
$n=& netstat -ano|Select-String ':8797'
if($n){$x+=$n.ToString()}else{$x+='(nothing listening)'}
$x|Set-Content $Out -Encoding UTF8
Start-Process notepad.exe -ArgumentList ('"'+$Out+'"')
