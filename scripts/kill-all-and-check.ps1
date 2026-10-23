# Kill wscript
Write-Host "Killing wscript..."
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "wscript.exe" -or $_.Name -eq "cscript.exe" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1

# Kill all bot.py processes
Write-Host "Killing bot.py processes..."
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | ForEach-Object {
    Write-Host "  Killing PID:" $_.ProcessId
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep 3

if ((netstat -ano | findstr :8080) -eq $null) { Write-Host "Port 8080: FREE" } else { Write-Host "Port 8080: IN USE"; netstat -ano | findstr :8080 }

# Check log
$log = "D:\all in new\BOT\data\rbx_family.log"
if (Test-Path $log) {
    $lines = Get-Content $log -ErrorAction SilentlyContinue
    Write-Host "=== Last 8 log lines ==="
    $lines | Select-Object -Last 8
}
