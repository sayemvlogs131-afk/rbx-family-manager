function Get-ParentChain([int]$PID) {
    $chain = @()
    $current = $PID
    while ($current -and $current -gt 0) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$current" | Select-Object -First 1
        if (-not $p) { break }
        $chain += $p
        $current = $p.ParentProcessId
        if ($current -eq $current) { break }  # prevent infinite loop
        if ($chain.Count -gt 10) { break }
    }
    return $chain
}

Write-Host "=== BOT PROCESS PARENT CHAINS ==="
$bots = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" }
foreach ($b in $bots) {
    Write-Host "Bot PID:" $b.ProcessId "Cmd:" $b.CommandLine.Substring(0, [Math]::Min(100, $b.CommandLine.Length))
    $chain = Get-ParentChain $b.ProcessId
    foreach ($p in $chain) {
        Write-Host "  PID:" $p.ProcessId "Name:" $p.Name "Path:" $p.ExecutablePath "Cmd:" $p.CommandLine.Substring(0, [Math]::Min(120, $p.CommandLine.Length))
    }
    Write-Host ""
}

Write-Host "=== ALL WSCRIPT/CScript ==="
Get-CimInstance Win32_Process | Where-Object { $_.Name -like "wscript.exe" -or $_.Name -like "cscript.exe" } | Select-Object ProcessId, ParentProcessId, CommandLine
