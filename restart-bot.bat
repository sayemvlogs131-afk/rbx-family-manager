@echo off
rem ---------------------------------------------------------------------------
rem RBX Family Manager - stop then start the bot in one action
rem Uses the same port-based stop logic as stop-bot.bat, then forwards to
rem start-bot.bat so the venv / single-instance checks still apply.
rem ---------------------------------------------------------------------------
cd /d "%~dp0"
set "LOG=%~dp0launch.log"
echo [%date% %time%] --- restart requested --- >> "%LOG%"
echo [info] Stopping RBX Family Manager (if running)...

rem The running bot owns :8080. Kill whatever holds that port.
netstat -ano | findstr /C:":8080" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":8080" ^| findstr /C:"LISTENING"') do (
        taskkill /F /PID %%p >nul 2>&1
    )
)

rem Give the OS time to release the port.
powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1

rem If the port is still busy, force remaining python.exe cleanup.
netstat -ano | findstr /C:":8080" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [warn] Port still in use - forcing python.exe cleanup...
    taskkill /F /IM python.exe /FI "WINDOWTITLE eq *RBX*" >nul 2>&1
    taskkill /F /IM python.exe >nul 2>&1
    powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
)

rem Forward to the normal launcher.
call "%~dp0start-bot.bat"
