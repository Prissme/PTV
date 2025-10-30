"""Commande d'aide interactive avec menu déroulant."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, cast

import discord
from discord.ext import commands

from config import PREFIX
from utils import embeds


@dataclass(frozen=True)
class HelpCommand:
    """Représente une commande individuelle affichée dans le menu d'aide."""

    command: str
    description: str
    aliases: tuple[str, ...] = ()
    icon: str = "•"
    note: Optional[str] = None

    def format_line(self) -> str:
        """Retourne une ligne mise en forme pour un embed Discord."""

        alias_text = ""
        if self.aliases:
            formatted_aliases = ", ".join(
                f"`{alias}`" if alias.startswith(PREFIX) else f"`{PREFIX}{alias}`"
                for alias in self.aliases
            )
            alias_text = f" · {formatted_aliases}"

        header = f"{self.icon} **{self.command}**{alias_text}"
        body_lines = [f"> {self.description}"]
        if self.note:
            body_lines.append(f"> {self.note}")

        return "\n".join([header, *body_lines])


@dataclass(frozen=True)
class HelpSection:
    """Représente une section d'aide avec ses commandes associées."""

    key: str
    label: str
    description: str
    commands: tuple[HelpCommand, ...]


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
            placeholder="🌟 Choisis la catégorie qui t'intéresse…",
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
                    HelpCommand(
                        f"{PREFIX}daily",
                        "Commence chaque journée en récupérant ta récompense pour lancer ton épargne.",
                        icon="1️⃣",
                    ),
                    HelpCommand(
                        f"{PREFIX}openbox [œuf]",
                        "Ouvre ton premier œuf et débloque un compagnon générateur de PB.",
                        icon="2️⃣",
                    ),
                    HelpCommand(
                        f"{PREFIX}equip [id]",
                        "Équipe ton meilleur pet puis récupère tes gains avec la commande claim.",
                        icon="3️⃣",
                        note=f"Pense à utiliser `{PREFIX}claim` pour ramasser tes profits.",
                    ),
                    HelpCommand(
                        f"{PREFIX}index",
                        "Suis l'avancement de ta collection et repère les pets manquants.",
                        icon="4️⃣",
                    ),
                ),
            ),
            HelpSection(
                key="economy",
                label="💰 Économie",
                description="Toutes les commandes liées aux PB et mini-jeux.",
                commands=(
                    HelpCommand(
                        f"{PREFIX}balance",
                        "Consulte ton solde actuel en un clin d'œil.",
                        aliases=("bal",),
                    ),
                    HelpCommand(
                        f"{PREFIX}daily",
                        "Collecte ta récompense quotidienne et maintiens ta série.",
                    ),
                    HelpCommand(
                        f"{PREFIX}give @membre montant",
                        "Offre des PrissBucks à un joueur pour soutenir sa progression.",
                    ),
                    HelpCommand(
                        f"{PREFIX}give mortis",
                        "Distribue un Huge Mortis à tous les VIP.",
                        note="Commande réservée au propriétaire du bot.",
                    ),
                    HelpCommand(
                        f"{PREFIX}slots mise",
                        "Tente ta chance à la machine à sous et décroche un jackpot.",
                    ),
                    HelpCommand(
                        f"{PREFIX}mastermind",
                        "Résous le code secret pour remporter des PB bonus.",
                    ),
                    HelpCommand(
                        f"{PREFIX}millionairerace",
                        "Prends part à la course millionnaire et fais exploser ta fortune.",
                    ),
                ),
            ),
            HelpSection(
                key="clans",
                label="⚔️ Clans",
                description="Gestion des clans et des boosts communautaires.",
                commands=(
                    HelpCommand(
                        f"{PREFIX}clan",
                        "Affiche le tableau de bord de ton clan et ses boosts actifs.",
                    ),
                    HelpCommand(
                        f"{PREFIX}clan create <nom>",
                        "Fonde un clan (25 000 PB) pour lancer ta propre alliance.",
                    ),
                    HelpCommand(
                        f"{PREFIX}clan join <nom>",
                        "Rejoins un clan existant et profite de ses avantages.",
                    ),
                    HelpCommand(
                        f"{PREFIX}clan slots",
                        "Augmente la capacité maximale de ton clan contre des PB.",
                    ),
                    HelpCommand(
                        f"{PREFIX}clan boost",
                        "Achète un turbo PB permanent bénéfique à tous les membres.",
                    ),
                ),
            ),
            HelpSection(
                key="grades",
                label="🎖️ Grades",
                description="Progression et classements de grades.",
                commands=(
                    HelpCommand(
                        f"{PREFIX}grade",
                        "Affiche ton profil de grade et tes objectifs en cours.",
                    ),
                    HelpCommand(
                        f"{PREFIX}gradeleaderboard",
                        "Consulte le classement des grades pour mesurer ton avancée.",
                        aliases=("gradelb",),
                    ),
                ),
            ),
            HelpSection(
                key="pets",
                label="🐾 Pets",
                description="Tout pour gérer ton armée de pets.",
                commands=(
                    HelpCommand(
                        f"{PREFIX}openbox [œuf]",
                        "Ouvre un œuf pour obtenir un nouveau pet.",
                    ),
                    HelpCommand(
                        f"{PREFIX}eggs",
                        "Consulte les zones et œufs disponibles.",
                        aliases=("zones",),
                    ),
                    HelpCommand(
                        f"{PREFIX}pets",
                        "Visualise ton inventaire actuel.",
                        aliases=("inventory",),
                    ),
                    HelpCommand(
                        f"{PREFIX}index",
                        "Parcours l'index complet et repère les pets manquants.",
                        aliases=("petindex", "dex"),
                    ),
                    HelpCommand(
                        f"{PREFIX}equip [id]",
                        "Équipe un pet pour augmenter tes gains.",
                    ),
                    HelpCommand(
                        f"{PREFIX}equipbest",
                        "Équipe automatiquement tes pets les plus rentables.",
                        aliases=("bestpets", "autoequip"),
                    ),
                    HelpCommand(
                        f"{PREFIX}goldify",
                        "Fusionne tes pets en version or pour booster leur puissance.",
                        aliases=("gold", "fusion"),
                    ),
                    HelpCommand(
                        f"{PREFIX}claim",
                        "Récupère les PB générés par tes pets.",
                    ),
                ),
            ),
            HelpSection(
                key="stands",
                label="🏬 Plaza",
                description="Installe ton stand et parcours la plaza.",
                commands=(
                    HelpCommand(
                        f"{PREFIX}stand [@membre]",
                        "Affiche ton stand ou celui d'un autre joueur.",
                    ),
                    HelpCommand(
                        f"{PREFIX}stand add <prix> <pet>",
                        "Met un pet en vente sur ton stand.",
                    ),
                    HelpCommand(
                        f"{PREFIX}stand remove <id>",
                        "Retire une annonce active.",
                    ),
                    HelpCommand(
                        f"{PREFIX}stand buy <id>",
                        "Achète un pet depuis un stand.",
                    ),
                    HelpCommand(
                        f"{PREFIX}stand history",
                        "Consulte l'historique de tes ventes et achats.",
                    ),
                    HelpCommand(
                        f"{PREFIX}plaza",
                        "Vue d'ensemble des stands actifs.",
                    ),
                ),
            ),
            HelpSection(
                key="leaderboards",
                label="📊 Classements",
                description="Accède aux différents classements économiques.",
                commands=(
                    HelpCommand(
                        f"{PREFIX}leaderboard",
                        "Classement général des fortunes.",
                        aliases=("lb",),
                    ),
                    HelpCommand(
                        f"{PREFIX}rapleaderboard",
                        "Classement RAP des pets.",
                        aliases=("raplb", "rap"),
                    ),
                    HelpCommand(
                        f"{PREFIX}revenusleaderboard",
                        "Classement des revenus horaires.",
                        aliases=("revenuslb", "incomelb", "hourlylb"),
                    ),
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
            title="EcoBot — Centre d'aide",
        )

        bot_user = self.bot.user
        if bot_user is not None:
            avatar_url = bot_user.display_avatar.url
            embed.set_author(name="EcoBot", icon_url=avatar_url)
            embed.set_thumbnail(url=avatar_url)

        for section in self._sections:
            formatted_commands = "\n\n".join(command.format_line() for command in section.commands)
            embed.add_field(name=section.label, value=formatted_commands, inline=False)

        embed.set_footer(text=self.FOOTER_TEXT)
        return embed

    def _build_section_embed(self, section: HelpSection) -> discord.Embed:
        embed = embeds.info_embed(
            "\n\n".join(command.format_line() for command in section.commands),
            title=f"{section.label} — Commandes",
        )

        bot_user = self.bot.user
        if bot_user is not None:
            avatar_url = bot_user.display_avatar.url
            embed.set_author(name=f"EcoBot · {section.label}", icon_url=avatar_url)
            embed.set_thumbnail(url=avatar_url)

        embed.set_footer(text=self.FOOTER_TEXT)
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
