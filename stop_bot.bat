@echo off
cd /d "%~dp0"
choice /C SN /M "Detener el bot"
if errorlevel 2 exit /b 0
rem Verificar posiciones activas antes de detener
powershell -NoProfile -Command "$j=Get-Content estado_bot.json -Raw|ConvertFrom-Json; $active=@(); $j.posiciones.PSObject.Properties|%%{if($_.Value -ne $null){$active+=$_.Name}}; if($active.Count -gt 0){Write-Host ''; Write-Host ('⚠  Hay posiciones activas: '+($active -join ', ')); exit 1} else {exit 0}"
if errorlevel 1 (
    choice /C SN /M "Detener igual"
    if errorlevel 2 exit /b 0
)
if exist bot.pid (
    set /p PID=<bot.pid
    tasklist /FI "PID eq %PID%" 2>nul | findstr /C:"python" >nul
    if not errorlevel 1 (
        taskkill /F /PID %PID% >nul 2>&1
        echo Bot detenido (PID %PID%^).
        del bot.pid
        exit /b 0
    )
)
powershell -Command "$p=Get-CimInstance Win32_Process -Filter 'name=''python.exe'' AND CommandLine like ''%%bot_trading%%''' | Select-Object -First 1; if($p){taskkill /F /PID $p.ProcessId; Remove-Item bot.pid -ErrorAction SilentlyContinue; Write-Host ('Bot detenido (PID '+$p.ProcessId+').')}else{Write-Host 'No se encontro el bot en ejecucion.'}"