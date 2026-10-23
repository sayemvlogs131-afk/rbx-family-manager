Write-Host "=== KILLING ALL PYTHON ==="
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
foreach ($p in $procs) {
    Write-Host "Killing PID:" $p.ProcessId "Path:" $p.ExecutablePath "Cmd:" $p.CommandLine.Substring(0, [Math]::Min(80, $p.CommandLine.Length))
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep 5
$remaining = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
if ($remaining) {
    Write-Host "WARNING: Could not kill:" $remaining.Count "processes"
    $remaining | Select-Object ProcessId, CommandLine
} else {
    Write-Host "All python killed."
}
if ((netstat -ano | findstr :8080) -eq $null) {
    Write-Host "Port 8080: FREE"
} else {
    Write-Host "Port 8080: IN USE"
    netstat -ano | findstr :8080
}
