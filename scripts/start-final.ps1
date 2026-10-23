$env:PYTHONPATH = "D:\all in new\BOT"
$cwd = "D:\all in new\BOT"
$python = "D:\all in new\BOT\.venv\Scripts\python.exe"

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $python
$psi.Arguments = "bot.py"
$psi.WorkingDirectory = $cwd
$psi.EnvironmentVariables["PYTHONPATH"] = "D:\all in new\BOT"
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$proc = [System.Diagnostics.Process]::Start($psi)
Write-Host "Started bot PID:" $proc.Id

# Wait up to 90s for both endpoints
$healthOk = $false
$adminOk = $false
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    Start-Sleep 2
    if ($proc.HasExited) { break }
    try {
        $h = [System.Net.WebRequest]::Create("http://localhost:8080/health")
        $h.Timeout = 3000
        $hr = $h.GetResponse()
        $hr.Close()
        if ($hr.StatusCode -eq 200) { $healthOk = $true }
    } catch {}
    try {
        $a = [System.Net.WebRequest]::Create("http://localhost:8080/")
        $a.Timeout = 3000
        $ar = $a.GetResponse()
        $ar.Close()
        if ($ar.StatusCode -eq 200) { $adminOk = $true; break }
    } catch {}
}

Write-Host "Health OK:" $healthOk "Admin OK:" $adminOk
if (-not $proc.HasExited) { Write-Host "Bot running PID:" $proc.Id }

# Final verification
Write-Host ""
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
    Write-Host "SNIPPET:" $body.Substring(0, [Math]::Min(200, $body.Length))
    $ar.Close()
} catch { Write-Host "FAILED:" $_.Exception.Message }
