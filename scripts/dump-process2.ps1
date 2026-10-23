Write-Host "=== ALL 8080 CONNECTIONS (LISTENING ONLY) ==="
netstat -ano | findstr :8080 | Select-String "LISTENING"

Write-Host ""
Write-Host "=== ALL BOT.PY PYTHON PROCESSES ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | Select-Object ProcessId, Name, ExecutablePath, CommandLine, CreationDate
