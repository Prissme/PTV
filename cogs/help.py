"""Commande d'aide interactive avec menu déroulant."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple, cast

from dataclasses import dataclass

import discord
from discord.ext import commands

from config import PREFIX
from utils import embeds
from utils.localization import DEFAULT_LANGUAGE, HelpLocaleStrings


def _strip_prefix(value: str) -> str:
    """Retire le préfixe configuré de la commande fournie si présent."""

    value = value.strip()
    if not value:
        return value

    prefix_length = len(PREFIX)
    if value[:prefix_length].lower() == PREFIX.lower():
        return value[prefix_length:].strip()

    return value


def _generate_lookup_variants(value: str) -> tuple[str, ...]:
    """Génère les variantes normalisées permettant de retrouver une commande."""

    base = value.strip()
    if not base:
        return ()

    variants: list[str] = []

    def _add_variant(text: str) -> None:
        normalized = text.strip().lower()
        if normalized and normalized not in variants:
            variants.append(normalized)

    _add_variant(base)

    stripped = _strip_prefix(base)
    if stripped:
        _add_variant(stripped)
        parts = stripped.split()
        if parts:
            _add_variant(parts[0])
            if len(parts) > 1:
                _add_variant(" ".join(parts[:2]))

    return tuple(variants)


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

    def iter_lookup_keys(self) -> tuple[str, ...]:
        """Retourne les clés normalisées permettant d'identifier la commande."""

        variants = list(_generate_lookup_variants(self.command))
        for alias in self.aliases:
            alias_value = alias if alias.startswith(PREFIX) else f"{PREFIX}{alias}"
            variants.extend(_generate_lookup_variants(alias_value))

        seen: set[str] = set()
        unique_variants: list[str] = []
        for variant in variants:
            if variant not in seen:
                seen.add(variant)
                unique_variants.append(variant)

        return tuple(unique_variants)

    def matches(self, query: str) -> bool:
        """Indique si la requête fournie correspond à cette commande."""

        normalized_query = _strip_prefix(query).lower()
        if not normalized_query:
            return False

        for key in self.iter_lookup_keys():
            if normalized_query == key or key.startswith(normalized_query):
                return True

        return False


@dataclass(frozen=True)
class HelpSection:
    """Représente une section d'aide avec ses commandes associées."""

    key: str
    label: str
    description: str
    commands: tuple[HelpCommand, ...]


_HELP_SECTION_BLUEPRINTS: Dict[str, Tuple[dict[str, object], ...]] = {
    "fr": (
        {
            "key": "getting_started",
            "label": "🚀 Bien démarrer",
            "description": "Les étapes essentielles pour lancer ton économie.",
            "commands": (
                {
                    "command": f"{PREFIX}daily",
                    "description": "Commence chaque journée en récupérant ta récompense pour lancer ton épargne.",
                    "icon": "1️⃣",
                },
                {
                    "command": f"{PREFIX}openbox [œuf]",
                    "description": "Ouvre ton premier œuf et débloque un compagnon générateur de PB.",
                    "icon": "2️⃣",
                },
                {
                    "command": f"{PREFIX}equip [id]",
                    "description": "Équipe ton meilleur pet puis récupère tes gains avec la commande claim.",
                    "icon": "3️⃣",
                    "note": f"Pense à utiliser `{PREFIX}claim` pour ramasser tes profits.",
                },
                {
                    "command": f"{PREFIX}index",
                    "description": "Suis l'avancement de ta collection et repère les pets manquants.",
                    "icon": "4️⃣",
                },
            ),
        },
        {
            "key": "economy",
            "label": "💰 Économie",
            "description": "Toutes les commandes liées aux PB et mini-jeux.",
            "commands": (
                {
                    "command": f"{PREFIX}balance",
                    "description": "Consulte ton solde actuel en un clin d'œil.",
                    "aliases": ("bal",),
                },
                {
                    "command": f"{PREFIX}daily",
                    "description": "Collecte ta récompense quotidienne et maintiens ta série.",
                },
                {
                    "command": f"{PREFIX}give @membre montant",
                    "description": "Offre des PrissBucks à un joueur pour soutenir sa progression.",
                },
                {
                    "command": f"{PREFIX}give mortis",
                    "description": "Distribue un Huge Mortis à tous les VIP.",
                    "note": "Commande réservée au propriétaire du bot.",
                },
                {
                    "command": f"{PREFIX}voler @membre",
                    "description": "Tente de voler 75% des PB de la cible (50% de base, +5% par grade, +50% max).",
                },
                {
                    "command": f"{PREFIX}slots mise",
                    "description": "Tente ta chance à la machine à sous et décroche un jackpot.",
                },
                {
                    "command": f"{PREFIX}mastermind",
                    "description": "Résous le code secret pour remporter des PB bonus.",
                },
                {
                    "command": f"{PREFIX}raffle",
                    "description": "Gère tes tickets de tombola, mise-les et suis le prochain tirage.",
                },
                {
                    "command": f"{PREFIX}millionairerace",
                    "description": "Prends part à la course millionnaire et fais exploser ta fortune.",
                },
                {
                    "command": f"{PREFIX}inventory",
                    "description": "Affiche ton inventaire complet (tickets, enchantements, potions, pets).",
                    "aliases": ("inv",),
                },
                {
                    "command": f"{PREFIX}koth",
                    "description": "Conquiers la colline et vise Huge Bo : 1/6000 toutes les 10s, aucun cooldown.",
                },
            ),
        },
        {
            "key": "grades",
            "label": "🎖️ Grades",
            "description": "Progression et classements de grades.",
            "commands": (
                {
                    "command": f"{PREFIX}grade",
                    "description": "Affiche ton profil de grade et tes objectifs en cours.",
                },
                {
                    "command": f"{PREFIX}gradeleaderboard",
                    "description": "Consulte le classement des grades pour mesurer ton avancée.",
                    "aliases": ("gradelb",),
                },
            ),
        },
        {
            "key": "pets",
            "label": "🐾 Pets",
            "description": "Tout pour gérer ton armée de pets.",
            "commands": (
                {
                    "command": f"{PREFIX}openbox [œuf]",
                    "description": "Ouvre un œuf pour obtenir un nouveau pet.",
                },
                {
                    "command": f"{PREFIX}eggs",
                    "description": "Consulte les zones et œufs disponibles.",
                    "aliases": ("zones",),
                },
                {
                    "command": f"{PREFIX}pets",
                    "description": "Visualise ton inventaire actuel.",
                    "aliases": ("inventory",),
                },
                {
                    "command": f"{PREFIX}index",
                    "description": "Parcours l'index complet et repère les pets manquants.",
                    "aliases": ("petindex", "dex"),
                },
                {
                    "command": f"{PREFIX}equip [id]",
                    "description": "Équipe un pet pour augmenter tes gains.",
                },
                {
                    "command": f"{PREFIX}equipbest",
                    "description": "Équipe automatiquement tes pets les plus rentables.",
                    "aliases": ("bestpets", "autoequip"),
                },
                {
                    "command": f"{PREFIX}gemshop",
                    "description": "Achète des slots d'équipement supplémentaires contre des gemmes.",
                },
                {
                    "command": f"{PREFIX}distributeur",
                    "description": "Récupère une potion et un pet doré à Mexico (cooldown de 10 min).",
                    "aliases": ("mexico",),
                },
                {
                    "command": f"{PREFIX}goldify",
                    "description": "Fusionne tes pets en version or pour booster leur puissance.",
                    "aliases": ("gold", "fusion"),
                },
                {
                    "command": f"{PREFIX}galaxy <pet>",
                    "description": "Fusionne 100 rainbow en une variante galaxy 25× plus puissante.",
                },
                {
                    "command": f"{PREFIX}claim",
                    "description": "Récupère les PB générés par tes pets.",
                },
                {
                    "command": f"{PREFIX}enchants",
                    "description": "Affiche tes enchantements, équipe-les et surveille tes slots disponibles.",
                },
            ),
        },
        {
            "key": "stands",
            "label": "🏬 Plaza",
            "description": "Installe ton stand et parcours la plaza.",
            "commands": (
                {
                    "command": f"{PREFIX}stand [@membre]",
                    "description": "Affiche ton stand ou celui d'un autre joueur.",
                },
                {
                    "command": f"{PREFIX}stand add <prix> <pet>",
                    "description": "Met un pet en vente sur ton stand.",
                },
                {
                    "command": f"{PREFIX}stand remove <id>",
                    "description": "Retire une annonce active.",
                },
                {
                    "command": f"{PREFIX}stand buy <id>",
                    "description": "Achète un pet depuis un stand.",
                },
                {
                    "command": f"{PREFIX}stand history",
                    "description": "Consulte l'historique de tes ventes et achats.",
                },
                {
                    "command": f"{PREFIX}plaza",
                    "description": "Vue d'ensemble des stands actifs.",
                },
            ),
        },
        {
            "key": "leaderboards",
            "label": "📊 Classements",
            "description": "Accède aux différents classements économiques.",
            "commands": (
                {
                    "command": f"{PREFIX}leaderboard",
                    "description": "Classement général des fortunes.",
                    "aliases": ("lb",),
                },
                {
                    "command": f"{PREFIX}rapleaderboard",
                    "description": "Classement RAP des pets.",
                    "aliases": ("raplb", "rap"),
                },
                {
                    "command": f"{PREFIX}revenusleaderboard",
                    "description": "Classement des revenus horaires.",
                    "aliases": ("revenuslb", "incomelb", "hourlylb"),
                },
            ),
        },
    ),
    "en": (
        {
            "key": "getting_started",
            "label": "🚀 Getting started",
            "description": "The essential steps to kick off your economy.",
            "commands": (
                {
                    "command": f"{PREFIX}daily",
                    "description": "Start every day by claiming your reward to kick-start your savings.",
                    "icon": "1️⃣",
                },
                {
                    "command": f"{PREFIX}openbox [egg]",
                    "description": "Open your first egg and unlock a pet that generates PrissBucks.",
                    "icon": "2️⃣",
                },
                {
                    "command": f"{PREFIX}equip [id]",
                    "description": "Equip your best pet and then collect your income with the claim command.",
                    "icon": "3️⃣",
                    "note": f"Remember to use `{PREFIX}claim` to scoop up your profits.",
                },
                {
                    "command": f"{PREFIX}index",
                    "description": "Track your collection progress and spot the missing pets.",
                    "icon": "4️⃣",
                },
            ),
        },
        {
            "key": "economy",
            "label": "💰 Economy",
            "description": "All commands related to PrissBucks and mini-games.",
            "commands": (
                {
                    "command": f"{PREFIX}balance",
                    "description": "Check your current balance at a glance.",
                    "aliases": ("bal",),
                },
                {
                    "command": f"{PREFIX}daily",
                    "description": "Collect your daily reward and keep your streak alive.",
                },
                {
                    "command": f"{PREFIX}give @member amount",
                    "description": "Gift PrissBucks to another player to support their progress.",
                },
                {
                    "command": f"{PREFIX}give mortis",
                    "description": "Distribute a Huge Mortis to every VIP.",
                    "note": "Command reserved to the bot owner.",
                },
                {
                    "command": f"{PREFIX}voler @member",
                    "description": "50% base chance to steal 75% of someone else's PB, +5% per grade (up to +50%).",
                },
                {
                    "command": f"{PREFIX}slots bet",
                    "description": "Spin the slot machine and aim for a jackpot.",
                },
                {
                    "command": f"{PREFIX}mastermind",
                    "description": "Solve the secret code to earn bonus PB.",
                },
                {
                    "command": f"{PREFIX}raffle",
                    "description": "Stake your raffle tickets, choose the amount, and follow the next draw.",
                },
                {
                    "command": f"{PREFIX}millionairerace",
                    "description": "Join the Millionaire Race and skyrocket your fortune.",
                },
                {
                    "command": f"{PREFIX}inventory",
                    "description": "Open an interactive view of all your items (tickets, enchants, potions, pets).",
                    "aliases": ("inv",),
                },
                {
                    "command": f"{PREFIX}koth",
                    "description": "Claim the hill and chase Huge Bo: 1/6000 chance every 10s, no cooldown.",
                },
            ),
        },
        {
            "key": "grades",
            "label": "🎖️ Grades",
            "description": "Progression and grade leaderboards.",
            "commands": (
                {
                    "command": f"{PREFIX}grade",
                    "description": "Display your grade profile and current objectives.",
                },
                {
                    "command": f"{PREFIX}gradeleaderboard",
                    "description": "Check the grade leaderboard to measure your progress.",
                    "aliases": ("gradelb",),
                },
            ),
        },
        {
            "key": "pets",
            "label": "🐾 Pets",
            "description": "Everything you need to manage your pet army.",
            "commands": (
                {
                    "command": f"{PREFIX}openbox [egg]",
                    "description": "Open an egg to obtain a new pet.",
                },
                {
                    "command": f"{PREFIX}eggs",
                    "description": "List all available zones and eggs.",
                    "aliases": ("zones",),
                },
                {
                    "command": f"{PREFIX}pets",
                    "description": "View your current inventory.",
                    "aliases": ("inventory",),
                },
                {
                    "command": f"{PREFIX}index",
                    "description": "Browse the complete index and identify missing pets.",
                    "aliases": ("petindex", "dex"),
                },
                {
                    "command": f"{PREFIX}equip [id]",
                    "description": "Equip a pet to increase your income.",
                },
                {
                    "command": f"{PREFIX}equipbest",
                    "description": "Automatically equip your most profitable pets.",
                    "aliases": ("bestpets", "autoequip"),
                },
                {
                    "command": f"{PREFIX}goldify",
                    "description": "Fuse your pets into their golden version to boost their power.",
                    "aliases": ("gold", "fusion"),
                },
                {
                    "command": f"{PREFIX}galaxy <pet>",
                    "description": "Fuse 100 rainbow copies into a galaxy variant that's 25× stronger.",
                },
                {
                    "command": f"{PREFIX}claim",
                    "description": "Collect the PB generated by your pets.",
                },
                {
                    "command": f"{PREFIX}enchants",
                    "description": "Manage your enchantment inventory, equip them, and track your available slots.",
                },
            ),
        },
        {
            "key": "stands",
            "label": "🏬 Plaza",
            "description": "Set up your booth and explore the plaza.",
            "commands": (
                {
                    "command": f"{PREFIX}stand [@member]",
                    "description": "Display your booth or another player's booth.",
                },
                {
                    "command": f"{PREFIX}stand add <price> <pet>",
                    "description": "List a pet for sale on your booth.",
                },
                {
                    "command": f"{PREFIX}stand remove <id>",
                    "description": "Remove an active listing.",
                },
                {
                    "command": f"{PREFIX}stand buy <id>",
                    "description": "Purchase a pet from a booth.",
                },
                {
                    "command": f"{PREFIX}stand history",
                    "description": "Review the history of your sales and purchases.",
                },
                {
                    "command": f"{PREFIX}plaza",
                    "description": "Overview of all active booths.",
                },
            ),
        },
        {
            "key": "leaderboards",
            "label": "📊 Leaderboards",
            "description": "Access the different economic leaderboards.",
            "commands": (
                {
                    "command": f"{PREFIX}leaderboard",
                    "description": "Overall fortune ranking.",
                    "aliases": ("lb",),
                },
                {
                    "command": f"{PREFIX}rapleaderboard",
                    "description": "RAP leaderboard for pets.",
                    "aliases": ("raplb", "rap"),
                },
                {
                    "command": f"{PREFIX}revenusleaderboard",
                    "description": "Hourly income leaderboard.",
                    "aliases": ("revenuslb", "incomelb", "hourlylb"),
                },
            ),
        },
    ),
}


_HELP_STRINGS: Dict[str, HelpLocaleStrings] = {
    "fr": HelpLocaleStrings(
        menu_placeholder="🌟 Choisis la catégorie qui t'intéresse…",
        all_option_label="📚 Toutes les commandes",
        all_option_description="Affiche l'ensemble des commandes disponibles.",
        interaction_denied="Seule la personne ayant demandé l'aide peut utiliser ce menu.",
        all_embed_title="EcoBot — Centre d'aide",
        all_embed_description="Sélectionne une catégorie pour filtrer les commandes ou parcoure la liste complète ci-dessous.",
        footer_text="Besoin d'un rappel ? Utilise e!help à tout moment. Astuce : démarre chaque journée avec e!daily !",
        section_embed_title_format="{label} — Commandes",
        command_not_found_title="Commande introuvable",
        command_not_found_body="Aucune commande ne correspond à `{query}`.",
        suggestions_heading="Commandes suggérées",
        command_detail_title_format="{command} — Aide détaillée",
        category_field_name="Catégorie",
        usage_field_name="Utilisation",
        aliases_field_name="Alias",
    ),
    "en": HelpLocaleStrings(
        menu_placeholder="🌟 Pick the category you want to explore…",
        all_option_label="📚 All commands",
        all_option_description="Show every available command.",
        interaction_denied="Only the person who asked for help can use this menu.",
        all_embed_title="EcoBot — Help Center",
        all_embed_description="Select a category to filter commands or browse the complete list below.",
        footer_text="Need a reminder? Use e!help anytime. Tip: start each day with e!daily!",
        section_embed_title_format="{label} — Commands",
        command_not_found_title="Command not found",
        command_not_found_body="No command matches `{query}`.",
        suggestions_heading="Suggested commands",
        command_detail_title_format="{command} — Detailed help",
        category_field_name="Category",
        usage_field_name="Usage",
        aliases_field_name="Aliases",
    ),
}


def _build_sections(language: str) -> tuple[HelpSection, ...]:
    blueprints = _HELP_SECTION_BLUEPRINTS.get(language)
    if blueprints is None:
        blueprints = _HELP_SECTION_BLUEPRINTS[DEFAULT_LANGUAGE]

    sections: list[HelpSection] = []
    for blueprint in blueprints:
        commands: list[HelpCommand] = []
        for command_blueprint in blueprint["commands"]:  # type: ignore[index]
            commands.append(
                HelpCommand(
                    command=command_blueprint["command"],
                    description=command_blueprint["description"],
                    aliases=tuple(command_blueprint.get("aliases", ())),
                    icon=command_blueprint.get("icon", "•"),
                    note=command_blueprint.get("note"),
                )
            )

        sections.append(
            HelpSection(
                key=blueprint["key"],
                label=blueprint["label"],
                description=blueprint["description"],
                commands=tuple(commands),
            )
        )

    return tuple(sections)


def _get_strings(language: str) -> HelpLocaleStrings:
    return _HELP_STRINGS.get(language, _HELP_STRINGS[DEFAULT_LANGUAGE])


class HelpMenuSelect(discord.ui.Select):
    """Menu déroulant permettant de choisir la catégorie d'aide."""

    def __init__(self, help_view: "HelpMenuView") -> None:
        options = [
            discord.SelectOption(
                label=help_view.strings.all_option_label,
                value="all",
                description=help_view.strings.all_option_description,
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
            placeholder=help_view.strings.menu_placeholder,
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
        strings: HelpLocaleStrings,
        build_section_embed: Callable[[HelpSection], discord.Embed],
        build_all_embed: Callable[[], discord.Embed],
        timeout: float = 180,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author = author
        self.section_order: tuple[HelpSection, ...] = tuple(sections)
        self.section_map = {section.key: section for section in sections}
        self.strings = strings
        self._build_section_embed = build_section_embed
        self._build_all_embed = build_all_embed
        self.message: Optional[discord.Message] = None
        self.add_item(HelpMenuSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author.id:
            return True

        await interaction.response.send_message(
            self.strings.interaction_denied,
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

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._sections_cache: Dict[str, tuple[HelpSection, ...]] = {
            language: _build_sections(language) for language in _HELP_SECTION_BLUEPRINTS
        }

    def _get_sections(self, language: str) -> tuple[HelpSection, ...]:
        sections = self._sections_cache.get(language)
        if sections is None:
            sections = _build_sections(language)
            self._sections_cache[language] = sections
        return sections

    def _get_strings_for_language(self, language: str) -> HelpLocaleStrings:
        return _get_strings(language)

    async def _resolve_language(self, user_id: int) -> str:
        language = DEFAULT_LANGUAGE
        getter = getattr(self.bot, "get_user_language", None)
        if callable(getter):
            try:
                language = await getter(user_id)
            except Exception:
                language = DEFAULT_LANGUAGE
        return language

    async def build_overview_embed_for_user(self, user_id: int) -> discord.Embed:
        """Construit l'embed récapitulatif pour l'utilisateur fourni."""

        language = await self._resolve_language(user_id)
        strings = self._get_strings_for_language(language)
        sections = self._get_sections(language)
        return self._build_all_embed(sections, strings)

    @commands.command(name="help")
    async def help_command(self, ctx: commands.Context, *, query: Optional[str] = None) -> None:
        """Envoie un menu déroulant ou le détail d'une commande recherchée."""

        language = await self._resolve_language(ctx.author.id)
        strings = _get_strings(language)
        sections = self._get_sections(language)

        if query:
            result = self._find_command(sections, query)
            if result is not None:
                section, command = result
                embed = self._build_command_detail_embed(section, command, strings)
                await ctx.send(embed=embed)
                return

            embed = embeds.error_embed(
                strings.command_not_found_body.format(query=query),
                title=strings.command_not_found_title,
            )

            suggestions = self._suggest_commands(sections, query)
            if suggestions:
                embed.add_field(
                    name=strings.suggestions_heading,
                    value="\n".join(f"• `{suggestion}`" for suggestion in suggestions),
                    inline=False,
                )

            await ctx.send(embed=embed)
            return

        build_section_embed = lambda section: self._build_section_embed(section, strings)
        build_all_embed = lambda: self._build_all_embed(sections, strings)

        view = HelpMenuView(
            author=ctx.author,
            sections=sections,
            strings=strings,
            build_section_embed=build_section_embed,
            build_all_embed=build_all_embed,
        )
        embed = build_all_embed()
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    def _build_all_embed(
        self, sections: Sequence[HelpSection], strings: HelpLocaleStrings
    ) -> discord.Embed:
        embed = embeds.info_embed(strings.all_embed_description, title=strings.all_embed_title)

        bot_user = self.bot.user
        if bot_user is not None:
            avatar_url = bot_user.display_avatar.url
            embed.set_author(name="EcoBot", icon_url=avatar_url)
            embed.set_thumbnail(url=avatar_url)

        for section in sections:
            formatted_commands = "\n\n".join(
                command.format_line() for command in section.commands
            )
            embed.add_field(name=section.label, value=formatted_commands, inline=False)

        embed.set_footer(text=strings.footer_text)
        return embed

    def _build_section_embed(
        self, section: HelpSection, strings: HelpLocaleStrings
    ) -> discord.Embed:
        embed = embeds.info_embed(
            "\n\n".join(command.format_line() for command in section.commands),
            title=strings.section_embed_title_format.format(label=section.label),
        )

        bot_user = self.bot.user
        if bot_user is not None:
            avatar_url = bot_user.display_avatar.url
            embed.set_author(name=f"EcoBot · {section.label}", icon_url=avatar_url)
            embed.set_thumbnail(url=avatar_url)

        embed.set_footer(text=strings.footer_text)
        return embed

    def _find_command(
        self, sections: Sequence[HelpSection], query: str
    ) -> Optional[tuple[HelpSection, HelpCommand]]:
        """Recherche une commande correspondant à la requête fournie."""

        for section in sections:
            for command in section.commands:
                if command.matches(query):
                    return section, command

        return None

    def _build_command_detail_embed(
        self, section: HelpSection, command: HelpCommand, strings: HelpLocaleStrings
    ) -> discord.Embed:
        """Construit un embed détaillant une commande spécifique."""

        description_lines = [command.description]
        if command.note:
            description_lines.append(f"💡 {command.note}")

        embed = embeds.info_embed(
            "\n\n".join(description_lines),
            title=strings.command_detail_title_format.format(command=command.command),
        )

        embed.add_field(name=strings.category_field_name, value=section.label, inline=False)
        embed.add_field(name=strings.usage_field_name, value=f"`{command.command}`", inline=False)

        if command.aliases:
            alias_display = ", ".join(
                f"`{alias}`" if alias.startswith(PREFIX) else f"`{PREFIX}{alias}`"
                for alias in command.aliases
            )
            embed.add_field(name=strings.aliases_field_name, value=alias_display, inline=False)

        bot_user = self.bot.user
        if bot_user is not None:
            avatar_url = bot_user.display_avatar.url
            embed.set_author(name="EcoBot", icon_url=avatar_url)
            embed.set_thumbnail(url=avatar_url)

        embed.set_footer(text=strings.footer_text)
        return embed

    def _suggest_commands(
        self, sections: Sequence[HelpSection], query: str, *, limit: int = 5
    ) -> list[str]:
        """Propose des commandes proches de la requête fournie."""

        normalized_query = _strip_prefix(query).lower()
        if not normalized_query:
            return []

        suggestions: list[str] = []
        seen: set[str] = set()

        for section in sections:
            for command in section.commands:
                if command.command in seen:
                    continue

                for key in command.iter_lookup_keys():
                    if normalized_query in key and command.command not in seen:
                        suggestions.append(command.command)
                        seen.add(command.command)
                        break

                if len(suggestions) >= limit:
                    return suggestions[:limit]

        return suggestions[:limit]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
