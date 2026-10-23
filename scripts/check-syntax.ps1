$env:PYTHONPATH = "D:\all in new\BOT"
$python = "D:\all in new\BOT\.venv\Scripts\python.exe"
$pyCode = 'import py_compile; py_compile.compile(r"D:\\all in new\\BOT\\webapp\\routes.py", doraise=True); print("SYNTAX OK")'
& $python -c $pyCode
