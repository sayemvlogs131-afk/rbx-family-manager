"""Duration parsing tests (timeout/giveaway/remind inputs)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils.time_parser import (
    MAX_TIMEOUT_SECONDS,
    DurationError,
    discord_relative,
    format_duration,
    parse_duration,
    parse_human_time,
    parse_optional_duration,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30s", 30),
        ("10m", 600),
        ("1h", 3600),
        ("7d", 604800),
        ("1w", 604800),
        ("1mo", 2592000),
        ("1y", 31536000),
        ("1h30m", 5400),
        ("2d 4h", 2 * 86400 + 4 * 3600),
        ("5 minutes", 300),
        ("  15 MIN ", 900),
    ],
)
def test_parse_duration_valid(text: str, expected: int) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   ", "abc", "10", "10x", "10m junk", "0s", "-5m", "10 m 5"],
)
def test_parse_duration_invalid(text: str) -> None:
    with pytest.raises(DurationError):
        parse_duration(text)


def test_parse_duration_respects_max_seconds() -> None:
    # Discord timeouts cap at 28 days.
    with pytest.raises(DurationError):
        parse_duration("40d", max_seconds=MAX_TIMEOUT_SECONDS)
    assert parse_duration("28d", max_seconds=MAX_TIMEOUT_SECONDS) == 28 * 86400


def test_parse_optional_duration() -> None:
    assert parse_optional_duration(None) == 0
    assert parse_optional_duration("") == 0
    assert parse_optional_duration("5m") == 300


def test_format_duration() -> None:
    assert format_duration(0) == "0s"
    assert format_duration(45) == "45s"
    assert format_duration(90) == "1m 30s"
    assert format_duration(5400) == "1h 30m"
    assert format_duration(90061) == "1d 1h 1m"


def test_parse_human_time_absolute() -> None:
    parsed = parse_human_time("2030-01-01 18:00")
    assert parsed == datetime(2030, 1, 1, 18, 0, tzinfo=timezone.utc)


def test_parse_human_time_relative_never_in_past() -> None:
    parsed = parse_human_time("00:01")
    assert parsed > datetime.now(timezone.utc) - timedelta(minutes=1)


def test_parse_human_time_invalid() -> None:
    with pytest.raises(DurationError):
        parse_human_time("not a time")


def test_discord_relative_format() -> None:
    stamp = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert discord_relative(stamp) == "<t:1893456000:R>"
