$env:PYTHONPATH = "D:\all in new\BOT"
$cwd = "D:\all in new\BOT"
$python = "D:\all in new\BOT\.venv\Scripts\python.exe"
$outlog = Join-Path $cwd "log_bot.out"
$errlog = Join-Path $cwd "log_bot.err"

# Fresh logs
Set-Content $outlog "" -Force
Set-Content $errlog "" -Force

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $python
$psi.Arguments = "bot.py"
$psi.WorkingDirectory = $cwd
$psi.EnvironmentVariables["PYTHONPATH"] = "D:\all in new\BOT"
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$proc.Start() | Out-Null
Write-Host "BOT PID:" $proc.Id

# Poll for readiness
$deadline = (Get-Date).AddSeconds(90)
$ready = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep 3
    if (-not $proc.HasExited) {
        try {
            $h = Invoke-WebRequest -Uri "http://localhost:8080/health" -TimeoutSec 3 -UseBasicParsing
            if ($h.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {}
    } else {
        Write-Host "BOT EXITED EARLY. stdout:"; Get-Content $outlog | Select-Object -Last 5
        Write-Host "stderr:"; Get-Content $errlog | Select-Object -Last 5
        exit 1
    }
}

if (-not $ready) {
    Write-Host "BOT DID NOT BECOME READY IN 90s"
    Write-Host "stdout:"; Get-Content $outlog | Select-Object -Last 20
    Write-Host "stderr:"; Get-Content $errlog | Select-Object -Last 10
    exit 1
}

Write-Host "=== BOT LOG (last 20) ==="
Get-Content $outlog | Select-Object -Last 20
Write-Host "=== STDERR ==="
Get-Content $errlog | Select-Object -Last 10

# Admin page
try {
    $admin = Invoke-WebRequest -Uri "http://localhost:8080/" -TimeoutSec 5 -UseBasicParsing
    Write-Host "ADMIN PAGE STATUS:" $admin.StatusCode "LENGTH:" $admin.Content.Length
    Write-Host "TITLE:" ($admin.Content | Select-String "<title>" | Select-Object -First 1).ToString()
    if ($admin.Content.Length -gt 100) {
        Write-Host "SNIPPET:" $admin.Content.Substring(0, 200)
    }
} catch {
    Write-Host "ADMIN PAGE FAILED:" $_.Exception.Message
}

Write-Host "BOT STILL RUNNING:" $proc.HasExited
