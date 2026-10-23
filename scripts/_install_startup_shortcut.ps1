$startup = [Environment]::GetFolderPath('Startup')
$lnkPath = Join-Path $startup 'RBX Family Manager Bot.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnkPath)
$s.TargetPath = 'C:\Windows\System32\wscript.exe'
$s.Arguments = '"D:\all in new\BOT\scripts\auto-start.vbs"'
$s.WorkingDirectory = 'D:\all in new\BOT'
$s.Description = 'Starts the RBX Family Manager Discord bot hidden at logon'
$s.Save()

# Verify what got written
$check = (New-Object -ComObject WScript.Shell).CreateShortcut($lnkPath)
Write-Output "PATH=$lnkPath"
Write-Output "TARGET=$($check.TargetPath)"
Write-Output "ARGS=$($check.Arguments)"
