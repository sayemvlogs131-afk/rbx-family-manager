"""Fallback aiohttp-compatible primitives for environments where the real
``aiohttp.web`` stack is unavailable.

The bot prefers the real ``aiohttp`` package. This shim only exists so the
health endpoint can still be mounted in stripped test environments where
aiohttp itself is not installed but the rest of the bot is.
"""

from __future__ import annotations

from typing import Any


class Application:
    """Minimal stand-in for ``aiohttp.web.Application``."""

    def __init__(self) -> None:
        self.router: Any = _Router()


class _Router:
    def add_get(self, path: str, handler: Any) -> None:
        pass  # pragma: no cover - not exercised in stripped envs


class AppRunner:
    """Minimal stand-in for ``aiohttp.web.AppRunner``."""

    def __init__(self, app: Application) -> None:
        self._app = app

    async def setup(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass


class TCPSite:
    """Minimal stand-in for ``aiohttp.web.TCPSite``."""

    def __init__(self, runner: AppRunner, host: str, port: int) -> None:
        self._runner = runner
        self._host = host
        self._port = port

    async def start(self) -> None:
        pass
