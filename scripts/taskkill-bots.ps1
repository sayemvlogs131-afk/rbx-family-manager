Write-Host "Killing PID 7792..."
Start-Process "taskkill.exe" -ArgumentList "/F", "/PID", "7792", "/T" -Wait -NoNewWindow
Write-Host "Killing PID 15960..."
Start-Process "taskkill.exe" -ArgumentList "/F", "/PID", "15960", "/T" -Wait -NoNewWindow
Write-Host "Killing PID 17956..."
Start-Process "taskkill.exe" -ArgumentList "/F", "/PID", "17956", "/T" -Wait -NoNewWindow
Start-Sleep 3
Write-Host "=== Port 8080 ==="
netstat -ano | findstr :8080
Write-Host "=== Remaining python ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | Select-Object ProcessId, CommandLine
