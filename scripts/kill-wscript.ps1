Write-Host "Killing wscript PID 15376..."
Stop-Process -Id 15376 -Force -ErrorAction SilentlyContinue
Start-Sleep 2

Write-Host "Killing all bot.py python processes..."
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | ForEach-Object {
    Write-Host "  Killing PID:" $_.ProcessId
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep 3

Write-Host "=== Port 8080 ==="
if ((netstat -ano | findstr :8080) -eq $null) {
    Write-Host "FREE"
} else {
    Write-Host "IN USE"
    netstat -ano | findstr :8080
}

Write-Host "=== Remaining bot procs ==="
$remaining = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" }
if ($remaining) {
    $remaining | Select-Object ProcessId, CommandLine
} else {
    Write-Host "None."
}
