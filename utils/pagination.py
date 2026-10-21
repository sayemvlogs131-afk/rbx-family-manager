"""Reusable Discord UI components: pagination and confirmation prompts."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Sequence

import discord


class Paginator(discord.ui.View):
    """Button paginator over a list of page strings (or embeds)."""

    def __init__(
        self,
        pages: Sequence[str | discord.Embed],
        *,
        author_id: int,
        timeout: float = 180.0,
        per_page_footer: bool = True,
    ) -> None:
        super().__init__(timeout=timeout)
        if not pages:
            raise ValueError("Paginator requires at least one page.")
        self.pages = list(pages)
        self.author_id = author_id
        self.index = 0
        self.per_page_footer = per_page_footer
        self.message: discord.Message | None = None
        self._configure()

    def _configure(self) -> None:
        total = len(self.pages)
        self.first.disabled = self.prev.disabled = total <= 1
        self.next.disabled = self.last.disabled = total <= 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Only the command invoker can use these controls.", ephemeral=True
        )
        return False

    def _current_page(self) -> str | discord.Embed:
        page = self.pages[self.index]
        if self.per_page_footer and isinstance(page, discord.Embed):
            page = page.copy()
            page.set_footer(
                text=f"RBX Family Manager • Page {self.index + 1}/{len(self.pages)}"
            )
        return page

    async def _update(self, interaction: discord.Interaction) -> None:
        self._configure()
        await interaction.response.edit_message(embed=self._current_page(), view=self)

    @discord.ui.button(label="« First", style=discord.ButtonStyle.secondary, row=1)
    async def first(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index = 0
        await self._update(interaction)

    @discord.ui.button(label="‹ Prev", style=discord.ButtonStyle.primary, row=1)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        await self._update(interaction)

    @discord.ui.button(label="Next ›", style=discord.ButtonStyle.primary, row=1)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index = min(len(self.pages) - 1, self.index + 1)
        await self._update(interaction)

    @discord.ui.button(label="Last »", style=discord.ButtonStyle.secondary, row=1)
    async def last(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.index = len(self.pages) - 1
        await self._update(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="✖️", row=2)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        if self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass
        else:
            await interaction.response.defer()
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class ConfirmView(discord.ui.View):
    """Yes/No confirmation used for dangerous actions."""

    def __init__(
        self,
        *,
        author_id: int,
        confirm_style: discord.ButtonStyle = discord.ButtonStyle.danger,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
        timeout: float = 60.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: bool | None = None
        self._confirm.label = confirm_label
        self._confirm.style = confirm_style
        self._cancel.label = cancel_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Only the command invoker can confirm this action.", ephemeral=True
        )
        return False

    async def _finish(self, interaction: discord.Interaction, value: bool) -> None:
        self.value = value
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def _confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._finish(interaction, True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def _cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._finish(interaction, False)

    async def on_timeout(self) -> None:
        if self.value is None:
            self.value = False
            self.stop()


async def wait_for_confirm(
    ctx: Any,
    *,
    author_id: int,
    prompt: discord.Embed,
    confirm_label: str = "Confirm",
    timeout: float = 60.0,
    ephemeral: bool = False,
) -> bool:
    """Send ``prompt`` with a confirm button and wait for the answer."""
    view = ConfirmView(author_id=author_id, confirm_label=confirm_label, timeout=timeout)
    message = await ctx.send(embed=prompt, view=view, ephemeral=ephemeral)
    await view.wait()
    try:
        await message.edit(view=None)
    except discord.HTTPException:
        pass
    return bool(view.value)


class MenuPaginator(discord.ui.View):
    """Select-menu based navigator (used by /help)."""

    def __init__(
        self,
        options: list[discord.SelectOption],
        resolver: Callable[[str], Awaitable[discord.Embed | str]],
        *,
        author_id: int,
        timeout: float = 300.0,
        placeholder: str = "Choose a category",
    ) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.resolver = resolver
        self.message: discord.Message | None = None
        select = discord.ui.Select(placeholder=placeholder, options=options[:25], row=0)
        select.callback = self._on_select
        self.select = select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        # Never index ``children`` positionally: decorated items are added
        # before programmatically created ones. Use the stored reference.
        values = list(self.select.values) or list(
            (interaction.data or {}).get("values") or []
        )
        if not values:
            await interaction.response.defer()
            return
        payload = await self.resolver(values[0])
        if isinstance(payload, discord.Embed):
            await interaction.response.edit_message(embed=payload, view=self)
        else:
            await interaction.response.edit_message(content=payload, embed=None, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(
            "Only the command invoker can use this menu.", ephemeral=True
        )
        return False

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


__all__ = ["Paginator", "ConfirmView", "MenuPaginator", "wait_for_confirm"]
