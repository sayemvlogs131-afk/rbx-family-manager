"""⚙️ Server configuration for RBX Family Manager.

Houses the per-guild configuration commands: staff role, auto role, ignore
lists and command permissions. Every guild has completely independent
settings (see ``guild_settings`` / ``command_config`` tables).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.tickets import (
    STAT_PANEL_MESSAGE,
    StaffRolePickerView,
    build_panel_embed,
)
from utils import permissions as perms
from utils.checks import require_admin, require_moderator
from utils.embeds import Embeds
from utils.helpers import truncate

log = logging.getLogger("rbx.server")

# ---------------------------------------------------------------- auto setup
# Names ``/setup`` looks for (and creates) when the server has nothing yet.
LOG_CHANNEL_NAME = "\U0001f4cb\u30fbmod-logs"
WELCOME_CHANNEL_NAME = "\U0001f44b\u30fbwelcome"
STAFF_ROLE_NAME = "\U0001f6e1\ufe0f Staff"
MEMBER_ROLE_NAME = "Member"

STAFF_ROLE_CANDIDATES = ("staff", "moderator", "moderators", "mods")
MEMBER_ROLE_CANDIDATES = ("member", "members")
LOG_CHANNEL_CANDIDATES = (
    "mod-logs",
    "modlog",
    "modlogs",
    "bot-logs",
    "botlogs",
    "logs",
    LOG_CHANNEL_NAME.lower(),
)
WELCOME_CHANNEL_CANDIDATES = ("welcome", "welcomes", "welcome-chat", WELCOME_CHANNEL_NAME.lower())
LOG_CHANNEL_KEYS = (
    "mod_log_channel",
    "member_log_channel",
    "server_log_channel",
    "message_log_channel",
)


@dataclass
class SetupOutcome:
    """Result of an automatic ``/setup`` run."""

    settings: dict
    created: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    panel_message: Optional[discord.Message] = None


def find_role_by_name(guild: discord.Guild, names: Iterable[str]) -> Optional[discord.Role]:
    """First role (besides @everyone) whose name matches ``names``."""
    wanted = {n.lower() for n in names}
    for role in guild.roles:
        if role.name.lower() not in wanted:
            continue
        is_default = getattr(role, "is_default", None)
        if is_default and is_default():
            continue
        return role
    return None


def find_channel_by_name(
    guild: discord.Guild, names: Iterable[str]
) -> Optional[discord.TextChannel]:
    """First text channel whose name matches ``names`` (case-insensitive)."""
    wanted = {n.lower() for n in names}
    for channel in guild.text_channels:
        if channel.name.lower() in wanted:
            return channel
    return None


def log_channel_overwrites(
    guild: discord.Guild, staff_role: Optional[discord.Role] = None
) -> dict:
    """Staff-only overwrites for the auto-created log channel."""
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
        ),
    }
    if staff_role is not None:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )
    return overwrites


def welcome_channel_overwrites(guild: discord.Guild) -> dict:
    """Read-only welcome feed: everyone sees it, only the bot posts."""
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True, send_messages=False, read_message_history=True
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
        ),
    }


class Server(commands.Cog, name="Server"):
    """Per-server configuration commands."""

    category = "⚙️ Server"
    category_description = "Configure logging, roles, permissions and ignores."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # --------------------------------------------------------- staff role
    @commands.hybrid_group(name="staffrole", invoke_without_command=True)
    @commands.guild_only()
    @require_admin()
    async def staffrole(self, ctx: commands.Context) -> None:
        """View or set the staff role used for moderator checks."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        role_id = settings.get("staff_role_id")
        value = f"<@&{role_id}>" if role_id else "`not set`"
        await ctx.send(
            embed=Embeds.info(
                "Staff role",
                f"Current: {value}\n\nUse `/staffrole set role:@Staff` to change it.",
            ),
            ephemeral=True,
        )

    @staffrole.command(name="set")
    @require_admin()
    @app_commands.describe(role="Role that grants moderator access")
    async def staffrole_set(self, ctx: commands.Context, role: discord.Role) -> None:
        """Set the staff role."""
        check = perms.bot_can_assign_role(ctx.guild.me, role)
        # The bot only needs to be *above* the role for hierarchy sanity; if not,
        # we still allow configuring it but warn the admin.
        await self.bot.db.update_guild(ctx.guild.id, staff_role_id=role.id)
        warning = "" if check.ok else f"\n⚠️ {check.reason}"
        await ctx.send(
            embed=Embeds.success(
                "Staff role updated",
                f"Members with {role.mention} are treated as staff.{warning}",
            )
        )

    @staffrole.command(name="remove")
    @require_admin()
    async def staffrole_remove(self, ctx: commands.Context) -> None:
        """Clear the staff role."""
        await self.bot.db.update_guild(ctx.guild.id, staff_role_id=None)
        await ctx.send(embed=Embeds.success("Staff role cleared", "No staff role is set."))

    # ------------------------------------------------------------ autorole
    @commands.hybrid_group(name="autorole", invoke_without_command=True)
    @commands.guild_only()
    @require_admin()
    async def autorole(self, ctx: commands.Context) -> None:
        """View, set or remove the automatic role for new members."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        role_id = settings.get("autorole_id")
        if not role_id:
            await ctx.send(
                embed=Embeds.info(
                    "Auto role", "No auto role configured. Use `/autorole set role:@Member`."
                ),
                ephemeral=True,
            )
            return
        role = ctx.guild.get_role(int(role_id))
        await ctx.send(
            embed=Embeds.info(
                "Auto role",
                f"New members receive: {role.mention if role else f'`{role_id}` (deleted)'}",
            ),
            ephemeral=True,
        )

    @autorole.command(name="set")
    @require_admin()
    @app_commands.describe(role="Role to give new members automatically")
    async def autorole_set(self, ctx: commands.Context, role: discord.Role) -> None:
        """Configure the automatic role for joining members."""
        if not perms.has_permissions(ctx.me, "manage_roles"):
            await ctx.send(
                embed=Embeds.error("Missing permission", "I need **Manage Roles**."),
                ephemeral=True,
            )
            return
        check = perms.bot_can_assign_role(ctx.guild.me, role)
        if not check.ok:
            await ctx.send(embed=Embeds.error("Cannot assign that role", check.reason), ephemeral=True)
            return
        await self.bot.db.update_guild(ctx.guild.id, autorole_id=role.id)
        await ctx.send(
            embed=Embeds.success(
                "Auto role set", f"New members will receive {role.mention}."
            )
        )

    @autorole.command(name="remove")
    @require_admin()
    async def autorole_remove(self, ctx: commands.Context) -> None:
        """Disable the automatic role."""
        await self.bot.db.update_guild(ctx.guild.id, autorole_id=None)
        await ctx.send(embed=Embeds.success("Auto role removed", "No role is assigned on join."))

    @autorole.command(name="view")
    @require_admin()
    async def autorole_view(self, ctx: commands.Context) -> None:
        """Show the current auto role."""
        await self.autorole(ctx)

    # ------------------------------------------------------ ignore lists
    @commands.hybrid_command(name="ignore")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(
        target="Channel or role to ignore",
        kind="channel or role",
        action="add or remove",
    )
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="channel", value="channel"),
            app_commands.Choice(name="role", value="role"),
        ],
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
        ],
    )
    async def ignore(
        self,
        ctx: commands.Context,
        target: str,
        kind: str = "channel",
        action: str = "add",
    ) -> None:
        """Ignore channels (commands disabled) or roles for restrictions."""
        raw = target.strip().strip("<>#&@!")
        if not raw.isdigit():
            await ctx.send(
                embed=Embeds.error(
                    "Invalid target", "Provide a channel or role mention / ID."
                ),
                ephemeral=True,
            )
            return
        target_id = int(raw)
        table = "ignored_channels" if kind == "channel" else "ignored_roles"
        if action == "add":
            added = await self.bot.db.add_ignore(table, ctx.guild.id, target_id)
            verb = "added" if added else "already present"
        else:
            removed = await self.bot.db.remove_ignore(table, ctx.guild.id, target_id)
            verb = "removed" if removed else "was not listed"
        await ctx.send(
            embed=Embeds.success(
                "Ignore list updated", f"`{kind}` `{target_id}` **{verb}**."
            )
        )

    @commands.hybrid_command(name="ignores")
    @commands.guild_only()
    @require_admin()
    async def ignores(self, ctx: commands.Context) -> None:
        """Show the current ignore lists."""
        channels = await self.bot.db.ignored_channels(ctx.guild.id)
        roles = await self.bot.db.ignored_roles(ctx.guild.id)
        embed = Embeds.info(
            "Ignore lists",
            "Ignored **channels**: commands are disabled there.\n"
            "Ignored **roles**: reserved for future exemptions.",
        )
        embed.add_field(
            name="Channels",
            value=", ".join(f"<#{c}>" for c in channels[:20]) or "`none`",
            inline=False,
        )
        embed.add_field(
            name="Roles",
            value=", ".join(f"<@&{r}>" for r in roles[:20]) or "`none`",
            inline=False,
        )
        await ctx.send(embed=embed, ephemeral=True)

    # ------------------------------------------- command permission control
    @commands.hybrid_command(name="command")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(
        name="Command name without the slash (e.g. ban)",
        action="enable, disable or restrict to a role",
        role="Role required (only for action=role)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="enable", value="enable"),
            app_commands.Choice(name="disable", value="disable"),
            app_commands.Choice(name="restrict to role", value="role"),
        ]
    )
    async def command_control(
        self,
        ctx: commands.Context,
        name: str,
        action: str = "enable",
        role: Optional[discord.Role] = None,
    ) -> None:
        """Enable, disable or role-restrict any command in this server."""
        clean = name.strip().lower().lstrip("/").split()[0] if name.strip() else ""
        if not clean or len(clean) > 32 or not clean.replace("_", "").isalnum():
            await ctx.send(
                embed=Embeds.error("Invalid name", "Provide a plain command name, e.g. `ban`."),
                ephemeral=True,
            )
            return
        tree = getattr(self.bot.tree, "get_command", None)
        if tree is not None:
            resolved = self.bot.tree.get_command(clean)
            if resolved is None:
                await ctx.send(
                    embed=Embeds.error(
                        "Unknown command", f"`/{clean}` does not exist on this bot."
                    ),
                    ephemeral=True,
                )
                return

        if action == "disable":
            await self.bot.db.set_command_config(ctx.guild.id, clean, enabled=False)
            state = f"`/{clean}` is now **disabled** here."
        elif action == "role":
            if role is None:
                await ctx.send(
                    embed=Embeds.error("Missing role", "Provide `role:` for this action."),
                    ephemeral=True,
                )
                return
            await self.bot.db.set_command_config(
                ctx.guild.id, clean, enabled=True, role_id=role.id
            )
            state = f"`/{clean}` now requires {role.mention} (admins bypass)."
        else:
            await self.bot.db.set_command_config(ctx.guild.id, clean, enabled=True, role_id=None)
            state = f"`/{clean}` is now **enabled** for everyone with permission."
        await ctx.send(embed=Embeds.success("Command permissions updated", state))

    @commands.hybrid_command(name="config")
    @commands.guild_only()
    @require_moderator()
    async def config_view(self, ctx: commands.Context) -> None:
        """Show this server's full configuration."""
        settings = await self.bot.db.get_guild(ctx.guild.id)
        automod = await self.bot.db.get_automod(ctx.guild.id)
        embed = Embeds.info(
            f"Configuration • {truncate(ctx.guild.name, 60)}",
            "Independent per server — nothing here affects other servers.",
        )

        def channel(value: Optional[int]) -> str:
            return f"<#{value}>" if value else "`not set`"

        def role(value: Optional[int]) -> str:
            return f"<@&{value}>" if value else "`not set`"

        embed.add_field(name="🛡️ Mod logs", value=channel(settings.get("mod_log_channel")), inline=True)
        embed.add_field(name="👋 Member logs", value=channel(settings.get("member_log_channel")), inline=True)
        embed.add_field(name="🖥️ Server logs", value=channel(settings.get("server_log_channel")), inline=True)
        embed.add_field(name="💬 Message logs", value=channel(settings.get("message_log_channel")), inline=True)
        embed.add_field(name="👋 Welcome", value=channel(settings.get("welcome_channel")), inline=True)
        embed.add_field(name="⭐ Auto role", value=role(settings.get("autorole_id")), inline=True)
        embed.add_field(name="🧑‍✈️ Staff role", value=role(settings.get("staff_role_id")), inline=True)
        embed.add_field(
            name="🎫 Ticket category",
            value=f"`{settings['ticket_category_id']}`" if settings.get("ticket_category_id") else "`not set`",
            inline=True,
        )
        embed.add_field(
            name="🎫 Ticket staff",
            value=role(settings.get("ticket_staff_role_id")),
            inline=True,
        )
        embed.add_field(
            name="⚙️ AutoMod",
            value="enabled" if automod.get("enabled") else "disabled",
            inline=True,
        )
        await ctx.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------ one-shot
    async def _auto_setup(
        self,
        guild: discord.Guild,
        *,
        logchannel: Optional[discord.TextChannel] = None,
        welcomechannel: Optional[discord.TextChannel] = None,
        staffrole: Optional[discord.Role] = None,
        autorole: Optional[discord.Role] = None,
        ticketcategory: Optional[discord.CategoryChannel] = None,
        ticketstaff: Optional[discord.Role] = None,
        welcomeembed: bool = True,
    ) -> SetupOutcome:
        """Build everything the server is missing, by itself.

        Explicit options are honoured as-is; anything omitted is resolved in
        three steps: already configured → found by name → created. Every step
        is independent, so one failure never aborts the rest — it lands in
        ``notes`` instead.
        """
        me = guild.me
        db = self.bot.db
        settings = await db.get_guild(guild.id)
        fields: dict[str, object] = {}
        outcome = SetupOutcome(settings=settings)

        def remember(key: str, value: object) -> None:
            """Queue a write only when it actually changes something."""
            if value is not None and settings.get(key) != value:
                fields[key] = value

        def can_post(channel: discord.abc.GuildChannel, kind: str) -> bool:
            """Validate an explicitly-passed channel, else explain the fix."""
            perms_in = channel.permissions_for(me)
            if perms_in.send_messages and perms_in.embed_links:
                return True
            outcome.notes.append(
                f"⚠️ I can't send/embed in the {kind} channel {channel.mention} - give me "
                "**Send Messages** + **Embed Links** there."
            )
            return False

        # 1. Staff role: given → configured → found by name → created.
        staff_role: Optional[discord.Role] = None
        if staffrole is not None:
            staff_role = staffrole
            remember("staff_role_id", staffrole.id)
        else:
            configured = settings.get("staff_role_id")
            if configured:
                staff_role = guild.get_role(int(configured))
            if staff_role is None:
                role = find_role_by_name(guild, STAFF_ROLE_CANDIDATES)
                if role is not None:
                    staff_role = role
                    remember("staff_role_id", role.id)
                    outcome.reused.append(f"Staff role: {role.name}")
                elif perms.has_permissions(me, "manage_roles"):
                    try:
                        role = await guild.create_role(
                            name=STAFF_ROLE_NAME,
                            reason="/setup: auto-created staff role",
                        )
                        staff_role = role
                        remember("staff_role_id", role.id)
                        outcome.created.append(f"Role {STAFF_ROLE_NAME}")
                    except discord.HTTPException as exc:
                        outcome.notes.append(f"⚠️ Could not create a staff role: {exc}")
                else:
                    outcome.notes.append(
                        "⚠️ I need **Manage Roles** to create a staff role — or pass "
                        "`staffrole:` yourself."
                    )

        # 2. Log channel: given → configured → found by name → created.
        if logchannel is not None:
            if can_post(logchannel, "log"):
                fields.update({key: logchannel.id for key in LOG_CHANNEL_KEYS})
        elif not settings.get("mod_log_channel"):
            existing = find_channel_by_name(guild, LOG_CHANNEL_CANDIDATES)
            if existing is not None:
                fields.update({key: existing.id for key in LOG_CHANNEL_KEYS})
                outcome.reused.append(f"Log channel: #{existing.name}")
            elif perms.has_permissions(me, "manage_channels"):
                try:
                    created = await guild.create_text_channel(
                        LOG_CHANNEL_NAME,
                        overwrites=log_channel_overwrites(guild, staff_role),
                        reason="/setup: auto-created log channel",
                    )
                    fields.update({key: created.id for key in LOG_CHANNEL_KEYS})
                    outcome.created.append(f"Channel {LOG_CHANNEL_NAME}")
                except discord.HTTPException as exc:
                    outcome.notes.append(f"⚠️ Could not create a log channel: {exc}")
            else:
                outcome.notes.append(
                    "⚠️ I need **Manage Channels** to create a log channel — or pass "
                    "`logchannel:` yourself."
                )

        # 3. Welcome channel: given → configured → found by name → created.
        if welcomechannel is not None:
            if can_post(welcomechannel, "welcome"):
                remember("welcome_channel", welcomechannel.id)
                remember("welcome_embed", int(welcomeembed))
        elif not settings.get("welcome_channel"):
            existing = find_channel_by_name(guild, WELCOME_CHANNEL_CANDIDATES)
            if existing is not None:
                remember("welcome_channel", existing.id)
                remember("welcome_embed", int(welcomeembed))
                outcome.reused.append(f"Welcome channel: #{existing.name}")
            elif perms.has_permissions(me, "manage_channels"):
                try:
                    created = await guild.create_text_channel(
                        WELCOME_CHANNEL_NAME,
                        overwrites=welcome_channel_overwrites(guild),
                        reason="/setup: auto-created welcome channel",
                    )
                    remember("welcome_channel", created.id)
                    remember("welcome_embed", int(welcomeembed))
                    outcome.created.append(f"Channel {WELCOME_CHANNEL_NAME}")
                except discord.HTTPException as exc:
                    outcome.notes.append(f"⚠️ Could not create a welcome channel: {exc}")
            else:
                outcome.notes.append(
                    "⚠️ I need **Manage Channels** to create a welcome channel — or "
                    "pass `welcomechannel:` yourself."
                )

        # 4. Auto role: given → configured → found by name → created.
        if autorole is not None:
            check = perms.bot_can_assign_role(me, autorole)
            if check.ok:
                remember("autorole_id", autorole.id)
            else:
                outcome.notes.append(f"⚠️ Auto role: {check.reason}")
        elif not settings.get("autorole_id"):
            role = find_role_by_name(guild, MEMBER_ROLE_CANDIDATES)
            if role is not None:
                remember("autorole_id", role.id)
                outcome.reused.append(f"Auto role: {role.name}")
            elif perms.has_permissions(me, "manage_roles"):
                try:
                    role = await guild.create_role(
                        name=MEMBER_ROLE_NAME,
                        reason="/setup: auto-created member role",
                    )
                    remember("autorole_id", role.id)
                    outcome.created.append(f"Role {MEMBER_ROLE_NAME}")
                except discord.HTTPException as exc:
                    outcome.notes.append(f"⚠️ Could not create a member role: {exc}")
            else:
                outcome.notes.append(
                    "⚠️ I need **Manage Roles** to create an auto role — or pass "
                    "`autorole:` yourself."
                )

        # 5. Ticket system: delegate to the Tickets cog (category + panel).
        if ticketcategory is not None:
            remember("ticket_category_id", ticketcategory.id)
        if ticketstaff is not None:
            remember("ticket_staff_role_id", ticketstaff.id)
        elif staff_role is not None and not settings.get("ticket_staff_role_id"):
            remember("ticket_staff_role_id", staff_role.id)
        if fields:
            await db.update_guild(guild.id, **fields)
            settings = await db.get_guild(guild.id)
            outcome.settings = settings

        tickets_cog = self.bot.get_cog("Tickets")
        if tickets_cog is None:  # pragma: no cover - defensive
            outcome.notes.append("⚠️ Ticket system unavailable — run `/ticket setup` later.")
            return outcome
        if tickets_cog is not None:
            try:
                recovered = await tickets_cog.recover_server(guild)
            except Exception:  # pragma: no cover - defensive
                recovered = []
            if recovered:
                outcome.notes.append(
                    "⚠️ Ticket recovery: " + "; ".join(recovered)
                )
        had_panel = await db.get_stat(guild.id, STAT_PANEL_MESSAGE)
        try:
            category = ticketcategory
            if category is None:
                category = await tickets_cog._ensure_category(guild)
            panel_channel = await tickets_cog._ensure_panel_channel(guild, category)
            panel_staff = None
            staff_ref = settings.get("ticket_staff_role_id") or settings.get(
                "staff_role_id"
            )
            if staff_ref:
                panel_staff = guild.get_role(int(staff_ref))
            embed = build_panel_embed(
                staff_role=panel_staff, category_name=category.name
            )
            message = await tickets_cog._post_panel(panel_channel, embed)
            outcome.panel_message = message
            if message is None:
                outcome.notes.append(
                    f"⚠️ I could not post the ticket panel in {panel_channel.mention}."
                )
            elif had_panel:
                outcome.reused.append("Ticket panel")
            else:
                outcome.created.append("Ticket panel")
        except discord.HTTPException as exc:
            outcome.notes.append(f"⚠️ Ticket setup failed: {exc}")
        # The ticket cog writes ticket_category_id itself — refresh so the
        # checklist reflects what actually happened.
        outcome.settings = await db.get_guild(guild.id)
        return outcome

    @commands.hybrid_command(name="setup")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(
        logchannel="Log channel (auto-created #\U0001f4cb\u30fbmod-logs if omitted)",
        welcomechannel="Welcome channel (auto-created #\U0001f44b\u30fbwelcome if omitted)",
        staffrole="Staff role (auto-found/created if omitted)",
        autorole="Role given to new members (auto-found if omitted)",
        ticketcategory="Ticket category (auto-created \U0001f3ab Tickets if omitted)",
        ticketstaff="Role that can see tickets (defaults to the staff role)",
        welcomeembed="Post welcomes as an embed (default true)",
    )
    async def setup_one_shot(
        self,
        ctx: commands.Context,
        logchannel: Optional[discord.TextChannel] = None,
        welcomechannel: Optional[discord.TextChannel] = None,
        staffrole: Optional[discord.Role] = None,
        autorole: Optional[discord.Role] = None,
        ticketcategory: Optional[discord.CategoryChannel] = None,
        ticketstaff: Optional[discord.Role] = None,
        welcomeembed: bool = True,
    ) -> None:
        """Set up the whole server automatically — no options needed.

        Creates/reuses the log channel, welcome channel, staff role and the
        full ticket system, then reports a live checklist. Options you do
        pass are used exactly as given.
        """
        # Building channels/roles/posts is network work — acknowledge first.
        await ctx.defer(ephemeral=True)

        outcome = await self._auto_setup(
            ctx.guild,
            logchannel=logchannel,
            welcomechannel=welcomechannel,
            staffrole=staffrole,
            autorole=autorole,
            ticketcategory=ticketcategory,
            ticketstaff=ticketstaff,
            welcomeembed=welcomeembed,
        )
        settings = outcome.settings
        automod = await self.bot.db.get_automod(ctx.guild.id)

        def mark(value: object, fix: str) -> str:
            return f"✅ {fix}" if value else f"❌ {fix}"

        lines = [
            mark(settings.get("mod_log_channel"), "Log channel"),
            mark(settings.get("welcome_channel"), "Welcome messages"),
            mark(settings.get("staff_role_id"), "Staff role"),
            mark(settings.get("autorole_id"), "Auto role (optional)"),
            mark(settings.get("ticket_category_id"), "Ticket category"),
            mark(settings.get("ticket_staff_role_id"), "Ticket staff"),
            mark(bool(automod.get("enabled")), "AutoMod"),
        ]

        done = all(line.startswith("✅") for line in lines)
        embed = Embeds.success(
            "Setup complete \U0001f680" if done else "Setup",
            "Everything below is checked against the live configuration.",
        )
        if outcome.created:
            embed.add_field(
                name="Built for you",
                value="\n".join(f"🆕 {item}" for item in outcome.created),
                inline=False,
            )
        if outcome.reused:
            embed.add_field(
                name="Already there (reused)",
                value="\n".join(f"♻️ {item}" for item in outcome.reused),
                inline=False,
            )
        embed.add_field(name="Checklist", value="\n".join(lines), inline=False)
        if outcome.notes:
            embed.add_field(
                name="Needs attention",
                value="\n".join(outcome.notes),
                inline=False,
            )
        embed.add_field(
            name="Next",
            value=(
                "Pick who handles tickets in the panel below, then test welcomes "
                "with `/welcome test`. Everything stays editable via `/config`."
                if outcome.panel_message is not None
                else "Test welcomes with `/welcome test`. Everything stays editable "
                "via `/config`."
            ),
            inline=False,
        )

        view: Optional[discord.ui.View] = None
        if outcome.panel_message is not None:
            tickets_cog = self.bot.get_cog("Tickets")
            if tickets_cog is not None:
                picker = StaffRolePickerView(
                    cog=tickets_cog,
                    author_id=ctx.author.id,
                    panel_message=outcome.panel_message,
                )
                tickets_cog._pickers[outcome.panel_message.id] = picker
                view = picker
        await ctx.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Server(bot))
