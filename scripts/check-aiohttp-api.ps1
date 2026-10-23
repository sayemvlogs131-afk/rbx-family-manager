$env:PYTHONPATH = "D:\all in new\BOT"
$code = @"
from aiohttp import web
req_methods = [m for m in dir(web.Request) if not m.startswith('_')]
print('Request methods:', req_methods)
print()
print('Has respond:', hasattr(web.Request, 'respond'))
print('Has write:', hasattr(web.Request, 'write'))
print()

# Check web.Response
resp_methods = [m for m in dir(web.Response) if not m.startswith('_')]
print('Response methods:', resp_methods[:20])
"@
$code | & "D:\all in new\BOT\.venv\Scripts\python.exe" -c $code 2>&1 | Select-Object -First 30
