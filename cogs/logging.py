"""📋 Logging system for RBX Family Manager.

Administrators configure separate channels for moderation, member, server and
message events with ``/setlogchannel``. Every event is optional: if a channel
type is not configured, its events are silently skipped (never crash).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import require_admin
from utils.embeds import Embeds
from utils.helpers import truncate

log = logging.getLogger("rbx.logging")

LOG_TYPES: dict[str, str] = {
    "all": "server_log_channel",  # special-cased below
    "moderation": "mod_log_channel",
    "member": "member_log_channel",
    "server": "server_log_channel",
    "message": "message_log_channel",
}

TYPE_ICONS: dict[str, str] = {
    "mod_log_channel": "🛡️",
    "member_log_channel": "👋",
    "server_log_channel": "🖥️",
    "message_log_channel": "💬",
}


def _account_age(user: discord.abc.User) -> str:
    created = user.created_at
    days = (datetime.now(timezone.utc) - created).days
    if days < 1:
        return "Today"
    if days < 30:
        return f"{days} day(s) ago"
    if days < 365:
        return f"{days // 30} month(s) ago"
    return f"{days // 365} year(s) ago"


class Logging(commands.Cog, name="Logging"):
    """Event logging into configurable channels."""

    category = "⚙️ Server"
    category_description = "Configure logging and server behaviour."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------ commands
    @commands.hybrid_command(name="setlogchannel")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(
        channel="Channel that receives the logs",
        type="Which log type this channel should receive",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name="all logs (default)", value="all"),
            app_commands.Choice(name="moderation logs", value="moderation"),
            app_commands.Choice(name="member logs", value="member"),
            app_commands.Choice(name="server logs", value="server"),
            app_commands.Choice(name="message logs", value="message"),
        ]
    )
    async def setlogchannel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        type: str = "all",  # noqa: A002 - Discord option name
    ) -> None:
        """Set the channel that receives bot logs."""
        if not _can_send(channel, ctx.me):
            await ctx.send(
                embed=Embeds.error(
                    "Cannot post there",
                    "I need **Send Messages** and **Embed Links** in that channel.",
                ),
                ephemeral=True,
            )
            return

        if type == "all":
            fields = {k: v for k, v in LOG_TYPES.items() if k != "all"}
            await self.bot.db.update_guild(ctx.guild.id, **fields)
            label = "All log types"
        else:
            key = LOG_TYPES[type]
            await self.bot.db.update_guild(ctx.guild.id, **{key: channel.id})
            label = f"{type.title()} logs"

        await ctx.send(
            embed=Embeds.success(
                "Logging configured",
                f"{label} will be sent to {channel.mention}.",
            )
        )
        await self.bot.log_to_channel(
            ctx.guild,
            "server_log_channel",
            Embeds.log(
                "Logging configured",
                "",
                fields=[("Channel", channel.mention, True), ("Type", label, True)],
                actor=ctx.author,
            ),
        )

    @commands.hybrid_command(name="logchannels")
    @commands.guild_only()
    @require_admin()
    async def logchannels(self, ctx: commands.Context) -> None:
        """Show the currently configured log channels."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        embed = Embeds.info("Log channels", "Use `/setlogchannel channel:#logs type:...` to change them.")
        for key, label in (
            ("mod_log_channel", "Moderation"),
            ("member_log_channel", "Members"),
            ("server_log_channel", "Server"),
            ("message_log_channel", "Messages"),
        ):
            channel_id = settings.get(key)
            mention = f"<#{channel_id}>" if channel_id else "`not set`"
            embed.add_field(name=f"{TYPE_ICONS[key]} {label}", value=mention, inline=True)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="logtest")
    @commands.guild_only()
    @require_admin()
    async def logtest(self, ctx: commands.Context) -> None:
        """Send a sample log entry to every configured log channel."""
        sample = Embeds.log(
            "Log test",
            "RBX Family Manager logging is working.",
            fields=[("Sent by", ctx.author.mention, True)],
            actor=ctx.author,
        )
        sent = 0
        for key in (
            "mod_log_channel",
            "member_log_channel",
            "server_log_channel",
            "message_log_channel",
        ):
            if (await self.bot.db.get_guild(ctx.guild.id)).get(key):
                await self.bot.log_to_channel(ctx.guild, key, sample)
                sent += 1
        await ctx.send(
            embed=Embeds.success(
                "Test sent",
                f"Sample log pushed to **{sent}** configured channel(s). "
                "Check them now — unconfigured types are skipped.",
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------- messages
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        content = message.content or ""
        if not content and message.attachments:
            content = "[attachment] " + ", ".join(a.url for a in message.attachments)
        if not content and message.embeds:
            content = "[embed]"
        embed = Embeds.log(
            "Message deleted",
            truncate(content, 1000) or "_empty message_",
            color=0xED4245,
            fields=[
                ("Author", message.author.mention, True),
                ("Channel", message.channel.mention, True),
                ("ID", f"`{message.id}`", True),
            ],
            actor=message.author,
        )
        await self.bot.log_to_channel(message.guild, "message_log_channel", embed)

    @commands.Cog.listener()
    async def on_message_delete_bulk(
        self, messages: list[discord.Message]
    ) -> None:
        if not messages or messages[0].guild is None:
            return
        channel = messages[0].channel
        embed = Embeds.log(
            "Messages purged (bulk delete)",
            "",
            color=0xED4245,
            fields=[
                ("Count", str(len(messages)), True),
                ("Channel", channel.mention, True),
                ("First author", messages[0].author.mention, True),
            ],
            actor=messages[0].author,
        )
        await self.bot.log_to_channel(messages[0].guild, "message_log_channel", embed)

    @commands.Cog.listener()
    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return  # embed/image refresh only
        embed = Embeds.log(
            "Message edited",
            "",
            color=0xFEE75C,
            fields=[
                ("Author", before.author.mention, True),
                ("Channel", before.channel.mention, True),
                ("Before", truncate(before.content or "_empty_", 900), False),
                ("After", truncate(after.content or "_empty_", 900), False),
                ("Jump", f"[go to message]({after.jump_url})", True),
            ],
            actor=before.author,
        )
        await self.bot.log_to_channel(before.guild, "message_log_channel", embed)

    # ------------------------------------------------------------- members
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        embed = Embeds.log(
            "Member joined",
            "",
            fields=[
                ("Member", f"{member.mention} (`{member.id}`)", True),
                ("Account age", _account_age(member), True),
                ("Members", str(member.guild.member_count), True),
            ],
            actor=member,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.bot.log_to_channel(member.guild, "member_log_channel", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        roles = [r.mention for r in member.roles[1:][:8]]
        embed = Embeds.log(
            "Member left",
            "",
            color=0xED4245,
            fields=[
                ("Member", f"`{member.id}`", True),
                ("Top role", member.top_role.mention, True),
                ("Members", str(member.guild.member_count), True),
                ("Roles", ", ".join(roles) or "`—`", False),
            ],
            actor=member,
        )
        await self.bot.log_to_channel(member.guild, "member_log_channel", embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = Embeds.log(
            "Member banned",
            "",
            color=0xED4245,
            fields=[("User", f"`{user.id}`\n{user}", True)],
            actor=user,
        )
        await self.bot.log_to_channel(guild, "member_log_channel", embed)
        await self.bot.log_to_channel(guild, "mod_log_channel", embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = Embeds.log(
            "Member unbanned",
            "",
            fields=[("User", f"`{user.id}`\n{user}", True)],
            actor=user,
        )
        await self.bot.log_to_channel(guild, "member_log_channel", embed)
        await self.bot.log_to_channel(guild, "mod_log_channel", embed)

    @commands.Cog.listener()
    async def on_member_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        # Nickname change.
        if before.nick != after.nick:
            embed = Embeds.log(
                "Nickname changed",
                "",
                fields=[
                    ("Member", after.mention, True),
                    ("Before", truncate(before.nick or "`none`", 100), True),
                    ("After", truncate(after.nick or "`none`", 100), True),
                ],
                actor=after,
            )
            await self.bot.log_to_channel(after.guild, "member_log_channel", embed)

        # Timeout applied / lifted.
        before_until = before.timed_out_until
        after_until = after.timed_out_until
        if before_until != after_until:
            if after_until is None:
                description = "Timeout removed."
                action = "Timeout removed"
            else:
                until = int(after_until.timestamp())
                description = f"Until <t:{until}:F> (<t:{until}:R>)"
                action = "Member timed out"
            embed = Embeds.log(
                action,
                "",
                color=0xFEE75C if after_until else 0x57F287,
                fields=[("Member", after.mention, True)],
                actor=after,
            )
            if description:
                embed.add_field(name="Details", value=description, inline=False)
            await self.bot.log_to_channel(after.guild, "mod_log_channel", embed)

        # Role changes.
        if before.roles != after.roles:
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            if added or removed:
                parts: list[str] = []
                if added:
                    parts.append("Added: " + ", ".join(r.mention for r in added[:10]))
                if removed:
                    parts.append("Removed: " + ", ".join(r.mention for r in removed[:10]))
                embed = Embeds.log(
                    "Roles updated",
                    "\n".join(parts),
                    fields=[("Member", after.mention, True)],
                    actor=after,
                )
                await self.bot.log_to_channel(after.guild, "server_log_channel", embed)

    # --------------------------------------------------------------- roles
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = Embeds.log(
            "Role created",
            "",
            fields=[
                ("Role", role.mention, True),
                ("ID", f"`{role.id}`", True),
                ("Color", str(role.colour) if role.colour.value else "`default`", True),
            ],
        )
        await self.bot.log_to_channel(role.guild, "server_log_channel", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = Embeds.log(
            "Role deleted",
            "",
            color=0xED4245,
            fields=[("Role", f"`{role.name}`", True), ("ID", f"`{role.id}`", True)],
        )
        await self.bot.log_to_channel(role.guild, "server_log_channel", embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"Name: `{before.name}` → `{after.name}`")
        if before.colour != after.colour:
            changes.append(f"Color: `{before.colour}` → `{after.colour}`")
        if before.permissions != after.permissions:
            changes.append("Permissions changed")
        if before.hoist != after.hoist:
            changes.append(f"Hoisted: `{before.hoist}` → `{after.hoist}`")
        if before.mentionable != after.mentionable:
            changes.append(f"Mentionable: `{before.mentionable}` → `{after.mentionable}`")
        if not changes:
            return
        embed = Embeds.log(
            "Role updated",
            "\n".join(changes),
            fields=[("Role", after.mention, True)],
        )
        await self.bot.log_to_channel(after.guild, "server_log_channel", embed)

    # ------------------------------------------------------------- channels
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        embed = Embeds.log(
            "Channel created",
            "",
            fields=[
                ("Channel", channel.mention if isinstance(channel, discord.TextChannel) else f"`{channel.name}`", True),
                ("Type", str(channel.type), True),
                ("ID", f"`{channel.id}`", True),
            ],
        )
        await self.bot.log_to_channel(channel.guild, "server_log_channel", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        embed = Embeds.log(
            "Channel deleted",
            "",
            color=0xED4245,
            fields=[
                ("Channel", f"`{channel.name}`", True),
                ("Type", str(channel.type), True),
                ("ID", f"`{channel.id}`", True),
            ],
        )
        await self.bot.log_to_channel(channel.guild, "server_log_channel", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"Name: `{before.name}` → `{after.name}`")
        if isinstance(before, discord.TextChannel) and isinstance(after, discord.TextChannel):
            if before.topic != after.topic:
                changes.append(f"Topic: `{truncate(after.topic or 'none', 200)}`")
            if before.slowmode_delay != after.slowmode_delay:
                changes.append(
                    f"Slowmode: `{before.slowmode_delay}s` → `{after.slowmode_delay}s`"
                )
        if not changes:
            return
        embed = Embeds.log(
            "Channel updated",
            "\n".join(changes),
            fields=[("Channel", after.mention, True)],
        )
        await self.bot.log_to_channel(after.guild, "server_log_channel", embed)

    # ---------------------------------------------------------------- voice
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        if before.channel is None and after.channel is not None:
            title, color = "Voice channel join", 0x57F287
        elif before.channel is not None and after.channel is None:
            title, color = "Voice channel leave", 0xED4245
        elif before.channel != after.channel:
            title, color = "Voice channel move", 0x3498DB
        else:
            return
        fields = [("Member", member.mention, True)]
        if before.channel and after.channel:
            fields.append(("From", f"`{before.channel.name}`", True))
            fields.append(("To", f"`{after.channel.name}`", True))
        elif after.channel:
            fields.append(("Channel", f"`{after.channel.name}`", True))
        elif before.channel:
            fields.append(("Channel", f"`{before.channel.name}`", True))
        embed = Embeds.log(title, "", color=color, fields=fields, actor=member)
        await self.bot.log_to_channel(member.guild, "server_log_channel", embed)

    # -------------------------------------------------------------- webhooks
    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.TextChannel) -> None:
        """Any webhook create/delete/edit in a channel — prime backdoor signal."""
        webhooks = []
        try:
            webhooks = await channel.webhooks()
        except discord.HTTPException:  # pragma: no cover - best effort
            pass
        listing = "\n".join(
            f"• `{w.name or 'unnamed'}` (by {f'<@{w.user.id}>' if w.user else '?uid'})"
            for w in webhooks[:10]
        ) or "_none readable_"
        embed = Embeds.log(
            "Webhooks changed",
            listing,
            color=0xFEE75C,
            fields=[("Channel", channel.mention, True)],
        )
        await self.bot.log_to_channel(channel.guild, "server_log_channel", embed)

    # --------------------------------------------------------------- threads
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        embed = Embeds.log(
            "Thread created",
            "",
            fields=[
                ("Thread", f"`{thread.name}`", True),
                ("Starter", getattr(thread.starter_message, "author", "?") if thread.starter_message else "`—`", True),
            ],
        )
        await self.bot.log_to_channel(thread.guild, "server_log_channel", embed)

    @commands.Cog.listener()
    async def on_thread_join(self, thread: discord.Thread) -> None:
        # Fires for bots only when we gain access to an existing thread.
        embed = Embeds.log(
            "Bot joined a thread",
            f"`{thread.name}`",
            fields=[("ID", f"`{thread.id}`", True)],
        )
        await self.bot.log_to_channel(thread.guild, "server_log_channel", embed)

    # ----------------------------------------------------------------- guild
    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        changes: list[str] = []
        if before.name != after.name:
            changes.append(f"Name: `{before.name}` → `{after.name}`")
        if before.description != after.description:
            changes.append("Description changed")
        if before.verification_level != after.verification_level:
            changes.append(
                f"Verification: `{before.verification_level.name}` → `{after.verification_level.name}`"
            )
        if before.rules_channel != after.rules_channel:
            changes.append("Rules channel changed")
        if not changes:
            return
        embed = Embeds.log(
            "Server settings updated",
            "\n".join(changes),
            fields=[("Server ID", f"`{after.id}`", True)],
        )
        await self.bot.log_to_channel(after, "server_log_channel", embed)


def _can_send(channel: discord.abc.Messageable, member: Optional[discord.Member]) -> bool:
    if member is None or not isinstance(channel, discord.TextChannel):
        return True
    perms = channel.permissions_for(member)
    return perms.send_messages and perms.embed_links


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Logging(bot))
