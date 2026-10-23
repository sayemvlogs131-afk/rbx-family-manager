$log = "D:\all in new\BOT\data\rbx_family.log"
if (Test-Path $log) {
    Get-Content $log -ErrorAction SilentlyContinue
} else {
    Write-Host "No log file."
}
