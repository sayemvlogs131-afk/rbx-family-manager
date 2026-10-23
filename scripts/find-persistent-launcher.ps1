Write-Host "=== TASKS WITH 'bot' IN NAME ==="
Get-ScheduledTask -TaskPath "\" | Where-Object { $_.TaskName -like "*bot*" -or $_.TaskName -like "*admin*" -or $_.TaskName -like "*rbx*" } | Select-Object TaskName, TaskPath, State

Write-Host ""
Write-Host "=== TASKS IN ROOT ==="
Get-ScheduledTask | Select-Object TaskName, TaskPath, State | Where-Object { $_.TaskName -like "*bot*" -or $_.TaskName -like "*start*" }

Write-Host ""
Write-Host "=== STARTUP FOLDER ==="
$startup = $env:APPDATA + "\Microsoft\Windows\Start Menu\Programs\Startup"
if (Test-Path $startup) {
    Get-ChildItem $startup -ErrorAction SilentlyContinue | Select-Object Name, FullName
}

Write-Host ""
Write-Host "=== RUN REGISTRY KEYS ==="
try {
    Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue | Select-Object -ExcludeProperty PSPath, PSParentPath, PSChildName, PSDrive, PSProvider
} catch {}
try {
    Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue | Select-Object -ExcludeProperty PSPath, PSParentPath, PSChildName, PSDrive, PSProvider
} catch {}
