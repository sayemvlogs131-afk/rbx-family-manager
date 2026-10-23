function Kill-Tree([int]$PID) {
    Write-Host "Killing PID:" $PID
    Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue
    $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $PID }
    foreach ($c in $children) {
        Kill-Tree $c.ProcessId
    }
}

Write-Host "=== BOT PROCESSES AND PARENTS ==="
$bots = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" }
$parentIds = @{}
foreach ($b in $bots) {
    Write-Host "Bot PID:" $b.ProcessId "Parent:" $b.ParentProcessId
    $parentIds[$b.ParentProcessId] = $true
    Kill-Tree $b.ProcessId
}

# Also kill the parents
foreach ($pid in $parentIds.Keys) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$pid" | Select-Object -First 1
    if ($p) {
        Write-Host "Killing parent PID:" $pid "Name:" $p.Name "Cmd:" $p.CommandLine.Substring(0, [Math]::Min(100, $p.CommandLine.Length))
        Kill-Tree $pid
    }
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
    netstat -ano | findstr :8080
}
