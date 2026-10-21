"""🛡️ Moderation commands for RBX Family Manager.

Every action passes three gates before touching Discord:

1. author permission checks (``utils.checks``)
2. hierarchy safety (``utils.permissions.can_moderate``)
3. DM to target (best effort) + database record + moderation log embed
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import permissions as perms
from utils.checks import (
    command_enabled,
    require_admin,
    require_moderator,
    require_permissions,
)
from utils.embeds import Embeds
from utils.helpers import MENTION_RE, truncate
from utils.pagination import ConfirmView, Paginator
from utils.time_parser import MAX_TIMEOUT_SECONDS, format_duration, parse_duration

log = logging.getLogger("rbx.moderation")

MIN_TIMEOUT_SECONDS = 6


def parse_user_list(raw: str) -> list[int]:
    """Extract user IDs from a space/comma separated list of mentions or IDs."""
    ids: list[int] = []
    for token in re.split(r"[\s,]+", raw.strip()):
        if not token:
            continue
        mention = MENTION_RE.fullmatch(token)
        if mention:
            ids.append(int(mention.group("id")))
        elif token.isdigit():
            ids.append(int(token))
    return list(dict.fromkeys(ids))  # de-duplicate, keep order


class WarnModal(discord.ui.Modal, title="Warn a member"):
    """Modal that collects the reason for the /warn context menu."""

    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        placeholder="Why are they being warned?",
        default="No reason provided",
        required=False,
        max_length=500,
    )

    def __init__(self, cog: "Moderation", target: discord.Member) -> None:
        super().__init__()
        self.cog = cog
        self.target = target

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This can only be used inside a server.", ephemeral=True
            )
            return
        reason = str(self.reason.value or "").strip() or "No reason provided"
        warning_id, total = await self.cog._warn_member(
            guild, interaction.user, self.target, reason
        )
        embed = Embeds.success(
            "Warning recorded",
            f"**{self.target}** now has **{total}** warning(s).\n"
            f"Warning ID: `{warning_id}`",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:  # pragma: no cover - defensive
        log.exception("Warn modal failed", exc_info=error)
        try:
            await interaction.response.send_message(
                "Something went wrong while recording the warning.", ephemeral=True
            )
        except discord.HTTPException:
            pass


class Moderation(commands.Cog, name="Moderation"):
    """Member punishment and channel control commands."""

    category = "🛡️ Moderation"
    category_description = "Manage members, punishments and channels."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------- helpers
    def _safety(
        self, ctx: commands.Context, target: discord.abc.User, *, allow_self: bool = False
    ) -> str | None:
        """Return a user-facing reason when the action is unsafe, else None."""
        guild = ctx.guild
        if guild is None:
            return "This command only works inside a server."
        if not isinstance(target, discord.Member):
            return None  # unbanned/plain users are not in the hierarchy
        result = perms.can_moderate(
            ctx.author,
            target,
            guild_owner_id=guild.owner_id,
            bot_member=guild.me,
            bot_owner_ids=config.OWNER_IDS,
            allow_self=allow_self,
        )
        return None if result.ok else result.reason

    async def _dm(self, target: discord.abc.User, embed: discord.Embed) -> bool:
        try:
            await target.send(embed=embed)
            return True
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            return False

    async def _confirm(
        self, ctx: commands.Context, embed: discord.Embed, label: str
    ) -> bool:
        """Show a confirmation button and return the answer."""
        view = ConfirmView(author_id=ctx.author.id, confirm_label=label)
        message = await ctx.send(embed=embed, view=view)
        await view.wait()
        if not view.value:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            return False
        try:
            await message.edit(view=None)
        except discord.HTTPException:
            pass
        return True

    async def _record_guild(
        self,
        guild: discord.Guild,
        actor: discord.abc.User,
        target: discord.abc.User,
        action: str,
        reason: str,
        duration: str | None = None,
    ) -> int:
        """Persist a case and post the moderation log embed."""
        case_id = await self.bot.db.log_moderation(
            guild.id, target.id, actor.id, action, reason, duration
        )
        embed = Embeds.moderation_action(
            action=action,
            target=target,
            moderator=actor,
            reason=reason,
            duration=duration,
            case_id=case_id,
        )
        await self.bot.log_to_channel(guild, "mod_log_channel", embed)
        return case_id

    async def _record(
        self,
        ctx: commands.Context,
        target: discord.abc.User,
        action: str,
        reason: str,
        duration: str | None = None,
    ) -> int:
        assert ctx.guild is not None
        return await self._record_guild(
            ctx.guild, ctx.author, target, action, reason, duration
        )

    async def _warn_member(
        self,
        guild: discord.Guild,
        actor: discord.abc.User,
        target: discord.Member,
        reason: str,
    ) -> tuple[int, int]:
        """Shared warn flow used by /warn and the "Warn member" context menu.

        Returns ``(warning_id, total_warnings)``.
        """
        warning_id = await self.bot.db.add_warning(
            guild.id, target.id, actor.id, reason
        )
        total = await self.bot.db.warning_count(guild.id, target.id)
        await self._dm(
            target,
            Embeds.warning(
                "You received a warning",
                f"**{guild.name}**\nReason: {truncate(reason, 400)}\n"
                f"Total warnings: **{total}**",
            ),
        )
        await self._record_guild(guild, actor, target, "warn", reason)
        return warning_id, total

    @staticmethod
    def _api_error(exc: discord.HTTPException, verb: str) -> str:
        if isinstance(exc, discord.Forbidden):
            return (
                f"I don't have permission to {verb}. Check my role position and "
                "the **Ban Members** / **Kick Members** / **Moderate Members** permissions."
            )
        return f"Discord returned an error ({exc.status}): {truncate(str(exc), 200)}"

    # ----------------------------------------------------------------- ban
    @commands.hybrid_command(name="ban")
    @commands.guild_only()
    @command_enabled("ban")
    @require_permissions("ban_members")
    @commands.cooldown(3, 5, commands.BucketType.member)
    @app_commands.describe(
        user="Member to ban",
        reason="Why they are being banned (logged)",
        delete_days="Delete their messages from the last N days (0-7)",
    )
    async def ban(
        self,
        ctx: commands.Context,
        user: discord.Member,
        reason: str = "No reason provided",
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        """Ban a member from the server."""
        error = self._safety(ctx, user)
        if error:
            await ctx.send(embed=Embeds.error("Cannot ban", error), ephemeral=True)
            return

        target = Embeds.warning(
            "Confirm ban",
            f"Ban **{user}** (`{user.id}`) from **{ctx.guild.name}**?\n"
            f"Reason: {truncate(reason, 200)}"
            + (f"\nMessages deleted: last **{delete_days}** day(s)." if delete_days else ""),
        )
        if not await self._confirm(ctx, target, "Ban"):
            return

        dm_sent = await self._dm(
            user,
            Embeds.warning(
                "You were banned",
                f"**{ctx.guild.name}**\nReason: {truncate(reason, 500)}",
            ),
        )
        try:
            await user.ban(
                reason=f"{ctx.author} | {reason}",
                delete_message_days=delete_days,
            )
        except discord.HTTPException as exc:
            log.warning("Ban failed for %s in %s: %s", user, ctx.guild.id, exc)
            await ctx.send(
                embed=Embeds.error("Ban failed", self._api_error(exc, "ban that member")),
                ephemeral=True,
            )
            return

        case_id = await self._record(ctx, user, "ban", reason)
        await ctx.send(
            embed=Embeds.success(
                "Member banned",
                f"**{user}** was banned (case `#{case_id}`)."
                + ("" if dm_sent else "\n_They could not be DMed._"),
            )
        )

    @commands.hybrid_command(name="softban")
    @commands.guild_only()
    @command_enabled("softban")
    @require_permissions("ban_members")
    @commands.cooldown(3, 10, commands.BucketType.member)
    @app_commands.describe(user="Member to softban", reason="Why (logged)")
    async def softban(
        self, ctx: commands.Context, user: discord.Member, reason: str = "No reason provided"
    ) -> None:
        """Ban then immediately unban to purge a member's recent messages."""
        error = self._safety(ctx, user)
        if error:
            await ctx.send(embed=Embeds.error("Cannot softban", error), ephemeral=True)
            return

        if not await self._confirm(
            ctx,
            Embeds.warning(
                "Confirm softban",
                f"Softban **{user}**? Their recent messages will be purged, "
                "then they are unbanned and may rejoin.\n"
                f"Reason: {truncate(reason, 200)}",
            ),
            "Softban",
        ):
            return

        try:
            await user.ban(reason=f"[softban] {ctx.author} | {reason}", delete_message_days=1)
            await ctx.guild.unban(user, reason=f"[softban] purge by {ctx.author}")
        except discord.HTTPException as exc:
            await ctx.send(
                embed=Embeds.error("Softban failed", self._api_error(exc, "softban that member")),
                ephemeral=True,
            )
            return

        await self._record(ctx, user, "softban", reason)
        await ctx.send(
            embed=Embeds.success(
                "Softban complete", f"**{user}** was softbanned and may rejoin."
            )
        )

    @commands.hybrid_command(name="unban")
    @commands.guild_only()
    @command_enabled("unban")
    @require_permissions("ban_members")
    @commands.cooldown(3, 10, commands.BucketType.member)
    @app_commands.describe(user="User ID (or name) of the banned account", reason="Why")
    async def unban(
        self, ctx: commands.Context, user: str, reason: str = "No reason provided"
    ) -> None:
        """Lift a ban by user ID or username."""
        assert ctx.guild is not None
        # fetch_ban is a network call; acknowledge first.
        await ctx.defer()
        user_id: int | None = None
        if user.strip().isdigit():
            user_id = int(user.strip())

        entry: discord.BanEntry | None = None
        try:
            if user_id is not None:
                entry = await ctx.guild.fetch_ban(discord.Object(id=user_id))
            else:
                name = user.strip().lower()
                async for ban in ctx.guild.bans(limit=1000):
                    if ban.user.name.lower() == name:
                        entry = ban
                        break
        except discord.NotFound:
            await ctx.send(
                embed=Embeds.error("Not banned", "No ban found for that user."),
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Unban failed", str(exc)), ephemeral=True)
            return

        if entry is None:
            await ctx.send(
                embed=Embeds.error(
                    "Not banned", "That user is not banned (or the ID is wrong)."
                ),
                ephemeral=True,
            )
            return

        try:
            await ctx.guild.unban(entry.user, reason=f"{ctx.author} | {reason}")
        except discord.HTTPException as exc:
            await ctx.send(
                embed=Embeds.error("Unban failed", self._api_error(exc, "unban")),
                ephemeral=True,
            )
            return

        await self._record(ctx, entry.user, "unban", reason)
        await ctx.send(
            embed=Embeds.success("Ban lifted", f"**{entry.user}** may rejoin the server.")
        )

    # ---------------------------------------------------------------- kick
    @commands.hybrid_command(name="kick")
    @commands.guild_only()
    @command_enabled("kick")
    @require_permissions("kick_members")
    @commands.cooldown(3, 5, commands.BucketType.member)
    @app_commands.describe(user="Member to kick", reason="Why (logged)")
    async def kick(
        self, ctx: commands.Context, user: discord.Member, reason: str = "No reason provided"
    ) -> None:
        """Kick a member from the server."""
        error = self._safety(ctx, user)
        if error:
            await ctx.send(embed=Embeds.error("Cannot kick", error), ephemeral=True)
            return

        if not await self._confirm(
            ctx,
            Embeds.warning(
                "Confirm kick",
                f"Kick **{user}** from **{ctx.guild.name}**?\n"
                f"Reason: {truncate(reason, 200)}",
            ),
            "Kick",
        ):
            return

        dm_sent = await self._dm(
            user,
            Embeds.warning(
                "You were kicked",
                f"**{ctx.guild.name}**\nReason: {truncate(reason, 500)}",
            ),
        )
        try:
            await user.kick(reason=f"{ctx.author} | {reason}")
        except discord.HTTPException as exc:
            await ctx.send(
                embed=Embeds.error("Kick failed", self._api_error(exc, "kick that member")),
                ephemeral=True,
            )
            return

        await self._record(ctx, user, "kick", reason)
        await ctx.send(
            embed=Embeds.success(
                "Member kicked",
                f"**{user}** was kicked." + ("" if dm_sent else "\n_They could not be DMed._"),
            )
        )

    # ----------------------------------------------------------- timeouts
    async def _timeout(
        self, ctx: commands.Context, user: discord.Member, duration_text: str, reason: str
    ) -> None:
        error = self._safety(ctx, user, allow_self=True)
        if error:
            await ctx.send(embed=Embeds.error("Cannot time out", error), ephemeral=True)
            return
        if user.id == ctx.author.id:
            await ctx.send(
                embed=Embeds.error("Cannot time out", "You cannot time yourself out."),
                ephemeral=True,
            )
            return

        try:
            seconds = parse_duration(duration_text, max_seconds=MAX_TIMEOUT_SECONDS)
            if seconds < MIN_TIMEOUT_SECONDS:
                raise ValueError("Timeouts must be at least 6 seconds.")
        except ValueError as exc:
            await ctx.send(embed=Embeds.error("Invalid duration", str(exc)), ephemeral=True)
            return

        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        pretty = format_duration(seconds)

        try:
            await user.timeout(until, reason=f"{ctx.author} | {reason}")
        except discord.HTTPException as exc:
            await ctx.send(
                embed=Embeds.error(
                    "Timeout failed", self._api_error(exc, "time out that member")
                ),
                ephemeral=True,
            )
            return

        await self._dm(
            user,
            Embeds.warning(
                "You were timed out",
                f"**{ctx.guild.name}**\nDuration: {pretty}\n"
                f"Reason: {truncate(reason, 400)}",
            ),
        )
        await self._record(ctx, user, "timeout", reason, pretty)
        await ctx.send(
            embed=Embeds.success(
                "Member timed out",
                f"**{user}** is timed out for **{pretty}** (<t:{int(until.timestamp())}:R>).",
            )
        )

    @commands.hybrid_command(name="timeout")
    @commands.guild_only()
    @command_enabled("timeout")
    @require_permissions("moderate_members")
    @commands.cooldown(5, 5, commands.BucketType.member)
    @app_commands.describe(
        user="Member to time out",
        duration="e.g. 10m, 1h, 1d (max 28d)",
        reason="Why (logged)",
    )
    async def timeout(
        self,
        ctx: commands.Context,
        user: discord.Member,
        duration: str,
        reason: str = "No reason provided",
    ) -> None:
        """Timeout (mute) a member for a duration."""
        await self._timeout(ctx, user, duration, reason)

    @commands.hybrid_command(name="mute")
    @commands.guild_only()
    @command_enabled("mute")
    @require_permissions("moderate_members")
    @commands.cooldown(5, 5, commands.BucketType.member)
    @app_commands.describe(
        user="Member to mute", duration="e.g. 10m, 1h, 1d (max 28d)", reason="Why"
    )
    async def mute(
        self,
        ctx: commands.Context,
        user: discord.Member,
        duration: str,
        reason: str = "No reason provided",
    ) -> None:
        """Alias of /timeout — silence a member for a duration."""
        await self._timeout(ctx, user, duration, reason)

    async def _untimeout(self, ctx: commands.Context, user: discord.Member, reason: str) -> None:
        error = self._safety(ctx, user, allow_self=True)
        if error:
            await ctx.send(embed=Embeds.error("Cannot act", error), ephemeral=True)
            return
        if user.is_timed_out() is False:
            await ctx.send(
                embed=Embeds.warning("Not timed out", f"**{user}** is not timed out."),
                ephemeral=True,
            )
            return
        try:
            await user.timeout(None, reason=f"{ctx.author} | {reason}")
        except discord.HTTPException as exc:
            await ctx.send(
                embed=Embeds.error(
                    "Untimeout failed", self._api_error(exc, "remove the timeout")
                ),
                ephemeral=True,
            )
            return
        await self._record(ctx, user, "untimeout", reason)
        await self._dm(
            user, Embeds.success("Timeout removed", f"Your timeout in **{ctx.guild.name}** ended.")
        )
        await ctx.send(embed=Embeds.success("Timeout removed", f"**{user}** can speak again."))

    @commands.hybrid_command(name="untimeout")
    @commands.guild_only()
    @command_enabled("untimeout")
    @require_permissions("moderate_members")
    @app_commands.describe(user="Member to free", reason="Why")
    async def untimeout(
        self, ctx: commands.Context, user: discord.Member, reason: str = "No reason provided"
    ) -> None:
        """Remove a timeout from a member."""
        await self._untimeout(ctx, user, reason)

    @commands.hybrid_command(name="unmute")
    @commands.guild_only()
    @command_enabled("unmute")
    @require_permissions("moderate_members")
    @app_commands.describe(user="Member to free", reason="Why")
    async def unmute(
        self, ctx: commands.Context, user: discord.Member, reason: str = "No reason provided"
    ) -> None:
        """Alias of /untimeout — remove a timeout."""
        await self._untimeout(ctx, user, reason)

    # ----------------------------------------------------------- warnings
    @commands.hybrid_command(name="warn")
    @commands.guild_only()
    @command_enabled("warn")
    @require_moderator()
    @commands.cooldown(5, 5, commands.BucketType.member)
    @app_commands.describe(user="Member to warn", reason="Why (stored in the database)")
    async def warn(
        self, ctx: commands.Context, user: discord.Member, reason: str = "No reason provided"
    ) -> None:
        """Warn a member and record it in the database."""
        error = self._safety(ctx, user, allow_self=True)
        if error:
            await ctx.send(embed=Embeds.error("Cannot warn", error), ephemeral=True)
            return

        warning_id, total = await self._warn_member(ctx.guild, ctx.author, user, reason)
        await ctx.send(
            embed=Embeds.success(
                "Warning recorded",
                f"**{user}** now has **{total}** warning(s).\nWarning ID: `{warning_id}`",
            )
        )

    # Context menus cannot live inside a Cog class; see WARN_MEMBER_MENU below.

    async def _warnings_view(self, ctx: commands.Context, user: discord.Member) -> None:
        rows = await self.bot.db.get_warnings(ctx.guild.id, user.id)
        if not rows:
            await ctx.send(
                embed=Embeds.info(
                    "Warning history", f"**{user}** has no warnings. 🎉"
                ),
                ephemeral=True,
            )
            return
        pages: list[str] = []
        for index in range(0, len(rows), 5):
            chunk = rows[index : index + 5]
            lines = [
                f"**#{r['id']}** • <t:{_iso_to_epoch(r['created_at'])}:R>\n"
                f"Reason: {truncate(str(r['reason']), 200)}\n"
                f"By: <@{r['moderator_id']}>"
                for r in chunk
            ]
            pages.append(
                f"### ⚠️ Warnings for {user}\n"
                + "\n\n".join(lines)
                + f"\n\nTotal: **{len(rows)}**"
            )
        if len(pages) == 1:
            await ctx.send(embed=Embeds.plain(pages[0]))
        else:
            paginator = Paginator(pages, author_id=ctx.author.id)
            paginator.message = await ctx.send(
                embed=Embeds.plain(pages[0]), view=paginator
            )

    @commands.hybrid_command(name="warnings")
    @commands.guild_only()
    @command_enabled("warnings")
    @app_commands.describe(user="Member to inspect (defaults to you)")
    async def warnings(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ) -> None:
        """View a member's warning history."""
        target = user or ctx.author
        if user is not None and user.id != ctx.author.id and not perms.is_moderator(
            ctx.author, await self._staff_roles(ctx)
        ):
            await ctx.send(
                embed=Embeds.error("Not allowed", "You need moderator access to inspect others."),
                ephemeral=True,
            )
            return
        await self._warnings_view(ctx, target)

    @commands.hybrid_group(name="warnmanage", invoke_without_command=True)
    @commands.guild_only()
    async def warnmanage(self, ctx: commands.Context) -> None:
        """Manage stored warnings (remove / clear)."""
        await ctx.send(
            embed=Embeds.info(
                "Warn management",
                "Use `/warnmanage remove id:` or `/warnmanage clear user:`.",
            ),
            ephemeral=True,
        )

    @warnmanage.command(name="remove")
    @commands.guild_only()
    @require_moderator()
    @app_commands.describe(id="Warning ID shown in /warnings")
    async def warn_remove(self, ctx: commands.Context, id: int) -> None:
        """Remove a single warning by ID."""
        removed = await self.bot.db.remove_warning(ctx.guild.id, id)
        if not removed:
            await ctx.send(
                embed=Embeds.error("Not found", f"No warning with ID `{id}` here."),
                ephemeral=True,
            )
            return
        await ctx.send(embed=Embeds.success("Warning removed", f"Warning `#{id}` deleted."))

    @warnmanage.command(name="clear")
    @commands.guild_only()
    @require_moderator()
    @app_commands.describe(user="Member whose warnings should be cleared")
    async def warn_clear(self, ctx: commands.Context, user: discord.Member) -> None:
        """Clear every warning for a member."""
        count = await self.bot.db.clear_warnings(ctx.guild.id, user.id)
        await ctx.send(
            embed=Embeds.success(
                "Warnings cleared", f"Removed **{count}** warning(s) for **{user}**."
            )
        )

    async def _staff_roles(self, ctx: commands.Context) -> list[int]:
        settings = await self.bot.db.get_guild(ctx.guild.id)
        role_id = settings.get("staff_role_id")
        return [int(role_id)] if role_id else []

    # ------------------------------------------------------- message purges
    async def _purge(
        self, ctx: commands.Context, amount: int, user: Optional[discord.Member]
    ) -> None:
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send(embed=Embeds.error("Unsupported", "Use this in a text channel."), ephemeral=True)
            return

        # Bulk deletion is a network operation; acknowledge first.
        await ctx.defer()

        async def check(message: discord.Message) -> bool:
            return user is None or message.author.id == user.id

        try:
            if user is None:
                deleted = await channel.purge(limit=amount)
            else:
                deleted = await channel.purge(limit=amount, check=check)
        except discord.Forbidden:
            await ctx.send(
                embed=Embeds.error(
                    "Purge failed", "I need **Manage Messages** in this channel."
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Purge failed", str(exc)), ephemeral=True)
            return

        embed = Embeds.success(
            "Messages cleared", f"Deleted **{len(deleted)}** message(s)."
        )
        if ctx.guild:
            await self.bot.log_to_channel(
                ctx.guild,
                "mod_log_channel",
                Embeds.log(
                    "Messages purged",
                    f"{len(deleted)} message(s) removed",
                    fields=[
                        ("Channel", channel.mention, True),
                        ("Moderator", ctx.author.mention, True),
                        ("Filtered", user.mention if user else "Everyone", True),
                    ],
                    actor=ctx.author,
                ),
            )
        try:
            reply = await ctx.send(embed=embed)
            await reply.delete(delay=8)
        except discord.HTTPException:
            pass

    @commands.hybrid_command(name="clear")
    @commands.guild_only()
    @command_enabled("clear")
    @require_permissions("manage_messages")
    @commands.cooldown(2, 10, commands.BucketType.member)
    @app_commands.describe(amount="How many messages to delete (1-100)", user="Only this user")
    async def clear(
        self,
        ctx: commands.Context,
        amount: app_commands.Range[int, 1, 100],
        user: Optional[discord.Member] = None,
    ) -> None:
        """Delete up to 100 recent messages."""
        await self._purge(ctx, amount, user)

    @commands.hybrid_command(name="purge")
    @commands.guild_only()
    @command_enabled("purge")
    @require_permissions("manage_messages")
    @commands.cooldown(2, 10, commands.BucketType.member)
    @app_commands.describe(amount="How many messages to delete (1-100)", user="Only this user")
    async def purge(
        self,
        ctx: commands.Context,
        amount: app_commands.Range[int, 1, 100],
        user: Optional[discord.Member] = None,
    ) -> None:
        """Alias of /clear — bulk-delete messages."""
        await self._purge(ctx, amount, user)

    # ------------------------------------------------------------ channels
    @commands.hybrid_command(name="slowmode")
    @commands.guild_only()
    @command_enabled("slowmode")
    @require_permissions("manage_channels")
    @app_commands.describe(seconds="Seconds between messages (0 disables)")
    async def slowmode(
        self,
        ctx: commands.Context,
        seconds: app_commands.Range[int, 0, 21600],
        reason: str = "No reason provided",
    ) -> None:
        """Set this channel's slowmode."""
        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            await ctx.send(embed=Embeds.error("Unsupported", "Use this in a text channel."), ephemeral=True)
            return
        try:
            await channel.edit(slowmode_delay=seconds, reason=f"{ctx.author} | {reason}")
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Slowmode failed", str(exc)), ephemeral=True)
            return
        text = "disabled" if seconds == 0 else f"**{format_duration(seconds)}**"
        await ctx.send(embed=Embeds.success("Slowmode updated", f"Slowmode is now {text}."))

    async def _lock_channel(
        self, ctx: commands.Context, channel: discord.TextChannel,        lock: bool, reason: str
    ) -> None:
        overwrites = channel.overwrites_for(ctx.guild.default_role)
        overwrites.send_messages = False if lock else None
        try:
            await channel.set_permissions(
                ctx.guild.default_role,
                overwrite=overwrites,
                reason=f"{ctx.author} | {reason}",
            )
        except discord.HTTPException as exc:
            await ctx.send(
                embed=Embeds.error(
                    "Lock failed" if lock else "Unlock failed", self._api_error(exc, "edit channel")
                ),
                ephemeral=True,
            )
            return

        verb = "locked" if lock else "unlocked"
        if ctx.guild:
            await self.bot.log_to_channel(
                ctx.guild,
                "mod_log_channel",
                Embeds.log(
                    f"Channel {verb.capitalize()}",
                    "",
                    fields=[
                        ("Channel", channel.mention, True),
                        ("Moderator", ctx.author.mention, True),
                        ("Reason", truncate(reason, 200), False),
                    ],
                    actor=ctx.author,
                ),
            )
        await ctx.send(embed=Embeds.success(f"Channel {verb}", f"{channel.mention} is now {verb}."))

    @commands.hybrid_command(name="lock")
    @commands.guild_only()
    @command_enabled("lock")
    @require_permissions("manage_channels")
    @app_commands.describe(channel="Channel to lock (defaults to here)", reason="Why")
    async def lock(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
        reason: str = "No reason provided",
    ) -> None:
        """Lock a channel so @everyone cannot speak."""
        target_channel = channel or ctx.channel
        if not isinstance(target_channel, discord.TextChannel):
            await ctx.send(embed=Embeds.error("Unsupported", "Use this in a text channel."), ephemeral=True)
            return
        if not perms.has_permissions(ctx.me, "manage_channels"):
            await ctx.send(
                embed=Embeds.error("Missing permission", "I need **Manage Channels**."),
                ephemeral=True,
            )
            return
        await self._lock_channel(ctx, target_channel, True, reason)

    @commands.hybrid_command(name="unlock")
    @commands.guild_only()
    @command_enabled("unlock")
    @require_permissions("manage_channels")
    @app_commands.describe(channel="Channel to unlock (defaults to here)", reason="Why")
    async def unlock(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
        reason: str = "No reason provided",
    ) -> None:
        """Unlock a previously locked channel."""
        target_channel = channel or ctx.channel
        if not isinstance(target_channel, discord.TextChannel):
            await ctx.send(embed=Embeds.error("Unsupported", "Use this in a text channel."), ephemeral=True)
            return
        await self._lock_channel(ctx, target_channel, False, reason)

    # ---------------------------------------------------------------- misc
    @commands.hybrid_command(name="nick")
    @commands.guild_only()
    @command_enabled("nick")
    @require_permissions("manage_nicknames")
    @app_commands.describe(
        nickname="New nickname (empty to reset)", user="Member (defaults to you)"
    )
    async def nick(
        self,
        ctx: commands.Context,
        nickname: Optional[str] = None,
        user: Optional[discord.Member] = None,
    ) -> None:
        """Change a member's nickname."""
        target = user or ctx.author
        if target.id != ctx.author.id:
            error = self._safety(ctx, target)
            if error:
                await ctx.send(embed=Embeds.error("Cannot rename", error), ephemeral=True)
                return
        if not perms.has_permissions(ctx.me, "manage_nicknames"):
            await ctx.send(
                embed=Embeds.error("Missing permission", "I need **Manage Nicknames**."),
                ephemeral=True,
            )
            return
        new_nick = truncate(nickname, 32) if nickname else None
        try:
            await target.edit(
                nick=new_nick, reason=f"{ctx.author} | nickname change"
            )
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Rename failed", str(exc)), ephemeral=True)
            return
        await ctx.send(
            embed=Embeds.success(
                "Nickname updated",
                f"**{target}** is now known as **{new_nick or target.name}**.",
            )
        )

    @commands.hybrid_command(name="modlogs")
    @commands.guild_only()
    @command_enabled("modlogs")
    @require_moderator()
    @app_commands.describe(user="Member to inspect (defaults to the whole server)")
    async def modlogs(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ) -> None:
        """Show recent moderation actions (cases)."""
        rows = await self.bot.db.recent_actions(ctx.guild.id, user and user.id, limit=20)
        if not rows:
            await ctx.send(
                embed=Embeds.info("Cases", "No moderation actions recorded yet."),
                ephemeral=True,
            )
            return
        lines = [
            f"**#{r['id']}** • `{r['action']}` • <@{r['user_id']}> • "
            f"by <@{r['moderator_id']}> • <t:{_iso_to_epoch(r['created_at'])}:R>\n"
            f"└ {truncate(str(r['reason']), 150)}"
            + (f" ({r['duration']})" if r.get("duration") else "")
            for r in rows
        ]
        pages = [
            "### 🛡️ Case log\n" + "\n".join(lines[i : i + 5])
            for i in range(0, len(lines), 5)
        ]
        if len(pages) == 1:
            await ctx.send(embed=Embeds.plain(pages[0]))
        else:
            paginator = Paginator(pages, author_id=ctx.author.id)
            paginator.message = await ctx.send(embed=Embeds.plain(pages[0]), view=paginator)

    @commands.hybrid_command(name="massban")
    @commands.guild_only()
    @command_enabled("massban")
    @require_admin()
    @require_permissions("ban_members")
    @commands.cooldown(1, 60, commands.BucketType.guild)
    @app_commands.describe(
        users="Space or comma separated user IDs / mentions",
        reason="Why (logged once for each target)",
    )
    async def massban(
        self, ctx: commands.Context, users: str, reason: str = "No reason provided"
    ) -> None:
        """Ban several users at once (administrator-only, each target safety-checked)."""
        ids = parse_user_list(users)
        if not ids:
            await ctx.send(
                embed=Embeds.error(
                    "No users", "Provide user IDs or mentions separated by spaces."
                ),
                ephemeral=True,
            )
            return
        if len(ids) > 25:
            await ctx.send(
                embed=Embeds.error("Too many", "Maximum of 25 users per /massban."),
                ephemeral=True,
            )
            return

        if not await self._confirm(
            ctx,
            Embeds.warning(
                "Confirm massban",
                f"Ban **{len(ids)}** account(s) for:\n> {truncate(reason, 200)}\n\n"
                "Each target is checked against the role hierarchy; unsafe targets are skipped.",
            ),
            f"Ban {len(ids)}",
        ):
            return

        banned: list[int] = []
        skipped: list[str] = []
        failed: list[str] = []

        for user_id in ids:
            try:
                member = await ctx.guild.fetch_member(user_id)
            except discord.NotFound:
                # Not a member — they may still be banned already; try direct ban.
                try:
                    await ctx.guild.ban(
                        discord.Object(id=user_id),
                        reason=f"{ctx.author} [massban] | {reason}",
                        delete_message_days=0,
                    )
                    banned.append(user_id)
                except discord.HTTPException:
                    failed.append(f"`{user_id}` (not found)")
                continue
            except discord.HTTPException:
                failed.append(f"`{user_id}` (lookup failed)")
                continue

            error = self._safety(ctx, member)
            if error:
                skipped.append(f"`{member}` — {error}")
                continue
            try:
                await member.ban(reason=f"{ctx.author} [massban] | {reason}")
                banned.append(user_id)
            except discord.HTTPException as exc:
                failed.append(f"`{member}` — {self._api_error(exc, 'ban')}")
            await asyncio.sleep(0.4)  # stay well inside rate limits

        assert ctx.guild is not None
        if banned:
            await self.bot.db.log_moderation(
                ctx.guild.id, 0, ctx.author.id, "massban", reason, f"{len(banned)} banned"
            )
            await self.bot.log_to_channel(
                ctx.guild,
                "mod_log_channel",
                Embeds.log(
                    "Massban executed",
                    "",
                    fields=[
                        ("Banned", str(len(banned)), True),
                        ("Skipped", str(len(skipped)), True),
                        ("Failed", str(len(failed)), True),
                        ("Reason", truncate(reason, 200), False),
                    ],
                    actor=ctx.author,
                ),
            )

        description = [f"✅ Banned **{len(banned)}**: " + ", ".join(f"`{i}`" for i in banned)]
        if skipped:
            description.append("⚠️ Skipped: " + "\n".join(skipped))
        if failed:
            description.append("❌ Failed: " + ", ".join(failed))
        await ctx.send(embed=Embeds.success("Massban complete", "\n\n".join(description)))


def _iso_to_epoch(value: str) -> int:
    """Convert an ISO-8601 UTC string to a unix timestamp."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return 0


async def warn_member_menu(
    interaction: discord.Interaction, user: discord.Member
) -> None:
    """Right-click a member → Warn member (opens a reason modal)."""
    bot = interaction.client
    assert isinstance(bot, commands.Bot)
    cog = bot.get_cog("Moderation")
    if not isinstance(cog, Moderation):  # pragma: no cover - defensive
        return

    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "This can only be used inside a server.", ephemeral=True
        )
        return

    # Context menus are app-commands only, so hybrid Context checks do not run
    # here - validate permissions and hierarchy explicitly.
    settings = await bot.db.get_guild(guild.id)
    staff_role = settings.get("staff_role_id")
    if not perms.is_moderator(interaction.user, [int(staff_role)] if staff_role else []):
        await interaction.response.send_message(
            embed=Embeds.error(
                "Not allowed",
                "You need moderator access (Administrator, moderation permissions, "
                "or the configured staff role).",
            ),
            ephemeral=True,
        )
        return

    result = perms.can_moderate(
        interaction.user,
        user,
        guild_owner_id=guild.owner_id,
        bot_member=guild.me,
        bot_owner_ids=config.OWNER_IDS,
    )
    if not result.ok:
        await interaction.response.send_message(
            embed=Embeds.error("Cannot warn", result.reason), ephemeral=True
        )
        return

    await interaction.response.send_modal(WarnModal(cog, user))


# discord.py forbids context menus declared inside a Cog class.
WARN_MEMBER_MENU = app_commands.ContextMenu(
    name="Warn member", callback=warn_member_menu
)


async def setup(bot: commands.Bot) -> None:
    # Idempotent on cog reload (tree commands are not tied to the cog).
    bot.tree.remove_command(WARN_MEMBER_MENU.name, type=WARN_MEMBER_MENU.type)
    bot.tree.add_command(WARN_MEMBER_MENU)
    await bot.add_cog(Moderation(bot))
