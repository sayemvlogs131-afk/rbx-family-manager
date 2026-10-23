$env:PYTHONPATH = "D:\all in new\BOT"
$cwd = "D:\all in new\BOT"
$python = "D:\all in new\BOT\.venv\Scripts\python.exe"
$logfile = "$cwd\log_startup.txt"

$proc = Start-Process -FilePath $python -ArgumentList "bot.py" -WorkingDirectory $cwd -NoNewWindow -PassThru -RedirectStandardOutput "$logfile.out" -RedirectStandardError "$logfile.err"
Start-Sleep 12
$proc | Stop-Process -Force -ErrorAction SilentlyContinue
Wait-Process -Id $proc.Id -Timeout 5 -ErrorAction SilentlyContinue
$proc.WaitForExit()
$proc.Close()

Get-Content "$logfile.out" -ErrorAction SilentlyContinue
Write-Host "=== STDERR ==="
Get-Content "$logfile.err" -ErrorAction SilentlyContinue
