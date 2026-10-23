@echo off
rem ---------------------------------------------------------------------------
rem RBX Family Manager - hidden auto-restart launcher
rem   * runs the bot with no visible console window
rem   * ONE instance at a time: waits for the bot to exit before restarting
rem   * restarts automatically after a crash (5 second pause)
rem   * refuses to double-start if the health port is already in use
rem   * the bot itself still writes data/rbx_family.log for diagnosis
rem ---------------------------------------------------------------------------
cd /d "%~dp0.."
set "ROOT=%~dp0.."

if not exist "%ROOT%\.venv\Scripts\python.exe" (
    wscript //nologo "%~dp0alert.vbs" "Missing .venv\Scripts\python.exe - run start-bot.bat once first."
    exit /b 1
)

:restart
rem If something else already holds the health port, do not start a second copy.
netstat -ano | findstr /C:":8080" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    timeout /t 10 /nobreak >nul 2>&1
    goto restart
)

rem Run the bot hidden and WAIT until it exits before looping again.
wscript //nologo "%~dp0run-hidden-wait.vbs" "%ROOT%\.venv\Scripts\python.exe" "%ROOT%\bot.py"
timeout /t 5 /nobreak >nul 2>&1
goto restart
