"""Commandes de classement économique."""
from __future__ import annotations

from discord.ext import commands

from config import LEADERBOARD_LIMIT
from utils import embeds


class Leaderboard(commands.Cog):
    """Expose le classement économique minimal."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database

    @commands.command(name="leaderboard", aliases=("lb",))
    async def leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.database.get_balance_leaderboard(LEADERBOARD_LIMIT)
        embed = embeds.leaderboard_embed(
            title="Classement des plus riches",
            entries=[(row["user_id"], row["balance"]) for row in rows],
            bot=self.bot,
            symbol="PB",
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
            title="Classement RAP des collectionneurs",
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leaderboard(bot))
