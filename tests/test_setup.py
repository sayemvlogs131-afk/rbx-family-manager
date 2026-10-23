"""Automatic ``/setup``: the command builds a bare server up by itself."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from cogs.server import (
    LOG_CHANNEL_NAME,
    MEMBER_ROLE_NAME,
    STAFF_ROLE_NAME,
    WELCOME_CHANNEL_NAME,
    Server,
    find_channel_by_name,
    find_role_by_name,
    log_channel_overwrites,
    welcome_channel_overwrites,
)
from cogs.tickets import CATEGORY_NAME, PANEL_CHANNEL_NAME, Tickets
from database.database import Database


# ------------------------------------------------------------------- fakes
class FakeRole:
    def __init__(self, id: int, name: str, position: int = 1, default: bool = False):
        self.id = id
        self.name = name
        self.position = position
        self.managed = False
        self._default = default
        self.mention = f"<@&{id}>"

    def is_default(self) -> bool:
        return self._default


class FakeMember:
    def __init__(self, id: int = 999, position: int = 50, **flags: bool):
        defaults = dict(
            manage_channels=True,
            manage_roles=True,
            manage_guild=True,
            administrator=True,
            send_messages=True,
            embed_links=True,
        )
        defaults.update(flags)
        self.id = id
        self.mention = f"<@{id}>"
        self.guild_permissions = SimpleNamespace(**defaults)
        self.top_role = SimpleNamespace(position=position)


class FakeMessage:
    def __init__(self, id: int, channel: "FakeChannel"):
        self.id = id
        self.channel = channel
        self.edits: list[dict] = []

    async def edit(self, **kwargs) -> "FakeMessage":
        self.edits.append(kwargs)
        return self


class FakeChannel:
    def __init__(self, id: int, name: str, guild: "FakeGuild", category=None, **can: bool):
        self.id = id
        self.name = name
        self.guild = guild
        self.category = category
        self.mention = f"<#{id}>"
        self.overwrites = {}
        self.messages: list[FakeMessage] = []
        defaults = dict(send_messages=True, embed_links=True)
        defaults.update(can)
        self._perms = SimpleNamespace(**defaults)

    def permissions_for(self, member):
        return self._perms

    async def send(self, content=None, *, embed=None, view=None, **kwargs) -> FakeMessage:
        message = FakeMessage(10_000 + len(self.messages), self)
        self.messages.append(message)
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        for message in self.messages:
            if message.id == message_id:
                return message
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Unknown Message"
        )


class FakeCategory:
    def __init__(self, id: int, name: str, guild: "FakeGuild"):
        self.id = id
        self.name = name
        self.guild = guild
        self.mention = f"<#{id}>"
        self.overwrites = {}


class FakeGuild:
    def __init__(self, *, allow_create: bool = True, id: int = 1):
        self.id = id
        self.name = "Test Server"
        self.allow_create = allow_create
        self.default_role = FakeRole(id, "@everyone", position=0, default=True)
        self.me = FakeMember()
        self.roles = [self.default_role]
        self.text_channels: list[FakeChannel] = []
        self.categories: list[FakeCategory] = []
        self.created_roles: list[str] = []
        self.created_channels: list[str] = []
        self.created_categories: list[str] = []
        self._next_id = 100

    def _denied(self) -> discord.Forbidden:
        return discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
        )

    def _take_id(self) -> int:
        self._next_id += 1
        return self._next_id

    # ---------------------------------------------------------- lookups
    def get_role(self, role_id: int):
        for role in self.roles:
            if role.id == role_id:
                return role
        return None

    def get_channel(self, channel_id: int):
        for channel in [*self.text_channels, *self.categories]:
            if channel.id == channel_id:
                return channel
        return None

    # --------------------------------------------------------- creation
    async def create_role(self, *, name: str, reason: str = "", **kwargs) -> FakeRole:
        if not self.allow_create:
            raise self._denied()
        role = FakeRole(self._take_id(), name, position=1)
        self.roles.append(role)
        self.created_roles.append(name)
        return role

    async def create_text_channel(
        self, name: str, *, overwrites=None, category=None, reason: str = "", **kwargs
    ) -> FakeChannel:
        if not self.allow_create:
            raise self._denied()
        channel = FakeChannel(self._take_id(), name, self, category=category)
        channel.overwrites = overwrites or {}
        self.text_channels.append(channel)
        self.created_channels.append(name)
        return channel

    async def create_category(
        self, name: str, *, overwrites=None, reason: str = "", **kwargs
    ) -> FakeCategory:
        if not self.allow_create:
            raise self._denied()
        category = FakeCategory(self._take_id(), name, self)
        category.overwrites = overwrites or {}
        self.categories.append(category)
        self.created_categories.append(name)
        return category


class FakeBot:
    def __init__(self, db: Database):
        self.db = db
        self._cogs: dict = {}

    def add_view(self, view) -> None:
        return None

    def get_cog(self, name: str):
        return self._cogs.get(name)

    def register(self, name: str, cog) -> None:
        self._cogs[name] = cog


async def make_world(tmp_path, **guild_flags):
    """Connected temp database + real Server/Tickets cogs on a fake guild."""
    db = Database(tmp_path / "setup.db")
    await db.connect()
    bot = FakeBot(db)
    tickets = Tickets(bot)
    server = Server(bot)
    bot.register("Tickets", tickets)
    guild = FakeGuild(**guild_flags)
    return db, bot, server, guild


# ------------------------------------------------------------ pure helpers
def test_find_role_by_name_is_case_insensitive_and_skips_everyone() -> None:
    guild = FakeGuild()
    guild.roles.append(FakeRole(10, "Moderator", position=2))
    assert find_role_by_name(guild, ("staff", "moderator")).id == 10
    # @everyone never matches even though its name is in the candidate list.
    assert find_role_by_name(guild, ("@everyone",)) is None
    assert find_role_by_name(guild, ("nobody",)) is None


def test_find_channel_by_name_is_case_insensitive() -> None:
    guild = FakeGuild()
    guild.text_channels.append(FakeChannel(20, "Mod-Logs", guild))
    assert find_channel_by_name(guild, ("mod-logs",)).id == 20
    assert find_channel_by_name(guild, ("welcome",)) is None


def test_auto_created_channel_overwrites() -> None:
    guild = FakeGuild()
    staff = FakeRole(11, "Staff")
    log = log_channel_overwrites(guild, staff)
    assert log[guild.default_role].view_channel is False, "logs must be hidden"
    assert log[staff].view_channel is True, "staff must see the logs"
    assert log[guild.me].send_messages is True

    welcome = welcome_channel_overwrites(guild)
    assert welcome[guild.default_role].view_channel is True, "welcome is public"
    assert welcome[guild.default_role].send_messages is False, "members don't post"
    assert welcome[guild.me].send_messages is True


# ------------------------------------------------------- the main behaviour
def test_zero_arg_setup_builds_a_bare_server(tmp_path) -> None:
    async def scenario() -> None:
        db, bot, server, guild = await make_world(tmp_path)
        outcome = await server._auto_setup(guild)
        settings = outcome.settings

        # Staff role created and configured.
        assert STAFF_ROLE_NAME in guild.created_roles
        assert settings["staff_role_id"] is not None
        staff = guild.get_role(settings["staff_role_id"])
        assert staff.name == STAFF_ROLE_NAME

        # Log channel created; all four log keys point at it.
        assert LOG_CHANNEL_NAME in guild.created_channels
        for key in ("mod_log_channel", "member_log_channel", "server_log_channel", "message_log_channel"):
            assert settings[key] == guild.text_channels[0].id

        # Welcome channel created and configured.
        assert WELCOME_CHANNEL_NAME in guild.created_channels
        assert settings["welcome_channel"] is not None

        # Auto role created.
        assert MEMBER_ROLE_NAME in guild.created_roles
        assert settings["autorole_id"] is not None

        # Full ticket system: category, panel channel, posted panel.
        assert CATEGORY_NAME in guild.created_categories
        assert PANEL_CHANNEL_NAME in guild.created_channels
        assert settings["ticket_category_id"] is not None
        assert settings["ticket_staff_role_id"] == settings["staff_role_id"]
        assert outcome.panel_message is not None
        assert outcome.panel_message.channel.name == PANEL_CHANNEL_NAME

        # Nothing failed, and the checklist is fully green.
        assert outcome.notes == []
        assert outcome.created, "the summary must report what was built"
        assert all(
            settings[key]
            for key in (
                "mod_log_channel",
                "welcome_channel",
                "staff_role_id",
                "autorole_id",
                "ticket_category_id",
                "ticket_staff_role_id",
            )
        )
        await db.close()

    asyncio.run(scenario())


def test_setup_reuses_existing_objects_instead_of_creating(tmp_path) -> None:
    async def scenario() -> None:
        db, bot, server, guild = await make_world(tmp_path)
        guild.roles.append(FakeRole(10, "Moderator", position=2))
        guild.roles.append(FakeRole(11, "Member", position=1))
        log = FakeChannel(20, "mod-logs", guild)
        welcome = FakeChannel(21, "welcome", guild)
        guild.text_channels.extend([log, welcome])

        outcome = await server._auto_setup(guild)
        settings = outcome.settings

        assert guild.created_roles == [], "existing staff role must be reused"
        assert LOG_CHANNEL_NAME not in guild.created_channels, "log channel reused"
        assert WELCOME_CHANNEL_NAME not in guild.created_channels, "welcome reused"
        assert settings["staff_role_id"] == 10
        assert settings["autorole_id"] == 11, "existing member role reused"
        assert settings["mod_log_channel"] == 20
        assert settings["welcome_channel"] == 21
        # The ticket system still gets built (that part was missing).
        assert PANEL_CHANNEL_NAME in guild.created_channels
        assert CATEGORY_NAME in guild.created_categories
        assert any("Staff role: Moderator" in item for item in outcome.reused)
        assert any("Log channel" in item for item in outcome.reused)
        assert outcome.notes == []
        await db.close()

    asyncio.run(scenario())


def test_setup_is_idempotent_second_run_builds_nothing(tmp_path) -> None:
    async def scenario() -> None:
        db, bot, server, guild = await make_world(tmp_path)
        await server._auto_setup(guild)
        first_roles = list(guild.created_roles)
        first_channels = list(guild.created_channels)
        first_categories = list(guild.created_categories)
        settings_before = dict(await db.get_guild(guild.id))
        panel = next(c for c in guild.text_channels if c.name == PANEL_CHANNEL_NAME)
        assert len(panel.messages) == 1

        outcome = await server._auto_setup(guild)

        assert guild.created_roles == first_roles, "no duplicate roles"
        assert guild.created_channels == first_channels, "no duplicate channels"
        assert guild.created_categories == first_categories, "no duplicate categories"
        assert len(panel.messages) == 1, "existing panel must be edited, not reposted"
        assert panel.messages[0].edits, "second run edits the stored panel"
        settings_after = dict(await db.get_guild(guild.id))
        assert settings_after["mod_log_channel"] == settings_before["mod_log_channel"]
        assert settings_after["staff_role_id"] == settings_before["staff_role_id"]
        assert outcome.notes == []
        await db.close()

    asyncio.run(scenario())


def test_setup_honours_explicit_options(tmp_path) -> None:
    async def scenario() -> None:
        db, bot, server, guild = await make_world(tmp_path)
        staff = FakeRole(30, "Helpers", position=2)
        guild.roles.append(staff)
        log = FakeChannel(31, "audit", guild)
        guild.text_channels.append(log)

        outcome = await server._auto_setup(
            guild, logchannel=log, staffrole=staff, welcomeembed=False
        )
        settings = outcome.settings

        assert settings["staff_role_id"] == 30, "explicit role wins"
        assert settings["mod_log_channel"] == 31, "explicit channel wins"
        assert settings["message_log_channel"] == 31
        assert settings["welcome_embed"] == 0, "welcomeembed=false is honoured"
        assert LOG_CHANNEL_NAME not in guild.created_channels, "no auto log channel"
        # Not passed → still auto-built.
        assert WELCOME_CHANNEL_NAME in guild.created_channels
        assert outcome.panel_message is not None
        await db.close()

    asyncio.run(scenario())


def test_setup_explains_missing_bot_permissions(tmp_path) -> None:
    async def scenario() -> None:
        db, bot, server, guild = await make_world(tmp_path, allow_create=False)
        guild.me.guild_permissions.manage_channels = False
        guild.me.guild_permissions.manage_roles = False

        outcome = await server._auto_setup(guild)  # must not raise
        settings = outcome.settings

        assert guild.created_channels == []
        assert guild.created_roles == []
        assert guild.created_categories == []
        assert settings["mod_log_channel"] is None
        assert settings["staff_role_id"] is None
        text = "\n".join(outcome.notes)
        assert "Manage Roles" in text, "the note must say which permission is missing"
        assert "Manage Channels" in text
        assert "Ticket setup failed" in text, "ticket creation failure is reported"
        await db.close()

    asyncio.run(scenario())


def test_setup_rejects_explicit_channel_the_bot_cannot_post_in(tmp_path) -> None:
    async def scenario() -> None:
        db, bot, server, guild = await make_world(tmp_path)
        mute = FakeChannel(40, "quiet", guild, send_messages=False, embed_links=False)
        guild.text_channels.append(mute)

        outcome = await server._auto_setup(guild, logchannel=mute)
        settings = outcome.settings

        assert settings["mod_log_channel"] is None, "unusable channel not configured"
        assert any("can't send/embed" in note for note in outcome.notes)
        # The rest of the setup still happened.
        assert WELCOME_CHANNEL_NAME in guild.created_channels
        assert outcome.panel_message is not None
        await db.close()

    asyncio.run(scenario())


# ------------------------------------------------------------ command level
def test_setup_command_defers_and_reports(tmp_path) -> None:
    async def scenario() -> None:
        db, bot, server, guild = await make_world(tmp_path)

        class FakeCtx:
            def __init__(self) -> None:
                self.guild = guild
                self.author = SimpleNamespace(id=7)
                self.deferred = False
                self.sent: list[dict] = []

            async def defer(self, *, ephemeral: bool = False) -> None:
                self.deferred = True

            async def send(self, content=None, *, embed=None, view=None, **kwargs) -> None:
                self.sent.append({"embed": embed, "view": view})

        ctx = FakeCtx()
        await Server.setup_one_shot.callback(server, ctx)

        assert ctx.deferred is True, "network work must acknowledge first"
        assert len(ctx.sent) == 1
        embed = ctx.sent[0]["embed"]
        fields = {f.name: f.value for f in embed.fields}
        assert "Checklist" in fields
        # Every item except nothing is green on a freshly built server.
        for line in fields["Checklist"].splitlines():
            assert line.startswith("✅"), line
        assert "Built for you" in fields, "the summary lists what it created"
        assert ctx.sent[0]["view"] is not None, "ticket role picker attached"
        await db.close()

    asyncio.run(scenario())


def test_setup_command_registers_all_optional_options() -> None:
    """Zero-arg invocation must be possible — every option is optional."""
    params = Server.setup_one_shot.params
    assert set(params) == {
        "logchannel",
        "welcomechannel",
        "staffrole",
        "autorole",
        "ticketcategory",
        "ticketstaff",
        "welcomeembed",
    }
    assert params["logchannel"].default is None
    assert params["welcomeembed"].default is True
