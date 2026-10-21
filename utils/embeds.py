"""Consistent RBX Family embed builders.

All bot responses should be created through :class:`Embeds` so the whole bot
shares one visual identity: brand color, footer, timestamps and icons.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

import config


def _footer(embed: discord.Embed) -> discord.Embed:
    embed.set_footer(text=config.FOOTER_TEXT, icon_url=config.ICON_URL or None)
    embed.timestamp = datetime.now(timezone.utc)
    return embed


def _base(
    title: str | None = None,
    description: str | None = None,
    *,
    color: int = config.BRAND_COLOR,
) -> discord.Embed:
    embed = discord.Embed(color=color)
    if title:
        embed.title = title
    if description:
        embed.description = description
    return embed


class Embeds:
    """Namespace of embed factories used across every cog."""

    # ------------------------------------------------------------ basics
    @staticmethod
    def success(title: str, description: str | None = None, **kwargs: Any) -> discord.Embed:
        embed = _base(f"✅ {title}", description, color=config.SUCCESS_COLOR)
        for key, value in kwargs.items():
            if hasattr(embed, key):
                setattr(embed, key, value)
        return _footer(embed)

    @staticmethod
    def error(title: str, description: str | None = None) -> discord.Embed:
        return _footer(_base(f"❌ {title}", description, color=config.ERROR_COLOR))

    @staticmethod
    def warning(title: str, description: str | None = None) -> discord.Embed:
        return _footer(_base(f"⚠️ {title}", description, color=config.WARNING_COLOR))

    @staticmethod
    def info(title: str, description: str | None = None) -> discord.Embed:
        return _footer(_base(f"ℹ️ {title}", description, color=config.INFO_COLOR))

    @staticmethod
    def question(title: str, description: str | None = None) -> discord.Embed:
        return _footer(_base(f"❓ {title}", description, color=config.BRAND_COLOR))

    @staticmethod
    def plain(description: str, color: int = config.BRAND_COLOR) -> discord.Embed:
        return _footer(_base(None, description, color=color))

    # ------------------------------------------------------- moderation
    @staticmethod
    def moderation_action(
        *,
        action: str,
        target: Any,
        moderator: Any,
        reason: str = "No reason provided",
        duration: str | None = None,
        case_id: int | None = None,
        extra: str | None = None,
    ) -> discord.Embed:
        target_text = getattr(target, "mention", None) or getattr(target, "name", str(target))
        mod_text = getattr(moderator, "mention", None) or getattr(moderator, "name", str(moderator))
        embed = _base(
            "🛡️ Moderation Action",
            color=config.MODERATION_COLOR,
        )
        embed.add_field(name="Action", value=action, inline=True)
        embed.add_field(name="Target", value=target_text, inline=True)
        embed.add_field(name="Moderator", value=mod_text, inline=True)
        embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)
        if case_id:
            embed.add_field(name="Case", value=f"#{case_id}", inline=True)
        if extra:
            embed.add_field(name="Details", value=extra, inline=False)
        avatar_url = getattr(getattr(target, "display_avatar", None), "url", None)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        return _footer(embed)

    @staticmethod
    def member(target: Any, title: str) -> discord.Embed:
        embed = _base(title, color=config.BRAND_COLOR)
        name = getattr(target, "name", str(target))
        embed.set_author(
            name=name,
            icon_url=getattr(getattr(target, "display_avatar", None), "url", None),
        )
        embed.add_field(name="User", value=getattr(target, "mention", str(target)), inline=True)
        embed.add_field(name="ID", value=f"`{getattr(target, 'id', 'unknown')}`", inline=True)
        return _footer(embed)

    # ------------------------------------------------------- logging
    @staticmethod
    def log(
        event: str,
        description: str,
        *,
        color: int = config.LOG_COLOR,
        fields: list[tuple[str, str, bool]] | None = None,
        actor: Any = None,
    ) -> discord.Embed:
        embed = _base(f"📋 {event}", description, color=color)
        for name, value, inline in fields or []:
            embed.add_field(name=name, value=value or "`—`", inline=inline)
        if actor is not None:
            avatar = getattr(getattr(actor, "display_avatar", None), "url", None)
            embed.set_author(
                name=str(getattr(actor, "name", actor)), icon_url=avatar
            )
        return _footer(embed)

    # ----------------------------------------------------- giveaways
    @staticmethod
    def giveaway(
        *,
        prize: str,
        description: str | None,
        winners: int,
        ends_at: int,
        host_id: int,
        entries: int = 0,
        ended: bool = False,
        winners_list: list[int] | None = None,
    ) -> discord.Embed:
        title = "🎉 GIVEAWAY ENDED" if ended else "🎉 GIVEAWAY"
        embed = _base(title, color=config.GIVEAWAY_COLOR)
        embed.add_field(name="Prize", value=prize, inline=True)
        embed.add_field(name="Winners", value=str(winners), inline=True)
        embed.add_field(name="Entries", value=str(entries), inline=True)
        embed.add_field(
            name="Ends",
            value=f"<t:{ends_at}:F> (<t:{ends_at}:R>)",
            inline=False,
        )
        host_text = f"<@{host_id}>"
        embed.add_field(name="Hosted by", value=host_text, inline=True)
        if description:
            embed.description = description
        if ended and winners_list is not None:
            if winners_list:
                embed.add_field(
                    name="Winner(s)",
                    value=", ".join(f"<@{uid}>" for uid in winners_list),
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Winner(s)",
                    value="No valid entries — no winner was drawn.",
                    inline=False,
                )
        return _footer(embed)

    # -------------------------------------------------------- help
    @staticmethod
    def help_home(bot: Any, categories: dict[str, str]) -> discord.Embed:
        embed = _base(
            f"{config.BOT_NAME}",
            "Select a category from the menu below to browse commands.\n"
            "Every command is a slash command — just type `/`.",
            color=config.BRAND_COLOR,
        )
        lines: list[str] = []
        for name, raw in categories.items():
            if "|" in raw:
                icon, desc = raw.split("|", 1)
            else:
                icon, desc = "•", raw
            lines.append(f"{icon} **{name}** — {desc}")
        embed.add_field(name="Categories", value="\n".join(lines) or "—", inline=False)
        avatar = getattr(getattr(bot, "user", None), "display_avatar", None)
        if avatar:
            embed.set_thumbnail(url=avatar.url)
        embed.add_field(
            name="Invite",
            value="[Add RBX Family Manager to your server]"
            "(https://discord.com/oauth2/authorize?scope=bot%20applications.commands)",
            inline=False,
        )
        return _footer(embed)

    @staticmethod
    def help_category(name: str, icon: str, description: str, commands: list[str]) -> discord.Embed:
        embed = _base(f"{icon} {name}", description, color=config.BRAND_COLOR)
        if commands:
            embed.add_field(name="Commands", value="\n".join(commands), inline=False)
        else:
            embed.description = description + "\n\n_No commands yet._"
        return _footer(embed)

    @staticmethod
    def command_list(commands: list[str], title: str = "Commands") -> discord.Embed:
        embed = _base(title, color=config.BRAND_COLOR)
        embed.add_field(name="Available", value="\n".join(commands) or "—", inline=False)
        return _footer(embed)


__all__ = ["Embeds"]
