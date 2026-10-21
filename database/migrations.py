"""Schema migrations for RBX Family Manager.

The database starts empty and is brought up to date automatically on boot.

Version 1 always applies ``schema.sql`` (written with ``IF NOT EXISTS`` so it
is safe on both fresh and existing databases). Higher versions apply small,
targeted ``ALTER TABLE`` statements so upgrades never destroy data.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger("rbx.migrations")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
LATEST_VERSION = 6


async def _has_column(db: aiosqlite.Connection, table: str, column: str) -> bool:
    async with db.execute(
        "SELECT 1 FROM pragma_table_info(?) WHERE name = ?", (table, column)
    ) as cur:
        return await cur.fetchone() is not None


async def _migration_2(db: aiosqlite.Connection) -> None:
    """Auto-reply support: guild flag column (added only when missing)."""
    if not await _has_column(db, "guild_settings", "autoreply_enabled"):
        await db.execute(
            "ALTER TABLE guild_settings ADD COLUMN autoreply_enabled INTEGER NOT NULL DEFAULT 1"
        )
    # Clean up the leftover guard table from older, buggier versions.
    await db.execute("DROP TABLE IF EXISTS _tmp_autoreply_check")


async def _migration_3(db: aiosqlite.Connection) -> None:
    """Per-server giveaway reaction-to-join toggle (default: enabled)."""
    if not await _has_column(db, "guild_settings", "giveaway_react_join"):
        await db.execute(
            "ALTER TABLE guild_settings ADD COLUMN giveaway_react_join INTEGER NOT NULL DEFAULT 1"
        )


async def _migration_4(db: aiosqlite.Connection) -> None:
    """Ticket upgrades: subject line from the modal + claim tracking."""
    if not await _has_column(db, "tickets", "subject"):
        await db.execute("ALTER TABLE tickets ADD COLUMN subject TEXT")
    if not await _has_column(db, "tickets", "claimed_by"):
        await db.execute("ALTER TABLE tickets ADD COLUMN claimed_by INTEGER")


async def _migration_5(db: aiosqlite.Connection) -> None:
    """Security layer: anti-nuke config + sensitive-action audit trail.

    Both tables are created by schema.sql, which is re-applied on every boot;
    this migration exists so the version counter tracks the schema layout.
    """
    await db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    await db.commit()


async def _migration_6(db: aiosqlite.Connection) -> None:
    """Mass-kick limits, anti-raid flood settings, suspicious-account threshold
    and the leave/farewell system."""
    security_columns = (
        ("security_config", "kick_limit", "INTEGER NOT NULL DEFAULT 5"),
        ("security_config", "join_limit", "INTEGER NOT NULL DEFAULT 8"),
        ("security_config", "join_window_seconds", "INTEGER NOT NULL DEFAULT 30"),
        ("security_config", "raid_action", "TEXT NOT NULL DEFAULT 'alert'"),
        ("security_config", "min_account_age_days", "INTEGER NOT NULL DEFAULT 3"),
        ("guild_settings", "leave_channel", "INTEGER"),
        ("guild_settings", "leave_message", "TEXT"),
        ("guild_settings", "leave_embed", "INTEGER NOT NULL DEFAULT 1"),
    )
    for table, column, definition in security_columns:
        if not await _has_column(db, table, column):
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    await db.commit()


# Migrations 2+ are coroutines so column checks run for real instead of
# relying on conditional DDL (SQLite cannot do "ADD COLUMN IF NOT EXISTS").
_MIGRATIONS: dict[int, object] = {
    # 1 is handled by schema.sql (full baseline).
    2: _migration_2,
    3: _migration_3,
    4: _migration_4,
    5: _migration_5,
    6: _migration_6,
}


async def _current_version(db: aiosqlite.Connection) -> int:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    await db.commit()
    async with db.execute("SELECT MAX(version) FROM schema_migrations") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


async def _apply_baseline(db: aiosqlite.Connection) -> None:
    """Run schema.sql. Every statement is IF NOT EXISTS, so this is idempotent
    and also creates tables (like autoreply_entries) that older databases are
    missing because they were baselined before those tables existed."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    await db.executescript(sql)
    await db.execute(
        "INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)"
    )
    await db.commit()


async def migrate(db: aiosqlite.Connection) -> int:
    """Bring the database up to ``LATEST_VERSION``. Returns the final version."""
    version = await _current_version(db)
    if version == 0:
        await _apply_baseline(db)
        version = 1
        log.info("Database baseline schema applied (version 1).")
    else:
        # Keep the schema in sync on every boot: schema.sql is entirely
        # IF NOT EXISTS, so re-running it adds tables introduced by newer
        # releases to databases that were baselined before they existed.
        await _apply_baseline(db)

    for target in sorted(_MIGRATIONS):
        if target <= version:
            continue
        try:
            step = _MIGRATIONS[target]
            if isinstance(step, list):
                for stmt in step:
                    await db.execute(stmt)
            else:
                await step(db)  # type: ignore[operator]
            await db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (target,)
            )
            await db.commit()
            log.info("Database migrated to version %s.", target)
        except Exception:
            log.exception("Migration to version %s failed", target)
            raise
        version = target

    if version < LATEST_VERSION and not _MIGRATIONS:
        # Baseline-only project: record it so future migrations chain correctly.
        await db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (LATEST_VERSION,)
        )
        await db.commit()
        version = LATEST_VERSION

    return version
