"""Configuration validation and health-endpoint tests."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web

import config


# ----------------------------------------------------------------- config
def test_env_example_documents_required_keys() -> None:
    text = open(".env.example", encoding="utf-8").read()
    for key in ("DISCORD_TOKEN=", "OWNER_ID=", "DATABASE_PATH="):
        assert key in text, f".env.example must document {key}"
    assert "DEV_GUILD_ID=" in text


def test_gitignore_hides_env() -> None:
    text = open(".gitignore", encoding="utf-8").read()
    assert "\n.env\n" in text or text.startswith(".env")
    assert "!/.env.example" in text or "!.env.example" in text


def test_validate_reports_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DISCORD_TOKEN", "")
    monkeypatch.setattr(config, "OWNER_ID", None)
    monkeypatch.setattr(config, "OWNER_IDS", ())
    problems = config.validate()
    assert any("DISCORD_TOKEN" in p for p in problems)
    assert any("OWNER_ID" in p for p in problems)


def test_validate_accepts_good_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DISCORD_TOKEN", "x" * 60)
    monkeypatch.setattr(config, "OWNER_ID", 123)
    monkeypatch.setattr(config, "OWNER_IDS", (123,))
    monkeypatch.setattr(config, "HEALTH_PORT", 8080)
    assert config.validate() == []


def test_branding_is_rbx_family(monkeypatch: pytest.MonkeyPatch) -> None:
    assert config.BOT_NAME == "RBX Family Manager"
    assert config.BOT_DESCRIPTION.startswith("The all-in-one management")
    assert config.DEFAULT_STATUS_TEXT == "RBX Family | /help"
    assert "RBX Family" in config.FOOTER_TEXT
    # The project must never use any other brand name (built dynamically so
    # this test file does not match itself).
    import pathlib

    needle = "Sp" + "arky"
    roots = ["bot.py", "config.py", "cogs", "utils", "database", "deployment", "README.md"]
    files: list[pathlib.Path] = []
    for root in roots:
        path = pathlib.Path(root)
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*"))
    for path in files:
        if not path.is_file() or path.suffix not in {".py", ".md", ".sh", ".service", ".sql"}:
            continue
        if "__pycache__" in str(path):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        assert needle not in source, f"{path} contains a foreign brand name"


# ---------------------------------------------------------- health endpoint
@pytest.mark.parametrize("expected_status", ["online"])
def test_health_endpoint(expected_status: str) -> None:
    from bot import RBXFamilyManager

    async def scenario() -> dict:
        bot = RBXFamilyManager()
        app = web.Application()
        app.router.add_get("/health", bot._health_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]

        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/health") as resp:
                assert resp.status == 200
                payload = await resp.json()
        await runner.cleanup()
        return payload

    payload = asyncio.run(scenario())
    assert payload["status"] == expected_status
    assert payload["bot"] == "RBX Family Manager"
    assert isinstance(payload["guilds"], int)
    assert isinstance(payload["latency_ms"], int)
    # Must be valid, compact JSON.
    assert json.loads(json.dumps(payload)) == payload
