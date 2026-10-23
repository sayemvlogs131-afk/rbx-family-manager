"""🏷️ Reaction roles (role panels) for RBX Family Manager.

Staff create one panel message with ``/rolesetup setup`` and add roles to it
with ``/rolesetup add``. Every member can then click the buttons to give
themselves (or remove) the roles — no staff involvement, no spam pinging mods.

Discord allows up to 25 buttons on one message (5 per row × 5 rows), which is
the panel limit enforced here. Buttons are persistent views: they keep working
after bot restarts because they are re-registered from the database on boot.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import permissions as perms
from utils.checks import require_admin
from utils.embeds import Embeds
from utils.helpers import truncate

log = logging.getLogger("rbx.reactionroles")

MAX_ROLES = 25  # 5 button rows × 5 buttons
PANEL_INSTRUCTIONS = (
    "Click a button below to get or remove that role. "
    "Clicking again takes it away. One role per button."
)


def _panel_embed(guild: discord.Guild, rows: list[dict[str, object]], title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(title, 250),
        description=truncate(description, 3500) + "\n\n" + PANEL_INSTRUCTIONS,
        color=config.BRAND_COLOR,
    )
    if rows:
        listed = "\n".join(f"• <@&{row['role_id']}> — {row['label']}" for row in rows)
        embed.add_field(name=f"Roles ({len(rows)}/{MAX_ROLES})", value=truncate(listed, 1000), inline=False)
    else:
        embed.add_field(name="Roles", value="_No roles yet — add some with `/rolesetup add`._", inline=False)
    embed.set_footer(text=config.FOOTER_TEXT)
    return embed


class RolePanelView(discord.ui.View):
    """Persistent button panel. One button per role; clicking toggles the role."""

    def __init__(self, cog: "ReactionRoles", rows: list[dict[str, object]]) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        for row in rows:
            self.add_item(RoleButton(row["role_id"], str(row["label"])))


class RoleButton(discord.ui.Button["RolePanelView"]):
    def __init__(self, role_id: object, label: str) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label=truncate(label, 80), custom_id=f"rr:{int(role_id)}")

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        cog: ReactionRoles = self.view.cog
        await cog.toggle_role(interaction, int(self.custom_id.split(":", 1)[1]))


class ReactionRoles(commands.Cog, name="Reaction Roles"):
    """Self-serve role panels: members click a button, they get the role."""

    category = "⚙️ Server"
    category_description = "Welcome messages, self-serve role panels and server utilities."

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------- persistence
    async def cog_load(self) -> None:
        """Re-register every saved panel's buttons so they survive restarts."""
        for guild in list(self.bot.guilds):
            try:
                await self._register_panel_view(guild)
            except Exception:
                log.debug("Panel restore failed for guild %s", guild.id, exc_info=True)

    async def _panel_rows(self, guild_id: int) -> list[dict[str, object]]:
        return await self.bot.db.reaction_roles_for_guild(guild_id)  # type: ignore[return-value]

    async def _register_panel_view(self, guild: discord.Guild) -> None:
        rows = await self._panel_rows(guild.id)
        if not rows:
            return
        self.bot.add_view(RolePanelView(self, rows))

    async def _refresh_panel(self, guild: discord.Guild, title: str, description: str) -> Optional[str]:
        """Rebuild the panel message (embed + buttons). Returns an error text on failure."""
        settings = await self.bot.db.get_guild(guild.id)
        channel_id = settings.get("reaction_channel")
        message_id = settings.get("reaction_message")
        if not channel_id or not message_id:
            return "No panel exists yet — run `/rolesetup setup` first."

        channel = guild.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await guild.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return "The panel channel no longer exists. Run `/rolesetup setup` again."
        if not isinstance(channel, discord.abc.Messageable):
            return "The panel channel is not a text channel."

        rows = await self._panel_rows(guild.id)
        view = RolePanelView(self, rows)
        self.bot.add_view(view)  # keep new buttons alive after restarts
        try:
            message = await channel.fetch_message(int(message_id))
            await message.edit(embed=_panel_embed(guild, rows, title, description), view=view)
        except discord.NotFound:
            return "The panel message was deleted. Run `/rolesetup setup` to create a new one."
        except discord.Forbidden:
            return "I lost permission to edit the panel message (missing Manage Messages?)."
        except discord.HTTPException:
            log.exception("Panel refresh failed in guild %s", guild.id)
            return "Discord rejected the panel update — try again in a moment."
        return None

    # ------------------------------------------------------------- toggling
    async def toggle_role(self, interaction: discord.Interaction, role_id: int) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            await interaction.response.send_message(
                embed=Embeds.error("Servers only", "Role panels only work inside a server."), ephemeral=True
            )
            return

        role = guild.get_role(role_id)
        if role is None:
            await interaction.response.send_message(
                embed=Embeds.error("Role removed", "That role no longer exists — ask staff to update the panel."),
                ephemeral=True,
            )
            return

        check = perms.bot_can_assign_role(guild.me, role)
        if not check.ok:
            await interaction.response.send_message(
                embed=Embeds.error("Cannot manage that role", check.reason), ephemeral=True
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction role panel (removed by member)")
                text = f"Removed {role.mention}."
            else:
                await member.add_roles(role, reason="Reaction role panel (added by member)")
                text = f"You now have {role.mention}!"
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=Embeds.error("Missing permission", "I could not change your roles — my role may be too low."),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=Embeds.error("Discord error", "The role change failed — try again in a moment."), ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=Embeds.success("Roles updated", text), ephemeral=True
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        """Keep panels consistent when someone deletes a role on the panel."""
        removed = await self.bot.db.remove_reaction_role(role.guild.id, role.id)
        if not removed:
            return
        settings = await self.bot.db.get_guild(role.guild.id)
        title = truncate(role.guild.name, 200)
        error = await self._refresh_panel(
            role.guild, str(settings.get("reaction_title") or f"{title} role panel"), "Pick your roles."
        )
        if error:
            log.debug("Panel refresh after role delete: %s", error)

    # ------------------------------------------------------------- commands
    @commands.hybrid_group(name="rolesetup", invoke_without_command=True)
    @commands.guild_only()
    @require_admin()
    async def rolesetup(self, ctx: commands.Context) -> None:
        """Configure self-serve role panels."""
        settings = await self.bot.db.get_guild(ctx.guild.id)  # type: ignore[union-attr]
        panel = bool(settings.get("reaction_channel") and settings.get("reaction_message"))
        embed = Embeds.info(
            "Reaction roles (role panels)",
            "**Commands**\n"
            "`/rolesetup setup channel:#roles` — create the panel\n"
            "`/rolesetup add role:@Gamer label:🎮 Gamer` — put a role on the panel\n"
            "`/rolesetup remove role:@Gamer` — take a role off the panel\n"
            "`/rolesetup list` — show the panel contents\n"
            "`/rolesetup reset` — delete the panel",
        )
        if panel:
            embed.add_field(name="Panel", value=f"<#{settings['reaction_channel']}>", inline=True)
            embed.add_field(name="Roles on panel", value=str(len(await self._panel_rows(ctx.guild.id))), inline=True)
        else:
            embed.add_field(name="Status", value="❌ not set up", inline=True)
        await ctx.send(embed=embed, ephemeral=True)

    @rolesetup.command(name="setup")
    @require_admin()
    @app_commands.describe(
        channel="Where the panel message lives",
        title="Panel title (default: Pick your roles)",
        description="Panel description shown above the buttons",
    )
    async def rolesetup_setup(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> None:
        """Create (or move) the role panel message."""
        guild = ctx.guild
        assert guild is not None
        if not channel.permissions_for(guild.me).send_messages:
            await ctx.send(
                embed=Embeds.error("Missing permission", f"I cannot send messages in {channel.mention}."),
                ephemeral=True,
            )
            return

        title = truncate(title or "Pick your roles", 250)
        description = truncate(description or "Click a button to give yourself a role.", 1000)

        settings = await self.bot.db.get_guild(guild.id)
        old_channel_id = settings.get("reaction_channel")
        old_message_id = settings.get("reaction_message")
        if old_channel_id and old_message_id:
            old_channel = guild.get_channel(int(old_channel_id))
            if isinstance(old_channel, discord.abc.Messageable):
                try:
                    old_message = await old_channel.fetch_message(int(old_message_id))
                    await old_message.delete(reason="Reaction role panel replaced")
                except discord.HTTPException:
                    pass

        rows = await self._panel_rows(guild.id)
        embed = _panel_embed(guild, rows, title, description)
        message = await channel.send(embed=embed, view=RolePanelView(self, rows))
        await self.bot.db.update_guild(
            guild.id, reaction_channel=channel.id, reaction_message=message.id,
            reaction_title=title, reaction_description=description,
        )
        await ctx.send(
            embed=Embeds.success("Role panel created", f"{message.jump_url}\nAdd roles with `/rolesetup add`."),
            ephemeral=True,
        )

    @rolesetup.command(name="add")
    @require_admin()
    @app_commands.describe(role="The role members get by clicking", label="Button text (default: the role name)")
    async def rolesetup_add(
        self, ctx: commands.Context, role: discord.Role, label: Optional[str] = None
    ) -> None:
        """Put a role on the panel."""
        guild = ctx.guild
        assert guild is not None
        check = perms.bot_can_assign_role(guild.me, role)
        if not check.ok:
            await ctx.send(embed=Embeds.error("Cannot use that role", check.reason), ephemeral=True)
            return
        if role.is_default() or role.managed:
            await ctx.send(
                embed=Embeds.error("Role not eligible", "@everyone and bot/integration roles cannot be self-assigned."),
                ephemeral=True,
            )
            return

        rows = await self._panel_rows(guild.id)
        if len(rows) >= MAX_ROLES:
            await ctx.send(
                embed=Embeds.error("Panel full", f"Discord allows at most {MAX_ROLES} buttons per message."),
                ephemeral=True,
            )
            return
        if any(int(row["role_id"]) == role.id for row in rows):
            await ctx.send(embed=Embeds.error("Already on the panel", f"{role.mention} is already on it."), ephemeral=True)
            return

        settings = await self.bot.db.get_guild(guild.id)
        message_id = settings.get("reaction_message")
        channel_id = settings.get("reaction_channel")
        if not message_id or not channel_id:
            await ctx.send(embed=Embeds.error("No panel yet", "Run `/rolesetup setup` first."), ephemeral=True)
            return

        button_label = truncate(label or role.name, 80)
        added = await self.bot.db.add_reaction_role(
            guild.id, int(channel_id), int(message_id), role.id, button_label
        )
        if not added:
            await ctx.send(embed=Embeds.error("Already on the panel", f"{role.mention} is already on it."), ephemeral=True)
            return

        title = str(settings.get("reaction_title") or "Pick your roles")
        description = str(settings.get("reaction_description") or "Click a button to give yourself a role.")
        error = await self._refresh_panel(guild, title, description)
        if error:
            await ctx.send(embed=Embeds.error("Panel not updated", error), ephemeral=True)
            return
        await ctx.send(embed=Embeds.success("Role added", f"{role.mention} is now on the panel as “{button_label}”."))

    @rolesetup.command(name="remove")
    @require_admin()
    @app_commands.describe(role="The role to take off the panel")
    async def rolesetup_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        """Take a role off the panel."""
        guild = ctx.guild
        assert guild is not None
        removed = await self.bot.db.remove_reaction_role(guild.id, role.id)
        if not removed:
            await ctx.send(embed=Embeds.error("Not on the panel", f"{role.mention} is not on it."), ephemeral=True)
            return
        settings = await self.bot.db.get_guild(guild.id)
        title = str(settings.get("reaction_title") or "Pick your roles")
        description = str(settings.get("reaction_description") or "Click a button to give yourself a role.")
        error = await self._refresh_panel(guild, title, description)
        if error:
            await ctx.send(embed=Embeds.error("Panel not updated", error), ephemeral=True)
            return
        await ctx.send(embed=Embeds.success("Role removed", f"{role.mention} is off the panel."))

    @rolesetup.command(name="list")
    @require_admin()
    async def rolesetup_list(self, ctx: commands.Context) -> None:
        """Show every role on the panel."""
        guild = ctx.guild
        assert guild is not None
        rows = await self._panel_rows(guild.id)
        if not rows:
            await ctx.send(embed=Embeds.info("Role panel", "No roles on the panel yet."), ephemeral=True)
            return
        lines = "\n".join(f"• <@&{row['role_id']}> — **{row['label']}**" for row in rows)
        await ctx.send(embed=Embeds.info(f"Role panel ({len(rows)}/{MAX_ROLES})", truncate(lines, 3800)), ephemeral=True)

    @rolesetup.command(name="reset")
    @require_admin()
    async def rolesetup_reset(self, ctx: commands.Context) -> None:
        """Delete the panel message and forget its roles."""
        guild = ctx.guild
        assert guild is not None
        settings = await self.bot.db.get_guild(guild.id)
        if settings.get("reaction_channel") and settings.get("reaction_message"):
            channel = guild.get_channel(int(settings["reaction_channel"]))
            if isinstance(channel, discord.abc.Messageable):
                try:
                    message = await channel.fetch_message(int(settings["reaction_message"]))
                    await message.delete(reason="Reaction role panel reset")
                except discord.HTTPException:
                    pass
        count = await self.bot.db.clear_reaction_roles(guild.id)
        await self.bot.db.update_guild(guild.id, reaction_channel=None, reaction_message=None)
        await ctx.send(
            embed=Embeds.success("Panel reset", f"Removed the panel and {count} role button(s)."), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
