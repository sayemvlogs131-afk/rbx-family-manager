"""🤖 Bot owner / developer commands for RBX Family Manager.

Every command here is restricted to the ``OWNER_ID`` configured in ``.env``.
Regular members cannot see or run any of them (double-gated: application
command check + :class:`discord.app_commands.DefaultChecksWithPrivateMessages`
style predicate).
"""

from __future__ import annotations

import logging
import platform
import time
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.checks import is_owner as owner_only  # single source of truth
from utils.embeds import Embeds
from utils.helpers import truncate

log = logging.getLogger("rbx.owner")

START_TIME = time.time()


class Owner(commands.Cog, name="Bot"):
    """Owner-only maintenance commands."""

    category = "🤖 Bot"
    category_description = "Bot information, maintenance and owner tools."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_group(name="owner", invoke_without_command=True)
    @commands.guild_only()
    @owner_only()
    async def owner(self, ctx: commands.Context) -> None:
        """Owner tools: reload, sync, shutdown, guilds, status, say, broadcast."""
        await ctx.send(
            embed=Embeds.info(
                "Owner commands",
                "`/owner reload cog:` • `/owner sync` • `/owner cookies file:` • `/owner backup` • `/owner shutdown` • "
                "`/owner guilds` • `/owner status` • `/owner say` • `/owner broadcast`",
            ),
            ephemeral=True,
        )

    @owner.command(name="reload")
    @owner_only()
    @app_commands.describe(cog="Cog name (e.g. moderation). Omit to reload all.")
    async def owner_reload(self, ctx: commands.Context, cog: Optional[str] = None) -> None:
        """Hot-reload one or all cogs without restarting."""
        await ctx.defer(ephemeral=True)
        if cog is None:
            reloaded: list[str] = []
            failed: list[str] = []
            for extension in list(self.bot.extensions):
                try:
                    await self.bot.reload_extension(extension)
                    reloaded.append(extension)
                except Exception as exc:
                    log.exception("Reload failed for %s", extension)
                    failed.append(f"{extension}: {type(exc).__name__}")
            description = f"Reloaded **{len(reloaded)}** extension(s)."
            if failed:
                description += "\nFailed:\n" + "\n".join(f"• `{f}`" for f in failed)
            await ctx.send(
                embed=Embeds.success("Reload complete", description)
                if not failed
                else Embeds.warning("Reload finished with errors", description)
            )
            return

        name = cog.strip().lower()
        if not name.startswith("cogs."):
            name = f"cogs.{name}"
        try:
            await self.bot.reload_extension(name)
        except Exception as exc:
            log.exception("Reload failed for %s", name)
            await ctx.send(
                embed=Embeds.error(
                    "Reload failed", f"`{name}`: {type(exc).__name__}: {truncate(str(exc), 300)}"
                )
            )
            return
        await ctx.send(embed=Embeds.success("Cog reloaded", f"`{name}` is up to date."))

    @owner.command(name="sync")
    @owner_only()
    @app_commands.describe(guild_id="Optional guild to sync instantly (dev testing)")
    async def owner_sync(
        self, ctx: commands.Context, guild_id: Optional[str] = None
    ) -> None:
        """Sync slash commands (global or to a specific guild)."""
        await ctx.defer(ephemeral=True)
        try:
            if guild_id and guild_id.strip().isdigit():
                obj = discord.Object(id=int(guild_id.strip()))
                self.bot.tree.copy_global_to(guild=obj)
                synced = await self.bot.tree.sync(guild=obj)
                await ctx.send(
                    embed=Embeds.success(
                        "Guild sync complete",
                        f"**{len(synced)}** command(s) synced to `{guild_id}`.",
                    )
                )
                return
            synced = await self.bot.tree.sync()
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Sync failed", str(exc)))
            return
        await ctx.send(
            embed=Embeds.success(
                "Global sync complete",
                f"**{len(synced)}** command(s) synced. Global updates can take up to an hour.",
            )
        )

    @owner.command(name="cookies")
    @owner_only()
    @app_commands.describe(
        file="cookies.txt exported with the “Get cookies.txt LOCALLY” browser extension",
    )
    async def owner_cookies(self, ctx: commands.Context, file: discord.Attachment) -> None:
        """Upload cookies.txt through Discord so /play works (no panel files needed)."""
        await ctx.defer(ephemeral=True)
        if file.size > 300_000:
            await ctx.send(
                embed=Embeds.error(
                    "File too big",
                    f"cookies.txt should be a few KB — got **{file.size}** bytes.",
                ),
                ephemeral=True,
            )
            return
        try:
            data = await file.read()
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Read failed", str(exc)), ephemeral=True)
            return
        text = data.decode("utf-8", errors="replace")
        if "# Netscape HTTP Cookie File" not in text and "# HTTP Cookie File" not in text:
            await ctx.send(
                embed=Embeds.error(
                    "Not a cookies file",
                    "Export the file with the **“Get cookies.txt LOCALLY”** browser extension "
                    "on youtube.com, then attach that file here.",
                ),
                ephemeral=True,
            )
            return
        dest = Path(__file__).resolve().parent.parent / "cookies.txt"
        dest.write_text(text, encoding="utf-8")
        await ctx.send(
            embed=Embeds.success(
                "cookies.txt installed ✅",
                f"Saved `{dest.name}` — `/play` will now use it on the next song.",
            ),
            ephemeral=True,
        )

    @owner.command(name="backup")
    @owner_only()
    async def owner_backup(self, ctx: commands.Context) -> None:
        """Snapshot the database right now into data/backups/ (keeps last 7)."""
        await ctx.defer(ephemeral=True)
        dest = await self.bot.db.backup()
        if dest is None:
            await ctx.send(
                embed=Embeds.error("Backup failed", "Check the console log — the snapshot could not be written."),
                ephemeral=True,
            )
            return
        await ctx.send(
            embed=Embeds.success(
                "Backup created 🗄️",
                f"Saved **{dest.name}** in `data/backups/`. "
                f"The bot also auto-snapshots every 6 hours and keeps the newest {self.bot.db.backup_keep} files.",
            ),
            ephemeral=True,
        )

    @owner.command(name="shutdown")
    @owner_only()
    async def owner_shutdown(self, ctx: commands.Context) -> None:
        """Shut the bot down cleanly (systemd/docker will restart it)."""
        await ctx.send(
            embed=Embeds.warning("Shutting down", "Closing database and disconnecting…")
        )
        log.info("Shutdown requested by owner %s", ctx.author.id)
        await self.bot.close()

    @owner.command(name="guilds")
    @owner_only()
    async def owner_guilds(self, ctx: commands.Context) -> None:
        """List every guild the bot is in."""
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        if not guilds:
            await ctx.send(embed=Embeds.info("Guilds", "Not in any guild yet."), ephemeral=True)
            return
        lines = [
            f"**{truncate(g.name, 40)}** • `{g.id}` • {g.member_count} member(s)"
            for g in guilds[:20]
        ]
        more = f"\n…and {len(guilds) - 20} more" if len(guilds) > 20 else ""
        await ctx.send(
            embed=Embeds.command_list(lines, f"🏠 Guilds ({len(guilds)})"), ephemeral=True
        )
        if more:
            await ctx.send(more, ephemeral=True)

    @owner.command(name="status")
    @owner_only()
    async def owner_status(self, ctx: commands.Context) -> None:
        """Show operational status of the bot."""
        embed = Embeds.success("Bot status", "")
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(
            name="Uptime",
            value=f"{int(time.time() - START_TIME)}s",
            inline=True,
        )
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="PID", value=f"`{_pid()}`", inline=True)
        cog_count = len(self.bot.cogs)
        ext_count = len(self.bot.extensions)
        embed.add_field(name="Cogs", value=str(cog_count), inline=True)
        embed.add_field(name="Extensions", value=str(ext_count), inline=True)
        embed.add_field(
            name="Startup issues",
            value=", ".join(self.bot.startup_errors) or "`none`",
            inline=True,
        )
        await ctx.send(embed=embed, ephemeral=True)

    @owner.command(name="say")
    @owner_only()
    @app_commands.describe(
        content="Text to send", channel="Channel to send it in (defaults to here)"
    )
    async def owner_say(
        self,
        ctx: commands.Context,
        content: str,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        """Send a message as the bot (raw text or markdown)."""
        target = channel or ctx.channel
        try:
            await target.send(
                truncate(content, 1900),
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        except discord.HTTPException as exc:
            await ctx.send(embed=Embeds.error("Failed", str(exc)), ephemeral=True)
            return
        if target != ctx.channel:
            await ctx.send(
                embed=Embeds.success("Sent", f"Message posted in {target.mention}."), ephemeral=True
            )

    @owner.command(name="broadcast")
    @owner_only()
    @app_commands.describe(content="Message to broadcast to every guild owner")
    async def owner_broadcast(self, ctx: commands.Context, content: str) -> None:
        """DM the owner of every guild the bot is in (announcements only)."""
        if not await self._confirm_broadcast(ctx, content):
            return
        sent = 0
        failed = 0
        for guild in self.bot.guilds:
            try:
                owner = guild.owner or await guild.fetch_member(guild.owner_id)
            except discord.HTTPException:
                failed += 1
                continue
            try:
                embed = Embeds.info("Message from RBX Family Manager", truncate(content, 1900))
                embed.set_footer(text=f"Server: {guild.name}")
                await owner.send(embed=embed)
                sent += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
        await ctx.send(
            embed=Embeds.success(
                "Broadcast complete",
                f"Delivered to **{sent}** owner(s); **{failed}** unavailable.",
            ),
            ephemeral=True,
        )

    async def _confirm_broadcast(self, ctx: commands.Context, content: str) -> bool:
        from utils.pagination import ConfirmView

        embed = Embeds.warning(
            "Confirm broadcast",
            f"This DMs the owner of **{len(self.bot.guilds)}** server(s):\n"
            f"> {truncate(content, 400)}",
        )
        view = ConfirmView(author_id=ctx.author.id, confirm_label="Broadcast")
        message = await ctx.send(embed=embed, view=view)
        await view.wait()
        try:
            await message.delete()
        except discord.HTTPException:
            pass
        return bool(view.value)


def _pid() -> int | str:
    try:
        import os

        return os.getpid()
    except Exception:  # pragma: no cover
        return "?"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Owner(bot))
