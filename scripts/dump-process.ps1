Write-Host "=== PROCESS ON 8080 ==="
$line = netstat -ano | findstr :8080 | Select-Object -First 1
$parts = $line.Split()
$pid = $parts[5]
Write-Host "PID:" $pid
Get-CimInstance Win32_Process -Filter "ProcessId=$pid" | Select-Object ProcessId, Name, ExecutablePath, CommandLine, CreationDate

Write-Host ""
Write-Host "=== ALL BOT.PY PROCESSES ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | Select-Object ProcessId, Name, ExecutablePath, CommandLine, CreationDate

Write-Host ""
Write-Host "=== PYTHON PROCESSES ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, Name, ExecutablePath, CommandLine, CreationDate
