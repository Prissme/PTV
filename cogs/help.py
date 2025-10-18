"""Aide personnalisée."""
from __future__ import annotations

import discord
from discord.ext import commands

from utils import embeds


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="help")
    async def help_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(embed=self._build_help_embed(ctx.guild))

    @commands.hybrid_command(name="help", with_app_command=True, description="Afficher l'aide")
    async def help_hybrid(self, ctx: commands.Context) -> None:
        await ctx.send(embed=self._build_help_embed(ctx.guild))

    def _build_help_embed(self, guild: discord.Guild | None) -> discord.Embed:
        embed = embeds.info_embed(
            "Commandes principales disponibles.",
            title="EcoBot — Aide",
        )
        embed.add_field(
            name="Économie",
            value="`/balance`, `/daily`, `/give`, `e!balance`, `e!give`",
            inline=False,
        )
        embed.add_field(
            name="Banque",
            value="`/bank`, `/deposit`, `/withdraw`, `/publicbank`, `/public_withdraw`",
            inline=False,
        )
        embed.add_field(
            name="Boutique",
            value="`/shop`, `/buy`, `/inventory`",
            inline=False,
        )
        embed.add_field(
            name="Jeux",
            value="`/roulette`, `/rps`, `/steal`",
            inline=False,
        )
        embed.add_field(
            name="Classements",
            value="`/leaderboard`, `/poorest`, `/xpleaderboard`, `/rank`",
            inline=False,
        )
        embed.set_footer(text="Utilise /help pour voir cette aide à tout moment.")
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
