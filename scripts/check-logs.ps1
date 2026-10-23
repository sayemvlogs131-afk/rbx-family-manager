Get-Content "D:\all in new\BOT\log_bot.out" -ErrorAction SilentlyContinue | Select-Object -Last 15
Write-Host "=== STDERR ==="
Get-Content "D:\all in new\BOT\log_bot.err" -ErrorAction SilentlyContinue | Select-Object -Last 10
