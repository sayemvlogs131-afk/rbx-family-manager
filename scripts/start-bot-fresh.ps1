$env:PYTHONPATH = "D:\all in new\BOT"
$cwd = "D:\all in new\BOT"
$python = "D:\all in new\BOT\.venv\Scripts\python.exe"
$outlog = "$cwd\log_bot.out"
$errlog = "$cwd\log_bot.err"

# Clear old logs
@"
"@ | Set-Content $outlog -Force
@"
"@ | Set-Content $errlog -Force

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $python
$psi.Arguments = "bot.py"
$psi.WorkingDirectory = $cwd
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
$proc.Start() | Out-Null

Write-Host "STARTED PID:" $proc.Id

Start-Sleep 16

# Read logs
Write-Host "=== STDOUT ==="
Get-Content $outlog -ErrorAction SilentlyContinue | Select-Object -First 25
Write-Host "=== STDERR ==="
Get-Content $errlog -ErrorAction SilentlyContinue | Select-Object -First 10

# Check health
try {
    $health = Invoke-WebRequest -Uri "http://localhost:8080/health" -TimeoutSec 5 -UseBasicParsing
    Write-Host "HEALTH:" $health.StatusCode $health.Content
} catch {
    Write-Host "HEALTH FAILED:" $_.Exception.Message
}

# Check admin page
try {
    $admin = Invoke-WebRequest -Uri "http://localhost:8080/" -TimeoutSec 5 -UseBasicParsing
    Write-Host "ADMIN PAGE:" $admin.StatusCode "len:" $admin.Content.Length
} catch {
    Write-Host "ADMIN FAILED:" $_.Exception.Message
}

Write-Host "PROCESS STILL RUNNING:" $proc.IsAlive
