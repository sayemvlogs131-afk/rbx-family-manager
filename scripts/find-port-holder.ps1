Write-Host "=== ALL 8080 CONNECTIONS ==="
netstat -ano | findstr :8080

Write-Host ""
Write-Host "=== ALL PROCESSES (not just python) ==="
Get-CimInstance Win32_Process | Select-Object ProcessId, Name, ExecutablePath, CommandLine | Where-Object { $_.CommandLine -like "*bot*" -or $_.CommandLine -like "*8080*" -or $_.Name -eq "python.exe" }
