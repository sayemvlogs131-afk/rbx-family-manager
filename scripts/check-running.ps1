$line = netstat -ano | findstr :8080 | Select-Object -First 1
if (-not $line) {
    Write-Host "NOTHING on 8080"
    exit
}
$parts = $line.Split()
$pid = $parts[5]
Write-Host "PID on 8080:" $pid
Get-Process -Id $pid -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, Path, StartTime
Write-Host "=== CIM ==="
Get-CimInstance Win32_Process -Filter "ProcessId=$pid" | Select-Object ProcessId, Name, ExecutablePath, CommandLine
