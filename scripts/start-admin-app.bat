@echo off
rem ---------------------------------------------------------------------------
rem RBX Family Manager - start the bot and open the admin web app
rem ---------------------------------------------------------------------------
cd /d "%~dp0.."

echo [info] Starting RBX Family Manager...
start "" "cmd.exe" /k "ubuntu.exe" run "cd /d \"%~dp0..\" && .venv\Scripts\python.exe bot.py"
timeout /t 12 /nobreak >nul 2>&1

echo [info] Opening admin app...
start "" "http://localhost:8080/"

echo.
echo [info] Bot console is running in the window that just opened.
echo        Close that window to stop the bot.
pause
