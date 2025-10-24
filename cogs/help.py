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
        embed = self._build_help_embed()
        try:
            await ctx.author.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(
                embed=embeds.error_embed(
                    "Je ne peux pas t'envoyer de message privé. Vérifie tes paramètres de confidentialité."
                )
            )
        else:
            if ctx.guild is not None:
                await ctx.send(
                    "La liste des commandes vient de t'être envoyée en message privé !"
                )

    def _build_help_embed(self) -> discord.Embed:
        embed = embeds.info_embed(
            "Bienvenue sur EcoBot ! Toutes les commandes utilisent le préfixe `e!`.",
            title="EcoBot — Aide",
        )

        embed.add_field(
            name="🚀 Bien démarrer",
            value="\n".join(
                (
                    "1️⃣ Commence par **e!daily** pour récupérer ta récompense et lancer ton épargne.",
                    "2️⃣ Ouvre un premier œuf avec **e!openbox** pour obtenir un pet compagnon.",
                    "3️⃣ Équipe ton meilleur pet via **e!equip [id]** puis collecte tes gains avec **e!claim**.",
                    "4️⃣ Suis ta progression et les pets manquants avec **e!index**.",
                )
            ),
            inline=False,
        )

        embed.add_field(
            name="💰 Économie",
            value="\n".join(
                (
                    "**e!balance** (bal) — Consulte ton solde actuel.",
                    "**e!daily** — Collecte ta récompense quotidienne.",
                    "**e!give** @membre montant — Offre des PrissBucks à quelqu'un.",
                    "**e!slots** mise — Tente ta chance à la machine à sous.",
                    "**e!mastermind** — Résous le code secret pour gagner des PB.",
                    "**e!millionairerace** — Prends part à la course millionnaire.",
                )
            ),
            inline=False,
        )

        embed.add_field(
            name="⚔️ Clans",
            value="\n".join(
                (
                    "**e!clan** — Tableau de bord de ton clan et des boosts actifs.",
                    "**e!clan create <nom>** — Fonde un clan (25 000 PB) et lance ta guerre.",
                    "**e!clan join <nom>** — Rejoins un clan existant et profite des boosts.",
                    "**e!clan slots** — Augmente la capacité du clan contre des PB.",
                    "**e!clan boost** — Achète un turbo PB permanent pour tous les membres.",
                )
            ),
            inline=False,
        )

        embed.add_field(
            name="🎖️ Grades",
            value="\n".join(
                (
                    "**e!grade** — Affiche ton profil de grade.",
                    "**e!gradeleaderboard** (gradelb) — Classement des grades.",
                )
            ),
            inline=False,
        )

        embed.add_field(
            name="🐾 Pets",
            value="\n".join(
                (
                    "**e!openbox** [œuf] — Ouvre un œuf pour obtenir un pet.",
                    "**e!eggs** (zones) — Consulte les zones et œufs disponibles.",
                    "**e!pets** (inventory) — Visualise ton inventaire actuel.",
                    "**e!index** (petindex, dex) — Parcours l'index complet et les pets manquants.",
                    "**e!equip** [id] — Équipe un pet pour augmenter tes gains.",
                    "**e!goldify** (gold, fusion) — Fusionne tes pets en version or.",
                    "**e!claim** — Récupère les PB générés par tes pets.",
                    "**e!petstats** — Analyse détaillée de ta collection.",
                )
            ),
            inline=False,
        )

        embed.add_field(
            name="🤝 Échanges",
            value="\n".join(
                (
                    "**e!trade** @membre — Lance un échange sécurisé.",
                    "**e!tradehistory** — Consulte ton historique d'échanges.",
                    "**e!tradestats** — Statistiques globales des échanges.",
                )
            ),
            inline=False,
        )

        embed.add_field(
            name="📊 Classements",
            value="\n".join(
                (
                    "**e!leaderboard** (lb) — Classement des fortunes.",
                    "**e!rapleaderboard** (raplb, rap) — Classement RAP des pets.",
                    "**e!revenusleaderboard** (revenuslb, incomelb, hourlylb) — Classement des revenus horaires.",
                )
            ),
            inline=False,
        )

        embed.set_footer(
            text=(
                "Besoin d'un rappel ? Utilise e!help à tout moment. Astuce : démarre chaque journée avec e!daily !"
            )
        )
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
