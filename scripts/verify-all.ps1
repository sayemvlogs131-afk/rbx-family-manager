Write-Host "=== /health ==="
try {
    $h = [System.Net.WebRequest]::Create("http://localhost:8080/health")
    $h.Timeout = 5000
    $hr = $h.GetResponse()
    $r = New-Object System.IO.StreamReader($hr.GetResponseStream())
    Write-Host "STATUS:" $hr.StatusCode
    Write-Host "BODY:" $r.ReadToEnd()
    $hr.Close()
} catch { Write-Host "FAILED:" $_.Exception.Message }

Write-Host ""
Write-Host "=== / (admin dashboard) ==="
try {
    $a = [System.Net.WebRequest]::Create("http://localhost:8080/")
    $a.Timeout = 5000
    $ar = $a.GetResponse()
    Write-Host "STATUS:" $ar.StatusCode
    Write-Host "LEN:" $ar.ContentLength
    $r = New-Object System.IO.StreamReader($ar.GetResponseStream())
    $body = $r.ReadToEnd()
    $t = ($body | Select-String "<title>" | Select-Object -First 1)
    Write-Host "TITLE:" $t.ToString()
    $ar.Close()
} catch { Write-Host "FAILED:" $_.Exception.Message }

Write-Host ""
Write-Host "=== /api/servers ==="
try {
    $s = [System.Net.WebRequest]::Create("http://localhost:8080/api/servers")
    $s.Timeout = 5000
    $sr = $s.GetResponse()
    $r = New-Object System.IO.StreamReader($sr.GetResponseStream())
    Write-Host "STATUS:" $sr.StatusCode
    Write-Host "BODY:" $r.ReadToEnd()
    $sr.Close()
} catch { Write-Host "FAILED:" $_.Exception.Message }

Write-Host ""
Write-Host "=== BOT PROCESS ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | Select-Object ProcessId, CommandLine, CreationDate
