$log = "D:\all in new\BOT\data\rbx_family.log"
if (Test-Path $log) {
    Get-Content $log -ErrorAction SilentlyContinue | Select-Object -Last 25
} else {
    Write-Host "No log file found."
}
