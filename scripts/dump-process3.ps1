Write-Host "=== ALL PYTHON PROCESSES ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, Name, ExecutablePath, CommandLine, CreationDate
Write-Host ""
Write-Host "=== PORT 8080 ==="
netstat -ano | findstr :8080
