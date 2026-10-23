' Runs a program with NO visible window and waits for it to exit.
' Usage: wscript run-hidden-wait.vbs "C:\path\python.exe" "C:\path\bot.py"
Set objShell = CreateObject("WScript.Shell")
If WScript.Arguments.Count < 2 Then WScript.Quit 1
cmd = """" & WScript.Arguments(0) & """ """ & WScript.Arguments(1) & """"
' 0 = hidden window, True = wait for the program to exit
objShell.Run cmd, 0, True
