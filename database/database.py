"""Async SQLite access layer for RBX Family Manager.

A single :class:`Database` instance is shared by every cog. All methods are
coroutine-safe: the underlying :mod:`aiosqlite` connection serialises writes on
its own event loop and WAL mode keeps readers fast.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

from database.migrations import migrate

log = logging.getLogger("rbx.database")

DEFAULT_GUILD_ROW: dict[str, Any] = {
    "guild_id": 0,
    "prefix": "!",
    "staff_role_id": None,
    "mod_log_channel": None,
    "member_log_channel": None,
    "server_log_channel": None,
    "message_log_channel": None,
    "welcome_channel": None,
    "welcome_message": None,
    "welcome_embed": 1,
    "dm_welcome": 0,
    "autorole_id": None,
    "ticket_category_id": None,
    "ticket_staff_role_id": None,
    "ticket_counter": 0,
    "autoreply_enabled": 1,
    "giveaway_react_join": 1,
}

DEFAULT_AUTOMOD_ROW: dict[str, Any] = {
    "guild_id": 0,
    "enabled": 1,
    "anti_spam": 1,
    "anti_spam_messages": 6,
    "anti_spam_window": 5,
    "duplicate_filter": 1,
    "duplicate_window": 7,
    "mention_limit": 6,
    "emoji_limit": 12,
    "caps_limit": 70,
    "caps_min_length": 12,
    "invite_filter": 1,
    "link_filter": 0,
    "banned_words": "[]",
    "warn_threshold": 3,
    "timeout_duration": 300,
    "delete_violations": 1,
    "log_violations": 1,
    "exempt_roles": "[]",
    "exempt_channels": "[]",
}


DEFAULT_SECURITY_ROW: dict[str, Any] = {
    "guild_id": 0,
    "anti_nuke_enabled": 1,
    "ban_limit": 5,
    "kick_limit": 5,
    "channel_limit": 5,
    "role_limit": 5,
    "window_seconds": 60,
    "join_limit": 8,
    "join_window_seconds": 30,
    "raid_action": "alert",
    "min_account_age_days": 3,
    "action": "timeout",
    "lockdown_on_nuke": 0,
    "trusted_role_ids": "[]",
    "trusted_user_ids": "[]",
    "log_channel": None,
}

# Columns scripts may write through update_security (whitelist = no SQL-injection
# surface from configuration keys).
SECURITY_COLUMNS = frozenset(DEFAULT_SECURITY_ROW) - {"guild_id"}


def utcnow() -> str:
    """ISO-8601 UTC timestamp used for every ``created_at`` column."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    """Thin async wrapper around a single SQLite connection."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._db: aiosqlite.Connection | None = None

    # --------------------------------------------------------------- security
    async def get_security(self, guild_id: int) -> dict[str, Any]:
        """Anti-nuke configuration for one guild, merged onto defaults."""
        row = await self.fetchone(
            "SELECT * FROM security_config WHERE guild_id = ?", (guild_id,)
        )
        if row is None:
            return {**DEFAULT_SECURITY_ROW, "guild_id": guild_id}
        return row

    async def update_security(self, guild_id: int, **fields: Any) -> None:
        """Write whitelisted security columns; creates the row if missing."""
        allowed = {k: v for k, v in fields.items() if k in SECURITY_COLUMNS}
        if not allowed:
            return
        await self.execute(
            "INSERT OR IGNORE INTO security_config(guild_id) VALUES (?)", (guild_id,)
        )
        cols = ", ".join(f"{k} = ?" for k in allowed)
        values = list(allowed.values()) + [utcnow(), guild_id]
        await self.execute(
            f"UPDATE security_config SET {cols}, updated_at = ? WHERE guild_id = ?",  # noqa: S608
            values,
        )

    async def log_security_event(
        self,
        guild_id: int,
        event: str,
        *,
        actor_id: int | None = None,
        target_id: int | None = None,
        details: str | None = None,
        severity: str = "info",
    ) -> int:
        """Append one entry to the sensitive-action audit trail."""
        cur = await self.execute(
            "INSERT INTO security_events(guild_id, event, actor_id, target_id, details, "
            "severity, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, event, actor_id, target_id, details, severity, utcnow()),
        )
        return int(cur.lastrowid or 0)

    async def recent_security_events(
        self, guild_id: int, limit: int = 25
    ) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM security_events WHERE guild_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (guild_id, limit),
        )

    # ------------------------------------------------------------------ core
    @property
    def connected(self) -> bool:
        """False after :meth:`close` (or before :meth:`connect`) — lets background
        tasks bail out instead of crash-looping during shutdown."""
        return self._db is not None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Database is not connected. Call await db.connect() first.")
        return self._db

    async def connect(self) -> None:
        """Open the database, enable WAL, and run migrations."""
        if self._db is not None:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA foreign_keys=ON;")
        await self._db.execute("PRAGMA busy_timeout=5000;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")
        await self._db.commit()
        await migrate(self._db)
        log.info("Database ready at %s", self.path)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            log.info("Database connection closed.")

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Cursor:
        cur = await self.connection.execute(sql, params)
        await self.connection.commit()
        return cur

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> aiosqlite.Cursor:
        cur = await self.connection.executemany(sql, seq)
        await self.connection.commit()
        return cur

    async def fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        async with await self.connection.execute(sql, params) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        async with await self.connection.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
        async with await self.connection.execute(sql, params) as cur:
            row = await cur.fetchone()
        if row is None:
            return default
        return row[0]

    # -------------------------------------------------------- guild settings
    async def get_guild(self, guild_id: int) -> dict[str, Any]:
        row = await self.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        if row is None:
            await self.execute(
                "INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)", (guild_id,)
            )
            return {**DEFAULT_GUILD_ROW, "guild_id": guild_id}
        return row

    async def update_guild(self, guild_id: int, **fields: Any) -> None:
        if not fields:
            return
        await self.get_guild(guild_id)  # ensure row exists
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [utcnow(), guild_id]
        await self.execute(
            f"UPDATE guild_settings SET {cols}, updated_at = ? WHERE guild_id = ?",  # noqa: S608
            values,
        )

    async def all_guild_ids(self) -> list[int]:
        rows = await self.fetchall("SELECT guild_id FROM guild_settings")
        return [int(r["guild_id"]) for r in rows]

    # ---------------------------------------------------------- automod
    async def get_automod(self, guild_id: int) -> dict[str, Any]:
        row = await self.fetchone("SELECT * FROM automod_config WHERE guild_id = ?", (guild_id,))
        if row is None:
            return {**DEFAULT_AUTOMOD_ROW, "guild_id": guild_id}
        return row

    async def update_automod(self, guild_id: int, **fields: Any) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO automod_config(guild_id) VALUES (?)", (guild_id,)
        )
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [utcnow(), guild_id]
        await self.execute(
            f"UPDATE automod_config SET {cols}, updated_at = ? WHERE guild_id = ?",  # noqa: S608
            values,
        )

    # ---------------------------------------------------------- warnings
    async def add_warning(
        self, guild_id: int, user_id: int, moderator_id: int, reason: str
    ) -> int:
        cur = await self.execute(
            "INSERT INTO warnings(guild_id, user_id, moderator_id, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, reason, utcnow()),
        )
        return int(cur.lastrowid or 0)

    async def get_warnings(self, guild_id: int, user_id: int) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY id DESC",
            (guild_id, user_id),
        )

    async def warning_count(self, guild_id: int, user_id: int) -> int:
        return int(
            await self.scalar(
                "SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
        )

    async def remove_warning(self, guild_id: int, warning_id: int) -> bool:
        cur = await self.execute(
            "DELETE FROM warnings WHERE id = ? AND guild_id = ?", (warning_id, guild_id)
        )
        return cur.rowcount > 0

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        cur = await self.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        return cur.rowcount

    # -------------------------------------------------- moderation actions
    async def log_moderation(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        action: str,
        reason: str,
        duration: str | None = None,
    ) -> int:
        cur = await self.execute(
            "INSERT INTO moderation_actions"
            "(guild_id, user_id, moderator_id, action, reason, duration, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, action, reason, duration, utcnow()),
        )
        return int(cur.lastrowid or 0)

    async def recent_actions(
        self, guild_id: int, user_id: int | None = None, limit: int = 25
    ) -> list[dict[str, Any]]:
        if user_id is None:
            return await self.fetchall(
                "SELECT * FROM moderation_actions WHERE guild_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (guild_id, limit),
            )
        return await self.fetchall(
            "SELECT * FROM moderation_actions WHERE guild_id = ? AND user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (guild_id, user_id, limit),
        )

    # ---------------------------------------------------------- giveaways
    async def create_giveaway(
        self,
        guild_id: int,
        channel_id: int,
        host_id: int,
        prize: str,
        ends_at: int,
        winners_count: int = 1,
        description: str | None = None,
        required_role_id: int | None = None,
        min_account_age: int = 0,
        min_server_age: int = 0,
    ) -> int:
        cur = await self.execute(
            "INSERT INTO giveaways"
            "(guild_id, channel_id, host_id, prize, description, winners_count, ends_at,"
            " required_role_id, min_account_age, min_server_age, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                guild_id,
                channel_id,
                host_id,
                prize,
                description,
                winners_count,
                ends_at,
                required_role_id,
                min_account_age,
                min_server_age,
                utcnow(),
            ),
        )
        return int(cur.lastrowid or 0)

    async def set_giveaway_message(self, giveaway_id: int, message_id: int) -> None:
        await self.execute(
            "UPDATE giveaways SET message_id = ? WHERE id = ?", (message_id, giveaway_id)
        )

    async def get_giveaway(self, giveaway_id: int) -> dict[str, Any] | None:
        return await self.fetchone("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))

    async def get_giveaway_by_message(
        self, channel_id: int, message_id: int
    ) -> dict[str, Any] | None:
        """Locate a giveaway from the interaction's source message."""
        return await self.fetchone(
            "SELECT * FROM giveaways WHERE channel_id = ? AND message_id = ?",
            (channel_id, message_id),
        )

    async def active_giveaways(self) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM giveaways WHERE ended = 0 AND cancelled = 0 ORDER BY ends_at ASC"
        )

    async def guild_giveaways(self, guild_id: int, include_ended: bool = False) -> list[dict]:
        if include_ended:
            return await self.fetchall(
                "SELECT * FROM giveaways WHERE guild_id = ? ORDER BY id DESC", (guild_id,)
            )
        return await self.fetchall(
            "SELECT * FROM giveaways WHERE guild_id = ? AND ended = 0 AND cancelled = 0 "
            "ORDER BY ends_at ASC",
            (guild_id,),
        )

    async def finish_giveaway(
        self, giveaway_id: int, winners: list[int], cancelled: bool = False
    ) -> None:
        await self.execute(
            "UPDATE giveaways SET ended = 1, cancelled = ?, winners = ? WHERE id = ?",
            (1 if cancelled else 0, json.dumps(winners), giveaway_id),
        )

    async def add_giveaway_entry(self, giveaway_id: int, user_id: int) -> bool:
        try:
            await self.execute(
                "INSERT INTO giveaway_entries(giveaway_id, user_id) VALUES (?, ?)",
                (giveaway_id, user_id),
            )
            return True
        except aiosqlite.IntegrityError:
            return False

    async def remove_giveaway_entry(self, giveaway_id: int, user_id: int) -> bool:
        cur = await self.execute(
            "DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
            (giveaway_id, user_id),
        )
        return cur.rowcount > 0

    async def giveaway_entries(self, giveaway_id: int) -> list[int]:
        rows = await self.fetchall(
            "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)
        )
        return [int(r["user_id"]) for r in rows]

    async def giveaway_entry_count(self, giveaway_id: int) -> int:
        return int(
            await self.scalar(
                "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)
            )
        )

    async def delete_giveaway(self, giveaway_id: int) -> None:
        await self.execute("DELETE FROM giveaways WHERE id = ?", (giveaway_id,))

    # ------------------------------------------------------------- tickets
    async def next_ticket_number(self, guild_id: int) -> int:
        await self.execute(
            "INSERT OR IGNORE INTO guild_settings(guild_id, ticket_counter) VALUES (?, 0)",
            (guild_id,),
        )
        await self.execute(
            "UPDATE guild_settings SET ticket_counter = ticket_counter + 1 WHERE guild_id = ?",
            (guild_id,),
        )
        value = await self.scalar(
            "SELECT ticket_counter FROM guild_settings WHERE guild_id = ?", (guild_id,), 1
        )
        return int(value or 1)

    async def create_ticket(
        self,
        guild_id: int,
        channel_id: int,
        owner_id: int,
        number: int,
        subject: str | None = None,
    ) -> int:
        cur = await self.execute(
            "INSERT INTO tickets(guild_id, channel_id, owner_id, number, subject, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, owner_id, number, subject, utcnow()),
        )
        ticket_id = int(cur.lastrowid or 0)
        await self.execute(
            "INSERT OR IGNORE INTO ticket_participants(ticket_id, user_id, added_by) "
            "VALUES (?, ?, ?)",
            (ticket_id, owner_id, owner_id),
        )
        return ticket_id

    async def get_ticket_by_channel(self, channel_id: int) -> dict[str, Any] | None:
        return await self.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ? ORDER BY id DESC LIMIT 1", (channel_id,)
        )

    async def update_ticket(self, ticket_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.execute(
            f"UPDATE tickets SET {cols} WHERE id = ?",  # noqa: S608
            [*fields.values(), ticket_id],
        )

    async def add_ticket_participant(
        self, ticket_id: int, user_id: int, added_by: int
    ) -> bool:
        try:
            await self.execute(
                "INSERT OR IGNORE INTO ticket_participants(ticket_id, user_id, added_by) "
                "VALUES (?, ?, ?)",
                (ticket_id, user_id, added_by),
            )
            return True
        except aiosqlite.IntegrityError:
            return False

    async def remove_ticket_participant(self, ticket_id: int, user_id: int) -> bool:
        cur = await self.execute(
            "DELETE FROM ticket_participants WHERE ticket_id = ? AND user_id = ?",
            (ticket_id, user_id),
        )
        return cur.rowcount > 0

    async def open_tickets(self, guild_id: int) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM tickets WHERE guild_id = ? AND status = 'open' ORDER BY number ASC",
            (guild_id,),
        )

    async def ticket_counts(self, guild_id: int) -> dict[str, int]:
        """Open/closed ticket totals for one guild (used by owner scan)."""
        rows = await self.fetchall(
            "SELECT status, COUNT(*) AS n FROM tickets WHERE guild_id = ? GROUP BY status",
            (guild_id,),
        )
        counts = {"open": 0, "closed": 0}
        for row in rows:
            counts[str(row["status"])] = int(row["n"])
        return counts

    async def purge_closed_tickets(self, guild_id: int) -> int:
        """Delete closed ticket records (with transcripts) for one guild."""
        cur = await self.execute(
            "DELETE FROM tickets WHERE guild_id = ? AND status = 'closed'", (guild_id,)
        )
        return cur.rowcount

    async def user_open_ticket_count(self, guild_id: int, owner_id: int) -> int:
        return int(
            await self.scalar(
                "SELECT COUNT(*) FROM tickets "
                "WHERE guild_id = ? AND owner_id = ? AND status = 'open'",
                (guild_id, owner_id),
            )
        )

    # ------------------------------------------------------- guild stats
    async def get_stat(self, guild_id: int, key: str, default: Any = None) -> Any:
        """Read a small per-guild key/value setting (guild_stats table)."""
        row = await self.fetchone(
            "SELECT value FROM guild_stats WHERE guild_id = ? AND key = ?",
            (guild_id, key),
        )
        return row["value"] if row else default

    async def set_stat(self, guild_id: int, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO guild_stats(guild_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (guild_id, key, str(value), utcnow()),
        )

    # ----------------------------------------------------------- reminders
    async def add_reminder(
        self, guild_id: int, channel_id: int, user_id: int, remind_at: int, message: str
    ) -> int:
        cur = await self.execute(
            "INSERT INTO reminders(guild_id, channel_id, user_id, remind_at, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, channel_id, user_id, remind_at, message, utcnow()),
        )
        return int(cur.lastrowid or 0)

    async def due_reminders(self, now_ts: int) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM reminders WHERE delivered = 0 AND remind_at <= ? ORDER BY remind_at ASC",
            (now_ts,),
        )

    async def pending_reminders_for(
        self, guild_id: int, user_id: int, now_ts: int
    ) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM reminders WHERE guild_id = ? AND user_id = ? AND delivered = 0 "
            "AND remind_at > ? ORDER BY remind_at ASC",
            (guild_id, user_id, now_ts),
        )

    async def mark_reminder(self, reminder_id: int) -> None:
        await self.execute("UPDATE reminders SET delivered = 1 WHERE id = ?", (reminder_id,))

    async def cancel_reminder(self, reminder_id: int, guild_id: int, user_id: int) -> bool:
        cur = await self.execute(
            "DELETE FROM reminders WHERE id = ? AND guild_id = ? AND user_id = ? AND delivered = 0",
            (reminder_id, guild_id, user_id),
        )
        return cur.rowcount > 0

    # --------------------------------------------------- command permissions
    async def get_command_config(
        self, guild_id: int, command_name: str
    ) -> dict[str, Any] | None:
        return await self.fetchone(
            "SELECT * FROM command_config WHERE guild_id = ? AND command_name = ?",
            (guild_id, command_name),
        )

    async def set_command_config(
        self,
        guild_id: int,
        command_name: str,
        enabled: bool = True,
        role_id: int | None = None,
    ) -> None:
        await self.execute(
            "INSERT INTO command_config(guild_id, command_name, enabled, role_id) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, command_name) DO UPDATE SET enabled = ?, role_id = ?",
            (guild_id, command_name, int(enabled), role_id, int(enabled), role_id),
        )

    async def guild_command_overrides(self, guild_id: int) -> dict[str, dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT * FROM command_config WHERE guild_id = ?", (guild_id,)
        )
        return {str(r["command_name"]): r for r in rows}

    async def delete_command_config(self, guild_id: int, command_name: str) -> bool:
        cur = await self.execute(
            "DELETE FROM command_config WHERE guild_id = ? AND command_name = ?",
            (guild_id, command_name),
        )
        return cur.rowcount > 0

    # ------------------------------------------------------- ignore lists
    async def add_ignore(self, table: str, guild_id: int, target_id: int) -> bool:
        if table not in {"ignored_channels", "ignored_roles"}:
            raise ValueError(f"Unsupported ignore table: {table}")
        cur = await self.execute(
            f"INSERT OR IGNORE INTO {table}(guild_id, {'channel_id' if table == 'ignored_channels' else 'role_id'}) VALUES (?, ?)",  # noqa: S608
            (guild_id, target_id),
        )
        return cur.rowcount > 0

    async def remove_ignore(self, table: str, guild_id: int, target_id: int) -> bool:
        if table not in {"ignored_channels", "ignored_roles"}:
            raise ValueError(f"Unsupported ignore table: {table}")
        col = "channel_id" if table == "ignored_channels" else "role_id"
        cur = await self.execute(
            f"DELETE FROM {table} WHERE guild_id = ? AND {col} = ?",  # noqa: S608
            (guild_id, target_id),
        )
        return cur.rowcount > 0

    async def ignored_channels(self, guild_id: int) -> list[int]:
        rows = await self.fetchall(
            "SELECT channel_id FROM ignored_channels WHERE guild_id = ?", (guild_id,)
        )
        return [int(r["channel_id"]) for r in rows]

    async def ignored_roles(self, guild_id: int) -> list[int]:
        rows = await self.fetchall(
            "SELECT role_id FROM ignored_roles WHERE guild_id = ?", (guild_id,)
        )
        return [int(r["role_id"]) for r in rows]

    # ----------------------------------------------------------------- AFK
    async def set_afk(self, guild_id: int, user_id: int, message: str, since: int) -> None:
        await self.execute(
            "INSERT INTO afk(guild_id, user_id, message, since) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET message = ?, since = ?",
            (guild_id, user_id, message, since, message, since),
        )

    async def get_afk(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        return await self.fetchone(
            "SELECT * FROM afk WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )

    async def remove_afk(self, guild_id: int, user_id: int) -> bool:
        cur = await self.execute(
            "DELETE FROM afk WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        return cur.rowcount > 0

    # ------------------------------------------------------------ auto replies
    async def autoreply_entries(self, guild_id: int) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM autoreply_entries WHERE guild_id = ? ORDER BY id ASC",
            (guild_id,),
        )

    async def autoreply_add(
        self, guild_id: int, trigger: str, response: str
    ) -> dict[str, Any]:
        cur = await self.execute(
            "INSERT INTO autoreply_entries(guild_id, trigger, response) VALUES (?, ?, ?)",
            (guild_id, trigger, response),
        )
        row_id = int(cur.lastrowid or 0)
        return {"id": row_id, "guild_id": guild_id, "trigger": trigger, "response": response}

    async def autoreply_remove(self, guild_id: int, entry_id: int) -> bool:
        cur = await self.execute(
            "DELETE FROM autoreply_entries WHERE id = ? AND guild_id = ?",
            (entry_id, guild_id),
        )
        return cur.rowcount > 0


__all__ = ["Database", "utcnow"]
