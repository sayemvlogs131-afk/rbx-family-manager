function Kill-Tree([int]$P) {
    Write-Host "Killing PID:" $P
    Stop-Process -Id $P -Force -ErrorAction SilentlyContinue
    $kids = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $P }
    foreach ($k in $kids) {
        Kill-Tree $k.ProcessId
    }
}

Write-Host "=== KILLING CMD PID 10508 AND ALL CHILDREN ==="
Kill-Tree 10508
Kill-Tree 2984
Start-Sleep 2

Write-Host "=== Remaining bot procs ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | ForEach-Object {
    Write-Host "  PID:" $_.ProcessId
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep 2

Write-Host "=== Port 8080 ==="
if ((netstat -ano | findstr :8080) -eq $null) { Write-Host "FREE" } else { Write-Host "IN USE"; netstat -ano | findstr :8080 }

Write-Host "=== Remaining python ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | Select-Object ProcessId, ParentProcessId, CommandLine
