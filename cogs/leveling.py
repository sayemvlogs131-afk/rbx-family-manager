"""🏅 Leveling & XP system for RBX Family Manager.

Members earn XP for chatting (one gain per minute, with a small random
bonus so grind pace feels natural). Levels are worked out from total XP:

* cumulative requirement: ``xp_to_next(lvl) = 5*lvl² + 50*lvl + 100``

Features:

* ``/rank`` — rank card with progress bar (anyone)
* ``/leaderboard`` — top 10 by XP (anyone)
* ``/levelsetup channel:…`` — where level-up messages are posted (admin)
* ``/rankrole add|remove|list`` — Discord roles granted automatically at
  a level (admin); stored in ``guild_stats`` as JSON
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import require_admin
from utils.embeds import Embeds

log = logging.getLogger("rbx.leveling")

XP_COOLDOWN = 60.0  # seconds between XP gains
XP_MIN, XP_MAX = 15, 25
LEVEL_ROLES_KEY = "level_roles"
LEVEL_CHANNEL_KEY = "level_announce_channel"

BAR_LENGTH = 10


def xp_for_level(level: int) -> int:
    """Total XP needed to reach ``level`` (cumulative)."""
    return 5 * level * level + 50 * level + 100


def level_from_xp(xp: int) -> int:
    """The level a total-XP value corresponds to (0 from 0 XP)."""
    level = 0
    total = 0
    while True:
        total += xp_for_level(level)
        if total > xp:
            return level
        level += 1
        if level > 1000:  # never happen, guard against silly input
            return level


class Leveling(commands.Cog, name="Leveling"):
    """XP tracking, rank cards and automatic rank roles."""

    category = "🧰 Utility"
    category_description = "Reminders, info, polls, embeds and bot status."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_gain: dict[tuple[int, int], float] = {}

    # -------------------------------------------------------------- helpers
    async def _level_roles(self, guild: discord.Guild) -> dict[int, int]:
        """Configured {level: role_id} map for a guild."""
        raw = await self.bot.db.get_stat(guild.id, LEVEL_ROLES_KEY, "")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return {int(level): int(role_id) for level, role_id in data.items()}
        except (ValueError, TypeError):
            log.warning("Corrupt level-roles config in guild %s", guild.id)
            return {}

    async def _announce_channel(self, guild: discord.Guild, fallback_channel: Any) -> Any:
        raw = await self.bot.db.get_stat(guild.id, LEVEL_CHANNEL_KEY, "")
        if raw and str(raw).isdigit():
            channel = guild.get_channel(int(raw))
            if isinstance(channel, discord.abc.Messageable):
                return channel
        return fallback_channel

    # --------------------------------------------------------------- XP core
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        guild = message.guild
        author = message.author
        if guild is None or author.bot or not isinstance(author, discord.Member):
            return
        now = time.time()
        key = (guild.id, author.id)
        if now - self._last_gain.get(key, 0.0) < XP_COOLDOWN:
            return
        self._last_gain[key] = now

        gain = random.randint(XP_MIN, XP_MAX)
        current = await self.bot.db.get_level(guild.id, author.id)
        old_level = level_from_xp(int(current["xp"]))
        new_xp = int(current["xp"]) + gain
        new_level = level_from_xp(new_xp)
        try:
            await self.bot.db.set_level_xp(guild.id, author.id, new_xp, int(now))
        except Exception:
            log.exception("Failed to store XP for %s in guild %s", author.id, guild.id)
            return
        # Free the cooldown cache once it gets silly-large.
        if len(self._last_gain) > 5000:
            self._last_gain.clear()

        if new_level <= old_level:
            return
        await self._handle_level_up(guild, author, new_level)

    async def _handle_level_up(
        self, guild: discord.Guild, member: discord.Member, level: int
    ) -> None:
        """Announce a new level and grant any configured rank role."""
        channel = await self._announce_channel(guild, member.channel)
        if channel is not None:
            try:
                await channel.send(
                    embed=Embeds.success(
                        "🏅 Level up!",
                        f"{member.mention} just reached **level {level}**! Keep chatting to grow.",
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                )
            except AttributeError:
                pass
            except discord.HTTPException:
                log.debug("Could not announce level-up in guild %s", guild.id)

        roles = await self._level_roles(guild)
        grants = [(lvl, role_id) for lvl, role_id in roles.items() if lvl <= level]
        if not grants:
            return
        me = guild.me
        for _, role_id in sorted(grants, key=lambda item: item[0]):
            role = guild.get_role(int(role_id))
            if role is None:
                continue
            if me is None or role >= me.top_role:
                continue  # cannot grant roles at/above my own top role
            if role not in member.roles:
                try:
                    await member.add_roles(role, reason=f"Level {int(level)} reached")
                except discord.Forbidden:
                    log.debug("Missing perms to grant %s in guild %s", role.name, guild.id)
                except discord.HTTPException:
                    log.warning("Role grant failed for %s in guild %s", role.name, guild.id)

    # ------------------------------------------------------------- commands
    @commands.hybrid_command(name="rank")
    @app_commands.describe(member="Check someone else's rank (defaults to you)")
    async def rank(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ) -> None:
        """Show a rank card with level, XP and progress."""
        guild = ctx.guild
        if guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send(
                embed=Embeds.error("Servers only", "Use this inside a server."), ephemeral=True
            )
            return
        target = member or ctx.author
        try:
            data = await self.bot.db.get_level(guild.id, target.id)
            position = int(
                await self.bot.db.scalar(
                    "SELECT COUNT(*) FROM user_levels WHERE guild_id = ? AND xp > "
                    "(SELECT COALESCE(MAX(xp), 0) FROM user_levels "
                    "WHERE guild_id = ? AND user_id = ?)",
                    (guild.id, guild.id, target.id),
                    1,
                )
            )
        except Exception:
            log.exception("Rank lookup failed in guild %s", guild.id)
            await ctx.send(
                embed=Embeds.error("Rank failed", "Database hiccup — try again shortly."),
                ephemeral=True,
            )
            return

        xp = int(data["xp"])
        level = level_from_xp(xp)
        spent = sum(xp_for_level(l) for l in range(level))
        next_needed = xp_for_level(level)
        in_level = xp - spent
        ratio = 0 if next_needed <= 0 else min(1.0, in_level / next_needed)
        filled = int(ratio * BAR_LENGTH)
        bar = "🟩" * filled + "⬛" * (BAR_LENGTH - filled)
        embed = Embeds.info(
            f"🏅 Rank card — {target.display_name}",
            "",
        )
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Total XP", value=f"{xp:,}", inline=True)
        embed.add_field(name="Position", value=f"#{position}", inline=True)
        embed.add_field(
            name=f"Progress to level {level + 1}",
            value=f"`{bar}` {in_level}/{next_needed} XP",
            inline=False,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="leaderboard")
    @app_commands.describe(page="Which page of the top 10 (1 = best members)")
    async def leaderboard(self, ctx: commands.Context, page: int = 1) -> None:
        """Top 10 members by XP."""
        guild = ctx.guild
        if guild is None:
            await ctx.send(
                embed=Embeds.error("Servers only", "Use this inside a server."), ephemeral=True
            )
            return
        page = max(1, page)
        offset = (page - 1) * 10
        try:
            rows = await self.bot.db.fetchall(
                "SELECT user_id, xp FROM user_levels WHERE guild_id = ? "
                "ORDER BY xp DESC LIMIT 10 OFFSET ?",
                (guild.id, offset),
            )
        except Exception:
            log.exception("Leaderboard lookup failed in guild %s", guild.id)
            await ctx.send(
                embed=Embeds.error("Leaderboard failed", "Database hiccup — try again shortly."),
                ephemeral=True,
            )
            return
        if not rows:
            await ctx.send(
                embed=Embeds.info(
                    "🏆 Leaderboard",
                    f"Page {page} is empty — not enough chatty members yet!",
                )
            )
            return
        medals = ("🥇", "🥈", "🥉")
        lines = []
        for idx, row in enumerate(rows):
            badge = medals[idx] if idx < 3 and page == 1 else f"**{offset + idx + 1}.**"
            level = level_from_xp(int(row["xp"]))
            member = guild.get_member(int(row["user_id"]))
            name = member.display_name if member else f"`{row['user_id']}`"
            lines.append(f"{badge} **{name}** — lvl {level} ({int(row['xp']):,} XP)")
        embed = Embeds.info(f"🏆 Leaderboard — page {page}", "\n".join(lines))
        await ctx.send(embed=embed)

    # ------------------------------------------------------------ admin setup
    @commands.hybrid_group(name="levelsetup", invoke_without_command=True)
    async def levelsetup(self, ctx: commands.Context) -> None:
        """Leveling setup menu."""
        await ctx.send(
            embed=Embeds.info(
                "Leveling",
                "`/levelsetup channel:` announce channel • `/levelsetup off` disable announcements",
            )
        )

    @levelsetup.command(name="channel")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(channel="Channel where level-ups are announced")
    async def levelsetup_channel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        """Pick the level-up announcement channel."""
        try:
            await self.bot.db.set_stat(ctx.guild.id, LEVEL_CHANNEL_KEY, str(channel.id))
        except Exception:
            log.exception("Could not save level channel in guild %s", ctx.guild.id)
            await ctx.send(
                embed=Embeds.error("Not saved", "Storage hiccup — try again."), ephemeral=True
            )
            return
        await ctx.send(
            embed=Embeds.success(
                "Level-up channel set",
                f"New levels will now be celebrated in {channel.mention}.",
            )
        )

    @levelsetup.command(name="off")
    @commands.guild_only()
    @require_admin()
    async def levelsetup_off(self, ctx: commands.Context) -> None:
        """Hide level-up announcements."""
        try:
            await self.bot.db.set_stat(ctx.guild.id, LEVEL_CHANNEL_KEY, "")
        except Exception:
            log.exception("Could not clear level channel in guild %s", ctx.guild.id)
        await ctx.send(
            embed=Embeds.success(
                "Level-ups hidden",
                "Members still earn XP — you just won't see announcements.",
            )
        )

    @commands.hybrid_group(name="rankrole", invoke_without_command=True)
    async def rankrole(self, ctx: commands.Context) -> None:
        """Auto-granted role per level."""
        await ctx.send(
            embed=Embeds.info(
                "Rank roles",
                "`/rankrole add level: role:` • `/rankrole remove level:` • `/rankrole list`",
            )
        )

    @rankrole.command(name="add")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(
        level="Minimum level that gets this role",
        role="Role granted automatically at that level",
    )
    async def rankrole_add(
        self, ctx: commands.Context, level: app_commands.Range[int, 1, 999], role: discord.Role
    ) -> None:
        """Grant a role automatically when a member reaches a level."""
        roles = await self._level_roles(ctx.guild)  # type: ignore[union-attr]
        roles[level] = role.id
        try:
            await self.bot.db.set_stat(ctx.guild.id, LEVEL_ROLES_KEY, json.dumps(roles))
        except Exception:
            log.exception("Could not save rank role in guild %s", ctx.guild.id)
            await ctx.send(
                embed=Embeds.error("Not saved", "Storage hiccup — try again."), ephemeral=True
            )
            return
        await ctx.send(
            embed=Embeds.success(
                "Rank role added",
                f"{role.mention} is now auto-granted at **level {level}**.",
            )
        )

    @rankrole.command(name="remove")
    @commands.guild_only()
    @require_admin()
    @app_commands.describe(level="Level to stop granting a role at")
    async def rankrole_remove(self, ctx: commands.Context, level: int) -> None:
        """Remove the auto-grant for a level."""
        roles = await self._level_roles(ctx.guild)  # type: ignore[union-attr]
        if level not in roles:
            await ctx.send(
                embed=Embeds.error("Nothing there", f"No rank role is set for level {level}."),
                ephemeral=True,
            )
            return
        del roles[level]
        try:
            await self.bot.db.set_stat(ctx.guild.id, LEVEL_ROLES_KEY, json.dumps(roles))
        except Exception:
            log.exception("Could not save rank role in guild %s", ctx.guild.id)
            await ctx.send(
                embed=Embeds.error("Not saved", "Storage hiccup — try again."), ephemeral=True
            )
            return
        await ctx.send(embed=Embeds.success("Rank role removed", f"Level {level} no longer grants a role."))

    @rankrole.command(name="list")
    @commands.guild_only()
    @require_admin()
    async def rankrole_list(self, ctx: commands.Context) -> None:
        """Show all configured rank roles."""
        roles = await self._level_roles(ctx.guild)  # type: ignore[union-attr]
        if not roles:
            await ctx.send(
                embed=Embeds.info("Rank roles", "None set yet — use `/rankrole add level: role:`.")
            )
            return
        lines = []
        for lvl in sorted(roles):
            role = ctx.guild.get_role(int(roles[lvl]))  # type: ignore[union-attr]
            mention = role.mention if role else f"`deleted ({roles[lvl]})`"
            lines.append(f"Level **{lvl}** → {mention}")
        await ctx.send(embed=Embeds.info("Rank roles", "\n".join(lines)))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leveling(bot))
