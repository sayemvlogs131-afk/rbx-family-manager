$log = "D:\all in new\BOT\data\rbx_family.log"
if (Test-Path $log) {
    $lines = Get-Content $log -ErrorAction SilentlyContinue
    $lines | Select-Object -Last 10
} else {
    Write-Host "No log file."
}
