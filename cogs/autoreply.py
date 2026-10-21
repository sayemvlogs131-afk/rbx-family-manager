"""Custom trigger→response replies for RBX Family Manager.

Admins configure trigger→response pairs per server. When a message in any
text channel contains a configured trigger, the bot replies in that channel
with the stored response. No prefix is required.

Matching is substring-based and case-insensitive. Only the first matching
trigger in a message is used, so one message triggers one reply.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import require_admin
from utils.embeds import Embeds
from utils.helpers import truncate

log = logging.getLogger("rbx.autoreply")


def _looks_like_text_channel(channel: Any) -> bool:
    return bool(
        getattr(channel, "guild", None)
        and getattr(channel, "permissions_for", None)
        and getattr(channel, "send_messages", True)
    )


def _channel_can_send(channel: Any, guildme: Any) -> bool:
    try:
        perms = channel.permissions_for(guildme)
    except Exception:
        return False
    return bool(getattr(perms, "send_messages", False))

MAX_TRIGGER_LENGTH = 100
MAX_RESPONSE_LENGTH = 1000


def _normalize(text: str) -> str:
    return text.strip().lower()


def _store_trigger(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    if len(text) > MAX_TRIGGER_LENGTH:
        return None
    return text


def _store_response(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    if len(text) > MAX_RESPONSE_LENGTH:
        return None
    return text


class AutoReply(commands.Cog, name="AutoReplies"):
    """Trigger-based auto responses configured by admins."""

    category = "💬 AutoReplies"
    category_description = "Configure custom trigger→response replies."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------ helpers
    async def _get_entries(self, guild_id: int) -> list[dict[str, Any]]:
        try:
            return await self.bot.db.autoreply_entries(guild_id)
        except Exception:
            log.debug("Failed to load autoreply entries for guild %s", guild_id, exc_info=True)
            return []

    async def _is_enabled(self, guild_id: int) -> bool:
        try:
            settings = await self.bot.db.get_guild(guild_id)
            return bool(settings.get("autoreply_enabled", 1))
        except Exception:
            log.debug("Failed to load autoreply enabled flag for guild %s", guild_id, exc_info=True)
            return True

    async def _set_enabled(self, guild_id: int, enabled: bool) -> None:
        try:
            await self.bot.db.update_guild(guild_id, autoreply_enabled=1 if enabled else 0)
        except Exception:
            log.debug("Failed to set autoreply enabled flag for guild %s", guild_id, exc_info=True)

    async def _trigger_for_message(self, message: discord.Message) -> dict[str, Any] | None:
        if not _looks_like_text_channel(message.channel):
            return None
        if message.author.bot:
            return None
        if not await self._is_enabled(message.guild.id):
            return None
        entries = await self._get_entries(message.guild.id)
        if not entries:
            return None
        normalized = _normalize(message.content)
        for entry in entries:
            trigger = _normalize(str(entry.get("trigger") or ""))
            if not trigger:
                continue
            if trigger in normalized:
                return entry
        return None

    async def _maybe_reply(self, message: discord.Message) -> None:
        entry = await self._trigger_for_message(message)
        if entry is None:
            return
        response = _store_response(str(entry.get("response") or ""))
        if not response:
            return
        try:
            if _channel_can_send(message.channel, message.guild.me):
                await message.channel.send(response)
        except discord.HTTPException:
            log.debug("AutoReply failed to send reply in %s/%s", message.guild.id, message.channel.id, exc_info=True)

    # -------------------------------------------------------------- events
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        await self._maybe_reply(message)

    # ------------------------------------------------------------ commands
    @commands.hybrid_group(name="autoreply", invoke_without_command=True)
    @commands.guild_only()
    @app_commands.describe(
        text="",
    )
    async def autoreply(self, ctx: commands.Context) -> None:
        """Custom auto replies. Add triggers → responses, then the bot replies in-server."""
        await ctx.send(
            embed=Embeds.info(
                "Auto replies",
                "**Commands**\n"
                "`/autoreply list` — show your auto replies\n"
                "`/autoreply add trigger: text response: text` — add a reply\n"
                "`/autoreply remove id:` — delete a reply\n"
                "`/autoreply toggle` — turn auto replies on/off\n"
                "`/autoreply test trigger:` — preview matching",
            ),
            ephemeral=True,
        )

    @autoreply.command(name="list")
    @commands.guild_only()
    @app_commands.describe()
    async def autoreply_list(self, ctx: commands.Context) -> None:
        """Show all configured auto replies in this server."""
        entries = await self._get_entries(ctx.guild.id)
        if not entries:
            await ctx.send(
                embed=Embeds.info(
                    "Auto replies",
                    "No auto replies configured.\n"
                    "Add one with `/autoreply add trigger: rules response: #rules`.",
                ),
                ephemeral=True,
            )
            return
        lines = []
        for entry in entries:
            trigger = str(entry.get("trigger") or "")
            response = str(entry.get("response") or "")
            lines.append(f"**{trigger}** → {truncate(response, 120)}")
        await ctx.send(
            embed=Embeds.command_list(lines, "💬 Auto replies"),
            ephemeral=True,
        )

    @autoreply.command(name="add")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(
        trigger="Word or phrase that triggers the reply",
        response="Message the bot will send",
    )
    async def autoreply_add(
        self,
        ctx: commands.Context,
        trigger: str,
        response: str,
    ) -> None:
        """Add a trigger → response pair."""
        trigger_text = _store_trigger(trigger)
        if trigger_text is None:
            await ctx.send(
                embed=Embeds.error(
                    "Invalid trigger",
                    "Trigger must be 1-100 characters and not empty.",
                ),
                ephemeral=True,
            )
            return
        response_text = _store_response(response)
        if response_text is None:
            await ctx.send(
                embed=Embeds.error(
                    "Invalid response",
                    "Response must be 1-1000 characters and not empty.",
                ),
                ephemeral=True,
            )
            return

        try:
            row = await self.bot.db.autoreply_add(ctx.guild.id, trigger_text, response_text)
        except Exception:
            log.debug("Failed to add autoreply for guild %s", ctx.guild.id, exc_info=True)
            await ctx.send(
                embed=Embeds.error("Save failed", "Could not save the auto reply."),
                ephemeral=True,
            )
            return

        await ctx.send(
            embed=Embeds.success(
                "Auto reply added",
                f"**{trigger_text}** → {truncate(response_text, 120)}\n"
                f"ID: `{row['id']}`",
            ),
            ephemeral=True,
        )

    @autoreply.command(name="remove")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(id="ID of the auto reply to remove")
    async def autoreply_remove(self, ctx: commands.Context, id: int) -> None:
        """Remove an auto reply by ID."""
        removed = await self._remove_entry(ctx.guild.id, id)
        if not removed:
            await ctx.send(
                embed=Embeds.error(
                    "Not found",
                    f"No auto reply with ID `{id}` here.",
                ),
                ephemeral=True,
            )
            return
        await ctx.send(
            embed=Embeds.success("Auto reply removed", f"Removed auto reply `#{id}`."),
            ephemeral=True,
        )

    async def _remove_entry(self, guild_id: int, entry_id: int) -> bool:
        try:
            return bool(await self.bot.db.autoreply_remove(guild_id, entry_id))
        except Exception:
            log.debug("Failed to remove autoreply %s from guild %s", entry_id, guild_id, exc_info=True)
            return False

    @autoreply.command(name="toggle")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(enabled="Turn auto replies on or off")
    async def autoreply_toggle(self, ctx: commands.Context, enabled: bool) -> None:
        """Enable or disable auto replies in this server."""
        await self._set_enabled(ctx.guild.id, enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(
            embed=Embeds.success("Auto replies updated", f"Auto replies are now **{status}**."),
            ephemeral=True,
        )

    @autoreply.command(name="test")
    @commands.guild_only()
    @app_commands.describe(trigger="Test whether this trigger would match")
    async def autoreply_test(self, ctx: commands.Context, trigger: str) -> None:
        """Check whether a trigger would match a sample message."""
        trigger_text = _store_trigger(trigger)
        if trigger_text is None:
            await ctx.send(
                embed=Embeds.error(
                    "Invalid trigger",
                    "Trigger must be 1-100 characters and not empty.",
                ),
                ephemeral=True,
            )
            return

        fake_message = discord.Message(
            state=None,
            id=0,
            author=ctx.author,
            channel=ctx.channel,
            guild=ctx.guild,
            content=trigger_text,
            created_at=ctx.message.created_at if ctx.message else None,
        )
        entry = await self._trigger_for_message(fake_message)
        if entry is None:
            await ctx.send(
                embed=Embeds.info(
                    "Test result",
                    f"Trigger `{trigger}` did not match any configured auto reply.",
                ),
                ephemeral=True,
            )
            return
        response = str(entry.get("response") or "")
        await ctx.send(
            embed=Embeds.success(
                "Test result",
                f"Trigger `{trigger}` would match:\n{response}",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoReply(bot))
