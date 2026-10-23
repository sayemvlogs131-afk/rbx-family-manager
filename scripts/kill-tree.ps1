function Kill-Tree($pid) {
    Write-Host "Killing PID:" $pid
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    # Find children
    $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $pid }
    foreach ($c in $children) {
        Kill-Tree $c.ProcessId
    }
}

Write-Host "=== FINDING BOT PARENTS ==="
$bots = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" }
foreach ($b in $bots) {
    Write-Host "Bot PID:" $b.ProcessId "Parent:" $b.ParentProcessId "Cmd:" $b.CommandLine.Substring(0, [Math]::Min(100, $b.CommandLine.Length))
    Kill-Tree $b.ProcessId
}
Start-Sleep 3

$remaining = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" }
if ($remaining) {
    Write-Host "REMAINING:" $remaining.Count
    $remaining | Select-Object ProcessId, ParentProcessId, CommandLine
} else {
    Write-Host "All bot.py killed."
}
if ((netstat -ano | findstr :8080) -eq $null) {
    Write-Host "Port 8080: FREE"
} else {
    Write-Host "Port 8080: IN USE"
}
