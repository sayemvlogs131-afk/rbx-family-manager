$venvPy = "D:\all in new\BOT\.venv\Scripts\python.exe"
$pip = "D:\all in new\BOT\.venv\Scripts\pip.exe"
Write-Host "Installing requirements-web.txt..."
& $pip install -r "D:\all in new\BOT\requirements-web.txt" 2>&1
Write-Host "EXIT CODE:" $LASTEXITCODE
