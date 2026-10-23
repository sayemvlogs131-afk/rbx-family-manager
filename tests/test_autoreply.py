"""Custom auto reply tests: trigger matching, add/remove, validation."""

from __future__ import annotations

import asyncio
import tempfile
from typing import Any

import pytest

from cogs.autoreply import _normalize, _store_response, _store_trigger


# ------------------------------------------------------------ normalization
def test_normalize_lower_and_strips() -> None:
    assert _normalize("  Rules  ") == "rules"
    assert _normalize("KEYBOARD RULES") == "keyboard rules"


# ------------------------------------------------------------ trigger storage
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("rules", "rules"),
        ("  rules  ", "rules"),
        ("keyboard rules", "keyboard rules"),
        ("", None),
        ("a" * 101, None),
    ],
)
def test_store_trigger(raw: str, expected: str | None) -> None:
    assert _store_trigger(raw) == expected


# ------------------------------------------------------------ response storage
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("#rules", "#rules"),
        ("  #rules  ", "#rules"),
        ("", None),
        ("a" * 1001, None),
    ],
)
def test_store_response(raw: str, expected: str | None) -> None:
    assert _store_response(raw) == expected


# ------------------------------------------------------------ trigger matching
def test_trigger_matches_substring() -> None:
    async def scenario() -> None:
        from cogs.autoreply import AutoReply

        bot = _make_bot()
        await bot.connect()
        cog = AutoReply(bot)

        await cog._set_enabled(bot.fake_guild_id, True)
        await bot.db.execute(
            "INSERT INTO autoreply_entries(guild_id, trigger, response) VALUES (?, ?, ?)",
            (bot.fake_guild_id, "rules", "#rules"),
        )

        msg = _make_message(content="please send rules", guildme=bot.user)
        matched = await cog._trigger_for_message(msg)
        assert matched is not None
        assert matched["trigger"] == "rules"

    asyncio.run(scenario())


def test_trigger_does_not_match_if_disabled() -> None:
    async def scenario() -> None:
        from cogs.autoreply import AutoReply

        bot = _make_bot()
        await bot.connect()
        cog = AutoReply(bot)

        await cog._set_enabled(bot.fake_guild_id, False)
        await bot.db.execute(
            "INSERT OR REPLACE INTO guild_settings(guild_id, autoreply_enabled) VALUES (?, 0)",
            (bot.fake_guild_id,),
        )
        await bot.db.execute(
            "INSERT INTO autoreply_entries(guild_id, trigger, response) VALUES (?, ?, ?)",
            (bot.fake_guild_id, "rules", "#rules"),
        )

        msg = _make_message(content="rules", guildme=bot.user)
        matched = await cog._trigger_for_message(msg)
        assert matched is None

    asyncio.run(scenario())


def test_bot_messages_are_ignored() -> None:
    async def scenario() -> None:
        from cogs.autoreply import AutoReply

        bot = _make_bot()
        await bot.connect()
        cog = AutoReply(bot)

        await cog._set_enabled(bot.fake_guild_id, True)
        await bot.db.execute(
            "INSERT INTO autoreply_entries(guild_id, trigger, response) VALUES (?, ?, ?)",
            (bot.fake_guild_id, "rules", "#rules"),
        )

        msg = _make_message(content="rules", author=bot.user, guildme=bot.user)
        matched = await cog._trigger_for_message(msg)
        assert matched is None

    asyncio.run(scenario())


def test_first_matching_trigger_wins() -> None:
    async def scenario() -> None:
        from cogs.autoreply import AutoReply

        bot = _make_bot()
        await bot.connect()
        cog = AutoReply(bot)

        await cog._set_enabled(bot.fake_guild_id, True)
        await bot.db.execute(
            "INSERT INTO autoreply_entries(guild_id, trigger, response) VALUES (?, ?, ?)",
            (bot.fake_guild_id, "keyboard", "KB"),
        )
        await bot.db.execute(
            "INSERT INTO autoreply_entries(guild_id, trigger, response) VALUES (?, ?, ?)",
            (bot.fake_guild_id, "keyboard rules", "KEYBOARD RULES"),
        )

        msg = _make_message(content="keyboard rules", guildme=bot.user)
        matched = await cog._trigger_for_message(msg)
        assert matched is not None
        assert matched["trigger"] == "keyboard"

    asyncio.run(scenario())


# --------------------------------------------------------- fake objects
class _FakeGuild:
    def __init__(self, *, id: int, me: Any = None) -> None:
        self.id = id
        self.me = me


class _FakeChannel:
    def __init__(self, *, guild_id: int, guildme: Any = None) -> None:
        self.guild = _FakeGuild(id=guild_id)
        self._guildme = guildme

    def permissions_for(self, member: Any) -> Any:
        class _Perms:
            send_messages = True

        return _Perms()

    send_messages = True


class _FakeUser:
    def __init__(self, *, id: int, name: str = "user", bot: bool = False, guild_id: int = 12345) -> None:
        self.id = id
        self.name = name
        self.bot = bot
        self.guild_id = guild_id


class _FakeAuthor(_FakeUser):
    pass


class _FakeMessage:
    def __init__(self, *, content: str, author: Any, bot_user: Any, guild_id: int, guildme: Any = None) -> None:
        self.content = content
        self.author = author
        self.bot_user = bot_user
        self.guild = _FakeGuild(id=guild_id, me=guildme)
        self.channel = _FakeChannel(guild_id=guild_id, guildme=guildme)
        import datetime as _dt

        self.created_at = _dt.datetime.now(tz=_dt.timezone.utc)

    @property
    def state(self) -> Any:
        return None


def _make_message(
    *,
    content: str = "",
    author: Any = None,
    bot_user: Any = None,
    guild_id: int = 12345,
    guildme: Any = None,
) -> Any:
    author = author or _FakeUser(id=2, name="user", bot=False, guild_id=guild_id)
    return _FakeMessage(content=content, author=author, bot_user=bot_user, guild_id=guild_id, guildme=guildme)


def _make_bot(path: str | None = None) -> _FakeBot:
    if path is None:
        path = tempfile.mktemp(suffix=".db")
    return _FakeBot(path)


class _FakeBot:
    fake_guild_id = 12345
    user: _FakeUser | None = None

    def __init__(self, path: str) -> None:
        import database.database

        self._path = path
        self.db = database.database.Database(path)
        self._ready = False
        self.user = _FakeUser(id=1, name="bot", bot=True, guild_id=self.fake_guild_id)

    async def connect(self) -> None:
        if not self._ready:
            await self.db.connect()
            self._ready = True

    async def close(self) -> None:
        if self._ready:
            await self.db.close()
            self._ready = False
