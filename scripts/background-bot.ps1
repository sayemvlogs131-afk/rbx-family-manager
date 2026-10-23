$env:PYTHONPATH = "D:\all in new\BOT"
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "D:\all in new\BOT\.venv\Scripts\python.exe"
$psi.Arguments = "bot.py"
$psi.WorkingDirectory = "D:\all in new\BOT"
$psi.EnvironmentVariables["PYTHONPATH"] = "D:\all in new\BOT"
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$proc = [System.Diagnostics.Process]::Start($psi)
Write-Host "Started bot PID:" $proc.Id
