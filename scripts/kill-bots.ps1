Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | ForEach-Object {
    Write-Host "Killing PID:" $_.ProcessId "CommandLine:" $_.CommandLine.Substring(0, [Math]::Min(100, $_.CommandLine.Length))
    Stop-Process -Id $_.ProcessId -Force
}
Start-Sleep 2
$remaining = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" }
if ($remaining) {
    Write-Host "WARNING: Could not kill some processes"
} else {
    Write-Host "All bot.py processes killed."
}
if ((netstat -ano | findstr :8080) -eq $null) { Write-Host "Port 8080: FREE" } else { Write-Host "Port 8080: IN USE" }
