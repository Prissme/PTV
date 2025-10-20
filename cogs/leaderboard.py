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

    @commands.command(name="leaderboard")
    async def leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.database.get_balance_leaderboard(LEADERBOARD_LIMIT)
        embed = embeds.leaderboard_embed(
            title="Classement des plus riches",
            entries=[(row["user_id"], row["balance"]) for row in rows],
            bot=self.bot,
            symbol="PB",
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leaderboard(bot))
