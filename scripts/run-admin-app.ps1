$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path -Path $projectRoot -ChildPath ".venv" | Join-Path -ChildPath "Scripts" | Join-Path -ChildPath "python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Error "Missing .venv\Scripts\python.exe - run start-bot.bat once first."
    exit 1
}

Write-Output "[info] Starting RBX Family Manager admin app..."
Write-Output "[info] If the bot is not running, this will start it for you."
Write-Output "[info] The admin app will open in your browser automatically."
Write-Output ""

$env:PYTHONPATH = $projectRoot

$proc = Start-Process -FilePath $pythonExe -ArgumentList "-m", "admin_app" -NoNewWindow -PassThru -WorkingDirectory $projectRoot -Wait -RedirectStandardOutput "$env:TEMP\admin-app-stdout.log" -RedirectStandardError "$env:TEMP\admin-app-stderr.log"

$exitCode = $proc.ExitCode

# If the process failed, show the captured logs.
if ($exitCode -ne 0) {
    Write-Output "--- stdout ---"
    Get-Content "$env:TEMP\admin-app-stdout.log" -ErrorAction SilentlyContinue
    Write-Output "--- stderr ---"
    Get-Content "$env:TEMP\admin-app-stderr.log" -ErrorAction SilentlyContinue
}

exit $exitCode
