"""Giveaway logic tests: winner drawing, eligibility, user-list parsing."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from cogs.giveaways import GIVEAWAY_REACTION_EMOJI, Giveaways, draw_winners
from cogs.moderation import parse_user_list
from database.database import DEFAULT_GUILD_ROW, Database


# ------------------------------------------------------------ winner drawing
def test_draw_winners_picks_unique_winners() -> None:
    entries = list(range(1, 101))
    winners = asyncio.run(draw_winners(entries, 3))
    assert len(winners) == 3
    assert len(set(winners)) == 3
    assert set(winners) <= set(entries)


def test_draw_winners_with_fewer_entries_than_winners() -> None:
    winners = asyncio.run(draw_winners([7, 8], 5))
    assert sorted(winners) == [7, 8]


def test_draw_winners_empty_pool() -> None:
    assert asyncio.run(draw_winners([], 3)) == []


def test_draw_winners_deduplicates_ids() -> None:
    winners = asyncio.run(draw_winners([5, 5, 5, 5], 2))
    assert winners == [5]


def test_draw_winners_uses_injectable_rng_for_determinism() -> None:
    rng = random.Random(1234)
    first = asyncio.run(draw_winners(list(range(50)), 5, seed=rng))
    rng2 = random.Random(1234)
    second = asyncio.run(draw_winners(list(range(50)), 5, seed=rng2))
    assert first == second


def test_draw_winners_result_is_a_valid_sample() -> None:
    entries = list(range(1000))
    winners = asyncio.run(draw_winners(entries, 10))
    assert len(winners) == 10
    assert all(w in entries for w in winners)
    assert len(set(winners)) == 10


# --------------------------------------------------------- massban parsing
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123456789", [123456789]),
        ("<@123456789>", [123456789]),
        ("<@!123456789>", [123456789]),
        ("111 222 333", [111, 222, 333]),
        ("111,222,333", [111, 222, 333]),
        ("<@111>, <@222>", [111, 222]),
        ("111 111 222", [111, 222]),
        ("", []),
        ("not-a-user", []),
        ("<@&999>", []),
    ],
)
def test_parse_user_list(raw: str, expected: list[int]) -> None:
    assert parse_user_list(raw) == expected


# --------------------------------------------------------------- eligibility
def test_embed_builder_for_giveaway() -> None:
    from cogs.giveaways import build_giveaway_embed

    embed = build_giveaway_embed(
        {
            "id": 1,
            "host_id": 42,
            "prize": "Robux",
            "description": "Enjoy!",
            "winners_count": 2,
            "ends_at": 2_000_000_000,
            "required_role_id": 55,
            "min_account_age": 86400,
            "min_server_age": 3600,
        },
        entries=0,
    )
    field_names = [f.name for f in embed.fields]
    assert "Prize" in field_names
    assert "Winners" in field_names
    assert "Requirements" in field_names
    requirements = next(f for f in embed.fields if f.name == "Requirements").value
    assert "<@&55>" in requirements
    assert "1d" in requirements  # account age
    assert "1h" in requirements  # server tenure


# ------------------------------------------------------ reaction-to-join
def test_reaction_listener_registered_on_cog() -> None:
    """The raw-reaction handler must be a real cog listener, not dead code."""
    listener_names = [name for name, _ in getattr(Giveaways, "__cog_listeners__", [])]
    assert "on_raw_reaction_add" in listener_names


def test_default_guild_row_enables_react_join() -> None:
    assert DEFAULT_GUILD_ROW["giveaway_react_join"] == 1


def test_migration_adds_giveaway_react_join(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "react_join.db")
        await database.connect()
        col = await database.fetchone(
            "SELECT dflt_value FROM pragma_table_info('guild_settings') "
            "WHERE name = 'giveaway_react_join'"
        )
        assert col is not None and col["dflt_value"] == "1"
        settings = await database.get_guild(123)
        assert settings["giveaway_react_join"] == 1
        await database.close()

    asyncio.run(scenario())


def test_reaction_join_gate_reads_per_server_setting(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "gate.db")
        await database.connect()
        await database.update_guild(111, giveaway_react_join=0)
        await database.update_guild(222, giveaway_react_join=1)

        cog = Giveaways.__new__(Giveaways)  # skip __init__ (starts background task)
        cog.bot = SimpleNamespace(db=database)

        assert await cog._reaction_join_enabled(111) is False
        assert await cog._reaction_join_enabled(222) is True
        assert await cog._reaction_join_enabled(333) is True  # server never configured
        await database.close()

    asyncio.run(scenario())


def test_listener_respects_server_toggle(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "listener.db")
        await database.connect()
        guild_id = 111
        giveaway_id = await database.create_giveaway(
            guild_id, channel_id=900, host_id=42, prize="Robux", ends_at=2_000_000_000
        )
        await database.set_giveaway_message(giveaway_id, 555)
        await database.update_guild(guild_id, giveaway_react_join=0)  # disabled

        member = SimpleNamespace(
            id=7,
            bot=False,
            guild=SimpleNamespace(id=guild_id),
            roles=[],
            created_at=datetime.now(timezone.utc),
            joined_at=datetime.now(timezone.utc),
        )
        payload = SimpleNamespace(
            guild_id=guild_id,
            channel_id=900,
            message_id=555,
            user_id=7,
            member=member,
            emoji=SimpleNamespace(name=GIVEAWAY_REACTION_EMOJI),
        )

        cog = Giveaways.__new__(Giveaways)
        cog.bot = SimpleNamespace(
            db=database, user=None, get_channel=lambda *_: None
        )

        # Disabled on this server: reaction must be ignored.
        await cog.on_raw_reaction_add(payload)
        assert await database.giveaway_entry_count(giveaway_id) == 0

        # Enabled: the same reaction enters the member exactly once.
        await database.update_guild(guild_id, giveaway_react_join=1)
        await cog.on_raw_reaction_add(payload)
        await cog.on_raw_reaction_add(payload)
        assert await database.giveaway_entry_count(giveaway_id) == 1
        assert await database.giveaway_entries(giveaway_id) == [7]

        # Wrong emoji / unknown message: ignored.
        payload.emoji.name = "🔥"
        await cog.on_raw_reaction_add(payload)
        payload.emoji.name = GIVEAWAY_REACTION_EMOJI
        payload.message_id = 999
        await cog.on_raw_reaction_add(payload)
        assert await database.giveaway_entry_count(giveaway_id) == 1
        await database.close()

    asyncio.run(scenario())
