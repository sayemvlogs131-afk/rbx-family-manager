@echo off
rem ---------------------------------------------------------------------------
rem RBX Family Manager - stop the running bot
rem Safe to run even if nothing is running.
rem ---------------------------------------------------------------------------
cd /d "%~dp0"
set "LOG=%~dp0launch.log"
echo [%date% %time%] --- stop requested --- >> "%LOG%"
echo [info] Stopping RBX Family Manager...

rem The bot owns :8080 while it is up - that is the single reliable source of
rem truth no matter how it was started (visible window, hidden launcher,
rem restart loop, shortcuts, etc.). Kill the process holding that port.
netstat -ano | findstr /C:":8080" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr /C:":8080" ^| findstr /C:"LISTENING"') do (
        taskkill /F /PID %%p >nul 2>&1
    )
)

rem Give the OS a moment to release the port.
powershell -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1

rem Anything still listening on :8080 is killed by any remaining python.exe.
rem This catches hidden-launcher runs whose command line does not contain
rem "bot.py" literally, plus any runaway python.exe that the port check alone
rem did not manage to clear.
netstat -ano | findstr /C:":8080" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [warn] Port still in use - forcing python.exe cleanup...
    taskkill /F /IM python.exe /FI "WINDOWTITLE eq *RBX*" >nul 2>&1
    taskkill /F /IM python.exe >nul 2>&1
    powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
)

netstat -ano | findstr /C:":8080" | findstr /C:"LISTENING" >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] stopped successfully >> "%LOG%"
    echo [ok] Bot stopped. Run start-bot.bat to start it again.
) else (
    echo [%date% %time%] FAILED to stop >> "%LOG%"
    echo [fail] The bot is still running on port 8080. Close its window manually.
)
echo.
pause
