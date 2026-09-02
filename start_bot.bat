@echo off
cd /d "%~dp0"
if exist bot.lock (
    echo Ya hay un inicio en curso. Si el bot no esta corriendo, borre bot.lock.
    exit /b 1
)
type nul > bot.lock
powershell -Command "$p=Get-CimInstance Win32_Process -Filter 'name=''python.exe'' AND CommandLine like ''%%bot_trading%%''' | Select-Object -First 1; if($p){Write-Host ('El bot ya esta en ejecucion (PID '+$p.ProcessId+').'); Remove-Item bot.lock -ErrorAction SilentlyContinue; exit 1}"
if errorlevel 1 (
    if exist bot.lock del bot.lock
    exit /b 1
)
echo Starting bot_trading.py in background...
powershell -Command "$p=Start-Process py -ArgumentList '-3','bot_trading.py' -WindowStyle Hidden -PassThru; Start-Sleep -Seconds 3; $bpid=Get-CimInstance Win32_Process -Filter 'name=''python.exe'' AND CommandLine like ''%%bot_trading%%''' | Select-Object -First 1 -ExpandProperty ProcessId; if($bpid){$bpid | Set-Content bot.pid; Write-Host ('PID: '+$bpid)}else{$p.Id | Set-Content bot.pid; Write-Host ('PID: '+$p.Id)}"
del bot.lock
echo Bot iniciado.
