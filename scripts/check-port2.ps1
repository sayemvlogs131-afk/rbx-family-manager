Write-Host "=== ALL 8080 LISTENERS ==="
netstat -ano | findstr :8080
Write-Host ""
Write-Host "=== ALL PYTHON PROCESSES ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, ExecutablePath, CommandLine
