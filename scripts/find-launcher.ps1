Write-Host "=== BOT PROCESSES WITH PARENTS ==="
$bots = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" }
foreach ($b in $bots) {
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($b.ParentProcessId)" | Select-Object -First 1
    Write-Host "Bot PID:" $b.ProcessId "Parent PID:" $b.ParentProcessId
    if ($parent) {
        Write-Host "  Parent Name:" $parent.Name
        Write-Host "  Parent Path:" $parent.ExecutablePath
        Write-Host "  Parent Cmd:" $parent.CommandLine.Substring(0, [Math]::Min(150, $parent.CommandLine.Length))
    }
}

Write-Host ""
Write-Host "=== ALL PROCESSES WITH 'bot' IN COMMANDLINE ==="
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*bot*" -and $_.CommandLine -notlike "*find-launcher*" } | Select-Object ProcessId, Name, ExecutablePath, CommandLine
