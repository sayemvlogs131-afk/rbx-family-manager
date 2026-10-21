"""Shared helpers for RBX Family Manager."""

from __future__ import annotations

import re
from typing import Any, Iterable, TypeVar

T = TypeVar("T")

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.(?:gg|io|me|li)|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
MENTION_RE = re.compile(r"<@!?(?P<id>\d+)>")
ROLE_MENTION_RE = re.compile(r"<@&(?P<id>\d+)>")
CHANNEL_MENTION_RE = re.compile(r"<#(?P<id>\d+)>")
# Emoji matcher without a third-party regex module dependency.
EMOJI_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}"          # flags
    r"|[\U0001F300-\U0001FAFF]"             # pictographs
    r"|[\U00002600-\U000027BF]"             # misc symbols
    r"|<a?:\w+:\d+>"                        # custom emoji
    r"|[\U0001F000-\U0001F02F]"
)
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")

# Unicode emoji codepoint ranges (avoids counting CJK / Arabic text as "emoji").
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),   # emoticons, symbols, transport, supplemental
    (0x2600, 0x27BF),     # misc symbols + dingbats
    (0x2B00, 0x2BFF),     # misc symbols and arrows
    (0x1F1E6, 0x1F1FF),   # regional indicators (flags)
)


def count_emojis(text: str) -> int:
    """Count custom + unicode emoji in ``text`` without false positives."""
    custom = len(CUSTOM_EMOJI_RE.findall(text))
    plain = CUSTOM_EMOJI_RE.sub("", text)
    unicode_emoji = sum(
        1
        for ch in plain
        if any(low <= ord(ch) <= high for low, high in _EMOJI_RANGES)
    )
    return custom + unicode_emoji


def chunk_text(text: str, size: int = 1000) -> list[str]:
    """Split text into Discord-safe chunks, preferring line boundaries."""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks or [""]


def truncate(text: str, limit: int = 1024, suffix: str = "…") -> str:
    """Truncate to ``limit`` characters, appending a suffix when cut."""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def slugify(text: str, limit: int = 40, fallback: str = "") -> str:
    """Lowercase ASCII slug for channel names (``ticket-3-report-bug``).

    Strips everything but letters/digits, collapses runs into single dashes.
    Returns ``fallback`` when nothing usable remains.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug[:limit].rstrip("-") or fallback


def sanitize(text: str, limit: int = 500) -> str:
    """Strip control characters and clamp length for safe embed output."""
    cleaned = "".join(ch for ch in str(text) if ch.isprintable() or ch in "\n\t")
    return truncate(cleaned.strip(), limit)


def parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}


def parse_id_list(values: Iterable[Any] | None) -> list[int]:
    """Convert an iterable of ids/mentions into a clean list of ints."""
    if not values:
        return []
    out: list[int] = []
    for value in values:
        if isinstance(value, int):
            out.append(value)
            continue
        text = str(value).strip()
        if text.isdigit():
            out.append(int(text))
            continue
        mention = MENTION_RE.fullmatch(text) or ROLE_MENTION_RE.fullmatch(text)
        if mention:
            out.append(int(mention.group("id")))
    return out


def mention_or_id(user_id: int) -> str:
    return f"<@{user_id}> (`{user_id}`)"


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or singular + "s")
    return f"{count} {word}"


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def human_join(items: list[str], sep: str = ", ") -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{sep.join(items[:-1])} and {items[-1]}"


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def bot_invite_url(client_id: int, permissions: int = 8) -> str:
    """OAuth2 invite URL. Default permission 8 = Administrator (setup flow)."""
    return (
        f"https://discord.com/oauth2/authorize?client_id={client_id}"
        f"&permissions={permissions}&scope=bot%20applications.commands"
    )


# ---------------------------------------------------------------------------
# Safe calculator (NO eval, NO exec) for /calc
# ---------------------------------------------------------------------------

# Simple strict arithmetic evaluator (NO eval, NO exec) for /calc
def safe_calc(expression: str, *, max_length: int = 200) -> str | None:
    """Evaluate a basic arithmetic expression safely.

    Returns the result as a string, or ``None`` when the input is invalid.
    Deliberately does **not** use ``eval``: only digits, operators, parentheses
    and the ``sqrt`` function are accepted.
    """
    import ast
    import math
    import operator

    text = str(expression)[:max_length].replace(" ", "")
    if not text:
        return None
    if not re.fullmatch(r"[0-9+\-*/%().,a-z]+", text.lower()):
        return None

    binary = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    unary = {ast.UAdd: operator.pos, ast.USub: operator.neg}

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 64:
                raise ValueError("exponent too large")
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                raise ValueError("division by zero")
            return binary[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
            return unary[type(node.op)](_eval(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id != "sqrt" or len(node.args) != 1 or node.keywords:
                raise ValueError("unsupported function")
            return math.sqrt(_eval(node.args[0]))
        raise ValueError("unsupported syntax")

    try:
        tree = ast.parse(text, mode="eval")
        value = _eval(tree)
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, RecursionError):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:.10g}"
