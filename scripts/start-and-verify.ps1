# Kill all bot.py processes
Write-Host "=== KILLING EXISTING BOTS ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*bot.py*" } | ForEach-Object {
    Write-Host "Killing PID:" $_.ProcessId
    Stop-Process -Id $_.ProcessId -Force
}
Start-Sleep 2
Write-Host "Port 8080 clear:" ($(netstat -ano | findstr :8080) -eq $null)

# Start fresh bot
$env:PYTHONPATH = "D:\all in new\BOT"
$cwd = "D:\all in new\BOT"
$python = "D:\all in new\BOT\.venv\Scripts\python.exe"
$outlog = Join-Path $cwd "log_bot.out"
$errlog = Join-Path $cwd "log_bot.err"

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
$bottime = Get-Date

# Poll for /health and / to both work
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    Start-Sleep 3
    if ($proc.HasExited) {
        Write-Host "BOT EXITED. code:" $proc.ExitCode
        break
    }
    try {
        $h = [System.Net.WebRequest]::Create("http://localhost:8080/health")
        $h.Timeout = 3000
        $hr = $h.GetResponse()
        $hr.Close()
        if ($hr.StatusCode -eq 200) {
            # Also check admin page
            try {
                $a = [System.Net.WebRequest]::Create("http://localhost:8080/")
                $a.Timeout = 3000
                $ar = $a.GetResponse()
                Write-Host "ADMIN PAGE OK - STATUS:" $ar.StatusCode "LEN:" $ar.ContentLength
                $ar.Close()
                break
            } catch {
                # health works but admin not ready yet, keep waiting
            }
        }
    } catch {}
}

# Final status
Write-Host ""
Write-Host "=== FINAL STATUS ==="
Write-Host "Bot exited:" $proc.HasExited
if (-not $proc.HasExited) { Write-Host "Bot still running PID:" $proc.Id }

# Check endpoints
Write-Host "--- /health ---"
try {
    $h = [System.Net.WebRequest]::Create("http://localhost:8080/health")
    $h.Timeout = 5000
    $hr = $h.GetResponse()
    $reader = New-Object System.IO.StreamReader($hr.GetResponseStream())
    Write-Host "STATUS:" $hr.StatusCode
    Write-Host "BODY:" $reader.ReadToEnd()
    $hr.Close()
} catch { Write-Host "FAILED:" $_.Exception.Message }

Write-Host "--- / (admin) ---"
try {
    $a = [System.Net.WebRequest]::Create("http://localhost:8080/")
    $a.Timeout = 5000
    $ar = $a.GetResponse()
    Write-Host "STATUS:" $ar.StatusCode
    Write-Host "LEN:" $ar.ContentLength
    $reader = New-Object System.IO.StreamReader($ar.GetResponseStream())
    $body = $reader.ReadToEnd()
    $t = ($body | Select-String "<title>" | Select-Object -First 1)
    Write-Host "TITLE:" $t.ToString()
    Write-Host "SNIPPET:" $body.Substring(0, [Math]::Min(250, $body.Length))
    $ar.Close()
} catch { Write-Host "FAILED:" $_.Exception.Message }

Write-Host ""
Write-Host "=== BOT LOG (last 25) ==="
Get-Content $outlog -ErrorAction SilentlyContinue | Select-Object -Last 25
Write-Host "=== STDERR (last 10) ==="
Get-Content $errlog -ErrorAction SilentlyContinue | Select-Object -Last 10

Write-Host ""
Write-Host "=== ROUTES.PY LOG (last 20) ==="
if (Test-Path (Join-Path $cwd "data\rbx_family.log")) {
    Get-Content (Join-Path $cwd "data\rbx_family.log") -ErrorAction SilentlyContinue | Select-Object -Last 20
}
