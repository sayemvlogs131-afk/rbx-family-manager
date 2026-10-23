@echo off
rem ---------------------------------------------------------------------------
rem RBX Family Manager - double-click to run the bot on Windows
rem   * creates the virtual environment on first run
rem   * refuses to start a second copy (two sessions with one token fight)
rem   * writes every step to launch.log so problems can be diagnosed
rem Keep this window open while you want the bot online.
rem ---------------------------------------------------------------------------
cd /d "%~dp0"
set "LOG=%~dp0launch.log"
echo [%date% %time%] --- launcher started --- > "%LOG%"

rem --- 0. already running? -------------------------------------------------
netstat -ano | findstr /C:":8080" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [%date% %time%] refused: another instance is already running >> "%LOG%"
    echo.
    echo [info] The bot is already running.
    echo        Close the other black window first, then run this again.
    echo.
    pause
    exit /b 1
)

rem --- 1. startup banner -------------------------------------------------
echo [%date% %time%] RBX Family Manager launcher starting >> "%LOG%"
echo =========================================================== >> "%LOG%"

rem --- 1. .env ------------------------------------------------------------
if not exist "%~dp0.env" (
    echo [%date% %time%] FAILED: .env not found >> "%LOG%"
    echo [fail] No .env file found. Copy .env.example to .env and fill it in.
    pause
    exit /b 1
)

rem --- 2. virtual environment --------------------------------------------
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [%date% %time%] creating virtual environment >> "%LOG%"
    echo [info] First run - creating virtual environment, this takes a minute...
    python -m venv .venv 2>>"%LOG%"
    if not exist "%~dp0.venv\Scripts\python.exe" (
        echo [%date% %time%] FAILED: venv creation failed >> "%LOG%"
        echo [fail] Could not create the virtual environment.
        echo        Is Python installed? Download it from https://www.python.org/downloads/
        echo        and tick "Add python.exe to PATH" during setup.
        pause
        exit /b 1
    )
)

call "%~dp0.venv\Scripts\activate.bat"
set "PY=%~dp0.venv\Scripts\python.exe"
echo [%date% %time%] using %PY% >> "%LOG%"
"%PY%" --version >> "%LOG%" 2>&1
"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] FAILED: python.exe is not runnable >> "%LOG%"
    echo [fail] The Python interpreter in .venv is not runnable.
    pause
    exit /b 1
)

if not exist "%~dp0data" mkdir "%~dp0data"

rem --- 3. run -------------------------------------------------------------
echo [%date% %time%] starting bot.py >> "%LOG%"
echo [info] Starting RBX Family Manager...
echo [info] Wait for: "Logged in as RBX Family Manager"
echo.
"%PY%" "%~dp0bot.py"
set "RC=%ERRORLEVEL%"
echo.
echo [%date% %time%] bot exited with code %RC% >> "%LOG%"
echo [info] Bot stopped (exit code %RC%). Press any key to close this window.
pause >nul
exit /b %RC%
