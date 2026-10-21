"""🎉 Giveaway system for RBX Family Manager.

Guarantees:

* every giveaway lives in SQLite, so entries and deadlines survive restarts
* the entry button is a **persistent view** re-registered on boot
* a background loop closes finished giveaways, picks winners with
  :func:`secrets.SystemRandom` (cryptographically strong) and announces them
* eligibility rules (role, account age, server tenure) are re-checked at draw
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.checks import require_moderator, require_permissions
from utils.embeds import Embeds
from utils.helpers import truncate
from utils.time_parser import format_duration, parse_duration

log = logging.getLogger("rbx.giveaways")

GIVEAWAY_VIEW_CUSTOM_ID = "rbx:giveaway:enter"

# Emoji guests join by reacting with this on the giveaway message.
# Users who react are entered the same way as button clickers.
GIVEAWAY_REACTION_EMOJI = "🎉"

# When this is set, the cog listens for raw reactions on giveaway messages and
# enters anyone who reacts with the emoji above (the same way the button does).
# Set it to ``None`` to disable reaction joining and rely on the buttons only.
GIVEAWAY_REACTION_MODE: str | None = "add"  # "add" | "toggle" | None

# A tiny persistent button lets people also click the emoji if they prefer.
GIVEAWAY_REACTION_BUTTON_CUSTOM_ID = "rbx:giveaway:emoji_enter"


class GiveawayEmojiJoinView(discord.ui.View):
    """Second persistent view exposes the emoji as a clickable button too.

    Kept separate from the main entry button so the emoji is always reachable
    even on clients that surface reactions badly.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="",
        emoji=GIVEAWAY_REACTION_EMOJI,
        style=discord.ButtonStyle.secondary,
        custom_id=GIVEAWAY_REACTION_BUTTON_CUSTOM_ID,
    )
    async def emoji_enter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        # Route the emoji-click through the same entry flow the main button uses.
        alt_view = GiveawayView(interaction.message.id if interaction.message is not None else None)
        await alt_view.emoji_enter(interaction, _)
MIN_GIVEAWAY_SECONDS = 30
MAX_GIVEAWAY_SECONDS = 30 * 86400


class GiveawayView(discord.ui.View):
    """Persistent entry button — survives restarts because of ``custom_id``.

    The giveaway is resolved from the message the button lives on, so one
    registered view serves every giveaway.
    """

    def __init__(self, giveaway_id: int | None = None) -> None:
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    async def _resolve(self, interaction: discord.Interaction) -> dict[str, Any] | None:
        bot = interaction.client
        assert isinstance(bot, commands.Bot)
        if self.giveaway_id is not None:
            return await bot.db.get_giveaway(self.giveaway_id)
        if interaction.message is None:
            return None
        return await bot.db.get_giveaway_by_message(
            interaction.message.channel.id, interaction.message.id
        )

    @discord.ui.button(
        label="Enter Giveaway",
        emoji="🎉",
        style=discord.ButtonStyle.primary,
        custom_id=GIVEAWAY_VIEW_CUSTOM_ID,
    )
    async def enter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handle_enter(interaction)

    @discord.ui.button(
        label="",
        emoji=GIVEAWAY_REACTION_EMOJI,
        style=discord.ButtonStyle.secondary,
        custom_id=GIVEAWAY_REACTION_BUTTON_CUSTOM_ID,
    )
    async def emoji_enter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._handle_enter(interaction)

    async def _handle_enter(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        assert isinstance(bot, commands.Bot)
        db = bot.db

        giveaway = await self._resolve(interaction)
        if giveaway is None:
            await interaction.response.send_message(
                "This giveaway no longer exists.", ephemeral=True
            )
            return
        if giveaway["ended"] or giveaway["cancelled"]:
            await interaction.response.send_message(
                "This giveaway has already ended.", ephemeral=True
            )
            return
        if giveaway["ends_at"] <= int(datetime.now(timezone.utc).timestamp()):
            await interaction.response.send_message(
                "This giveaway is ending right now — it is too late to enter.",
                ephemeral=True,
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member) or member.guild.id != giveaway["guild_id"]:
            await interaction.response.send_message(
                "You can only enter from the server hosting the giveaway.", ephemeral=True
            )
            return
        if member.bot:
            return

        # ---- eligibility ------------------------------------------------
        reason = await _eligibility_failure(member, giveaway)
        if reason:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        added = await db.add_giveaway_entry(int(giveaway["id"]), member.id)
        if not added:
            await interaction.response.send_message(
                "You have already entered this giveaway! 🎉", ephemeral=True
            )
            return

        count = await db.giveaway_entry_count(int(giveaway["id"]))
        await interaction.response.send_message(
            f"🎉 You are entered! There are now **{count}** participant(s).", ephemeral=True
        )
        await _refresh_entry_count(bot, giveaway, count)


async def _eligibility_failure(
    member: discord.Member, giveaway: dict[str, Any]
) -> str | None:
    """Return a human reason the member may not enter, or ``None``."""
    required_role = giveaway.get("required_role_id")
    if required_role:
        role = member.guild.get_role(int(required_role))
        if role is not None and role.id not in {r.id for r in member.roles}:
            return f"You need the {role.mention} role to enter this giveaway."

    min_account_age = int(giveaway.get("min_account_age") or 0)
    if min_account_age:
        age = (datetime.now(timezone.utc) - member.created_at).total_seconds()
        if age < min_account_age:
            return (
                f"Your Discord account must be at least "
                f"**{format_duration(min_account_age)}** old to enter."
            )

    min_server_age = int(giveaway.get("min_server_age") or 0)
    if min_server_age:
        joined = member.joined_at or datetime.now(timezone.utc)
        tenure = (datetime.now(timezone.utc) - joined).total_seconds()
        if tenure < min_server_age:
            return (
                f"You must have been in this server for at least "
                f"**{format_duration(min_server_age)}** to enter."
            )
    return None


async def _refresh_entry_count(
    bot: commands.Bot, giveaway: dict[str, Any], count: int
) -> None:
    """Best-effort live update of the entry counter in the embed."""
    try:
        channel = bot.get_channel(int(giveaway["channel_id"]))
        if channel is None:
            return
        if not giveaway.get("message_id"):
            return
        message = await channel.fetch_message(int(giveaway["message_id"]))
        if not message.embeds:
            return
        embed = message.embeds[0]
        for index, field in enumerate(embed.fields):
            if field.name == "Entries":
                embed.set_field_at(index, name="Entries", value=str(count), inline=True)
                break
        await message.edit(embed=embed)
    except discord.HTTPException:
        log.debug("Could not refresh giveaway entry count", exc_info=True)


def build_giveaway_embed(
    giveaway: dict[str, Any], entries: int, *, ended: bool = False, winners: list[int] | None = None
) -> discord.Embed:
    embed = Embeds.giveaway(
        prize=giveaway["prize"],
        description=giveaway.get("description"),
        winners=int(giveaway["winners_count"]),
        ends_at=int(giveaway["ends_at"]),
        host_id=int(giveaway["host_id"]),
        entries=entries,
        ended=ended,
        winners_list=winners,
    )
    embed.set_footer(text=f"RBX Family Manager • Giveaway #{giveaway['id']}")
    requirements: list[str] = []
    if giveaway.get("required_role_id"):
        requirements.append(f"Role: <@&{giveaway['required_role_id']}>")
    if giveaway.get("min_account_age"):
        requirements.append(
            f"Account age: {format_duration(giveaway['min_account_age'])}"
        )
    if giveaway.get("min_server_age"):
        requirements.append(
            f"Server tenure: {format_duration(giveaway['min_server_age'])}"
        )
    if requirements:
        embed.add_field(name="Requirements", value="\n".join(requirements), inline=False)
    return embed


async def draw_winners(
    entries: list[int], winner_count: int, *, seed: Any = None
) -> list[int]:
    """Pick unique winners with a cryptographically strong random source."""
    if not entries:
        return []
    rng = secrets.SystemRandom() if seed is None else seed
    pool = list(dict.fromkeys(entries))  # de-duplicate defensively
    rng.shuffle(pool)
    return pool[: max(1, winner_count)]


class Giveaways(commands.Cog, name="Giveaways"):
    """Giveaway creation, management and automatic drawing."""

    category = "🎉 Giveaways"
    category_description = "Create, run and draw giveaways with buttons."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.GIVEAWAY_REACTION_MODE = GIVEAWAY_REACTION_MODE
        self.watch_giveaways.start()
        self._register_persistent_views()

    def _register_persistent_views(self) -> None:
        # One view instance serves every giveaway (resolved from the message).
        self.bot.add_view(GiveawayView())
        if self.GIVEAWAY_REACTION_MODE is not None:
            self.bot.add_view(GiveawayEmojiJoinView())

    # ------------------------------------------------------- reaction join
    async def _reaction_join_enabled(self, guild_id: int) -> bool:
        """Per-server toggle: react with the giveaway emoji to enter."""
        try:
            settings = await self.bot.db.get_guild(guild_id)
        except Exception:  # pragma: no cover - defensive: never break on settings read
            log.debug("Could not read giveaway reaction setting", exc_info=True)
            return True
        return bool(settings.get("giveaway_react_join", 1))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Enter anyone who reacts with 🎉 on an active giveaway message."""
        if GIVEAWAY_REACTION_MODE is None:
            return
        if payload.emoji.name != GIVEAWAY_REACTION_EMOJI:
            return
        if payload.guild_id is None:
            return
        if not await self._reaction_join_enabled(payload.guild_id):
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return
        giveaway = await self.bot.db.get_giveaway_by_message(
            payload.channel_id, payload.message_id
        )
        if giveaway is None:
            return
        if giveaway["ended"] or giveaway["cancelled"]:
            return
        member = payload.member
        if member is None or getattr(member, "bot", False):
            return
        if getattr(getattr(member, "guild", None), "id", None) != giveaway["guild_id"]:
            return
        reason = await _eligibility_failure(member, giveaway)
        if reason:
            return
        added = await self.bot.db.add_giveaway_entry(int(giveaway["id"]), member.id)
        if not added:
            return
        count = await self.bot.db.giveaway_entry_count(int(giveaway["id"]))
        await _refresh_entry_count(self.bot, giveaway, count)

    # ------------------------------------------------------------- helpers
    async def _resolve_channel(
        self, ctx: commands.Context, channel: discord.abc.GuildChannel
    ) -> discord.TextChannel | None:
        if not isinstance(channel, discord.TextChannel):
            await ctx.send(
                embed=Embeds.error("Invalid channel", "Giveaways need a text channel."),
                ephemeral=True,
            )
            return None
        if not channel.permissions_for(ctx.guild.me).send_messages:
            await ctx.send(
                embed=Embeds.error(
                    "Missing permission", f"I cannot send messages in {channel.mention}."
                ),
                ephemeral=True,
            )
            return None
        return channel

    # ------------------------------------------------------------ commands
    @commands.hybrid_group(name="giveaway", invoke_without_command=True)
    @commands.guild_only()
    async def giveaway(self, ctx: commands.Context) -> None:
        """Giveaway management. Use the subcommands: start, end, reroll, list, cancel."""
        await ctx.send(
            embed=Embeds.info(
                "Giveaways",
                "**Commands**\n"
                "`/giveaway easy` — create a giveaway the easy way\n"
                "`/giveaway start` — create a giveaway with full options\n"
                "`/giveaway end` — end one early\n"
                "`/giveaway reroll` — draw new winners\n"
                "`/giveaway list` — see active giveaways\n"
                "`/giveaway cancel` — cancel without a winner\n"
                "`/giveaway reactions` — react-to-join on/off",
            ),
            ephemeral=True,
        )

    @giveaway.command(name="reactions")
    @commands.guild_only()
    @require_permissions("manage_guild")
    @app_commands.describe(enabled="Turn react-to-join on or off (omit to view current state)")
    async def giveaway_reactions(
        self, ctx: commands.Context, enabled: Optional[bool] = None
    ) -> None:
        """Per-server toggle: members react with 🎉 to enter giveaways."""
        if ctx.guild is None:
            return
        if enabled is None:
            settings = await self.bot.db.get_guild(ctx.guild.id)
            state = "on" if settings.get("giveaway_react_join", 1) else "off"
            await ctx.send(
                embed=Embeds.info(
                    "Giveaway reactions",
                    f"React-to-join is currently **{state}** in this server.\n"
                    "Change it with `/giveaway reactions enabled: true` or `enabled: false`.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.update_guild(ctx.guild.id, giveaway_react_join=1 if enabled else 0)
        await ctx.send(
            embed=Embeds.success(
                "Giveaway reactions",
                "Members can now react with 🎉 on a giveaway to enter it."
                if enabled
                else "Reaction joining is off. Members can still enter with the button.",
            )
        )

    @giveaway.command(name="easy")
    @commands.guild_only()
    @require_permissions("manage_guild")
    @commands.cooldown(2, 30, commands.BucketType.guild)
    @app_commands.describe(
        prize="What is being given away",
        winners="Number of winners (1-20, default 1)",
        duration="How long it runs (e.g. 10m, 1h, 1d, default 1h)",
        channel="Where to post it (defaults to here)",
    )
    async def giveaway_easy(
        self,
        ctx: commands.Context,
        prize: str,
        winners: app_commands.Range[int, 1, 20] = 1,
        duration: str = "1h",
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """Create a giveaway the easy way — only prize is required."""
        target_channel = channel or ctx.channel
        resolved = await self._resolve_channel(ctx, target_channel)
        if resolved is None:
            return

        try:
            seconds = parse_duration(duration, max_seconds=MAX_GIVEAWAY_SECONDS)
            if seconds < MIN_GIVEAWAY_SECONDS:
                raise ValueError("Giveaways must run for at least 30 seconds.")
        except ValueError as exc:
            await ctx.send(embed=Embeds.error("Invalid duration", str(exc)), ephemeral=True)
            return

        ends_at = int((datetime.now(timezone.utc) + timedelta(seconds=seconds)).timestamp())
        giveaway_id = await self.bot.db.create_giveaway(
            guild_id=ctx.guild.id,
            channel_id=resolved.id,
            host_id=ctx.author.id,
            prize=truncate(prize, 250),
            ends_at=ends_at,
            winners_count=winners,
        )

        embed = build_giveaway_embed(
            {
                "id": giveaway_id,
                "host_id": ctx.author.id,
                "prize": prize,
                "description": None,
                "winners_count": winners,
                "ends_at": ends_at,
                "required_role_id": None,
                "min_account_age": 0,
                "min_server_age": 0,
            },
            entries=0,
        )
        view = GiveawayView(giveaway_id)
        try:
            message = await resolved.send(
                content=f"🎉 **GIVEAWAY** — {truncate(prize, 150)}",
                embed=embed,
                view=view,
            )
        except discord.HTTPException as exc:
            await self.bot.db.delete_giveaway(giveaway_id)
            await ctx.send(
                embed=Embeds.error("Could not post", f"Discord rejected the message: {exc.status}"),
                ephemeral=True,
            )
            return

        await self.bot.db.set_giveaway_message(giveaway_id, message.id)

        if ctx.guild:
            await self.bot.log_to_channel(
                ctx.guild,
                "server_log_channel",
                Embeds.log(
                    "Giveaway started",
                    "",
                    color=config.GIVEAWAY_COLOR,
                    fields=[
                        ("Prize", truncate(prize, 200), True),
                        ("Winners", str(winners), True),
                        ("Duration", format_duration(seconds), True),
                        ("Channel", resolved.mention, True),
                    ],
                    actor=ctx.author,
                ),
            )

        await ctx.send(
            embed=Embeds.success(
                "Giveaway started",
                f"Posted in {resolved.mention} — ends "
                f"<t:{ends_at}:R>.\nGiveaway ID: `{giveaway_id}`\n"
                "People can enter by clicking the 🎉 button or by reacting with 🎉 on that message.",
            ),
            ephemeral=True,
        )

    @giveaway.command(name="start")
    @commands.guild_only()
    @require_permissions("manage_guild")
    @commands.cooldown(2, 30, commands.BucketType.guild)
    @app_commands.describe(
        duration="How long it runs (e.g. 10m, 1h, 1d)",
        winners="Number of winners (1-20)",
        prize="What is being given away",
        channel="Where to post it (defaults to here)",
        description="Extra details shown in the embed",
        role_requirement="Role required to enter",
        min_account_age="Minimum account age (e.g. 7d)",
        min_server_age="Minimum server membership time (e.g. 1d)",
    )
    async def giveaway_start(
        self,
        ctx: commands.Context,
        duration: str,
        winners: app_commands.Range[int, 1, 20],
        prize: str,
        channel: Optional[discord.TextChannel] = None,
        description: Optional[str] = None,
        role_requirement: Optional[discord.Role] = None,
        min_account_age: Optional[str] = None,
        min_server_age: Optional[str] = None,
    ) -> None:
        """Start a giveaway with buttons, requirements and automatic drawing."""
        target_channel = channel or ctx.channel
        resolved = await self._resolve_channel(ctx, target_channel)
        if resolved is None:
            return

        try:
            seconds = parse_duration(
                duration, max_seconds=MAX_GIVEAWAY_SECONDS
            )
            if seconds < MIN_GIVEAWAY_SECONDS:
                raise ValueError("Giveaways must run for at least 30 seconds.")
        except ValueError as exc:
            await ctx.send(embed=Embeds.error("Invalid duration", str(exc)), ephemeral=True)
            return

        account_age = 0
        server_age = 0
        try:
            if min_account_age:
                account_age = parse_duration(min_account_age)
            if min_server_age:
                server_age = parse_duration(min_server_age)
        except ValueError as exc:
            await ctx.send(embed=Embeds.error("Invalid duration", str(exc)), ephemeral=True)
            return

        if role_requirement is not None and ctx.guild.me.top_role <= role_requirement:
            await ctx.send(
                embed=Embeds.error(
                    "Role hierarchy",
                    "My highest role must be above the required role so I can verify it.",
                ),
                ephemeral=True,
            )
            return

        ends_at = int((datetime.now(timezone.utc) + timedelta(seconds=seconds)).timestamp())
        giveaway_id = await self.bot.db.create_giveaway(
            guild_id=ctx.guild.id,
            channel_id=resolved.id,
            host_id=ctx.author.id,
            prize=truncate(prize, 250),
            ends_at=ends_at,
            winners_count=winners,
            description=truncate(description, 900) if description else None,
            required_role_id=role_requirement.id if role_requirement else None,
            min_account_age=account_age,
            min_server_age=server_age,
        )

        embed = build_giveaway_embed(
            {
                "id": giveaway_id,
                "host_id": ctx.author.id,
                "prize": prize,
                "description": description,
                "winners_count": winners,
                "ends_at": ends_at,
                "required_role_id": role_requirement and role_requirement.id,
                "min_account_age": account_age,
                "min_server_age": server_age,
            },
            entries=0,
        )
        view = GiveawayView(giveaway_id)
        try:
            message = await resolved.send(
                content=f"🎉 **GIVEAWAY** — {truncate(prize, 150)}",
                embed=embed,
                view=view,
            )
        except discord.HTTPException as exc:
            await self.bot.db.delete_giveaway(giveaway_id)
            await ctx.send(
                embed=Embeds.error("Could not post", f"Discord rejected the message: {exc.status}"),
                ephemeral=True,
            )
            return

        await self.bot.db.set_giveaway_message(giveaway_id, message.id)

        if ctx.guild:
            await self.bot.log_to_channel(
                ctx.guild,
                "server_log_channel",
                Embeds.log(
                    "Giveaway started",
                    "",
                    color=config.GIVEAWAY_COLOR,
                    fields=[
                        ("Prize", truncate(prize, 200), True),
                        ("Winners", str(winners), True),
                        ("Duration", format_duration(seconds), True),
                        ("Channel", resolved.mention, True),
                    ],
                    actor=ctx.author,
                ),
            )

        await ctx.send(
            embed=Embeds.success(
                "Giveaway started",
                f"Posted in {resolved.mention} — ends "
                f"<t:{ends_at}:R>.\nGiveaway ID: `{giveaway_id}`",
            ),
            ephemeral=True,
        )

    @giveaway.command(name="end")
    @commands.guild_only()
    @require_moderator()
    @app_commands.describe(id="Giveaway ID (from /giveaway list)")
    async def giveaway_end(self, ctx: commands.Context, id: int) -> None:  # noqa: A002
        """End a giveaway early and draw winners now."""
        giveaway = await self.bot.db.get_giveaway(id)
        if giveaway is None or giveaway["guild_id"] != ctx.guild.id:
            await ctx.send(embed=Embeds.error("Not found", "Unknown giveaway ID."), ephemeral=True)
            return
        if giveaway["ended"] or giveaway["cancelled"]:
            await ctx.send(
                embed=Embeds.warning("Already finished", "That giveaway is already over."),
                ephemeral=True,
            )
            return
        await ctx.send(embed=Embeds.success("Ending…", "Drawing winners now."))
        await self._finish_giveaway(giveaway)

    @giveaway.command(name="reroll")
    @commands.guild_only()
    @require_moderator()
    @app_commands.describe(id="Giveaway ID to redraw")
    async def giveaway_reroll(self, ctx: commands.Context, id: int) -> None:  # noqa: A002
        """Draw new winners for a finished giveaway."""
        giveaway = await self.bot.db.get_giveaway(id)
        if giveaway is None or giveaway["guild_id"] != ctx.guild.id:
            await ctx.send(embed=Embeds.error("Not found", "Unknown giveaway ID."), ephemeral=True)
            return
        if not giveaway["ended"]:
            await ctx.send(
                embed=Embeds.error("Not finished", "End the giveaway before rerolling."),
                ephemeral=True,
            )
            return
        entries = await self.bot.db.giveaway_entries(id)
        if not entries:
            await ctx.send(
                embed=Embeds.error("No entries", "Nobody entered — nothing to redraw."),
                ephemeral=True,
            )
            return
        winners = await draw_winners(entries, int(giveaway["winners_count"]))
        await self.bot.db.finish_giveaway(id, winners, cancelled=bool(giveaway["cancelled"]))
        await self._announce_winners(giveaway, winners, reroll=True)
        await ctx.send(embed=Embeds.success("Rerolled", "New winner(s): " + _fmt(winners)))

    @giveaway.command(name="list")
    @commands.guild_only()
    @require_moderator()
    async def giveaway_list(self, ctx: commands.Context) -> None:
        """List active giveaways in this server."""
        rows = await self.bot.db.guild_giveaways(ctx.guild.id)
        if not rows:
            await ctx.send(
                embed=Embeds.info("Giveaways", "No active giveaways. Use `/giveaway easy` or `/giveaway start`."),
                ephemeral=True,
            )
            return
        lines = [
            f"**#{r['id']}** • {r['prize']} • <#{r['channel_id']}> • "
            f"ends <t:{r['ends_at']}:R> • {int(r['winners_count'])} winner(s)"
            for r in rows[:15]
        ]
        await ctx.send(embed=Embeds.command_list(lines, "🎉 Active giveaways"))

    @giveaway.command(name="cancel")
    @commands.guild_only()
    @require_moderator()
    @app_commands.describe(id="Giveaway ID to cancel", reason="Why")
    async def giveaway_cancel(
        self, ctx: commands.Context, id: int, reason: str = "No reason provided"  # noqa: A002
    ) -> None:
        """Cancel a giveaway without picking winners."""
        giveaway = await self.bot.db.get_giveaway(id)
        if giveaway is None or giveaway["guild_id"] != ctx.guild.id:
            await ctx.send(embed=Embeds.error("Not found", "Unknown giveaway ID."), ephemeral=True)
            return
        if giveaway["ended"] or giveaway["cancelled"]:
            await ctx.send(
                embed=Embeds.warning("Already finished", "That giveaway is already over."),
                ephemeral=True,
            )
            return
        await self.bot.db.finish_giveaway(id, [], cancelled=True)
        embed = build_giveaway_embed(giveaway, 0, ended=True, winners=[])
        embed.title = "🎉 GIVEAWAY CANCELLED"
        embed.add_field(name="Cancelled by", value=ctx.author.mention, inline=False)
        embed.add_field(name="Reason", value=truncate(reason, 300), inline=False)
        await self._edit_giveaway_message(giveaway, embed, view=None)
        if ctx.guild:
            await self.bot.log_to_channel(
                ctx.guild,
                "server_log_channel",
                Embeds.log(
                    "Giveaway cancelled",
                    "",
                    fields=[("Prize", giveaway["prize"], True), ("Reason", truncate(reason, 200), False)],
                    actor=ctx.author,
                ),
            )
        await ctx.send(embed=Embeds.success("Giveaway cancelled", f"Giveaway `#{id}` cancelled."))

    @giveaway.command(name="info")
    @commands.guild_only()
    @require_moderator()
    @app_commands.describe(id="Giveaway ID")
    async def giveaway_info(self, ctx: commands.Context, id: int) -> None:  # noqa: A002
        """Show details for one giveaway."""
        giveaway = await self.bot.db.get_giveaway(id)
        if giveaway is None or giveaway["guild_id"] != ctx.guild.id:
            await ctx.send(embed=Embeds.error("Not found", "Unknown giveaway ID."), ephemeral=True)
            return
        entries = await self.bot.db.giveaway_entry_count(id)
        embed = build_giveaway_embed(
            giveaway, entries, ended=bool(giveaway["ended"]), winners=_load_ids(giveaway["winners"])
        )
        embed.add_field(
            name="Status",
            value="ended" if giveaway["ended"] else ("cancelled" if giveaway["cancelled"] else "active"),
            inline=True,
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------- drawing
    async def _edit_giveaway_message(
        self,
        giveaway: dict[str, Any],
        embed: discord.Embed,
        view: discord.ui.View | None = None,
    ) -> None:
        try:
            channel = self.bot.get_channel(int(giveaway["channel_id"]))
            if channel is None or not giveaway.get("message_id"):
                return
            message = await channel.fetch_message(int(giveaway["message_id"]))
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            log.debug("Could not edit giveaway message", exc_info=True)

    async def _announce_winners(
        self, giveaway: dict[str, Any], winners: list[int], *, reroll: bool = False
    ) -> None:
        channel = self.bot.get_channel(int(giveaway["channel_id"]))
        if channel is None:
            return
        if winners:
            text = (
                (f"🎊 Reroll! New winner(s): {_fmt(winners)}" if reroll else
                 f"🎊 Congratulations! Winner(s): {_fmt(winners)}")
                + f" — you won **{giveaway['prize']}**!"
            )
        else:
            text = (
                f"The giveaway for **{giveaway['prize']}** ended with no eligible "
                "entries, so no winner was drawn."
            )
        try:
            await channel.send(
                content=text,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.HTTPException:
            log.debug("Could not announce winners", exc_info=True)

        await self.bot.log_to_channel(
            channel.guild,
            "server_log_channel",
            Embeds.log(
                "Giveaway ended" if not reroll else "Giveaway rerolled",
                "",
                color=config.GIVEAWAY_COLOR,
                fields=[
                    ("Prize", giveaway["prize"], True),
                    ("Winners", _fmt(winners) or "`none`", True),
                    ("Channel", channel.mention, True),
                ],
            ),
        )

    async def _finish_giveaway(self, giveaway: dict[str, Any]) -> None:
        """Draw winners, persist them, update the message and announce."""
        entries = await self.bot.db.giveaway_entries(int(giveaway["id"]))
        winners = await draw_winners(entries, int(giveaway["winners_count"]))
        await self.bot.db.finish_giveaway(int(giveaway["id"]), winners)

        embed = build_giveaway_embed(
            giveaway, len(entries), ended=True, winners=winners
        )
        await self._edit_giveaway_message(giveaway, embed, view=None)
        await self._announce_winners(giveaway, winners)

    # ------------------------------------------------------------ background
    @tasks.loop(seconds=20)
    async def watch_giveaways(self) -> None:
        """Close finished giveaways even after a restart."""
        try:
            now = int(datetime.now(timezone.utc).timestamp())
            for giveaway in await self.bot.db.active_giveaways():
                if int(giveaway["ends_at"]) > now:
                    continue
                try:
                    await self._finish_giveaway(giveaway)
                except Exception:
                    log.exception("Failed to finish giveaway %s", giveaway.get("id"))
                await asyncio.sleep(0.5)
        except Exception:  # pragma: no cover - keep the loop alive
            log.exception("watch_giveaways iteration failed")

    @watch_giveaways.before_loop
    async def before_watch(self) -> None:
        await self.bot.wait_until_ready()

    @watch_giveaways.after_loop
    async def after_watch(self) -> None:
        if self.watch_giveaways.is_being_cancelled():
            log.info("Giveaway watcher stopped.")


def _fmt(user_ids: list[int]) -> str:
    return ", ".join(f"<@{uid}>" for uid in user_ids) if user_ids else ""


def _load_ids(raw: Any) -> list[int]:
    if isinstance(raw, list):
        return [int(x) for x in raw]
    try:
        parsed = json.loads(raw or "[]")
        return [int(x) for x in parsed] if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


async def setup(bot: commands.Bot) -> None:
    # Persistent views survive restarts using fixed custom_ids.
    bot.add_view(GiveawayView())
    if GIVEAWAY_REACTION_MODE is not None:
        bot.add_view(GiveawayEmojiJoinView())
    await bot.add_cog(Giveaways(bot))
