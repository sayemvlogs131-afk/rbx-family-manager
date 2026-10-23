@echo off
rem ---------------------------------------------------------------------------
rem RBX Family Manager - manual "start hidden now" launcher
rem   Starts the bot in the background with no visible window.
rem   (For automatic start at PC login, use the shortcut already installed
rem    in the Windows Startup folder - it calls scripts\auto-start.vbs.)
rem ---------------------------------------------------------------------------
cd /d "%~dp0.."
wscript //nologo "%~dp0auto-start.vbs"
exit /b 0
