"""🎫 Ticket system for RBX Family Manager.

``/ticket setup`` builds the whole system automatically: it creates a hidden
**🎫 Tickets** category and a panel channel when they don't exist, posts a
panel interface that shows *who can see the ticket* (the staff role), and
opens a modal where the member writes their message. New tickets are always
created inside the ticket category.

Inside every ticket: **Close**, **Claim** (staff takes ownership),
**Transcript** (staff export) and an **Add members** select. All buttons are
persistent views, so they keep working after restarts.

Server-owner-only powers live under ``/ticket owner``: ``scan`` (audits the
whole server's ticket setup, optionally auto-fixing it), ``closeall``,
``purge`` and ``limit``.
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import permissions as perms
from utils.checks import require_moderator, require_server_owner
from utils.embeds import Embeds
from utils.helpers import slugify, truncate
from utils.pagination import wait_for_confirm

log = logging.getLogger("rbx.tickets")

CREATE_CUSTOM_ID = "rbx:ticket:create"
CLOSE_CUSTOM_ID = "rbx:ticket:close"
CLAIM_CUSTOM_ID = "rbx:ticket:claim"
TRANSCRIPT_CUSTOM_ID = "rbx:ticket:transcript"
ADD_USERS_CUSTOM_ID = "rbx:ticket:add_users"
STAFF_SELECT_CUSTOM_ID = "rbx:ticket:staff_role_select"

CATEGORY_NAME = "🎫 Tickets"
PANEL_CHANNEL_NAME = "🎫・tickets"

STAT_PANEL_CHANNEL = "ticket_panel_channel"
STAT_PANEL_MESSAGE = "ticket_panel_message"
STAT_USER_LIMIT = "ticket_user_limit"
DEFAULT_USER_LIMIT = 1
MAX_USER_LIMIT = 10

PANEL_EMBED_DESCRIPTION = (
    "Need help? Click **Create Ticket** below, write a message describing your "
    "issue, and a private channel will be opened just for you.\n\n"
    "🔒 Only you and the staff team can see it.\n"
    "✅ Use **Close Ticket** when you are done — a transcript is saved."
)


def build_panel_embed(
    *,
    staff_role: Optional[discord.Role] = None,
    category_name: Optional[str] = None,
) -> discord.Embed:
    """The panel interface embed: always shows who can see the ticket."""
    embed = Embeds.info("Support Tickets", PANEL_EMBED_DESCRIPTION)
    embed.add_field(
        name="👀 Who can see your ticket",
        value=(staff_role.mention if staff_role else "Only you and the server staff"),
        inline=True,
    )
    embed.add_field(
        name="📂 Category",
        value=(f"**{category_name}**" if category_name else "Auto-created on open"),
        inline=True,
    )
    return embed


class CloseView(discord.ui.View):
    """Persistent action bar shown inside every ticket channel.

    Close / Claim / Transcript buttons plus a member picker for adding
    people. Every component checks permissions itself, so the view stays
    safe to register globally.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id=CLOSE_CUSTOM_ID,
    )
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot = interaction.client
        assert isinstance(bot, commands.Bot)
        cog = bot.get_cog("Tickets")
        assert isinstance(cog, Tickets)
        record = await bot.db.get_ticket_by_channel(interaction.channel.id)
        if record is None or record["status"] != "open":
            await interaction.response.send_message(
                "This ticket is already closed or unknown.", ephemeral=True
            )
            return
        if interaction.user.id != record["owner_id"] and not perms.is_moderator(
            interaction.user, await cog._staff_role_ids(interaction.guild)
        ):
            await interaction.response.send_message(
                "Only the ticket owner or staff can close this ticket.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await cog._close_ticket(
            interaction.guild, record, closed_by=interaction.user, reason="Closed via button"
        )
        await interaction.followup.send("Ticket closed. 🔒", ephemeral=True)

    @discord.ui.button(
        label="Claim",
        emoji="🙋",
        style=discord.ButtonStyle.success,
        custom_id=CLAIM_CUSTOM_ID,
    )
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot = interaction.client
        assert isinstance(bot, commands.Bot)
        cog = bot.get_cog("Tickets")
        assert isinstance(cog, Tickets)
        record = await bot.db.get_ticket_by_channel(interaction.channel.id)
        if record is None or record["status"] != "open":
            await interaction.response.send_message(
                "This ticket is already closed or unknown.", ephemeral=True
            )
            return
        if not await cog._is_staff(interaction.user, interaction.guild):
            await interaction.response.send_message(
                "Only staff can claim tickets.", ephemeral=True
            )
            return
        claimed_by = record.get("claimed_by")
        if claimed_by and int(claimed_by) != interaction.user.id:
            await interaction.response.send_message(
                f"This ticket is already claimed by <@{claimed_by}>.", ephemeral=True
            )
            return
        if claimed_by:  # release an own claim
            await bot.db.update_ticket(int(record["id"]), claimed_by=None)
            await interaction.response.send_message(
                "You released this ticket.", ephemeral=True
            )
            return
        await bot.db.update_ticket(int(record["id"]), claimed_by=interaction.user.id)
        # Reflect the claim in the channel topic (best effort).
        channel = interaction.channel
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.edit(
                    topic=(
                        f"Ticket #{record['number']} • owner {record['owner_id']} "
                        f"• claimed {interaction.user.id}"
                    ),
                    reason=f"Ticket claimed by {interaction.user}",
                )
            except discord.HTTPException:
                log.debug("Could not update ticket topic", exc_info=True)
        await interaction.response.send_message(
            f"🙋 {interaction.user.mention} claimed this ticket — they are handling it."
        )

    @discord.ui.button(
        label="Transcript",
        emoji="📜",
        style=discord.ButtonStyle.secondary,
        custom_id=TRANSCRIPT_CUSTOM_ID,
    )
    async def transcript(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        bot = interaction.client
        assert isinstance(bot, commands.Bot)
        cog = bot.get_cog("Tickets")
        assert isinstance(cog, Tickets)
        record = await bot.db.get_ticket_by_channel(interaction.channel.id)
        if record is None:
            await interaction.response.send_message(
                "This is not a known ticket channel.", ephemeral=True
            )
            return
        if interaction.user.id != record["owner_id"] and not perms.is_moderator(
            interaction.user, await cog._staff_role_ids(interaction.guild)
        ):
            await interaction.response.send_message(
                "Only the ticket owner or staff can export a transcript.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        text = await cog._build_transcript(interaction.channel)
        await interaction.followup.send(
            file=discord.File(
                fp=io.BytesIO(text.encode("utf-8")),
                filename=f"transcript-ticket-{record['number']}.txt",
            ),
            ephemeral=True,
        )

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        custom_id=ADD_USERS_CUSTOM_ID,
        placeholder="Add members to this ticket…",
        min_values=1,
        max_values=5,
    )
    async def add_users(
        self, interaction: discord.Interaction, _: discord.ui.UserSelect
    ) -> None:
        bot = interaction.client
        assert isinstance(bot, commands.Bot)
        cog = bot.get_cog("Tickets")
        assert isinstance(cog, Tickets)
        record = await bot.db.get_ticket_by_channel(interaction.channel.id)
        if record is None or record["status"] != "open":
            await interaction.response.send_message(
                "This ticket is already closed or unknown.", ephemeral=True
            )
            return
        if interaction.user.id != record["owner_id"] and not perms.is_moderator(
            interaction.user, await cog._staff_role_ids(interaction.guild)
        ):
            await interaction.response.send_message(
                "Only the ticket owner or staff can add members.", ephemeral=True
            )
            return
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return
        data = interaction.data or {}
        ids = [int(v) for v in (data.get("values") or [])]
        await interaction.response.defer(ephemeral=True)
        added: list[str] = []
        skipped: list[str] = []
        for user_id in ids:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.HTTPException, discord.NotFound):
                    skipped.append(f"`{user_id}`")
                    continue
            try:
                await channel.set_permissions(
                    member,
                    overwrite=discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                    ),
                    reason=f"Added to ticket by {interaction.user}",
                )
            except discord.HTTPException:
                skipped.append(member.mention)
                continue
            await bot.db.add_ticket_participant(
                int(record["id"]), member.id, interaction.user.id
            )
            added.append(member.mention)
        lines = []
        if added:
            lines.append(f"Added: {', '.join(added)}")
        if skipped:
            lines.append(f"Skipped: {', '.join(skipped)}")
        await interaction.followup.send(
            "\n".join(lines) or "No members were added.", ephemeral=True
        )


class TicketReasonModal(discord.ui.Modal, title="Open a ticket"):
    """Modal collected when the Create Ticket button is pressed.

    Short subject (names the channel) + the message staff will read.
    """

    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="e.g. I was wrongly punished",
        required=False,
        max_length=80,
    )
    issue = discord.ui.TextInput(
        label="What do you need help with?",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your issue so staff can help faster…",
        required=False,
        max_length=1000,
    )

    def __init__(self, cog: "Tickets") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.open_ticket(
            interaction,
            issue=str(self.issue.value or "").strip(),
            subject=str(self.subject.value or "").strip(),
        )

    async def on_error(
        self, interaction: discord.Interaction, error: Exception
    ) -> None:  # pragma: no cover - defensive
        log.exception("Ticket modal failed", exc_info=error)
        try:
            await interaction.response.send_message(
                "Something went wrong opening the ticket. Please try again.",
                ephemeral=True,
            )
        except discord.HTTPException:
            pass


class TicketCreateView(discord.ui.View):
    """Persistent 'Create Ticket' button for the panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id=CREATE_CUSTOM_ID,
    )
    async def create(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot = interaction.client
        assert isinstance(bot, commands.Bot)
        cog = bot.get_cog("Tickets")
        assert isinstance(cog, Tickets)
        # Ask what the issue is first; the modal callback creates the channel.
        await interaction.response.send_modal(TicketReasonModal(cog))


class StaffRolePickerView(discord.ui.View):
    """Setup-only role select: chooses who can see tickets, edits the panel.

    Not persistent (timeout-based) — it only lives during ``/ticket setup``.
    """

    def __init__(
        self,
        *,
        cog: "Tickets",
        author_id: int,
        panel_message: discord.Message,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.author_id = author_id
        self.panel_message = panel_message
        self.picked_role: Optional[discord.Role] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Only the person who ran `/ticket setup` can pick.", ephemeral=True
        )
        return False

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        custom_id=STAFF_SELECT_CUSTOM_ID,
        placeholder="Choose the role that can see tickets…",
        min_values=1,
        max_values=1,
    )
    async def pick(
        self, interaction: discord.Interaction, select: discord.ui.RoleSelect
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        role = select.values[0] if select.values else None
        if role is None:
            await interaction.response.send_message("No role selected.", ephemeral=True)
            return
        if role >= guild.me.top_role:
            await interaction.response.send_message(
                "My top role must be above that role. Move it up and try again.",
                ephemeral=True,
            )
            return
        await self.cog.bot.db.update_guild(guild.id, ticket_staff_role_id=role.id)
        self.picked_role = role
        # Update the live panel so "who can see" is correct immediately.
        embed = build_panel_embed(
            staff_role=role,
            category_name=(
                self.panel_message.channel.category.name
                if isinstance(self.panel_message.channel, discord.TextChannel)
                and self.panel_message.channel.category
                else None,
            ),
        )
        try:
            await self.panel_message.edit(embed=embed, view=TicketCreateView())
        except discord.HTTPException:
            log.debug("Could not refresh panel embed", exc_info=True)
        await interaction.response.send_message(
            f"✅ Tickets are now handled by {role.mention}.", ephemeral=True
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True
        try:
            await self.panel_message.edit(view=TicketCreateView())
        except discord.HTTPException:
            pass
        # Release the cog's reference now that the picker is dead.
        self.cog._pickers.pop(self.panel_message.id, None)


class Tickets(commands.Cog, name="Tickets"):
    """Private support tickets with transcripts."""

    category = "⚙️ Server"
    category_description = "Ticket panel, private support channels, transcripts."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Transient setup pickers we must keep referenced until they time out.
        self._pickers: dict[int, StaffRolePickerView] = {}
        self.bot.add_view(TicketCreateView())
        self.bot.add_view(CloseView())

    # ------------------------------------------------------------ helpers
    async def _staff_role_ids(self, guild: Optional[discord.Guild]) -> list[int]:
        if guild is None:
            return []
        settings = await self.bot.db.get_guild(guild.id)
        role_id = settings.get("ticket_staff_role_id") or settings.get("staff_role_id")
        return [int(role_id)] if role_id else []

    async def _is_staff(self, member: discord.abc.User, guild: Optional[discord.Guild]) -> bool:
        if not isinstance(member, discord.Member):
            return False
        return perms.is_moderator(member, await self._staff_role_ids(guild))

    async def _user_limit(self, guild_id: int) -> int:
        """Per-member open-ticket limit (server owner configurable)."""
        raw = await self.bot.db.get_stat(guild_id, STAT_USER_LIMIT, DEFAULT_USER_LIMIT)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_USER_LIMIT
        return max(1, min(MAX_USER_LIMIT, value))

    async def _ensure_category(self, guild: discord.Guild) -> discord.CategoryChannel:
        """Find the ticket category, creating it (hidden) if it doesn't exist."""
        settings = await self.bot.db.get_guild(guild.id)
        existing = await self._resolve_category(guild, settings)
        if existing is not None:
            return existing
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
            ),
        }
        category = await guild.create_category(
            name=CATEGORY_NAME,
            overwrites=overwrites,
            reason="Ticket system: auto-created category",
        )
        await self.bot.db.update_guild(guild.id, ticket_category_id=category.id)
        log.info("Auto-created ticket category %s in %s", category.id, guild.id)
        return category

    # ------------------------------------------------------------- opening
    async def _resolve_category(
        self, guild: discord.Guild, settings: dict
    ) -> Optional[discord.CategoryChannel]:
        """Configured category, else one named CATEGORY_NAME, else None.

        Never creates anything — creation happens in ``ensure_category``
        during ``/ticket setup`` so open stays fast.
        """
        category_id = settings.get("ticket_category_id")
        if category_id:
            candidate = guild.get_channel(int(category_id))
            if isinstance(candidate, discord.CategoryChannel):
                return candidate
        for category in guild.categories:
            if category.name == CATEGORY_NAME:
                return category
        return None

    async def open_ticket(
        self,
        interaction: discord.Interaction,
        issue: str = "",
        subject: str = "",
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        member = interaction.user
        if not isinstance(member, discord.Member):
            return

        open_rows = await self.bot.db.open_tickets(guild.id)
        limit = await self._user_limit(guild.id)
        mine = sum(1 for r in open_rows if r["owner_id"] == member.id)
        if mine >= limit:
            await interaction.response.send_message(
                f"You already have {mine} open ticket(s) (limit {limit}). "
                "Use it or close it first.",
                ephemeral=True,
            )
            return
        if len(open_rows) >= 200:
            await interaction.response.send_message(
                "Too many tickets are open right now. Please try again later.",
                ephemeral=True,
            )
            return

        settings = await self.bot.db.get_guild(guild.id)
        number = await self.bot.db.next_ticket_number(guild.id)
        staff_role_id = (
            settings.get("ticket_staff_role_id") or settings.get("staff_role_id")
        )
        category = await self._resolve_category(guild, settings)

        # Channel creation is a network call - acknowledge before starting.
        await interaction.response.defer(ephemeral=True)

        overwrites: dict[discord.abc.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False, send_messages=False
            ),
            member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
                attach_files=True,
            ),
        }
        if staff_role_id:
            staff_role = guild.get_role(int(staff_role_id))
            if staff_role is not None:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                )

        try:
            slug = slugify(subject, limit=30)
            channel_name = f"ticket-{number}-{slug}" if slug else f"ticket-{number}"
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"Ticket #{number} opened by {member}",
                topic=f"Ticket #{number} • owner {member.id}",
            )
        except discord.HTTPException as exc:
            log.warning("Ticket channel creation failed: %s", exc)
            # The interaction was already deferred above, so the response
            # slot is spent - send through the followup instead.
            await interaction.followup.send(
                "I could not create the ticket channel. Please contact staff.",
                ephemeral=True,
            )
            return

        ticket_id = await self.bot.db.create_ticket(
            guild.id, channel.id, member.id, number, subject=subject or None
        )

        embed = Embeds.info(
            f"🎫 Ticket #{number}",
            f"Hello {member.mention}, our staff will be with you shortly.\n"
            "Explain your issue and please be patient.",
        )
        embed.add_field(name="Ticket ID", value=f"`{ticket_id}`", inline=True)
        embed.add_field(name="Owner", value=member.mention, inline=True)
        if subject:
            embed.add_field(name="Subject", value=truncate(subject, 250), inline=True)
        if staff_role_id:
            staff_role = guild.get_role(int(staff_role_id))
            if staff_role is not None:
                embed.add_field(
                    name="👀 Who can see this ticket",
                    value=staff_role.mention,
                    inline=True,
                )
        if issue:
            embed.add_field(
                name="Issue", value=truncate(issue, 900), inline=False
            )
        embed.set_footer(text="RBX Family Manager • Use the button below to close")
        try:
            await channel.send(
                content=member.mention,
                embed=embed,
                view=CloseView(),
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        except discord.HTTPException:  # pragma: no cover - defensive
            log.debug("Could not post ticket welcome", exc_info=True)

        await interaction.followup.send(
            f"Ticket created: {channel.mention}", ephemeral=True
        )
        await self.bot.log_to_channel(
            guild,
            "server_log_channel",
            Embeds.log(
                "Ticket opened",
                "",
                fields=[
                    ("Channel", channel.mention, True),
                    ("Owner", member.mention, True),
                    ("Ticket", f"#{number}", True),
                ],
                actor=member,
            ),
        )

    # -------------------------------------------------------------- closing
    async def _build_transcript(self, channel: discord.abc.Messageable) -> str:
        lines: list[str] = []
        if not isinstance(channel, discord.TextChannel):
            return "\n".join(lines)
        try:
            messages = [
                m async for m in channel.history(limit=200, oldest_first=True)
            ]
        except discord.HTTPException:
            return "Transcript unavailable (history could not be fetched)."
        for message in messages:
            stamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            author = f"{message.author} ({message.author.id})"
            body = message.content or ""
            if message.attachments:
                body += " [attachments: " + ", ".join(a.url for a in message.attachments) + "]"
            if message.embeds:
                body += " [embed]"
            lines.append(f"[{stamp}] {author}: {body}")
        header = (
            f"RBX Family Manager transcript\n"
            f"Channel: #{channel.name} ({channel.id})\n"
            f"Exported: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Messages: {len(lines)}\n" + "-" * 60 + "\n"
        )
        return header + "\n".join(lines)

    async def _close_ticket(
        self,
        guild: Optional[discord.Guild],
        record: dict,
        *,
        closed_by: discord.abc.User,
        reason: str,
    ) -> None:
        if guild is None:
            return
        channel = guild.get_channel(int(record["channel_id"]))
        transcript = await self._build_transcript(channel) if channel else "Channel unavailable."
        await self.bot.db.update_ticket(
            int(record["id"]),
            status="closed",
            reason=truncate(reason, 500),
            transcript=truncate(transcript, 100000),
            closed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        # Hand the transcript to staff (log channel) or DM the ticket owner.
        delivered = False
        if channel is not None and isinstance(channel, discord.TextChannel):
            # Fresh File per attempt: a failed send may have consumed the
            # BytesIO cursor, leaving later attempts with an empty file.
            def transcript_file() -> discord.File:
                return discord.File(
                    fp=io.BytesIO(transcript.encode("utf-8")),
                    filename=f"transcript-ticket-{record['number']}.txt",
                )

            settings = await self.bot.db.get_guild(guild.id)
            log_channel_id = settings.get("mod_log_channel") or settings.get("server_log_channel")
            log_channel = guild.get_channel(int(log_channel_id)) if log_channel_id else None
            if isinstance(log_channel, discord.TextChannel):
                try:
                    await log_channel.send(
                        content=f"Ticket #{record['number']} closed by {closed_by.mention}",
                        file=transcript_file(),
                    )
                    delivered = True
                except discord.HTTPException:
                    log.debug("Could not send transcript to log channel", exc_info=True)
            if not delivered:
                try:
                    owner = guild.get_member(int(record["owner_id"])) or await guild.fetch_member(
                        int(record["owner_id"])
                    )
                    await owner.send(
                        f"Your ticket in **{guild.name}** was closed.", file=transcript_file()
                    )
                    delivered = True
                except (discord.HTTPException, discord.Forbidden, discord.NotFound):
                    pass
            if not delivered:
                try:
                    await channel.send(file=transcript_file())
                except discord.HTTPException:
                    pass

        await self.bot.log_to_channel(
            guild,
            "server_log_channel",
            Embeds.log(
                "Ticket closed",
                "",
                color=0xED4245,
                fields=[
                    ("Ticket", f"#{record['number']}", True),
                    ("Closed by", getattr(closed_by, "mention", str(closed_by)), True),
                    ("Reason", truncate(reason, 200), False),
                ],
                actor=closed_by,
            ),
        )

        if channel is not None and isinstance(channel, discord.TextChannel):
            try:
                await channel.delete(reason=f"Ticket #{record['number']} closed")
            except discord.HTTPException:
                log.debug("Could not delete ticket channel", exc_info=True)

    async def _ensure_panel_channel(
        self, guild: discord.Guild, category: discord.CategoryChannel
    ) -> discord.TextChannel:
        """Find the panel channel by name, creating it inside ``category``.

        The category hides @everyone, so the panel channel explicitly grants
        them view access — channel overwrites win over category overwrites.
        """
        for channel in guild.text_channels:
            if channel.name == PANEL_CHANNEL_NAME:
                return channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True, send_messages=False, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
            ),
        }
        channel = await guild.create_text_channel(
            PANEL_CHANNEL_NAME,
            category=category,
            overwrites=overwrites,
            reason="Ticket system: auto-created panel channel",
        )
        log.info("Auto-created ticket panel channel %s in %s", channel.id, guild.id)
        return channel

    async def _save_panel_ref(self, guild: discord.Guild, message: discord.Message) -> None:
        await self.bot.db.set_stat(guild.id, STAT_PANEL_CHANNEL, str(message.channel.id))
        await self.bot.db.set_stat(guild.id, STAT_PANEL_MESSAGE, str(message.id))

    async def _post_panel(
        self, target: discord.TextChannel, embed: discord.Embed
    ) -> Optional[discord.Message]:
        """Post the panel, or edit the still-existing one from a past setup.

        Reuse only happens when the recorded panel lives in ``target`` —
        pointing ``/ticket setup`` at a different channel posts a fresh panel.
        """
        guild = target.guild
        stored_channel = await self.bot.db.get_stat(guild.id, STAT_PANEL_CHANNEL)
        stored_message = await self.bot.db.get_stat(guild.id, STAT_PANEL_MESSAGE)
        if (
            stored_channel
            and stored_message
            and int(stored_channel) == target.id
        ):
            try:
                old_message = await target.fetch_message(int(stored_message))
            except discord.HTTPException:
                old_message = None
            if old_message is not None:
                await old_message.edit(embed=embed, view=TicketCreateView())
                return old_message
            # The stored message is gone (deleted/invalid) — pretend there is
            # no recorded panel so we post a fresh one and re-save the ref.
            await self.bot.db.set_stat(guild.id, STAT_PANEL_CHANNEL, "0")
            await self.bot.db.set_stat(guild.id, STAT_PANEL_MESSAGE, "0")
        try:
            message = await target.send(embed=embed, view=TicketCreateView())
        except discord.HTTPException:
            log.debug("Could not post ticket panel", exc_info=True)
            return None
        await self._save_panel_ref(guild, message)
        return message

    async def recover_server(self, guild: discord.Guild) -> list[str]:
        """Best-effort repair of ticket state after a crash/restart.

        Persistent views survive restarts, but if the bot died mid-setup the
        recorded panel reference can point at a deleted message. This reconciles
        the stored refs with reality without touching live tickets.
        """
        fixed: list[str] = []
        panel_channel_id = await self.bot.db.get_stat(guild.id, STAT_PANEL_CHANNEL)
        panel_message_id = await self.bot.db.get_stat(guild.id, STAT_PANEL_MESSAGE)
        if not panel_channel_id or not panel_message_id:
            return fixed
        try:
            pc_id = int(panel_channel_id)
            pm_id = int(panel_message_id)
        except (TypeError, ValueError):
            return fixed
        panel_channel = guild.get_channel(pc_id)
        if panel_channel is None:
            await self.bot.db.set_stat(guild.id, STAT_PANEL_CHANNEL, "0")
            await self.bot.db.set_stat(guild.id, STAT_PANEL_MESSAGE, "0")
            fixed.append("cleared stale panel ref (panel channel gone)")
            return fixed
        if not callable(getattr(panel_channel, "send", None)):
            # The ID exists but points at a category/voice channel (should be
            # impossible) — the ref is unusable either way.
            await self.bot.db.set_stat(guild.id, STAT_PANEL_CHANNEL, "0")
            await self.bot.db.set_stat(guild.id, STAT_PANEL_MESSAGE, "0")
            fixed.append("cleared stale panel ref (panel channel is not a text channel)")
            return fixed
        try:
            message = await panel_channel.fetch_message(pm_id)
        except discord.HTTPException:
            message = None
        if message is None:
            await self.bot.db.set_stat(guild.id, STAT_PANEL_CHANNEL, "0")
            await self.bot.db.set_stat(guild.id, STAT_PANEL_MESSAGE, "0")
            fixed.append("cleared stale panel ref (panel message not found)")
        return fixed

    # ------------------------------------------------------------ commands
    @commands.hybrid_group(name="ticket", invoke_without_command=True)
    @commands.guild_only()
    async def ticket(self, ctx: commands.Context) -> None:
        """Ticket system commands: setup, close, add, remove, list, owner."""
        await ctx.send(
            embed=Embeds.info(
                "Tickets",
                "`/ticket setup` — auto-build the category, panel channel & role picker\n"
                "`/ticket close` — close the current ticket\n"
                "`/ticket add user:` — add a member to this ticket\n"
                "`/ticket remove user:` — remove a member\n"
                "`/ticket list` — list open tickets\n"
                "`/ticket category` / `staff_role` — configuration\n"
                "`/ticket owner scan|closeall|purge|limit` — **server owner only**",
            ),
            ephemeral=True,
        )

    @ticket.command(name="setup")
    @commands.guild_only()
    @app_commands.describe(
        category="Ticket category (auto-created if omitted)",
        staff_role="Role that can see and reply in tickets (pick interactively if omitted)",
        channel="Where to post the panel (auto-created #🎫・tickets if omitted)",
    )
    async def ticket_setup(
        self,
        ctx: commands.Context,
        category: Optional[discord.CategoryChannel] = None,
        staff_role: Optional[discord.Role] = None,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """Build the whole ticket system: category, panel channel, role, panel."""
        if not perms.has_permissions(ctx.author, "manage_channels"):
            await ctx.send(
                embed=Embeds.error(
                    "Not allowed", "You need **Manage Channels** to set up tickets."
                ),
                ephemeral=True,
            )
            return
        guild = ctx.guild
        me = guild.me
        if staff_role is not None and staff_role >= me.top_role:
            await ctx.send(
                embed=Embeds.error(
                    "Role hierarchy", "My top role must be above the staff role."
                ),
                ephemeral=True,
            )
            return
        bot_missing = perms.missing_permissions(me, "manage_channels", "manage_roles")
        if bot_missing:
            await ctx.send(
                embed=Embeds.error(
                    "Missing permissions",
                    "I need these to build the ticket system: "
                    f"**{', '.join(bot_missing)}**",
                ),
                ephemeral=True,
            )
            return

        # Network work below — acknowledge first.
        await ctx.defer(ephemeral=True)

        fields: dict[str, object] = {}
        if staff_role is not None:
            fields["ticket_staff_role_id"] = staff_role.id

        # 1. Category: use the given one, else find-or-create CATEGORY_NAME.
        try:
            if category is None:
                category = await self._ensure_category(guild)
            else:
                fields["ticket_category_id"] = category.id
        except discord.HTTPException as exc:
            await ctx.send(
                embed=Embeds.error("Category failed", str(exc)), ephemeral=True
            )
            return

        # 2. Panel channel: use the given one, else find-or-create inside category.
        if channel is None:
            try:
                channel = await self._ensure_panel_channel(guild, category)
            except discord.HTTPException as exc:
                await ctx.send(
                    embed=Embeds.error("Panel channel failed", str(exc)), ephemeral=True
                )
                return

        if fields:
            await self.bot.db.update_guild(guild.id, **fields)

        if not channel.permissions_for(me).send_messages:
            await ctx.send(
                embed=Embeds.error(
                    "Missing permission", f"I cannot send messages in {channel.mention}."
                ),
                ephemeral=True,
            )
            return

        # 3. Panel: shows who can see tickets; reuses the old message on re-setup.
        if staff_role is None:
            configured = await self.bot.db.get_guild(guild.id)
            role_id = configured.get("ticket_staff_role_id") or configured.get(
                "staff_role_id"
            )
            staff_role = guild.get_role(int(role_id)) if role_id else None
        embed = build_panel_embed(staff_role=staff_role, category_name=category.name)
        message = await self._post_panel(channel, embed)
        if message is None:
            await ctx.send(
                embed=Embeds.error(
                    "Failed to post panel",
                    f"I could not post the panel in {channel.mention}.",
                ),
                ephemeral=True,
            )
            return

        # 4. Interactive role picker + summary in one non-blocking message:
        #    the picker edits the panel live; discord.py only keeps transient
        #    views alive while we hold a reference, so store it until timeout.
        picker = StaffRolePickerView(
            cog=self, author_id=ctx.author.id, panel_message=message
        )
        self._pickers[message.id] = picker

        summary = Embeds.success(
            "Ticket system ready",
            f"📂 Category: **{category.name}**\n"
            f"💬 Panel: {channel.mention}\n"
            "Buttons survive restarts — new tickets land in the category.",
        )
        if staff_role is not None:
            summary.add_field(
                name="👀 Who can see tickets",
                value=staff_role.mention,
                inline=True,
            )
            next_step = "Pick a different role below to change it - the panel updates instantly."
        else:
            next_step = (
                "**Choose the staff role below** - who should see and reply to "
                "tickets? The panel shows the choice instantly."
            )
        summary.add_field(name="Next step", value=next_step, inline=False)
        await ctx.send(embed=summary, view=picker, ephemeral=True)

    @ticket.command(name="close")
    @commands.guild_only()
    @app_commands.describe(reason="Why the ticket is being closed")
    async def ticket_close(
        self, ctx: commands.Context, reason: str = "No reason provided"
    ) -> None:
        """Close this ticket (works inside a ticket channel)."""
        record = await self.bot.db.get_ticket_by_channel(ctx.channel.id)
        if record is None:
            await ctx.send(
                embed=Embeds.error(
                    "Not a ticket", "Run this inside a ticket channel created by the bot."
                ),
                ephemeral=True,
            )
            return
        if record["status"] != "open":
            await ctx.send(
                embed=Embeds.warning("Already closed", "This ticket is already closed."),
                ephemeral=True,
            )
            return
        if ctx.author.id != record["owner_id"] and not await self._is_staff(
            ctx.author, ctx.guild
        ):
            await ctx.send(
                embed=Embeds.error("Not allowed", "Only the ticket owner or staff can close it."),
                ephemeral=True,
            )
            return
        # Transcript export + channel deletion are slow: acknowledge first.
        await ctx.defer(ephemeral=True)
        if isinstance(ctx.channel, discord.TextChannel):
            try:
                await ctx.channel.send(
                    embed=Embeds.warning(
                        "Ticket closing", "Transcript saved. This channel is being removed."
                    )
                )
            except discord.HTTPException:
                pass
        await self._close_ticket(ctx.guild, record, closed_by=ctx.author, reason=reason)
        try:
            await ctx.send(
                embed=Embeds.success(
                    "Ticket closed",
                    f"Ticket `#{record['number']}` is closed and the transcript was saved.",
                )
            )
        except discord.HTTPException:
            pass  # channel already deleted - the in-channel notice covered it

    async def _assert_can_manage(
        self, ctx: commands.Context, record: dict
    ) -> bool:
        """Staff or the ticket owner may add/remove participants."""
        if ctx.author.id == int(record["owner_id"]):
            return True
        if await self._is_staff(ctx.author, ctx.guild):
            return True
        await ctx.send(
            embed=Embeds.error("Not allowed", "Only the ticket owner or staff can do that."),
            ephemeral=True,
        )
        return False

    @ticket.command(name="add")
    @commands.guild_only()
    @app_commands.describe(user="Member to add to this ticket")
    async def ticket_add(self, ctx: commands.Context, user: discord.Member) -> None:
        """Grant a member access to this ticket."""
        record = await self.bot.db.get_ticket_by_channel(ctx.channel.id)
        if record is None:
            await ctx.send(
                embed=Embeds.error("Not a ticket", "This is not a ticket channel."),
                ephemeral=True,
            )
            return
        if not await self._assert_can_manage(ctx, record):
            return
        if not isinstance(ctx.channel, discord.TextChannel):
            return
        try:
            await ctx.channel.set_permissions(
                user,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                ),
                reason=f"Added to ticket by {ctx.author}",
            )
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Failed", str(exc)), ephemeral=True)
            return
        await self.bot.db.add_ticket_participant(int(record["id"]), user.id, ctx.author.id)
        await ctx.send(
            embed=Embeds.success("Member added", f"{user.mention} can now see this ticket.")
        )

    @ticket.command(name="remove")
    @commands.guild_only()
    @app_commands.describe(user="Member to remove from this ticket")
    async def ticket_remove(self, ctx: commands.Context, user: discord.Member) -> None:
        """Revoke a member's access to this ticket."""
        record = await self.bot.db.get_ticket_by_channel(ctx.channel.id)
        if record is None or not isinstance(ctx.channel, discord.TextChannel):
            await ctx.send(
                embed=Embeds.error("Not a ticket", "This is not a ticket channel."),
                ephemeral=True,
            )
            return
        if not await self._assert_can_manage(ctx, record):
            return
        if user.id == int(record["owner_id"]):
            await ctx.send(
                embed=Embeds.error("Protected", "The ticket owner cannot be removed."),
                ephemeral=True,
            )
            return
        try:
            await ctx.channel.set_permissions(
                user, overwrite=None, reason=f"Removed from ticket by {ctx.author}"
            )
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Failed", str(exc)), ephemeral=True)
            return
        await self.bot.db.remove_ticket_participant(int(record["id"]), user.id)
        await ctx.send(
            embed=Embeds.success("Member removed", f"{user.mention} can no longer see this ticket.")
        )

    @ticket.command(name="list")
    @commands.guild_only()
    @require_moderator()
    async def ticket_list(self, ctx: commands.Context) -> None:
        """List open tickets in this server."""
        rows = await self.bot.db.open_tickets(ctx.guild.id)
        if not rows:
            await ctx.send(
                embed=Embeds.info("Tickets", "No open tickets."), ephemeral=True
            )
            return
        lines = [
            f"**#{r['number']}** • <#{r['channel_id']}> • owner <@{r['owner_id']}>"
            for r in rows[:20]
        ]
        await ctx.send(
            embed=Embeds.command_list(lines, f"🎫 Open tickets ({len(rows)})")
        )

    @ticket.command(name="category")
    @commands.guild_only()
    @app_commands.describe(category="Where ticket channels are created")
    async def ticket_category(
        self, ctx: commands.Context, category: discord.CategoryChannel
    ) -> None:
        """Set the ticket category."""
        await self.bot.db.update_guild(ctx.guild.id, ticket_category_id=category.id)
        await ctx.send(
            embed=Embeds.success("Ticket category", f"Tickets will be created in **{category.name}**.")
        )

    @ticket.command(name="staff_role")
    @commands.guild_only()
    @app_commands.describe(role="Role that handles tickets")
    async def ticket_staff_role(self, ctx: commands.Context, role: discord.Role) -> None:
        """Set the ticket staff role."""
        check = perms.bot_can_assign_role(ctx.guild.me, role)
        await self.bot.db.update_guild(ctx.guild.id, ticket_staff_role_id=role.id)
        note = "" if check.ok else f"\n⚠️ {check.reason}"
        await ctx.send(
            embed=Embeds.success("Ticket staff role", f"Staff role set to {role.mention}.{note}")
        )

    # ------------------------------------------------- server-owner powers
    @ticket.group(name="owner", invoke_without_command=True)
    @commands.guild_only()
    @require_server_owner()
    async def ticket_owner(self, ctx: commands.Context) -> None:
        """Server-owner-only powers over the ticket system."""
        await ctx.send(
            embed=Embeds.info(
                "🎫 Owner powers",
                "`/ticket owner scan fix:` — scan the **whole server**'s ticket "
                "setup and report (optionally repair) every problem\n"
                "`/ticket owner closeall reason:` — force-close every open ticket\n"
                "`/ticket owner purge` — delete closed ticket records + transcripts\n"
                f"`/ticket owner limit n:` — per-member open-ticket limit "
                f"(1–{MAX_USER_LIMIT})",
            ),
            ephemeral=True,
        )

    @ticket_owner.command(name="scan")
    @commands.guild_only()
    @require_server_owner()
    @app_commands.describe(fix="Automatically repair the problems the scan finds")
    async def owner_scan(self, ctx: commands.Context, fix: bool = False) -> None:
        """Scan this server's entire ticket setup and report problems."""
        # Whole-server scan with possible repairs — acknowledge first.
        await ctx.defer(ephemeral=True)
        guild = ctx.guild
        me = guild.me
        settings = await self.bot.db.get_guild(guild.id)
        findings: list[tuple[str, str]] = []

        # 1. Bot permissions needed by the whole system.
        missing = perms.missing_permissions(
            me, "manage_channels", "manage_roles", "manage_messages"
        )
        if missing:
            findings.append(
                ("❌", f"I am missing: **{', '.join(missing)}** — tickets will fail.")
            )
        else:
            findings.append(("OK", "My permissions are sufficient."))

        # 2. Category (find or auto-repair).
        category = await self._resolve_category(guild, settings)
        if category is None:
            if fix:
                try:
                    category = await self._ensure_category(guild)
                    findings.append(
                        ("✅", f"No category found — created **{category.name}**.")
                    )
                except discord.HTTPException as exc:
                    findings.append(("❌", f"No category and creation failed: {exc}"))
            if category is None:
                findings.append(
                    ("⚠️", "No ticket category — run `/ticket setup` to build it.")
                )
        else:
            in_cat = [
                c
                for c in category.channels
                if isinstance(c, discord.TextChannel) and c.name.startswith("ticket-")
            ]
            findings.append(
                (
                    "✅",
                    f"Category **{category.name}** — {len(in_cat)} live ticket channel(s).",
                )
            )

        # 3. Panel channel + panel message (from the stored references).
        panel_channel_id = await self.bot.db.get_stat(guild.id, STAT_PANEL_CHANNEL)
        panel_channel = (
            guild.get_channel(int(panel_channel_id)) if panel_channel_id else None
        )
        if isinstance(panel_channel, discord.TextChannel):
            findings.append(("✅", f"Panel channel: {panel_channel.mention}"))
            panel_message_id = await self.bot.db.get_stat(guild.id, STAT_PANEL_MESSAGE)
            panel_message = None
            if panel_message_id:
                try:
                    panel_message = await panel_channel.fetch_message(
                        int(panel_message_id)
                    )
                except discord.HTTPException:
                    panel_message = None
            if panel_message is None:
                if fix and category is not None:
                    role_id = (
                        settings.get("ticket_staff_role_id")
                        or settings.get("staff_role_id")
                    )
                    role = guild.get_role(int(role_id)) if role_id else None
                    panel_message = await self._post_panel(
                        panel_channel,
                        build_panel_embed(
                            staff_role=role,
                            category_name=category.name if category else None,
                        ),
                    )
                if panel_message is not None:
                    findings.append(("✅", "Panel message was missing — reposted it."))
                else:
                    findings.append(
                        (
                            "⚠️",
                            "Panel message missing — re-run `/ticket setup` "
                            "(or the scan with `fix: True`).",
                        )
                    )
            else:
                findings.append(("✅", "Panel message is alive and clickable."))
        else:
            if fix and category is not None:
                try:
                    new_channel = await self._ensure_panel_channel(guild, category)
                    findings.append(
                        ("✅", f"Panel channel missing — created {new_channel.mention}.")
                    )
                except discord.HTTPException as exc:
                    findings.append(("❌", f"Panel channel missing: {exc}"))
            else:
                findings.append(
                    ("⚠️", "No panel channel recorded — run `/ticket setup`.")
                )

        # 4. Staff role: exists? above my top role?
        role_id = settings.get("ticket_staff_role_id") or settings.get("staff_role_id")
        role = guild.get_role(int(role_id)) if role_id else None
        if role is None:
            findings.append(
                (
                    "⚠️",
                    "No staff role — only administrators can see tickets. "
                    "Pick one in the setup message or `/ticket staff_role`.",
                )
            )
        elif role >= me.top_role:
            findings.append(
                (
                    "❌",
                    f"Staff role {role.mention} is **above my top role** — "
                    "ticket overwrites will be rejected.",
                )
            )
        else:
            findings.append(
                ("✅", f"Staff role: {role.mention} ({len(role.members)} member(s)).")
            )

        # 5. Whole-server ticket-channel scan + DB consistency.
        open_rows = await self.bot.db.open_tickets(guild.id)
        tracked = {int(r["channel_id"]) for r in open_rows}
        untracked = [
            c
            for c in guild.text_channels
            if c.name.startswith("ticket-") and c.id not in tracked
        ]
        if untracked:
            shown = ", ".join(f"<#{c.id}>" for c in untracked[:8])
            more = f" (+{len(untracked) - 8} more)" if len(untracked) > 8 else ""
            findings.append(
                (
                    "⚠️",
                    f"{len(untracked)} channel(s) look like tickets but are not "
                    f"tracked: {shown}{more}",
                )
            )
        dead = [r for r in open_rows if guild.get_channel(int(r["channel_id"])) is None]
        if dead:
            if fix:
                for record in dead:
                    await self.bot.db.update_ticket(
                        int(record["id"]),
                        status="closed",
                        reason="Channel deleted (repaired by owner scan)",
                        closed_at=datetime.now(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                    )
                findings.append(
                    (
                        "✅",
                        f"Closed {len(dead)} stale record(s) whose channels are gone.",
                    )
                )
            else:
                findings.append(
                    (
                        "⚠️",
                        f"{len(dead)} open record(s) point at deleted channels — "
                        "scan with `fix: True` to repair.",
                    )
                )

        # 6. Stats.
        counts = await self.bot.db.ticket_counts(guild.id)
        limit = await self._user_limit(guild.id)
        findings.append(
            (
                "ℹ️",
                f"Open: **{counts['open']}** • Closed: **{counts['closed']}** • "
                f"Per-member limit: **{limit}**",
            )
        )

        embed = Embeds.info(
            f"🔍 Ticket scan — {guild.name}",
            "\n".join(f"{emoji} {line}" for emoji, line in findings),
        )
        if not fix:
            embed.set_footer(text="Add fix: True to repair what can be repaired")
        await ctx.send(embed=embed, ephemeral=True)

    @ticket_owner.command(name="closeall")
    @commands.guild_only()
    @require_server_owner()
    @app_commands.describe(reason="Why every ticket is being closed")
    async def owner_closeall(
        self, ctx: commands.Context, reason: str = "Server owner closed all tickets"
    ) -> None:
        """Force-close every open ticket in this server."""
        rows = await self.bot.db.open_tickets(ctx.guild.id)
        if not rows:
            await ctx.send(
                embed=Embeds.info("Nothing to do", "No open tickets."),
                ephemeral=True,
            )
            return
        confirmed = await wait_for_confirm(
            ctx,
            author_id=ctx.author.id,
            prompt=Embeds.warning(
                "Close all tickets?",
                f"This will close and delete **{len(rows)}** ticket channel(s) "
                "(transcripts are saved first).",
            ),
            confirm_label=f"Close {len(rows)}",
            ephemeral=True,
        )
        if not confirmed:
            return
        # NB: the confirm prompt already used the interaction response, so no
        # defer here — later ctx.send() calls go straight to the followup.
        closed = 0
        for record in rows:
            try:
                await self._close_ticket(
                    ctx.guild, record, closed_by=ctx.author, reason=reason
                )
                closed += 1
            except Exception:  # pragma: no cover - keep going on one failure
                log.exception("closeall failed for ticket %s", record.get("id"))
            await asyncio.sleep(0.5)
        await self.bot.log_to_channel(
            ctx.guild,
            "server_log_channel",
            Embeds.log(
                "All tickets closed",
                "",
                color=0xED4245,
                fields=[
                    ("Closed", str(closed), True),
                    ("Reason", truncate(reason, 200), False),
                ],
                actor=ctx.author,
            ),
        )
        await ctx.send(
            embed=Embeds.success(
                "Done", f"Closed **{closed}** of {len(rows)} ticket(s)."
            ),
            ephemeral=True,
        )

    @ticket_owner.command(name="purge")
    @commands.guild_only()
    @require_server_owner()
    async def owner_purge(self, ctx: commands.Context) -> None:
        """Delete closed ticket records and their transcripts permanently."""
        counts = await self.bot.db.ticket_counts(ctx.guild.id)
        if counts["closed"] == 0:
            await ctx.send(
                embed=Embeds.info("Nothing to purge", "No closed tickets."),
                ephemeral=True,
            )
            return
        confirmed = await wait_for_confirm(
            ctx,
            author_id=ctx.author.id,
            prompt=Embeds.warning(
                "Purge closed tickets?",
                f"This permanently deletes **{counts['closed']}** closed record(s) "
                "including their saved transcripts. Open tickets are untouched. "
                "This cannot be undone.",
            ),
            confirm_label="Purge forever",
            ephemeral=True,
        )
        if not confirmed:
            return
        deleted = await self.bot.db.purge_closed_tickets(ctx.guild.id)
        await ctx.send(
            embed=Embeds.success(
                "Purged", f"Deleted **{deleted}** closed ticket record(s)."
            ),
            ephemeral=True,
        )

    @ticket_owner.command(name="limit")
    @commands.guild_only()
    @require_server_owner()
    @app_commands.describe(n="Open tickets allowed per member (1–10)")
    async def owner_limit(
        self, ctx: commands.Context, n: app_commands.Range[int, 1, MAX_USER_LIMIT]
    ) -> None:
        """Set how many open tickets one member may have."""
        if not 1 <= n <= MAX_USER_LIMIT:
            await ctx.send(
                embed=Embeds.error(
                    "Invalid limit", f"Pick a number between 1 and {MAX_USER_LIMIT}."
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.set_stat(ctx.guild.id, STAT_USER_LIMIT, str(n))
        await ctx.send(
            embed=Embeds.success(
                "Ticket limit",
                f"Members may now have **{n}** open ticket(s) each.",
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
