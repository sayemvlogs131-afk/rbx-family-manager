# Check if admin-app-launcher.bat is still running somewhere
Write-Host "=== Looking for launcher processes ==="
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*admin-app*" -or $_.CommandLine -like "*launcher*" -or $_.Name -eq "cmd.exe" } | Select-Object ProcessId, Name, CommandLine
