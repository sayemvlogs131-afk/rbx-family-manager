"""⚙️ Automoderation for RBX Family Manager.

Every rule is configurable per guild and evaluated by the pure function
:func:`evaluate_automod`, which is unit-tested without a Discord connection.

Design goals:

* never punish normal conversation (thresholds are deliberately lenient)
* per-user cooldowns so the same person cannot be spammed-punished
* everything can be toggled independently from ``/automod``
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping

import discord
from discord import app_commands
from discord.ext import commands

from utils import permissions as perms
from utils.checks import require_admin
from utils.embeds import Embeds
from utils.helpers import INVITE_RE, URL_RE, truncate
from utils.time_parser import format_duration, parse_duration

log = logging.getLogger("rbx.automod")

# Known scam / phishing link patterns (reused across guilds, kept short and
# deliberately fuzzy — scam shops re-register lookalike domains weekly).
_SCAM_TOKENS = [
    "free-nitro",
    "nitrogift",
    "nitro-gift",
    "discordgift",
    "discord-gift",
    "steamcomunity",
    "steamcomnunity",
    "steamconmunity",
    "stearncommunity",
    "steamcomunuity",
    "steampowerd",
    "csgo-skin",
    "csgocase",
    "case-exchange",
    "skin-daily",
    "robuxgenerator",
    "robux-generat",
    "free-robux",
    "blox.claims",
]
SCAM_RE = re.compile(
    "|".join(re.escape(token) for token in _SCAM_TOKENS), re.IGNORECASE
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": 1,
    "anti_spam": 1,
    "anti_spam_messages": 6,
    "anti_spam_window": 5,
    "duplicate_filter": 1,
    "duplicate_window": 7,
    "mention_limit": 6,
    "emoji_limit": 12,
    "caps_limit": 70,
    "caps_min_length": 12,
    "invite_filter": 1,
    "scam_filter": 1,
    "link_filter": 0,
    "banned_words": [],
    "warn_threshold": 3,
    "timeout_duration": 300,
    "delete_violations": 1,
    "log_violations": 1,
    "exempt_roles": [],
    "exempt_channels": [],
}


@dataclass
class Violation:
    """A single automod rule break."""

    rule: str
    description: str
    points: int = 1


@dataclass
class UserTracker:
    """Sliding-window counters for one user in one guild."""

    timestamps: list[float] = field(default_factory=list)
    last_content: str = ""
    last_content_time: float = 0.0
    last_violation: float = 0.0
    strikes: int = 0


def _id_set(value: Any) -> set[int]:
    """Parse an id list that may be a real list or a JSON string from SQLite."""
    out: set[int] = set()
    for item in _parse_json_list(value):
        try:
            out.add(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _is_exempt(
    config_map: Mapping[str, Any], member: discord.Member, channel_id: int
) -> bool:
    if channel_id in _id_set(config_map.get("exempt_channels")):
        return True
    exempt_roles = _id_set(config_map.get("exempt_roles"))
    member_roles = {r.id for r in getattr(member, "roles", [])}
    if exempt_roles & member_roles:
        return True
    return perms.has_permissions(member, "administrator") or perms.has_permissions(
        member, "manage_guild"
    )


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def evaluate_automod(
    config_map: Mapping[str, Any],
    content: str,
    tracker: UserTracker,
    *,
    member: Any = None,
    channel_id: int = 0,
    now: float | None = None,
    mention_count: int | None = None,
    emoji_count: int | None = None,
    author_id: int | None = None,
) -> Violation | None:
    """Evaluate one message against the guild's automod rules.

    Returns the first :class:`Violation` found, or ``None`` when clean.
    Pure function: no Discord or database access, fully unit-testable.
    """
    if not config_map.get("enabled", 1):
        return None
    if member is not None and _is_exempt(config_map, member, channel_id):
        return None

    now = time.time() if now is None else now
    text = content or ""

    # ---- banned words ---------------------------------------------------
    banned = [str(w).lower() for w in _parse_json_list(config_map.get("banned_words"))]
    lowered = text.lower()
    for word in banned:
        if word and word in lowered:
            return Violation("banned_word", f"Message contains a banned word (`{truncate(word, 30)}`).")

    # ---- invite links ---------------------------------------------------
    if config_map.get("invite_filter", 1) and INVITE_RE.search(text):
        return Violation("invite", "Invite links are not allowed here.")

    # ---- scam / phishing links ------------------------------------------
    # Runs before the generic filters because scam links are the *first*
    # thing we want to catch (they do the most damage per message).
    if config_map.get("scam_filter", 1) and SCAM_RE.search(text):
        return Violation("scam", "That message contains a known scam/phishing link.", points=3)

    # ---- generic links --------------------------------------------------
    if config_map.get("link_filter", 0) and URL_RE.search(text):
        return Violation("link", "Links are not allowed here.")

    # ---- mention spam ---------------------------------------------------
    mention_limit = int(config_map.get("mention_limit", 6) or 0)
    if mention_limit > 0:
        count = mention_count
        if count is None:
            count = len(re.findall(r"<@!?\d+>|<@&\d+>", text))
        if count > mention_limit:
            return Violation(
                "mentions", f"Mentioned {count} roles/users (limit {mention_limit}).", points=2
            )

    # ---- excessive emoji ------------------------------------------------
    emoji_limit = int(config_map.get("emoji_limit", 12) or 0)
    if emoji_limit > 0:
        count = emoji_count
        if count is None:
            from utils.helpers import count_emojis

            count = count_emojis(text)
        if count > emoji_limit:
            return Violation("emoji", f"Too many emoji ({count}, limit {emoji_limit}).")

    # ---- excessive caps -------------------------------------------------
    caps_limit = int(config_map.get("caps_limit", 70) or 0)
    min_length = int(config_map.get("caps_min_length", 12) or 0)
    letters = [ch for ch in text if ch.isalpha()]
    if caps_limit > 0 and len(text) >= min_length and len(letters) >= 10:
        ratio = sum(1 for ch in letters if ch.isupper()) / len(letters) * 100
        if ratio > caps_limit and len(letters) > 15:
            return Violation("caps", f"Excessive caps ({ratio:.0f}% of letters).")

    # ---- duplicate message ---------------------------------------------
    if config_map.get("duplicate_filter", 1) and text.strip():
        window = float(config_map.get("duplicate_window", 7) or 7)
        if (
            tracker.last_content
            and tracker.last_content == text
            and now - tracker.last_content_time <= window
            and len(text.strip()) >= 4
        ):
            return Violation("duplicate", "Repeated messages are not allowed.")
        tracker.last_content = text
        tracker.last_content_time = now

    # ---- flood / anti-spam ---------------------------------------------
    if config_map.get("anti_spam", 1):
        limit = max(3, int(config_map.get("anti_spam_messages", 6) or 6))
        window = max(2, int(config_map.get("anti_spam_window", 5) or 5))
        tracker.timestamps.append(now)
        tracker.timestamps = [t for t in tracker.timestamps if now - t <= window]
        if len(tracker.timestamps) > limit:
            return Violation(
                "spam",
                f"Sent {len(tracker.timestamps)} messages in {window}s (limit {limit}).",
                points=2,
            )

    return None


class Automod(commands.Cog, name="Automod"):
    """Configurable automatic message moderation."""

    category = "🛡️ Moderation"
    category_description = "Automatic filtering: spam, invites, mentions, words."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._trackers: dict[tuple[int, int], UserTracker] = {}
        self._cooldowns: dict[tuple[int, int], float] = {}

    # ----------------------------------------------------------- listeners
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.webhook_id:
            return
        await self._evaluate(message)

    async def _evaluate(self, message: discord.Message) -> None:
        guild = message.guild
        assert guild is not None
        config_map = await self.bot.db.get_automod(guild.id)

        key = (guild.id, message.author.id)
        tracker = self._trackers.get(key)
        if tracker is None:
            tracker = UserTracker()
            self._trackers[key] = tracker

        violation = evaluate_automod(
            config_map,
            message.content,
            tracker,
            member=message.author,
            channel_id=message.channel.id,
            author_id=message.author.id,
        )
        if violation is None:
            return

        # Cooldown: don't punish the same user more than once per 15 seconds.
        now = time.time()
        if now - self._cooldowns.get(key, 0.0) < 15:
            if config_map.get("delete_violations", 1):
                try:
                    await message.delete()
                except discord.HTTPException:
                    pass
            return
        self._cooldowns[key] = now

        # Delete the message when configured.
        if config_map.get("delete_violations", 1):
            try:
                await message.delete()
            except discord.HTTPException:
                pass

        tracker.strikes += 1
        await self._log_violation(message, violation, config_map)

        # Warn / timeout escalation.
        threshold = max(1, int(config_map.get("warn_threshold", 3) or 3))
        if tracker.strikes % threshold == 0:
            duration = int(config_map.get("timeout_duration", 300) or 300)
            duration = max(6, min(duration, 28 * 86400))
            try:
                await message.author.timeout(
                    discord.utils.utcnow() + timedelta(seconds=duration),
                    reason=f"AutoMod: {violation.rule} ({tracker.strikes} strikes)",
                )
                await message.channel.send(
                    embed=Embeds.warning(
                        "AutoMod action",
                        f"{message.author.mention} was timed out for "
                        f"**{format_duration(duration)}** — {violation.description}",
                    ),
                    delete_after=10,
                )
            except discord.HTTPException:
                log.debug("AutoMod timeout failed", exc_info=True)

    async def _log_violation(
        self, message: discord.Message, violation: Violation, config_map: Mapping[str, Any]
    ) -> None:
        log.debug(
            "AutoMod hit %s for %s in %s",
            violation.rule,
            message.author,
            message.guild and message.guild.id,
        )
        if not config_map.get("log_violations", 1) or message.guild is None:
            return
        await self.bot.log_to_channel(
            message.guild,
            "mod_log_channel",
            Embeds.log(
                "AutoMod violation",
                violation.description,
                color=0xE67E22,
                fields=[
                    ("User", message.author.mention, True),
                    ("Channel", message.channel.mention, True),
                    ("Rule", violation.rule, True),
                    ("Content", truncate(message.content or "`(empty)`", 300), False),
                ],
                actor=message.author,
            ),
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        self._trackers.pop((member.guild.id, member.id), None)

    # ------------------------------------------------------------ commands
    @commands.hybrid_group(name="automod", invoke_without_command=True)
    @commands.guild_only()
    @require_admin()
    async def automod(self, ctx: commands.Context) -> None:
        """Configure the automatic moderation system."""
        config_map = await self.bot.db.get_automod(ctx.guild.id)
        embed = Embeds.info(
            "AutoMod configuration",
            "Toggle rules with `/automod toggle rule:` and tune thresholds with "
            "`/automod setting key: value:`.",
        )
        toggles = [
            ("enabled", "Master switch"),
            ("anti_spam", "Anti-spam (message flood)"),
            ("duplicate_filter", "Duplicate messages"),
            ("invite_filter", "Invite links"),
            ("scam_filter", "Scam/phishing links"),
            ("link_filter", "Any links"),
        ]
        for key, label in toggles:
            state = "✅" if config_map.get(key) else "❌"
            embed.add_field(name=label, value=state, inline=True)
        embed.add_field(
            name="Thresholds",
            value=(
                f"mentions ≤ **{config_map['mention_limit']}** • "
                f"emoji ≤ **{config_map['emoji_limit']}** • "
                f"caps ≤ **{config_map['caps_limit']}%**\n"
                f"spam ≥ **{config_map['anti_spam_messages']}** msgs / "
                f"**{config_map['anti_spam_window']}s** • "
                f"warn every **{config_map['warn_threshold']}** strikes • "
                f"timeout **{format_duration(config_map['timeout_duration'])}**"
            ),
            inline=False,
        )
        words = _parse_json_list(config_map.get("banned_words"))
        embed.add_field(
            name=f"Banned words ({len(words)})",
            value=truncate(", ".join(f"`{w}`" for w in words), 900) or "`—`",
            inline=False,
        )
        await ctx.send(embed=embed)

    @automod.command(name="toggle")
    @require_admin()
    @app_commands.describe(rule="Which rule to enable/disable")
    @app_commands.choices(
        rule=[
            app_commands.Choice(name="automod (master switch)", value="enabled"),
            app_commands.Choice(name="anti-spam", value="anti_spam"),
            app_commands.Choice(name="duplicate messages", value="duplicate_filter"),
            app_commands.Choice(name="invite links", value="invite_filter"),
            app_commands.Choice(name="scam/phishing links", value="scam_filter"),
            app_commands.Choice(name="all links", value="link_filter"),
            app_commands.Choice(name="delete violations", value="delete_violations"),
            app_commands.Choice(name="log violations", value="log_violations"),
        ]
    )
    async def automod_toggle(self, ctx: commands.Context, rule: str) -> None:
        """Enable or disable an automod rule."""
        config_map = await self.bot.db.get_automod(ctx.guild.id)
        new_value = 0 if config_map.get(rule, 1) else 1
        await self.bot.db.update_automod(ctx.guild.id, **{rule: new_value})
        if not new_value:
            state_text = "disabled"
        else:
            state_text = "enabled"
        await ctx.send(
            embed=Embeds.success(
                "AutoMod updated", f"`{rule}` is now **{state_text}**."
            )
        )

    @automod.command(name="setting")
    @require_admin()
    @app_commands.describe(
        key="Setting name", value="New value (number, or words separated by commas)"
    )
    @app_commands.choices(
        key=[
            app_commands.Choice(name="mention limit", value="mention_limit"),
            app_commands.Choice(name="emoji limit", value="emoji_limit"),
            app_commands.Choice(name="caps limit (%)", value="caps_limit"),
            app_commands.Choice(name="caps min length", value="caps_min_length"),
            app_commands.Choice(name="spam message count", value="anti_spam_messages"),
            app_commands.Choice(name="spam window (seconds)", value="anti_spam_window"),
            app_commands.Choice(name="duplicate window (seconds)", value="duplicate_window"),
            app_commands.Choice(name="warn threshold (strikes)", value="warn_threshold"),
            app_commands.Choice(name="timeout duration (e.g. 10m)", value="timeout_duration"),
        ]
    )
    async def automod_setting(self, ctx: commands.Context, key: str, value: str) -> None:
        """Change an automod threshold."""
        if key == "timeout_duration":
            try:
                parsed = parse_duration(value)
            except ValueError as exc:
                await ctx.send(embed=Embeds.error("Invalid duration", str(exc)), ephemeral=True)
                return
            await self.bot.db.update_automod(ctx.guild.id, timeout_duration=parsed)
            await ctx.send(
                embed=Embeds.success(
                    "AutoMod updated", f"Auto-timeout set to **{format_duration(parsed)}**."
                )
            )
            return

        if not value.strip().isdigit():
            await ctx.send(
                embed=Embeds.error("Invalid value", "This setting expects a whole number."),
                ephemeral=True,
            )
            return
        number = int(value)
        if number < 0 or number > 100000:
            await ctx.send(embed=Embeds.error("Out of range", "Value is out of range."), ephemeral=True)
            return
        await self.bot.db.update_automod(ctx.guild.id, **{key: number})
        await ctx.send(embed=Embeds.success("AutoMod updated", f"`{key}` = **{number}**."))

    @automod.command(name="word")
    @require_admin()
    @app_commands.describe(word="Word or phrase to block", action="add or remove")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
        ]
    )
    async def automod_word(
        self, ctx: commands.Context, word: str, action: str = "add"
    ) -> None:
        """Add or remove a banned word."""
        config_map = await self.bot.db.get_automod(ctx.guild.id)
        words = [str(w) for w in _parse_json_list(config_map.get("banned_words"))]
        target = word.strip().lower()
        if not target or len(target) > 60:
            await ctx.send(
                embed=Embeds.error("Invalid word", "Provide 1-60 characters."),
                ephemeral=True,
            )
            return
        if action == "add":
            if target in words:
                await ctx.send(
                    embed=Embeds.warning("Already blocked", f"`{target}` is already banned."),
                    ephemeral=True,
                )
                return
            words.append(target)
        else:
            if target not in words:
                await ctx.send(
                    embed=Embeds.error("Not found", f"`{target}` is not in the list."),
                    ephemeral=True,
                )
                return
            words.remove(target)
        await self.bot.db.update_automod(
            ctx.guild.id, banned_words=json.dumps(sorted(set(words)))
        )
        await ctx.send(
            embed=Embeds.success(
                "Banned words updated",
                f"**{len(words)}** word(s) configured.\n"
                f"`{action}`: `{truncate(target, 40)}`",
            )
        )

    @automod.command(name="exempt")
    @require_admin()
    @app_commands.describe(
        target="Role or channel to exempt from automod",
        action="add or remove",
        kind="role or channel",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
        ],
        kind=[
            app_commands.Choice(name="role", value="role"),
            app_commands.Choice(name="channel", value="channel"),
        ],
    )
    async def automod_exempt(
        self,
        ctx: commands.Context,
        target: str,
        action: str = "add",
        kind: str = "role",
    ) -> None:
        """Exempt a role or channel from automod."""
        config_map = await self.bot.db.get_automod(ctx.guild.id)
        key = "exempt_roles" if kind == "role" else "exempt_channels"
        items = [int(x) for x in _parse_json_list(config_map.get(key))]
        raw = target.strip().strip("<>&#>")
        if not raw.isdigit():
            await ctx.send(
                embed=Embeds.error("Invalid target", "Provide a role/channel mention or ID."),
                ephemeral=True,
            )
            return
        target_id = int(raw)
        if action == "add":
            if target_id not in items:
                items.append(target_id)
        else:
            if target_id not in items:
                await ctx.send(
                    embed=Embeds.error("Not found", "That target is not exempt yet."),
                    ephemeral=True,
                )
                return
            items.remove(target_id)
        await self.bot.db.update_automod(ctx.guild.id, **{key: json.dumps(items)})
        await ctx.send(
            embed=Embeds.success(
                "Exemptions updated", f"`{kind}` `{target_id}` → **{'added' if action == 'add' else 'removed'}**."
            )
        )

    @automod.command(name="reset")
    @require_admin()
    @app_commands.describe(confirm="Type RESET to confirm")
    async def automod_reset(self, ctx: commands.Context, confirm: str) -> None:
        """Reset automod to default settings."""
        if confirm != "RESET":
            await ctx.send(
                embed=Embeds.error("Cancelled", "Type `/automod reset confirm:RESET` to confirm."),
                ephemeral=True,
            )
            return
        await self.bot.db.update_automod(
            ctx.guild.id,
            **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()},
        )
        await ctx.send(embed=Embeds.success("AutoMod reset", "Defaults restored."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Automod(bot))
