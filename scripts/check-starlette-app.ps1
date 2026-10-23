$env:PYTHONPATH = "D:\all in new\BOT"
$code = @"
from fastapi import FastAPI
app = FastAPI()
print('Has get:', hasattr(app, 'get'))
print('Has state:', hasattr(app, 'state'))
print('App type:', type(app))
print('MRO:', [c.__name__ for c in type(app).__mro__])
"@
$code | & "D:\all in new\BOT\.venv\Scripts\python.exe" -c $code 2>&1
