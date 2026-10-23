"""Security-cog unit tests: changelog parser, security DB layer, owner checks."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

import cogs.security as security
from database.database import Database
from utils.checks import MissingOwnerPermissions, require_guild_or_bot_owner


# ---------------------------------------------------------------- changelog
def test_parse_updates_reads_newest_release(tmp_path: Path) -> None:
    doc = textwrap.dedent(
        """
        # v2.0.0 — Security hardening
        - 🟢 Added: new feature
        - 🛠️ Fixed: bug gone
        - 🔒 Security: harder, better
        - 🔴 Critical Issue: none
        random prose that is not a bullet

        # v1.0.0 — old
        - ⚡ Improved: nothing to see here
        """
    )
    path = tmp_path / "UPDATES.md"
    path.write_text(doc, encoding="utf-8")
    release = security.parse_updates(path)
    assert release.version == "2.0.0"
    assert "Security hardening" in release.title
    assert any("new feature" in i for i in release.sections.get("🟢 Added", []))
    assert any("harder" in i for i in release.sections.get("🔒 Security", []))
    assert any("bug gone" in i for i in release.sections.get("🛠️ Fixed", []))
    assert any("none" in i for i in release.sections.get("🔴 Critical Issue", []))
    # stray prose must not become a bullet
    assert not any("random prose" in item for items in release.sections.values() for item in items)
    assert release.latest_summary().startswith("**v2.0.0")


def test_parse_updates_missing_file(tmp_path: Path) -> None:
    release = security.parse_updates(tmp_path / "nope.md")
    assert release.version == ""


def test_build_update_embed_fields() -> None:
    release = security.Release(version="9.9.9", title="Testing", sections={"🟢 Added": ["thing"]})
    embed = security.build_update_embed(release)
    assert embed.title == "🚀 Bot Update — v9.9.9"
    names = [f.name for f in embed.fields]
    assert "🟢 Added" in names and "Version" in names


# ------------------------------------------------------------ security DB row
def test_security_config_roundtrip(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "sec.db")
        await db.connect()
        try:
            settings = await db.get_security(42)
            assert settings["anti_nuke_enabled"] == 1
            assert settings["action"] == "timeout"

            await db.update_security(
                42, action="kick", ban_limit=3, log_channel=999, NOT_A_COLUMN="x"
            )
            settings = await db.get_security(42)
            assert settings["action"] == "kick"
            assert settings["ban_limit"] == 3
            assert settings["log_channel"] == 999
            assert "NOT_A_COLUMN" not in settings  # whitelist enforced
        finally:
            await db.close()

    asyncio.run(scenario())


def test_security_events_roundtrip(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database(tmp_path / "events.db")
        await db.connect()
        try:
            db_id = await db.log_security_event(
                7, "ban_breach", actor_id=10, target_id=20, details="boom", severity="critical"
            )
            assert db_id > 0
            rows = await db.recent_security_events(7, limit=5)
            assert rows and rows[0]["event"] == "ban_breach"
            assert rows[0]["severity"] == "critical"
        finally:
            await db.close()

    asyncio.run(scenario())


# -------------------------------------------------------------- owner gates
def _ctx(author_id: int, owner_id: int | None, perms_=None):
    base = {"administrator": False, "manage_guild": False}
    base.update(perms_ or {})
    return SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=owner_id),
        author=SimpleNamespace(id=author_id, roles=[], guild_permissions=SimpleNamespace(**base)),
        bot=SimpleNamespace(db=None),
    )


def test_emergency_gate_denies_admin() -> None:
    check = require_guild_or_bot_owner()

    async def scenario() -> None:
        with pytest.raises(MissingOwnerPermissions):
            await check.predicate(_ctx(111, owner_id=222, perms_={"administrator": True}))

    asyncio.run(scenario())


def test_emergency_gate_allows_real_server_owner() -> None:
    check = require_guild_or_bot_owner()

    async def scenario() -> None:
        assert await check.predicate(_ctx(222, owner_id=222, perms_={"administrator": False})) is True

    asyncio.run(scenario())


def test_emergency_gate_allows_bot_owner(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "OWNER_IDS", (333,))
    check = require_guild_or_bot_owner()

    async def scenario() -> None:
        assert await check.predicate(_ctx(333, owner_id=444)) is True

    asyncio.run(scenario())
