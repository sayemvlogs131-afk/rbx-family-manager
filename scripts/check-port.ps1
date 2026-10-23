$line = netstat -ano | findstr :8080 | Select-Object -First 1
$parts = $line.Split()
$pid = $parts[-1]
Write-Host "PID on 8080:" $pid
Get-CimInstance Win32_Process -Filter "ProcessId=$pid" | Select-Object ProcessId, Name, ExecutablePath, CommandLine
