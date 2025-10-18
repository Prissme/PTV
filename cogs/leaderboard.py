"""Classements économiques."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import MAX_LEADERBOARD_LIMIT
from utils import embeds

logger = logging.getLogger(__name__)


class Leaderboard(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database

    async def _send_leaderboard(self, ctx_or_inter, *, ascending: bool = False, limit: int = 10, title: str) -> None:
        limit = max(1, min(limit, MAX_LEADERBOARD_LIMIT))
        rows = await self.database.get_balance_leaderboard(limit=limit, ascending=ascending)
        embed = embeds.leaderboard_embed(title, [(row["user_id"], row["balance"]) for row in rows], self.bot, symbol="PB")
        await ctx_or_inter.send(embed=embed)

    @commands.command(name="leaderboard")
    async def leaderboard_prefix(self, ctx: commands.Context, limit: int = 10) -> None:
        await self._send_leaderboard(ctx, limit=limit, title="Classement des plus riches")

    @app_commands.command(name="leaderboard", description="Voir les plus riches")
    async def leaderboard_slash(self, interaction: discord.Interaction, limit: int = 10) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._send_leaderboard(interaction.followup, limit=limit, title="Classement des plus riches")

    @commands.command(name="poorest")
    async def poorest_prefix(self, ctx: commands.Context, limit: int = 10) -> None:
        await self._send_leaderboard(ctx, ascending=True, limit=limit, title="Les plus pauvres")

    @app_commands.command(name="poorest", description="Voir les soldes les plus bas")
    async def poorest_slash(self, interaction: discord.Interaction, limit: int = 10) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._send_leaderboard(interaction.followup, ascending=True, limit=limit, title="Les plus pauvres")

    @commands.command(name="total")
    async def total_prefix(self, ctx: commands.Context) -> None:
        total = await self.database.get_total_wealth()
        embed = embeds.info_embed(f"Richesse cumulée du serveur : {embeds.format_currency(total)}", title="Économie")
        await ctx.send(embed=embed)

    @app_commands.command(name="total", description="Voir la richesse cumulée du serveur")
    async def total_slash(self, interaction: discord.Interaction) -> None:
        total = await self.database.get_total_wealth()
        embed = embeds.info_embed(f"Richesse cumulée du serveur : {embeds.format_currency(total)}", title="Économie")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(name="rank")
    async def rank_prefix(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        await self._send_rank(ctx, member or ctx.author)

    @app_commands.command(name="rank", description="Voir ton rang économique")
    async def rank_slash(self, interaction: discord.Interaction, membre: discord.Member | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        await self._send_rank(interaction.followup, membre or interaction.user)

    async def _send_rank(self, ctx_or_inter, member: discord.Member) -> None:
        balance = await self.database.fetch_balance(member.id)
        higher = await self.database.fetch_value("SELECT COUNT(*) FROM users WHERE balance > $1", balance)
        rank = (higher or 0) + 1
        embed = embeds.info_embed(
            f"Solde : {embeds.format_currency(balance)}\nRang : #{rank}",
            title=f"Classement de {member.display_name}",
        )
        await ctx_or_inter.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leaderboard(bot))
