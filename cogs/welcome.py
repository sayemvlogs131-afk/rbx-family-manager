"""👋 Welcome system and auto role for RBX Family Manager.

Handles new members: welcome message/embed, optional DM, and the configured
auto role. Supports placeholders:

``{user}`` ``{mention}`` ``{username}`` ``{server}`` ``{member_count}`` ``{id}``
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import permissions as perms
from utils.checks import require_admin
from utils.embeds import Embeds
from utils.helpers import truncate
from utils.time_parser import format_duration

log = logging.getLogger("rbx.welcome")

DEFAULT_WELCOME = (
    "Welcome to **{server}**, {user}! You are member **#{member_count}**. "
    "Please read the rules and enjoy your stay."
)

DEFAULT_LEAVE = (
    "{user} (`{username}`) has left **{server}**. "
    "We are now **{member_count}** members."
)

LEAVE_PLACEHOLDER_HINT = (
    "Placeholders: `{user}` `{mention}` `{username}` `{server}` `{member_count}` `{id}`"
)

PLACEHOLDER_HINT = (
    "Placeholders: `{user}` `{mention}` `{username}` `{server}` `{member_count}` `{id}`"
)


def render(template: str, member: discord.Member) -> str:
    """Replace supported placeholders safely (no format-string injection)."""
    guild = member.guild
    values = {
        "{user}": member.mention,
        "{mention}": member.mention,
        "{username}": member.name,
        "{server}": guild.name,
        "{member_count}": str(guild.member_count or 0),
        "{id}": str(member.id),
    }
    out = template or ""
    for key, value in values.items():
        out = out.replace(key, value)
    return out


class Welcome(commands.Cog, name="Welcome"):
    """Welcome messages, welcome embeds and auto roles."""

    category = "⚙️ Server"
    category_description = "Greet new members and assign roles automatically."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -------------------------------------------------------------- events
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._apply_autorole(member)
        await self._send_welcome(member)

    async def _apply_autorole(self, member: discord.Member) -> None:
        settings = await self.bot.db.get_guild(member.guild.id)
        role_id = settings.get("autorole_id")
        if not role_id:
            return
        role = member.guild.get_role(int(role_id))
        if role is None:
            log.debug("Auto role %s missing in guild %s", role_id, member.guild.id)
            return
        if not perms.bot_can_assign_role(member.guild.me, role).ok:
            log.warning("Cannot assign auto role %s in guild %s", role_id, member.guild.id)
            return
        try:
            await member.add_roles(role, reason="RBX Family Manager auto role")
        except discord.HTTPException:
            log.debug("Auto role assignment failed", exc_info=True)

    async def _send_welcome(self, member: discord.Member) -> None:
        settings = await self.bot.db.get_guild(member.guild.id)
        channel_id = settings.get("welcome_channel")
        if not channel_id:
            return

        channel = member.guild.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await member.guild.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return
        if not isinstance(channel, discord.TextChannel):
            return

        message_template = settings.get("welcome_message") or DEFAULT_WELCOME
        text = render(str(message_template), member)

        try:
            if int(settings.get("welcome_embed", 1)):
                embed = discord.Embed(
                    title=f"Welcome to {truncate(member.guild.name, 200)}!",
                    description=truncate(text, 3500),
                    color=0x57F287,
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_author(name=str(member), icon_url=member.display_avatar.url)
                embed.add_field(
                    name="Member count", value=str(member.guild.member_count), inline=True
                )
                embed.set_footer(text="RBX Family Manager • RBX Family")
                await channel.send(
                    content=member.mention, embed=embed, allowed_mentions=discord.AllowedMentions(users=True)
                )
            else:
                await channel.send(
                    content=text[:1800],
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
        except discord.HTTPException:
            log.debug("Welcome message failed in guild %s", member.guild.id, exc_info=True)

        if int(settings.get("dm_welcome", 0)):
            try:
                dm = Embeds.success(
                    f"Welcome to {truncate(member.guild.name, 150)}",
                    truncate(text, 1500),
                )
                await member.send(embed=dm)
            except (discord.Forbidden, discord.HTTPException):
                pass

    # ------------------------------------------------------ leave / goodbye
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Say goodbye when someone leaves (with the member and a settable message)."""
        guild = member.guild
        try:
            settings = await self.bot.db.get_guild(guild.id)
        except Exception:  # pragma: no cover - database problems must not crash
            log.exception("Could not read guild settings for leave message")
            return
        channel_id = settings.get("leave_channel")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await guild.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return
        if not isinstance(channel, discord.TextChannel):
            return

        template = str(settings.get("leave_message") or DEFAULT_LEAVE)
        text = render(template, member)
        try:
            if int(settings.get("leave_embed", 1)):
                embed = discord.Embed(
                    title=f"Member left {truncate(guild.name, 200)}",
                    description=truncate(text, 3500),
                    color=config.NEUTRAL_COLOR,
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_author(name=f"{member} left", icon_url=member.display_avatar.url)
                embed.add_field(name="Account created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
                if member.joined_at:
                    embed.add_field(
                        name="Was a member for",
                        value=format_duration((discord.utils.utcnow() - member.joined_at).total_seconds()),
                        inline=True,
                    )
                embed.add_field(name="Members now", value=str(guild.member_count), inline=True)
                embed.set_footer(text=config.FOOTER_TEXT)
                await channel.send(embed=embed)
            else:
                await channel.send(
                    content=text[:1800],
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
        except discord.HTTPException:
            log.debug("Leave message failed in guild %s", guild.id, exc_info=True)

    @commands.hybrid_group(name="leave", invoke_without_command=True)
    @commands.guild_only()
    @require_admin()
    async def leave(self, ctx: commands.Context) -> None:
        """Configure the goodbye/leave system."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        enabled = bool(settings.get("leave_channel"))
        embed = Embeds.info(
            "Goodbye / leave system",
            "**Commands**\n"
            "`/leave setup` — set the channel, message and style\n"
            "`/leave disable` — turn it off\n"
            "`/leave test` — preview the message\n"
            "`/leave view` — show current settings",
        )
        embed.add_field(name="Status", value="✅ enabled" if enabled else "❌ disabled", inline=True)
        if enabled:
            embed.add_field(name="Channel", value=f"<#{settings['leave_channel']}>", inline=True)
            embed.add_field(
                name="Embed", value="yes" if settings.get("leave_embed") else "no", inline=True
            )
        await ctx.send(embed=embed, ephemeral=True)

    @leave.command(name="setup")
    @require_admin()
    @app_commands.describe(
        channel="Where goodbyes are posted",
        message="Leave text with placeholders (default: a friendly goodbye)",
        use_embed="Post as an embed (default yes)",
    )
    async def leave_setup(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        message: Optional[str] = None,
        use_embed: bool = True,
    ) -> None:
        """Configure and enable the leave system."""
        if not channel.permissions_for(ctx.guild.me).send_messages:
            await ctx.send(
                embed=Embeds.error(
                    "Missing permission", f"I cannot send messages in {channel.mention}."
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.update_guild(
            ctx.guild.id,
            leave_channel=channel.id,
            leave_message=truncate(message or DEFAULT_LEAVE, 1500),
            leave_embed=int(use_embed),
        )
        embed = Embeds.success(
            "Goodbye system enabled",
            f"Channel: {channel.mention}\nEmbed: `{'yes' if use_embed else 'no'}`",
        )
        embed.add_field(name="Placeholders", value=LEAVE_PLACEHOLDER_HINT, inline=False)
        await ctx.send(embed=embed)

    @leave.command(name="disable")
    @require_admin()
    async def leave_disable(self, ctx: commands.Context) -> None:
        """Turn off leave messages."""
        await self.bot.db.update_guild(ctx.guild.id, leave_channel=None)
        await ctx.send(embed=Embeds.success("Goodbye system disabled", "Leaves will no longer be announced."))

    @leave.command(name="test")
    @require_admin()
    async def leave_test(self, ctx: commands.Context) -> None:
        """Preview the goodbye message as if you left right now."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        if not settings.get("leave_channel"):
            await ctx.send(
                embed=Embeds.error("Not configured", "Run `/leave setup` first."),
                ephemeral=True,
            )
            return
        template = str(settings.get("leave_message") or DEFAULT_LEAVE)
        preview = render(template, ctx.author)  # type: ignore[arg-type]
        embed = Embeds.info("Leave preview", truncate(preview, 1500))
        embed.add_field(name="Channel", value=f"<#{settings['leave_channel']}>", inline=True)
        embed.add_field(
            name="Style", value="embed" if settings.get("leave_embed") else "text", inline=True
        )
        await ctx.send(embed=embed, ephemeral=True)

    @leave.command(name="view")
    @require_admin()
    async def leave_view(self, ctx: commands.Context) -> None:
        """Show the current leave configuration."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        if not settings.get("leave_channel"):
            await ctx.send(
                embed=Embeds.info("Goodbye system", "Not configured yet."), ephemeral=True
            )
            return
        embed = Embeds.info("Goodbye configuration", "")
        embed.add_field(
            name="Channel", value=f"<#{settings['leave_channel'] }>", inline=True
        )
        embed.add_field(
            name="Embed",
            value="yes" if settings.get("leave_embed") else "no",
            inline=True,
        )
        embed.add_field(
            name="Message",
            value=truncate(str(settings.get("leave_message") or DEFAULT_LEAVE), 900),
            inline=False,
        )
        await ctx.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------ commands
    @commands.hybrid_group(name="welcome", invoke_without_command=True)
    @commands.guild_only()
    @require_admin()
    async def welcome(self, ctx: commands.Context) -> None:
        """Configure the welcome system."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        enabled = bool(settings.get("welcome_channel"))
        embed = Embeds.info(
            "Welcome system",
            "**Commands**\n"
            "`/welcome setup` — configure channel, message and options\n"
            "`/welcome disable` — turn it off\n"
            "`/welcome test` — preview the message\n"
            "`/welcome view` — show current settings",
        )
        embed.add_field(name="Status", value="✅ enabled" if enabled else "❌ disabled", inline=True)
        if enabled:
            embed.add_field(
                name="Channel", value=f"<#{settings['welcome_channel']}>", inline=True
            )
            embed.add_field(
                name="Embed", value="yes" if settings.get("welcome_embed") else "no", inline=True
            )
        await ctx.send(embed=embed, ephemeral=True)

    @welcome.command(name="setup")
    @require_admin()
    @app_commands.describe(
        channel="Where welcomes are posted",
        message="Welcome text with placeholders",
        use_embed="Post as an embed (default yes)",
        dm_welcome="Also DM the new member",
        autorole="Role to give automatically on join",
    )
    async def welcome_setup(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        message: Optional[str] = None,
        use_embed: bool = True,
        dm_welcome: bool = False,
        autorole: Optional[discord.Role] = None,
    ) -> None:
        """Configure and enable the welcome system."""
        if not channel.permissions_for(ctx.guild.me).send_messages:
            await ctx.send(
                embed=Embeds.error(
                    "Missing permission", f"I cannot send messages in {channel.mention}."
                ),
                ephemeral=True,
            )
            return

        fields: dict[str, object] = {
            "welcome_channel": channel.id,
            "welcome_message": truncate(message or DEFAULT_WELCOME, 1500),
            "welcome_embed": int(use_embed),
            "dm_welcome": int(dm_welcome),
        }

        if autorole is not None:
            check = perms.bot_can_assign_role(ctx.guild.me, autorole)
            if not check.ok:
                await ctx.send(
                    embed=Embeds.error("Auto role rejected", check.reason), ephemeral=True
                )
                return
            fields["autorole_id"] = autorole.id

        await self.bot.db.update_guild(ctx.guild.id, **fields)

        embed = Embeds.success(
            "Welcome system enabled",
            f"Channel: {channel.mention}\n"
            f"Embed: `{'yes' if use_embed else 'no'}`\n"
            f"DM: `{'yes' if dm_welcome else 'no'}`"
            + (f"\nAuto role: {autorole.mention}" if autorole else ""),
        )
        embed.add_field(name="Placeholders", value=PLACEHOLDER_HINT, inline=False)
        await ctx.send(embed=embed)

    @welcome.command(name="disable")
    @require_admin()
    async def welcome_disable(self, ctx: commands.Context) -> None:
        """Turn off welcome messages."""
        await self.bot.db.update_guild(
            ctx.guild.id, welcome_channel=None, dm_welcome=0
        )
        await ctx.send(embed=Embeds.success("Welcome disabled", "New members get no message."))

    @welcome.command(name="test")
    @require_admin()
    async def welcome_test(self, ctx: commands.Context) -> None:
        """Post a preview of the welcome message."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        if not settings.get("welcome_channel"):
            await ctx.send(
                embed=Embeds.error("Not configured", "Run `/welcome setup` first."),
                ephemeral=True,
            )
            return
        template = settings.get("welcome_message") or DEFAULT_WELCOME
        preview = render(str(template), ctx.author)  # type: ignore[arg-type]
        embed = Embeds.info("Welcome preview", truncate(preview, 1500))
        embed.add_field(
            name="Channel", value=f"<#{settings['welcome_channel']}>", inline=True
        )
        embed.add_field(
            name="Style", value="embed" if settings.get("welcome_embed") else "text", inline=True
        )
        await ctx.send(embed=embed, ephemeral=True)

    @welcome.command(name="view")
    @require_admin()
    async def welcome_view(self, ctx: commands.Context) -> None:
        """Show the current welcome configuration."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        if not settings.get("welcome_channel"):
            await ctx.send(
                embed=Embeds.info("Welcome", "Not configured yet."), ephemeral=True
            )
            return
        embed = Embeds.info("Welcome configuration", "")
        embed.add_field(
            name="Channel", value=f"<#{settings['welcome_channel']}>", inline=True
        )
        embed.add_field(
            name="Embed",
            value="yes" if settings.get("welcome_embed") else "no",
            inline=True,
        )
        embed.add_field(
            name="DM", value="yes" if settings.get("dm_welcome") else "no", inline=True
        )
        embed.add_field(
            name="Message",
            value=truncate(str(settings.get("welcome_message") or DEFAULT_WELCOME), 900),
            inline=False,
        )
        autorole = settings.get("autorole_id")
        embed.add_field(
            name="Auto role",
            value=f"<@&{autorole}>" if autorole else "`none`",
            inline=True,
        )
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
