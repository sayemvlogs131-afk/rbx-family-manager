"""🔒 Security, anti-nuke, bot status and update announcements.

Everything dangerous in this module is **default-deny**:

* configuration commands require administrator rights (or the real owners)
* `/emergencylockdown` requires the *actual* Discord server owner or the
  configured bot owner — nobody else, no role can grant it
* every sensitive action and every anti-nuke hit is written to the
  ``security_events`` audit table (visible with ``/auditlogs``)
* anti-nuke never retaliates against the real server owner, the bot owner,
  trusted roles/users, or anyone above the bot in the hierarchy

Update announcements read ``UPDATES.md`` (``config.UPDATES_PATH``); each guild
with ``/botupdatesetup`` configured receives a new-release embed automatically
when the version changes.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils import permissions as perms
from utils.checks import (
    get_guild_owner_id,
    require_admin,
    require_guild_or_bot_owner,
    require_moderator,
)
from utils.embeds import Embeds
from utils.helpers import truncate
from utils.pagination import ConfirmView

log = logging.getLogger("rbx.security")

STAT_UPDATE_CHANNEL = "update_channel_id"
STAT_ANNOUNCED_VERSION = "update_announced_version"
STAT_STATUS_MODE = "bot_status_mode"
STAT_STATUS_MESSAGE = "bot_status_message"

STATUS_CHOICES = [
    app_commands.Choice(name="🟢 Online (normal)", value="online"),
    app_commands.Choice(name="🟡 Maintenance (commands paused)", value="maintenance"),
    app_commands.Choice(name="🔴 Issues (degraded)", value="issues"),
]

STATUS_META = {
    "online": ("🟢", "Online", discord.Color.brand_green()),
    "maintenance": ("🟡", "Maintenance", discord.Color.gold()),
    "issues": ("🔴", "Issues", discord.Color.brand_red()),
}

# UPDATES.md category marker prefix -> embed field name.
UPDATE_CATEGORIES: dict[str, str] = {
    "🟢": "🟢 Added",
    "🛠": "🛠️ Fixed",
    "⚡": "⚡ Improved",
    "🔒": "🔒 Security",
    "🔴": "🔴 Critical Issue",
}

VERSION_HEADER_RE = re.compile(r"^#\s*v(\d+\.\d+\.\d+)[^\n]*$", re.MULTILINE)
BULLET_RE = re.compile(r"^\s*[-•]\s*(.*)$")

START_TIME = time.time()


def _start_epoch() -> float:
    """Process start time (module import ≈ bot boot)."""
    return START_TIME


def bot_owner_only():
    """Only the configured bot owner(s) may change the global status mode."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id in config.OWNER_IDS:
            return True
        raise commands.NotOwner("Only the bot owner can change the status mode.")

    return commands.check(predicate)


# ---------------------------------------------------------------- changelog
@dataclass
class Release:
    """The newest release parsed from ``UPDATES.md``."""

    version: str = ""
    title: str = ""
    sections: dict[str, list[str]] = field(default_factory=dict)

    def latest_summary(self, limit: int = 900) -> str:
        lines = [f"- {item}" for items in self.sections.values() for item in items]
        text = f"**v{self.version} — {self.title}**\n" + "\n".join(lines[:12])
        return truncate(text, limit, "… (see the update channel)")


def parse_updates(path: str | Path) -> Release:
    """Parse the newest release block from a ``UPDATES.md``-style file.

    Pure function (file I/O via ``Path``), safe when the file is missing.
    """
    release = Release()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return release

    headings = list(VERSION_HEADER_RE.finditer(text))
    if not headings:
        return release
    first = headings[0]
    release.version = first.group(1)
    heading_line_end = text.find("\n", first.start())
    next_heading = headings[1].start() if len(headings) > 1 else len(text)
    heading_line = text[first.start() + 2 : heading_line_end if heading_line_end > -1 else first.end()]
    release.title = heading_line.strip(" -—\u2014").replace(f"v{release.version}", "", 1).strip(" —-—")
    for raw in text[heading_line_end + 1 : next_heading].splitlines():
        line = None
        bullet = BULLET_RE.match(raw)
        if bullet is not None:
            line = bullet.group(1).strip()
        else:
            # Release lines may also start directly with the marker emoji
            # (no "- " prefix) — accept both formats.
            stripped = raw.strip()
            if any(stripped.startswith(marker) for marker in UPDATE_CATEGORIES):
                line = stripped
        if not line:
            continue
        if not line:
            continue
        for marker, label in UPDATE_CATEGORIES.items():
            if line.startswith(marker):
                release.sections.setdefault(label, []).append(truncate(line[len(marker) :].strip(), 250))
                break
        else:
            release.sections.setdefault("📌 Other", []).append(truncate(line, 250))
    return release


def build_update_embed(release: Release) -> discord.Embed:
    """The public release-notes embed with status emojis."""
    total = sum(len(items) for items in release.sections.values())
    embed = discord.Embed(
        title=f"🚀 Bot Update — v{release.version}",
        description=(
            (f"**{release.title}**\n" if release.title else "")
            + f"*{total} change(s) in this release · see `/changelog` anytime*"
        ),
        color=config.BRAND_COLOR,
    )
    for label, items in release.sections.items():
        embed.add_field(name=label, value=truncate("\n".join(f"• {i}" for i in items), 1024), inline=False)
    embed.add_field(name="Version", value=f"`v{release.version}`", inline=True)
    embed.set_footer(text=config.FOOTER_TEXT)
    return embed


# ------------------------------------------------------------------ machinery
ALLOWED_SECURITY_ACTIONS = ("alert", "timeout", "kick")


@dataclass
class _Window:
    timestamps: list[float] = field(default_factory=list)


class SecuritySetupView(discord.ui.View):
    """Interactive anti-nuke panel for ``/securitysetup`` (invoker-only)."""

    def __init__(self, cog: "Security", author_id: int, guild_id: int, settings: dict) -> None:
        super().__init__(timeout=240.0)
        self.cog = cog
        self.author_id = author_id
        self.guild_id = guild_id
        self.settings = dict(settings)
        self.message: Optional[discord.Message] = None

    async def _reload(self) -> None:
        try:
            self.settings = dict(await self.cog.bot.db.get_security(self.guild_id))
        except Exception:  # pragma: no cover - defensive
            log.exception("Failed to reload security settings")

    async def _save(self, **fields: Any) -> None:
        await self.cog.bot.db.update_security(self.guild_id, **fields)
        await self._reload()

    async def _log(self, event: str, details: str) -> None:
        await self.cog.bot.db.log_security_event(self.guild_id, event, details=details)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran `/securitysetup` can use this panel.",
                ephemeral=True,
            )
            return False
        if not perms.is_privileged(interaction.user):  # permission may have changed since
            await interaction.response.send_message(
                "You no longer hold administrator permissions.", ephemeral=True
            )
            return False
        return True

    def _embed(self) -> discord.Embed:
        s = self.settings
        if bool(s.get("anti_nuke_enabled")):
            state = "✅ **Enabled**"
        else:
            state = "❌ **Disabled**"
        embed = Embeds.info(
            "🛡️ Anti-nuke & security panel",
            f"Anti-nuke: {state}\n**(Refresh)** to re-read from the database.",
        )
        embed.add_field(
            name="Action limits",
            value=(
                f"Bans: **{s.get('ban_limit')}** / {s.get('window_seconds')}s\n"
                f"Kicks: **{s.get('kick_limit')}** / {s.get('window_seconds')}s\n"
                f"Channel deletes: **{s.get('channel_limit')}** / {s.get('window_seconds')}s\n"
                f"Role deletes: **{s.get('role_limit')}** / {s.get('window_seconds')}s"
            ),
            inline=True,
        )
        embed.add_field(
            name="Anti-raid joins",
            value=(
                f"**{s.get('join_limit')}** joins / {s.get('join_window_seconds')}s\n"
                f"On raid: **{s.get('raid_action')}**\n"
                f"Suspicious: **< {s.get('min_account_age_days')}d** account age"
            ),
            inline=True,
        )
        embed.add_field(name="On breach", value=f"**{s.get('action')}**", inline=True)
        embed.add_field(
            name="Auto-lockdown",
            value=("✅ on" if s.get("lockdown_on_nuke") else "❌ off"),
            inline=True,
        )
        role_ids = _load_id_list(s.get("trusted_role_ids"))
        user_ids = _load_id_list(s.get("trusted_user_ids"))
        embed.add_field(
            name="Trusted roles",
            value=" ".join(f"<@&{r}>" for r in role_ids[:10]) or "`none`",
            inline=True,
        )
        embed.add_field(
            name="Trusted users",
            value=" ".join(f"<@{u}>" for u in user_ids[:10]) or "`none`",
            inline=True,
        )
        log_channel = s.get("log_channel")
        embed.add_field(
            name="Security log",
            value=f"<#{log_channel}>" if log_channel else "`not set` (falls back to server logs)",
            inline=False,
        )
        return embed

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await self._reload()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.select(
        placeholder="Action when a breach is detected…",
        options=[
            discord.SelectOption(label="Alert only (log & DM owner)", value="alert"),
            discord.SelectOption(label="Timeout the abuser", value="timeout"),
            discord.SelectOption(label="Kick the abuser", value="kick"),
        ],
        row=0,
    )
    async def choose_action(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        value = str(select.values[0])
        if value not in ALLOWED_SECURITY_ACTIONS:
            value = "alert"
        await self._save(action=value)
        await self._log("security_config_changed", f"breach action → {value} (by {interaction.user})")
        await self._refresh(interaction)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Trusted roles (immune to anti-nuke)…",
        min_values=0,
        max_values=10,
        row=1,
    )
    async def trusted_roles(self, interaction: discord.Interaction, select: discord.ui.RoleSelect) -> None:
        ids = [int(role.id) for role in select.values]
        await self._save(trusted_role_ids=_dump_id_list(ids))
        await self._log("security_config_changed", f"trusted roles → {ids} (by {interaction.user})")
        await self._refresh(interaction)

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Trusted users (immune to anti-nuke)…",
        min_values=0,
        max_values=10,
        row=2,
    )
    async def trusted_users(self, interaction: discord.Interaction, select: discord.ui.UserSelect) -> None:
        ids = [int(user.id) for user in select.values]
        await self._save(trusted_user_ids=_dump_id_list(ids))
        await self._log("security_config_changed", f"trusted users → {ids} (by {interaction.user})")
        await self._refresh(interaction)

    @discord.ui.button(label="Enable", style=discord.ButtonStyle.success, row=3)
    async def enable(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._save(anti_nuke_enabled=1)
        await self._log("anti_nuke_enabled", f"enabled by {interaction.user}")
        await self._refresh(interaction)

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.danger, row=3)
    async def disable(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._save(anti_nuke_enabled=0)
        await self._log("anti_nuke_disabled", f"disabled by {interaction.user}")
        await self._refresh(interaction)

    @discord.ui.button(label="Toggle auto-lockdown", style=discord.ButtonStyle.secondary, row=3)
    async def toggle_lockdown(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        new_value = 0 if int(self.settings.get("lockdown_on_nuke") or 0) else 1
        await self._save(lockdown_on_nuke=new_value)
        await self._log(
            "security_config_changed",
            f"auto-lockdown → {'on' if new_value else 'off'} (by {interaction.user})",
        )
        await self._refresh(interaction)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._refresh(interaction)


def _dump_id_list(values: list[int] | str | None) -> str:
    if isinstance(values, str):
        try:
            import json

            parsed = json.loads(values or "[]")
            return _dump_id_list(parsed if isinstance(parsed, list) else [])
        except ValueError:
            return "[]"
    return _json_dump(list(values))


def _json_dump(values: list[int]) -> str:
    import json

    return json.dumps(values)


def _load_id_list(raw: Any) -> list[int]:
    import json

    if isinstance(raw, list):
        out = []
        for item in raw:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out
    try:
        parsed = json.loads(raw or "[]")
        return _load_id_list(parsed if isinstance(parsed, list) else [])
    except (ValueError, TypeError):
        return []


# --------------------------------------------------------------------- cog
class Security(commands.Cog, name="Security"):
    """Anti-nuke protection, bot status, update announcements and lockdown."""

    category = "🔒 Security"
    category_description = "Anti-nuke, status, update feed and emergency lockdown."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # (guild_id, kind, target_id) -> (actor_id, cached_at)
        self._actor_cache: dict[tuple[int, str, int], tuple[int, float]] = {}
        # (guild_id, kind, actor_id) -> sliding window
        self._windows: dict[tuple[int, str, int], _Window] = {}
        # throttling keys -> last alert timestamp per actor
        self._last_alert: dict[tuple[Any, Any], float] = {}
        # guild_id -> {channel_id: previous @everyone overwrite}
        self._lockdown_state: dict[int, dict[int, discord.PermissionOverwrite]] = {}
        # guild_id -> sliding join timestamps (raid detection)
        self._join_windows: dict[int, _Window] = {}
        # guild_id -> {webhook_id: Webhook} last seen state
        self._webhook_counts: dict[int, dict[int, discord.Webhook]] = {}
        self.update_loop.start()

    async def cog_unload(self) -> None:  # pragma: no cover - discipline on reload
        self.update_loop.cancel()

    # -------------------------------------------------------------owned helpers
    async def _owner_and_log(self, guild: discord.Guild, *, event: str, embed: discord.Embed,
                            severity: str = "info", actor_id: int | None = None,
                            target_id: int | None = None, details: str | None = None) -> None:
        """Persist a sensitive action, then notify the security log channel."""
        try:
            await self.bot.db.log_security_event(
                guild.id, event, actor_id=actor_id, target_id=target_id,
                details=details, severity=severity,
            )
        except Exception:  # pragma: no cover - audit must never break the flow
            log.exception("Failed to write security event")
        log_channel_id = await self.bot.db.get_stat(guild.id, "security_log_channel", None)
        settings = await self.bot.db.get_security(guild.id)
        candidates = [log_channel_id, settings.get("log_channel")]
        for candidate in candidates:
            stored = int(candidate) if candidate else None
            if stored is None:
                guild_settings = await self.bot.db.get_guild(guild.id)
                stored = int(guild_settings.get("server_log_channel") or 0) or None
            if stored:
                channel = guild.get_channel(stored) or None
                if isinstance(channel, discord.TextChannel):
                    try:
                        await channel.send(embed=embed)
                        return
                    except (discord.HTTPException, discord.Forbidden):
                        continue
        # No channel could receive it — the DB row above still recorded it.

    async def _alert_owner(self, guild: discord.Guild, embed: discord.Embed) -> None:
        try:
            owner_id = await get_guild_owner_id(guild)
            if owner_id is None:
                return
            owner = guild.get_member(owner_id)
            if owner is None:
                owner = await guild.fetch_member(owner_id)
            if owner is not None:
                await owner.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            log.debug("Could not DM the server owner of %s", guild.id)

    # ------------------------------------------------------------- anti-nuke
    async def _register_event_count(
        self,
        guild: discord.Guild,
        kind: str,
        target_id: int,
        audit_action: discord.AuditLogAction,
        *,
        limit_override: int | None = None,
    ) -> None:
        try:
            settings = await self.bot.db.get_security(guild.id)
            if not int(settings.get("anti_nuke_enabled", 1)):
                return
            actor = await self._resolve_actor(guild, kind, target_id, audit_action)
            if actor is None:
                await self._owner_and_log(
                    guild,
                    event=f"unattributed_{kind}",
                    embed=Embeds.warning(
                        "Anti-nuke event (actor unknown)",
                        f"{kind} happened (target `{target_id}`) but I could not read the audit log.\n"
                        "Give me **View Audit Log** and keep my role above staff to attribute it.",
                    ),
                    severity="warning",
                    target_id=target_id,
                    details="actor unknown",
                )
                return
            if actor.bot:
                return
            owner_id = await get_guild_owner_id(guild)
            if owner_id is not None and actor.id == owner_id:
                return  # the true owner is always allowed
            if actor.id in config.OWNER_IDS:
                return  # configured bot owner is exempt
            trusted_roles = set(_load_id_list(settings.get("trusted_role_ids")))
            trusted_users = set(_load_id_list(settings.get("trusted_user_ids")))
            if actor.id in trusted_users:
                return
            if trusted_roles & {r.id for r in getattr(actor, "roles", [])}:
                return

            key = (guild.id, kind, actor.id)
            window = self._windows.setdefault(key, _Window())
            now_ts = time.time()
            seconds = max(5, int(settings.get("window_seconds", 60) or 60))
            window.timestamps = [t for t in window.timestamps if now_ts - t <= seconds]
            window.timestamps.append(now_ts)
            limit_key = {"ban": "ban_limit", "kick": "kick_limit", "channel": "channel_limit", "role": "role_limit"}[kind]
            limit = max(1, int(limit_override or settings.get(limit_key, 5) or 5))
            label = {"ban": "bans", "kick": "kicks", "channel": "channel deletions", "role": "role deletions"}[kind]
            if len(window.timestamps) < limit:
                await self._owner_and_log(
                    guild,
                    event=f"{kind}_watch",
                    embed=Embeds.plain(
                        f"👀 **{actor.mention}** performed action **#{len(window.timestamps)}/{limit}** "
                        f"({label} within {seconds}s).",
                        color=config.WARNING_COLOR,
                    ),
                    severity="info",
                    actor_id=actor.id,
                    target_id=target_id,
                    details=f"{len(window.timestamps)}/{limit} in window",
                )
                return

            # ---- breach ----
            window.timestamps = []
            label = {"ban": "bans", "kick": "kicks", "channel": "channel deletions", "role": "role deletions"}[kind]
            embed = Embeds.error(
                "Anti-nuke breach detected",
                f"**{actor.mention}** (`{actor.id}`) hit the **{limit}**-{label}-per-"
                f"{seconds}s limit in **{guild.name}**.\n"
                f"Configured response: **{settings.get('action')}**.",
            )
            await self._owner_and_log(
                guild,
                event=f"{kind}_breach",
                embed=embed,
                severity="critical",
                actor_id=actor.id,
                details=f"{limit}+ {label} in {seconds}s",
            )
            if settings.get("lockdown_on_nuke"):
                await self.lockdown(guild, reason="Automatic anti-nuke lockdown", actor=None)
            await self._respond_to_breach(guild, settings, actor, kind)
        except Exception:  # pragma: no cover - the watcher must never crash
            log.exception("Anti-nuke evaluation failed for guild %s", guild.id)

    async def _respond_to_breach(
        self, guild: discord.Guild, settings: dict, actor: discord.Member, kind: str
    ) -> None:
        """Default-deny response: hierarchy-verified timeout or kick."""
        action = str(settings.get("action") or "timeout")
        key = (guild.id, actor.id)
        now_ts = time.time()
        if now_ts - self._last_alert.get(key, 0.0) < 300:
            return  # already handled this actor very recently
        self._last_alert[key] = now_ts

        owner_id = await get_guild_owner_id(guild)
        if owner_id is not None and actor.id == owner_id:
            return
        hierarchy = perms.bot_can_act(guild.me, actor)
        if not hierarchy.ok:
            await self._alert_owner(
                guild,
                Embeds.warning(
                    "Anti-nuke could not act automatically",
                    f"{hierarchy.reason}\nTarget: {actor.mention} (`{actor.id}`)",
                ),
            )
            return
        try:
            if action == "kick":
                await actor.kick(reason=f"Anti-nuke: {kind} breach")
                outcome = f"{actor.mention} was **kicked**."
            elif action == "timeout":
                until = discord.utils.utcnow() + timedelta(hours=12)
                await actor.timeout(until, reason=f"Anti-nuke: {kind} breach")
                outcome = f"{actor.mention} was **timed out for 12h**."
            else:
                outcome = "Alert-only mode — logged and reported, no punishment."
        except discord.Forbidden:
            outcome = "I lacked the permission to punish them."
        await self.owner_alert(
            guild,
            title="🚨 Anti-nuke response executed",
            description=f"{outcome}\nKind: `{kind}` • Response mode: `{action}`",
        )
        await self._owner_and_log(
            guild,
            event=f"anti_nuke_{action}",
            embed=Embeds.log(
                f"Anti-nuke {action}",
                outcome,
                fields=[("Actor", actor.mention, True), ("Kind", kind, True)],
            ),
            severity="critical",
            actor_id=actor.id,
        )

    async def owner_alert(self, guild: discord.Guild, *, title: str, description: str) -> None:
        await self._alert_owner(guild, Embeds.error(title, description))

    async def _resolve_actor(
        self,
        guild: discord.Guild,
        kind: str,
        target_id: int,
        audit_action: discord.AuditLogAction,
    ) -> Optional[discord.Member]:
        """Find who committed an action using Discord's audit log."""
        cached = self._actor_cache.get((guild.id, kind, target_id))
        if cached is not None:
            actor_id, stamp = cached
            if time.time() - stamp < 900:
                member = guild.get_member(actor_id)
                if member is not None:
                    return member
        try:
            async for entry in guild.audit_logs(limit=6, action=audit_action):
                entry_target = getattr(entry, "target_id", None) or getattr(
                    getattr(entry, "target", None), "id", None
                )
                entry_time = getattr(entry, "created_at", None)
                is_recent = (
                    entry_time is not None
                    and (discord.utils.utcnow() - entry_time) < timedelta(seconds=60)
                )
                if entry_target == target_id and is_recent:
                    actor_id = entry.user_id or getattr(entry.user, "id", None)
                    if actor_id is not None:
                        self._actor_cache[(guild.id, kind, target_id)] = (int(actor_id), time.time())
                        member = guild.get_member(int(actor_id))
                        if member is None:
                            try:
                                member = await guild.fetch_member(int(actor_id))
                            except discord.HTTPException:
                                member = None
                        return member
        except discord.Forbidden:
            log.debug("Need View Audit Log in guild %s to attribute events", guild.id)
        except discord.HTTPException:
            log.debug("Audit log fetch failed in guild %s", guild.id, exc_info=True)
        return None

    # --------------------------------------------------------------- lockdown
    async def lockdown(
        self,
        guild: discord.Guild,
        *,
        reason: str,
        actor: Optional[discord.abc.User],
    ) -> list[str]:
        """Pause @everyone messaging in every text channel (reversible)."""
        if guild.id in self._lockdown_state:
            return ["already locked"]
        stored: dict[int, discord.PermissionOverwrite] = {}
        issues: list[str] = []
        notice = Embeds.error(
            "🚨 Server locked down",
            f"**{guild.name}** is in emergency lockdown.\nReason: {truncate(reason, 300)}\n"
            "Staff will release it soon — this is a security measure.",
        )
        for channel in guild.text_channels:
            try:
                previous = channel.overwrites_for(guild.default_role)
                stored[channel.id] = previous.copy()
                previous.send_messages = False
                await channel.set_permissions(
                    guild.default_role,
                    overwrite=previous,
                    reason=f"Emergency lockdown by {actor or 'anti-nuke'} | {reason}",
                )
            except (discord.HTTPException, discord.Forbidden):
                issues.append(f"could not lock {getattr(channel, 'mention', channel.id)}")
        self._lockdown_state[guild.id] = stored
        try:
            system_channel = guild.system_channel
            if isinstance(system_channel, discord.TextChannel):
                await system_channel.send(embed=notice)
        except (discord.HTTPException, discord.Forbidden):
            pass
        await self._owner_and_log(
            guild,
            event="lockdown_on",
            embed=Embeds.log(
                "EMERGENCY LOCKDOWN engaged",
                reason,
                fields=[("By", getattr(actor, "mention", "anti-nuke"), True),
                        ("Channels", str(len(stored)), True)],
            ),
            severity="critical",
            details=reason,
        )
        return [f"locked {len(stored)} channels"] + issues

    async def release_lockdown(self, guild: discord.Guild, *, reason: str, actor) -> list[str]:
        stored = self._lockdown_state.pop(guild.id, None)
        if not stored:
            return ["not locked"]
        issues: list[str] = []
        for channel in guild.text_channels:
            if channel.id not in stored:
                continue
            try:
                await channel.set_permissions(
                    guild.default_role,
                    overwrite=stored[channel.id],
                    reason=f"Lockdown release by {actor} | {reason}",
                )
            except (discord.HTTPException, discord.Forbidden):
                issues.append(f"could not release {channel.mention}")
        await self._owner_and_log(
            guild,
            event="lockdown_off",
            embed=Embeds.log(
                "🔒 Lockdown RELEASED",
                reason,
                fields=[("By", getattr(actor, "mention", "?"), True),
                        ("Channels", str(len(stored)), True)],
            ),
        )
        return [f"released {len(stored)} channels"] + issues

    # --------------------------------------------------------------- listeners
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        await self._register_event_count(guild, "ban", user.id, discord.AuditLogAction.ban)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self._register_event_count(
            channel.guild,
            "channel",
            channel.id,
            discord.AuditLogAction.channel_delete,
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self._register_event_count(
            role.guild, "role", role.id, discord.AuditLogAction.role_delete
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Mass-kick tracking: attribute the removal to a kicker via audit log."""
        if not isinstance(member, discord.Member):
            return
        guild = member.guild
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.member_kick):
                entry_target = getattr(entry, "target_id", None) or getattr(
                    getattr(entry, "target", None), "id", None
                )
                entry_time = getattr(entry, "created_at", None)
                is_recent = (
                    entry_time is not None
                    and (discord.utils.utcnow() - entry_time) < timedelta(seconds=30)
                )
                if entry_target == member.id and is_recent:
                    await self._register_event_count(
                        guild, "kick", member.id, discord.AuditLogAction.member_kick
                    )
                    return
        except (discord.Forbidden, discord.HTTPException):
            return

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Anti-raid join flood, suspicious-account detection and bot-add alerts."""
        try:
            settings = await self.bot.db.get_security(member.guild.id)
            if not int(settings.get("anti_nuke_enabled", 1)):
                return
            if member.bot:
                await self._alert_bot_added(member.guild, member)
                return
            await self._check_raid(member.guild, settings)
            await self._check_suspicious_account(member, settings)
        except Exception:  # pragma: no cover - the watcher must never crash
            log.exception("Security join evaluation failed", exc_info=True)

    async def _check_raid(self, guild: discord.Guild, settings: dict) -> None:
        """Detect a sudden flood of joins inside the configured window."""
        limit = max(2, int(settings.get("join_limit", 8) or 8))
        window = max(10, int(settings.get("join_window_seconds", 30) or 30))
        now_ts = time.time()
        join_window = self._join_windows.setdefault(guild.id, _Window())
        join_window.timestamps = [t for t in join_window.timestamps if now_ts - t <= window]
        join_window.timestamps.append(now_ts)
        count = len(join_window.timestamps)
        if count < limit or now_ts - self._last_alert.get(("raid", guild.id), 0.0) < 600:
            return
        self._last_alert[("raid", guild.id)] = now_ts
        join_window.timestamps = []
        message = Embeds.error(
            "🚨 Possible raid detected",
            f"**{count} members joined within {window}s** — this can be a mass-join raid.\n"
            "Suggestions: enable `/automod` word & invite filters, slow the server down, "
            "or run `/emergencylockdown on` if it gets worse.",
        )
        await self._owner_and_log(
            guild,
            event="raid_joins",
            embed=message,
            severity="critical",
            details=f"{count} joins in {window}s",
        )
        await self._alert_owner(guild, message)
        if str(settings.get("raid_action")) == "lockdown":
            await self.lockdown(guild, reason="Automatic anti-raid lockdown", actor=None)

    async def _check_suspicious_account(self, member: discord.Member, settings: dict) -> None:
        """Flag brand-new accounts joining the server (likely alt/raid accounts)."""
        min_age = int(settings.get("min_account_age_days", 3) or 0)
        if min_age <= 0:
            return
        now = discord.utils.utcnow()
        age_days = (now - member.created_at).days
        if age_days >= min_age:
            return
        alert = Embeds.warning(
            "🕵️ Suspicious new account joined",
            f"{member.mention} (`{member}`) joined **{member.guild.name}** with an account "
            f"only **{max(age_days, 0)} day(s) old** (< {min_age} days).\n"
            f"Member count: {member.guild.member_count}. Watch them or timeout if they misbehave.",
        )
        # Throttle: one alert per account creation day-bucket per guild.
        key = ("sus", member.guild.id)
        if time.time() - self._last_alert.get(key, 0.0) < 20:
            await self._owner_and_log(
                member.guild,
                event="suspicious_account",
                embed=alert,
                severity="warning",
                actor_id=member.id,
                details=f"account age {age_days}d",
            )
            return
        self._last_alert[key] = time.time()
        await self._owner_and_log(
            member.guild,
            event="suspicious_account",
            embed=alert,
            severity="warning",
            actor_id=member.id,
            details=f"account age {age_days}d",
        )

    async def _alert_bot_added(self, guild: discord.Guild, bot_member: discord.Member) -> None:
        """Alert the server owner whenever a third-party bot is added."""
        inviter: discord.User | None = None
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.bot_add):
                if getattr(entry, "target_id", None) == bot_member.id:
                    inviter = entry.user
                    break
        except (discord.Forbidden, discord.HTTPException):
            inviter = None
        who = f"added by **{inviter.mention}** (`{inviter.id}`)" if inviter else "invited (added by **unknown** — grant me *View Audit Log*)"
        alert = Embeds.warning(
            "🤖 A bot was added to the server",
            f"**{bot_member.mention}** (`{bot_member}`) was {who}.\n"
            "Review its permissions; if it is unexpected, kick it and re-check role assignment.",
        )
        await self._owner_and_log(
            guild,
            event="bot_added",
            embed=alert,
            severity="warning",
            actor_id=inviter.id if inviter else None,
            target_id=bot_member.id,
            details=f"bot {bot_member.id} added",
        )
        await self._alert_owner(guild, alert)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel, webhooks, guild) -> None:  # noqa: ANN001
        """Alert when a new webhook is created in any channel."""
        try:
            previous = self._webhook_counts.get(guild.id, {})
            current = {w.id: w for w in webhooks}
            self._webhook_counts[guild.id] = current
            if not guild.me.guild_permissions.view_audit_log:
                return
            new_ids = set(current) - set(previous)
            if not new_ids:
                return
            try:
                async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.webhook_create):
                    entry_time = getattr(entry, "created_at", None)
                    is_recent = (
                        entry_time is not None
                        and (discord.utils.utcnow() - entry_time) < timedelta(seconds=60)
                    )
                    if not is_recent:
                        continue
                    created_name = str(getattr(entry, "extra", ""))
                    creator = getattr(entry, "user", None)
                    for webhook_id in new_ids:
                        webhook = current[webhook_id]
                        alert = Embeds.warning(
                            "🪝 New webhook created",
                            f"Webhook **{webhook.name}** in {getattr(channel, 'mention', channel)} "
                            + (f"created by **{creator.mention}**." if creator else "created (creator unknown — grant me *View Audit Log*).")
                            + "\nWebhooks can spam the server — verify this was intentional.",
                        )
                        await self._owner_and_log(
                            guild,
                            event="webhook_created",
                            embed=alert,
                            severity="warning",
                            actor_id=creator.id if creator else None,
                            target_id=int(webhook_id),
                            details=f"webhook {webhook.name} in {created_name}",
                        )
                        await self._alert_owner(guild, alert)
                    return
            except (discord.Forbidden, discord.HTTPException):
                pass
        except Exception:  # pragma: no cover - the watcher must never crash
            log.exception("Webhook audit failed", exc_info=True)

    # --------------------------------------------------------------- commands
    @commands.hybrid_command(name="changelog")
    async def changelog(self, ctx: commands.Context) -> None:
        """Show the latest bot update notes (what was added recently)."""
        release = parse_updates(config.UPDATES_PATH)
        if not release.version:
            await ctx.send(
                embed=Embeds.info("Changelog", "No release notes found on the host."),
                ephemeral=True,
            )
            return
        embed = build_update_embed(release)
        embed.set_thumbnail(url=ctx.me.display_avatar.url if ctx.me else None)
        await ctx.send(embed=embed, ephemeral=False)

    @commands.hybrid_group(name="botstatus", invoke_without_command=True)
    async def botstatus(self, ctx: commands.Context) -> None:
        """Show the 🟢/🟡/🔴 status, version, uptime and latest update."""
        await self._send_status_embed(ctx)

    @botstatus.command(name="set")
    @bot_owner_only()
    @app_commands.describe(status="New operating mode", message="Status note shown in the embed")
    @app_commands.choices(status=STATUS_CHOICES)
    async def botstatus_set(self, ctx: commands.Context, status: str = "online", *, message: str = "") -> None:
        """Set the status mode (bot owner only)."""
        if status not in STATUS_META:
            status = "online"
        await self.bot.db.set_stat(0, STAT_STATUS_MODE, status)
        await self.bot.db.set_stat(0, STAT_STATUS_MESSAGE, truncate(message, 500))
        await self.bot.db.log_security_event(
            ctx.guild.id if ctx.guild else 0,
            "bot_status_changed",
            actor_id=ctx.author.id,
            details=f"mode={status} message={truncate(message, 200)}",
        )
        icon, label, _ = STATUS_META[status]
        await self.bot._set_presence()  # presence mirrors the new mode
        await ctx.send(
            embed=Embeds.success(
                "Status updated",
                f"Bot status is now {icon} **{label}**" + (f"\n> {truncate(message, 300)}" if message else ""),
            ),
        )

    async def _send_status_embed(self, ctx: commands.Context) -> None:
        mode = str(await self.bot.db.get_stat(0, STAT_STATUS_MODE, "online") or "online")
        if mode not in STATUS_META:
            mode = "online"
        icon, label, color = STATUS_META[mode]
        note = str(await self.bot.db.get_stat(0, STAT_STATUS_MESSAGE, "") or "")
        uptime_seconds = int(time.time()) - _start_epoch()
        release = parse_updates(config.UPDATES_PATH)
        from utils.time_parser import format_duration

        embed = discord.Embed(title="🤖 Bot Status", color=color)
        embed.add_field(name="Status", value=f"{icon} **{label}**", inline=True)
        embed.add_field(name="Version", value=f"`v{config.BOT_VERSION}`", inline=True)
        embed.add_field(name="Uptime", value=format_duration(uptime_seconds), inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(
            name="Latest update",
            value=release.latest_summary(600) if release.version else "No release notes found.",
            inline=False,
        )
        if note:
            embed.add_field(name="Note", value=note, inline=False)
        embed.set_footer(text=config.FOOTER_TEXT)
        await ctx.send(embed=embed, ephemeral=False)

    # /botupdatesetup ----------------------------------------------------------
    @commands.hybrid_command(name="botupdatesetup")
    @commands.guild_only()
    @require_admin()
    @commands.cooldown(2, 10, commands.BucketType.guild)
    @app_commands.describe(
        channel="Channel that receives bot updates automatically",
        post_now="Also post the latest release notes right away",
    )
    async def botupdatesetup(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
        post_now: bool = True,
    ) -> None:
        """Pick the update channel for automatic release announcements."""
        target = channel or ctx.channel
        if not isinstance(target, discord.TextChannel):
            await ctx.send(embed=Embeds.error("Unsupported", "Updates need a text channel."), ephemeral=True)
            return
        channel_permissions = target.permissions_for(ctx.guild.me)
        if not (channel_permissions.send_messages and channel_permissions.embed_links):
            await ctx.send(
                embed=Embeds.error(
                    "Cannot post there",
                    f"I need **Send Messages** and **Embed Links** in {target.mention}.",
                ),
                ephemeral=True,
            )
            return
        await self.bot.db.set_stat(ctx.guild.id, STAT_UPDATE_CHANNEL, str(target.id))
        await self.bot.db.log_security_event(
            ctx.guild.id,
            "update_channel_set",
            actor_id=ctx.author.id,
            details=f"channel={target.id}",
        )
        release = parse_updates(config.UPDATES_PATH)
        desc = f"{target.mention} will now receive bot updates automatically.\n"
        if not release.version:
            desc += "⚠️ No release notes file found — add `UPDATES.md` on the host."
        await ctx.send(embed=Embeds.success("Update channel configured", desc), ephemeral=True)
        if post_now and release.version:
            await target.send(embed=build_update_embed(release))

    # /securitysetup ------------------------------------------------------------
    @commands.hybrid_command(name="securitysetup")
    @commands.guild_only()
    @require_admin()
    @commands.cooldown(2, 10, commands.BucketType.guild)
    async def securitysetup(self, ctx: commands.Context) -> None:
        """Interactive anti-nuke & security configuration panel."""
        settings = await self.bot.db.get_security(ctx.guild.id)
        view = SecuritySetupView(self, ctx.author.id, ctx.guild.id, settings)
        embed = view._embed()
        await ctx.send(embed=embed, view=view, ephemeral=True)

    # /permissionscheck ----------------------------------------------------------
    @commands.hybrid_command(name="permissionscheck")
    @commands.guild_only()
    @require_moderator()
    @commands.cooldown(2, 10, commands.BucketType.guild)
    async def permissionscheck(self, ctx: commands.Context) -> None:
        """Audit this server's security posture in one embed."""
        await ctx.defer(ephemeral=True)
        guild = ctx.guild
        me = guild.me
        lines: list[str] = []

        bot_missing = perms.missing_permissions(
            me, "manage_channels", "manage_roles", "manage_messages", "moderate_members", "ban_members", "kick_members"
        )
        if bot_missing:
            lines.append(f"⚠️ Bot missing: **{', '.join(bot_missing)}**")
        else:
            lines.append("✅ Bot holds all moderation permissions it needs")

        guild_settings = await self.bot.db.get_guild(guild.id)
        security_settings = await self.bot.db.get_security(guild.id)
        if security_settings.get("anti_nuke_enabled"):
            lines.append("✅ Anti-nuke protection is enabled")
        else:
            lines.append("🔴 Anti-nuke protection is **OFF** — run `/securitysetup`")
        for key, label in (
            ("mod_log_channel", "Moderation logs"),
            ("server_log_channel", "Server logs"),
            ("welcome_channel", "Welcome channel"),
            ("staff_role_id", "Staff role"),
        ):
            if guild_settings.get(key):
                lines.append(f"✅ {label} configured")
            else:
                lines.append(f"⚠️ {label} not set")

        admin_roles = [
            role for role in guild.roles
            if role.permissions.administrator and not role.managed and not role.is_default()
        ]
        dangerous_lines = []
        for role in admin_roles:
            dangerous_lines.append(f"<@&{role.id}> — **Administrator** ({len(role.members)} members)")
        webhook_perm = [
            role for role in guild.roles
            if role.permissions.manage_webhooks and not role.permissions.administrator
        ]
        for role in webhook_perm[:5]:
            dangerous_lines.append(f"<@&{role.id}> — Manage Webhooks ({len(role.members)} members)")
        if dangerous_lines:
            lines.append("🔎 Powerful roles: " + " • ".join(dangerous_lines[:6]))
        admins = [
            m for m in guild.members if not m.bot and m.guild_permissions.administrator and m.id != guild.owner_id
        ]
        lines.append(f"👥 Members with Administrator: **{len(admins)}**")

        my_top = me.top_role
        above_bot = [m for m in guild.members if m.top_role.position >= my_top.position and not m.bot]
        if above_bot:
            lines.append(f"⚠️ {len(above_bot)} member(s) sit at or above my top role — hierarchy pawns cannot be punished")

        embed = Embeds.info(f"🩺 Security check — {truncate(guild.name, 50)}", "\n".join(lines[:20]))
        await self.bot.db.log_security_event(
            guild.id, "permissionscheck", actor_id=ctx.author.id
        )
        await ctx.send(embed=embed, ephemeral=True)

    # /auditlogs ------------------------------------------------------------------
    @commands.hybrid_command(name="auditlogs")
    @commands.guild_only()
    @require_moderator()
    @commands.cooldown(2, 10, commands.BucketType.member)
    @app_commands.describe(limit="How many entries (5-25)")
    async def auditlogs(self, ctx: commands.Context, limit: app_commands.Range[int, 5, 25] = 15) -> None:
        """Recent sensitive security actions and anti-nuke events."""
        rows = await self.bot.db.recent_security_events(ctx.guild.id, limit=limit)
        if not rows:
            await ctx.send(
                embed=Embeds.info("Audit log", "No security events recorded yet."), ephemeral=True
            )
            return
        severity_icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}
        lines = []
        for row in rows:
            actor = f"<@{row['actor_id']}>" if row.get("actor_id") else "`unknown`"
            created = _iso_to_epoch(row.get("created_at"))
            lines.append(
                f"{severity_icon.get(str(row.get('severity')), 'ℹ️')} `{row['event']}` • {actor} • <t:{created}:R>\n"
                f"└ {truncate(str(row.get('details') or ''), 140)}"
            )
        embed = Embeds.plain("### 🧾 Security audit log\n" + "\n".join(lines), config.LOG_COLOR)
        await ctx.send(embed=embed, ephemeral=True)

    # /emergencylockdown ----------------------------------------------------------
    @commands.hybrid_group(name="emergencylockdown", invoke_without_command=True)
    @commands.guild_only()
    async def emergencylockdown(self, ctx: commands.Context) -> None:
        """Emergency lockdown controls (server owner or bot owner only)."""
        await ctx.send(
            embed=Embeds.info(
                "🔒 Emergency lockdown",
                "`/emergencylockdown on reason:` — freeze all text channels (owners only)\n"
                "`/emergencylockdown off` — release the lockdown\n"
                "`/emergencylockdown status` — current state",
            ),
            ephemeral=True,
        )

    @emergencylockdown.command(name="on")
    @require_guild_or_bot_owner()
    @commands.cooldown(1, 30, commands.BucketType.guild)
    @app_commands.describe(reason="Why the server is being locked down")
    async def lockdown_on(self, ctx: commands.Context, *, reason: str = "Manual lockdown") -> None:
        """Freeze every text channel (default-deny: real owners only)."""
        if ctx.guild.id in self._lockdown_state:
            await ctx.send(
                embed=Embeds.warning("Already locked", "The server is already in lockdown. Use `/emergencylockdown off`."),
                ephemeral=True,
            )
            return
        confirmed = await ctx_confirm(ctx, prompt=Embeds.warning(
            "Engage emergency lockdown?",
            f"This freezes **@everyone** messaging in {len(ctx.guild.text_channels)} text channel(s).\n"
            f"Reason: {truncate(reason, 200)}\n\nOnly the server and bot owners can release it.",
        ), confirm_label="LOCK SERVER", author_id=ctx.author.id)
        if not confirmed:
            return
        result = await self.lockdown(ctx.guild, reason=reason, actor=ctx.author)
        await ctx.send(embed=Embeds.success("Lockdown engaged", "\n".join(result)), ephemeral=True)

    @emergencylockdown.command(name="off")
    @require_guild_or_bot_owner()
    @commands.cooldown(1, 30, commands.BucketType.guild)
    @app_commands.describe(reason="Why the lockdown is being released")
    async def lockdown_off(self, ctx: commands.Context, *, reason: str = "Manual release") -> None:
        """Release an emergency lockdown."""
        result = await self.release_lockdown(ctx.guild, reason=reason, actor=ctx.author)
        if result == ["not locked"]:
            await ctx.send(embed=Embeds.info("Not locked", "No active lockdown in this server."), ephemeral=True)
            return
        await ctx.send(embed=Embeds.success("Lockdown released", "\n".join(result)), ephemeral=True)

    @emergencylockdown.command(name="status")
    @commands.guild_only()
    async def lockdown_status(self, ctx: commands.Context) -> None:
        """Show whether the server is currently locked down."""
        state = self._lockdown_state.get(ctx.guild.id)
        if state:
            await ctx.send(
                embed=Embeds.error(
                    "🔒 Lockdown active",
                    f"{len(state)} text channel(s) are frozen. Use `/emergencylockdown off` to release.",
                )
            )
        else:
            await ctx.send(embed=Embeds.success("🟢 No lockdown", "All channels are at normal permissions."))

    # ----------------------------------------------------------- update announcer
    @tasks.loop(minutes=15)
    async def update_loop(self) -> None:
        """Post release notes to every configured update channel when a new version lands."""
        try:
            release = parse_updates(config.UPDATES_PATH)
            if not release.version:
                return
            guild_ids = await self.bot.db.all_guild_ids()
            for guild_id in guild_ids:
                try:
                    announced = await self.bot.db.get_stat(guild_id, STAT_ANNOUNCED_VERSION, None)
                    if announced == release.version:
                        continue
                    channel_id = await self.bot.db.get_stat(guild_id, STAT_UPDATE_CHANNEL, None)
                    if not channel_id:
                        continue
                    guild = self.bot.get_guild(int(guild_id))
                    if guild is None:
                        continue
                    channel = guild.get_channel(int(channel_id))
                    if not isinstance(channel, discord.TextChannel):
                        continue
                    await channel.send(embed=build_update_embed(release))
                    await self.bot.db.set_stat(guild_id, STAT_ANNOUNCED_VERSION, release.version)
                    await self.bot.db.log_security_event(
                        guild_id, "update_announced", target_id=channel.id,
                        details=f"v{release.version}",
                    )
                except Exception:
                    log.exception("Update announce failed for guild %s", guild_id)
        except Exception:  # pragma: no cover - keep the loop alive
            log.exception("update_loop iteration failed")

    @update_loop.before_loop
    async def before_update_loop(self) -> None:
        await self.bot.wait_until_ready()


def _bot_owner_check():  # legacy alias kept so old imports do not break
    return bot_owner_only()


async def ctx_confirm(
    ctx: commands.Context, *, prompt: discord.Embed, confirm_label: str, author_id: int
) -> bool:
    """Send a confirmation view and return True when the author confirms."""
    view = ConfirmView(author_id=author_id, confirm_label=confirm_label)
    message = await ctx.send(embed=prompt, view=view)
    await view.wait()
    try:
        await message.edit(view=None)
    except discord.HTTPException:
        pass
    return bool(view.value)


def _iso_to_epoch(value: Any) -> int:
    try:
        from datetime import datetime as _dt

        parsed = _dt.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return 0


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Security(bot))
