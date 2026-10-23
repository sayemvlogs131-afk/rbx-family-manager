Write-Host "=== ALL PYTHON PROCESSES ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, Name, ExecutablePath, CommandLine, ParentProcessId

Write-Host ""
Write-Host "=== ALL PROCESSES ON 8080 ==="
$line = netstat -ano | findstr :8080 | Select-Object -First 1
if ($line) {
    $parts = $line.Split()
    $p = $parts[5]
    Write-Host "PID:" $p
    Get-CimInstance Win32_Process -Filter "ProcessId=$p" | Select-Object ProcessId, Name, ExecutablePath, CommandLine, ParentProcessId
}
