' RBX Family Manager - Windows logon entry point.
' A shortcut to this file lives in the Startup folder. It derives the bot
' folder from its own location, so moving the project folder keeps working
' as long as the shortcut is regenerated.
' Launches the restart loop with no visible window.
Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' ...BOT\scripts\auto-start.vbs  ->  bot root is the parent of "scripts"
scriptsDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
root = objFSO.GetParentFolderName(scriptsDir)

bat = root & "\scripts\run-hidden-restart.bat"
If Not objFSO.FileExists(bat) Then WScript.Quit 1

' 0 = hidden window, False = let the loop run in the background
objShell.Run """" & bat & """", 0, False
