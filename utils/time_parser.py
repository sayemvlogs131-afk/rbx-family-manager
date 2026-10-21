"""Duration and timestamp parsing for RBX Family Manager.

Supported human formats::

    30s  10m  2h  7d  1w  1mo  1y
    1h30m            -> 90 minutes
    1d 12h 30m       -> whitespace separated units
    2026-01-01 18:00 -> absolute UTC datetime (YYYY-MM-DD HH:MM)
    tomorrow 18:00   -> relative day + time
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
    "mo": 2592000,
    "month": 2592000,
    "months": 2592000,
    "y": 31536000,
    "year": 31536000,
    "years": 31536000,
}

_TOKEN_RE = re.compile(r"(\d+)\s*([a-zA-Z]+)")
_NUMBER_RE = re.compile(r"^\d+$")

# Discord timeouts are capped at 28 days.
MAX_TIMEOUT_SECONDS = 28 * 86400
MAX_DURATION_SECONDS = 365 * 86400


class DurationError(ValueError):
    """Raised when a duration string cannot be understood."""


def parse_duration(text: str, *, max_seconds: int | None = MAX_DURATION_SECONDS) -> int:
    """Parse a human duration into whole seconds.

    Accepts ``10m``, ``1h30m``, ``2d``, ``1w``, ``1mo``, ``1y`` and mixes.
    Bare numbers are rejected because their unit would be ambiguous.
    """
    if text is None:
        raise DurationError("Duration is required.")
    raw = str(text).strip().lower()
    if not raw:
        raise DurationError("Duration is required.")

    # Allow plain seconds only when explicitly suffixed ("30s" / "30 sec").
    matches = list(_TOKEN_RE.finditer(raw))
    if not matches:
        if _NUMBER_RE.match(raw):
            raise DurationError(
                f"`{text}` has no unit. Use formats like `30s`, `10m`, `2h`, `7d`."
            )
        raise DurationError(f"Could not understand duration `{text}`.")

    # Ensure the whole string was consumed (allow spaces between tokens).
    residue = _TOKEN_RE.sub(" ", raw).strip()
    if residue.replace(" ", ""):
        raise DurationError(f"Could not understand duration `{text}`.")

    total = 0
    for match in matches:
        amount = int(match.group(1))
        unit = match.group(2)
        seconds = _UNIT_SECONDS.get(unit)
        if seconds is None:
            raise DurationError(f"Unknown time unit `{unit}` in `{text}`.")
        total += amount * seconds

    if total <= 0:
        raise DurationError("Duration must be greater than zero.")
    if max_seconds is not None and total > max_seconds:
        raise DurationError(
            f"Duration exceeds the maximum of {format_duration(max_seconds)}."
        )
    return total


def parse_optional_duration(
    text: str | None, *, max_seconds: int | None = MAX_DURATION_SECONDS
) -> int:
    """Parse a duration that may be ``None``/empty (returns 0 when absent)."""
    if text is None or not str(text).strip():
        return 0
    return parse_duration(text, max_seconds=max_seconds)


def format_duration(seconds: int | float) -> str:
    """Render seconds as a compact human string like ``1h 30m``."""
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    units = (
        ("y", 31536000),
        ("mo", 2592000),
        ("w", 604800),
        ("d", 86400),
        ("h", 3600),
        ("m", 60),
        ("s", 1),
    )
    parts: list[str] = []
    remaining = seconds
    for suffix, size in units:
        if remaining >= size:
            count, remaining = divmod(remaining, size)
            parts.append(f"{count}{suffix}")
        if len(parts) == 3:
            break
    return " ".join(parts)


def parse_human_time(text: str) -> datetime:
    """Parse an absolute or relative time into an aware UTC datetime.

    Supported: ``2026-01-01 18:00``, ``2026-01-01T18:00``,
    ``tomorrow 18:00``, ``today 18:00``, ``18:00`` (today/tomorrow).
    """
    raw = str(text).strip()
    if not raw:
        raise DurationError("Time is required.")

    now = datetime.now(timezone.utc)
    lower = raw.lower()

    day = now
    if lower.startswith("tomorrow"):
        day = now + timedelta(days=1)
        lower = lower[len("tomorrow"):].strip(" ,")
    elif lower.startswith("today"):
        day = now
        lower = lower[len("today"):].strip(" ,")

    # ISO-ish date + optional time.
    try:
        if not lower:
            raise ValueError
        candidate = lower.replace("T", " ")
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    # Time only, e.g. "18:00" or "6:00 PM".
    time_part = lower.replace(".", ":").upper().replace(" ", "")
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M%p"):
        try:
            parsed_time = datetime.strptime(time_part, fmt).time()
            combined = datetime(
                day.year, day.month, day.day, parsed_time.hour, parsed_time.minute,
                parsed_time.second, tzinfo=timezone.utc,
            )
            if combined <= now and day is now:
                combined += timedelta(days=1)
            return combined
        except ValueError:
            continue

    raise DurationError(
        f"Could not understand time `{text}`. Try `2026-01-01 18:00` or `18:00`."
    )


def future_timestamp(seconds: int) -> datetime:
    """Aware datetime ``seconds`` from now."""
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def discord_relative(dt: datetime) -> str:
    """Discord markdown relative timestamp, e.g. ``<t:1735689600:R>``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:R>"


def discord_long(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:F>"
