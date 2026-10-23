import sys
print('python', sys.version)
try:
    import aiohttp
    print('aiohttp', aiohttp.__version__)
except Exception as e:
    print('aiohttp MISSING:', e)
import os
print('PYTHONPATH', os.environ.get('PYTHONPATH'))
print('CWD', os.getcwd())
from config import HEALTH_PORT, HEALTH_ENABLED, DISCORD_TOKEN
print('HEALTH_PORT', HEALTH_PORT, 'HEALTH_ENABLED', HEALTH_ENABLED)
print('TOKEN len', len(DISCORD_TOKEN))
