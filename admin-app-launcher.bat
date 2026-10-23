@echo off
@echo off
cd /d "%~dp0.."
echo [info] Starting RBX Family Manager admin app...
echo [info] If the bot is not running, this will start it for you.
echo [info] The admin app will open in your browser automatically.
echo.
set "PYTHONPATH=%~dp0.."
"%~dp0.venv\Scripts\python.exe" -m admin_app
echo.
pause

