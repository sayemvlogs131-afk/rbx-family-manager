"""Tests for shared helpers: calculator, emoji counting, truncation, IDs."""

from __future__ import annotations

import pytest

from utils.helpers import (
    clamp,
    count_emojis,
    human_join,
    ordinal,
    parse_bool,
    parse_id_list,
    plural,
    safe_calc,
    sanitize,
    truncate,
)


# ------------------------------------------------------------------ safe_calc
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1+1", "2"),
        ("2 + 3 * 4", "14"),
        ("(2+3)*4", "20"),
        ("10/4", "2.5"),
        ("10//3", "3"),
        ("10%3", "1"),
        ("2**10", "1024"),
        ("-5+3", "-2"),
        ("sqrt(144)", "12"),
        ("sqrt(2)", "1.414213562"),
    ],
)
def test_safe_calc_valid(expression: str, expected: str) -> None:
    assert safe_calc(expression) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "hello",
        "1/0",
        "__import__('os')",
        "open('/etc/passwd')",
        "1 +",
        "(1",
        "1 ? 2",
        "exec('print(1)')",
        "sqrt(1, 2)",
    ],
)
def test_safe_calc_rejects_dangerous_input(expression: str) -> None:
    assert safe_calc(expression) is None


# --------------------------------------------------------------- emoji counts
def test_count_emojis_custom() -> None:
    assert count_emojis("<:blob:1234567890>") == 1
    assert count_emojis("<a:dance:1234567890><a:dance:1234567890>") == 2


def test_count_emojis_unicode() -> None:
    assert count_emojis("🎉🎉🎉") == 3
    assert count_emojis("hi 🎉 there") == 1


def test_count_emojis_ignores_plain_text() -> None:
    assert count_emojis("hello world") == 0
    assert count_emojis("こんにちは世界") == 0


# ------------------------------------------------------------------- text ops
def test_truncate() -> None:
    assert truncate("short", 10) == "short"
    assert len(truncate("x" * 500, 100)) == 100
    assert truncate("x" * 500, 100).endswith("…")


def test_sanitize_strips_control_chars() -> None:
    assert "\x00" not in sanitize("a\x00b")
    assert sanitize("abc", 2) == "a…"


def test_parse_bool() -> None:
    assert parse_bool("yes") is True
    assert parse_bool("off") is False
    assert parse_bool(True) is True
    assert parse_bool(None, default=True) is True


def test_parse_id_list() -> None:
    assert parse_id_list(["123", "<@456>", "<@&789>", "not-an-id", 999]) == [123, 456, 789, 999]


def test_small_helpers() -> None:
    assert clamp(15, 0, 10) == 10
    assert plural(1, "warning") == "1 warning"
    assert plural(3, "warning") == "3 warnings"
    assert ordinal(1) == "1st"
    assert ordinal(22) == "22nd"
    assert ordinal(13) == "13th"
    assert human_join(["a"]) == "a"
    assert human_join(["a", "b", "c"]) == "a, b and c"
