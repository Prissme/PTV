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
        lines = [
            "╔═══════════════════════════════════╗",
            "║    EcoBot - Commandes 📜          ║",
            "╠═══════════════════════════════════╣",
            "║ 💰 ÉCONOMIE                       ║",
            "║ e!balance (bal)  - Voir ton solde ║",
            "║ e!daily          - Récompense 24h ║",
            "║ e!give @user montant - Donner PB  ║",
            "║ e!leaderboard    - Top richesses  ║",
            "║                                   ║",
            "║ 🎖️ GRADES                        ║",
            "║ e!grade          - Ton profil     ║",
            "║ e!gradeleaderboard - Top grades  ║",
            "║                                   ║",
            "║ 🐾 PETS                           ║",
            "║ e!openbox (egg)  - Ouvrir un œuf  ║",
            "║ e!pets (inventory) - Ta collection║",
            "║ e!equip [id]     - Équiper un pet ║",
            "║ e!claim          - Collecter PB   ║",
            "║ e!petstats       - Statistiques   ║",
            "║                                   ║",
            "║ 🤝 ÉCHANGES                       ║",
            "║ e!trade @user    - Échanger       ║",
            "║ e!tradehistory   - Historique     ║",
            "║                                   ║",
            "║ ℹ️ e!help        - Cette aide     ║",
            "╚═══════════════════════════════════╝",
        ]
        description = "\n".join(lines)
        embed = embeds.info_embed(description, title="EcoBot — Aide")
        embed.set_footer(text="Toutes les commandes utilisent le préfixe e!")
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
