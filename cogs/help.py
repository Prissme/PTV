"""Commande d'aide interactive avec menu déroulant."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, cast

import discord
from discord.ext import commands

from utils import embeds


@dataclass(frozen=True)
class HelpSection:
    """Représente une section d'aide avec ses commandes associées."""

    key: str
    label: str
    description: str
    commands: tuple[str, ...]


class HelpMenuSelect(discord.ui.Select):
    """Menu déroulant permettant de choisir la catégorie d'aide."""

    def __init__(self, help_view: "HelpMenuView") -> None:
        options = [
            discord.SelectOption(
                label="📚 Toutes les commandes",
                value="all",
                description="Affiche l'ensemble des commandes disponibles.",
            )
        ]

        for section in help_view.section_order:
            options.append(
                discord.SelectOption(
                    label=section.label,
                    value=section.key,
                    description=section.description[:100],
                )
            )

        super().__init__(
            placeholder="Choisis une catégorie de commandes…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is None:  # pragma: no cover - defensive
            return

        help_view = cast(HelpMenuView, self.view)
        embed = help_view.get_embed(self.values[0])
        await interaction.response.edit_message(embed=embed, view=help_view)


class HelpMenuView(discord.ui.View):
    """Vue interactive contenant le menu déroulant des commandes."""

    def __init__(
        self,
        *,
        author: discord.abc.User,
        sections: Sequence[HelpSection],
        build_section_embed: Callable[[HelpSection], discord.Embed],
        build_all_embed: Callable[[], discord.Embed],
        timeout: float = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author = author
        self.section_order: tuple[HelpSection, ...] = tuple(sections)
        self.section_map = {section.key: section for section in sections}
        self._build_section_embed = build_section_embed
        self._build_all_embed = build_all_embed
        self.message: Optional[discord.Message] = None
        self.add_item(HelpMenuSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author.id:
            return True

        await interaction.response.send_message(
            "Seule la personne ayant demandé l'aide peut utiliser ce menu.",
            ephemeral=True,
        )
        return False

    def get_embed(self, key: str) -> discord.Embed:
        if key == "all":
            return self._build_all_embed()

        section = self.section_map.get(key)
        if section is None:
            return self._build_all_embed()

        return self._build_section_embed(section)

    async def on_timeout(self) -> None:
        if self.message is None:
            return

        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.disabled = True

        await self.message.edit(view=self)


class Help(commands.Cog):
    """Affiche la liste complète des commandes via un menu interactif."""

    FOOTER_TEXT = (
        "Besoin d'un rappel ? Utilise e!help à tout moment. Astuce : démarre chaque journée avec e!daily !"
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._sections: tuple[HelpSection, ...] = (
            HelpSection(
                key="getting_started",
                label="🚀 Bien démarrer",
                description="Les étapes essentielles pour lancer ton économie.",
                commands=(
                    "1️⃣ Commence par **e!daily** pour récupérer ta récompense et lancer ton épargne.",
                    "2️⃣ Ouvre un premier œuf avec **e!openbox** pour obtenir un pet compagnon.",
                    "3️⃣ Équipe ton meilleur pet via **e!equip [id]** puis collecte tes gains avec **e!claim**.",
                    "4️⃣ Suis ta progression et les pets manquants avec **e!index**.",
                ),
            ),
            HelpSection(
                key="economy",
                label="💰 Économie",
                description="Toutes les commandes liées aux PB et mini-jeux.",
                commands=(
                    "**e!balance** (bal) — Consulte ton solde actuel.",
                    "**e!daily** — Collecte ta récompense quotidienne.",
                    "**e!give** @membre montant — Offre des PrissBucks à quelqu'un.",
                    "**e!slots** mise — Tente ta chance à la machine à sous.",
                    "**e!mastermind** — Résous le code secret pour gagner des PB.",
                    "**e!millionairerace** — Prends part à la course millionnaire.",
                ),
            ),
            HelpSection(
                key="clans",
                label="⚔️ Clans",
                description="Gestion des clans et des boosts communautaires.",
                commands=(
                    "**e!clan** — Tableau de bord de ton clan et des boosts actifs.",
                    "**e!clan create <nom>** — Fonde un clan (25 000 PB) et lance ta guerre.",
                    "**e!clan join <nom>** — Rejoins un clan existant et profite des boosts.",
                    "**e!clan slots** — Augmente la capacité du clan contre des PB.",
                    "**e!clan boost** — Achète un turbo PB permanent pour tous les membres.",
                ),
            ),
            HelpSection(
                key="grades",
                label="🎖️ Grades",
                description="Progression et classements de grades.",
                commands=(
                    "**e!grade** — Affiche ton profil de grade.",
                    "**e!gradeleaderboard** (gradelb) — Classement des grades.",
                ),
            ),
            HelpSection(
                key="pets",
                label="🐾 Pets",
                description="Tout pour gérer ton armée de pets.",
                commands=(
                    "**e!openbox** [œuf] — Ouvre un œuf pour obtenir un pet.",
                    "**e!eggs** (zones) — Consulte les zones et œufs disponibles.",
                    "**e!pets** (inventory) — Visualise ton inventaire actuel.",
                    "**e!index** (petindex, dex) — Parcours l'index complet et les pets manquants.",
                    "**e!equip** [id] — Équipe un pet pour augmenter tes gains.",
                    "**e!goldify** (gold, fusion) — Fusionne tes pets en version or.",
                    "**e!claim** — Récupère les PB générés par tes pets.",
                    "**e!petstats** — Analyse détaillée de ta collection.",
                ),
            ),
            HelpSection(
                key="trades",
                label="🤝 Échanges",
                description="Commandes liées aux échanges sécurisés.",
                commands=(
                    "**e!trade** @membre — Lance un échange sécurisé.",
                    "**e!tradehistory** — Consulte ton historique d'échanges.",
                    "**e!tradestats** — Statistiques globales des échanges.",
                ),
            ),
            HelpSection(
                key="leaderboards",
                label="📊 Classements",
                description="Accède aux différents classements économiques.",
                commands=(
                    "**e!leaderboard** (lb) — Classement des fortunes.",
                    "**e!rapleaderboard** (raplb, rap) — Classement RAP des pets.",
                    "**e!revenusleaderboard** (revenuslb, incomelb, hourlylb) — Classement des revenus horaires.",
                ),
            ),
        )

    @commands.command(name="help")
    async def help_command(self, ctx: commands.Context) -> None:
        """Envoie un menu déroulant répertoriant toutes les commandes."""

        view = HelpMenuView(
            author=ctx.author,
            sections=self._sections,
            build_section_embed=self._build_section_embed,
            build_all_embed=self._build_all_embed,
        )
        embed = self._build_all_embed()
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    def _build_all_embed(self) -> discord.Embed:
        embed = embeds.info_embed(
            "Sélectionne une catégorie pour filtrer les commandes ou parcoure la liste complète ci-dessous.",
            title="EcoBot — Aide",
        )
        for section in self._sections:
            embed.add_field(
                name=section.label,
                value="\n".join(section.commands),
                inline=False,
            )
        embed.set_footer(text=self.FOOTER_TEXT)
        return embed

    def _build_section_embed(self, section: HelpSection) -> discord.Embed:
        embed = embeds.info_embed(
            "\n".join(section.commands),
            title=f"{section.label} — Commandes",
        )
        embed.set_footer(text=self.FOOTER_TEXT)
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
