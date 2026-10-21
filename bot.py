"""RBX Family Manager - bot entry point.

Run with::

    python bot.py

Responsibilities handled here:

* logging to console **and** file
* database bootstrap
* cog loading
* slash-command syncing (global or ``DEV_GUILD_ID``)
* global error handling (never leak stack traces to users)
* graceful startup/shutdown
* optional ``GET /health`` endpoint for uptime monitors
* optional admin webapp mounted onto the same health port
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import math
import sys
import traceback
from typing import Any

import discord
from discord.ext import commands

import config
from database.database import Database
from utils.embeds import Embeds

log = logging.getLogger("rbx")

# Voice libraries are never used by this bot. Silence the two discord.py voice
# warnings that otherwise litter every startup in data/rbx_family.log.
logging.getLogger("discord.client").setLevel(logging.CRITICAL)
logging.getLogger("discord.http").setLevel(logging.CRITICAL)

COGS: tuple[str, ...] = (
    "cogs.moderation",
    "cogs.automod",
    "cogs.logging",
    "cogs.giveaways",
    "cogs.fun",
    "cogs.utility",
    "cogs.server",
    "cogs.welcome",
    "cogs.tickets",
    "cogs.owner",
    "cogs.autoreply",
    "cogs.security",
)

INTENTS = discord.Intents.none()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.messages = True
INTENTS.message_content = True
INTENTS.reactions = True
INTENTS.voice_states = True


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
def setup_logging() -> None:
    config.ensure_dirs()
    config.warn_env_permissions()  # non-fatal credential hygiene (POSIX hosts)
    level = getattr(logging, config.LOG_LEVEL, logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            config.LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - unwritable log path
        log.warning("Could not open log file %s; console logging only.", config.LOG_FILE)

    # Quiet noisy libraries.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# Bot
# --------------------------------------------------------------------------
class RBXFamilyManager(commands.Bot):
    """The RBX Family Manager bot."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,  # slash-first; mention fallback
            intents=INTENTS,
            help_command=None,  # custom /help in cogs.utility
            allowed_mentions=discord.AllowedMentions(
                users=True, roles=False, everyone=False, replied_user=False
            ),
        )
        self.db = Database(config.DATABASE_PATH)
        self.owner_ids = frozenset(config.OWNER_IDS)
        self.session_ready = asyncio.Event()
        self._health_runner: Any = None
        self._health_site: Any = None
        self.startup_errors: list[str] = []

    # ------------------------------------------------------------- lifecycle
    async def setup_hook(self) -> None:
        await self.db.connect()

        # Global checks: ignored channels + maintenance mode.
        from utils.checks import maintenance_mode, not_ignored

        self.add_check(not_ignored().predicate)
        self.add_check(maintenance_mode().predicate)

        for extension in COGS:
            try:
                await self.load_extension(extension)
            except Exception:
                log.exception("Failed to load cog %s", extension)
                self.startup_errors.append(extension)

        if config.SYNC_COMMANDS:
            await self.sync_commands()
        else:
            log.info("SYNC_COMMANDS=false — skipping slash command sync.")

        if config.HEALTH_ENABLED:
            await self._start_health_server()
            await self._mount_webapp()

    async def sync_commands(self) -> None:
        """Sync slash commands globally, and instantly to DEV_GUILD_ID if set."""
        try:
            if config.DEV_GUILD_ID:
                dev_guild = discord.Object(id=config.DEV_GUILD_ID)
                self.tree.copy_global_to(guild=dev_guild)
                synced_dev = await self.tree.sync(guild=dev_guild)
                log.info(
                    "Synced %s command(s) to dev guild %s.",
                    len(synced_dev),
                    config.DEV_GUILD_ID,
                )
            synced = await self.tree.sync()
            log.info("Synced %s global command(s).", len(synced))
        except discord.HTTPException:
            log.exception("Slash command sync failed")

    async def on_ready(self) -> None:
        self.session_ready.set()
        log.info(
            "Logged in as %s (%s) | guilds=%s | latency=%sms",
            self.user,
            self.user and self.user.id,
            len(self.guilds),
            round(self.latency * 1000),
        )
        await self._recover_after_restart()
        await self._set_presence()
        if self.startup_errors:
            await self._alert_owner_startup_problems()
            log.warning("Cogs that failed to load: %s", ", ".join(self.startup_errors))

    async def _recover_after_restart(self) -> None:
        """Best-effort reconciliation after a crash/restart.

        Persistence (buttons, DB) survives restarts, but in-memory refs can
        drift — e.g. a ticket panel message recorded in the DB may have been
        deleted while the bot was down. Repair what we can without disturbing
        live tickets.
        """
        tickets_cog = self.get_cog("Tickets")
        if tickets_cog is None:
            return
        recovered: list[str] = []
        for guild in self.guilds:
            try:
                fixes = await tickets_cog.recover_server(guild)
            except Exception:
                log.debug("Ticket recovery skipped for guild %s", guild.id, exc_info=True)
                continue
            if fixes:
                recovered.append(f"guild {guild.id} ({guild.name}): " + "; ".join(fixes))
        if recovered:
            log.info("Ticket recovery after restart: %s", "; ".join(recovered))

    async def on_resumed(self) -> None:
        await self._set_presence()

    async def _set_presence(self) -> None:
        """Presence mirrors the operating mode set with `/botstatus set`.

        online -> green "Watching" status; maintenance -> do-not-disturb
        "maintenance"; issues -> idle "degraded". Falls back to the normal
        watching status when the database has not been connected yet.
        """
        mode = "online"
        try:
            mode = str(await self.db.get_stat(0, "bot_status_mode", "online") or "online")
        except Exception:
            mode = "online"
        if mode == "maintenance":
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name="🛠️ Bot under maintenance — /botstatus",
            )
            status = discord.Status.dnd
        elif mode == "issues":
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name="⚠️ Experiencing issues — /botstatus",
            )
            status = discord.Status.idle
        else:
            activity = discord.Activity(
                type=discord.ActivityType.watching, name=config.DEFAULT_STATUS_TEXT
            )
            status = discord.Status.online
        try:
            await self.change_presence(activity=activity, status=status)
        except discord.HTTPException:  # pragma: no cover - transient API failure
            log.debug("Could not update presence", exc_info=True)

    async def notify_bot_owners(self, embed: discord.Embed) -> None:
        """DM every configured bot owner about a problem (best effort)."""
        for owner_id in config.OWNER_IDS:
            user = self.get_user(owner_id) or (
                await self.fetch_user(owner_id) if not self.is_closed() else None
            )
            if user is None:
                continue
            try:
                await user.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                log.debug("Could not DM owner %s", owner_id, exc_info=True)

    async def _alert_owner_startup_problems(self) -> None:
        if not self.startup_errors:
            return
        problems = ", ".join(self.startup_errors)
        lines = "\n".join(f"• `{ext}` failed to load" for ext in self.startup_errors)
        embed = discord.Embed(
            title="🚨 Bot started with problems",
            description=(
                f"**{config.BOT_NAME}** is online, but some components failed:\n{lines}\n\n"
                'Fix or rename the files and use `/owner reload cog:<name>`.'
            ),
            color=config.ERROR_COLOR,
        )
        embed.set_footer(text=config.FOOTER_TEXT)
        await self.notify_bot_owners(embed)
        log.warning("Startup problems: %s", problems)

    async def close(self) -> None:
        log.info("Shutting down gracefully...")
        await self._stop_health_server()
        try:
            await self.db.close()
        except Exception:  # pragma: no cover - defensive
            log.exception("Error closing database")
        await super().close()
        log.info("Shutdown complete.")

    # ---------------------------------------------------------- web app
    async def _mount_webapp(self) -> None:
        """Mount the admin dashboard onto the bot's existing aiohttp site.

        The dashboard shares port ``HEALTH_PORT`` with ``/health`` so there is
        one public HTTP surface for both monitoring and admin views.
        """
        if self._health_runner is None or self._health_site is None:
            log.warning("Web app disabled: health server is not running.")
            return
        try:
            from aiohttp import web as _web
        except Exception:  # pragma: no cover - only in stripped test envs
            _web = None

        if _web is None:
            return

        try:
            from webapp.routes import app as fastapp
            from webapp.adapters import fastapi_to_aiohttp
        except Exception:
            log.exception("Failed to import webapp; admin dashboard will be unavailable.")
            return

        # Give FastAPI access to the live bot instance.
        fastapp.state.bot = self

        # Mount the FastAPI dashboard onto the existing health app so both
        # share the same TCPSite/port instead of trying to bind a second
        # listener on ``HEALTH_PORT``.
        wrapper = _web.Application()
        wrapper["bot"] = self

        async def forward(request):
            """Route request into FastAPI and return an aiohttp Response."""
            return await fastapi_to_aiohttp(request, fastapp)

        # Replace the health app's router with a combined one: keep the
        # existing /health handler and add the admin dashboard routes.
        combined = _web.Application()
        combined["bot"] = self
        combined.router.add_get("/health", self._health_handler)
        combined.router.add_get("/", forward)
        for api_path in (
            "/api/servers",
            "/api/overview",
            "/api/changelog",
            "/api/security",
        ):
            combined.router.add_get(api_path, forward)

        # Re-create the runner/site on top of the combined app so both the
        # health endpoint and the dashboard share one TCP binding.
        if self._health_runner is not None:
            try:
                await self._health_runner.cleanup()
            except Exception:
                log.debug("Health runner cleanup before webapp mount", exc_info=True)

        self._health_runner = _web.AppRunner(combined)
        await self._health_runner.setup()
        self._health_site = _web.TCPSite(
            self._health_runner, "0.0.0.0", config.HEALTH_PORT
        )
        await self._health_site.start()
        self._webapp_runner = self._health_runner
        log.info("Admin dashboard listening on :%s/", config.HEALTH_PORT)

    # ---------------------------------------------------------- health server
    async def _start_health_server(self) -> None:
        try:
            from aiohttp import web as _web
        except Exception:  # pragma: no cover - only in stripped test envs
            _web = None

        if _web is None:
            try:
                from utils.aiohttp_health_shim import (
                    Application,
                    AppRunner,
                    TCPSite,
                )
            except Exception:
                log.warning("No aiohttp available and shim missing; skipping /health.")
                self._health_runner = None
                return
            app = Application()
            app.router.add_get("/health", self._health_handler)
            try:
                self._health_runner = AppRunner(app)
                await self._health_runner.setup()
                self._health_site = TCPSite(
                    self._health_runner, "0.0.0.0", config.HEALTH_PORT
                )
                await self._health_site.start()
                log.info("Health endpoint listening on :%s/health", config.HEALTH_PORT)
            except OSError:
                log.exception(
                    "Could not bind health endpoint on port %s (continuing without it)",
                    config.HEALTH_PORT,
                )
                self._health_runner = None
            return

        app = _web.Application()
        app.router.add_get("/health", self._health_handler)
        try:
            self._health_runner = _web.AppRunner(app)
            await self._health_runner.setup()
            self._health_site = _web.TCPSite(
                self._health_runner, "0.0.0.0", config.HEALTH_PORT
            )
            await self._health_site.start()
            log.info("Health endpoint listening on :%s/health", config.HEALTH_PORT)
        except OSError:
            log.exception(
                "Could not bind health endpoint on port %s (continuing without it)",
                config.HEALTH_PORT,
            )
            self._health_runner = None

    async def _stop_health_server(self) -> None:
        if self._health_runner is not None:
            try:
                await self._health_runner.cleanup()
            except Exception:  # pragma: no cover - defensive
                log.debug("Health server cleanup failed", exc_info=True)
            self._health_runner = None

    async def _health_handler(self, _request) -> Any:
        from aiohttp import web as _web
        latency = self.latency
        # ``latency`` is NaN before the gateway connects; never let /health 500.
        latency_ms = round(latency * 1000) if math.isfinite(latency) else -1
        payload = {
            "status": "online" if not self.is_closed() else "offline",
            "bot": config.BOT_NAME,
            "guilds": len(self.guilds),
            "latency_ms": latency_ms,
        }
        return _web.json_response(payload)

    # -------------------------------------------------------------- utilities

    async def log_to_channel(
        self, guild: discord.Guild, key: str, embed: discord.Embed
    ) -> None:
        """Send ``embed`` to the configured channel for ``key``.

        ``key`` is one of: mod_log_channel, member_log_channel,
        server_log_channel, message_log_channel.
        """
        if guild is None:
            return
        try:
            settings = await self.db.get_guild(guild.id)
            channel_id = settings.get(key)
            if not channel_id:
                return
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                channel = await guild.fetch_channel(int(channel_id))
            if not isinstance(channel, discord.abc.Messageable):
                return
            await channel.send(embed=embed)
        except discord.Forbidden:
            log.debug("Missing access to %s in guild %s", key, guild.id)
        except discord.HTTPException:
            log.debug("Failed to send %s log in guild %s", key, guild.id, exc_info=True)
        except Exception:  # pragma: no cover - defensive
            log.exception("Unexpected error sending %s log", key)


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------
def _brief_error(exc: Exception) -> str:
    """User-facing message for an exception (never a stack trace)."""
    if isinstance(exc, commands.MissingPermissions):
        missing = ", ".join(p.replace("_", " ").title() for p in exc.missing_permissions)
        return f"You are missing the required permissions: **{missing}**."
    if isinstance(exc, discord.Forbidden):
        return (
            "I don't have permission to do that. Check my role position and "
            "channel permissions."
        )
    if isinstance(exc, discord.NotFound):
        return "I couldn't find that target — it may have been deleted."
    if isinstance(exc, discord.HTTPException):
        return f"Discord rejected that request ({exc.status}). Please try again later."
    if isinstance(exc, commands.BotMissingAnyRole):
        return "I am missing a required role for this command."
    if isinstance(exc, commands.BotMissingPermissions):
        missing = ", ".join(p.replace("_", " ").title() for p in exc.missing_permissions)
        return f"I am missing permissions: **{missing}**."
    if isinstance(exc, commands.MissingRole):
        return "You are missing the required role for this command."
    if isinstance(exc, commands.MemberNotFound):
        return "I couldn't find that member."
    if isinstance(exc, commands.RoleNotFound):
        return "I couldn't find that role."
    if isinstance(exc, commands.ChannelNotFound):
        return "I couldn't find that channel."
    if isinstance(exc, commands.BadArgument):
        return f"Invalid argument: {exc}"
    if isinstance(exc, commands.MissingRequiredArgument):
        return f"Missing required argument: `{exc.param.name}`."
    if isinstance(exc, commands.CommandOnCooldown):
        return f"Slow down — try again in {exc.retry_after:.1f}s."
    if isinstance(exc, commands.NoPrivateMessage):
        return "This command can only be used inside a server."
    if isinstance(exc, commands.NotOwner):
        return "Only the bot owner can use this command."
    from utils.checks import MissingOwnerPermissions, MaintenanceMode
    if isinstance(exc, (MaintenanceMode, MissingOwnerPermissions)):
        return str(exc) or "Only the server owner or the bot owner can use this."
    if isinstance(exc, commands.DisabledCommand):
        return "That command is currently disabled."
    return "Something went wrong while running that command. The error has been logged."


async def _send_error(
    destination: Any, title: str, message: str, *, ephemeral: bool = True
) -> None:
    embed = Embeds.error(title, message)
    try:
        if isinstance(destination, discord.Interaction):
            if destination.response.is_done():
                await destination.followup.send(embed=embed, ephemeral=ephemeral)
            else:
                await destination.response.send_message(embed=embed, ephemeral=ephemeral)
        else:
            await destination.send(embed=embed)
    except discord.HTTPException:  # pragma: no cover - best effort
        log.debug("Could not deliver error message", exc_info=True)


async def on_app_command_error(
    interaction: discord.Interaction, error: Exception
) -> None:
    """Global application-command error handler."""
    original = getattr(error, "original", None) or error
    log.error(
        "App command %s failed in guild %s",
        interaction.command and interaction.command.qualified_name,
        interaction.guild and interaction.guild.id,
        exc_info=original if not isinstance(original, commands.CheckFailure) else None,
    )
    bot = interaction.client
    if (
        not isinstance(original, commands.CheckFailure)
        and isinstance(bot, RBXFamilyManager)
        and interaction.user.id not in config.OWNER_IDS
    ):
        # Unexpected failures: quietly tell the bot owner in DM.
        try:
            embed = discord.Embed(
                title="⚠️ A command failed",
                description=(
                    f"`/{interaction.command.qualified_name if interaction.command else '?'}` "
                    f"ran by `{interaction.user}` failed with: "
                    f"`{type(original).__name__}: {original}`"
                )[:1900],
                color=config.WARNING_COLOR,
            )
            await bot.notify_bot_owners(embed)
        except Exception:  # pragma: no cover - best-effort alert
            log.debug("Could not notify owners of command failure", exc_info=True)
    if isinstance(original, commands.CheckFailure):
        message = str(original) or "You do not have permission to use this command."
        await _send_error(interaction, "Not allowed", message)
        return
    await _send_error(interaction, "Command failed", _brief_error(original))


async def handle_command_error(ctx: commands.Context[Any], error: Exception) -> None:
    """Global prefix-command error handler (mention-prefix fallback)."""
    original = getattr(error, "original", None) or error
    if isinstance(original, commands.CommandNotFound):
        return
    log.error(
        "Command %s failed", ctx.command and ctx.command.qualified_name, exc_info=original
    )
    await _send_error(ctx, "Command failed", _brief_error(original))


# --------------------------------------------------------------------------
# Bootstrap
# --------------------------------------------------------------------------
async def init_database() -> None:
    """Create/upgrade the SQLite database and exit (used by install scripts)."""
    config.ensure_dirs()
    db = Database(config.DATABASE_PATH)
    await db.connect()
    tables = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    version = await db.scalar("SELECT MAX(version) FROM schema_migrations", default=0)
    await db.close()
    log.info(
        "Database initialised at %s (schema v%s, %s tables).",
        config.DATABASE_PATH,
        version,
        len(tables),
    )
    print(f"Database ready: {config.DATABASE_PATH} (schema v{version})")


def main() -> None:
    setup_logging()

    if "--init-db" in sys.argv:
        asyncio.run(init_database())
        return

    problems = config.validate()
    if problems:
        for problem in problems:
            log.error("Configuration error: %s", problem)
        config.fatal("Fix the configuration errors above and restart.")

    bot = RBXFamilyManager()
    bot.tree.on_error = on_app_command_error  # type: ignore[method-assign]
    bot.on_command_error = handle_command_error  # type: ignore[method-assign]

    try:
        bot.run(config.DISCORD_TOKEN, log_handler=None, log_formatter=None)
    except KeyboardInterrupt:  # pragma: no cover
        log.info("Interrupted by user.")
    except discord.LoginFailure:  # pragma: no cover - bad token
        log.error("Invalid DISCORD_TOKEN - check your .env file.")
        sys.exit(1)
    except Exception:  # pragma: no cover
        log.critical("Fatal error during startup:\n%s", traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
