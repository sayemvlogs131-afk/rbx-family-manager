"""Central configuration for RBX Family Manager.

All secrets live in the environment / ``.env`` file and are never hardcoded.
See ``.env.example`` for the full list of supported variables.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a hard dependency in production
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:  # type: ignore[misc]
        """Fallback no-op so the bot can still boot with a real environment."""
        return False

# Load .env from the project root (the directory containing this file).
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logging.warning("Config: %s is not a valid integer, using default %r", name, default)
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------
# Required secrets
# --------------------------------------------------------------------------
DISCORD_TOKEN: str = _get("DISCORD_TOKEN")
OWNER_IDS: tuple[int, ...] = tuple(
    int(part) for part in _get("OWNER_ID").replace(",", " ").split() if part.strip().isdigit()
)
# Backwards-friendly alias: OWNER_ID (singular) is the documented variable.
OWNER_ID: int | None = OWNER_IDS[0] if OWNER_IDS else None

# --------------------------------------------------------------------------
# Database / storage
# --------------------------------------------------------------------------
DATABASE_PATH: str = _get("DATABASE_PATH", str(BASE_DIR / "data" / "rbx_family.db"))
DATA_DIR: Path = Path(DATABASE_PATH).expanduser().resolve().parent

# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
DEV_GUILD_ID: int | None = _get_int("DEV_GUILD_ID", 0) or None
HEALTH_PORT: int = _get_int("HEALTH_PORT", 8080)
HEALTH_ENABLED: bool = _get_bool("HEALTH_ENABLED", True)
LOG_LEVEL: str = _get("LOG_LEVEL", "INFO").upper()
LOG_FILE: str = _get("LOG_FILE", str(BASE_DIR / "data" / "rbx_family.log"))
SYNC_COMMANDS: bool = _get_bool("SYNC_COMMANDS", True)

# --------------------------------------------------------------------------
# Branding
# --------------------------------------------------------------------------
BOT_NAME = "RBX Family Manager"
BOT_DESCRIPTION = (
    "The all-in-one management, moderation, giveaway, utility and fun bot for RBX Family."
)
BOT_VERSION = "2.4.2"
DEFAULT_STATUS_TEXT = "RBX Family | /help"
SUPPORT_SERVER: str = _get("SUPPORT_SERVER", "")
# Human-readable release notes parsed by the update-announcer (cogs.security).
UPDATES_PATH: Path = Path(_get("UPDATES_PATH", str(BASE_DIR / "UPDATES.md")))

# Consistent RBX Family visual identity.
BRAND_COLOR = 0x5865F2        # Discord blurple - primary brand color
SUCCESS_COLOR = 0x57F287      # Green
ERROR_COLOR = 0xED4245        # Red
WARNING_COLOR = 0xFEE75C      # Yellow
INFO_COLOR = 0x3498DB         # Blue
MODERATION_COLOR = 0xE67E22   # Orange
GIVEAWAY_COLOR = 0xEB459E     # Fuchsia
LOG_COLOR = 0x99AAB5          # Blurple-grey
NEUTRAL_COLOR = 0x2F3136      # Dark grey

FOOTER_TEXT = "RBX Family Manager • RBX Family"
ICON_URL = ""  # Optional: set to your bot avatar / server icon URL.

# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate() -> list[str]:
    """Return a list of fatal configuration problems (empty when healthy)."""
    problems: list[str] = []
    if not DISCORD_TOKEN:
        problems.append(
            "DISCORD_TOKEN is missing. Copy .env.example to .env and add your bot token."
        )
    if len(DISCORD_TOKEN) < 50 and DISCORD_TOKEN:
        problems.append("DISCORD_TOKEN looks invalid (too short).")
    if OWNER_ID is None:
        problems.append("OWNER_ID is missing. Add your Discord user ID to .env.")
    if HEALTH_PORT and not (1024 <= HEALTH_PORT <= 65535):
        problems.append("HEALTH_PORT must be between 1024 and 65535.")
    return problems


def ensure_dirs() -> None:
    """Create writable directories the bot depends on."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Path(LOG_FILE).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def warn_env_permissions() -> None:
    """Non-fatal credential hygiene: flag a world-readable ``.env`` (POSIX only).

    Windows mode bits do not reflect ACL reality, so the check is skipped there;
    on Linux/Docker hosts a group/world-readable ``.env`` is worth a warning
    without blocking the bot from starting.
    """
    import os
    import stat as _stat

    if sys.platform.startswith("win"):
        return
    token_path = BASE_DIR / ".env"
    try:
        mode = token_path.stat().st_mode
        if token_path.exists() and mode & (_stat.S_IRGRP | _stat.S_IROTH):
            logging.getLogger("rbx").warning(
                "%s is readable by group/others — restrict it with chmod 600.", token_path
            )
    except OSError:  # pragma: no cover - defensive
        pass


def fatal(msg: str) -> "None":
    print(f"[FATAL] {msg}", file=sys.stderr)
    raise SystemExit(1)
