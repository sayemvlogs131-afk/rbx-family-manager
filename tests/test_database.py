"""Database initialisation, warnings, config storage and giveaway recovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from database.database import Database
from database.migrations import LATEST_VERSION


# ------------------------------------------------------------ initialisation
def test_database_starts_empty(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "fresh.db"
        assert not path.exists()
        database = Database(path)
        await database.connect()
        tables = await database.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        names = {t["name"] for t in tables}
        for required in (
            "guild_settings",
            "warnings",
            "moderation_actions",
            "giveaways",
            "giveaway_entries",
            "tickets",
            "reminders",
            "automod_config",
            "command_config",
            "autoreply_entries",
            "schema_migrations",
        ):
            assert required in names, f"missing table {required}"
        version = await database.scalar("SELECT MAX(version) FROM schema_migrations")
        assert version == LATEST_VERSION
        await database.close()

    asyncio.run(scenario())


def test_database_reopen_is_idempotent(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "reopen.db"
        first = Database(path)
        await first.connect()
        await first.add_warning(1, 2, 3, "first")
        await first.close()

        second = Database(path)
        await second.connect()
        warnings = await second.get_warnings(1, 2)
        assert len(warnings) == 1
        await second.close()

    asyncio.run(scenario())


# ------------------------------------------------------------- guild settings
def test_reaction_roles_crud(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "roles.db")
        await database.connect()

        assert await database.add_reaction_role(1, 10, 20, 30, "🎮 Gamer")
        rows = await database.reaction_roles_for_guild(1)
        assert len(rows) == 1
        assert rows[0]["role_id"] == 30
        assert rows[0]["label"] == "🎮 Gamer"

        # Duplicate role on the same panel is rejected.
        assert not await database.add_reaction_role(1, 10, 20, 30, "again")
        # Same role in another guild is fine.
        assert await database.add_reaction_role(2, 10, 20, 30, "🎮 Gamer")
        assert len(await database.reaction_roles_for_guild(1)) == 1
        assert len(await database.reaction_roles_for_guild(2)) == 1

        assert await database.remove_reaction_role(1, 30)
        assert not await database.remove_reaction_role(1, 999)
        assert await database.reaction_roles_for_guild(1) == []

        await database.add_reaction_role(1, 10, 20, 31, "a")
        await database.add_reaction_role(1, 10, 20, 32, "b")
        assert await database.clear_reaction_roles(1) == 2
        assert await database.reaction_roles_for_guild(1) == []

        await database.close()

    asyncio.run(scenario())


def test_guild_settings_are_isolated_per_guild(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "guilds.db")
        await database.connect()
        await database.update_guild(111, mod_log_channel=1000, staff_role_id=2000)
        await database.update_guild(222, mod_log_channel=3000)

        guild_a = await database.get_guild(111)
        guild_b = await database.get_guild(222)
        assert guild_a["mod_log_channel"] == 1000
        assert guild_b["mod_log_channel"] == 3000
        assert guild_a["staff_role_id"] == 2000
        assert guild_b["staff_role_id"] is None

        # A third guild gets defaults without touching the others.
        guild_c = await database.get_guild(333)
        assert guild_c["mod_log_channel"] is None
        assert (await database.get_guild(111))["mod_log_channel"] == 1000
        await database.close()

    asyncio.run(scenario())


def test_automod_config_defaults_and_updates(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "automod.db")
        await database.connect()
        defaults = await database.get_automod(42)
        assert defaults["enabled"] == 1
        assert defaults["mention_limit"] == 6

        await database.update_automod(42, mention_limit=3, banned_words=json.dumps(["bad"]))
        updated = await database.get_automod(42)
        assert updated["mention_limit"] == 3
        assert json.loads(updated["banned_words"]) == ["bad"]

        other = await database.get_automod(43)
        assert other["mention_limit"] == 6
        await database.close()

    asyncio.run(scenario())


def test_command_config_and_ignore_lists(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "cmds.db")
        await database.connect()
        await database.set_command_config(1, "ban", enabled=False)
        row = await database.get_command_config(1, "ban")
        assert row["enabled"] == 0

        await database.set_command_config(1, "ban", enabled=True, role_id=55)
        row = await database.get_command_config(1, "ban")
        assert row["enabled"] == 1 and row["role_id"] == 55

        assert await database.add_ignore("ignored_channels", 1, 777)
        assert 777 in await database.ignored_channels(1)
        assert await database.remove_ignore("ignored_channels", 1, 777)
        assert 777 not in await database.ignored_channels(1)
        await database.close()

    asyncio.run(scenario())


# ------------------------------------------------------------------- warnings
def test_warning_lifecycle(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "warnings.db")
        await database.connect()

        first = await database.add_warning(1, 100, 200, "Spamming")
        second = await database.add_warning(1, 100, 200, "Advertising")
        assert first != second
        assert await database.warning_count(1, 100) == 2

        rows = await database.get_warnings(1, 100)
        assert rows[0]["reason"] == "Advertising"  # newest first
        assert rows[0]["moderator_id"] == 200
        assert rows[0]["guild_id"] == 1

        # Another guild's warnings are invisible.
        await database.add_warning(2, 100, 300, "Other server")
        assert await database.warning_count(1, 100) == 2
        assert await database.warning_count(2, 100) == 1

        assert await database.remove_warning(1, first)
        assert not await database.remove_warning(1, first)
        assert await database.warning_count(1, 100) == 1

        assert await database.clear_warnings(1, 100) == 1
        assert await database.warning_count(1, 100) == 0
        await database.close()

    asyncio.run(scenario())


def test_moderation_actions_are_recorded(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "modlog.db")
        await database.connect()
        case = await database.log_moderation(1, 100, 200, "ban", "spam", None)
        assert case >= 1
        rows = await database.recent_actions(1, 100)
        assert rows[0]["action"] == "ban"
        assert rows[0]["reason"] == "spam"
        assert await database.recent_actions(1, 999) == []
        await database.close()

    asyncio.run(scenario())


# ------------------------------------------------------- giveaway persistence
def test_giveaway_creation_and_restart_recovery(tmp_path: Path) -> None:
    async def scenario():
        path = tmp_path / "giveaways.db"

        first = Database(path)
        await first.connect()
        giveaway_id = await first.create_giveaway(
            guild_id=1,
            channel_id=555,
            host_id=42,
            prize="Discord Nitro",
            ends_at=99_999_999_999,
            winners_count=2,
            description="Enjoy",
            required_role_id=7,
            min_account_age=86400,
            min_server_age=3600,
        )
        await first.set_giveaway_message(giveaway_id, 987654)
        assert await first.add_giveaway_entry(giveaway_id, 1)
        assert await first.add_giveaway_entry(giveaway_id, 2)
        assert not await first.add_giveaway_entry(giveaway_id, 1)  # duplicate rejected
        await first.close()

        # Simulate a restart: fresh connection, same file.
        second = Database(path)
        await second.connect()
        active = await second.active_giveaways()
        assert len(active) == 1
        recovered = active[0]
        assert recovered["prize"] == "Discord Nitro"
        assert recovered["message_id"] == 987654
        assert recovered["ended"] == 0
        assert recovered["required_role_id"] == 7
        entries = await second.giveaway_entries(giveaway_id)
        assert sorted(entries) == [1, 2]

        await second.finish_giveaway(giveaway_id, [2])
        assert await second.active_giveaways() == []
        ended = await second.get_giveaway(giveaway_id)
        assert ended["ended"] == 1
        assert json.loads(ended["winners"]) == [2]
        await second.close()

    asyncio.run(scenario())


def test_giveaway_message_lookup(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "lookup.db")
        await database.connect()
        gid = await database.create_giveaway(1, 10, 20, "Prize", 9_999_999_999)
        await database.set_giveaway_message(gid, 777)
        found = await database.get_giveaway_by_message(10, 777)
        assert found is not None and found["id"] == gid
        assert await database.get_giveaway_by_message(10, 778) is None
        await database.close()

    asyncio.run(scenario())


# ------------------------------------------------------------------ reminders
def test_reminder_scheduling(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "reminders.db")
        await database.connect()
        await database.add_reminder(1, 10, 20, 1000, "eat")
        await database.add_reminder(1, 10, 20, 5000, "sleep")
        assert len(await database.due_reminders(2000)) == 1
        assert len(await database.due_reminders(9000)) == 2
        assert len(await database.pending_reminders_for(1, 20, 2000)) == 1

        due = await database.due_reminders(9000)
        await database.mark_reminder(due[0]["id"])
        assert len(await database.due_reminders(9000)) == 1
        await database.close()

    asyncio.run(scenario())


# -------------------------------------------------------------------- tickets
def test_ticket_flow(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "tickets.db")
        await database.connect()
        number = await database.next_ticket_number(1)
        assert number == 1
        assert await database.next_ticket_number(1) == 2

        ticket_id = await database.create_ticket(1, 500, 42, number)
        record = await database.get_ticket_by_channel(500)
        assert record is not None and record["status"] == "open"
        assert await database.open_tickets(1)

        await database.update_ticket(ticket_id, status="closed", transcript="hello")
        record = await database.get_ticket_by_channel(500)
        assert record["status"] == "closed"
        assert record["transcript"] == "hello"
        assert await database.open_tickets(1) == []
        await database.close()

    asyncio.run(scenario())


def test_ticket_subject_claim_and_counts(tmp_path: Path) -> None:
    """v4 columns: subject + claimed_by, plus owner-scan helpers."""

    async def scenario():
        db = Database(tmp_path / "tickets_v4.db")
        await db.connect()

        # Subject flows through create_ticket (optional, defaults to NULL).
        tid = await db.create_ticket(7, 501, 42, 1, subject="billing question")
        record = await db.get_ticket_by_channel(501)
        assert record["subject"] == "billing question"
        assert record["claimed_by"] is None

        await db.create_ticket(7, 502, 43, 2)
        assert (await db.get_ticket_by_channel(502))["subject"] is None

        # Claim / release round-trip.
        await db.update_ticket(tid, claimed_by=99)
        assert (await db.get_ticket_by_channel(501))["claimed_by"] == 99
        await db.update_ticket(tid, claimed_by=None)
        assert (await db.get_ticket_by_channel(501))["claimed_by"] is None

        # Per-member open count + status counts (owner scan inputs).
        assert await db.user_open_ticket_count(7, 42) == 1
        assert await db.user_open_ticket_count(7, 43) == 1
        assert await db.user_open_ticket_count(7, 44) == 0
        counts = await db.ticket_counts(7)
        assert counts == {"open": 2, "closed": 0}

        # Close one, then purge only closed records — open survives.
        await db.update_ticket(tid, status="closed", transcript="bye")
        counts = await db.ticket_counts(7)
        assert counts == {"open": 1, "closed": 1}
        deleted = await db.purge_closed_tickets(7)
        assert deleted == 1
        assert await db.get_ticket_by_channel(501) is None
        assert await db.get_ticket_by_channel(502) is not None
        assert (await db.ticket_counts(7))["open"] == 1
        await db.close()

    asyncio.run(scenario())


def test_guild_stats_roundtrip(tmp_path: Path) -> None:
    """Small key/value settings used by the ticket system."""

    async def scenario():
        db = Database(tmp_path / "stats.db")
        await db.connect()

        assert await db.get_stat(1, "ticket_user_limit", 1) == 1
        await db.set_stat(1, "ticket_user_limit", "3")
        assert await db.get_stat(1, "ticket_user_limit", 1) == "3"

        # Upsert: writing again overwrites instead of duplicating.
        await db.set_stat(1, "ticket_user_limit", "5")
        assert await db.get_stat(1, "ticket_user_limit") == "5"

        # Guilds and keys are independent.
        await db.set_stat(2, "ticket_panel_message", "777")
        assert await db.get_stat(1, "ticket_panel_message") is None
        assert await db.get_stat(2, "ticket_user_limit") is None
        assert await db.get_stat(2, "ticket_panel_message") == "777"
        await db.close()

    asyncio.run(scenario())


# ----------------------------------------------------------------------- afk
def test_afk_storage(tmp_path: Path) -> None:
    async def scenario():
        database = Database(tmp_path / "afk.db")
        await database.connect()
        await database.set_afk(1, 2, "brb", 123)
        row = await database.get_afk(1, 2)
        assert row["message"] == "brb"
        assert await database.remove_afk(1, 2)
        assert await database.get_afk(1, 2) is None
        await database.close()

    asyncio.run(scenario())
