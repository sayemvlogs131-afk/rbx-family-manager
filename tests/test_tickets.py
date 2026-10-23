"""Ticket system tests: panel interface, persistent views, owner powers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
import pytest

from bot import COGS, RBXFamilyManager
from cogs.tickets import (
    ADD_USERS_CUSTOM_ID,
    CATEGORY_NAME,
    CLAIM_CUSTOM_ID,
    CLOSE_CUSTOM_ID,
    CREATE_CUSTOM_ID,
    DEFAULT_USER_LIMIT,
    MAX_USER_LIMIT,
    PANEL_CHANNEL_NAME,
    STAT_USER_LIMIT,
    TRANSCRIPT_CUSTOM_ID,
    CloseView,
    StaffRolePickerView,
    TicketCreateView,
    TicketReasonModal,
    build_panel_embed,
)
from utils.checks import MissingServerOwner
from utils.helpers import slugify


# --------------------------------------------------------------------- panel
def test_panel_embed_always_shows_who_can_see() -> None:
    """The interface must name the role that sees tickets."""

    class FakeRole:
        mention = "<@&4242>"

    embed = build_panel_embed(staff_role=FakeRole())  # type: ignore[arg-type]
    values = {f.name: f.value for f in embed.fields}
    assert "👀 Who can see your ticket" in values
    assert values["👀 Who can see your ticket"] == "<@&4242>"

    # No role configured: falls back to a sensible default, never blanks.
    embed2 = build_panel_embed()
    values2 = {f.name: f.value for f in embed2.fields}
    assert values2["👀 Who can see your ticket"] == "Only you and the server staff"
    assert values2["📂 Category"] == "Auto-created on open"


def test_panel_embed_shows_category_name() -> None:
    embed = build_panel_embed(category_name=CATEGORY_NAME)
    values = {f.name: f.value for f in embed.fields}
    assert values["📂 Category"] == f"**{CATEGORY_NAME}**"


# -------------------------------------------------------------------- modal
def test_modal_collects_subject_and_message() -> None:
    fields = vars(TicketReasonModal)
    assert "issue" in fields, "spec: the modal must collect the issue message"
    assert "subject" in fields, "the modal also collects a short subject"
    assert issubclass(TicketReasonModal, discord.ui.Modal)


def test_persistent_ticket_views_have_static_rbx_ids() -> None:
    """All persistent components survive restarts via fixed custom_ids."""

    async def scenario():
        for view in (TicketCreateView(), CloseView()):
            assert view.timeout is None, "persistent views must not time out"
            ids = [
                c.custom_id
                for c in view.children
                if isinstance(c, (discord.ui.Button, discord.ui.Select))
            ]
            assert ids, "view must expose components"
            assert all(i.startswith("rbx:") for i in ids), ids

        close_ids = {
            c.custom_id for c in CloseView().children if c.custom_id is not None
        }
        assert {CLOSE_CUSTOM_ID, CLAIM_CUSTOM_ID, TRANSCRIPT_CUSTOM_ID} <= close_ids
        assert CREATE_CUSTOM_ID in {
            c.custom_id for c in TicketCreateView().children
        }
        # The member picker is a UserSelect, still persistent.
        select = next(
            c for c in CloseView().children if isinstance(c, discord.ui.UserSelect)
        )
        assert select.custom_id == ADD_USERS_CUSTOM_ID

    asyncio.run(scenario())


def test_staff_role_picker_is_transient_and_owner_scoped() -> None:
    """Setup picker: non-persistent, rejects other users, releases itself."""

    class FakeMessage:
        id = 1234

        async def edit(self, **kwargs):
            pass

    cog = SimpleNamespace(_pickers={1234: "marker"})
    view = StaffRolePickerView(cog=cog, author_id=7, panel_message=FakeMessage())  # type: ignore[arg-type]
    assert view.timeout is not None, "picker must expire after setup"

    async def scenario():
        class FakeResponse:
            def __init__(self) -> None:
                self.sent: list[tuple] = []

            async def send_message(self, content, **kwargs):
                self.sent.append((content, kwargs))

        # Wrong user is rejected without raising (and told why).
        denied_resp = FakeResponse()
        denied = SimpleNamespace(user=SimpleNamespace(id=8), response=denied_resp)
        assert await view.interaction_check(denied) is False  # type: ignore[arg-type]
        assert denied_resp.sent and denied_resp.sent[0][1].get("ephemeral") is True

        allowed = SimpleNamespace(user=SimpleNamespace(id=7))
        assert await view.interaction_check(allowed) is True  # type: ignore[arg-type]

        # on_timeout drops the cog's strong reference (no leak).
        await view.on_timeout()
        assert 1234 not in cog._pickers

    asyncio.run(scenario())


# ------------------------------------------------------------------- helpers
def test_slugify_for_channel_names() -> None:
    assert slugify("I was wrongly punished!") == "i-was-wrongly-punished"
    assert slugify("  REPORT  Bug  ") == "report-bug"
    assert slugify("???") == "", "nothing usable -> empty (caller keeps ticket-N)"
    assert slugify("x" * 80, limit=30) == "x" * 30
    assert slugify("Cómo estás") == "c-m-est-s" or slugify("Cómo estás").startswith("c")


def test_user_limit_constants() -> None:
    assert 1 <= DEFAULT_USER_LIMIT <= MAX_USER_LIMIT
    assert STAT_USER_LIMIT.startswith("ticket_")
    assert PANEL_CHANNEL_NAME and CATEGORY_NAME


# ------------------------------------------------------------- owner gating
def test_owner_commands_registered_and_gated() -> None:
    """`/ticket owner ...` exists and every power requires the server owner."""

    async def scenario():
        bot = RBXFamilyManager()
        await bot._async_setup_hook()
        for extension in COGS:
            await bot.load_extension(extension)
        assert bot.startup_errors == []

        group = bot.tree.get_command("ticket")
        assert group is not None
        names = {c.name for c in getattr(group, "commands", [])}
        assert {"setup", "close", "add", "remove", "list", "owner"} <= names

        owner = next(c for c in group.commands if c.name == "owner")
        owner_names = {c.name for c in getattr(owner, "commands", [])}
        assert {"scan", "closeall", "purge", "limit"} <= owner_names

        # Each hybrid command carries the require_server_owner predicate.
        # NB: @commands.guild_only() contributes a *sync* predicate, so pick
        # the coroutine checks (require_server_owner is async) explicitly.
        import inspect

        for name in ("scan", "closeall", "purge", "limit"):
            cmd = bot.get_command(f"ticket owner {name}")
            assert cmd is not None, f"missing /ticket owner {name}"
            async_checks = [
                c for c in cmd.checks if inspect.iscoroutinefunction(c)
            ]
            assert async_checks, f"{name} has no async (owner) check"

            # And that check really blocks a non-owner.
            pred = async_checks[0]

            def ctx_for(author_id, owner_id=555, guild_id=1):
                return SimpleNamespace(
                    guild=(
                        SimpleNamespace(id=guild_id, owner_id=owner_id)
                        if guild_id
                        else None
                    ),
                    author=SimpleNamespace(id=author_id),
                )

            assert await pred(ctx_for(555)) is True
            with pytest.raises(MissingServerOwner):
                await pred(ctx_for(999))

        await bot.db.close()  # type: ignore[union-attr]

    asyncio.run(scenario())
