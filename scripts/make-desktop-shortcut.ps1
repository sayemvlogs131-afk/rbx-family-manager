$ErrorActionPreference = "Stop"

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "RBX Family Manager Admin.lnk"

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcutPath)
$link.TargetPath = "D:\all in new\BOT\admin-app-launcher.bat"
$link.WorkingDirectory = "D:\all in new\BOT"
$link.Description = "Open the RBX Family Manager admin app"
$link.Save()

Write-Output "Created: $shortcutPath"
