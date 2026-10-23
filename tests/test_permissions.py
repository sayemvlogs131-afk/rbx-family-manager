"""Hierarchy / permission safety tests using lightweight stubs."""

from __future__ import annotations

from types import SimpleNamespace


from utils import permissions as perms


def make_member(
    user_id: int,
    top_position: int = 0,
    *,
    perms_: dict[str, bool] | None = None,
    roles: list[int] | None = None,
) -> SimpleNamespace:
    base_perms = {"administrator": False, "manage_guild": False}
    base_perms.update(perms_ or {})
    top_role = SimpleNamespace(position=top_position, id=1000 + top_position)
    return SimpleNamespace(
        id=user_id,
        top_role=top_role,
        guild_permissions=SimpleNamespace(**base_perms),
        roles=[SimpleNamespace(id=r) for r in (roles or [])],
        name=f"user{user_id}",
    )


# --------------------------------------------------------------- has_permissions
def test_has_permissions_true() -> None:
    member = make_member(1, 0, perms_={"ban_members": True})
    assert perms.has_permissions(member, "ban_members")


def test_has_permissions_false() -> None:
    member = make_member(1, 0)
    assert not perms.has_permissions(member, "ban_members")
    assert perms.missing_permissions(member, "ban_members") == ["Ban Members"]


# ---------------------------------------------------------------- can_moderate
def test_cannot_moderate_guild_owner() -> None:
    actor = make_member(1, 50, perms_={"ban_members": True})
    target = make_member(2, 99)
    result = perms.can_moderate(actor, target, guild_owner_id=2, bot_member=make_member(9, 60))
    assert not result.ok
    assert "server owner" in result.reason


def test_cannot_moderate_self() -> None:
    actor = make_member(1, 50, perms_={"ban_members": True})
    result = perms.can_moderate(actor, actor, guild_owner_id=2, bot_member=make_member(9, 60))
    assert not result.ok
    assert "yourself" in result.reason


def test_cannot_moderate_equal_or_higher_role() -> None:
    actor = make_member(1, 10, perms_={"ban_members": True})
    higher = make_member(2, 11)
    equal = make_member(3, 10)
    bot = make_member(9, 60)
    assert not perms.can_moderate(actor, higher, guild_owner_id=99, bot_member=bot).ok
    assert not perms.can_moderate(actor, equal, guild_owner_id=99, bot_member=bot).ok


def test_guild_owner_may_moderate_anyone() -> None:
    owner = make_member(1, 1, perms_={"ban_members": True, "administrator": True})
    target = make_member(2, 50)
    result = perms.can_moderate(owner, target, guild_owner_id=1, bot_member=make_member(9, 60))
    assert result.ok


def test_bot_hierarchy_is_enforced() -> None:
    actor = make_member(1, 50, perms_={"ban_members": True})
    target = make_member(2, 5)
    low_bot = make_member(9, 3)
    result = perms.can_moderate(actor, target, guild_owner_id=99, bot_member=low_bot)
    assert not result.ok
    assert "highest role" in result.reason

    high_bot = make_member(9, 60)
    assert perms.can_moderate(actor, target, guild_owner_id=99, bot_member=high_bot).ok


def test_cannot_moderate_bot_itself() -> None:
    actor = make_member(1, 50, perms_={"ban_members": True})
    bot = make_member(9, 60)
    result = perms.can_moderate(actor, bot, guild_owner_id=99, bot_member=bot)
    assert not result.ok


def test_bot_owner_account_is_protected() -> None:
    actor = make_member(1, 50, perms_={"ban_members": True})
    target = make_member(777, 2)
    result = perms.can_moderate(
        actor, target, guild_owner_id=99, bot_member=make_member(9, 60), bot_owner_ids=[777]
    )
    assert not result.ok
    assert "bot owner" in result.reason

    # ...unless explicitly allowed.
    allowed = perms.can_moderate(
        actor,
        target,
        guild_owner_id=99,
        bot_member=make_member(9, 60),
        bot_owner_ids=[777],
        allow_bot_owner=True,
    )
    assert allowed.ok


def test_allow_self_flag() -> None:
    actor = make_member(1, 50, perms_={"moderate_members": True})
    result = perms.can_moderate(
        actor, actor, guild_owner_id=99, bot_member=make_member(9, 60), allow_self=True
    )
    assert result.ok


# ------------------------------------------------------------------- role checks
def test_bot_can_assign_role_hierarchy() -> None:
    role = SimpleNamespace(position=10, managed=False, is_default=lambda: False)
    low_bot = make_member(9, 5, perms_={"manage_roles": True})
    assert not perms.bot_can_assign_role(low_bot, role).ok
    high_bot = make_member(9, 50, perms_={"manage_roles": True})
    assert perms.bot_can_assign_role(high_bot, role).ok


def test_bot_cannot_assign_managed_or_default_role() -> None:
    bot = make_member(9, 50, perms_={"manage_roles": True})
    managed = SimpleNamespace(position=1, managed=True, is_default=lambda: False)
    default = SimpleNamespace(position=0, managed=False, is_default=lambda: True)
    assert not perms.bot_can_assign_role(bot, managed).ok
    assert not perms.bot_can_assign_role(bot, default).ok


def test_manage_roles_check_requires_permission() -> None:
    role = SimpleNamespace(position=10, managed=False, is_default=lambda: False)
    member = make_member(1, 50)
    assert not perms.manage_roles_check(member, role).ok
    granted = make_member(1, 50, perms_={"manage_roles": True})
    assert perms.manage_roles_check(granted, role).ok


# --------------------------------------------------------------- role helpers
def test_is_moderator_via_staff_role() -> None:
    member = make_member(1, 1, roles=[555])
    assert not perms.is_moderator(member, [])
    assert perms.is_moderator(member, [555])


def test_is_privileged() -> None:
    assert perms.is_privileged(make_member(1, 0, perms_={"administrator": True}))
    assert perms.is_privileged(make_member(2, 0, perms_={"manage_guild": True}))
    assert not perms.is_privileged(make_member(3, 0))
