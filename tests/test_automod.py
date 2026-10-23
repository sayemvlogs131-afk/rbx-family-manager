"""Pure-function tests for the automod engine."""

from __future__ import annotations

import pytest

from cogs.automod import DEFAULT_SETTINGS, UserTracker, evaluate_automod


def cfg(**overrides):
    merged = dict(DEFAULT_SETTINGS)
    merged.update(overrides)
    return merged


def tracker() -> UserTracker:
    return UserTracker()


# ---------------------------------------------------------------- master switch
def test_disabled_master_switch_returns_none() -> None:
    assert evaluate_automod(cfg(enabled=0), "discord.gg/spam", tracker()) is None


# --------------------------------------------------------------- banned words
def test_banned_word_detected_case_insensitively() -> None:
    violation = evaluate_automod(cfg(banned_words=["badword"]), "You are BADWORD", tracker())
    assert violation is not None
    assert violation.rule == "banned_word"


def test_clean_message_passes_banned_words() -> None:
    assert evaluate_automod(cfg(banned_words=["badword"]), "nice message", tracker()) is None


# ------------------------------------------------------------------- invites
def test_invite_filter() -> None:
    violation = evaluate_automod(cfg(), "join discord.gg/abcdef", tracker())
    assert violation is not None and violation.rule == "invite"
    assert evaluate_automod(cfg(invite_filter=0), "discord.gg/abc", tracker()) is None


def test_plain_urls_allowed_by_default() -> None:
    assert evaluate_automod(cfg(), "check https://example.com", tracker()) is None


# ------------------------------------------------------------------ mentions
def test_mention_spam() -> None:
    content = " ".join(f"<@&{i}>" for i in range(10))
    violation = evaluate_automod(cfg(mention_limit=6), content, tracker())
    assert violation is not None and violation.rule == "mentions"


def test_few_mentions_allowed() -> None:
    content = "<@1> <@2> <@3> hello"
    assert evaluate_automod(cfg(mention_limit=6), content, tracker()) is None


# ---------------------------------------------------------------------- caps
def test_excessive_caps_flagged() -> None:
    violation = evaluate_automod(cfg(), "THIS IS AN ALL CAPS MESSAGE AND IT IS LOUD", tracker())
    assert violation is not None and violation.rule == "caps"


def test_normal_sentence_not_flagged() -> None:
    assert evaluate_automod(cfg(), "Hey everyone, how are you doing today?", tracker()) is None


def test_short_caps_allowed() -> None:
    assert evaluate_automod(cfg(), "OMG", tracker()) is None


# --------------------------------------------------------------------- emoji
def test_excessive_emoji_flagged() -> None:
    text = "🎉" * 20 + " party"
    violation = evaluate_automod(cfg(emoji_limit=12), text, tracker())
    assert violation is not None and violation.rule == "emoji"


def test_cjk_text_is_not_treated_as_emoji() -> None:
    japanese = "こんにちは世界" * 10
    assert evaluate_automod(cfg(emoji_limit=5), japanese, tracker()) is None


# ------------------------------------------------------------------- duplicate
def test_duplicate_messages_flagged_in_window() -> None:
    user = tracker()
    assert evaluate_automod(cfg(), "buy my stuff", user, now=100.0) is None
    violation = evaluate_automod(cfg(), "buy my stuff", user, now=102.0)
    assert violation is not None and violation.rule == "duplicate"


def test_duplicates_outside_window_allowed() -> None:
    user = tracker()
    evaluate_automod(cfg(), "hello there", user, now=100.0)
    assert evaluate_automod(cfg(), "hello there", user, now=200.0) is None


# ----------------------------------------------------------------------- spam
def test_flood_detected() -> None:
    user = tracker()
    hit = None
    for i in range(12):
        hit = evaluate_automod(cfg(), f"message {i}", user, now=i * 0.4)
        if hit:
            break
    assert hit is not None and hit.rule == "spam"


def test_normal_pace_is_allowed() -> None:
    user = tracker()
    for i in range(8):
        assert evaluate_automod(cfg(), f"message {i}", user, now=float(i * 30)) is None


# -------------------------------------------------------------------- exempt
def test_administrator_is_exempt() -> None:
    from types import SimpleNamespace

    admin = SimpleNamespace(
        roles=[SimpleNamespace(id=1)],
        guild_permissions=SimpleNamespace(administrator=True, manage_guild=False),
    )
    assert evaluate_automod(cfg(), "discord.gg/nope", tracker(), member=admin) is None


def test_exempt_role_is_exempt() -> None:
    from types import SimpleNamespace

    member = SimpleNamespace(
        roles=[SimpleNamespace(id=777)],
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )
    assert (
        evaluate_automod(
            cfg(exempt_roles=[777]), "discord.gg/nope", tracker(), member=member, channel_id=5
        )
        is None
    )


def test_exempt_channel_is_exempt() -> None:
    from types import SimpleNamespace

    member = SimpleNamespace(
        roles=[SimpleNamespace(id=1)],
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )
    assert (
        evaluate_automod(
            cfg(exempt_channels=[9]), "discord.gg/nope", tracker(), member=member, channel_id=9
        )
        is None
    )


def test_exempt_lists_as_json_strings_from_database() -> None:
    """SQLite stores exempt lists as JSON TEXT, not lists.

    Regression: iterating the raw string raised ValueError on every message.
    """
    from types import SimpleNamespace

    member = SimpleNamespace(
        roles=[SimpleNamespace(id=777)],
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )
    other = SimpleNamespace(
        roles=[SimpleNamespace(id=1)],
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )
    settings = cfg(exempt_roles='[777]', exempt_channels='[9]')

    # Exempt channel (string id list) -> no violation.
    assert (
        evaluate_automod(
            settings, "discord.gg/abc", tracker(), member=member, channel_id=9
        )
        is None
    )
    # Exempt role, non-exempt channel -> no violation.
    assert (
        evaluate_automod(
            settings, "discord.gg/abc", tracker(), member=member, channel_id=5
        )
        is None
    )
    # Non-exempt member -> still filtered.
    violation = evaluate_automod(
        settings, "discord.gg/abc", tracker(), member=other, channel_id=5
    )
    assert violation is not None and violation.rule == "invite"


def test_malformed_exempt_values_do_not_crash() -> None:
    from types import SimpleNamespace

    member = SimpleNamespace(
        roles=[SimpleNamespace(id=1)],
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )
    broken = cfg(exempt_roles="not json", exempt_channels="[", banned_words="nope")
    assert evaluate_automod(broken, "hello there", tracker(), member=member, channel_id=1) is None
    assert (
        evaluate_automod(broken, "discord.gg/abc", tracker(), member=member, channel_id=1)
        is not None
    )


def test_database_row_shape_is_accepted() -> None:
    """The real DEFAULT_AUTOMOD_ROW uses JSON strings for list columns."""
    from types import SimpleNamespace

    from database.database import DEFAULT_AUTOMOD_ROW

    row = {k: v for k, v in DEFAULT_AUTOMOD_ROW.items()}
    assert isinstance(row["exempt_channels"], str)  # documents the real shape
    member = SimpleNamespace(
        roles=[SimpleNamespace(id=1)],
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )
    assert evaluate_automod(row, "good morning", tracker(), member=member, channel_id=1) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
