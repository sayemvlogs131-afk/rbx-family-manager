$ErrorActionPreference = "Stop"

$shell = New-Object -ComObject WScript.Shell
shortcutPath = Join-Path $HOME "Desktop\admin-app.lnk"

$link = $shell.CreateShortcut($shortcutPath)
$link.TargetPath = "rundll32.exe"
$link.Arguments = "url.dll,FileProtocolHandler http://localhost:8080/"
$link.Description = "RBX Family Manager admin app"
$link.Save()

Write-Output "Created: $shortcutPath"
