"""Commande d'aide adaptée à la version minimaliste du bot."""
from __future__ import annotations

import discord
from discord.ext import commands

from utils import embeds


class Help(commands.Cog):
    """Affiche un résumé des commandes disponibles."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="help")
    async def help_command(self, ctx: commands.Context) -> None:
        await ctx.send(embed=self._build_help_embed())

    def _build_help_embed(self) -> discord.Embed:
        embed = embeds.info_embed(
            "Commandes disponibles :",
            title="EcoBot — Aide",
        )
        embed.add_field(
            name="Économie",
            value="`e!balance`, `e!daily`, `e!leaderboard`",
            inline=False,
        )
        embed.add_field(
            name="XP",
            value="`e!rank`, `e!xpleaderboard`",
            inline=False,
        )
        embed.set_footer(text="Toutes les commandes utilisent le préfixe e!")
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
