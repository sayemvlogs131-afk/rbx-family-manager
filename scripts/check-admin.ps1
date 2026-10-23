$line = netstat -ano | findstr :8080 | Select-Object -First 1
if (-not $line) { Write-Host "NOTHING on 8080"; exit }
$pid = ($line.Split())[5]
Write-Host "PID on 8080:" $pid
Write-Host "=== PROCESS ==="
$p = Get-Process -Id $pid -ErrorAction SilentlyContinue
if ($p) { $p | Select-Object Id, ProcessName, Path, StartTime }
Write-Host "=== CHILDREN ==="
Get-CimInstance Win32_Process -Filter "ParentProcessId=$pid" | Select-Object ProcessId, Name, CommandLine

Write-Host "=== HEALTH CHECK ==="
try {
    $hc = [System.Net.WebRequest]::Create("http://localhost:8080/health")
    $hc.Timeout = 5000
    $resp = $hc.GetResponse()
    $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
    $body = $reader.ReadToEnd()
    Write-Host "HEALTH STATUS:" $resp.StatusCode
    Write-Host "HEALTH BODY:" $body
    $resp.Close()
} catch {
    Write-Host "HEALTH FAILED:" $_.Exception.Message
}

Write-Host "=== ADMIN PAGE ==="
try {
    $ar = [System.Net.WebRequest]::Create("http://localhost:8080/")
    $ar.Timeout = 5000
    $resp = $ar.GetResponse()
    Write-Host "ADMIN STATUS:" $resp.StatusCode
    Write-Host "ADMIN LEN:" $resp.ContentLength
    $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
    $body = $reader.ReadToEnd()
    $title = ($body | Select-String "<title>" | Select-Object -First 1)
    Write-Host "TITLE:" $title.ToString()
    if ($body.Length -gt 0) {
        Write-Host "FIRST 300 CHARS:" $body.Substring(0, [Math]::Min(300, $body.Length))
    }
    $resp.Close()
} catch {
    Write-Host "ADMIN FAILED:" $_.Exception.Message
}
