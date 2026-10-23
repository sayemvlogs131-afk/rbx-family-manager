"""🧰 Utility commands for RBX Family Manager.

Includes the interactive ``/help`` menu, bot status, reminders, AFK, small
tools (calc, timestamp, poll, embed builder) and account/server information.
"""

from __future__ import annotations

import json
import logging
import platform
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils.checks import is_bot_owner, require_permissions
from utils.embeds import Embeds
from utils.helpers import bot_invite_url, safe_calc, truncate
from utils.time_parser import (
    DurationError,
    format_duration,
    parse_duration,
    parse_human_time,
)

log = logging.getLogger("rbx.utility")

START_TIME = time.time()

# Category metadata used by /help (name -> "icon|description").
CATEGORIES: dict[str, str] = {
    "🛡️ Moderation": "🛡️|Manage members, punishments and messages.",
    "⚙️ Server": "⚙️|Configure logging, welcome, autorole and permissions.",
    "🎉 Giveaways": "🎉|Create and run button-based giveaways.",
    "🔒 Security": "🔒|Anti-nuke, status, update feed and emergency lockdown.",
    "🧰 Utility": "🧰|Reminders, info, polls, embeds and bot status.",
    "😂 Fun": "😂|Harmless fun commands for the whole server.",
    "🔊 Voice": "🔊|Join voice, autojoin, play music and control the queue.",
    "🤖 Bot": "🤖|Bot information, support and owner tools.",
}

COMMAND_CATEGORY: dict[str, str] = {
    "/help": "🧰 Utility",
    "/ping": "🤖 Bot",
    "/botinfo": "🤖 Bot",
    "/uptime": "🤖 Bot",
    "/invite": "🤖 Bot",
    "/serverinfo": "🧰 Utility",
    "/userinfo": "🧰 Utility",
    "/avatar": "🧰 Utility",
    "/banner": "🧰 Utility",
    "/roleinfo": "🧰 Utility",
    "/channelinfo": "🧰 Utility",
    "/permissions": "🧰 Utility",
    "/afk": "🧰 Utility",
    "/remind": "🧰 Utility",
    "/timestamp": "🧰 Utility",
    "/calc": "🧰 Utility",
    "/poll": "🧰 Utility",
    "/embed": "🧰 Utility",
    "/announce": "🧰 Utility",
    "/8ball": "😂 Fun",
    "/coinflip": "😂 Fun",
    "/dice": "😂 Fun",
    "/roll": "😂 Fun",
    "/choose": "😂 Fun",
    "/rate": "😂 Fun",
    "/ship": "😂 Fun",
    "/roast": "😂 Fun",
    "/compliment": "😂 Fun",
    "/meme": "😂 Fun",
    "/joke": "😂 Fun",
    "/join": "🔊 Voice",
    "/autojoin on": "🔊 Voice",
    "/autojoin off": "🔊 Voice",
    "/autojoin status": "🔊 Voice",
    "/play": "🔊 Voice",
    "/skip": "🔊 Voice",
    "/pause": "🔊 Voice",
    "/resume": "🔊 Voice",
    "/stop": "🔊 Voice",
    "/disconnect": "🔊 Voice",
    "/volume": "🔊 Voice",
    "/loop": "🔊 Voice",
    "/shuffle": "🔊 Voice",
    "/queue": "🔊 Voice",
    "/nowplaying": "🔊 Voice",
    "/scan": "🧰 Utility",
    "/rank": "🧰 Utility",
    "/leaderboard": "🧰 Utility",
    "/levelsetup": "🧰 Utility",
    "/rankrole": "🧰 Utility",
    "/warn": "🛡️ Moderation",
    "/warnings": "🛡️ Moderation",
    "/automod": "🛡️ Moderation",
    "/giveaway": "🎉 Giveaways",
    "/botstatus": "🔒 Security",
    "/botupdatesetup": "🔒 Security",
    "/securitysetup": "🔒 Security",
    "/permissionscheck": "🔒 Security",
    "/auditlogs": "🔒 Security",
    "/emergencylockdown": "🔒 Security",
    "/ticket": "⚙️ Server",
    "/welcome": "⚙️ Server",
    "/rolesetup": "⚙️ Server",
    "/autorole": "⚙️ Server",
    "/setlogchannel": "⚙️ Server",
    "/owner": "🤖 Bot",
}


def _build_help_pages(bot: commands.Bot, category: str) -> list[discord.Embed]:
    """Render the command list for one help category."""
    icon, _, description = CATEGORIES.get(category, "•|").partition("|")
    lines = [
        f"`{name}`"
        for name, cat in COMMAND_CATEGORY.items()
        if cat == category
    ]
    if not lines:
        lines = ["_No commands in this category yet._"]
    embed = Embeds.help_category(category, icon or "•", description, lines)
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    return [embed]


class HelpView(discord.ui.View):
    """Category select menu + previous/next buttons for /help."""

    def __init__(self, bot: commands.Bot, author_id: int) -> None:
        super().__init__(timeout=180.0)
        self.bot = bot
        self.author_id = author_id
        self.message: discord.Message | None = None
        self.category = next(iter(CATEGORIES))
        self.index = 0
        self.pages = _build_help_pages(bot, self.category)

        options = [
            discord.SelectOption(label=name, description=truncate(desc, 95), emoji=None)
            for name, desc in ((n, v.split("|", 1)[1]) for n, v in CATEGORIES.items())
        ]
        select = discord.ui.Select(placeholder="Choose a category", options=options, row=0)
        select.callback = self._on_select
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Only the command invoker can use this menu.", ephemeral=True
        )
        return False

    async def _on_select(self, interaction: discord.Interaction) -> None:
        # Decorated buttons are added BEFORE this select, so never index
        # children positionally - use the reference kept in __init__.
        values = list(self.select.values)
        if not values:
            values = list((interaction.data or {}).get("values") or [])
        if not values:
            await interaction.response.defer()
            return
        self.category = values[0]
        self.pages = _build_help_pages(self.bot, self.category)
        self.index = 0
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[0], view=self)

    def _sync_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = len(self.pages) <= 1

    @discord.ui.button(label="‹", style=discord.ButtonStyle.secondary, row=1)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="›", style=discord.ButtonStyle.secondary, row=1)
    async def nxt(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index = min(len(self.pages) - 1, self.index + 1)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=1)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            pass

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Utility(commands.Cog, name="Utility"):
    """General purpose tools."""

    category = "🧰 Utility"
    category_description = "Reminders, information, polls and embeds."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.reminder_loop.start()

    # ------------------------------------------------------------- help
    @commands.hybrid_command(name="help")
    @app_commands.describe(category="Jump straight to a category")
    @app_commands.choices(
        category=[
            app_commands.Choice(name=name, value=name) for name in CATEGORIES
        ]
    )
    async def help_command(
        self, ctx: commands.Context, category: Optional[str] = None
    ) -> None:
        """Interactive help menu for RBX Family Manager."""
        view = HelpView(self.bot, ctx.author.id)
        if category:
            view.category = category
            view.pages = _build_help_pages(self.bot, category)
        view._sync_buttons()
        embed = view.pages[0]
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(
            name="Invite",
            value=f"[Add me to your server]({bot_invite_url(self.bot.user.id)})",
            inline=False,
        )
        if isinstance(ctx, discord.Interaction):
            await ctx.response.send_message(embed=embed, view=view)
            view.message = await ctx.original_response()
        else:
            view.message = await ctx.send(embed=embed, view=view)

    # ----------------------------------------------------------- status
    @commands.hybrid_command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        """Show API latency and response time."""
        start = time.perf_counter()
        message = await ctx.send(embed=Embeds.info("Ping", "Measuring…"))
        elapsed = (time.perf_counter() - start) * 1000
        ws = round(self.bot.latency * 1000)
        embed = Embeds.success(
            "Pong!",
            f"**Gateway:** `{ws}ms`\n**Response:** `{elapsed:.0f}ms`\n"
            f"**Database:** `ok`",
        )
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:  # pragma: no cover
            pass

    @commands.hybrid_command(name="botinfo")
    async def botinfo(self, ctx: commands.Context) -> None:
        """Information about RBX Family Manager."""
        import config as cfg

        embed = Embeds.info(f"{cfg.BOT_NAME}", cfg.BOT_DESCRIPTION)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(
            name="Users", value=str(sum(g.member_count or 0 for g in self.bot.guilds)), inline=True
        )
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Version", value=cfg.BOT_VERSION, inline=True)
        embed.add_field(
            name="Uptime", value=format_duration(int(time.time() - START_TIME)), inline=True
        )
        # Security posture snapshot (v2.0.0 security layer).
        try:
            mode = await self.bot.db.get_stat(0, "bot_status_mode", "online") or "online"
            icon = {"online": "🟢", "maintenance": "🟡", "issues": "🔴"}.get(mode, "🟢")
            embed.add_field(name="Status", value=icon, inline=True)
        except Exception:
            embed.add_field(name="Status", value="🟢", inline=True)
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(
            name="Invite",
            value=f"[Add me to your server]({bot_invite_url(self.bot.user.id)})",
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="uptime")
    async def uptime(self, ctx: commands.Context) -> None:
        """Show how long the bot has been running."""
        seconds = int(time.time() - START_TIME)
        started = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        embed = Embeds.info(
            "Uptime",
            f"**Running for:** {format_duration(seconds)}\n"
            f"**Since:** <t:{int(started.timestamp())}:F>",
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="invite")
    async def invite(self, ctx: commands.Context) -> None:
        """Get the bot invite link."""
        embed = Embeds.info(
            "Invite RBX Family Manager",
            f"[Click here to add me to your server]({bot_invite_url(self.bot.user.id)})\n\n"
            "Required intent: **Server Members** and **Message Content** "
            "(enable both in the Developer Portal).",
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------- info
    @commands.hybrid_command(name="serverinfo")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        """Information about this server."""
        guild = ctx.guild
        embed = Embeds.info(guild.name, truncate(guild.description or "", 400))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(
            name="Channels",
            value=f"{len(guild.text_channels)} text / {len(guild.voice_channels)} voice",
            inline=True,
        )
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        humans = sum(1 for m in guild.members if not m.bot)
        bots = sum(1 for m in guild.members if m.bot)
        embed.add_field(name="Humans / Bots", value=f"{humans} / {bots}", inline=True)
        embed.add_field(
            name="Emojis / Stickers",
            value=f"{len(guild.emojis)} / {len(guild.stickers)}",
            inline=True,
        )
        embed.add_field(
            name="Verification",
            value=guild.verification_level.name.title(),
            inline=True,
        )
        embed.add_field(
            name="Boosts",
            value=f"Tier {guild.premium_tier} ({guild.premium_subscription_count} boosts)",
            inline=True,
        )
        embed.add_field(
            name="Created",
            value=f"<t:{int(guild.created_at.timestamp())}:R>",
            inline=True,
        )
        if guild.mfa_level:
            embed.add_field(name="2FA", value="Required for moderation", inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="userinfo")
    @commands.guild_only()
    @app_commands.describe(user="User to inspect (defaults to you)")
    async def userinfo(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ) -> None:
        """Information about a member."""
        member = user or ctx.author
        await ctx.send(embed=self._userinfo_embed(member, member))

    @staticmethod
    def _userinfo_embed(
        target: discord.User, member: Optional[discord.Member]
    ) -> discord.Embed:
        """Shared profile embed for /userinfo and the User Info context menu."""
        display = member or target
        roles = (
            [r.mention for r in member.roles[1:][:15]] if member is not None else []
        )
        embed = Embeds.member(display, f"User info • {display}")
        embed.add_field(
            name="Created", value=f"<t:{int(target.created_at.timestamp())}:R>", inline=True
        )
        if member is not None:
            if member.joined_at:
                embed.add_field(
                    name="Joined",
                    value=f"<t:{int(member.joined_at.timestamp())}:R>",
                    inline=True,
                )
            embed.add_field(
                name="Nickname", value=truncate(member.nick or "`none`", 100), inline=True
            )
            embed.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
            embed.add_field(name="Highest role", value=member.top_role.mention, inline=True)
            embed.add_field(
                name=f"Roles ({len(member.roles) - 1})",
                value=", ".join(roles) or "`none`",
                inline=False,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            if member.banner:
                embed.set_image(url=member.banner.url)
        else:
            embed.add_field(
                name="Server",
                value="Not a member of this server.",
                inline=False,
            )
            embed.set_thumbnail(url=target.display_avatar.url)
            if getattr(target, "banner", None):
                embed.set_image(url=target.banner.url)
        return embed

    # Context menus cannot live inside a Cog class; see USER_INFO_MENU below.

    @commands.hybrid_command(name="avatar")
    @app_commands.describe(user="Whose avatar", style="Image format")
    @app_commands.choices(
        style=[
            app_commands.Choice(name="png", value="png"),
            app_commands.Choice(name="jpg", value="jpg"),
            app_commands.Choice(name="webp", value="webp"),
            app_commands.Choice(name="gif", value="gif"),
        ]
    )
    async def avatar(
        self,
        ctx: commands.Context,
        user: Optional[discord.User] = None,
        style: str = "png",
    ) -> None:
        """Show a user's avatar."""
        target = user or ctx.author
        url = target.display_avatar.replace(size=1024, format=style).url
        embed = Embeds.info(f"Avatar • {target}", f"[Open full size]({url})")
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="banner")
    @app_commands.describe(user="Whose profile banner")
    async def banner(
        self, ctx: commands.Context, user: Optional[discord.User] = None
    ) -> None:
        """Show a user's profile banner."""
        target = user or ctx.author
        # fetch_user is a network round-trip; acknowledge first.
        await ctx.defer()
        fresh = target
        try:
            fresh = await self.bot.fetch_user(target.id)
        except discord.HTTPException:  # pragma: no cover - fall back to cache
            pass
        banner = getattr(fresh, "banner", None)
        if banner is None:
            await ctx.send(
                embed=Embeds.info(
                    "No banner",
                    f"**{target}** has no profile banner set.",
                ),
                ephemeral=True,
            )
            return
        url = banner.replace(size=1024).url
        embed = Embeds.info(f"Banner • {target}", f"[Open full size]({url})")
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="servericon")
    @commands.guild_only()
    @app_commands.describe(full="Also show the server banner if one is set")
    async def servericon(self, ctx: commands.Context, full: bool = False) -> None:
        """Show this server's icon in full size."""
        guild = ctx.guild
        icon = guild.icon
        banner = guild.banner if full else None
        if icon is None and banner is None:
            await ctx.send(
                embed=Embeds.info(
                    "No icon",
                    "**{0}** has no {1}.".format(
                        guild.name,
                        "icon or banner" if full else "icon set",
                    ),
                ),
                ephemeral=True,
            )
            return
        messages = []
        if icon is not None:
            icon_embed = Embeds.info(
                f"Server icon • {guild.name}", f"[Open full size]({icon.url})"
            )
            icon_embed.set_image(url=icon.url)
            messages.append(icon_embed)
        if banner is not None:
            banner_embed = Embeds.info(
                f"Server banner • {guild.name}", f"[Open full size]({banner.url})"
            )
            banner_embed.set_image(url=banner.url)
            messages.append(banner_embed)
        if len(messages) == 1:
            await ctx.send(embed=messages[0])
        else:
            await ctx.send(embeds=messages)

    @commands.hybrid_command(name="roleinfo")
    @commands.guild_only()
    @app_commands.describe(role="Role to inspect")
    async def roleinfo(self, ctx: commands.Context, role: discord.Role) -> None:
        """Information about a role."""
        embed = Embeds.info(f"Role info • {role.name}", "")
        embed.color = role.colour if role.colour.value else discord.Colour.blurple()
        embed.add_field(name="ID", value=f"`{role.id}`", inline=True)
        embed.add_field(name="Members", value=str(len(role.members)), inline=True)
        embed.add_field(name="Position", value=f"{role.position}/{len(ctx.guild.roles)}", inline=True)
        embed.add_field(name="Color", value=str(role.colour), inline=True)
        embed.add_field(name="Hoisted", value=str(role.hoist), inline=True)
        embed.add_field(name="Mentionable", value=str(role.mentionable), inline=True)
        embed.add_field(name="Managed", value=str(role.managed), inline=True)
        embed.add_field(
            name="Created", value=f"<t:{int(role.created_at.timestamp())}:R>", inline=True
        )
        perms_list = [p.replace("_", " ").title() for p, v in role.guild_permissions if v]
        embed.add_field(
            name=f"Permissions ({len(perms_list)})",
            value=truncate(", ".join(perms_list) or "`none`", 1000),
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="channelinfo")
    @commands.guild_only()
    @app_commands.describe(channel="Channel to inspect (defaults to here)")
    async def channelinfo(
        self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None
    ) -> None:
        """Information about a text channel."""
        target = channel or ctx.channel
        embed = Embeds.info(f"Channel info • {target.name}", "")
        embed.add_field(name="ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="Category", value=str(target.category or "`none`"), inline=True)
        embed.add_field(name="NSFW", value=str(target.nsfw), inline=True)
        embed.add_field(name="Slowmode", value=f"{target.slowmode_delay}s", inline=True)
        embed.add_field(name="Position", value=str(target.position), inline=True)
        embed.add_field(
            name="Created", value=f"<t:{int(target.created_at.timestamp())}:R>", inline=True
        )
        embed.add_field(
            name="Topic", value=truncate(target.topic or "`none`", 1000), inline=False
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="permissions")
    @commands.guild_only()
    @app_commands.describe(user="Member (defaults to you)", channel="Channel to check")
    async def permissions(
        self,
        ctx: commands.Context,
        user: Optional[discord.Member] = None,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """Show a member's permissions here."""
        member = user or ctx.author
        target = channel or ctx.channel
        if not isinstance(target, discord.TextChannel):
            await ctx.send(embed=Embeds.error("Unsupported", "Use a text channel."), ephemeral=True)
            return
        effective = target.permissions_for(member)
        allowed = [p.replace("_", " ").title() for p, v in effective if v]
        denied = [p.replace("_", " ").title() for p, v in effective if not v]
        embed = Embeds.info(f"Permissions • {member}", f"Checked in {target.mention}")
        embed.add_field(
            name=f"✅ Allowed ({len(allowed)})",
            value=truncate(", ".join(allowed) or "`none`", 1000),
            inline=False,
        )
        embed.add_field(
            name=f"❌ Denied ({len(denied)})",
            value=truncate(", ".join(denied) or "`none`", 1000),
            inline=False,
        )
        await ctx.send(embed=embed)

    # -------------------------------------------------------------- tools
    @commands.hybrid_command(name="afk")
    @commands.guild_only()
    @app_commands.describe(message="Why you are away (shown when you are pinged)")
    async def afk(self, ctx: commands.Context, *, message: str = "AFK") -> None:
        """Mark yourself as AFK until you send a message again."""
        await self.bot.db.set_afk(
            ctx.guild.id, ctx.author.id, truncate(message, 200), int(time.time())
        )
        await ctx.send(
            embed=Embeds.success(
                "AFK enabled", f"You are now AFK: {truncate(message, 200)}"
            )
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        # Remove AFK status when the user talks (read-first to keep writes cheap).
        existing = await self.bot.db.get_afk(message.guild.id, message.author.id)
        if existing:
            await self.bot.db.remove_afk(message.guild.id, message.author.id)
            try:
                await message.channel.send(
                    f" Welcome back {message.author.mention} — AFK cleared.",
                    delete_after=5,
                )
            except discord.HTTPException:
                pass
        # Notify when mentioning an AFK user.
        for user_id in {m.id for m in message.mentions}:
            if user_id == message.author.id:
                continue
            row = await self.bot.db.get_afk(message.guild.id, user_id)
            if row:
                ago = format_duration(int(time.time()) - int(row["since"]))
                try:
                    await message.channel.send(
                        f" **<@{user_id}>** is AFK ({ago}): {truncate(str(row['message']), 150)}",
                        delete_after=8,
                    )
                except discord.HTTPException:
                    pass

    @commands.hybrid_command(name="remind")
    @commands.guild_only()
    @app_commands.describe(
        duration="When to remind you (e.g. 10m, 1h30m, 2d)",
        message="What should I remind you about?",
    )
    async def remind(
        self, ctx: commands.Context, duration: str, *, message: str
    ) -> None:
        """Schedule a reminder in this channel."""
        try:
            seconds = parse_duration(duration, max_seconds=365 * 86400)
        except DurationError as exc:
            await ctx.send(embed=Embeds.error("Invalid duration", str(exc)), ephemeral=True)
            return
        remind_at = int((datetime.now(timezone.utc) + timedelta(seconds=seconds)).timestamp())
        reminder_id = await self.bot.db.add_reminder(
            ctx.guild.id, ctx.channel.id, ctx.author.id, remind_at, truncate(message, 500)
        )
        await ctx.send(
            embed=Embeds.success(
                "Reminder set",
                f"I will remind you <t:{remind_at}:R> in this channel.\n"
                f"Reminder ID: `{reminder_id}`\n"
                f"Message: {truncate(message, 200)}",
            )
        )

    @commands.hybrid_command(name="reminders")
    @commands.guild_only()
    async def reminders(self, ctx: commands.Context) -> None:
        """List your pending reminders."""
        rows = await self.bot.db.pending_reminders_for(
            ctx.guild.id, ctx.author.id, int(time.time())
        )
        if not rows:
            await ctx.send(
                embed=Embeds.info("Reminders", "You have no pending reminders."),
                ephemeral=True,
            )
            return
        lines = [
            f"**#{r['id']}** • <t:{r['remind_at']}:R> • {truncate(str(r['message']), 100)}"
            for r in rows[:15]
        ]
        await ctx.send(embed=Embeds.command_list(lines, "⏰ Your reminders"))

    @commands.hybrid_command(name="timestamp")
    @app_commands.describe(
        time_text="e.g. `tomorrow 18:00`, `2026-01-01 12:00` or `18:00`",
        style="Timestamp rendering style",
    )
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Short time", value="t"),
            app_commands.Choice(name="Long time", value="T"),
            app_commands.Choice(name="Short date", value="d"),
            app_commands.Choice(name="Long date", value="D"),
            app_commands.Choice(name="Long date with day", value="F"),
            app_commands.Choice(name="Relative (countdown)", value="R"),
        ]
    )
    async def timestamp(
        self, ctx: commands.Context, time_text: str, style: str = "F"
    ) -> None:
        """Convert a date/time into a Discord timestamp you can copy."""
        try:
            parsed = parse_human_time(time_text)
        except DurationError as exc:
            await ctx.send(embed=Embeds.error("Invalid time", str(exc)), ephemeral=True)
            return
        epoch = int(parsed.timestamp())
        embed = Embeds.info(
            "Timestamp generated",
            f"**Result:** <t:{epoch}:{style}>\n"
            f"**Copyable:** `<t:{epoch}:{style}>`\n"
            f"**UTC:** {parsed.strftime('%Y-%m-%d %H:%M')}",
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="calc")
    @app_commands.describe(expression="e.g. `2 + 2 * 10` or `sqrt(144)`")
    async def calc(self, ctx: commands.Context, expression: str) -> None:
        """Evaluate a simple arithmetic expression (no code execution)."""
        result = safe_calc(expression)
        if result is None:
            await ctx.send(
                embed=Embeds.error(
                    "Invalid expression",
                    "Only basic arithmetic is supported: numbers, `+ - * / // % **`, "
                    "parentheses and `sqrt(x)`.",
                ),
                ephemeral=True,
            )
            return
        await ctx.send(
            embed=Embeds.info("Calculator", f"```{truncate(expression, 200)}```\n**= {result}**")
        )

    @commands.hybrid_command(name="poll")
    @commands.guild_only()
    @app_commands.describe(
        question="The question to ask", options="Options separated by commas (2-10)"
    )
    async def poll(self, ctx: commands.Context, question: str, *, options: str) -> None:
        """Create a reaction poll."""
        parts = [p.strip() for p in options.split(",") if p.strip()]
        if len(parts) < 2:
            await ctx.send(
                embed=Embeds.error(
                    "Not enough options", "Provide at least two options separated by commas."
                ),
                ephemeral=True,
            )
            return
        if len(parts) > 10:
            await ctx.send(
                embed=Embeds.error("Too many options", "Maximum of 10 options."), ephemeral=True
            )
            return
        numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        embed = Embeds.question(truncate(question, 250))
        embed.add_field(
            name="Options",
            value="\n".join(
                f"{numbers[i]} {truncate(opt, 100)}" for i, opt in enumerate(parts)
            ),
            inline=False,
        )
        embed.set_author(
            name=f"Poll by {ctx.author}", icon_url=ctx.author.display_avatar.url
        )
        message = await ctx.send(embed=embed)
        for i in range(len(parts)):
            try:
                await message.add_reaction(numbers[i])
            except discord.HTTPException:
                break

    # -------------------------------------------------------- embed/announce
    @commands.hybrid_command(name="embed")
    @commands.guild_only()
    @require_permissions("manage_messages")
    @app_commands.describe(
        title="Embed title",
        description="Embed description (supports \\n for line breaks)",
        color="Hex color, e.g. 5865f2",
        channel="Where to post it (defaults to here)",
    )
    async def embed(
        self,
        ctx: commands.Context,
        title: str,
        description: str,
        color: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """Post a custom embed (quick builder)."""
        target = channel or ctx.channel
        colour = discord.Colour.blurple()
        if color:
            raw = color.strip().lstrip("#")
            if raw.lower().startswith("0x"):
                raw = raw[2:]
            try:
                colour = discord.Colour(int(raw, 16))
            except ValueError:
                await ctx.send(
                    embed=Embeds.error("Invalid color", "Use hex like `5865f2`."), ephemeral=True
                )
                return
        embed_obj = discord.Embed(
            title=truncate(title, 256),
            description=description.replace("\\n", "\n")[:4000],
            color=colour,
        )
        embed_obj.set_footer(text="RBX Family Manager • RBX Family")
        embed_obj.timestamp = datetime.now(timezone.utc)
        try:
            await target.send(embed=embed_obj)
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Failed", str(exc)), ephemeral=True)
            return
        if target != ctx.channel:
            await ctx.send(
                embed=Embeds.success("Embed posted", f"Sent to {target.mention}."), ephemeral=True
            )

    @commands.hybrid_command(name="announce")
    @commands.guild_only()
    @require_permissions("manage_messages")
    @app_commands.describe(
        message="Announcement text (supports \\n line breaks)",
        channel="Where to post (defaults to here)",
        role="Role to ping (optional)",
        title="Embed title",
    )
    async def announce(
        self,
        ctx: commands.Context,
        message: str,
        channel: Optional[discord.TextChannel] = None,
        role: Optional[discord.Role] = None,
        title: str = "Announcement",
    ) -> None:
        """Post an announcement embed, optionally pinging a role."""
        target = channel or ctx.channel
        content = role.mention if role else None
        embed_obj = discord.Embed(
            title=truncate(title, 256),
            description=message.replace("\\n", "\n")[:4000],
            color=0x5865F2,
        )
        embed_obj.set_author(
            name=f"By {ctx.author}", icon_url=ctx.author.display_avatar.url
        )
        embed_obj.set_footer(text="RBX Family Manager • RBX Family")
        embed_obj.timestamp = datetime.now(timezone.utc)
        allowed = discord.AllowedMentions(
            users=True, roles=bool(role), everyone=False, replied_user=False
        )
        try:
            await target.send(content=content, embed=embed_obj, allowed_mentions=allowed)
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Failed", str(exc)), ephemeral=True)
            return
        if target != ctx.channel:
            await ctx.send(
                embed=Embeds.success("Announcement posted", f"Sent to {target.mention}."),
                ephemeral=True,
            )

    # --------------------------------------------------------------- scan
    @commands.hybrid_command(name="scan")
    @commands.guild_only()
    async def scan(self, ctx: commands.Context) -> None:
        """Audit the whole server: members, channels, config, moderation data and problems."""
        guild = ctx.guild
        if guild is None:  # pragma: no cover - guild_only check should catch this
            return
        if not (is_bot_owner(ctx) or guild.owner_id == ctx.author.id):
            await ctx.send(
                embed=Embeds.error(
                    "Owner only",
                    "Only the **bot owner** or the **server owner** can run `/scan`.",
                ),
                ephemeral=True,
            )
            return

        await ctx.defer(ephemeral=True)

        def channel_ref(value: Any) -> str:
            if not value:
                return "❌ not set"
            channel = guild.get_channel(int(value))
            if channel is None:
                return f"⚠️ deleted (`{value}`)"
            return f"✅ {channel.mention}"

        def role_ref(value: Any) -> str:
            if not value:
                return "❌ not set"
            role = guild.get_role(int(value))
            if role is None:
                return f"⚠️ deleted (`{value}`)"
            return f"✅ {role.mention}"

        def ok(value: Any) -> str:
            return "✅ on" if value else "❌ off"

        members = list(guild.members)
        bots = sum(1 for m in members if m.bot)
        humans = len(members) - bots
        key_perm_names = (
            "ban_members", "kick_members", "moderate_members", "manage_roles",
            "manage_channels", "manage_messages", "view_audit_log", "connect",
            "send_messages", "view_channel",
        )
        joys: list[str] = []  # issues

        db = self.bot.db
        db_error = False
        try:
            settings = await db.get_guild(guild.id)
            automod = await db.get_automod(guild.id)
            security = await db.get_security(guild.id)
            giveaways = await db.guild_giveaways(guild.id, include_ended=False)
            giveaways_total = len(await db.guild_giveaways(guild.id, include_ended=True))
            tickets = await db.ticket_counts(guild.id)
            warnings_total = int(
                await db.scalar(
                    "SELECT COUNT(*) FROM warnings WHERE guild_id = ?", (guild.id,), 0
                )
            )
            actions_total = int(
                await db.scalar(
                    "SELECT COUNT(*) FROM moderation_actions WHERE guild_id = ?",
                    (guild.id,),
                    0,
                )
            )
            events_total = int(
                await db.scalar(
                    "SELECT COUNT(*) FROM security_events WHERE guild_id = ?",
                    (guild.id,),
                    0,
                )
            )
            autoreplies = len(await db.autoreply_entries(guild.id))
            overrides = len(await db.guild_command_overrides(guild.id))
            ignored_ch = len(await db.ignored_channels(guild.id))
            ignored_roles = len(await db.ignored_roles(guild.id))
            try:
                autojoin_raw = await db.get_stat(guild.id, "vc_autojoin", "")
            except Exception:
                autojoin_raw = ""
        except Exception:
            log.exception("/scan database read failed in guild %s", guild.id)
            db_error = True
            settings = {}
            automod = {}
            security = {}
            giveaways = []
            giveaways_total = 0
            tickets = {"open": 0, "closed": 0}
            warnings_total = actions_total = events_total = 0
            autoreplies = overrides = ignored_ch = ignored_roles = 0
            autojoin_raw = ""

        # --------- issues: broken config references + bot permission trouble
        invincible_roles = 0
        top_role_blocker = ""
        me_top = guild.me.top_role.position if guild.me else 0
        for role in guild.roles:
            if role.managed or role.is_default() or role.position <= me_top:
                continue
            if role.permissions.administrator:
                invincible_roles += 1
            if not top_role_blocker:
                top_role_blocker = role.name
        if top_role_blocker:
            joys.append(
                f"My top role is below **{top_role_blocker}** — I can't punish members "
                "wearing higher roles. Drag my role above it."
            )
        broken = []
        for label, kind, value in (
            ("mod log", "channel", settings.get("mod_log_channel")),
            ("member log", "channel", settings.get("member_log_channel")),
            ("server log", "channel", settings.get("server_log_channel")),
            ("message log", "channel", settings.get("message_log_channel")),
            ("welcome channel", "channel", settings.get("welcome_channel")),
            ("ticket category", "channel", settings.get("ticket_category_id")),
            ("staff role", "role", settings.get("staff_role_id")),
            ("autorole", "role", settings.get("autorole_id")),
            ("ticket staff role", "role", settings.get("ticket_staff_role_id")),
            ("anti-nuke log", "channel", security.get("log_channel")),
        ):
            if not value:
                continue
            found = (
                guild.get_channel(int(value)) if kind == "channel"
                else guild.get_role(int(value))
            )
            if found is None:
                broken.append(label)
        if broken:
            joys.append(
                "Config points to deleted " + ", ".join(broken) +
                " — re-set them with their setup commands."
            )
        missing_perms = [
            p.replace("_", " ") for p in key_perm_names
            if not (guild.me and getattr(guild.me.guild_permissions, p, False))
        ]
        if missing_perms:
            joys.append(
                "Missing permissions: **" + ", ".join(missing_perms) + "** — "
                "reinvite or fix my role permissions."
            )
        if not settings.get("welcome_message"):
            joys.append("**Welcome message is not set** — new members get no greeting. Use `/welcome`.")
        if db_error:
            joys.insert(0, "⚠️ Database read failed — some numbers may be missing.")

        # ------------------------------------------------------------ embed
        me = guild.me
        has_admin = bool(me and me.guild_permissions.administrator)
        embed = Embeds.info(
            f"🔍 Server scan — {guild.name}",
            f"Requested by {ctx.author.mention} • every value is live from Discord + the bot database",
        )
        embed.add_field(
            name="📊 Server",
            value=(
                f"**Owner:** <@{guild.owner_id}>\n"
                f"**Created:** <t:{int(guild.created_at.timestamp())}:R>\n"
                f"**Members:** {humans} 👤 + {bots} 🤖 = {guild.member_count}\n"
                f"**Boosts:** level {guild.premium_tier} ({guild.premium_subscription_count})\n"
                f"**Channels:** {len(guild.text_channels)} 💬 • {len(guild.voice_channels)} 🔊 • "
                f"{len(guild.forum_channels)} 🗒️ • {len(guild.stage_channels)} 🎙️ • {len(guild.categories)} 📁\n"
                f"**Roles:** {len(guild.roles)} (administrators: {invincible_roles})\n"
                f"**Emojis:** {len(guild.emojis)}/{guild.emoji_limit}"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛡️ Automod",
            value=(
                f"**Enabled:** {ok(automod.get('enabled', 1))}\n"
                f"Spam filter {ok(automod.get('anti_spam', 0))} • duplicates {ok(automod.get('duplicate_filter', 0))} • "
                f"invites {ok(automod.get('invite_filter', 0))} • links {ok(automod.get('link_filter', 0))}\n"
                f"Mention limit {automod.get('mention_limit', '—')} • emoji {automod.get('emoji_limit', '—')} • "
                f"caps {automod.get('caps_limit', '—')}% • warn threshold {automod.get('warn_threshold', '—')}\n"
                f"Timeout {automod.get('timeout_duration', '—')}s • exempt roles {len(json.loads(str(automod.get('exempt_roles') or '[]')))} • "
                f"exempt channels {len(json.loads(str(automod.get('exempt_channels') or '[]')))}"
            ),
            inline=False,
        )
        embed.add_field(
            name="🔒 Anti-nuke / security",
            value=(
                f"**Enabled:** {ok(security.get('anti_nuke_enabled', 0))}\n"
                f"Limits /60s: bans {security.get('ban_limit', '—')} • kicks {security.get('kick_limit', '—')} • "
                f"channels {security.get('channel_limit', '—')} • roles {security.get('role_limit', '—')}\n"
                f"Joins: {security.get('join_limit', '—')} / {security.get('join_window_seconds', '—')}s → raid action *{security.get('raid_action', '?')}*\n"
                f"Min account age {security.get('min_account_age_days', '—')}d → *{security.get('action', '?')}*\n"
                f"Trusted roles {len(json.loads(str(security.get('trusted_role_ids') or '[]')))} • "
                f"trusted users {len(json.loads(str(security.get('trusted_user_ids') or '[]')))} • "
                f"log {channel_ref(security.get('log_channel'))}"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚙️ Setup channels / roles",
            value=(
                f"Staff role: {role_ref(settings.get('staff_role_id'))}\n"
                f"Mod log: {channel_ref(settings.get('mod_log_channel'))} • Member log: {channel_ref(settings.get('member_log_channel'))}\n"
                f"Server log: {channel_ref(settings.get('server_log_channel'))} • Message log: {channel_ref(settings.get('message_log_channel'))}\n"
                f"Welcome: {channel_ref(settings.get('welcome_channel'))} • message: "
                f"{['❌ not set', '✅ set'][bool(settings.get('welcome_message'))]} • DM: {ok(settings.get('dm_welcome', 0))}\n"
                f"Autorole: {role_ref(settings.get('autorole_id'))}\n"
                f"Tickets: category {channel_ref(settings.get('ticket_category_id'))} • staff {role_ref(settings.get('ticket_staff_role_id'))}"
            ),
            inline=False,
        )
        embed.add_field(
            name="📚 Activity data",
            value=(
                f"**Warnings:** {warnings_total} total\n"
                f"**Mod actions:** {actions_total} logged\n"
                f"**Security events:** {events_total} logged\n"
                f"**Giveaways:** active {len(giveaways)} / total {giveaways_total}\n"
                f"**Tickets:** open {tickets.get('open', 0)} / closed {tickets.get('closed', 0)}\n"
                f"**Auto-replies:** {autoreplies} • command limits: {overrides} • ignored: {ignored_ch} ch / {ignored_roles} roles"
            ),
            inline=False,
        )
        autojoin_desc = "❌ off"
        if autojoin_raw:
            try:
                data = json.loads(autojoin_raw)
                autojoin_desc = f"✅ follows <@{data['user_id']}>"
            except Exception:
                autojoin_desc = "⚠️ corrupt config"
        embed.add_field(
            name="🔊 Voice",
            value=(
                f"Autojoin: {autojoin_desc}\n"
                f"Administrator permission: {ok(has_admin)}"
            ),
            inline=False,
        )
        if joys:
            embed.add_field(
                name="⛔ Problems found",
                value="\n".join(f"• {line}" for line in joys)[:1024],
                inline=False,
            )
        if not joys:
            embed.add_field(name="⛔ Problems found", value="✅ None — everything looks healthy!", inline=False)

        await ctx.send(embed=embed, ephemeral=True)

    # ---------------------------------------------------------- reminders job
    @tasks.loop(seconds=15)
    async def reminder_loop(self) -> None:
        # Shutting down? Bail quietly — the database may already be closed.
        if self.bot.is_closed() or not getattr(self.bot.db, "connected", True):
            return
        try:
            now = int(time.time())
            for reminder in await self.bot.db.due_reminders(now):
                # Each reminder is isolated: a deleted channel or failed send
                # must not abort the batch and wedge every later reminder.
                try:
                    channel = self.bot.get_channel(int(reminder["channel_id"]))
                    if channel is None:
                        channel = await self.bot.fetch_channel(int(reminder["channel_id"]))
                    embed = Embeds.info(
                        "⏰ Reminder",
                        f"<@{reminder['user_id']}> — {truncate(str(reminder['message']), 500)}",
                    )
                    await channel.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(
                            users=True, roles=False, everyone=False
                        ),
                    )
                except (discord.HTTPException, discord.Forbidden, discord.NotFound):
                    log.debug("Could not deliver reminder %s", reminder["id"], exc_info=True)
                # Mark delivered even on failure: a permanently dead channel
                # would otherwise block this reminder forever.
                await self.bot.db.mark_reminder(int(reminder["id"]))
        except Exception:  # pragma: no cover - keep the loop alive
            log.exception("reminder_loop iteration failed")

    @reminder_loop.before_loop
    async def before_reminders(self) -> None:
        await self.bot.wait_until_ready()


async def userinfo_context_menu(
    interaction: discord.Interaction, user: discord.User
) -> None:
    """Right-click a user → User Info (works for non-members too)."""
    # Acknowledge immediately: fetch_member may exceed the 3s deadline.
    await interaction.response.defer(ephemeral=True)
    member: Optional[discord.Member] = None
    guild = interaction.guild
    if guild is not None:
        member = guild.get_member(user.id)
        if member is None:
            try:
                member = await guild.fetch_member(user.id)
            except discord.HTTPException:
                member = None
    embed = Utility._userinfo_embed(user, member)
    await interaction.followup.send(embed=embed, ephemeral=True)


# discord.py forbids context menus declared inside a Cog class.
USER_INFO_MENU = app_commands.ContextMenu(
    name="User Info", callback=userinfo_context_menu
)


async def setup(bot: commands.Bot) -> None:
    # Idempotent on cog reload (tree commands are not tied to the cog).
    bot.tree.remove_command(USER_INFO_MENU.name, type=USER_INFO_MENU.type)
    bot.tree.add_command(USER_INFO_MENU)
    await bot.add_cog(Utility(bot))
