"""Command-check tests using stub contexts and a real temporary database."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from discord.ext import commands

from database.database import Database
from utils.checks import (
    DisabledCommand,
    MissingServerOwner,
    MissingStaffRole,
    command_enabled,
    not_ignored,
    require_moderator,
    require_server_owner,
)


def make_ctx(db: Database, *, channel_id: int = 1, author_id: int = 10, roles=(), perms_=None):
    base = {"administrator": False, "manage_guild": False}
    base.update(perms_ or {})
    author = SimpleNamespace(
        id=author_id,
        roles=[SimpleNamespace(id=r) for r in roles],
        guild_permissions=SimpleNamespace(**base),
    )
    bot = SimpleNamespace(db=db)
    return SimpleNamespace(guild=SimpleNamespace(id=1), channel=SimpleNamespace(id=channel_id),
                           author=author, bot=bot)


def test_global_ignore_check_is_registered(tmp_path: Path, monkeypatch) -> None:
    from bot import RBXFamilyManager
    import config as config_module

    async def scenario():
        bot = RBXFamilyManager()
        bot.db = Database(tmp_path / "setup.db")
        await bot._async_setup_hook()
        # setup_hook registers the global check; skip network side effects.
        monkeypatch.setattr(config_module, "SYNC_COMMANDS", False)
        monkeypatch.setattr(config_module, "HEALTH_ENABLED", False)
        await bot.setup_hook()
        # Two global checks are registered since v2.0.0: the ignored-channel
        # gate AND the maintenance-mode gate (new feature, user-requested).
        assert len(bot._checks) == 2, "ignored-channel + maintenance gates must both be registered"
        await bot.db.close()

    asyncio.run(scenario())


def test_ignored_channel_blocks_normal_members(tmp_path: Path) -> None:
    async def scenario():
        db = Database(tmp_path / "checks.db")
        await db.connect()
        await db.add_ignore("ignored_channels", 1, 777)
        predicate = not_ignored().predicate

        blocked = make_ctx(db, channel_id=777, author_id=10)
        with pytest.raises(DisabledCommand):
            await predicate(blocked)

        allowed = make_ctx(db, channel_id=1, author_id=10)
        assert await predicate(allowed) is True
        await db.close()

    asyncio.run(scenario())


def test_admins_bypass_ignored_channels(tmp_path: Path, monkeypatch) -> None:
    async def scenario():
        db = Database(tmp_path / "checks2.db")
        await db.connect()
        await db.add_ignore("ignored_channels", 1, 777)
        predicate = not_ignored().predicate

        admin = make_ctx(db, channel_id=777, author_id=10, perms_={"administrator": True})
        assert await predicate(admin) is True

        import config

        monkeypatch.setattr(config, "OWNER_IDS", (999,))
        owner = make_ctx(db, channel_id=777, author_id=999)
        assert await predicate(owner) is True
        await db.close()

    asyncio.run(scenario())


def test_command_disable_and_role_restriction(tmp_path: Path) -> None:
    async def scenario():
        db = Database(tmp_path / "cmdcheck.db")
        await db.connect()
        check = command_enabled("ban")

        # No override -> allowed.
        ctx = make_ctx(db)
        assert await check.predicate(ctx) is True

        # Disabled for everyone -> denied.
        await db.set_command_config(1, "ban", enabled=False)
        with pytest.raises(DisabledCommand):
            await check.predicate(make_ctx(db))

        # Re-enabled but role-gated -> denied without the role, allowed with it.
        await db.set_command_config(1, "ban", enabled=True, role_id=555)
        with pytest.raises(MissingStaffRole):
            await check.predicate(make_ctx(db, roles=[111]))
        assert await check.predicate(make_ctx(db, roles=[555])) is True

        # Admins bypass the role gate.
        assert (
            await check.predicate(make_ctx(db, roles=[], perms_={"administrator": True}))
            is True
        )
        await db.close()

    asyncio.run(scenario())


def test_require_moderator_by_role_or_permission(tmp_path: Path) -> None:
    async def scenario():
        db = Database(tmp_path / "staff.db")
        await db.connect()
        await db.update_guild(1, staff_role_id=4242)
        check = require_moderator()

        with pytest.raises(MissingStaffRole):
            await check.predicate(make_ctx(db, roles=[1], perms_={}))

        assert await check.predicate(make_ctx(db, roles=[4242], perms_={})) is True
        assert await check.predicate(make_ctx(db, roles=[1], perms_={"ban_members": True})) is True
        assert (
            await check.predicate(make_ctx(db, roles=[1], perms_={"manage_guild": True})) is True
        )
        await db.close()

    asyncio.run(scenario())


def test_check_decorators_attach_predicates() -> None:
    from utils.checks import require_permissions

    decorator = require_permissions("ban_members")
    assert hasattr(decorator, "predicate")
    assert callable(decorator.predicate)
    assert hasattr(not_ignored(), "predicate")


def test_require_server_owner_only_guild_owner() -> None:
    """Server-owner powers must accept the real owner and nobody else."""

    def ctx_for(owner_id, author_id, guild_id=1):
        guild = SimpleNamespace(id=guild_id, owner_id=owner_id) if guild_id else None
        return SimpleNamespace(
            guild=guild, author=SimpleNamespace(id=author_id)
        )

    check = require_server_owner()

    # Owner passes (async predicate).
    assert asyncio.run(check.predicate(ctx_for(555, 555))) is True

    # A different member — even an admin — is rejected.
    with pytest.raises(MissingServerOwner):
        asyncio.run(check.predicate(ctx_for(555, 999)))

    # Ownership transfer: new owner id wins over old holder.
    assert asyncio.run(check.predicate(ctx_for(111, 111))) is True
    with pytest.raises(MissingServerOwner):
        asyncio.run(check.predicate(ctx_for(111, 555)))

    # DMs are not guilds.
    with pytest.raises(commands.NoPrivateMessage):
        asyncio.run(check.predicate(ctx_for(555, 555, guild_id=None)))
