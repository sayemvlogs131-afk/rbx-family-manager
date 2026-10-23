$env:PYTHONPATH = "D:\all in new\BOT"
$cwd = "D:\all in new\BOT"
$python = "D:\all in new\BOT\.venv\Scripts\python.exe"

$proc = Start-Process -FilePath $python -ArgumentList "bot.py" -WorkingDirectory $cwd -NoNewWindow -PassThru -RedirectStandardOutput "$cwd\log_bot.out" -RedirectStandardError "$cwd\log_bot.err"

Start-Sleep 14

try {
    $resp = Invoke-WebRequest -Uri "http://localhost:8080/health" -TimeoutSec 5 -UseBasicParsing
    Write-Host "HEALTH STATUS:" $resp.StatusCode
    Write-Host $resp.Content
} catch {
    Write-Host "HEALTH CHECK FAILED:" $_.Exception.Message
}

Get-Content "$cwd\log_bot.out" -ErrorAction SilentlyContinue | Select-Object -First 30
Write-Host "=== STDERR ==="
Get-Content "$cwd\log_bot.err" -ErrorAction SilentlyContinue | Select-Object -First 20

# leave running so user can test
Write-Host "BOT STILL RUNNING PID:" $proc.Id
