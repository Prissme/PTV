"""Commandes de classement économique."""
from __future__ import annotations

import asyncio
import logging

import asyncpg
import discord
from discord.ext import commands, tasks

from config import (
    LEADERBOARD_LIMIT,
    Emojis,
    QUERY_TIMEOUT_SECONDS,
    TOP_PB_ROLE_ID,
    TOP_PB_ROLE_LIMIT,
    TOP_PB_ROLE_REFRESH_MINUTES,
)
from database.db import DatabaseError
from utils.mastery import EGG_MASTERY, MASTERMIND_MASTERY, PET_MASTERY, MasteryDefinition
from utils import embeds

logger = logging.getLogger(__name__)


class LeaderboardView(discord.ui.View):
    """Vue paginée pour les classements économiques."""

    def __init__(
        self,
        ctx: commands.Context,
        cog: "Leaderboard",
        *,
        initial_type: str = "rap",
    ) -> None:
        super().__init__(timeout=120)
        self.ctx = ctx
        self.cog = cog
        self.current_type = initial_type
        self.page = 0
        self.total_entries = 0
        self.per_page = LEADERBOARD_LIMIT
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _max_page(self) -> int:
        if self.total_entries <= 0:
            return 1
        return max(1, (self.total_entries + self.per_page - 1) // self.per_page)

    def _sync_buttons(self) -> None:
        max_page = self._max_page()
        if hasattr(self, "previous_page"):
            self.previous_page.disabled = self.page <= 0
        if hasattr(self, "next_page"):
            self.next_page.disabled = self.page >= max_page - 1

    async def _fetch_entries(self) -> tuple[list[tuple[int, int]], int, str, str]:
        offset = self.page * self.per_page
        if self.current_type == "pb":
            rows, total = await self.cog.database.get_balance_leaderboard_page(
                self.per_page, offset
            )
            entries = [(int(row["user_id"]), int(row["balance"])) for row in rows]
            return entries, total, "Classement des plus riches", "PB"
        if self.current_type == "gem":
            rows, total = await self.cog.database.get_gem_leaderboard_page(
                self.per_page, offset
            )
            entries = [(int(row["user_id"]), int(row["gems"])) for row in rows]
            return entries, total, f"Classement {Emojis.GEM}", "GEM"
        entries, total = await self.cog.database.get_pet_rap_leaderboard_page(
            self.per_page, offset
        )
        return entries, total, "Classement des plus gros RAP", "RAP"

    async def build_embed(self) -> discord.Embed:
        try:
            entries, total, title, symbol = await self._fetch_entries()
        except Exception:
            logger.exception("Impossible de récupérer le classement %s", self.current_type)
            return embeds.error_embed("Impossible de récupérer le classement demandé.")

        self.total_entries = total
        self._sync_buttons()
        return embeds.leaderboard_embed(
            title=title,
            entries=entries,
            bot=self.cog.bot,
            symbol=symbol,
            start_rank=self.page * self.per_page + 1,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Seule la personne qui a lancé la commande peut utiliser ces boutons.",
                ephemeral=True,
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        embed = await self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Précédent", style=discord.ButtonStyle.secondary, row=0)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page > 0:
            self.page -= 1
        await self._refresh(interaction)

    @discord.ui.button(label="Suivant", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page < self._max_page() - 1:
            self.page += 1
        await self._refresh(interaction)

    @discord.ui.button(label="RAP", style=discord.ButtonStyle.primary, row=1)
    async def show_rap(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current_type = "rap"
        self.page = 0
        await self._refresh(interaction)

    @discord.ui.button(label="PB", style=discord.ButtonStyle.secondary, row=1)
    async def show_pb(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current_type = "pb"
        self.page = 0
        await self._refresh(interaction)

    @discord.ui.button(label=str(Emojis.GEM), style=discord.ButtonStyle.secondary, row=1)
    async def show_gems(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.current_type = "gem"
        self.page = 0
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Leaderboard(commands.Cog):
    """Expose le classement économique minimal."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._top_pb_role_loop.start()

    def cog_unload(self) -> None:
        self._top_pb_role_loop.cancel()

    @tasks.loop(minutes=TOP_PB_ROLE_REFRESH_MINUTES)
    async def _top_pb_role_loop(self) -> None:
        await self._refresh_top_pb_roles()

    @_top_pb_role_loop.before_loop
    async def _before_top_pb_role_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _refresh_top_pb_roles(self) -> None:
        if TOP_PB_ROLE_ID <= 0:
            return

        try:
            rows = await self.database.get_pet_rap_leaderboard(TOP_PB_ROLE_LIMIT)
        except Exception:
            logger.exception("Impossible de récupérer le classement RAP pour le rôle top.")
            return

        top_ids = {int(user_id) for user_id, _value in rows}
        if not top_ids:
            return

        for guild in self.bot.guilds:
            role = guild.get_role(TOP_PB_ROLE_ID)
            if role is None:
                continue
            await self._sync_top_role_for_guild(role, top_ids)

    async def _sync_top_role_for_guild(self, role: discord.Role, top_ids: set[int]) -> None:
        reason = "Mise à jour automatique du top 30 RAP"
        guild = role.guild
        members_to_remove = [
            member
            for member in role.members
            if not member.bot and member.id not in top_ids
        ]
        for member in members_to_remove:
            try:
                await member.remove_roles(role, reason=reason)
            except Exception:
                logger.exception(
                    "Impossible de retirer le rôle top PB de %s sur %s",
                    member.id,
                    guild.id,
                )

        for user_id in top_ids:
            member = guild.get_member(user_id)
            if member is None or member.bot or role in member.roles:
                continue
            try:
                await member.add_roles(role, reason=reason)
            except Exception:
                logger.exception(
                    "Impossible d'ajouter le rôle top PB à %s sur %s",
                    member.id,
                    guild.id,
                )

    @commands.command(name="leaderboard", aliases=("lb",))
    async def leaderboard(self, ctx: commands.Context) -> None:
        view = LeaderboardView(ctx, self, initial_type="rap")
        status_msg = await ctx.send("⏳ Génération du classement…")
        try:
            embed = await asyncio.wait_for(view.build_embed(), timeout=QUERY_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, asyncpg.exceptions.QueryCanceledError):
            await status_msg.edit(
                content=None,
                embed=embeds.warning_embed(
                    "Le classement est trop lourd à générer pour le moment."
                ),
            )
            return
        message = await status_msg.edit(content=None, embed=embed, view=view)
        view.message = message

    @commands.command(name="gemlb", aliases=("gemleaderboard",))
    async def gem_leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.database.get_gem_leaderboard(LEADERBOARD_LIMIT)
        embed = embeds.leaderboard_embed(
            title=f"Classement {Emojis.GEM}",
            entries=[(row["user_id"], row["gems"]) for row in rows],
            bot=self.bot,
            symbol="GEM",
        )
        await ctx.send(embed=embed)

    @commands.command(name="rapleaderboard", aliases=("raplb", "rap"))
    async def rap_leaderboard(self, ctx: commands.Context) -> None:
        try:
            rows = await self.database.get_pet_rap_leaderboard(LEADERBOARD_LIMIT)
        except Exception:
            await ctx.send(embed=embeds.error_embed("Impossible de récupérer le classement RAP."))
            return

        embed = embeds.leaderboard_embed(
            title="Classement des plus gros RAP",
            entries=[(user_id, rap) for user_id, rap in rows],
            bot=self.bot,
            symbol="RAP",
        )
        await ctx.send(embed=embed)

    @commands.command(name="revenusleaderboard", aliases=("revenuslb", "incomelb", "hourlylb"))
    async def income_leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.database.get_hourly_income_leaderboard(LEADERBOARD_LIMIT)
        embed = embeds.leaderboard_embed(
            title="Classement des revenus horaires",
            entries=[(user_id, income) for user_id, income in rows],
            bot=self.bot,
            symbol="PB/h",
        )
        await ctx.send(embed=embed)

    async def _send_mastery_leaderboard(
        self, ctx: commands.Context, mastery: MasteryDefinition
    ) -> None:
        try:
            rows = await self.database.get_mastery_leaderboard(mastery.slug, LEADERBOARD_LIMIT)
        except DatabaseError as exc:  # type: ignore[name-defined]
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        except Exception:
            await ctx.send(
                embed=embeds.error_embed("Impossible de récupérer le classement demandé.")
            )
            return

        title = f"Classement {mastery.display_name}"
        if not rows:
            await ctx.send(
                embed=embeds.info_embed(
                    "Aucune donnée disponible pour cette maîtrise pour le moment.",
                    title=title,
                )
            )
            return

        lines = []
        for rank, row in enumerate(rows, start=1):
            user_id = int(row["user_id"])
            level = int(row.get("level") or 0)
            xp = int(row.get("experience") or 0)
            required = mastery.required_xp(level)
            if required <= 0:
                progress = f"{xp} XP (niveau max)"
            else:
                progress = f"{xp}/{required} XP"

            user = self.bot.get_user(user_id)
            name = user.display_name if user else f"Utilisateur {user_id}"
            lines.append(f"**{rank}.** {name} — Niveau {level} ({progress})")

        embed = embeds.info_embed("\n".join(lines), title=title)
        await ctx.send(embed=embed)

    @commands.command(name="eggmasteryleb", aliases=("eggmasterylb",))
    async def egg_mastery_leaderboard(self, ctx: commands.Context) -> None:
        await self._send_mastery_leaderboard(ctx, EGG_MASTERY)

    @commands.command(name="petmasteryleb", aliases=("petmasterylb",))
    async def pet_mastery_leaderboard(self, ctx: commands.Context) -> None:
        await self._send_mastery_leaderboard(ctx, PET_MASTERY)

    @commands.command(
        name="mastermindmasterylb",
        aliases=("mastermindmasteryleb", "mmmasterylb"),
    )
    async def mastermind_mastery_leaderboard(self, ctx: commands.Context) -> None:
        await self._send_mastery_leaderboard(ctx, MASTERMIND_MASTERY)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leaderboard(bot))
