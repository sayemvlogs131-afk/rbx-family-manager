"""Web routes for the RBX Family Manager admin app.

Mounted onto the bot's existing aiohttp health server so the dashboard shares
the same port as ``/health``. The app is owned start-to-finish by the bot
process, so it goes down cleanly with the bot and never runs as a separate
service.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import Depends as Dependency, FastAPI, HTTPException, Request, responses

import config
from config import BOT_NAME
from cogs.security import parse_updates

log = logging.getLogger("rbx.web")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
TOKEN_FILE = Path(__file__).resolve().parent.parent / "data" / "web_admin_token.txt"


def _ensure_admin_token() -> str:
    """Return the admin dashboard bearer token.

    Source of truth is the ``WEB_ADMIN_TOKEN`` environment variable. When it is
    not set, a cryptographically random token is generated once and persisted
    (file permissions pointedly not world-readable on POSIX). Without a valid
    token the dashboard refuses every request — it is never public.
    """
    import os

    env_token = os.environ.get("WEB_ADMIN_TOKEN", "").strip()
    if env_token:
        return env_token
    if TOKEN_FILE.exists():
        try:
            stored = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        except OSError:
            pass
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(32)
        TOKEN_FILE.write_text(generated, encoding="utf-8")
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
        log.warning(
            "WEB_ADMIN_TOKEN unset — generated dashboard token at %s. "
            "Requests without it get HTTP 401.",
            TOKEN_FILE,
        )
        return generated
    except OSError:  # pragma: no cover - unwritable data dir
        log.exception("Could not persist a dashboard token; denying dashboard access")
        return ""


async def require_admin_token(request: Request) -> None:
    """FastAPI dependency: Authorization: Bearer <token> (or ?token=)."""
    expected = _ensure_admin_token()
    header = request.headers.get("authorization", "")
    supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not supplied:
        supplied = str(request.query_params.get("token", ""))
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Unauthorized — dashboard is token-protected")

app = FastAPI(
    title=BOT_NAME + " admin",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health", response_model=None)
async def health_proxy(request: Request) -> responses.HTMLResponse | responses.JSONResponse:
    """Forward ``/health`` to the bot's own health handler and return JSON.

    This endpoint is meant to be mounted under the bot's aiohttp site, so
    ``request`` is an aiohttp request object wrapped by FastAPI. We forward it
    to the already-existing ``bot._health_handler``.
    """
    bot = getattr(request.app.state, "bot", None)
    if bot is None or not callable(getattr(bot, "_health_handler", None)):
        return responses.JSONResponse(
            {"status": "unavailable", "error": "no bot health handler mounted"},
            status_code=503,
        )

    aiohttp_req = request.scope.get("aiohttp.request")
    if aiohttp_req is None:
        return responses.JSONResponse(
            {"status": "unavailable", "error": "no aiohttp request available"},
            status_code=503,
        )

    resp = await bot._health_handler(aiohttp_req)
    return responses.JSONResponse(
        content=resp.body,
        status_code=resp.status,
        headers=dict(resp.headers),
    )


@app.get("/api/servers", response_model=None, dependencies=[Dependency(require_admin_token)])
async def server_list(request: Request) -> responses.JSONResponse:
    """One card per guild the bot is currently in, plus a few cached stats."""
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        return responses.JSONResponse(
            {"error": "no bot mounted"}, status_code=503
        )

    try:
        servers = await build_server_cards(bot)
    except Exception:
        log.exception("Failed to build server list for web app")
        return responses.JSONResponse(
            {"error": "failed to load servers"}, status_code=500
        )

    return responses.JSONResponse(
        {
            "bot": BOT_NAME,
            "servers": servers,
            "updated_at": round(bot.latency * 1000) if bot.latency and bot.latency > 0 else None,
        }
    )


@app.get("/api/overview", response_model=None, dependencies=[Dependency(require_admin_token)])
async def overview(request: Request) -> responses.JSONResponse:
    """Live bot health overview: status mode, uptime, version and failures."""
    bot = getattr(request.app.state, "bot", None)
    if bot is None:
        return responses.JSONResponse({"error": "no bot mounted"}, status_code=503)

    mode = "online"
    mode_message = ""
    try:
        mode = str(await bot.db.get_stat(0, "bot_status_mode", "online") or "online")
        mode_message = str(await bot.db.get_stat(0, "bot_status_message", "") or "")
    except Exception:
        log.debug("Could not read status mode", exc_info=True)

    latency = getattr(bot, "latency", float("nan"))
    latency_ms = round(latency * 1000) if latency and latency > 0 else None
    return responses.JSONResponse(
        {
            "bot": BOT_NAME,
            "version": config.BOT_VERSION,
            "status": mode,
            "statusMessage": mode_message,
            "guilds": len(bot.guilds),
            "latencyMs": latency_ms,
            "cogs": [c.__class__.__name__ for c in bot.cogs.values()],
            "startupErrors": list(getattr(bot, "startup_errors", [])),
            "updated_at": latency_ms,
        }
    )


@app.get("/api/changelog", response_model=None, dependencies=[Dependency(require_admin_token)])
async def changelog(request: Request) -> responses.JSONResponse:
    """The newest release notes, same source as /changelog and update announcements."""
    release = parse_updates(config.UPDATES_PATH)
    if not release.version:
        return responses.JSONResponse({"note": "No release notes found."})
    return responses.JSONResponse(
        {
            "version": release.version,
            "title": release.title,
            "sections": release.sections,
        }
    )


@app.get("/api/security", response_model=None, dependencies=[Dependency(require_admin_token)])
async def security_feed(request: Request) -> responses.JSONResponse:
    """Anti-nuke settings + the latest audit log events for one guild."""
    bot = getattr(request.app.state, "bot", None)
    guild_id = str(request.query_params.get("guild_id", "")).strip()
    if bot is None:
        return responses.JSONResponse({"error": "no bot mounted"}, status_code=503)
    if not guild_id.isdigit() or bot.get_guild(int(guild_id)) is None:
        return responses.JSONResponse({"error": "unknown guild"}, status_code=404)
    try:
        settings = await bot.db.get_security(int(guild_id))
        events = await bot.db.recent_security_events(int(guild_id), limit=25)
    except Exception:
        log.exception("Could not load security feed for guild %s", guild_id)
        return responses.JSONResponse({"error": "load failed"}, status_code=500)
    return responses.JSONResponse(
        {"guildId": guild_id, "settings": settings, "events": events}
    )


@app.get("/", response_model=None, dependencies=[Dependency(require_admin_token)])
async def index() -> responses.HTMLResponse:
    """Serve the admin dashboard frontend."""
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        return responses.HTMLResponse(
            "<html><body><h1>RBX Family Manager Admin</h1><p>Frontend not found.</p></body></html>",
            status_code=503,
        )
    return responses.HTMLResponse(content=index_path.read_text(encoding="utf-8"))


async def build_server_cards(bot) -> list[dict]:
    """Build the per-server cards shown on the admin dashboard."""
    out: list[dict] = []
    tickets_cog = bot.get_cog("Tickets")
    for guild in bot.guilds:
        try:
            settings = await bot.db.get_guild(guild.id)
        except Exception:
            log.debug("Could not load settings for guild %s", guild.id, exc_info=True)
            settings = {}

        staff_role_id = int(settings.get("staff_role_id") or 0)
        staff_role = _role_by_id(guild, staff_role_id)

        mod_log_id = int(settings.get("mod_log_channel") or 0)
        mod_log = _channel_by_id(guild, mod_log_id)

        welcome_id = int(settings.get("welcome_channel") or 0)
        welcome = _channel_by_id(guild, welcome_id)

        auto_role_id = int(settings.get("autorole_id") or 0)
        auto_role = _role_by_id(guild, auto_role_id)

        ticket_setup = await _ticket_setup_state(tickets_cog, guild)

        open_count = 0
        if tickets_cog is not None:
            try:
                counts = await tickets_cog.bot.db.ticket_counts(guild.id)
                open_count = counts.get("open", 0)
            except Exception:
                log.debug(
                    "Could not load ticket counts for guild %s", guild.id, exc_info=True
                )

        ticket_staff_role_id = int(settings.get("ticket_staff_role_id") or 0)
        ticket_staff_role = _role_by_id(guild, ticket_staff_role_id)

        out.append(
            {
                "id": guild.id,
                "name": guild.name,
                "memberCount": guild.member_count,
                "channelCount": len(guild.channels),
                "roleCount": len(guild.roles),
                "owner": _format_owner(guild.owner),
                "prefix": settings.get("prefix", "!"),
                "staffRole": _role_summary(staff_role),
                "modLogChannel": _channel_summary(mod_log),
                "welcomeChannel": _channel_summary(welcome),
                "autoRole": _role_summary(auto_role),
                "ticketStaffRole": _role_summary(ticket_staff_role),
                "ticketSystem": bool(ticket_setup.get("panel")),
                "openTickets": open_count if open_count else 0,
                "setupStatus": _setup_status(settings, staff_role_id, mod_log_id, welcome_id),
            }
        )
    return out


def _role_by_id(guild, role_id: int):
    if role_id and guild:
        return guild.get_role(role_id)
    return None


def _channel_by_id(guild, channel_id: int):
    if channel_id and guild:
        return guild.get_channel(channel_id)
    return None


def _role_summary(role) -> dict | None:
    if role is None:
        return None
    return {"id": role.id, "name": role.name}


def _channel_summary(channel) -> dict | None:
    if channel is None:
        return None
    name = getattr(channel, "name", None)
    return {"id": channel.id, "name": name}


def _format_owner(owner) -> str:
    if owner is None:
        return "unknown"
    return str(getattr(owner, "name", None) or "unknown")


async def _ticket_setup_state(tickets_cog, guild) -> dict:
    if tickets_cog is None:
        return {"panel": False}

    try:
        pc_id = await tickets_cog.bot.db.get_stat(guild.id, "ticket_panel_channel")
        pm_id = await tickets_cog.bot.db.get_stat(guild.id, "ticket_panel_message")
        panel = bool(pc_id and pm_id and str(pc_id) != "0" and str(pm_id) != "0")
    except Exception:
        log.debug("Could not read ticket panel state for guild %s", guild.id, exc_info=True)
        panel = False

    return {"panel": panel}


def _setup_status(settings: dict, staff_role_id: int, mod_log_id: int, welcome_id: int) -> str:
    parts = []
    if staff_role_id:
        parts.append("staff")
    else:
        parts.append("no staff role")
    if mod_log_id:
        parts.append("mod logs")
    else:
        parts.append("no mod log")
    if welcome_id:
        parts.append("welcome")
    else:
        parts.append("no welcome")
    if len(parts) == 0:
        return "not configured"
    if all(p.startswith("no ") for p in parts):
        return "needs setup"
    return "partial: " + ", ".join(parts)
