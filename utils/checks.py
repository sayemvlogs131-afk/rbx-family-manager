"""Custom :mod:`discord.ext.commands` checks for RBX Family Manager.

Checks raise :class:`CheckFailure`; the global error handler in ``bot.py``
converts them into clean user-facing embeds (no stack traces).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

import discord
from discord.ext import commands

from utils import permissions as perms

log = logging.getLogger("rbx.checks")


class DisabledCommand(commands.CheckFailure):
    """Raised when a command is switched off for this guild."""


class MissingStaffRole(commands.CheckFailure):
    """Raised when a staff role is configured but the user lacks it."""


class MissingServerOwner(commands.CheckFailure):
    """Raised when a server-owner-only command is used by anyone else."""


class MissingOwnerPermissions(commands.CheckFailure):
    """Raised when a command needs the server owner *or* the bot owner."""


class MaintenanceMode(commands.CheckFailure):
    """Raised while the bot is operating in maintenance mode."""


def _db(bot: Any):
    return getattr(bot, "db", None)


async def _staff_role_id(bot: Any, guild_id: int) -> int | None:
    db = _db(bot)
    if db is None:
        return None
    try:
        settings = await db.get_guild(guild_id)
        return settings.get("staff_role_id")
    except Exception:  # pragma: no cover - defensive
        log.exception("Failed to load guild settings for checks")
        return None


async def _command_override(bot: Any, guild_id: int, name: str) -> dict[str, Any] | None:
    db = _db(bot)
    if db is None:
        return None
    try:
        return await db.get_command_config(guild_id, name)
    except Exception:  # pragma: no cover - defensive
        log.exception("Failed to load command config for %s", name)
        return None


def is_owner() -> Callable[[Any], Any]:
    """Only the configured OWNER_ID(s) may run a command."""

    async def predicate(ctx: commands.Context[Any]) -> bool:
        import config

        if ctx.author.id in config.OWNER_IDS:
            return True
        raise commands.NotOwner("This command is restricted to the bot owner.")

    return commands.check(predicate)


def guild_only() -> Callable[[Any], Any]:
    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("This command can only be used in a server.")
        return True

    return commands.check(predicate)


def require_permissions(*names: str, check_bot: bool = True) -> Callable[[Any], Any]:
    """Require the author (and optionally the bot) to hold ``names``."""

    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("This command can only be used in a server.")
        missing = perms.missing_permissions(ctx.author, *names)
        if missing:
            raise discord.MissingPermissions([n.lower().replace(" ", "_") for n in missing])
        if check_bot and ctx.me is not None:
            bot_missing = perms.missing_permissions(ctx.me, *names)
            if bot_missing:
                raise discord.BotMissingPermissions(
                    [n.lower().replace(" ", "_") for n in bot_missing]
                )
        return True

    return commands.check(predicate)


def require_moderator() -> Callable[[Any], Any]:
    """Administrator, moderator permissions, or the configured staff role."""

    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("This command can only be used in a server.")
        staff_role = await _staff_role_id(ctx.bot, ctx.guild.id)
        staff_ids: Iterable[int] = [staff_role] if staff_role else []
        if perms.is_moderator(ctx.author, staff_ids):
            return True
        raise MissingStaffRole(
            "You need moderator access (Administrator, moderation permissions, "
            "or the configured staff role)."
        )

    return commands.check(predicate)


def require_admin() -> Callable[[Any], Any]:
    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("This command can only be used in a server.")
        if perms.is_privileged(ctx.author):
            return True
        raise discord.MissingPermissions(["administrator"])

    return commands.check(predicate)


async def get_guild_owner_id(guild: Any) -> int | None:
    """The *actual* owner ID of a guild, verified against Discord.

    ``guild.owner_id`` comes from the gateway and is always current after an
    ownership transfer; when the guild is only partially cached (e.g. right
    after a restart) it may be ``None`` — then fall back to ``guild.owner``.
    Deliberately *not* trusting any configured ID or role: only Discord's own
    notion of ownership decides who wields these powers.
    """
    if guild is None:
        return None
    owner_id = getattr(guild, "owner_id", None)
    if owner_id is None:
        owner = getattr(guild, "owner", None)
        owner_id = getattr(owner, "id", None) if owner is not None else None
    return int(owner_id) if owner_id else None


def is_guild_owner(ctx_or_interaction: Any) -> bool:
    """True when the caller is the *actual* server owner (verified ID)."""
    guild = getattr(ctx_or_interaction, "guild", None)
    author = getattr(ctx_or_interaction, "author", None) or getattr(
        ctx_or_interaction, "user", None
    )
    if guild is None or author is None:
        return False
    owner_id = getattr(guild, "owner_id", None)
    if owner_id is None:
        owner = getattr(guild, "owner", None)
        owner_id = getattr(owner, "id", None) if owner is not None else None
    return owner_id is not None and int(owner_id) == int(author.id)


def is_bot_owner(ctx_or_interaction: Any) -> bool:
    """True when the caller is a configured bot owner (``OWNER_ID`` in .env)."""
    author = getattr(ctx_or_interaction, "author", None) or getattr(
        ctx_or_interaction, "user", None
    )
    if author is None:
        return False
    import config

    return author.id in getattr(config, "OWNER_IDS", ())


def require_guild_or_bot_owner() -> Callable[[Any], Any]:
    """Default-deny: only the *real* guild owner or a configured bot owner.

    Used for emergency powers (lockdowns). Nothing else passes — not admins,
    not staff roles, not ``Manage Server`` holders.
    """

    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage(
                "This command can only be used in a server."
            )
        if is_bot_owner(ctx) or is_guild_owner(ctx):
            return True
        raise MissingOwnerPermissions(
            "Only the **server owner** or the **bot owner** can use emergency "
            "powers."
        )

    return commands.check(predicate)


def require_server_owner() -> Callable[[Any], Any]:
    """Only the guild's actual owner may run a command.

    Uses ``guild.owner_id`` from Discord (not a cached member), so it stays
    correct after ownership transfers. The bot owner is *not* included —
    these powers belong to the server owner alone.
    """

    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("This command can only be used in a server.")
        owner_id = await get_guild_owner_id(ctx.guild)
        if owner_id and ctx.author.id == owner_id:
            return True
        raise MissingServerOwner(
            "Only the server owner can use these powers."
        )

    return commands.check(predicate)


def maintenance_mode() -> Any:
    """Global check: regular commands freeze during maintenance.

    While ``bot_status_mode`` is ``maintenance`` or ``issues``, only the bot
    owner and the *actual* server owner may run commands (so the outage can
    be handled/E2E-tested without members poking at a wounded bot).
    """

    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            return True
        db = _db(ctx.bot)
        if db is None:
            return True
        try:
            mode = await db.get_stat(0, "bot_status_mode", None)
        except Exception:  # pragma: no cover - a broken check must not block
            log.exception("Failed to read maintenance mode")
            mode = None
        if mode in (None, "", "online"):
            return True
        if is_bot_owner(ctx) or is_guild_owner(ctx):
            return True
        raise MaintenanceMode(
            f"The bot is currently marked as **{mode}** — commands are paused. "
            "The bot owner can lift this with `/botstatus set status:Online`."
        )

    return commands.check(predicate)


def not_ignored() -> Any:
    """Global check: commands are disabled inside ignored channels.

    Admins and the bot owner bypass the restriction so configuration is never
    locked out. Register once with ``bot.add_check(not_ignored().predicate)``.
    """

    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            return True
        db = _db(ctx.bot)
        if db is None:
            return True
        try:
            if perms.is_privileged(ctx.author):
                return True
            import config

            if ctx.author.id in config.OWNER_IDS:
                return True
            if ctx.channel.id in await db.ignored_channels(ctx.guild.id):
                raise DisabledCommand(
                    "Commands are disabled in this channel. "
                    "An administrator can re-enable them with `/ignore`."
                )
        except DisabledCommand:
            raise
        except Exception:  # pragma: no cover - a broken check must not block commands
            log.exception("Failed to evaluate ignored channels")
        return True

    return commands.check(predicate)


def command_enabled(name: str) -> Callable[[Any], Any]:
    """Honour per-guild ``/command disable`` and role restrictions."""

    async def predicate(ctx: commands.Context[Any]) -> bool:
        if ctx.guild is None:
            return True
        row = await _command_override(ctx.bot, ctx.guild.id, name)
        if row is None:
            return True
        if not int(row.get("enabled", 1)):
            raise DisabledCommand(f"`/{name}` is disabled in this server.")
        role_id = row.get("role_id")
        if role_id:
            member_role_ids = {r.id for r in getattr(ctx.author, "roles", [])}
            if int(role_id) not in member_role_ids and not perms.is_privileged(ctx.author):
                raise MissingStaffRole(
                    f"`/{name}` requires a specific role configured by administrators."
                )
        return True

    return commands.check(predicate)


__all__ = [
    "DisabledCommand",
    "MissingStaffRole",
    "MissingServerOwner",
    "MissingOwnerPermissions",
    "MaintenanceMode",
    "is_owner",
    "guild_only",
    "require_permissions",
    "require_moderator",
    "require_admin",
    "require_server_owner",
    "require_guild_or_bot_owner",
    "get_guild_owner_id",
    "is_guild_owner",
    "is_bot_owner",
    "maintenance_mode",
    "not_ignored",
    "command_enabled",
]
