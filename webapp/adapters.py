"""Tiny ASGI-to-aiohttp adapters used when the admin dashboard is mounted onto
the bot's existing aiohttp health site.

These are intentionally minimal: FastAPI writes response headers and body
through the ASGI ``send`` channel, and we translate that back to aiohttp's
request/response objects we already have in scope.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web


def receive_for_aiohttp(request: web.Request) -> Any:
    """Return an ASGI ``receive`` callable that drains the aiohttp request body."""
    async def receive() -> dict:
        body = await request.read()
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    return receive


def asgi_headers_to_dict(header_list: list) -> dict:
    """Decode an ASGI header list (``[(bytes, bytes), ...]``) to a dict."""
    return {
        (k.decode("latin1") if isinstance(k, bytes) else k):
        (v.decode("latin1") if isinstance(v, bytes) else v)
        for k, v in header_list
    }


async def fastapi_to_aiohttp(request: web.Request, fastapp: Any) -> web.Response:
    """Run a FastAPI/FastAPI-like ASGI app for one aiohttp request and
    return the resulting ``web.Response``."""
    scope = {
        "type": "http",
        "method": request.method,
        "path": request.path,
        "query_string": request.query_string,
        "headers": [(k.lower(), v) for k, v in request.headers.items()],
        "server": request.host.split(":") if request.host else ("localhost", 80),
        "scheme": "http",
        "root_path": "",
        "app": request.app,
        "aiohttp.request": request,
    }
    receive = receive_for_aiohttp(request)

    status_code = 200
    response_headers: dict = {}
    body_bytes: bytes | None = None

    async def send(msg: dict) -> None:
        nonlocal status_code, response_headers, body_bytes
        if msg["type"] == "http.response.start":
            # Remember status and headers so the final response carries them.
            status_code = int(msg.get("status", 200))
            response_headers = asgi_headers_to_dict(msg.get("headers", []))
        elif msg["type"] == "http.response.body":
            body = msg.get("body", b"")
            if isinstance(body, str):
                body = body.encode("utf-8")
            body_bytes = body

    await fastapp(scope, receive, send)
    if body_bytes is None:
        # The ASGI app never produced a body (e.g. connection closed early).
        if status_code != 200:
            body_bytes = b""
        else:
            return web.Response(status=404)
    return web.Response(
        body=body_bytes,
        status=status_code,
        headers=response_headers,
    )
