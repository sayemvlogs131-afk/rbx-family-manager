"""Bot startup tests: extensions load, commands register, help exists."""

from __future__ import annotations

import asyncio

import discord
import pytest

from bot import COGS, RBXFamilyManager
from utils.time_parser import parse_duration


@pytest.fixture(scope="module")
def loaded_bot() -> RBXFamilyManager:
    """Build a bot and load every cog (no network access required)."""

    async def scenario() -> RBXFamilyManager:
        bot = RBXFamilyManager()
        # Initialises asyncio primitives so background tasks can wait for
        # "ready" instead of raising (there is no gateway connection here).
        await bot._async_setup_hook()
        for extension in COGS:
            await bot.load_extension(extension)
        return bot

    return asyncio.run(scenario())


def test_bot_has_recovery_path(loaded_bot: RBXFamilyManager) -> None:
    """After restart the bot reconciles ticket refs - the cog exposes recover_server."""
    from cogs.tickets import Tickets

    tickets = loaded_bot.get_cog("Tickets")
    assert tickets is not None
    assert isinstance(tickets, Tickets)
    assert callable(tickets.recover_server)


def test_all_cogs_load(loaded_bot: RBXFamilyManager) -> None:
    assert loaded_bot.startup_errors == []
    for extension in COGS:
        assert extension in loaded_bot.extensions


def test_expected_commands_registered(loaded_bot: RBXFamilyManager) -> None:
    names = {c.name for c in loaded_bot.tree.get_commands()}
    required = {
        # moderation
        "ban", "unban", "kick", "timeout", "untimeout", "warn", "warnings",
        "clear", "purge", "slowmode", "lock", "unlock", "softban", "mute",
        "unmute", "nick", "massban", "modlogs",
        # automod / logging
        "automod", "setlogchannel", "logchannels",
        # giveaways
        "giveaway",
        # utility
        "help", "ping", "botinfo", "uptime", "serverinfo", "userinfo",
        "avatar", "banner", "roleinfo", "channelinfo", "permissions",
        "afk", "remind", "reminders", "timestamp", "calc", "poll",
        "embed", "announce", "invite",
        # fun
        "8ball", "coinflip", "dice", "roll", "choose", "rate", "ship",
        "roast", "compliment", "joke", "meme",
        # server
        "setup", "welcome", "autorole", "staffrole", "ticket", "config",
        "command", "ignore", "ignores",
        # owner
        "owner",
    }
    missing = required - names
    assert not missing, f"missing commands: {sorted(missing)}"


def test_logging_covers_required_events(loaded_bot: RBXFamilyManager) -> None:
    """Every event the spec asks for must have a listener."""
    cog = loaded_bot.get_cog("Logging")
    assert cog is not None
    listeners = {item[0] for item in cog.get_listeners()}
    required = {
        "on_message_delete",
        "on_message_delete_bulk",
        "on_message_edit",
        "on_member_join",
        "on_member_remove",
        "on_member_ban",
        "on_member_unban",
        "on_member_update",   # nicknames, timeouts, roles
        "on_guild_role_create",
        "on_guild_role_delete",
        "on_guild_role_update",
        "on_guild_channel_create",
        "on_guild_channel_delete",
        "on_guild_channel_update",
        "on_voice_state_update",
    }
    missing = required - listeners
    assert not missing, f"missing log listeners: {sorted(missing)}"


def test_context_menus_registered(loaded_bot: RBXFamilyManager) -> None:
    """Right-click (context) commands must be registered."""
    from discord import app_commands

    menus = [
        c
        for c in loaded_bot.tree.get_commands()
        if isinstance(c, app_commands.ContextMenu)
    ]
    by_name = {m.name: m for m in menus}
    assert {"User Info", "Warn member"} <= set(by_name)
    for menu in by_name.values():
        assert menu.type == discord.enums.AppCommandType.user, (
            f"{menu.name} must target users"
        )


def test_persistent_views_use_fixed_custom_ids() -> None:
    """Buttons that must survive restarts need a static custom_id + no timeout."""
    import discord

    from cogs.giveaways import GIVEAWAY_VIEW_CUSTOM_ID, GiveawayView
    from cogs.tickets import CLOSE_CUSTOM_ID, CREATE_CUSTOM_ID, CloseView, TicketCreateView

    async def scenario():
        cases = (
            (GiveawayView(), GIVEAWAY_VIEW_CUSTOM_ID),
            (TicketCreateView(), CREATE_CUSTOM_ID),
            (CloseView(), CLOSE_CUSTOM_ID),
        )
        for view, expected in cases:
            assert view.timeout is None, "persistent views must not time out"
            ids = [c.custom_id for c in view.children if isinstance(c, discord.ui.Button)]
            assert expected in ids, f"missing persistent custom_id {expected}"
            assert all(i.startswith("rbx:") for i in ids)

    asyncio.run(scenario())


def test_modals_exist_for_warn_and_ticket() -> None:
    """Spec requires modals: warn reason + ticket issue description."""
    from cogs.moderation import WarnModal
    from cogs.tickets import TicketReasonModal

    assert "reason" in vars(WarnModal), "WarnModal must collect a reason"
    assert "issue" in vars(TicketReasonModal), "Ticket modal must collect the issue"
    assert issubclass(WarnModal, discord.ui.Modal)
    assert issubclass(TicketReasonModal, discord.ui.Modal)


def test_help_select_callback_works_despite_button_ordering() -> None:
    """Regression: HelpView looked up children[0], which is a button.

    Decorated buttons are registered before programmatically added items,
    so the select must be kept as an explicit reference.
    """
    from cogs.utility import HelpView

    class FakeResponse:
        def __init__(self) -> None:
            self.kwargs: dict | None = None
            self.deferred = False

        async def edit_message(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def defer(self) -> None:
            self.deferred = True

    class FakeInteraction:
        def __init__(self, values: list[str] | None = None) -> None:
            self.response = FakeResponse()
            self.data = {"values": values} if values is not None else {}

    async def scenario() -> None:
        bot = RBXFamilyManager()
        await bot._async_setup_hook()
        view = HelpView(bot, 123)

        # The select is NOT the first child - that is the bug this guards.
        assert isinstance(view.select, discord.ui.Select)
        assert view.select in view.children
        assert view.children[0] is not view.select

        interaction = FakeInteraction(["\u2699\ufe0f Server"])
        await view.select.callback(interaction)  # type: ignore[misc]
        assert interaction.response.kwargs is not None
        assert "embed" in interaction.response.kwargs

        # No values at all -> defer instead of raising IndexError.
        empty = FakeInteraction(None)
        await view.select.callback(empty)  # type: ignore[misc]
        assert empty.response.deferred is True

    asyncio.run(scenario())


def test_giveaway_subcommands(loaded_bot: RBXFamilyManager) -> None:
    group = loaded_bot.tree.get_command("giveaway")
    assert group is not None
    subcommands = {c.name for c in getattr(group, "commands", [])}
    assert {"start", "end", "reroll", "list", "cancel", "easy"} <= subcommands


def test_help_command_has_options(loaded_bot: RBXFamilyManager) -> None:
    help_cmd = loaded_bot.tree.get_command("help")
    assert help_cmd is not None
    assert help_cmd.description


def test_owner_group_subcommands(loaded_bot: RBXFamilyManager) -> None:
    group = loaded_bot.tree.get_command("owner")
    subcommands = {c.name for c in getattr(group, "commands", [])}
    assert {"reload", "sync", "shutdown", "guilds", "status", "say", "broadcast"} <= subcommands


def test_ban_command_has_reason_parameter(loaded_bot: RBXFamilyManager) -> None:
    ban = loaded_bot.tree.get_command("ban")
    params = {p.name for p in ban.parameters}
    assert {"user", "reason"} <= params


def test_timeout_command_has_duration(loaded_bot: RBXFamilyManager) -> None:
    timeout = loaded_bot.tree.get_command("timeout")
    params = {p.name for p in timeout.parameters}
    assert {"user", "duration", "reason"} <= params


def test_giveaway_start_parameters(loaded_bot: RBXFamilyManager) -> None:
    group = loaded_bot.tree.get_command("giveaway")
    start = next(c for c in group.commands if c.name == "start")
    params = {p.name for p in start.parameters}
    assert {"duration", "winners", "prize", "channel"} <= params


def test_duration_limits_for_timeouts() -> None:
    # 28 days is the Discord maximum, 40 days must be rejected.
    assert parse_duration("28d", max_seconds=28 * 86400) == 28 * 86400
    with pytest.raises(Exception):
        parse_duration("40d", max_seconds=28 * 86400)


def test_bot_identity_constants() -> None:
    import config

    assert config.BOT_NAME == "RBX Family Manager"
    assert "RBX Family" in config.BOT_DESCRIPTION
    assert config.DEFAULT_STATUS_TEXT == "RBX Family | /help"
    assert config.FOOTER_TEXT.startswith("RBX Family Manager")
