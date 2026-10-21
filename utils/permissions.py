"""Permission and hierarchy safety checks for RBX Family Manager.

Every function here is deliberately duck-typed so it can be unit-tested with
simple stub objects instead of a live Discord connection.

Rules enforced for moderation actions:

* nobody may act on the guild owner
* nobody may act on the bot itself
* nobody may act on a configured bot owner (unless explicitly allowed)
* moderators may not act on themselves through moderation commands
* a moderator may not act on an equal or higher top role (unless guild owner)
* the bot must sit strictly above the target in the role hierarchy
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# Permission names the bot needs for common moderation actions.
BAN_PERMISSIONS = ("ban_members",)
KICK_PERMISSIONS = ("kick_members",)
TIMEOUT_PERMISSIONS = ("moderate_members",)
MANAGE_ROLES_PERMISSIONS = ("manage_roles",)
MANAGE_GUILD_PERMISSIONS = ("manage_guild",)
MANAGE_CHANNELS_PERMISSIONS = ("manage_channels",)


@dataclass(frozen=True)
class SafetyResult:
    """Outcome of a safety/hierarchy check."""

    ok: bool
    reason: str = ""

    @classmethod
    def allowed(cls) -> "SafetyResult":
        return cls(True)

    @classmethod
    def denied(cls, reason: str) -> "SafetyResult":
        return cls(False, reason)

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.ok


def _position(member: Any) -> int:
    """Highest role position of a member — or the position of a role itself.

    Duck-typed so both ``discord.Member`` and ``discord.Role`` work.
    """
    top = getattr(member, "top_role", None)
    if top is not None:
        return int(getattr(top, "position", 0) or 0)
    return int(getattr(member, "position", 0) or 0)


def _perm(member: Any, name: str) -> bool:
    perms = getattr(member, "guild_permissions", None)
    if perms is None:
        return False
    return bool(getattr(perms, name, False))


def has_permissions(member: Any, *names: str) -> bool:
    """True when ``member`` holds every listed guild permission."""
    return all(_perm(member, n) for n in names)


def missing_permissions(member: Any, *names: str) -> list[str]:
    return [n.replace("_", " ").title() for n in names if not _perm(member, n)]


def bot_can_act(bot_member: Any, target: Any) -> SafetyResult:
    """The bot's own role hierarchy relative to ``target``."""
    if bot_member is None:
        return SafetyResult.allowed()  # unknown - let Discord API decide
    if getattr(target, "id", None) == getattr(bot_member, "id", None):
        return SafetyResult.denied("I cannot perform this action on myself.")
    if _position(bot_member) <= _position(target):
        return SafetyResult.denied(
            "My highest role is not above the target's highest role. "
            "Move my top role above theirs and try again."
        )
    return SafetyResult.allowed()


def can_moderate(
    actor: Any,
    target: Any,
    *,
    guild_owner_id: int | None = None,
    bot_member: Any = None,
    bot_owner_ids: Iterable[int] = (),
    allow_bot_owner: bool = False,
    allow_self: bool = False,
) -> SafetyResult:
    """Full safety check before a moderation action.

    Returns :class:`SafetyResult`; ``result.reason`` is a user-facing message.
    """
    if target is None:
        return SafetyResult.denied("I could not find that member.")

    target_id = getattr(target, "id", None)
    actor_id = getattr(actor, "id", None)

    if guild_owner_id is not None and target_id == guild_owner_id:
        return SafetyResult.denied("I cannot moderate the server owner.")

    if bot_member is not None and target_id == getattr(bot_member, "id", None):
        return SafetyResult.denied("I cannot moderate myself.")

    owner_ids = set(int(x) for x in bot_owner_ids)
    if owner_ids and target_id in owner_ids and not allow_bot_owner:
        return SafetyResult.denied("That account is a configured bot owner.")

    if not allow_self and target_id == actor_id:
        return SafetyResult.denied("You cannot take moderation actions on yourself.")

    acting_on_self = target_id == actor_id
    actor_is_owner = guild_owner_id is not None and actor_id == guild_owner_id
    if not actor_is_owner and not acting_on_self:
        if _position(actor) <= _position(target):
            return SafetyResult.denied(
                "You cannot act on a member whose highest role is equal to or "
                "higher than yours."
            )

    hierarchy = bot_can_act(bot_member, target)
    if not hierarchy.ok:
        return hierarchy

    return SafetyResult.allowed()


def can_interact_with(member: Any, other: Any) -> SafetyResult:
    """Non-punitive variant: used for role/channel targeting sanity checks."""
    if member is None or other is None:
        return SafetyResult.allowed()
    if getattr(member, "id", None) == getattr(other, "id", None):
        return SafetyResult.allowed()
    if _position(member) < _position(other):
        return SafetyResult.denied("Your highest role must be above theirs.")
    return SafetyResult.allowed()


def manage_roles_check(member: Any, role: Any, *, guild_owner_id: int | None = None) -> SafetyResult:
    """Can ``member`` assign/modify ``role``? (position + Manage Roles)."""
    if role is None:
        return SafetyResult.denied("Role not found.")
    if getattr(role, "managed", False):
        return SafetyResult.denied("That role is managed by an integration and cannot be assigned.")
    if getattr(role, "is_default", lambda: False)():
        return SafetyResult.denied("@everyone cannot be assigned as an auto role.")
    if not has_permissions(member, "manage_roles"):
        return SafetyResult.denied("You need the **Manage Roles** permission.")
    if guild_owner_id is not None and getattr(member, "id", None) == guild_owner_id:
        return SafetyResult.allowed()
    if _position(member) <= _position(role):
        return SafetyResult.denied("Your highest role must be above that role.")
    return SafetyResult.allowed()


def bot_can_assign_role(bot_member: Any, role: Any) -> SafetyResult:
    """Hierarchy check for the bot assigning a role."""
    if role is None:
        return SafetyResult.denied("Role not found.")
    if getattr(role, "managed", False):
        return SafetyResult.denied("That role is integration-managed and cannot be assigned.")
    if getattr(role, "is_default", lambda: False)():
        return SafetyResult.denied("@everyone cannot be used.")
    if not has_permissions(bot_member, "manage_roles"):
        return SafetyResult.denied("I need the **Manage Roles** permission.")
    if _position(bot_member) <= _position(role):
        return SafetyResult.denied(
            "My highest role must be above that role. Drag my role higher."
        )
    return SafetyResult.allowed()


def is_privileged(member: Any) -> bool:
    """Administrator or guild management access."""
    return has_permissions(member, "administrator") or has_permissions(member, "manage_guild")


def is_moderator(member: Any, staff_role_ids: Iterable[int] = ()) -> bool:
    """Administrator, *any* moderator-level permission, or a staff role."""
    if is_privileged(member):
        return True
    moderation_perms = ("ban_members", "kick_members", "moderate_members")
    if any(_perm(member, p) for p in moderation_perms):
        return True
    member_role_ids = {getattr(r, "id", None) for r in getattr(member, "roles", [])}
    return bool(member_role_ids & {int(r) for r in staff_role_ids})


__all__ = [
    "SafetyResult",
    "can_moderate",
    "bot_can_act",
    "can_interact_with",
    "manage_roles_check",
    "bot_can_assign_role",
    "has_permissions",
    "missing_permissions",
    "is_privileged",
    "is_moderator",
    "BAN_PERMISSIONS",
    "KICK_PERMISSIONS",
    "TIMEOUT_PERMISSIONS",
    "MANAGE_ROLES_PERMISSIONS",
    "MANAGE_GUILD_PERMISSIONS",
    "MANAGE_CHANNELS_PERMISSIONS",
]
