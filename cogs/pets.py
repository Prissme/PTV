"""Système d'ouverture d'œufs et de gestion des pets Brawl Stars."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

import discord
from discord.ext import commands

from config import (
    BASE_PET_SLOTS,
    MEXICO_ZONE_SLUG,
    DEFAULT_PET_EGG_SLUG,
    GOLD_PET_CHANCE,
    GOLD_PET_COMBINE_REQUIRED,
    GOLD_PET_MULTIPLIER,
    GRADE_DEFINITIONS,
    EGG_MASTERY_MAX_ROLE_ID,
    PET_MASTERY_MAX_ROLE_ID,
    PET_DEFINITIONS,
    PET_EGG_DEFINITIONS,
    PET_RARITY_ORDER,
    PET_ZONES,
    POTION_DEFINITIONS,
    EGG_FRENZY_LUCK_BONUS,
    get_egg_frenzy_window,
    is_egg_frenzy_active,
    RAINBOW_PET_CHANCE,
    RAINBOW_PET_COMBINE_REQUIRED,
    RAINBOW_PET_MULTIPLIER,
    GALAXY_PET_COMBINE_REQUIRED,
    GALAXY_PET_MULTIPLIER,
    PET_EMOJIS,
    SHINY_PET_MULTIPLIER,
    PET_SLOT_MAX_CAPACITY,
    PET_SLOT_SHOP_BASE_COST,
    PET_SLOT_SHOP_COST_GROWTH,
    PET_SLOT_SHOP_CURRENCY,
    MEXICO_DISTRIBUTOR_COOLDOWN,
    HUGE_PET_LEVEL_CAP,
    HUGE_PET_NAME,
    HUGE_PET_MIN_INCOME,
    HUGE_PET_NAMES,
    HUGE_PET_SOURCES,
    HUGE_BULL_NAME,
    HUGE_GALE_NAME,
    HUGE_GRIFF_NAME,
    HUGE_KENJI_ONI_NAME,
    HUGE_MORTIS_NAME,
    TITANIC_GRIFF_NAME,
    PotionDefinition,
    compute_huge_income,
    get_huge_level_multiplier,
    get_huge_level_progress,
    huge_level_required_xp,
    PetDefinition,
    PetEggDefinition,
    PetZoneDefinition,
)
from utils import embeds
from utils.pet_formatting import pet_emoji
from cogs.economy import (
    CASINO_HUGE_CHANCE_PER_PB,
    CASINO_HUGE_MAX_CHANCE,
    CASINO_TITANIC_CHANCE_PER_PB,
    CASINO_TITANIC_MAX_CHANCE,
    MASTERMIND_HUGE_MAX_CHANCE,
    MASTERMIND_HUGE_MIN_CHANCE,
)
from utils.mastery import (
    EGG_MASTERY,
    PET_MASTERY,
    MASTERMIND_MASTERY,
    MasteryDefinition,
    iter_masteries,
)
from database.db import ActivePetLimitError, DatabaseError, InsufficientBalanceError
from utils.enchantments import (
    compute_egg_luck_bonus,
    get_source_label,
    pick_random_enchantment,
    roll_enchantment_power,
    should_drop_enchantment,
    format_enchantment,
)

logger = logging.getLogger(__name__)


HUGE_SHELLY_ALERT_CHANNEL_ID = 1236724293631611022
EGG_OPEN_EMOJI = "<:Egg:1433411763944296518>"
FALLBACK_EGG_EMOJI = "🐣"

HUGE_GOLD_CHANCE = 0.1
HUGE_RAINBOW_CHANCE = 0.01
HUGE_SHINY_CHANCE = 0.004
HUGE_GALAXY_CHANCE = 0.0001



@dataclass(frozen=True)
class EggMasteryPerks:
    """Regroupe les bonus associés à la maîtrise des œufs."""

    double_chance: float = 0.0
    triple_chance: float = 0.0
    gold_chance: float = 0.0
    rainbow_chance: float = 0.0
    animation_speed: float = 1.0
    luck_bonus: float = 0.0


def _compute_egg_mastery_perks(level: int) -> EggMasteryPerks:
    """Calcule les bonus actifs pour un niveau donné de maîtrise des œufs."""

    double_chance = 0.0
    triple_chance = 0.0
    gold_chance = 0.0
    rainbow_chance = 0.0
    animation_speed = 1.0
    luck_bonus = 0.0

    if level >= 5:
        double_chance = 0.05
    if level >= 10:
        gold_chance = 0.03
    if level >= 20:
        rainbow_chance = 0.01
        animation_speed = 2.0
    if level >= 30:
        double_chance = 0.15
        triple_chance = 0.01
    if level >= 40:
        double_chance = 0.20
        triple_chance = 0.03
        gold_chance = 0.05
        rainbow_chance = 0.02
    if level >= 50:
        double_chance = 0.35
        triple_chance = 0.10
        gold_chance = 0.10
        rainbow_chance = 0.04
    if level >= 64:
        luck_bonus = 1.0

    return EggMasteryPerks(
        double_chance=double_chance,
        triple_chance=triple_chance,
        gold_chance=gold_chance,
        rainbow_chance=rainbow_chance,
        animation_speed=animation_speed,
        luck_bonus=luck_bonus,
    )


@dataclass(frozen=True)
class PetMasteryPerks:
    """Synthétise les bonus associés à la maîtrise des pets."""

    fuse_unlocked: bool = False
    auto_goldify: bool = False
    auto_rainbowify: bool = False
    egg_shiny_chance: float = 0.0
    goldify_shiny_chance: float = 0.0
    rainbowify_shiny_chance: float = 0.0
    fuse_double_chance: float = 0.0
    fuse_triple_chance: float = 0.0
    egg_shiny_multiplier: float = 1.0
    gold_luck_multiplier: float = 1.0
    rainbow_luck_multiplier: float = 1.0


@dataclass(frozen=True)
class GemshopState:
    """Représente l'état actuel du magasin de slots de pets pour un joueur."""

    grade_level: int
    base_capacity: int
    extra_slots: int
    hard_cap: int
    total_slots: int
    max_extra_allowed: int
    next_cost: int | None

    @property
    def has_reached_hard_cap(self) -> bool:
        return self.total_slots >= self.hard_cap

    @property
    def can_purchase(self) -> bool:
        return (
            self.max_extra_allowed > 0
            and self.extra_slots < self.max_extra_allowed
            and not self.has_reached_hard_cap
        )


@dataclass(frozen=True)
class GemshopPurchaseResult:
    """Résultat d'une tentative d'achat dans le gemshop."""

    embed: discord.Embed
    state: GemshopState
    success: bool


@dataclass(frozen=True)
class MasteryTier:
    level: int
    title: str
    description: str


_EGG_MASTERY_TIERS: tuple[MasteryTier, ...] = (
    MasteryTier(5, "Ouverture double", "5% de chance supplémentaire d'ouvrir un œuf bonus."),
    MasteryTier(10, "Reflets dorés", "+3% de chance qu'un œuf devienne gold."),
    MasteryTier(
        20,
        "Arc-en-ciel express",
        "Animations deux fois plus rapides et +1% de chance d'œuf rainbow.",
    ),
    MasteryTier(
        30,
        "Session frénétique",
        "15% de doubles ouvertures et 1% de triples coups d'œil.",
    ),
    MasteryTier(
        40,
        "Run légendaire",
        "20% de doubles, 3% de triples, +5% or et +2% rainbow.",
    ),
    MasteryTier(
        50,
        "Jackpot permanent",
        "35% de doubles, 10% de triples, +10% or et +4% rainbow.",
    ),
    MasteryTier(64, "Instinct cosmique", "+1.0 de luck constant sur tous les œufs."),
)

_PET_MASTERY_TIERS: tuple[MasteryTier, ...] = (
    MasteryTier(
        5,
        "Atelier fusion",
        "Débloque la fusion, l'auto goldify et +1% de shiny via les œufs.",
    ),
    MasteryTier(10, "Artisan patient", "10% de chance de fusion double."),
    MasteryTier(
        20,
        "Forge colorée",
        "+3% de shiny sur les goldifies et +1% sur les rainbowifies.",
    ),
    MasteryTier(
        30,
        "Orfèvre spectral",
        "Active l'auto rainbowify et booste la chance shiny des œufs à 3%.",
    ),
    MasteryTier(
        40,
        "Fusions maîtrisées",
        "35% de doubles fusions et 10% de triples réussies.",
    ),
    MasteryTier(
        50,
        "Légende des altérations",
        "50% de doubles, +5% shiny œufs et goldify, +3% shiny rainbowify.",
    ),
    MasteryTier(
        64,
        "Génie du polissage",
        "Shiny œufs x1.2, luck gold x1.5 et luck rainbow x1.3.",
    ),
)

_MASTERMIND_TIERS: tuple[MasteryTier, ...] = (
    MasteryTier(5, "Échauffement", "Récompenses Mastermind doublées."),
    MasteryTier(10, "Mentaliste", "Total x8 et potions qui tombent deux fois plus."),
    MasteryTier(
        20,
        "Visionnaire",
        "Total x16 et une couleur en moins à deviner.",
    ),
    MasteryTier(30, "Chasseur d'Oni", "Chance de Kenji Oni doublée."),
    MasteryTier(40, "Architecte", "Total x64 après chaque victoire."),
    MasteryTier(50, "Maître absolu", "Total x256 sur les gains Mastermind."),
    MasteryTier(64, "Grand stratège", "Deux couleurs en moins et rôle ultime."),
)

_MASTERY_TIERS: dict[str, tuple[MasteryTier, ...]] = {
    EGG_MASTERY.slug: _EGG_MASTERY_TIERS,
    PET_MASTERY.slug: _PET_MASTERY_TIERS,
    MASTERMIND_MASTERY.slug: _MASTERMIND_TIERS,
}


class GemshopView(discord.ui.View):
    """Affiche le gemshop avec un bouton d'achat interactif."""

    def __init__(self, cog: "Pets", ctx: commands.Context, state: GemshopState) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self.state = state
        self.message: discord.Message | None = None
        self._lock = asyncio.Lock()
        self._refresh_buttons()

    def attach_message(self, message: discord.Message) -> None:
        self.message = message

    def _refresh_buttons(self) -> None:
        label = "Acheter un slot"
        if self.state.next_cost is not None:
            label = f"Acheter un slot ({self.cog._format_slot_cost(self.state.next_cost)})"
        self.buy_slot.label = label
        self.buy_slot.disabled = not self.state.can_purchase

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Seul l'acheteur initial peut utiliser ce magasin.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        self.buy_slot.disabled = True
        if self.message is None:
            return
        with contextlib.suppress(discord.HTTPException):
            await self.message.edit(view=self)

    @discord.ui.button(
        label="Acheter un slot",
        style=discord.ButtonStyle.green,
        custom_id="gemshop:buy_slot",
    )
    async def buy_slot(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        del button
        async with self._lock:
            await interaction.response.defer(ephemeral=True, thinking=False)
            result = await self.cog._attempt_gemshop_purchase(interaction.user)
            self.state = result.state
            self._refresh_buttons()
            updated_embed = self.cog._render_gemshop_embed(
                self.ctx.author, self.state
            )
            if self.message is not None:
                with contextlib.suppress(discord.HTTPException):
                    await self.message.edit(embed=updated_embed, view=self)
            await interaction.followup.send(embed=result.embed, ephemeral=True)
        

def _compute_pet_mastery_perks(level: int) -> PetMasteryPerks:
    """Calcule les bonus actifs pour la maîtrise des pets."""

    fuse_unlocked = level >= 5
    auto_goldify = level >= 5
    auto_rainbowify = level >= 30
    egg_shiny_chance = 0.01 if level >= 5 else 0.0
    goldify_shiny_chance = 0.03 if level >= 20 else 0.0
    rainbowify_shiny_chance = 0.01 if level >= 20 else 0.0
    fuse_double_chance = 0.10 if level >= 10 else 0.0
    fuse_triple_chance = 0.0
    egg_shiny_multiplier = 1.0
    gold_luck_multiplier = 1.0
    rainbow_luck_multiplier = 1.0

    if level >= 30:
        egg_shiny_chance = 0.03
    if level >= 40:
        fuse_double_chance = 0.35
        fuse_triple_chance = 0.10
    if level >= 50:
        fuse_double_chance = 0.50
        egg_shiny_chance = 0.05
        goldify_shiny_chance = 0.05
        rainbowify_shiny_chance = 0.03
    if level >= 64:
        egg_shiny_multiplier = 1.2
        gold_luck_multiplier = 1.5
        rainbow_luck_multiplier = 1.3

    return PetMasteryPerks(
        fuse_unlocked=fuse_unlocked,
        auto_goldify=auto_goldify,
        auto_rainbowify=auto_rainbowify,
        egg_shiny_chance=egg_shiny_chance,
        goldify_shiny_chance=goldify_shiny_chance,
        rainbowify_shiny_chance=rainbowify_shiny_chance,
        fuse_double_chance=fuse_double_chance,
        fuse_triple_chance=fuse_triple_chance,
        egg_shiny_multiplier=egg_shiny_multiplier,
        gold_luck_multiplier=gold_luck_multiplier,
        rainbow_luck_multiplier=rainbow_luck_multiplier,
    )


@dataclass
class PetHatchResult:
    definition: PetDefinition
    income_per_hour: int
    market_value: int | None
    is_gold: bool = False
    is_rainbow: bool = False
    is_galaxy: bool = False
    is_shiny: bool = False
    is_huge: bool = False
    auto_messages: List[str] = field(default_factory=list)
    bonus: bool = False
    was_forced_gold: bool = False


class PetInventoryView(discord.ui.View):
    """Interface paginée pour afficher la collection de pets par lots de huit."""

    def __init__(
        self,
        *,
        ctx: commands.Context,
        pets: Iterable[Mapping[str, Any]],
        total_income: int,
        per_page: int = 8,
        huge_descriptions: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(timeout=120)
        self.ctx = ctx
        self.member = ctx.author
        self._pets: List[Dict[str, Any]] = [dict(pet) for pet in pets]
        self._per_page = max(1, per_page)
        self._total_income = int(total_income)
        self._huge_descriptions: Dict[str, str] = dict(huge_descriptions or {})
        self.page_count = max(1, math.ceil(len(self._pets) / self._per_page))
        self.page = 0
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _current_slice(self) -> List[Mapping[str, Any]]:
        if not self._pets:
            return []
        start = self.page * self._per_page
        end = start + self._per_page
        return self._pets[start:end]

    def build_embed(self) -> discord.Embed:
        return embeds.pet_collection_embed(
            member=self.member,
            pets=self._current_slice(),
            total_count=len(self._pets),
            total_income_per_hour=self._total_income,
            page=self.page + 1,
            page_count=self.page_count,
            huge_descriptions=self._huge_descriptions,
        )

    def _sync_buttons(self) -> None:
        has_multiple_pages = self.page_count > 1
        if hasattr(self, "previous_page"):
            self.previous_page.disabled = not has_multiple_pages or self.page <= 0
        if hasattr(self, "next_page"):
            self.next_page.disabled = not has_multiple_pages or self.page >= self.page_count - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Seul le propriétaire de l'inventaire peut utiliser ces boutons.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page > 0:
            self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page < self.page_count - 1:
            self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)


class ZoneOverviewView(discord.ui.View):
    """Interface paginée pour présenter les zones et œufs disponibles."""

    @dataclass
    class PageState:
        embed: discord.Embed
        zone: PetZoneDefinition | None
        has_unlocked: bool
        meets_egg_mastery: bool
        meets_pet_mastery: bool
        meets_rebirth: bool

    def __init__(
        self,
        ctx: commands.Context,
        pages: Sequence[PageState],
        pets_cog: "Pets",
    ) -> None:
        super().__init__(timeout=120)
        self.ctx = ctx
        self.pets_cog = pets_cog
        self._pages: List[ZoneOverviewView.PageState] = [page for page in pages]
        self.page = 0
        self.page_count = max(1, len(self._pages))
        self.message: discord.Message | None = None
        self._sync_buttons()

    def current_embed(self) -> discord.Embed:
        if not self._pages:
            return embeds.info_embed("Aucune zone disponible pour le moment.")
        return self._pages[self.page].embed

    def _current_page(self) -> PageState:
        if not self._pages:
            return ZoneOverviewView.PageState(
                embed=embeds.info_embed("Aucune zone disponible pour le moment."),
                zone=None,
                has_unlocked=False,
                meets_egg_mastery=False,
                meets_pet_mastery=False,
                meets_rebirth=False,
            )
        return self._pages[self.page]

    def _refresh_footer(self) -> None:
        total = self.page_count
        for index, page in enumerate(self._pages, start=1):
            page.embed.set_footer(text=f"Page {index}/{total}")

    def _sync_buttons(self) -> None:
        has_multiple_pages = self.page_count > 1
        if hasattr(self, "previous_page"):
            self.previous_page.disabled = not has_multiple_pages or self.page <= 0
        if hasattr(self, "next_page"):
            self.next_page.disabled = (
                not has_multiple_pages or self.page >= self.page_count - 1
            )
        current = self._current_page()
        can_open = bool(current.zone and current.zone.eggs and current.has_unlocked)
        can_unlock = (
            current.zone is not None
            and current.zone.entry_cost > 0
            and not current.has_unlocked
        )
        if hasattr(self, "open_egg"):
            self.open_egg.disabled = not can_open
        if hasattr(self, "unlock_zone"):
            self.unlock_zone.disabled = not can_unlock

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Seul le propriétaire de la commande peut changer de page.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page > 0:
            self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.page < self.page_count - 1:
            self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(label="Ouvrir l'œuf", style=discord.ButtonStyle.success)
    async def open_egg(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        page = self._current_page()
        if page.zone is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Aucune zone à ouvrir ici."), ephemeral=True
            )
            return
        if not page.zone.eggs:
            await interaction.response.send_message(
                embed=embeds.error_embed("Aucun œuf disponible dans cette zone."),
                ephemeral=True,
            )
            return
        if not page.has_unlocked:
            await interaction.response.send_message(
                embed=embeds.error_embed("Débloque la zone avant d'ouvrir un œuf."),
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        await self.pets_cog._openbox_impl(self.ctx, page.zone.eggs[0].slug)

    @discord.ui.button(label="Débloquer la zone", style=discord.ButtonStyle.primary)
    async def unlock_zone(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        page = self._current_page()
        if page.zone is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Aucune zone à débloquer ici."), ephemeral=True
            )
            return
        if page.has_unlocked or page.zone.entry_cost <= 0:
            await interaction.response.send_message(
                embed=embeds.info_embed("Cette zone est déjà accessible."),
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        unlocked = await self.pets_cog._ensure_zone_access(self.ctx, page.zone)
        if unlocked:
            page.has_unlocked = True
            page.embed = self.pets_cog._build_zone_overview_embed(
                self.ctx,
                page.zone,
                has_unlocked=True,
                meets_egg_mastery=page.meets_egg_mastery,
                meets_pet_mastery=page.meets_pet_mastery,
                meets_rebirth=page.meets_rebirth,
            )
            self._refresh_footer()
            self._sync_buttons()
            if self.message:
                await self.message.edit(embed=self.current_embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)


class HatchReplayView(discord.ui.View):
    def __init__(self, ctx: commands.Context, pets_cog: "Pets", egg_slug: str) -> None:
        super().__init__(timeout=60)
        self.ctx = ctx
        self.pets_cog = pets_cog
        self.egg_slug = egg_slug
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Seul l'acheteur peut relancer l'ouverture.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Encore!", style=discord.ButtonStyle.success)
    async def replay(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        await self.pets_cog._openbox_impl(self.ctx, self.egg_slug)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)


class PetSelectionView(discord.ui.View):
    """Vue interactive permettant de sélectionner un pet parmi plusieurs."""

    def __init__(
        self,
        *,
        ctx: commands.Context,
        candidates: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(timeout=60)
        self.ctx = ctx
        self.member = ctx.author
        self.candidates: List[Mapping[str, Any]] = list(candidates)
        self.selection: Optional[Mapping[str, Any]] = None
        self.cancelled = False
        self.message: discord.Message | None = None

        for index, candidate in enumerate(self.candidates, start=1):
            label = self._build_label(candidate, index)
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            button.callback = self._make_callback(index - 1)
            self.add_item(button)

        cancel_button = discord.ui.Button(label="Annuler", style=discord.ButtonStyle.secondary)
        cancel_button.callback = self._cancel
        self.add_item(cancel_button)

    @staticmethod
    def _build_label(candidate: Mapping[str, Any], index: int) -> str:
        data = candidate.get("data", {})
        name = str(data.get("name", "Pet"))
        is_galaxy = bool(data.get("is_galaxy"))
        is_rainbow = bool(data.get("is_rainbow"))
        is_gold = bool(data.get("is_gold"))
        is_active = bool(data.get("is_active"))
        income = int(data.get("base_income_per_hour", 0))
        if is_galaxy:
            marker = " 🌌"
        elif is_rainbow:
            marker = " 🌈"
        elif is_gold:
            marker = " 🥇"
        else:
            marker = ""
        active_marker = "⭐ " if is_active else ""
        base_label = f"{index}. {active_marker}{name}{marker}"
        income_part = f" • {income:,} PB/h" if income else ""
        label = f"{base_label}{income_part}".replace(",", " ")
        return label[:80]

    def _make_callback(self, index: int):
        async def _callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message(
                    "Tu ne peux pas sélectionner un pet pour quelqu'un d'autre.",
                    ephemeral=True,
                )
                return
            self.selection = self.candidates[index]
            self.disable_all()
            if interaction.response.is_done():
                await interaction.followup.edit_message(interaction.message.id, view=self)
            else:
                await interaction.response.edit_message(view=self)
            self.stop()

        return _callback

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Tu ne peux pas annuler cette sélection.", ephemeral=True
            )
            return
        self.cancelled = True
        self.selection = None
        self.disable_all()
        if interaction.response.is_done():
            await interaction.followup.edit_message(interaction.message.id, view=self)
        else:
            await interaction.response.edit_message(view=self)
        self.stop()

    def disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Tu ne peux pas interagir avec ce menu.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        self.disable_all()
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)


class MasteryDetailButton(discord.ui.Button["MasteryOverviewView"]):
    """Bouton ouvrant un résumé détaillé d'une maîtrise."""

    def __init__(
        self,
        *,
        ctx: commands.Context,
        mastery: MasteryDefinition,
        progress: Mapping[str, object] | None,
        tiers: Sequence[MasteryTier],
    ) -> None:
        super().__init__(label=mastery.display_name, style=discord.ButtonStyle.primary)
        self.ctx = ctx
        self.mastery = mastery
        self.progress: Dict[str, object] = dict(progress or {})
        self._tier_payload = [
            {
                "level": tier.level,
                "title": tier.title,
                "description": tier.description,
            }
            for tier in tiers
        ]

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Seul le propriétaire de la commande peut consulter ces détails.",
                ephemeral=True,
            )
            return
        embed = embeds.mastery_detail_embed(
            member=self.ctx.author,
            mastery=self.mastery,
            progress=self.progress,
            tiers=self._tier_payload,
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


class MasteryOverviewView(discord.ui.View):
    """Vue listant les maîtrises et offrant des boutons de détail."""

    def __init__(
        self,
        ctx: commands.Context,
        masteries: Sequence[MasteryDefinition],
        progress: Mapping[str, Mapping[str, object]],
    ) -> None:
        super().__init__(timeout=120)
        self.ctx = ctx
        self.message: discord.Message | None = None
        added_button = False
        for mastery in masteries:
            tiers = _MASTERY_TIERS.get(mastery.slug)
            if not tiers:
                continue
            button = MasteryDetailButton(
                ctx=ctx,
                mastery=mastery,
                progress=progress.get(mastery.slug),
                tiers=tiers,
            )
            self.add_item(button)
            added_button = True
        if not added_button:
            self.add_item(
                discord.ui.Button(
                    label="Aucun détail disponible",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                )
            )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Seul le propriétaire de la commande peut utiliser ces boutons.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)


class Pets(commands.Cog):
    """Commande de collection de pets inspirée de Brawl Stars."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._definitions: List[PetDefinition] = list(PET_DEFINITIONS)
        self._definition_by_name: Dict[str, PetDefinition] = {pet.name: pet for pet in self._definitions}
        self._definition_by_slug: Dict[str, PetDefinition] = {}
        # FIX: Track hashed slugs to avoid collisions between similarly named pets.
        self._slug_aliases: Dict[str, List[str]] = {}
        slug_counters: Dict[str, int] = {}
        for pet in self._definitions:
            base_slug = self._normalize_pet_key(pet.name)
            if base_slug:
                count = slug_counters.get(base_slug, 0) + 1
                slug_counters[base_slug] = count
                hashed_slug = f"{base_slug}#{count}"
                self._definition_by_slug[hashed_slug] = pet
                self._slug_aliases.setdefault(base_slug, []).append(hashed_slug)
                if count == 1:
                    self._definition_by_slug[base_slug] = pet
            self._definition_by_slug[pet.name.lower()] = pet
        self._definition_by_id: Dict[int, PetDefinition] = {}
        self._pet_ids: Dict[str, int] = {}
        self._eggs: Dict[str, PetEggDefinition] = {
            egg.slug: egg for egg in PET_EGG_DEFINITIONS
        }
        self._zones: Dict[str, PetZoneDefinition] = {zone.slug: zone for zone in PET_ZONES}
        self._mastery_definitions: tuple[MasteryDefinition, ...] = tuple(iter_masteries())
        self._default_egg_slug: str = (
            DEFAULT_PET_EGG_SLUG if DEFAULT_PET_EGG_SLUG in self._eggs else next(iter(self._eggs), "")
        )
        self._egg_lookup: Dict[str, str] = {}
        for egg in self._eggs.values():
            aliases = {egg.slug, egg.name}
            aliases.update(egg.aliases)
            for token in aliases:
                for variant in self._generate_alias_variants(token):
                    self._egg_lookup[variant] = egg.slug
        if self._default_egg_slug:
            self._egg_lookup.setdefault(self._default_egg_slug, self._default_egg_slug)
        self._egg_open_locks: Dict[int, asyncio.Lock] = {}
        self._last_clock_sample = datetime.now(timezone.utc)

    @staticmethod
    def _normalize_pet_key(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return "".join(ch for ch in normalized.lower() if ch.isalnum())

    @staticmethod
    def _generate_alias_variants(token: str | None) -> Set[str]:
        variants: Set[str] = set()
        if not token:
            return variants

        def _add_variant(text: str) -> None:
            stripped = text.strip().lower()
            if stripped:
                variants.add(stripped)

        _add_variant(token)
        _add_variant(token.replace("œ", "oe").replace("Œ", "oe"))

        for candidate in list(variants):
            decomposed = unicodedata.normalize("NFKD", candidate)
            without_diacritics = "".join(
                ch for ch in decomposed if not unicodedata.combining(ch)
            )
            _add_variant(without_diacritics)

        return variants

    def _can_use_external_emojis(self, ctx: commands.Context) -> bool:
        """Return True if the bot can use external emojis in the channel."""

        guild = ctx.guild
        channel = getattr(ctx, "channel", None)
        if guild is None or channel is None:
            return True

        permissions_for = getattr(channel, "permissions_for", None)
        if permissions_for is None:
            return True

        member = getattr(guild, "me", None)
        if member is None:
            bot_user = getattr(self.bot, "user", None)
            if bot_user is not None:
                get_member = getattr(guild, "get_member", None)
                if callable(get_member):
                    member = get_member(bot_user.id)
        if member is None:
            return True

        try:
            permissions = permissions_for(member)
        except Exception:  # pragma: no cover - defensive branch for exotic channels
            return True
        return getattr(permissions, "use_external_emojis", True)

    def _egg_emoji(self, ctx: commands.Context) -> str:
        return EGG_OPEN_EMOJI if self._can_use_external_emojis(ctx) else FALLBACK_EGG_EMOJI

    @staticmethod
    def _market_variant_code(
        *, is_gold: bool, is_rainbow: bool, is_galaxy: bool, is_shiny: bool
    ) -> str:
        if is_galaxy:
            base = "galaxy"
        elif is_rainbow:
            base = "rainbow"
        elif is_gold:
            base = "gold"
        else:
            base = "normal"
        return f"{base}+shiny" if is_shiny else base

    @staticmethod
    def _roll_chance(chance: float) -> bool:
        return chance > 0 and random.random() < chance

    @staticmethod
    def _variant_income_multiplier(
        *, is_gold: bool, is_rainbow: bool, is_galaxy: bool, is_shiny: bool
    ) -> float:
        multiplier = 1.0
        if is_galaxy:
            multiplier *= GALAXY_PET_MULTIPLIER
        elif is_rainbow:
            multiplier *= RAINBOW_PET_MULTIPLIER
        elif is_gold:
            multiplier *= GOLD_PET_MULTIPLIER
        if is_shiny:
            multiplier *= SHINY_PET_MULTIPLIER
        return multiplier

    def _roll_huge_variants(self) -> tuple[bool, bool, bool, bool]:
        is_galaxy = self._roll_chance(HUGE_GALAXY_CHANCE)
        if is_galaxy:
            return False, False, True, self._roll_chance(HUGE_SHINY_CHANCE)

        is_rainbow = self._roll_chance(HUGE_RAINBOW_CHANCE)
        is_gold = False if is_rainbow else self._roll_chance(HUGE_GOLD_CHANCE)
        is_shiny = self._roll_chance(HUGE_SHINY_CHANCE)
        return is_gold, is_rainbow, False, is_shiny

    def _roll_standard_pet_variants(
        self,
        *,
        mastery_perks: EggMasteryPerks | None,
        pet_mastery_perks: PetMasteryPerks | None,
        clan_shiny_multiplier: float,
    ) -> tuple[bool, bool, bool, bool]:
        base_gold = max(0.0, float(GOLD_PET_CHANCE))
        base_rainbow = max(0.0, float(RAINBOW_PET_CHANCE))
        bonus_gold = 0.0
        bonus_rainbow = 0.0
        if mastery_perks is not None:
            bonus_gold = max(0.0, float(mastery_perks.gold_chance))
            bonus_rainbow = max(0.0, float(mastery_perks.rainbow_chance))

        gold_chance = min(1.0, base_gold + bonus_gold)
        rainbow_chance = min(1.0, base_rainbow + bonus_rainbow)
        shiny_chance = 0.0
        if pet_mastery_perks is not None:
            gold_chance *= float(pet_mastery_perks.gold_luck_multiplier)
            rainbow_chance *= float(pet_mastery_perks.rainbow_luck_multiplier)
            shiny_chance = max(0.0, float(pet_mastery_perks.egg_shiny_chance))
            shiny_chance *= float(pet_mastery_perks.egg_shiny_multiplier)

        gold_chance = min(1.0, gold_chance)
        rainbow_chance = min(1.0, rainbow_chance)
        shiny_chance *= max(1.0, float(clan_shiny_multiplier))

        is_gold = self._roll_chance(gold_chance)
        is_rainbow = self._roll_chance(rainbow_chance)
        if is_rainbow:
            is_gold = False
        is_shiny = self._roll_chance(shiny_chance)
        return is_gold, is_rainbow, False, is_shiny

    async def _maybe_award_enchantment(
        self, ctx: commands.Context, source: str
    ) -> None:
        if not should_drop_enchantment(source):
            return
        definition = pick_random_enchantment()
        power = roll_enchantment_power()
        try:
            await self.database.add_user_enchantment(
                ctx.author.id, definition.slug, power=power
            )
        except DatabaseError:
            logger.exception(
                "Impossible d'attribuer un enchantement", extra={"user_id": ctx.author.id}
            )
            return
        label = get_source_label(source)
        embed = embeds.success_embed(
            f"{ctx.author.mention} obtient {format_enchantment(definition, power)} grâce à {label} !",
            title="✨ Enchantement trouvé",
        )
        await ctx.send(embed=embed)

    @staticmethod
    def _parse_toggle_argument(raw: str | None) -> bool | None:
        if raw is None:
            return None
        normalized = raw.strip().lower()
        if normalized in {"on", "enable", "enabled", "true", "1", "oui", "activer", "activé", "active"}:
            return True
        if normalized in {"off", "disable", "disabled", "false", "0", "non", "desactiver", "désactiver", "desactive", "désactivé"}:
            return False
        return None

    def _get_open_lock(self, user_id: int) -> asyncio.Lock:
        lock = self._egg_open_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._egg_open_locks[user_id] = lock
        return lock

    def _resolve_market_value(
        self,
        market_values: Mapping[tuple[int, str], int],
        *,
        pet_id: int,
        is_gold: bool,
        is_rainbow: bool,
        is_galaxy: bool,
        is_shiny: bool,
    ) -> int:
        codes = [
            self._market_variant_code(
                is_gold=is_gold,
                is_rainbow=is_rainbow,
                is_galaxy=is_galaxy,
                is_shiny=is_shiny,
            )
        ]
        if is_shiny:
            codes.append(
                self._market_variant_code(
                    is_gold=is_gold,
                    is_rainbow=is_rainbow,
                    is_galaxy=is_galaxy,
                    is_shiny=False,
                )
            )
        if is_galaxy:
            codes.append(
                self._market_variant_code(
                    is_gold=is_gold,
                    is_rainbow=is_rainbow,
                    is_galaxy=False,
                    is_shiny=is_shiny,
                )
            )
        if is_rainbow or is_gold:
            codes.append(
                self._market_variant_code(
                    is_gold=False,
                    is_rainbow=False,
                    is_galaxy=False,
                    is_shiny=is_shiny,
                )
            )
        codes.append(
            self._market_variant_code(
                is_gold=False, is_rainbow=False, is_galaxy=False, is_shiny=False
            )
        )

        for code in codes:
            key = (pet_id, code)
            if key in market_values:
                return int(market_values[key])
        return 0

    async def cog_load(self) -> None:
        self._pet_ids = await self.database.sync_pets(self._definitions)
        self._definition_by_id = {pet_id: self._definition_by_name[name] for name, pet_id in self._pet_ids.items()}
        logger.info("Catalogue de pets synchronisé (%d entrées)", len(self._definition_by_id))

    async def _resync_pets(self) -> None:
        self._pet_ids = await self.database.sync_pets(self._definitions)
        self._definition_by_id = {
            pet_id: self._definition_by_name[name]
            for name, pet_id in self._pet_ids.items()
            if name in self._definition_by_name
        }
        logger.info(
            "Catalogue de pets resynchronisé (%d entrées)",
            len(self._definition_by_id),
        )

    async def _ensure_pet_registered(self, pet_name: str) -> bool:
        definition = self._definition_by_name.get(pet_name)
        if definition is None:
            return False
        try:
            new_ids = await self.database.sync_pets([definition])
        except Exception:
            logger.exception(
                "Impossible de synchroniser le pet manquant",
                extra={"pet_name": pet_name},
            )
            return False
        if not new_ids:
            return False
        self._pet_ids.update(new_ids)
        for name, identifier in new_ids.items():
            matched = self._definition_by_name.get(name)
            if matched is not None:
                self._definition_by_id[identifier] = matched
        return pet_name in self._pet_ids

    # ------------------------------------------------------------------
    # Utilitaires internes
    # ------------------------------------------------------------------
    def _choose_pet(
        self, egg: PetEggDefinition, *, luck_bonus: float = 0.0
    ) -> tuple[PetDefinition, int]:
        weights = [pet.drop_rate for pet in egg.pets]
        if luck_bonus > 0 and egg.pets:
            sorted_indices = sorted(
                range(len(egg.pets)),
                key=lambda idx: (
                    egg.pets[idx].drop_rate,
                    -egg.pets[idx].base_income_per_hour,
                ),
            )
            rare_count = max(1, len(sorted_indices) // 3)
            multiplier = 1.0 + float(luck_bonus)
            for idx in sorted_indices[:rare_count]:
                weights[idx] *= multiplier
        pet = random.choices(egg.pets, weights=weights, k=1)[0]
        pet_id = self._pet_ids[pet.name]
        return pet, pet_id

    def _egg_showcase_image(self, egg: PetEggDefinition) -> str | None:
        if egg.image_url:
            return egg.image_url
        if not egg.pets:
            return None

        def _sort_key(pet: PetDefinition) -> tuple[int, int]:
            rarity_rank = PET_RARITY_ORDER.get(pet.rarity, -1)
            return rarity_rank, int(getattr(pet, "base_income_per_hour", 0))

        for pet in sorted(egg.pets, key=_sort_key, reverse=True):
            image = getattr(pet, "image_url", None)
            if image:
                return image
        return None

    def _monotonic_now(self) -> datetime:
        current = datetime.now(timezone.utc)
        if current < self._last_clock_sample:
            return self._last_clock_sample
        self._last_clock_sample = current
        return current

    @staticmethod
    def _compute_huge_income(
        best_non_huge_income: int | None,
        *,
        pet_name: str | None = None,
        level: int = 1,
    ) -> int:
        best_value = max(0, int(best_non_huge_income or 0))
        multiplier = get_huge_level_multiplier(pet_name or "", level)
        return compute_huge_income(best_value, multiplier)

    @staticmethod
    def _apply_huge_progress_fields(data: Dict[str, Any], level: int, xp: int) -> None:
        clamped_level = max(1, min(level, HUGE_PET_LEVEL_CAP))
        xp = max(0, xp)
        data["huge_level"] = clamped_level
        if clamped_level >= HUGE_PET_LEVEL_CAP:
            data["huge_xp"] = 0
            data["huge_xp_required"] = 0
            data["huge_progress"] = 1.0
        else:
            required = huge_level_required_xp(clamped_level)
            progress_xp = min(xp, required)
            data["huge_xp"] = progress_xp
            data["huge_xp_required"] = required
            data["huge_progress"] = get_huge_level_progress(clamped_level, progress_xp)

    def _convert_record(
        self, record: Mapping[str, Any], *, best_non_huge_income: int | None = None
    ) -> Dict[str, Any]:
        data = dict(record)
        pet_identifier = int(record.get("pet_id", 0))
        definition = self._definition_by_id.get(pet_identifier)
        is_gold = bool(record.get("is_gold"))
        is_rainbow = bool(record.get("is_rainbow"))
        is_galaxy = bool(record.get("is_galaxy"))
        is_shiny = bool(record.get("is_shiny"))
        is_huge = bool(record.get("is_huge"))
        data["is_gold"] = is_gold
        data["is_rainbow"] = is_rainbow
        data["is_galaxy"] = is_galaxy
        data["is_shiny"] = is_shiny
        base_income = int(record.get("base_income_per_hour", data.get("base_income_per_hour", 0)))
        pet_name = str(data.get("name", ""))
        if definition is not None:
            data["image_url"] = definition.image_url
            data["rarity"] = definition.rarity
            pet_name = definition.name
            data["name"] = pet_name
            is_huge = definition.is_huge
            base_income = definition.base_income_per_hour
        else:
            data["name"] = pet_name
        data["is_huge"] = is_huge

        if is_huge:
            level = int(record.get("huge_level") or 1)
            xp = int(record.get("huge_xp") or 0)
            self._apply_huge_progress_fields(data, level, xp)
            data["_reference_income"] = int(best_non_huge_income or 0)
            effective_income = self._compute_huge_income(
                best_non_huge_income, pet_name=pet_name, level=level
            )
        else:
            data.pop("huge_level", None)
            data.pop("huge_xp", None)
            data.pop("huge_xp_required", None)
            data.pop("huge_progress", None)
            data.pop("_reference_income", None)
            multiplier = self._variant_income_multiplier(
                is_gold=is_gold,
                is_rainbow=is_rainbow,
                is_galaxy=is_galaxy,
                is_shiny=is_shiny,
            )
            effective_income = base_income * multiplier

        data["base_income_per_hour"] = int(effective_income)
        return data

    def _owned_pet_names(self, records: Iterable[Mapping[str, Any]]) -> Set[str]:
        owned: Set[str] = set()
        for record in records:
            pet_id = int(record.get("pet_id", 0))
            definition = self._definition_by_id.get(pet_id)
            if definition is not None:
                owned.add(definition.name.casefold())
                continue
            raw_name = str(record.get("name", "")).strip()
            if raw_name:
                owned.add(raw_name.casefold())
        return owned

    async def _prepare_pet_data(
        self, user_id: int, rows: Sequence[Mapping[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not rows:
            return []
        market_values = await self.database.get_pet_market_values()
        best_non_huge_income = await self.database.get_best_non_huge_income(user_id)
        pets_data: List[Dict[str, Any]] = []
        for row in rows:
            data = self._convert_record(row, best_non_huge_income=best_non_huge_income)
            pet_identifier = int(data.get("pet_id", 0))
            data["market_value"] = self._resolve_market_value(
                market_values,
                pet_id=pet_identifier,
                is_gold=bool(data.get("is_gold")),
                is_rainbow=bool(data.get("is_rainbow")),
                is_galaxy=bool(data.get("is_galaxy")),
                is_shiny=bool(data.get("is_shiny")),
            )
            pets_data.append(data)
        return pets_data

    @staticmethod
    def _resolve_member(ctx: commands.Context) -> Optional[discord.Member]:
        if isinstance(ctx.author, discord.Member):
            return ctx.author
        guild = ctx.guild
        if guild is not None:
            member = guild.get_member(ctx.author.id)
            if member is not None:
                return member
        return None

    @staticmethod
    def _embed_length(embed: discord.Embed) -> int:
        total = len(embed.title or "") + len(embed.description or "")
        for field in embed.fields:
            total += len(field.name or "") + len(field.value or "")
        footer = embed.footer
        if footer and footer.text:
            total += len(footer.text)
        return total

    def _dispatch_grade_progress(
        self, ctx: commands.Context, quest_type: str, amount: int
    ) -> None:
        member = self._resolve_member(ctx)
        if member is None:
            return
        self.bot.dispatch("grade_quest_progress", member, quest_type, amount, ctx.channel)

    async def _apply_auto_upgrades(
        self,
        ctx: commands.Context,
        definition: PetDefinition,
        pet_id: int,
        pet_perks: PetMasteryPerks,
        *,
        clan_shiny_multiplier: float = 1.0,
        auto_settings: Mapping[str, bool] | None = None,
    ) -> List[str]:
        messages: List[str] = []
        auto_gold_enabled = pet_perks.auto_goldify
        auto_rainbow_enabled = pet_perks.auto_rainbowify
        if auto_settings is not None:
            auto_gold_enabled = auto_gold_enabled and auto_settings.get(
                "auto_goldify", True
            )
            auto_rainbow_enabled = auto_rainbow_enabled and auto_settings.get(
                "auto_rainbowify", True
            )
        if not (auto_gold_enabled or auto_rainbow_enabled):
            return messages

        shiny_multiplier = max(1.0, float(clan_shiny_multiplier))

        def _roll_shiny(base_chance: float) -> bool:
            chance = max(0.0, float(base_chance))
            if chance <= 0:
                return False
            chance *= float(pet_perks.egg_shiny_multiplier)
            chance *= shiny_multiplier
            chance = min(1.0, chance)
            return random.random() < chance

        if auto_gold_enabled:
            while True:
                try:
                    make_shiny = _roll_shiny(pet_perks.goldify_shiny_chance)
                    gold_record, consumed = await self.database.upgrade_pet_to_gold(
                        ctx.author.id, pet_id, make_shiny=make_shiny
                    )
                except DatabaseError:
                    break

                name = str(gold_record.get("name", definition.name))
                shiny_suffix = " shiny" if bool(gold_record.get("is_shiny")) else ""
                messages.append(
                    f"⚙️ Auto Goldify : **{name}** passe directement en version or{shiny_suffix}!"
                )
                mastery_update = await self.database.add_mastery_experience(
                    ctx.author.id, PET_MASTERY.slug, max(1, int(consumed))
                )
                await self._handle_mastery_notifications(
                    ctx, mastery_update, mastery=PET_MASTERY
                )

        if auto_rainbow_enabled:
            while True:
                try:
                    make_shiny = _roll_shiny(pet_perks.rainbowify_shiny_chance)
                    rainbow_record, consumed = await self.database.upgrade_pet_to_rainbow(
                        ctx.author.id, pet_id, make_shiny=make_shiny
                    )
                except DatabaseError:
                    break

                name = str(rainbow_record.get("name", definition.name))
                shiny_suffix = " shiny" if bool(rainbow_record.get("is_shiny")) else ""
                messages.append(
                    f"🌈 Auto Rainbow : **{name}** se transforme en rainbow{shiny_suffix}!"
                )
                mastery_update = await self.database.add_mastery_experience(
                    ctx.author.id, PET_MASTERY.slug, max(1, int(consumed))
                )
                await self._handle_mastery_notifications(
                    ctx, mastery_update, mastery=PET_MASTERY
                )

        return messages

    async def _handle_mastery_notifications(
        self,
        ctx: commands.Context,
        update: Mapping[str, object],
        *,
        mastery: MasteryDefinition = EGG_MASTERY,
    ) -> None:
        levels_gained = int(update.get("levels_gained", 0) or 0)
        if levels_gained <= 0:
            return

        level = int(update.get("level", 1) or 1)
        previous_level = int(update.get("previous_level", level) or level)
        xp_to_next = int(update.get("xp_to_next_level", 0) or 0)
        current_progress = int(update.get("experience", 0) or 0)

        lines = [
            f"Tu passes niveau **{level}** de {mastery.display_name} !"
        ]
        if xp_to_next > 0:
            remaining = max(0, xp_to_next - current_progress)
            remaining_text = f"Encore {remaining:,} points d'expérience pour le prochain palier.".replace(",", " ")
            lines.append(remaining_text)
        else:
            lines.append("Tu as atteint le niveau maximal de maîtrise, félicitations !")

        if mastery is EGG_MASTERY:
            if previous_level < 5 <= level:
                lines.append(
                    "Tu as maintenant **5% de chance** d'obtenir un deuxième œuf gratuitement à chaque ouverture !"
                )
            if previous_level < 10 <= level:
                lines.append(
                    "Tu peux désormais PACK des pets **Gold** directement dans les œufs (3% de chance)."
                )
            if previous_level < 20 <= level:
                lines.append(
                    "Tu débloques **1% de chance** de pet rainbow et les animations d'ouverture sont 2× plus rapides."
                )
            if previous_level < 30 <= level:
                lines.append(
                    "Tu profites maintenant de **15% de chance** d'œuf double et **1% de chance** de triple ouverture."
                )
            if previous_level < 40 <= level:
                lines.append(
                    "Tes chances passent à **20% double**, **3% triple**, avec **5% Gold** et **2% Rainbow** dans les œufs."
                )
            if previous_level < 50 <= level:
                lines.append(
                    "Tu atteins **35%** de double, **10%** de triple et jusqu'à **10% Gold / 4% Rainbow** à chaque ouverture !"
                )
            if previous_level < 64 <= level:
                lines.append(
                    "Tu obtiens le rôle ultime avec **x2 chance** permanente sur tes ouvertures d'œufs !"
                )
        elif mastery is PET_MASTERY:
            if previous_level < 5 <= level:
                lines.append(
                    "La machine de fusion est débloquée, auto-goldify activé et **1%** de shiny dans les œufs !"
                )
            if previous_level < 10 <= level:
                lines.append(
                    "La machine de fusion offre désormais **10%** de chance de double récompense !"
                )
            if previous_level < 20 <= level:
                lines.append(
                    "Tes goldify ont **3%** de chance de produire un shiny et les rainbowify **1%** !"
                )
            if previous_level < 30 <= level:
                lines.append(
                    "Auto-rainbowify débloqué et ta chance de shiny dans les œufs passe à **3%** !"
                )
            if previous_level < 40 <= level:
                lines.append(
                    "La machine de fusion atteint **35% double** et **10% triple** !"
                )
            if previous_level < 50 <= level:
                lines.append(
                    "Tu profites de **50%** de double fuse, **5%** de shiny dans les œufs et plus de chances via goldify/rainbowify !"
                )
            if previous_level < 64 <= level:
                lines.append(
                    "Ton rôle ultime booste tes chances : x1.5 Gold, x1.3 Rainbow et x1.2 Shiny !"
                )

        role_map = {
            EGG_MASTERY.slug: EGG_MASTERY_MAX_ROLE_ID,
            PET_MASTERY.slug: PET_MASTERY_MAX_ROLE_ID,
        }
        if mastery.slug in role_map and level >= mastery.max_level:
            guild = ctx.guild
            if guild is not None:
                role = guild.get_role(role_map[mastery.slug])
                member = guild.get_member(ctx.author.id) if role else None
                if role is not None and member is not None and role not in member.roles:
                    with contextlib.suppress(discord.HTTPException, discord.Forbidden):
                        await member.add_roles(
                            role, reason=f"{mastery.display_name} niveau {level}"
                        )
                    lines.append(f"🎖️ Tu obtiens {role.mention} !")

        await ctx.send("\n".join(lines))

        dm_content = "🥚 " + "\n".join(lines)
        try:
            await ctx.author.send(dm_content)
        except discord.Forbidden:
            logger.debug("Impossible d'envoyer le MP de maîtrise à %s", ctx.author.id)
        except discord.HTTPException:
            logger.warning("Échec de l'envoi du MP de maîtrise", exc_info=True)

        new_levels = [int(level) for level in update.get("new_levels", [])]
        milestone_levels = [lvl for lvl in new_levels if lvl in EGG_MASTERY.broadcast_levels]
        if not milestone_levels or ctx.guild is None:
            return

        highest_milestone = max(milestone_levels)
        channel = ctx.channel
        if not hasattr(channel, "permissions_for"):
            return

        me = ctx.guild.me
        if me is None or not channel.permissions_for(me).send_messages:
            return

        announcement_lines = [
            f"🥚 **{ctx.author.display_name}** vient d'atteindre le niveau {highest_milestone} de {EGG_MASTERY.display_name}!",
        ]
        if highest_milestone >= 64:
            announcement_lines.append(
                "Ils obtiennent le rôle suprême et voient leur chance d'œuf doublée en permanence !"
            )
        elif highest_milestone >= 50:
            announcement_lines.append(
                "Ils profitent maintenant de 35% de chance d'œuf double et 10% de triple à chaque ouverture !"
            )
        elif highest_milestone >= 30:
            announcement_lines.append(
                "Ils débloquent des triples ouvertures et 15% de chance d'œuf bonus !"
            )
        elif highest_milestone >= 10:
            announcement_lines.append(
                "Ils débloquent désormais une chance d'obtenir des œufs bonus à chaque ouverture !"
            )

        try:
            await channel.send("\n".join(announcement_lines))
        except discord.HTTPException:
            logger.warning("Impossible d'envoyer l'annonce de maîtrise", exc_info=True)

    def _parse_pet_query(self, raw: str) -> tuple[str, Optional[int], Optional[str]]:
        tokens = [token for token in raw.split() if token]
        if not tokens:
            return "", None, None

        variant: Optional[str] = None
        ordinal: Optional[int] = None
        name_parts: List[str] = []
        slug_suffix: Optional[str] = None

        gold_aliases = {"gold", "doré", "doree", "or"}
        rainbow_aliases = {"rainbow", "rb", "arcenciel", "arc-en-ciel"}
        normal_aliases = {"normal", "base", "standard"}

        for token in tokens:
            lowered = token.lower()
            if lowered.isdigit():
                ordinal = int(lowered)
                continue
            if lowered.startswith("#") and lowered[1:].isdigit():
                slug_suffix = lowered
                continue
            if "#" in lowered:
                base_part, suffix_part = lowered.split("#", 1)
                if suffix_part.isdigit():
                    slug_suffix = f"#{suffix_part}"
                    if base_part:
                        name_parts.append(base_part)
                    continue
            if lowered in gold_aliases:
                variant = "gold"
                continue
            if lowered in rainbow_aliases:
                variant = "rainbow"
                continue
            if lowered in normal_aliases:
                variant = "normal"
                continue
            name_parts.append(token)

        if not name_parts:
            name_parts = tokens

        normalized = self._normalize_pet_key(" ".join(name_parts))
        if slug_suffix:
            normalized = f"{normalized}{slug_suffix}"
        else:
            aliases = self._slug_aliases.get(normalized)
            if aliases:
                normalized = aliases[0]
        return normalized, ordinal, variant

    async def _resolve_user_pet_candidates(
        self,
        ctx: commands.Context,
        raw_name: str,
        *,
        include_active: bool = True,
        include_inactive: bool = True,
    ) -> tuple[Optional[PetDefinition], List[Mapping[str, Any]], Optional[int], Optional[str]]:
        slug, ordinal, variant = self._parse_pet_query(raw_name)
        if not slug:
            return None, [], None, None

        definition = self._definition_by_slug.get(slug)
        if definition is None:
            return None, [], ordinal, variant

        is_gold: Optional[bool]
        is_rainbow: Optional[bool]
        if variant == "gold":
            is_gold = True
            is_rainbow = False
        elif variant == "rainbow":
            is_gold = False
            is_rainbow = True
        elif variant == "normal":
            is_gold = False
            is_rainbow = False
        else:
            is_gold = None
            is_rainbow = None

        rows = await self.database.get_user_pet_by_name(
            ctx.author.id,
            definition.name,
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            include_active=include_active,
            include_inactive=include_inactive,
        )
        return definition, list(rows), ordinal, variant

    def _build_pet_choice_embed(
        self,
        ctx: commands.Context,
        *,
        title: str,
        description: str,
        candidates: Sequence[Mapping[str, Any]],
    ) -> discord.Embed:
        lines: List[str] = []
        for index, candidate in enumerate(candidates, start=1):
            data = candidate.get("data", {})
            record = candidate.get("record", {})
            name = str(data.get("name", "Pet"))
            rarity = str(data.get("rarity", ""))
            emoji = embeds._pet_emoji(name) if hasattr(embeds, "_pet_emoji") else ""
            emoji_prefix = f"{emoji} " if emoji else ""
            is_active = bool(data.get("is_active"))
            is_gold = bool(data.get("is_gold"))
            is_rainbow = bool(data.get("is_rainbow"))
            income = int(data.get("base_income_per_hour", 0))
            acquired_at = record.get("acquired_at")
            acquired_text = ""
            if isinstance(acquired_at, datetime):
                acquired_text = acquired_at.strftime("%d/%m/%Y")
            markers = ""
            if is_rainbow:
                markers += " 🌈"
            elif is_gold:
                markers += " 🥇"
            status = "⭐ Actif" if is_active else "Disponible"
            line = (
                f"**{index}. {emoji_prefix}{name}{markers}** — {income:,} PB/h"
            ).replace(",", " ")
            if rarity:
                line += f" ({rarity})"
            line += f"\n{status}"
            if acquired_text:
                line += f" • Obtenu le {acquired_text}"
            lines.append(line)

        body = description
        if lines:
            body = f"{description}\n\n" + "\n\n".join(lines)
        embed = embeds.info_embed(body, title=title)
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        return embed

    async def _prompt_pet_selection(
        self,
        ctx: commands.Context,
        *,
        title: str,
        description: str,
        candidates: Sequence[Mapping[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        if not candidates:
            return None

        view = PetSelectionView(ctx=ctx, candidates=candidates)
        embed = self._build_pet_choice_embed(
            ctx,
            title=title,
            description=description,
            candidates=candidates,
        )
        message = await ctx.send(embed=embed, view=view)
        view.message = message
        timed_out = await view.wait()

        if view.selection is not None:
            return view.selection

        if view.cancelled:
            await ctx.send(embed=embeds.warning_embed("Sélection annulée."))
        elif timed_out:
            await ctx.send(embed=embeds.warning_embed("Temps écoulé pour la sélection."))
        return None

    async def _make_candidates(
        self, ctx: commands.Context, rows: Sequence[Mapping[str, Any]]
    ) -> List[Mapping[str, Any]]:
        pet_data = await self._prepare_pet_data(ctx.author.id, rows)
        return [
            {"record": row, "data": data}
            for row, data in zip(rows, pet_data)
        ]

    async def _build_pet_equip_embed(
        self,
        ctx: commands.Context,
        record: Mapping[str, Any],
        *,
        activated: bool,
        active_count: int,
        slot_limit: int,
    ) -> discord.Embed:
        best_non_huge_income = await self.database.get_best_non_huge_income(ctx.author.id)
        pet_data = self._convert_record(record, best_non_huge_income=best_non_huge_income)
        market_values = await self.database.get_pet_market_values()
        pet_identifier = int(pet_data.get("pet_id", 0))
        pet_data["market_value"] = self._resolve_market_value(
            market_values,
            pet_id=pet_identifier,
            is_gold=bool(pet_data.get("is_gold")),
            is_rainbow=bool(pet_data.get("is_rainbow")),
            is_galaxy=bool(pet_data.get("is_galaxy")),
            is_shiny=bool(pet_data.get("is_shiny")),
        )
        embed = embeds.pet_equip_embed(
            member=ctx.author,
            pet=pet_data,
            activated=activated,
            active_count=active_count,
            slot_limit=slot_limit,
        )
        return embed

    def _resolve_egg(self, raw: str | None) -> PetEggDefinition | None:
        if not self._eggs:
            return None
        if raw is None or not raw.strip():
            if self._default_egg_slug:
                return self._eggs.get(self._default_egg_slug) or next(
                    iter(self._eggs.values()), None
                )
            return next(iter(self._eggs.values()), None)
        key = raw.strip().lower()
        slug = self._egg_lookup.get(key)
        if slug is None:
            return None
        return self._eggs.get(slug)

    def _get_zone_for_egg(self, egg: PetEggDefinition) -> PetZoneDefinition | None:
        return self._zones.get(egg.zone_slug)

    @staticmethod
    def _grade_label(level: int) -> str:
        if 1 <= level <= len(GRADE_DEFINITIONS):
            return GRADE_DEFINITIONS[level - 1].name
        return f"Grade {level}"

    @staticmethod
    def _format_zone_cost(zone: PetZoneDefinition) -> str:
        if zone.currency == "gem":
            return embeds.format_gems(zone.entry_cost)
        return embeds.format_currency(zone.entry_cost)

    @staticmethod
    def _compute_slot_purchase_cost(extra_slots: int) -> int:
        exponent = max(0, int(extra_slots))
        base = Decimal(PET_SLOT_SHOP_BASE_COST)
        growth = Decimal(str(PET_SLOT_SHOP_COST_GROWTH))
        cost = base * (growth**exponent)
        rounded = cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return max(1, int(rounded))

    @staticmethod
    def _format_slot_cost(amount: int) -> str:
        if PET_SLOT_SHOP_CURRENCY == "gem":
            return embeds.format_gems(amount)
        return embeds.format_currency(amount)

    async def _ensure_zone_access(
        self, ctx: commands.Context, zone: PetZoneDefinition
    ) -> bool:
        if zone.entry_cost > 0:
            unlocked = await self.database.has_unlocked_zone(ctx.author.id, zone.slug)
            if unlocked:
                return True

        if zone.egg_mastery_required > 0:
            egg_mastery = await self.database.get_mastery_progress(
                ctx.author.id, EGG_MASTERY.slug
            )
            egg_level = int(egg_mastery.get("level", 1))
            if egg_level < zone.egg_mastery_required:
                await ctx.send(
                    embed=embeds.error_embed(
                        f"Tu dois atteindre le niveau {zone.egg_mastery_required} de {EGG_MASTERY.display_name} pour accéder à {zone.name}."
                    )
                )
                return False

        if zone.pet_mastery_required > 0:
            pet_mastery = await self.database.get_mastery_progress(
                ctx.author.id, PET_MASTERY.slug
            )
            pet_level = int(pet_mastery.get("level", 1))
            if pet_level < zone.pet_mastery_required:
                await ctx.send(
                    embed=embeds.error_embed(
                        f"Tu dois atteindre le niveau {zone.pet_mastery_required} de {PET_MASTERY.display_name} pour accéder à {zone.name}."
                    )
                )
                return False

        if zone.rebirth_required > 0:
            rebirth_count = await self.database.get_rebirth_count(ctx.author.id)
            if rebirth_count < zone.rebirth_required:
                await ctx.send(
                    embed=embeds.error_embed(
                        "Tu dois effectuer au moins "
                        f"{zone.rebirth_required} rebirth{'s' if zone.rebirth_required > 1 else ''} "
                        f"pour accéder à {zone.name}."
                    )
                )
                return False

        if zone.entry_cost <= 0:
            return True

        if zone.currency == "gem":
            balance = await self.database.fetch_gems(ctx.author.id)
        else:
            balance = await self.database.fetch_balance(ctx.author.id)

        if balance < zone.entry_cost:
            await ctx.send(
                embed=embeds.error_embed(
                    f"Il te faut {self._format_zone_cost(zone)} pour débloquer {zone.name}."
                )
            )
            return False

        if zone.currency == "gem":
            await self.database.increment_gems(
                ctx.author.id,
                -zone.entry_cost,
                transaction_type="zone_unlock",
                description=f"Déblocage zone {zone.name}",
            )
        else:
            await self.database.increment_balance(
                ctx.author.id,
                -zone.entry_cost,
                transaction_type="zone_unlock",
                description=f"Déblocage zone {zone.name}",
            )
        await self.database.unlock_zone(ctx.author.id, zone.slug)

        eggs_commands = ", ".join(f"`e!openbox {egg.slug}`" for egg in zone.eggs)
        lines = [
            f"{ctx.author.mention}, tu as débloqué **{zone.name}** !",
            f"Coût : {self._format_zone_cost(zone)}.",
        ]
        if eggs_commands:
            lines.append(f"Œufs disponibles : {eggs_commands}")
        await ctx.send(
            embed=embeds.success_embed("\n".join(lines), title="Nouvelle zone débloquée")
        )
        return True

    def _build_zone_overview_embed(
        self,
        ctx: commands.Context,
        zone: PetZoneDefinition,
        *,
        has_unlocked: bool,
        meets_egg_mastery: bool,
        meets_pet_mastery: bool,
        meets_rebirth: bool,
    ) -> discord.Embed:
        status_emoji = "✅" if has_unlocked else "🔒"
        title = f"{status_emoji} {zone.name}"
        cost_text = "Gratuit" if zone.entry_cost <= 0 else self._format_zone_cost(zone)
        cost_suffix = ""
        if zone.entry_cost > 0 and has_unlocked:
            cost_suffix = " (déjà payé)"
        description_lines = [
            f"Statut : {'Débloquée' if has_unlocked else 'Verrouillée'}",
            f"Prix d'accès : {cost_text}{cost_suffix}",
        ]
        accessible = (
            has_unlocked
            and meets_egg_mastery
            and meets_pet_mastery
            and meets_rebirth
        )
        description_lines.append(
            f"Accès actuel : {'Disponible' if accessible else 'Conditions à remplir'}"
        )

        requirements: List[str] = []
        if zone.egg_mastery_required > 0:
            requirements.append(
                f"{'✅' if meets_egg_mastery else '❌'} {EGG_MASTERY.display_name} niveau {zone.egg_mastery_required}"
            )
        if zone.pet_mastery_required > 0:
            requirements.append(
                f"{'✅' if meets_pet_mastery else '❌'} {PET_MASTERY.display_name} niveau {zone.pet_mastery_required}"
            )
        if zone.rebirth_required > 0:
            plural = "s" if zone.rebirth_required > 1 else ""
            requirements.append(
                f"{'✅' if meets_rebirth else '❌'} {zone.rebirth_required} rebirth{plural}"
            )
        if zone.entry_cost > 0:
            requirements.append(
                f"{'✅' if has_unlocked else '❌'} {self._format_zone_cost(zone)}"
            )

        embed = embeds.info_embed(
            "\n".join(description_lines),
            title=title,
        )
        embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url,
        )

        if requirements:
            embed.add_field(
                name="Conditions",
                value="\n".join(requirements),
                inline=False,
            )

        if zone.eggs:
            for egg in zone.eggs:
                emoji_sequence = [pet_emoji(pet.name) for pet in egg.pets]
                unique_emojis = list(dict.fromkeys(emoji_sequence))
                if egg.currency == "gem":
                    price_display = embeds.format_gems(egg.price)
                else:
                    price_display = embeds.format_currency(egg.price)
                field_lines = [f"Prix : {price_display}"]
                if has_unlocked:
                    field_lines.append(f"Commande : `e!openbox {egg.slug}`")
                else:
                    field_lines.append(
                        f"Commande : `e!openbox {egg.slug}` (après déblocage)"
                    )
                    field_lines.append("Débloque la zone pour ouvrir cet œuf.")
                if unique_emojis:
                    field_lines.append(f"Pets : {' '.join(unique_emojis)}")
                field_name = egg.name if has_unlocked else f"🔒 {egg.name}"
                embed.add_field(
                    name=field_name,
                    value="\n".join(field_lines),
                    inline=False,
                )
        elif has_unlocked:
            embed.add_field(
                name="Œufs disponibles",
                value="Aucun œuf n'est proposé ici.",
                inline=False,
            )
        else:
            embed.add_field(name="Prix des œufs", value="???", inline=False)
            embed.add_field(name="Commande", value="???", inline=False)
            embed.add_field(name="Pets possibles", value="???", inline=False)

        return embed

    async def _send_egg_overview(self, ctx: commands.Context) -> None:
        unlocked_zones = await self.database.get_unlocked_zones(ctx.author.id)
        egg_mastery = await self.database.get_mastery_progress(ctx.author.id, EGG_MASTERY.slug)
        pet_mastery = await self.database.get_mastery_progress(ctx.author.id, PET_MASTERY.slug)
        egg_level = int(egg_mastery.get("level", 1))
        pet_level = int(pet_mastery.get("level", 1))
        rebirth_count = await self.database.get_rebirth_count(ctx.author.id)

        zone_pages: List[ZoneOverviewView.PageState] = []
        for zone in PET_ZONES:
            has_unlocked = zone.entry_cost <= 0 or zone.slug in unlocked_zones
            meets_egg_mastery = egg_level >= zone.egg_mastery_required
            meets_pet_mastery = pet_level >= zone.pet_mastery_required
            meets_rebirth = rebirth_count >= zone.rebirth_required

            embed = self._build_zone_overview_embed(
                ctx,
                zone,
                has_unlocked=has_unlocked,
                meets_egg_mastery=meets_egg_mastery,
                meets_pet_mastery=meets_pet_mastery,
                meets_rebirth=meets_rebirth,
            )
            zone_pages.append(
                ZoneOverviewView.PageState(
                    embed=embed,
                    zone=zone,
                    has_unlocked=has_unlocked,
                    meets_egg_mastery=meets_egg_mastery,
                    meets_pet_mastery=meets_pet_mastery,
                    meets_rebirth=meets_rebirth,
                )
            )

        if rebirth_count > 0:
            mystery_embed = embeds.info_embed(
                "Zone encore mystérieuse... Revenez bientôt pour en savoir plus !",
                title="✨ Zone Mystérieuse",
            )
            mystery_embed.set_author(
                name=ctx.author.display_name,
                icon_url=ctx.author.display_avatar.url,
            )
            zone_pages.append(
                ZoneOverviewView.PageState(
                    embed=mystery_embed,
                    zone=None,
                    has_unlocked=False,
                    meets_egg_mastery=False,
                    meets_pet_mastery=False,
                    meets_rebirth=False,
                )
            )

        if not zone_pages:
            embed = embeds.info_embed(
                "Aucun œuf n'est disponible pour le moment.",
                title="Œufs & zones disponibles",
            )
            embed.set_author(
                name=ctx.author.display_name,
                icon_url=ctx.author.display_avatar.url,
            )
            await ctx.send(embed=embed)
            return

        total_pages = len(zone_pages)
        for index, page in enumerate(zone_pages, start=1):
            page.embed.set_footer(text=f"Page {index}/{total_pages}")

        view = ZoneOverviewView(ctx, zone_pages, self)
        message = await ctx.send(embed=zone_pages[0].embed, view=view)
        view.message = message

    async def _send_huge_shelly_alert(self, ctx: commands.Context) -> None:
        channel = self.bot.get_channel(HUGE_SHELLY_ALERT_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(HUGE_SHELLY_ALERT_CHANNEL_ID)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                logger.warning("Canal Huge Shelly introuvable pour l'alerte hype")
                return
        if not isinstance(channel, discord.abc.Messageable):
            logger.warning(
                "Canal Huge Shelly non compatible pour l'envoi : %s",
                type(channel).__name__,
            )
            return

        hype_message = (
            "🚨🚨🚨 **ALERTE ÉPIQUE !** 🚨🚨🚨\n"
            f"**{ctx.author.display_name}** vient de PACK la **{HUGE_PET_NAME.upper()}** !!!\n"
            "🔥🔥 FLAMMES, CRIS, HYPE ABSOLUE 🔥🔥\n"
            f"{ctx.author.mention} rejoint le club des légendes, spammez les GGs et sortez les confettis !!!"
        )
        try:
            # FIX: Guard against unexpected HTTP errors when broadcasting the alert.
            await channel.send(hype_message)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Impossible d'envoyer l'alerte Huge Shelly", exc_info=exc)

    async def _hatch_pet(
        self,
        ctx: commands.Context,
        egg: PetEggDefinition,
        *,
        mastery_perks: EggMasteryPerks | None = None,
        pet_mastery_perks: PetMasteryPerks | None = None,
        clan_shiny_multiplier: float = 1.0,
        active_potion: tuple[PotionDefinition, datetime] | None = None,
        charge_cost: bool = True,
        bonus: bool = False,
        price_multiplier: int = 1,
        force_gold: bool = False,
    ) -> PetHatchResult | None:
        await self.database.ensure_user(ctx.author.id)
        if charge_cost:
            effective_price = egg.price * max(1, int(price_multiplier))
            if egg.currency == "gem":
                balance = await self.database.fetch_gems(ctx.author.id)
                if balance < effective_price:
                    await ctx.send(
                        embed=embeds.error_embed(
                            "Tu n'as pas assez de gemmes. Il t'en faut "
                            f"**{embeds.format_gems(effective_price)}** pour acheter {egg.name}."
                        )
                    )
                    return None

                await self.database.increment_gems(
                    ctx.author.id,
                    -effective_price,
                    transaction_type="pet_purchase",
                    description=f"Achat de {egg.name}",
                )
            else:
                balance = await self.database.fetch_balance(ctx.author.id)
                if balance < effective_price:
                    await ctx.send(
                        embed=embeds.error_embed(
                            "Tu n'as pas assez de PB. Il te faut "
                            f"**{embeds.format_currency(effective_price)}** pour acheter {egg.name}."
                        )
                    )
                    return None

                await self.database.increment_balance(
                    ctx.author.id,
                    -effective_price,
                    transaction_type="pet_purchase",
                    description=f"Achat de {egg.name}",
                )
        enchantments = await self.database.get_enchantment_powers(ctx.author.id)
        effective_luck_bonus = 0.0
        frenzy_active = is_egg_frenzy_active()
        if mastery_perks:
            effective_luck_bonus += max(0.0, float(mastery_perks.luck_bonus))
        if active_potion:
            potion_definition, potion_expires_at = active_potion
            if (
                potion_definition.effect_type == "egg_luck"
                and potion_expires_at > self._monotonic_now()
            ):
                effective_luck_bonus += max(0.0, float(potion_definition.effect_value))
        if frenzy_active:
            effective_luck_bonus += max(0.0, float(EGG_FRENZY_LUCK_BONUS))
        if enchantments:
            effective_luck_bonus += compute_egg_luck_bonus(
                enchantments.get("egg_luck", 0)
            )
        pet_definition: PetDefinition | None = None
        pet_id: int | None = None
        last_missing_name = ""
        for attempt in range(3):
            try:
                pet_definition, pet_id = self._choose_pet(
                    egg, luck_bonus=effective_luck_bonus
                )
                break
            except KeyError as exc:
                missing_name = exc.args[0] if exc.args else ""
                last_missing_name = str(missing_name)
                if attempt == 0:
                    await self._resync_pets()
                    continue
                if attempt == 1 and last_missing_name:
                    ensured = await self._ensure_pet_registered(last_missing_name)
                    if ensured:
                        continue
                logger.exception(
                    "Pet introuvable lors de l'ouverture d'œuf",
                    extra={
                        "user_id": ctx.author.id,
                        "egg": egg.slug,
                        "missing_pet": last_missing_name,
                    },
                )
                await ctx.send(
                    embed=embeds.error_embed(
                        "Impossible d'ouvrir cet œuf pour le moment. "
                        "Réessaie dans quelques instants."
                    )
                )
                return None

        if pet_definition is None or pet_id is None:
            logger.error(
                "Aucun pet sélectionné après plusieurs tentatives",
                extra={
                    "user_id": ctx.author.id,
                    "egg": egg.slug,
                    "missing_pet": last_missing_name,
                },
            )
            await ctx.send(
                embed=embeds.error_embed(
                    "Impossible d'ouvrir cet œuf pour le moment. "
                    "Réessaie dans quelques instants."
                )
            )
            return None
        is_gold = False
        is_rainbow = False
        is_galaxy = False
        is_shiny = False
        if pet_definition.is_huge:
            (
                is_gold,
                is_rainbow,
                is_galaxy,
                is_shiny,
            ) = self._roll_huge_variants()
        else:
            (
                is_gold,
                is_rainbow,
                is_galaxy,
                is_shiny,
            ) = self._roll_standard_pet_variants(
                mastery_perks=mastery_perks,
                pet_mastery_perks=pet_mastery_perks,
                clan_shiny_multiplier=clan_shiny_multiplier,
            )

        if force_gold and not pet_definition.is_huge:
            is_rainbow = False
            is_galaxy = False
            is_gold = True

        await self.database.add_user_pet(
            ctx.author.id,
            pet_id,
            is_huge=pet_definition.is_huge,
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            is_galaxy=is_galaxy,
            is_shiny=is_shiny,
        )
        await self.database.record_pet_opening(ctx.author.id, pet_id)

        auto_messages: List[str] = []
        if frenzy_active:
            auto_messages.append(
                "🍀 **Egg Frenzy** : tes chances ont profité d'un bonus de "
                f"{EGG_FRENZY_LUCK_BONUS * 100:.0f}% !"
            )
        if pet_mastery_perks is not None and not pet_definition.is_huge:
            auto_settings: Mapping[str, bool] | None = None
            if pet_mastery_perks.auto_goldify or pet_mastery_perks.auto_rainbowify:
                try:
                    auto_settings = await self.database.get_pet_auto_settings(
                        ctx.author.id
                    )
                except DatabaseError:
                    logger.exception(
                        "Impossible de récupérer les préférences auto pet",
                        extra={"user_id": ctx.author.id},
                    )
            auto_messages.extend(
                await self._apply_auto_upgrades(
                    ctx,
                    pet_definition,
                    pet_id,
                    pet_mastery_perks,
                    clan_shiny_multiplier=clan_shiny_multiplier,
                    auto_settings=auto_settings,
                )
            )

        market_values = await self.database.get_pet_market_values()
        market_value = self._resolve_market_value(
            market_values,
            pet_id=pet_id,
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            is_galaxy=is_galaxy,
            is_shiny=is_shiny,
        )
        if pet_definition.is_huge:
            best_non_huge_income = await self.database.get_best_non_huge_income(ctx.author.id)
            income_per_hour = self._compute_huge_income(
                best_non_huge_income, pet_name=pet_definition.name, level=1
            )
        else:
            multiplier = self._variant_income_multiplier(
                is_gold=is_gold,
                is_rainbow=is_rainbow,
                is_galaxy=is_galaxy,
                is_shiny=is_shiny,
            )
            income_per_hour = int(pet_definition.base_income_per_hour * multiplier)

        if pet_definition.name == HUGE_PET_NAME:
            await self._send_huge_shelly_alert(ctx)

        mastery_update = await self.database.add_mastery_experience(
            ctx.author.id, EGG_MASTERY.slug, 1
        )
        await self._handle_mastery_notifications(ctx, mastery_update)

        self._dispatch_grade_progress(ctx, "egg", 1)
        return PetHatchResult(
            definition=pet_definition,
            income_per_hour=income_per_hour,
            market_value=market_value,
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            is_galaxy=is_galaxy,
            is_shiny=is_shiny,
            is_huge=pet_definition.is_huge,
            auto_messages=auto_messages,
            bonus=bonus,
            was_forced_gold=force_gold,
        )

    async def _display_hatch_results(
        self,
        ctx: commands.Context,
        egg: PetEggDefinition,
        results: Sequence[PetHatchResult],
        *,
        mastery_perks: EggMasteryPerks | None = None,
    ) -> None:
        egg_title = egg.name
        egg_emoji = self._egg_emoji(ctx)
        animation_steps = (
            (egg_title, "L'œuf commence à bouger…"),
            (egg_title, "Des fissures apparaissent !"),
            (egg_title, "Ça y est, il est sur le point d'éclore !"),
        )
        speed_factor = 1.0
        if mastery_perks is not None:
            speed_factor = max(0.5, min(5.0, float(mastery_perks.animation_speed)))
        step_delay = max(0.2, 1.1 / speed_factor)
        reveal_delay = max(0.2, 1.2 / speed_factor)

        message = await ctx.send(
            content=egg_emoji,
            embed=embeds.pet_animation_embed(
                title=animation_steps[0][0],
                description=animation_steps[0][1],
                emoji=egg_emoji,
            ),
        )
        for title, description in animation_steps[1:]:
            await asyncio.sleep(step_delay)
            await message.edit(
                content=egg_emoji,
                embed=embeds.pet_animation_embed(
                    title=title,
                    description=description,
                    emoji=egg_emoji,
                ),
            )

        await asyncio.sleep(reveal_delay)

        if not results:
            return

        if len(results) == 1:
            result = results[0]
            embed = embeds.pet_reveal_embed(
                name=result.definition.name,
                rarity=result.definition.rarity,
                image_url=result.definition.image_url,
                income_per_hour=result.income_per_hour,
                is_huge=result.is_huge,
                is_gold=result.is_gold,
                is_galaxy=result.is_galaxy,
                is_rainbow=result.is_rainbow,
                is_shiny=result.is_shiny,
                market_value=int(result.market_value or 0),
            )
            footer_parts: list[str] = []
            if result.was_forced_gold:
                footer_parts.append("Gold garanti")
            if result.bonus:
                footer_parts.append("Bonus gratuit")
            footer_parts.append(f"Utilise e!equip {result.definition.name} pour l'équiper !")
            embed.set_footer(text=" • ".join(footer_parts))
        else:
            payloads: list[dict[str, object]] = []
            for entry in results:
                payloads.append(
                    {
                        "name": entry.definition.name,
                        "rarity": entry.definition.rarity,
                        "image_url": entry.definition.image_url,
                        "income_per_hour": entry.income_per_hour,
                        "is_huge": entry.is_huge,
                        "is_gold": entry.is_gold,
                        "is_galaxy": entry.is_galaxy,
                        "is_rainbow": entry.is_rainbow,
                        "is_shiny": entry.is_shiny,
                        "market_value": int(entry.market_value or 0),
                        "bonus": entry.bonus,
                        "forced": entry.was_forced_gold,
                    }
                )
            embed = embeds.pet_multi_reveal_embed(
                egg_name=egg.name,
                pets=payloads,
            )
            names = list(dict.fromkeys(entry.definition.name for entry in results))
            if names:
                if len(names) == 1:
                    footer_text = f"Utilise e!equip {names[0]} pour l'équiper !"
                elif len(names) == 2:
                    footer_text = f"Utilise e!equip {names[0]} ou {names[1]} pour les équiper !"
                else:
                    footer_text = (
                        "Utilise e!equip "
                        + ", ".join(names[:-1])
                        + f" ou {names[-1]} pour les équiper !"
                    )
                embed.set_footer(text=footer_text)

        replay_view = HatchReplayView(ctx, self, egg.slug)
        replay_view.message = message
        await message.edit(content=egg_emoji, embed=embed, view=replay_view)

    async def _open_pet_egg(
        self,
        ctx: commands.Context,
        egg: PetEggDefinition,
        *,
        mastery_perks: EggMasteryPerks | None = None,
        pet_mastery_perks: PetMasteryPerks | None = None,
        clan_shiny_multiplier: float = 1.0,
        active_potion: tuple[PotionDefinition, datetime] | None = None,
        charge_cost: bool = True,
        bonus: bool = False,
    ) -> bool:
        """Compatibilité héritée pour les tests existants.

        La nouvelle implémentation découpe l'éclosion en deux méthodes
        (`_hatch_pet` et `_display_hatch_results`). Ce wrapper conserve
        l'ancienne signature pour éviter d'adapter tous les appelants
        historiques, notamment dans la suite de tests.
        """

        result = await self._hatch_pet(
            ctx,
            egg,
            mastery_perks=mastery_perks,
            pet_mastery_perks=pet_mastery_perks,
            clan_shiny_multiplier=clan_shiny_multiplier,
            active_potion=active_potion,
            charge_cost=charge_cost,
            bonus=bonus,
        )
        if result is None:
            return False

        await self._display_hatch_results(
            ctx,
            egg,
            [result],
            mastery_perks=mastery_perks,
        )
        return True

    def _sort_pets_for_display(
        self,
        records: Iterable[Mapping[str, Any]],
        market_values: Mapping[tuple[int, str], int] | None = None,
    ) -> List[Dict[str, Any]]:
        record_list = list(records)
        best_non_huge_income = 0
        for record in record_list:
            if not bool(record.get("is_huge")):
                base_income = int(record.get("base_income_per_hour", 0))
                multiplier = self._variant_income_multiplier(
                    is_gold=bool(record.get("is_gold")),
                    is_rainbow=bool(record.get("is_rainbow")),
                    is_galaxy=bool(record.get("is_galaxy")),
                    is_shiny=bool(record.get("is_shiny")),
                )
                effective_income = int(base_income * multiplier)
                if effective_income > best_non_huge_income:
                    best_non_huge_income = effective_income

        converted = []
        for record in record_list:
            data = self._convert_record(record, best_non_huge_income=best_non_huge_income)
            data["income"] = int(data.get("base_income_per_hour", 0))
            pet_identifier = int(data.get("pet_id", 0))
            if market_values:
                data["market_value"] = self._resolve_market_value(
                    market_values,
                    pet_id=pet_identifier,
                    is_gold=bool(data.get("is_gold")),
                    is_rainbow=bool(data.get("is_rainbow")),
                    is_galaxy=bool(data.get("is_galaxy")),
                    is_shiny=bool(data.get("is_shiny")),
                )
            data.pop("_reference_income", None)
            converted.append(data)

        converted.sort(
            key=lambda pet: (
                -PET_RARITY_ORDER.get(str(pet.get("rarity", "")), -1),
                -int(pet.get("income", 0)),
                str(pet.get("name", "")),
            )
        )
        return converted


    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------
    @commands.group(name="buy", invoke_without_command=True)
    async def buy(self, ctx: commands.Context, *, item: str | None = None) -> None:
        if item:
            egg_definition = self._resolve_egg(item)
            if egg_definition is not None:
                await ctx.invoke(self.openbox, egg=item)
                return
        await ctx.send(
            embed=embeds.info_embed(
                "Utilise `e!openbox [œuf]` pour ouvrir un œuf ou `e!eggs` pour voir les zones disponibles."
            )
        )

    @buy.command(name="egg")
    async def buy_egg(self, ctx: commands.Context, *, egg: str | None = None) -> None:
        await ctx.invoke(self.openbox, egg=egg)

    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.command(name="openbox", aliases=("buyegg", "openegg", "egg"))
    async def openbox(self, ctx: commands.Context, egg: str | None = None) -> None:
        lock = self._get_open_lock(ctx.author.id)
        async with lock:
            await self._openbox_impl(ctx, egg)

    async def _openbox_impl(self, ctx: commands.Context, egg: str | None) -> None:
        log_context: Dict[str, Any] = {
            "user_id": ctx.author.id,
            "guild_id": getattr(ctx.guild, "id", None),
            "channel_id": getattr(getattr(ctx, "channel", None), "id", None),
            "raw_request": egg,
            "stage": "start",
        }
        try:
            log_context["stage"] = "normalize_request"
            raw_request = (egg or "").strip()
            double_request = False
            force_gold_request = False
            if raw_request:
                tokens = raw_request.split()
                while tokens:
                    token = tokens[-1].lower()
                    if token in {"x2", "2", "double"}:
                        double_request = True
                        tokens.pop()
                        continue
                    if token in {"gold", "golden", "garanti", "garantie", "guaranteed", "100x", "x100"}:
                        force_gold_request = True
                        tokens.pop()
                        continue
                    break
                raw_request = " ".join(tokens).strip()

            log_context["double_request"] = double_request
            log_context["force_gold_initial"] = force_gold_request

            normalized_request = raw_request or None
            log_context["normalized_request"] = normalized_request

            if normalized_request and normalized_request.lower() in {"list", "liste", "eggs", "oeufs"}:
                log_context["stage"] = "send_overview"
                await self._send_egg_overview(ctx)
                return

            log_context["stage"] = "resolve_egg"
            egg_definition = self._resolve_egg(normalized_request)
            if egg_definition is None:
                if normalized_request:
                    await ctx.send(
                        embed=embeds.error_embed("Œuf introuvable. Voici les options disponibles :")
                    )
                    await self._send_egg_overview(ctx)
                else:
                    await ctx.send(
                        embed=embeds.error_embed("Aucun œuf n'est disponible pour le moment.")
                    )
                return

            log_context["resolved_egg"] = egg_definition.slug

            log_context["stage"] = "resolve_zone"
            zone = self._get_zone_for_egg(egg_definition)
            if zone is None:
                await ctx.send(embed=embeds.error_embed("La zone associée à cet œuf est introuvable."))
                return

            log_context["zone"] = zone.slug

            log_context["stage"] = "ensure_zone_access"
            if not await self._ensure_zone_access(ctx, zone):
                return

            log_context["stage"] = "load_egg_mastery"
            mastery_progress = await self.database.get_mastery_progress(
                ctx.author.id, EGG_MASTERY.slug
            )
            mastery_level = int(mastery_progress.get("level", 1))
            log_context["egg_mastery_level"] = mastery_level
            egg_perks = _compute_egg_mastery_perks(mastery_level)

            log_context["stage"] = "load_pet_mastery"
            pet_mastery_progress = await self.database.get_mastery_progress(
                ctx.author.id, PET_MASTERY.slug
            )
            pet_mastery_level = int(pet_mastery_progress.get("level", 1))
            log_context["pet_mastery_level"] = pet_mastery_level
            pet_perks = _compute_pet_mastery_perks(pet_mastery_level)

            log_context["stage"] = "fetch_clan"
            clan_row = await self.database.get_user_clan(ctx.author.id)
            clan_shiny_multiplier = 1.0
            if clan_row is not None:
                clan_shiny_multiplier = max(
                    1.0, float(clan_row.get("shiny_luck_multiplier") or 1.0)
                )
            log_context["clan_shiny_multiplier"] = clan_shiny_multiplier

            if double_request:
                log_context["stage"] = "inform_double_request"
                if egg_perks.double_chance > 0:
                    await ctx.send(
                        embed=embeds.info_embed(
                            "Le mode double est désormais automatique : tu as "
                            f"{egg_perks.double_chance * 100:.0f}% de chances d'obtenir un œuf bonus gratuitement à chaque ouverture."
                        )
                    )
                else:
                    await ctx.send(
                        embed=embeds.warning_embed(
                            "Atteins le niveau 5 de Maîtrise des œufs pour débloquer 5% de chance d'obtenir un deuxième œuf gratuit."
                        )
                    )

            log_context["stage"] = "fetch_rebirth_count"
            rebirth_count = await self.database.get_rebirth_count(ctx.author.id)
            log_context["rebirth_count"] = rebirth_count
            price_multiplier = 1
            if force_gold_request:
                log_context["stage"] = "handle_force_gold"
                if rebirth_count <= 0:
                    await ctx.send(
                        embed=embeds.warning_embed(
                            "Le gold garanti se débloque après ton premier rebirth."
                        )
                    )
                    force_gold_request = False
                else:
                    price_multiplier = 100
                    price_text = (
                        embeds.format_gems(egg_definition.price * price_multiplier)
                        if egg_definition.currency == "gem"
                        else embeds.format_currency(egg_definition.price * price_multiplier)
                    )
                    await ctx.send(
                        embed=embeds.info_embed(
                            f"Tu choisis de payer **{price_text}** pour garantir un pet or.",
                            title="Gold garanti activé",
                        )
                    )

            log_context["price_multiplier"] = price_multiplier
            log_context["force_gold_final"] = force_gold_request

            log_context["stage"] = "fetch_active_potion"
            active_potion = await self.database.get_active_potion(ctx.author.id)
            if active_potion is not None:
                potion_definition, potion_expires_at = active_potion
                log_context["active_potion"] = getattr(potion_definition, "slug", None)
                log_context["potion_expires_at"] = getattr(
                    potion_expires_at, "isoformat", lambda: None
                )()
            else:
                log_context["active_potion"] = None

            log_context["stage"] = "hatch_primary"
            primary_result = await self._hatch_pet(
                ctx,
                egg_definition,
                pet_mastery_perks=pet_perks,
                clan_shiny_multiplier=clan_shiny_multiplier,
                active_potion=active_potion,
                mastery_perks=egg_perks,
                price_multiplier=price_multiplier,
                force_gold=force_gold_request,
            )
            if primary_result is None:
                return

            results: List[PetHatchResult] = [primary_result]

            log_context["stage"] = "compute_bonus_eggs"
            bonus_eggs = 0
            triple_triggered = False
            if egg_perks.triple_chance > 0 and random.random() < egg_perks.triple_chance:
                bonus_eggs = 2
                triple_triggered = True
            elif egg_perks.double_chance > 0 and random.random() < egg_perks.double_chance:
                bonus_eggs = 1

            log_context["bonus_eggs"] = bonus_eggs
            log_context["triple_triggered"] = triple_triggered

            if bonus_eggs:
                egg_emoji = self._egg_emoji(ctx)
                if triple_triggered:
                    await ctx.send(
                        f"{egg_emoji} 🎉 **Chance triple !** Tu ouvres deux œufs bonus gratuitement !"
                    )
                else:
                    await ctx.send(
                        f"{egg_emoji} 🎉 **Chance !** Tu ouvres un œuf bonus gratuitement !"
                    )
                for bonus_index in range(bonus_eggs):
                    log_context["stage"] = "hatch_bonus"
                    log_context["bonus_iteration"] = bonus_index + 1
                    bonus_result = await self._hatch_pet(
                        ctx,
                        egg_definition,
                        pet_mastery_perks=pet_perks,
                        clan_shiny_multiplier=clan_shiny_multiplier,
                        active_potion=active_potion,
                        mastery_perks=egg_perks,
                        charge_cost=False,
                        bonus=True,
                    )
                    if bonus_result is not None:
                        results.append(bonus_result)
                log_context.pop("bonus_iteration", None)

            log_context["stage"] = "display_results"
            await self._display_hatch_results(
                ctx,
                egg_definition,
                results,
                mastery_perks=egg_perks,
            )

            log_context["stage"] = "send_auto_messages"
            for result in results:
                for auto_message in result.auto_messages:
                    await ctx.send(auto_message)

            log_context["stage"] = "completed"
        except asyncio.CancelledError:
            raise
        except commands.CommandError:
            raise
        except Exception as exc:  # pragma: no cover - diagnostic logging
            error_reference = (
                f"egg-{ctx.author.id}-{int(datetime.now(timezone.utc).timestamp())}"
            )
            log_context["error_reference"] = error_reference
            log_context["exception_type"] = type(exc).__name__
            logger.exception(
                "Erreur inattendue lors de l'ouverture d'œuf",
                extra=log_context,
            )
            try:
                await ctx.send(
                    embed=embeds.error_embed(
                        "Une erreur inattendue est survenue pendant l'ouverture de l'œuf. "
                        f"Merci de réessayer plus tard. (code : `{error_reference}`)"
                    )
                )
            except Exception:  # pragma: no cover - fallback logging
                logger.error(
                    "Impossible d'envoyer le message d'erreur à l'utilisateur",
                    exc_info=True,
                    extra=log_context,
                )

    @commands.group(
        name="petauto",
        invoke_without_command=True,
        aliases=("autopet", "petsettings"),
    )
    async def pet_auto_settings(self, ctx: commands.Context) -> None:
        settings = await self.database.get_pet_auto_settings(ctx.author.id)
        lines = [
            f"Auto goldify : {'✅ activé' if settings['auto_goldify'] else '❌ désactivé'}",
            f"Auto rainbow : {'✅ activé' if settings['auto_rainbowify'] else '❌ désactivé'}",
        ]
        embed = embeds.info_embed(
            "\n".join(lines),
            title="Préférences d'amélioration automatique",
        )
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        embed.set_footer(
            text="Utilise e!petauto gold [on/off] ou e!petauto rainbow [on/off] pour modifier ces options."
        )
        await ctx.send(embed=embed)

    @pet_auto_settings.command(name="gold", aliases=("goldify", "or"))
    async def pet_auto_gold(self, ctx: commands.Context, state: str | None = None) -> None:
        value = self._parse_toggle_argument(state)
        if value is None:
            await ctx.send(
                embed=embeds.error_embed(
                    "Précise `on` ou `off` pour activer ou désactiver l'auto goldify."
                )
            )
            return
        settings = await self.database.set_pet_auto_settings(
            ctx.author.id, auto_goldify=value
        )
        status = "activé" if settings["auto_goldify"] else "désactivé"
        await ctx.send(
            embed=embeds.success_embed(
                f"Auto goldify {status}.", title="Préférence mise à jour"
            )
        )

    @pet_auto_settings.command(name="rainbow", aliases=("rainbowify", "rb"))
    async def pet_auto_rainbow(
        self, ctx: commands.Context, state: str | None = None
    ) -> None:
        value = self._parse_toggle_argument(state)
        if value is None:
            await ctx.send(
                embed=embeds.error_embed(
                    "Précise `on` ou `off` pour activer ou désactiver l'auto rainbow."
                )
            )
            return
        settings = await self.database.set_pet_auto_settings(
            ctx.author.id, auto_rainbowify=value
        )
        status = "activé" if settings["auto_rainbowify"] else "désactivé"
        await ctx.send(
            embed=embeds.success_embed(
                f"Auto rainbow {status}.", title="Préférence mise à jour"
            )
        )

    @commands.command(name="eggs", aliases=("zones", "zone"))
    async def eggs(self, ctx: commands.Context) -> None:
        await self._send_egg_overview(ctx)

    @commands.command(name="eggindex", aliases=("eggdex", "oeufsdex", "oeufindex"))
    async def egg_index(self, ctx: commands.Context) -> None:
        embed = embeds.egg_index_embed(eggs=PET_EGG_DEFINITIONS)
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)

        if self._embed_length(embed) > 5_500:
            try:
                dm = await ctx.author.create_dm()
                await dm.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                await ctx.send(
                    embed=embeds.error_embed(
                        "Impossible de t'envoyer l'index des œufs en message privé. Active tes MP et réessaie."
                    )
                )
                return

            await ctx.send(
                embed=embeds.info_embed(
                    "L'index complet est un peu long, je te l'ai envoyé en message privé.",
                    title="Index des œufs",
                )
            )
            return

        await ctx.send(embed=embed)

    @commands.command(
        name="mastery",
        aliases=("masteries", "maitrise", "maitrises"),
    )
    async def mastery_overview(self, ctx: commands.Context) -> None:
        definitions = self._mastery_definitions
        try:
            progress_rows = await asyncio.gather(
                *[
                    self.database.get_mastery_progress(ctx.author.id, mastery.slug)
                    for mastery in definitions
                ]
            )
        except Exception:
            logger.exception(
                "Impossible de récupérer la progression des maîtrises",
                extra={"user_id": ctx.author.id},
            )
            await ctx.send(
                embed=embeds.error_embed(
                    "Impossible de récupérer tes maîtrises pour le moment. "
                    "Réessaie plus tard."
                )
            )
            return

        progress_map = {
            mastery.slug: data for mastery, data in zip(definitions, progress_rows)
        }
        embed = embeds.mastery_overview_embed(
            member=ctx.author,
            masteries=definitions,
            progress=progress_map,
        )
        view = MasteryOverviewView(ctx, definitions, progress_map)
        message = await ctx.send(embed=embed, view=view)
        view.message = message

    @commands.command(name="pets", aliases=("collection",))
    async def pets_command(self, ctx: commands.Context) -> None:
        records = await self.database.get_user_pets(ctx.author.id)
        market_values = await self.database.get_pet_market_values()
        pets = self._sort_pets_for_display(records, market_values)
        active_income = sum(int(pet["income"]) for pet in pets if pet.get("is_active"))
        per_page = 8
        total_count = len(pets)
        page_count = max(1, math.ceil(total_count / per_page))
        if page_count <= 1:
            embed = embeds.pet_collection_embed(
                member=ctx.author,
                pets=pets,
                total_count=total_count,
                total_income_per_hour=active_income,
                page=1,
                page_count=1,
                huge_descriptions=HUGE_PET_SOURCES,
            )
            await ctx.send(embed=embed)
        else:
            view = PetInventoryView(
                ctx=ctx,
                pets=pets,
                total_income=active_income,
                per_page=per_page,
                huge_descriptions=HUGE_PET_SOURCES,
            )
            embed = view.build_embed()
            message = await ctx.send(embed=embed, view=view)
            view.message = message

    @commands.command(name="index", aliases=("petindex", "dex"))
    async def pet_index_command(self, ctx: commands.Context) -> None:
        (
            records,
            pet_counts_by_id,
            market_values_by_id,
        ) = await asyncio.gather(
            self.database.get_user_pets(ctx.author.id),
            self.database.get_pet_counts(),
            self.database.get_pet_market_values(),
        )
        owned_names = self._owned_pet_names(records)
        count_lookup: Dict[str, int] = {}
        market_lookup: Dict[str, int] = {}
        for definition in self._definitions:
            pet_id = self._pet_ids.get(definition.name)
            if pet_id is None:
                continue
            key = definition.name.casefold()
            count_lookup[key] = int(pet_counts_by_id.get(pet_id, 0))
            market_lookup[key] = self._resolve_market_value(
                market_values_by_id,
                pet_id=pet_id,
                is_gold=False,
                is_rainbow=False,
                is_galaxy=False,
                is_shiny=False,
            )
        embed = embeds.pet_index_embed(
            member=ctx.author,
            pet_definitions=self._definitions,
            owned_names=owned_names,
            huge_descriptions=HUGE_PET_SOURCES,
            pet_counts=count_lookup,
            market_values=market_lookup,
        )
        await ctx.send(embed=embed)

    @commands.command(name="equipbest", aliases=("bestpets", "autoequip"))
    async def equip_best_pets(self, ctx: commands.Context) -> None:
        await self.database.ensure_user(ctx.author.id)
        rows = await self.database.get_user_pets(ctx.author.id)
        if not rows:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Tu n'as pas encore de pet à équiper. Ouvre un œuf avec `e!openbox`."
                )
            )
            return

        available_rows = [row for row in rows if not bool(row.get("on_market"))]
        if not available_rows:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Tous tes pets sont actuellement listés sur ton stand. Retire-en un avec `e!stand remove` avant d'utiliser cette commande."
                )
            )
            return

        max_slots = await self.database.get_pet_slot_limit(ctx.author.id)
        if max_slots <= 0:
            await ctx.send(
                embed=embeds.error_embed(
                    "Impossible d'équiper un pet pour le moment. Monte en grade pour débloquer un premier slot."
                )
            )
            return

        best_non_huge_income = await self.database.get_best_non_huge_income(ctx.author.id)
        entry_by_id: Dict[int, Dict[str, Any]] = {}
        scored_entries: List[Dict[str, Any]] = []
        for row in available_rows:
            user_pet_id = int(row.get("id") or 0)
            if user_pet_id <= 0:
                continue
            data = self._convert_record(row, best_non_huge_income=best_non_huge_income)
            income = int(data.get("base_income_per_hour", 0))
            rarity = str(data.get("rarity", ""))
            rarity_rank = PET_RARITY_ORDER.get(rarity, -1)
            acquired_at = row.get("acquired_at")
            if isinstance(acquired_at, datetime):
                acquired_sort = acquired_at.timestamp()
            else:
                acquired_sort = float("inf")
            name = str(data.get("name", "Pet"))
            entry = {
                "id": user_pet_id,
                "record": row,
                "data": data,
                "income": income,
                "rarity_rank": rarity_rank,
                "acquired_sort": acquired_sort,
                "name": name,
            }
            entry_by_id[user_pet_id] = entry
            scored_entries.append(entry)

        if not scored_entries:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Aucun pet disponible n'a pu être évalué. Vérifie que tes pets ne sont pas verrouillés sur le marché."
                )
            )
            return

        scored_entries.sort(
            key=lambda item: (
                -int(item.get("income", 0)),
                -int(item.get("rarity_rank", -1)),
                float(item.get("acquired_sort", float("inf"))),
                str(item.get("name", "")),
            )
        )
        desired_entries = scored_entries[:max_slots]
        desired_ids = {int(entry["id"]) for entry in desired_entries if int(entry["id"]) > 0}

        if not desired_ids:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Tu n'as aucun pet libre à équiper pour le moment."
                )
            )
            return

        current_active_ids = {int(row.get("id") or 0) for row in rows if bool(row.get("is_active"))}
        to_deactivate = [pet_id for pet_id in current_active_ids if pet_id not in desired_ids]
        to_activate = [pet_id for pet_id in desired_ids if pet_id not in current_active_ids]

        removed_names: List[str] = []
        for user_pet_id in to_deactivate:
            try:
                await self.database.deactivate_user_pet(ctx.author.id, user_pet_id)
            except DatabaseError as exc:
                await ctx.send(embed=embeds.error_embed(str(exc)))
                return
            entry = entry_by_id.get(user_pet_id)
            if entry:
                removed_names.append(entry.get("name", "Pet"))

        added_names: List[str] = []
        for user_pet_id in to_activate:
            try:
                await self.database.activate_user_pet(ctx.author.id, user_pet_id)
            except ActivePetLimitError as exc:
                message = (
                    "❌ Tous tes slots sont déjà utilisés "
                    f"({exc.active}/{exc.limit}). Déséquipe un pet avant de relancer la commande."
                )
                await ctx.send(embed=embeds.error_embed(message))
                return
            except DatabaseError as exc:
                await ctx.send(embed=embeds.error_embed(str(exc)))
                return
            entry = entry_by_id.get(user_pet_id)
            if entry:
                added_names.append(entry.get("name", "Pet"))

        updated_rows = await self.database.get_user_pets(ctx.author.id)
        active_rows = [row for row in updated_rows if bool(row.get("is_active"))]
        pets_data = await self._prepare_pet_data(ctx.author.id, active_rows)

        active_entries: List[Dict[str, Any]] = []
        for row, data in zip(active_rows, pets_data):
            user_pet_id = int(row.get("id") or 0)
            income = int(data.get("base_income_per_hour", 0))
            rarity = str(data.get("rarity", ""))
            active_entries.append(
                {
                    "id": user_pet_id,
                    "data": data,
                    "income": income,
                    "rarity_rank": PET_RARITY_ORDER.get(rarity, -1),
                    "name": str(data.get("name", "Pet")),
                }
            )

        active_entries.sort(
            key=lambda item: (
                -int(item.get("income", 0)),
                -int(item.get("rarity_rank", -1)),
                str(item.get("name", "")),
            )
        )

        total_income = sum(int(item.get("income", 0)) for item in active_entries)
        summary_lines: List[str] = []
        if added_names or removed_names:
            if added_names:
                summary_lines.append(
                    "✅ Activés : " + ", ".join(f"**{name}**" for name in added_names)
                )
            if removed_names:
                summary_lines.append(
                    "♻️ Retirés : " + ", ".join(f"**{name}**" for name in removed_names)
                )
        else:
            summary_lines.append("🔄 Tes meilleurs pets étaient déjà équipés.")

        summary_lines.append(
            f"Slots utilisés : **{len(active_entries)}/{max_slots}**"
        )
        summary_lines.append(
            f"Revenus actifs : **{embeds.format_currency(total_income)}**/h"
        )

        if active_entries:
            detail_lines = []
            for index, item in enumerate(active_entries, start=1):
                data = item.get("data", {})
                name = str(data.get("name", "Pet"))
                income = int(item.get("income", 0))
                markers: List[str] = []
                if bool(data.get("is_rainbow")):
                    markers.append("🌈")
                elif bool(data.get("is_gold")):
                    markers.append("🥇")
                if bool(data.get("is_huge")):
                    markers.append("✨")
                marker_text = " ".join(markers)
                rarity = str(data.get("rarity", ""))
                line = (
                    f"{index}. {marker_text} **{name}** ({rarity}) — {income:,} PB/h"
                ).replace(",", " ")
                detail_lines.append(line.strip())
            summary_lines.append("")
            summary_lines.extend(detail_lines)

        description = "\n".join(summary_lines)
        embed = embeds.success_embed(
            description,
            title="Équipe de pets optimisée",
        )
        embed.set_author(
            name=ctx.author.display_name,
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.send(embed=embed)

    async def _fetch_gemshop_state(self, user_id: int) -> GemshopState:
        grade_level = await self.database.get_grade_level(user_id)
        extra_slots = await self.database.get_extra_pet_slots(user_id)
        base_capacity = BASE_PET_SLOTS + grade_level
        hard_cap = PET_SLOT_MAX_CAPACITY
        total_slots = min(hard_cap, base_capacity + extra_slots)
        max_extra_allowed = max(0, hard_cap - base_capacity)
        next_cost: int | None = None
        if (
            max_extra_allowed > 0
            and extra_slots < max_extra_allowed
            and total_slots < hard_cap
        ):
            next_cost = self._compute_slot_purchase_cost(extra_slots)
        return GemshopState(
            grade_level=grade_level,
            base_capacity=base_capacity,
            extra_slots=extra_slots,
            hard_cap=hard_cap,
            total_slots=total_slots,
            max_extra_allowed=max_extra_allowed,
            next_cost=next_cost,
        )

    def _render_gemshop_embed(
        self, user: discord.abc.User, state: GemshopState
    ) -> discord.Embed:
        lines = [
            f"• Slots équipables : **{state.total_slots}/{state.hard_cap}**",
            f"• Slots via les grades : **{state.base_capacity}**",
            f"• Slots achetés : **{state.extra_slots}**",
        ]

        if state.can_purchase and state.next_cost is not None:
            lines.append(
                f"• Prochain slot : **{self._format_slot_cost(state.next_cost)}**"
            )
            lines.append(
                "Appuie sur le bouton ci-dessous ou utilise `e!gemshop buy` pour acheter un slot."
            )
        else:
            if state.has_reached_hard_cap or state.base_capacity >= state.hard_cap:
                lines.append(
                    "Tu as déjà atteint la capacité maximale de pets équipés."
                )
            elif state.extra_slots >= state.max_extra_allowed:
                lines.append(
                    "Tu devras monter en grade pour débloquer de nouveaux achats de slots."
                )
            else:
                lines.append("Le magasin est temporairement indisponible.")

        description = "\n".join(lines)
        embed = embeds.info_embed(description, title="💎 Gemshop")
        embed.set_author(
            name=user.display_name,
            icon_url=user.display_avatar.url,
        )
        if state.can_purchase:
            embed.set_footer(text="Le prix augmente à chaque slot acheté.")
        return embed

    async def _attempt_gemshop_purchase(
        self, user: discord.abc.User
    ) -> GemshopPurchaseResult:
        state = await self._fetch_gemshop_state(user.id)

        if state.has_reached_hard_cap or state.base_capacity >= state.hard_cap:
            embed = embeds.error_embed(
                "Tu as déjà atteint la capacité maximale de pets équipés."
            )
            return GemshopPurchaseResult(embed=embed, state=state, success=False)
        if state.max_extra_allowed <= 0:
            embed = embeds.error_embed(
                "Tes grades te donnent déjà accès à tous les slots disponibles."
            )
            return GemshopPurchaseResult(embed=embed, state=state, success=False)
        if state.extra_slots >= state.max_extra_allowed:
            embed = embeds.error_embed(
                "Tu devras monter en grade pour débloquer de nouveaux achats de slots."
            )
            return GemshopPurchaseResult(embed=embed, state=state, success=False)

        price = self._compute_slot_purchase_cost(state.extra_slots)
        balance_after: int | None = None

        if PET_SLOT_SHOP_CURRENCY == "gem":
            balance = await self.database.fetch_gems(user.id)
            if balance < price:
                missing = price - balance
                embed = embeds.error_embed(
                    f"Il te manque {self._format_slot_cost(missing)} pour acheter ce slot."
                )
                return GemshopPurchaseResult(embed=embed, state=state, success=False)
            _, balance_after = await self.database.increment_gems(
                user.id,
                -price,
                transaction_type="gemshop_slot",
                description="Achat slot supplémentaire",
            )
        else:
            balance = await self.database.fetch_balance(user.id)
            if balance < price:
                missing = price - balance
                embed = embeds.error_embed(
                    f"Il te manque {self._format_slot_cost(missing)} pour acheter ce slot."
                )
                return GemshopPurchaseResult(embed=embed, state=state, success=False)
            _, balance_after = await self.database.increment_balance(
                user.id,
                -price,
                transaction_type="gemshop_slot",
                description="Achat slot supplémentaire",
            )

        _new_extra, added = await self.database.add_extra_pet_slot(
            user.id, grade_level=state.grade_level
        )
        if not added:
            if PET_SLOT_SHOP_CURRENCY == "gem":
                await self.database.increment_gems(
                    user.id,
                    price,
                    transaction_type="gemshop_refund",
                    description="Remboursement slot pets",
                )
            else:
                await self.database.increment_balance(
                    user.id,
                    price,
                    transaction_type="gemshop_refund",
                    description="Remboursement slot pets",
                )
            new_state = await self._fetch_gemshop_state(user.id)
            embed = embeds.error_embed(
                "Impossible d'ajouter un slot supplémentaire pour le moment. Tes fonds ont été remboursés."
            )
            return GemshopPurchaseResult(embed=embed, state=new_state, success=False)

        new_state = await self._fetch_gemshop_state(user.id)
        lines = [
            f"Slot acheté pour {self._format_slot_cost(price)}.",
            f"Tu peux maintenant équiper **{new_state.total_slots}** pet{'s' if new_state.total_slots > 1 else ''}.",
        ]
        if PET_SLOT_SHOP_CURRENCY == "gem" and balance_after is not None:
            lines.append(f"Gemmes restantes : {embeds.format_gems(balance_after)}")
        elif balance_after is not None:
            lines.append(f"Solde restant : {embeds.format_currency(balance_after)}")

        if new_state.can_purchase and new_state.next_cost is not None:
            lines.append(
                f"Prochain slot : {self._format_slot_cost(new_state.next_cost)}."
            )
            lines.append("Utilise le bouton du magasin ou `e!gemshop buy` (`e!shop buy`).")
        else:
            if new_state.has_reached_hard_cap:
                lines.append("Tu as atteint la capacité maximale de 40 pets équipés.")
            elif new_state.extra_slots >= new_state.max_extra_allowed:
                lines.append(
                    "Tu as atteint la limite de slots disponible pour ton grade actuel."
                )

        embed = embeds.success_embed("\n".join(lines), title="💎 Gemshop")
        return GemshopPurchaseResult(embed=embed, state=new_state, success=True)

    @commands.command(
        name="gemshop",
        aliases=("shop", "gem", "gems", "gemmes"),
    )
    async def gemshop(self, ctx: commands.Context, action: str | None = None) -> None:
        await self.database.ensure_user(ctx.author.id)

        state = await self._fetch_gemshop_state(ctx.author.id)

        normalized_action = (
            (action or "").strip().lower().replace(" ", "").replace("-", "")
        )
        purchase_aliases = {
            "buy",
            "acheter",
            "slot",
            "buyslot",
            "acheterslot",
            "acheterunslot",
            "achete",
        }
        attempting_purchase = normalized_action in purchase_aliases

        if attempting_purchase:
            result = await self._attempt_gemshop_purchase(ctx.author)
            await ctx.send(embed=result.embed)
            return

        embed = self._render_gemshop_embed(ctx.author, state)
        view = GemshopView(self, ctx, state)
        message = await ctx.send(embed=embed, view=view)
        view.attach_message(message)

    @commands.command(
        name="distributeur",
        aliases=("mexico", "mexicodispenser", "dispenser"),
    )
    async def mexico_dispenser(self, ctx: commands.Context) -> None:
        await self.database.ensure_user(ctx.author.id)

        zone = self._zones.get(MEXICO_ZONE_SLUG)
        if zone is None:
            await ctx.send(
                embed=embeds.error_embed(
                    "La zone de Mexico est introuvable pour le moment."
                )
            )
            return

        unlocked = await self.database.has_unlocked_zone(
            ctx.author.id, MEXICO_ZONE_SLUG
        )
        if not unlocked:
            await ctx.send(
                embed=embeds.error_embed(
                    f"Tu dois d'abord débloquer {zone.name} pour utiliser ce distributeur."
                )
            )
            return

        last_claim = await self.database.get_mexico_dispenser_last_claim(ctx.author.id)
        now = datetime.now(timezone.utc)
        if last_claim is not None:
            ready_at = last_claim + MEXICO_DISTRIBUTOR_COOLDOWN
            if ready_at > now:
                ready_text = discord.utils.format_dt(ready_at, style="R")
                await ctx.send(
                    embed=embeds.warning_embed(
                        f"Le distributeur se recharge encore. Reviens {ready_text}."
                    )
                )
                return

        potion_definition = random.choice(POTION_DEFINITIONS)
        await self.database.add_user_potion(ctx.author.id, potion_definition.slug)

        eligible_pets = [pet for pet in self._definitions if not pet.is_huge]
        if not eligible_pets:
            await ctx.send(
                embed=embeds.error_embed(
                    "Aucun pet n'est disponible pour le distributeur en ce moment."
                )
            )
            return

        pet_definition = random.choice(eligible_pets)
        pet_id = self._pet_ids.get(pet_definition.name)
        if pet_id is None:
            await self._resync_pets()
            pet_id = self._pet_ids.get(pet_definition.name)
        if pet_id is None:
            await ctx.send(
                embed=embeds.error_embed(
                    "Impossible de récupérer le pet sélectionné. Réessaie dans quelques instants."
                )
            )
            return

        try:
            await self.database.add_user_pet(
                ctx.author.id,
                pet_id,
                is_gold=True,
                is_rainbow=False,
                is_galaxy=False,
                is_shiny=False,
            )
        except DatabaseError:
            logger.exception(
                "Impossible d'ajouter un pet doré depuis le distributeur Mexico",
                extra={"user_id": ctx.author.id, "pet": pet_definition.name},
            )
            await ctx.send(
                embed=embeds.error_embed(
                    "Impossible de livrer le pet doré pour le moment. Réessaie plus tard."
                )
            )
            return

        await self.database.record_mexico_dispenser_claim(ctx.author.id)

        next_ready = now + MEXICO_DISTRIBUTOR_COOLDOWN
        potion_line = f"🧪 Potion reçue : **{potion_definition.name}**"
        emoji = pet_emoji(pet_definition.name)
        pet_line = f"🥇 Pet reçu : {emoji} **{pet_definition.name}** (version or)"
        cooldown_line = (
            f"⏳ Prochain distributeur disponible {discord.utils.format_dt(next_ready, style='R')}"
        )

        description = "\n".join(
            [
                potion_line,
                pet_line,
                cooldown_line,
                "Le distributeur se recharge toutes les 10 minutes.",
            ]
        )

        await ctx.send(
            embed=embeds.success_embed(
                description,
                title="🎁 Distributeur de Mexico",
            )
        )
        await self._maybe_award_enchantment(ctx, "distributor")

    @commands.command(name="equip")
    async def equip(self, ctx: commands.Context, *, pet_name: str) -> None:
        definition, rows, ordinal, variant = await self._resolve_user_pet_candidates(
            ctx,
            pet_name,
            include_active=True,
            include_inactive=True,
        )
        if definition is None:
            await ctx.send(embed=embeds.error_embed("Ce pet n'existe pas."))
            return

        if not rows:
            variant_text = ""
            if variant == "gold":
                variant_text = " doré"
            elif variant == "rainbow":
                variant_text = " rainbow"
            elif variant == "normal":
                variant_text = " classique"
            await ctx.send(
                embed=embeds.error_embed(
                    f"Tu ne possèdes aucun {definition.name}{variant_text} correspondant."
                )
            )
            return

        candidates = await self._make_candidates(ctx, rows)

        async def activate_candidate(candidate: Mapping[str, Any]) -> None:
            record = candidate.get("record", {})
            if bool(record.get("is_active")):
                await ctx.send(
                    embed=embeds.warning_embed(
                        f"⚠️ {definition.name} est déjà équipé ! Utilise `e!unequip {definition.name}` pour le retirer."
                    )
                )
                return

            user_pet_id = int(record.get("id") or 0)
            if user_pet_id <= 0:
                await ctx.send(embed=embeds.error_embed("Pet introuvable."))
                return

            try:
                db_record, active_count, max_slots = await self.database.activate_user_pet(
                    ctx.author.id,
                    user_pet_id,
                )
            except ActivePetLimitError as exc:
                message = (
                    "❌ Tous tes slots sont pleins "
                    f"({exc.active}/{exc.limit}) ! Options :\n"
                    "• Utilise `e!unequip <nom>` pour libérer un slot\n"
                    "• Ou utilise `e!swap <ancien> <nouveau>` pour remplacer directement"
                )
                await ctx.send(embed=embeds.error_embed(message))
                return
            except DatabaseError as exc:
                await ctx.send(embed=embeds.error_embed(str(exc)))
                return

            if db_record is None:
                await ctx.send(embed=embeds.error_embed("Pet introuvable."))
                return

            embed = await self._build_pet_equip_embed(
                ctx,
                db_record,
                activated=True,
                active_count=active_count,
                slot_limit=max_slots,
            )
            await ctx.send(embed=embed)

        if ordinal is not None:
            index = ordinal - 1
            if index < 0 or index >= len(candidates):
                await ctx.send(
                    embed=embeds.error_embed(
                        f"Tu as seulement {len(candidates)} exemplaires de {definition.name}."
                    )
                )
                return
            await activate_candidate(candidates[index])
            return

        if len(candidates) == 1:
            await activate_candidate(candidates[0])
            return

        selection = await self._prompt_pet_selection(
            ctx,
            title=f"Quel {definition.name} veux-tu équiper ?",
            description=f"Tu as {len(candidates)} exemplaires disponibles.",
            candidates=candidates,
        )
        if selection is None:
            return
        await activate_candidate(selection)

    @commands.command(name="unequip", aliases=("desequip", "remove"))
    async def unequip(self, ctx: commands.Context, *, pet_name: str) -> None:
        definition, rows, ordinal, variant = await self._resolve_user_pet_candidates(
            ctx,
            pet_name,
            include_active=True,
            include_inactive=False,
        )
        if definition is None:
            await ctx.send(embed=embeds.error_embed("Ce pet n'existe pas."))
            return

        if not rows:
            await ctx.send(
                embed=embeds.warning_embed(
                    f"⚠️ {definition.name} n'est pas équipé actuellement. Utilise `e!pets` pour voir tes pets actifs."
                )
            )
            return

        candidates = await self._make_candidates(ctx, rows)

        async def deactivate_candidate(candidate: Mapping[str, Any]) -> None:
            record = candidate.get("record", {})
            if not bool(record.get("is_active")):
                await ctx.send(
                    embed=embeds.warning_embed(
                        f"⚠️ {definition.name} n'est pas équipé actuellement. Utilise `e!pets` pour voir tes pets actifs."
                    )
                )
                return

            user_pet_id = int(record.get("id") or 0)
            if user_pet_id <= 0:
                await ctx.send(embed=embeds.error_embed("Pet introuvable."))
                return

            try:
                db_record, active_count, max_slots = await self.database.deactivate_user_pet(
                    ctx.author.id,
                    user_pet_id,
                )
            except DatabaseError as exc:
                await ctx.send(embed=embeds.error_embed(str(exc)))
                return

            if db_record is None:
                await ctx.send(embed=embeds.error_embed("Pet introuvable."))
                return

            embed = await self._build_pet_equip_embed(
                ctx,
                db_record,
                activated=False,
                active_count=active_count,
                slot_limit=max_slots,
            )
            await ctx.send(embed=embed)

        if ordinal is not None:
            index = ordinal - 1
            if index < 0 or index >= len(candidates):
                await ctx.send(
                    embed=embeds.error_embed(
                        f"Tu as seulement {len(candidates)} exemplaires actifs de {definition.name}."
                    )
                )
                return
            await deactivate_candidate(candidates[index])
            return

        if len(candidates) == 1:
            await deactivate_candidate(candidates[0])
            return

        selection = await self._prompt_pet_selection(
            ctx,
            title=f"Quel {definition.name} veux-tu retirer ?",
            description=f"Tu as {len(candidates)} exemplaires équipés.",
            candidates=candidates,
        )
        if selection is None:
            return
        await deactivate_candidate(selection)

    @commands.command(name="swap")
    async def swap(self, ctx: commands.Context, pet_out: str, *, pet_in: str) -> None:
        out_definition, out_rows, out_ordinal, _ = await self._resolve_user_pet_candidates(
            ctx,
            pet_out,
            include_active=True,
            include_inactive=False,
        )
        if out_definition is None:
            await ctx.send(embed=embeds.error_embed("Le pet à retirer est introuvable."))
            return
        if not out_rows:
            await ctx.send(
                embed=embeds.warning_embed(
                    f"⚠️ {out_definition.name} n'est pas équipé actuellement. Utilise `e!pets` pour voir tes pets actifs."
                )
            )
            return

        in_definition, in_rows, in_ordinal, in_variant = await self._resolve_user_pet_candidates(
            ctx,
            pet_in,
            include_active=True,
            include_inactive=True,
        )
        if in_definition is None:
            await ctx.send(embed=embeds.error_embed("Le pet à équiper est introuvable."))
            return
        if not in_rows:
            variant_text = ""
            if in_variant == "gold":
                variant_text = " doré"
            elif in_variant == "rainbow":
                variant_text = " rainbow"
            elif in_variant == "normal":
                variant_text = " classique"
            await ctx.send(
                embed=embeds.error_embed(
                    f"Tu ne possèdes aucun {in_definition.name}{variant_text} correspondant."
                )
            )
            return

        out_candidates = await self._make_candidates(ctx, out_rows)
        in_candidates = await self._make_candidates(ctx, in_rows)

        async def resolve_candidate(
            definition: PetDefinition,
            candidates: List[Mapping[str, Any]],
            ordinal: Optional[int],
            *,
            prompt: str,
        ) -> Optional[Mapping[str, Any]]:
            if ordinal is not None:
                index = ordinal - 1
                if index < 0 or index >= len(candidates):
                    await ctx.send(
                        embed=embeds.error_embed(
                            f"Tu as seulement {len(candidates)} exemplaires de {definition.name}."
                        )
                    )
                    return None
                return candidates[index]

            if len(candidates) == 1:
                return candidates[0]

            return await self._prompt_pet_selection(
                ctx,
                title=prompt,
                description=f"Tu as {len(candidates)} exemplaires disponibles.",
                candidates=candidates,
            )

        selected_out = await resolve_candidate(
            out_definition,
            out_candidates,
            out_ordinal,
            prompt=f"Quel {out_definition.name} veux-tu retirer ?",
        )
        if selected_out is None:
            return

        selected_in = await resolve_candidate(
            in_definition,
            in_candidates,
            in_ordinal,
            prompt=f"Quel {in_definition.name} veux-tu équiper ?",
        )
        if selected_in is None:
            return

        out_id = int(selected_out["record"].get("id") or 0)
        in_id = int(selected_in["record"].get("id") or 0)
        if out_id <= 0 or in_id <= 0:
            await ctx.send(embed=embeds.error_embed("Impossible de déterminer les pets sélectionnés."))
            return

        if out_id == in_id:
            await ctx.send(embed=embeds.error_embed("Tu dois sélectionner deux pets différents pour le swap."))
            return

        try:
            removed, added, active_count, max_slots = await self.database.swap_active_pets(
                ctx.author.id,
                out_id,
                in_id,
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        out_data = selected_out.get("data", {})
        out_label = str(out_data.get("name", out_definition.name))
        if out_data.get("is_rainbow"):
            out_label += " 🌈"
        elif out_data.get("is_gold"):
            out_label += " 🥇"

        embed = await self._build_pet_equip_embed(
            ctx,
            added,
            activated=True,
            active_count=active_count,
            slot_limit=max_slots,
        )
        embed.add_field(
            name="Swap effectué",
            value=f"{out_label} se repose désormais.",
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.command(name="fuse")
    async def fuse(self, ctx: commands.Context, *user_pet_ids: int) -> None:
        if not user_pet_ids:
            await ctx.send(
                embed=embeds.info_embed(
                    "Utilise `e!fuse <id1> <id2> … <id10>` pour sacrifier 10 pets et en obtenir un nouveau."
                )
            )
            return

        unique_ids = []
        for value in user_pet_ids:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in unique_ids:
                unique_ids.append(parsed)
            if len(unique_ids) >= 10:
                break

        if len(unique_ids) < 10:
            await ctx.send(
                embed=embeds.error_embed(
                    "La machine a besoin de **10 pets différents**."
                    " Utilise `e!pets` puis repère la colonne `ID` pour noter ceux qui sont libres.",
                )
            )
            return

        pet_mastery_progress = await self.database.get_mastery_progress(
            ctx.author.id, PET_MASTERY.slug
        )
        pet_mastery_level = int(pet_mastery_progress.get("level", 1))
        pet_perks = _compute_pet_mastery_perks(pet_mastery_level)
        if not pet_perks.fuse_unlocked:
            await ctx.send(
                embed=embeds.error_embed(
                    "Atteins le niveau 5 de Maîtrise des pets pour débloquer la machine de fusion."
                    f" (Niveau actuel : {pet_mastery_level}).",
                )
            )
            return

        clan_row = await self.database.get_user_clan(ctx.author.id)
        clan_shiny_multiplier = 1.0
        if clan_row is not None:
            clan_shiny_multiplier = max(
                1.0, float(clan_row.get("shiny_luck_multiplier") or 1.0)
            )

        non_huge_definitions = [
            pet for pet in PET_DEFINITIONS if not getattr(pet, "is_huge", False)
        ]
        if not non_huge_definitions:
            await ctx.send(embed=embeds.error_embed("Aucun pet disponible pour la fusion."))
            return

        weights = [max(0.0001, float(getattr(pet, "drop_rate", 0.0)) or 0.0001) for pet in non_huge_definitions]

        total_outputs = 1
        bonus_label = None
        if pet_perks.fuse_triple_chance > 0 and random.random() < pet_perks.fuse_triple_chance:
            total_outputs = 3
            bonus_label = "🔥 Chance triple !"
        elif pet_perks.fuse_double_chance > 0 and random.random() < pet_perks.fuse_double_chance:
            total_outputs = 2
            bonus_label = "⚙️ Chance double !"

        selected_defs = random.choices(non_huge_definitions, weights=weights, k=total_outputs)

        def _roll_shiny(base_chance: float) -> bool:
            chance = max(0.0, float(base_chance))
            if chance <= 0:
                return False
            chance *= float(pet_perks.egg_shiny_multiplier)
            chance *= clan_shiny_multiplier
            return random.random() < min(1.0, chance)

        results: List[Dict[str, Any]] = []
        consumed_ids = unique_ids[:10]

        primary_definition = selected_defs[0]
        primary_shiny = _roll_shiny(pet_perks.egg_shiny_chance)
        try:
            primary_record = await self.database.fuse_user_pets(
                ctx.author.id,
                consumed_ids,
                self._pet_ids[primary_definition.name],
                make_shiny=primary_shiny,
                result_is_huge=primary_definition.is_huge,
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        results.append(
            self._convert_record(primary_record, best_non_huge_income=None)
        )

        for extra_definition in selected_defs[1:]:
            extra_shiny = _roll_shiny(pet_perks.egg_shiny_chance)
            extra_record = await self.database.add_user_pet(
                ctx.author.id,
                self._pet_ids[extra_definition.name],
                is_huge=extra_definition.is_huge,
                is_shiny=extra_shiny,
            )
            results.append(
                self._convert_record(extra_record, best_non_huge_income=None)
            )

        embed = embeds.success_embed("Résultats de la fusion", title="🛠️ Machine de fusion")
        lines = []
        for entry in results:
            name = str(entry.get("name", "Pet"))
            income = int(entry.get("base_income_per_hour", 0))
            tags: List[str] = []
            if entry.get("is_galaxy"):
                tags.append("Galaxy")
            elif entry.get("is_rainbow"):
                tags.append("Rainbow")
            elif entry.get("is_gold"):
                tags.append("Gold")
            if entry.get("is_shiny"):
                tags.append("Shiny")
            suffix = f" ({', '.join(tags)})" if tags else ""
            lines.append(f"{embeds.format_currency(income)}/h — **{name}**{suffix}")

        if bonus_label:
            lines.append(bonus_label)

        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

        mastery_update = await self.database.add_mastery_experience(
            ctx.author.id, PET_MASTERY.slug, len(consumed_ids)
        )
        await self._handle_mastery_notifications(
            ctx, mastery_update, mastery=PET_MASTERY
        )

    @commands.command(name="goldify", aliases=("gold", "fusion"))
    async def goldify(self, ctx: commands.Context, *, pet_name: str | None = None) -> None:
        if not pet_name:
            await ctx.send(
                embed=embeds.info_embed(
                    "Utilise `e!goldify <nom du pet>` pour fusionner **"
                    f"{GOLD_PET_COMBINE_REQUIRED}** exemplaires identiques en une version or."
                )
            )
            return

        lookup = pet_name.strip().lower()
        definition = self._definition_by_slug.get(lookup)
        if definition is None:
            await ctx.send(embed=embeds.error_embed("Ce pet n'existe pas."))
            return
        if definition.is_huge:
            await ctx.send(embed=embeds.error_embed("Les énormes pets ne peuvent pas devenir or."))
            return

        pet_id = self._pet_ids.get(definition.name)
        if pet_id is None:
            await ctx.send(embed=embeds.error_embed("Pet non synchronisé. Réessaie plus tard."))
            return

        pet_mastery_progress = await self.database.get_mastery_progress(
            ctx.author.id, PET_MASTERY.slug
        )
        pet_mastery_level = int(pet_mastery_progress.get("level", 1))
        pet_perks = _compute_pet_mastery_perks(pet_mastery_level)
        clan_row = await self.database.get_user_clan(ctx.author.id)
        clan_shiny_multiplier = 1.0
        if clan_row is not None:
            clan_shiny_multiplier = max(
                1.0, float(clan_row.get("shiny_luck_multiplier") or 1.0)
            )

        shiny_chance = (
            float(pet_perks.goldify_shiny_chance)
            * float(pet_perks.egg_shiny_multiplier)
            * clan_shiny_multiplier
        )
        make_shiny = random.random() < min(1.0, max(0.0, shiny_chance))

        try:
            record, consumed = await self.database.upgrade_pet_to_gold(
                ctx.author.id, pet_id, make_shiny=make_shiny
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        best_non_huge_income = await self.database.get_best_non_huge_income(ctx.author.id)
        pet_data = self._convert_record(record, best_non_huge_income=best_non_huge_income)
        market_values = await self.database.get_pet_market_values()
        pet_identifier = int(pet_data.get("pet_id", 0))
        pet_data["market_value"] = self._resolve_market_value(
            market_values,
            pet_id=pet_identifier,
            is_gold=bool(pet_data.get("is_gold")),
            is_rainbow=bool(pet_data.get("is_rainbow")),
            is_galaxy=bool(pet_data.get("is_galaxy")),
            is_shiny=bool(pet_data.get("is_shiny")),
        )
        reveal_embed = embeds.pet_reveal_embed(
            name=str(pet_data.get("name", definition.name)),
            rarity=str(pet_data.get("rarity", definition.rarity)),
            image_url=str(pet_data.get("image_url", definition.image_url)),
            income_per_hour=int(pet_data.get("base_income_per_hour", definition.base_income_per_hour)),
            is_huge=bool(pet_data.get("is_huge", False)),
            is_gold=True,
            is_rainbow=bool(pet_data.get("is_rainbow", False)),
            is_galaxy=bool(pet_data.get("is_galaxy", False)),
            is_shiny=bool(pet_data.get("is_shiny", False)),
            market_value=int(pet_data.get("market_value", 0)),
        )
        reveal_embed.add_field(
            name="Fusion dorée",
            value=(
                f"{consumed} exemplaires combinés pour obtenir cette version or !\n"
                "Les pets utilisés ont été retirés de ton inventaire."
            ),
            inline=False,
        )
        user_pet_id = int(pet_data.get("id", 0))
        if user_pet_id:
            reveal_embed.set_footer(
                text=f"Utilise e!equip {definition.name} pour l'équiper !"
            )
        await ctx.send(embed=reveal_embed)

        mastery_update = await self.database.add_mastery_experience(
            ctx.author.id, PET_MASTERY.slug, max(1, int(consumed))
        )
        await self._handle_mastery_notifications(
            ctx, mastery_update, mastery=PET_MASTERY
        )

    @commands.command(name="rainbow", aliases=("rainbowify", "rb"))
    async def rainbow(self, ctx: commands.Context, *, pet_name: str) -> None:
        slug, _, _ = self._parse_pet_query(pet_name)
        if not slug:
            await ctx.send(embed=embeds.error_embed("Ce pet n'existe pas."))
            return

        definition = self._definition_by_slug.get(slug)
        if definition is None:
            await ctx.send(embed=embeds.error_embed("Ce pet n'existe pas."))
            return
        if definition.is_huge:
            await ctx.send(embed=embeds.error_embed("Les Huge pets ne peuvent pas être rainbow."))
            return

        pet_id = self._pet_ids.get(definition.name)
        if pet_id is None:
            await ctx.send(embed=embeds.error_embed("Pet non synchronisé."))
            return

        pet_mastery_progress = await self.database.get_mastery_progress(
            ctx.author.id, PET_MASTERY.slug
        )
        pet_mastery_level = int(pet_mastery_progress.get("level", 1))
        pet_perks = _compute_pet_mastery_perks(pet_mastery_level)
        clan_row = await self.database.get_user_clan(ctx.author.id)
        clan_shiny_multiplier = 1.0
        if clan_row is not None:
            clan_shiny_multiplier = max(
                1.0, float(clan_row.get("shiny_luck_multiplier") or 1.0)
            )

        shiny_chance = (
            float(pet_perks.rainbowify_shiny_chance)
            * float(pet_perks.egg_shiny_multiplier)
            * clan_shiny_multiplier
        )
        make_shiny = random.random() < min(1.0, max(0.0, shiny_chance))

        try:
            record, consumed = await self.database.upgrade_pet_to_rainbow(
                ctx.author.id, pet_id, make_shiny=make_shiny
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        pet_data = self._convert_record(record, best_non_huge_income=None)
        base_income = definition.base_income_per_hour
        rainbow_income = base_income * RAINBOW_PET_MULTIPLIER

        embed = embeds.pet_reveal_embed(
            name=definition.name,
            rarity=definition.rarity,
            image_url=definition.image_url,
            income_per_hour=rainbow_income,
            is_huge=False,
            is_gold=False,
            is_galaxy=False,
            is_rainbow=True,
            is_shiny=bool(pet_data.get("is_shiny", False)),
            market_value=0,
        )
        embed.add_field(
            name="🌈 Fusion Rainbow",
            value=(
                f"🎉 {consumed} pets GOLD fusionnés en 1 RAINBOW !\n"
                f"Puissance : **{rainbow_income:,} PB/h** ({RAINBOW_PET_MULTIPLIER}x le pet de base)\n"
                "Les pets utilisés ont été retirés de ton inventaire."
            ).replace(",", " "),
            inline=False,
        )

        await ctx.send(embed=embed)

        mastery_update = await self.database.add_mastery_experience(
            ctx.author.id, PET_MASTERY.slug, max(1, int(consumed))
        )
        await self._handle_mastery_notifications(
            ctx, mastery_update, mastery=PET_MASTERY
        )

    @commands.command(name="galaxy")
    async def galaxy(self, ctx: commands.Context, *, pet_name: str) -> None:
        slug, _, _ = self._parse_pet_query(pet_name)
        if not slug:
            await ctx.send(embed=embeds.error_embed("Ce pet n'existe pas."))
            return

        definition = self._definition_by_slug.get(slug)
        if definition is None:
            await ctx.send(embed=embeds.error_embed("Ce pet n'existe pas."))
            return
        if definition.is_huge:
            await ctx.send(embed=embeds.error_embed("Les Huge pets ne peuvent pas devenir galaxy."))
            return

        pet_id = self._pet_ids.get(definition.name)
        if pet_id is None:
            await ctx.send(embed=embeds.error_embed("Pet non synchronisé."))
            return

        pet_mastery_progress = await self.database.get_mastery_progress(
            ctx.author.id, PET_MASTERY.slug
        )
        pet_mastery_level = int(pet_mastery_progress.get("level", 1))
        pet_perks = _compute_pet_mastery_perks(pet_mastery_level)
        clan_row = await self.database.get_user_clan(ctx.author.id)
        clan_shiny_multiplier = 1.0
        if clan_row is not None:
            clan_shiny_multiplier = max(
                1.0, float(clan_row.get("shiny_luck_multiplier") or 1.0)
            )

        shiny_chance = (
            float(pet_perks.rainbowify_shiny_chance)
            * float(pet_perks.egg_shiny_multiplier)
            * clan_shiny_multiplier
        )
        make_shiny = random.random() < min(1.0, max(0.0, shiny_chance))

        try:
            record, consumed = await self.database.upgrade_pet_to_galaxy(
                ctx.author.id, pet_id, make_shiny=make_shiny
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        pet_data = self._convert_record(record, best_non_huge_income=None)
        base_income = definition.base_income_per_hour
        galaxy_income = base_income * GALAXY_PET_MULTIPLIER

        embed = embeds.pet_reveal_embed(
            name=definition.name,
            rarity=definition.rarity,
            image_url=definition.image_url,
            income_per_hour=galaxy_income,
            is_huge=False,
            is_gold=False,
            is_galaxy=True,
            is_rainbow=False,
            is_shiny=bool(pet_data.get("is_shiny", False)),
            market_value=0,
        )
        embed.add_field(
            name="🌌 Fusion Galaxy",
            value=(
                f"🎉 {consumed} pets RAINBOW fusionnés en 1 GALAXY !\n"
                f"Puissance : **{galaxy_income:,} PB/h** ({GALAXY_PET_MULTIPLIER}x le pet de base)\n"
                "Les pets utilisés ont été retirés de ton inventaire."
            ).replace(",", " "),
            inline=False,
        )

        await ctx.send(embed=embed)

        mastery_update = await self.database.add_mastery_experience(
            ctx.author.id, PET_MASTERY.slug, max(1, int(consumed))
        )
        await self._handle_mastery_notifications(
            ctx, mastery_update, mastery=PET_MASTERY
        )

    @commands.command(name="trade", aliases=("tradepet",))
    async def trade(
        self, ctx: commands.Context, member: discord.Member
    ) -> None:
        if member.id == ctx.author.id:
            await ctx.send(embed=embeds.error_embed("Tu ne peux pas lancer un trade avec toi-même."))
            return
        if member.bot:
            await ctx.send(embed=embeds.error_embed("Les bots ne peuvent pas participer aux trades."))
            return

        channel = ctx.channel
        parent: discord.TextChannel | None = None
        if isinstance(channel, discord.TextChannel):
            parent = channel
        elif isinstance(channel, discord.Thread) and isinstance(channel.parent, discord.TextChannel):
            parent = channel.parent
        if parent is None:
            await ctx.send(
                embed=embeds.error_embed("Les trades ne peuvent être lancés que depuis un salon textuel du serveur.")
            )
            return

        thread_name = f"trade-{ctx.author.display_name}-{member.display_name}"
        thread_name = thread_name.replace("/", "-")[:95]
        thread: discord.Thread | None = None
        try:
            thread = await parent.create_thread(
                name=thread_name,
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason="Trade interactif",
            )
        except discord.HTTPException:
            try:
                thread = await parent.create_thread(
                    name=thread_name,
                    type=discord.ChannelType.public_thread,
                    reason="Trade interactif",
                )
            except discord.HTTPException:
                await ctx.send(
                    embed=embeds.error_embed("Impossible de créer un fil pour ce trade.")
                )
                return

        with contextlib.suppress(discord.HTTPException, discord.Forbidden):
            await thread.add_user(ctx.author)
            await thread.add_user(member)

        session = TradeSession(self, thread, ctx.author, member)
        view = TradeView(session)
        embed = session.build_embed()
        message = await thread.send(
            content=f"{ctx.author.mention} {member.mention}",
            embed=embed,
            view=view,
        )
        session.message = message
        await ctx.send(
            embed=embeds.success_embed(
                f"Un fil de trade a été ouvert : {thread.mention}"
            )
        )

    @commands.command(name="multi")
    async def huge_multipliers(self, ctx: commands.Context) -> None:
        """Affiche le multiplicateur maximal de chaque Huge et Titanic."""

        multiplier_entries: list[tuple[float, str]] = []
        for name in HUGE_PET_NAMES:
            multiplier = float(
                get_huge_level_multiplier(name, HUGE_PET_LEVEL_CAP)
            )
            multiplier_entries.append((multiplier, name))

        if not multiplier_entries:
            await ctx.send(
                embed=embeds.info_embed(
                    "Aucun pet Huge ou Titanic n'est configuré pour le moment.",
                    title="📈 Multiplicateurs Huge & Titanic",
                )
            )
            return

        multiplier_entries.sort(key=lambda entry: (-entry[0], entry[1]))

        lines: list[str] = []
        for multiplier, name in multiplier_entries:
            emoji = PET_EMOJIS.get(name, PET_EMOJIS.get("default", "🐾"))
            label = f"x{multiplier:.2f}".rstrip("0").rstrip(".")
            lines.append(f"{emoji} **{name}** — {label}")

        header = (
            "Les multiplicateurs ci-dessous correspondent au niveau"
            f" {HUGE_PET_LEVEL_CAP} (maximum)."
        )
        embed = embeds.info_embed(
            header,
            title="📈 Multiplicateurs Huge & Titanic",
        )

        field_buffer: list[str] = []
        field_length = 0
        for line in lines:
            line_length = len(line)
            if field_buffer and field_length + 1 + line_length > 1024:
                embed.add_field(
                    name="\u200b",
                    value="\n".join(field_buffer),
                    inline=False,
                )
                field_buffer = [line]
                field_length = line_length
                continue

            if field_buffer:
                field_length += 1 + line_length
            else:
                field_length = line_length
            field_buffer.append(line)

        if field_buffer:
            embed.add_field(
                name="\u200b",
                value="\n".join(field_buffer),
                inline=False,
            )

        await ctx.send(embed=embed)

    @commands.command(name="luck")
    async def luck(self, ctx: commands.Context) -> None:
        """Affiche les chances d'obtention de chaque Huge et Titanic."""

        def _format_emoji(name: str) -> str:
            return PET_EMOJIS.get(name, PET_EMOJIS.get("default", "🐾"))

        egg_entries: list[tuple[float, str]] = []
        for egg in PET_EGG_DEFINITIONS:
            for definition in egg.pets:
                if not definition.is_huge:
                    continue
                if definition.drop_rate <= 0:
                    continue
                chance_pct = max(0.0, float(definition.drop_rate) * 100)
                emoji = _format_emoji(definition.name)
                line = f"{emoji} **{definition.name}** — {chance_pct:.4f}% dans {egg.name}"
                egg_entries.append((chance_pct, line))

        egg_entries.sort(key=lambda entry: entry[0], reverse=True)

        try:
            raffle_pool = await self.database.get_total_raffle_tickets()
        except Exception:
            raffle_pool = 0

        special_lines: list[str] = []
        frenzy_start, frenzy_end = get_egg_frenzy_window()
        frenzy_active = is_egg_frenzy_active()
        frenzy_start_utc = frenzy_start.astimezone(timezone.utc)
        frenzy_end_utc = frenzy_end.astimezone(timezone.utc)
        if frenzy_active:
            special_lines.append(
                "🍀 **Egg Frenzy** — bonus de +50% de chance actif jusqu'à "
                f"{discord.utils.format_dt(frenzy_end_utc, style='t')} "
                f"({discord.utils.format_dt(frenzy_end_utc, style='R')})."
            )
        else:
            special_lines.append(
                "🍀 Egg Frenzy quotidien : +50% de chance entre "
                f"{discord.utils.format_dt(frenzy_start_utc, style='t')} et "
                f"{discord.utils.format_dt(frenzy_end_utc, style='t')} "
                f"({discord.utils.format_dt(frenzy_start_utc, style='R')})."
            )
        # Huge Bull — daily raffle.
        bull_line = (
            f"{_format_emoji(HUGE_BULL_NAME)} **{HUGE_BULL_NAME}** — tirage quotidien via `e!raffle`."
        )
        if raffle_pool > 0:
            pool_display = f"{raffle_pool:,}".replace(",", " ")
            bull_line += (
                f" {pool_display} tickets sont déjà misés pour ce tirage. Miser depuis `e!raffle`."
            )
        else:
            bull_line += " Mise tes tickets depuis `e!raffle` pour participer (remise à zéro après chaque tirage)."
        special_lines.append(bull_line)

        min_kenji = MASTERMIND_HUGE_MIN_CHANCE * 100
        max_kenji = MASTERMIND_HUGE_MAX_CHANCE * 100
        special_lines.append(
            f"{_format_emoji(HUGE_KENJI_ONI_NAME)} **{HUGE_KENJI_ONI_NAME}** — récompense bonus du Mastermind"
            f" (de {min_kenji:.2f}% à {max_kenji:.2f}% selon tes tentatives et bonus)."
        )
        special_lines.append(
            f"{_format_emoji(HUGE_GALE_NAME)} **{HUGE_GALE_NAME}** — garanti après la 20ᵉ étape de la Millionaire Race."
        )
        special_lines.append(
            f"{_format_emoji(HUGE_MORTIS_NAME)} **{HUGE_MORTIS_NAME}** — réservé aux membres VIP (obtention garantie)."
        )
        special_lines.append(
            f"{_format_emoji(HUGE_GRIFF_NAME)} **{HUGE_GRIFF_NAME}** — distribué lors d'événements spéciaux du staff."
        )

        # Titanic Griff odds via casino (bets ≤ 1 000 PB).
        max_bet_for_titanic = 1_000
        base_chance = min(
            CASINO_HUGE_MAX_CHANCE, max_bet_for_titanic * CASINO_HUGE_CHANCE_PER_PB
        )
        titanic_factor = min(
            CASINO_TITANIC_MAX_CHANCE,
            max_bet_for_titanic * CASINO_TITANIC_CHANCE_PER_PB,
        )
        titanic_chance = min(base_chance, titanic_factor) / 10
        special_lines.append(
            f"{_format_emoji(TITANIC_GRIFF_NAME)} **{TITANIC_GRIFF_NAME}** — jackpot du casino"
            f" ({titanic_chance * 100:.7f}% avec une mise de {embeds.format_currency(max_bet_for_titanic)})."
        )

        embed = embeds.info_embed(
            "Voici un récapitulatif à jour des chances pour chaque Huge et Titanic.",
            title="🍀 Chances des Huge & Titanic",
        )
        if egg_entries:
            embed.add_field(
                name="🎲 Chances dans les œufs",
                value="\n".join(line for _, line in egg_entries),
                inline=False,
            )
        embed.add_field(
            name="🎯 Récompenses spéciales",
            value="\n".join(special_lines),
            inline=False,
        )

        await ctx.send(embed=embed)

    @commands.command(name="claim")
    async def claim(self, ctx: commands.Context) -> None:
        (
            amount,
            rows,
            elapsed,
            booster_info,
            clan_info,
            progress_updates,
            potion_info,
            enchantment_info,
            _rebirth_info,
        ) = await self.database.claim_active_pet_income(ctx.author.id)
        if not rows:
            await ctx.send(embed=embeds.error_embed("Tu dois équiper un pet avant de pouvoir collecter ses revenus."))
            return

        original_levels: Dict[int, int] = {
            int(row["id"]): int(row.get("huge_level") or 1)
            for row in rows
            if bool(row.get("is_huge"))
        }
        pets_data = await self._prepare_pet_data(ctx.author.id, rows)

        if progress_updates:
            for pet in pets_data:
                user_pet_id = int(pet.get("id", 0))
                update = progress_updates.get(user_pet_id)
                if not update:
                    continue
                new_level, new_xp = update
                self._apply_huge_progress_fields(pet, new_level, new_xp)
                if pet.get("is_huge"):
                    reference = int(pet.get("_reference_income", 0))
                    income_value = self._compute_huge_income(
                        reference,
                        pet_name=str(pet.get("name", "")),
                        level=new_level,
                    )
                    pet["base_income_per_hour"] = income_value
                    if "income" in pet:
                        pet["income"] = income_value

        for pet in pets_data:
            pet.pop("_reference_income", None)

        level_up_lines: List[str] = []
        if original_levels and pets_data:
            for pet in pets_data:
                if not pet.get("is_huge"):
                    continue
                user_pet_id = int(pet.get("id", 0))
                old_level = original_levels.get(user_pet_id)
                new_level = int(pet.get("huge_level", old_level or 1))
                if old_level is not None and new_level > old_level:
                    name = str(pet.get("name", "Pet"))
                    level_up_lines.append(f"🎉 Ton Huge {name} monte niveau {new_level} !")

        if clan_info:
            clan_id = int(clan_info.get("id", 0))
            if clan_id:
                top_rows = await self.database.get_clan_contribution_leaderboard(clan_id, limit=3)
                top_entries: List[Dict[str, object]] = []
                for row in top_rows:
                    contributor_id = int(row["user_id"])
                    contribution = int(row["contribution"])
                    member_obj = None
                    if ctx.guild is not None:
                        member_obj = ctx.guild.get_member(contributor_id)
                    display = member_obj.display_name if member_obj else f"<@{contributor_id}>"
                    top_entries.append(
                        {
                            "display": display,
                            "mention": f"<@{contributor_id}>",
                            "contribution": contribution,
                        }
                    )
                if top_entries:
                    clan_info["top_contributors"] = top_entries

        embed = embeds.pet_claim_embed(
            member=ctx.author,
            pets=pets_data,
            amount=amount,
            elapsed_seconds=elapsed,
            booster=booster_info,
            clan=clan_info if clan_info else None,
            potion=potion_info if potion_info else None,
            enchantment=enchantment_info if enchantment_info else None,
        )
        if level_up_lines:
            embed.add_field(
                name="🎉 Nouveaux niveaux",
                value="\n".join(level_up_lines),
                inline=False,
            )

        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            logger.exception(
                "Impossible d'envoyer l'embed de claim des pets",
                extra={
                    "user_id": ctx.author.id,
                    "pet_count": len(pets_data),
                    "amount": amount,
                },
            )
            fallback_parts = [embed.title or "Gains des pets"]
            if embed.description:
                fallback_parts.append(embed.description)
            if level_up_lines:
                fallback_parts.extend(level_up_lines)
            fallback_message = "\n".join(part for part in fallback_parts if part)
            if not fallback_message:
                fallback_message = (
                    "Tu récupères des PB avec tes pets, mais un problème est survenu "
                    "lors de l'affichage de l'embed."
                )
            await ctx.send(fallback_message)




@dataclass
class TradeOffer:
    pb: int = 0
    pets: list[dict[str, Any]] = field(default_factory=list)

class TradeSession:
    """Orchestre un échange interactif entre deux joueurs."""

    def __init__(
        self,
        cog: "Pets",
        thread: discord.Thread,
        initiator: discord.Member,
        partner: discord.Member,
    ) -> None:
        self.cog = cog
        self.thread = thread
        self.initiator = initiator
        self.partner = partner
        self.offers: dict[int, TradeOffer] = {
            initiator.id: TradeOffer(),
            partner.id: TradeOffer(),
        }
        self.ready: set[int] = set()
        self.lock = asyncio.Lock()
        self.message: discord.Message | None = None
        self.view: TradeView | None = None
        self.completed = False
        self._refresh_lock = asyncio.Lock()
        self._last_refresh: float = 0.0
        self._refresh_interval = 0.8

    @property
    def participants(self) -> tuple[discord.Member, discord.Member]:
        return self.initiator, self.partner

    def other_user(self, user_id: int) -> discord.Member:
        return self.partner if user_id == self.initiator.id else self.initiator

    def toggle_ready(self, user_id: int) -> bool:
        if user_id in self.ready:
            self.ready.discard(user_id)
            return False
        self.ready.add(user_id)
        return True

    def both_ready(self) -> bool:
        return len(self.ready) == 2

    def has_trade_value(self) -> bool:
        for offer in self.offers.values():
            if offer.pb > 0 or offer.pets:
                return True
        return False

    async def add_pet(
        self, user: discord.abc.User, user_pet_id: int, price: int
    ) -> None:
        async with self.lock:
            offer = self.offers.setdefault(user.id, TradeOffer())
            if any(pet["user_pet_id"] == user_pet_id for pet in offer.pets):
                raise DatabaseError("Ce pet est déjà dans ton offre.")
            record = await self.cog.database.get_user_pet(user.id, user_pet_id)
            if record is None:
                raise DatabaseError("Impossible de trouver ce pet dans ton inventaire.")
            if bool(record.get("is_active")):
                raise DatabaseError("Ce pet est actuellement équipé.")
            if bool(record.get("on_market")):
                raise DatabaseError("Ce pet est listé sur un stand.")

            pet_data = self.cog._convert_record(record, best_non_huge_income=None)
            offer.pets.append(
                {
                    "user_pet_id": user_pet_id,
                    "pet_id": int(pet_data.get("pet_id", 0)),
                    "name": str(pet_data.get("name", "Pet")),
                    "is_gold": bool(pet_data.get("is_gold")),
                    "is_rainbow": bool(pet_data.get("is_rainbow")),
                    "is_shiny": bool(pet_data.get("is_shiny")),
                    "price": max(0, price),
                }
            )
            self.ready.discard(user.id)

    async def set_pb(self, user_id: int, amount: int) -> None:
        if amount < 0:
            raise DatabaseError("Le montant en PB doit être positif.")
        async with self.lock:
            self.offers.setdefault(user_id, TradeOffer()).pb = amount
            self.ready.discard(user_id)

    async def clear_offer(self, user_id: int) -> None:
        async with self.lock:
            self.offers[user_id] = TradeOffer()
            self.ready.discard(user_id)

    def _format_pet_line(self, data: Mapping[str, Any], price: int) -> str:
        markers: list[str] = []
        if bool(data.get("is_rainbow")):
            markers.append("🌈")
        elif bool(data.get("is_gold")):
            markers.append("🥇")
        if bool(data.get("is_shiny")):
            markers.append("✨")
        suffix = f" {' '.join(markers)}" if markers else ""
        return (
            f"#{data.get('user_pet_id', 0)} • {data.get('name', 'Pet')}{suffix}"
            f" — {embeds.format_currency(price)}"
        )

    def build_embed(self) -> discord.Embed:
        embed = embeds.info_embed(
            "Ajoute des pets ou des PB à ton offre, puis valide avec Prêt lorsque tout te convient.",
            title="💱 Trade interactif",
        )
        for member in self.participants:
            offer = self.offers.get(member.id) or TradeOffer()
            lines: list[str] = []
            if offer.pb:
                lines.append(f"PB : {embeds.format_currency(offer.pb)}")
            for pet in offer.pets:
                lines.append(self._format_pet_line(pet, int(pet.get("price", 0))))
            if not lines:
                lines.append("Aucune offre")
            status = "✅" if member.id in self.ready else "⏳"
            embed.add_field(
                name=f"{status} {member.display_name}",
                value="\n".join(lines),
                inline=False,
            )
        embed.set_footer(
            text="Le trade se finalise automatiquement lorsque vous êtes deux à être prêts."
        )
        return embed

    def _serialize_offer(self, user_id: int) -> dict[str, object]:
        offer = self.offers.get(user_id) or TradeOffer()
        return {
            "pb": int(offer.pb),
            "pets": [
                {"id": int(pet["user_pet_id"]), "price": int(pet.get("price", 0))}
                for pet in offer.pets
            ],
        }

    async def refresh(self) -> None:
        if self.message is None or self.view is None:
            return
        async with self._refresh_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            delay = self._refresh_interval - (now - self._last_refresh)
            if delay > 0:
                await asyncio.sleep(delay)
                now = loop.time()

            try:
                await self.message.edit(embed=self.build_embed(), view=self.view)
            except discord.HTTPException as exc:
                logger.warning("Impossible de rafraîchir le trade", exc_info=exc)
            finally:
                self._last_refresh = loop.time()

    async def complete_trade(
        self, interaction: discord.Interaction, view: "TradeView"
    ) -> None:
        async with self.lock:
            if self.completed:
                return
            initiator_offer = self._serialize_offer(self.initiator.id)
            partner_offer = self._serialize_offer(self.partner.id)

        if not self.has_trade_value():
            self.ready.clear()
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Ajoute au moins un pet ou des PB avant de finaliser le trade."
                ),
                ephemeral=True,
            )
            await self.refresh()
            return

        try:
            result = await self.cog.database.execute_trade(
                self.initiator.id,
                self.partner.id,
                initiator_offer,
                partner_offer,
            )
        except InsufficientBalanceError as exc:
            self.ready.clear()
            await interaction.followup.send(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            await self.refresh()
            return
        except DatabaseError as exc:
            self.ready.clear()
            await interaction.followup.send(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            await self.refresh()
            return

        self.completed = True
        view.disable_all_items()
        if self.message is not None:
            await interaction.followup.edit_message(
                self.message.id,
                embed=self._build_result_embed(result),
                view=view,
            )
        await interaction.followup.send(
            embed=embeds.success_embed(
                "Le trade est terminé ! Ce fil sera supprimé dans 60 secondes."
            ),
            ephemeral=True,
        )
        view.stop()
        await self._schedule_thread_close()

    def _build_result_embed(self, result: Mapping[str, Any]) -> discord.Embed:
        embed = embeds.success_embed(
            "Les échanges ont été effectués avec succès.", title="✅ Trade finalisé"
        )
        initiator_lines = self._result_lines(
            outgoing=self.offers[self.initiator.id],
            incoming=result.get("partner_pets", []),
            received_pb=int(result.get("partner_pb_out", 0)),
            given_pb=int(result.get("initiator_pb_out", 0)),
        )
        partner_lines = self._result_lines(
            outgoing=self.offers[self.partner.id],
            incoming=result.get("initiator_pets", []),
            received_pb=int(result.get("initiator_pb_out", 0)),
            given_pb=int(result.get("partner_pb_out", 0)),
        )
        embed.add_field(
            name=self.initiator.display_name,
            value="\n".join(initiator_lines),
            inline=False,
        )
        embed.add_field(
            name=self.partner.display_name,
            value="\n".join(partner_lines),
            inline=False,
        )
        return embed

    def _result_lines(
        self,
        *,
        outgoing: TradeOffer,
        incoming: Sequence[Mapping[str, Any]],
        received_pb: int,
        given_pb: int,
    ) -> list[str]:
        lines: list[str] = []
        if given_pb:
            lines.append(f"PB donnés : {embeds.format_currency(given_pb)}")
        if outgoing.pets:
            lines.append(
                "Pets donnés : "
                + ", ".join(pet["name"] for pet in outgoing.pets)
            )
        if received_pb:
            lines.append(f"PB reçus : {embeds.format_currency(received_pb)}")
        if incoming:
            received_names: list[str] = []
            for pet in incoming:
                definition = self.cog._definition_by_id.get(int(pet.get("pet_id", 0)))
                name = definition.name if definition else "Pet"
                markers: list[str] = []
                if bool(pet.get("is_rainbow")):
                    markers.append("🌈")
                elif bool(pet.get("is_gold")):
                    markers.append("🥇")
                if bool(pet.get("is_shiny")):
                    markers.append("✨")
                label = " ".join(markers)
                if label:
                    name = f"{name} {label}"
                received_names.append(name)
            lines.append("Pets reçus : " + ", ".join(received_names))
        if not lines:
            lines.append("Aucun changement")
        return lines

    async def _schedule_thread_close(self) -> None:
        async def _closer() -> None:
            await asyncio.sleep(60)
            with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                await self.thread.delete(reason="Trade finalisé")

        asyncio.create_task(_closer())

    async def cancel(self, reason: str) -> None:
        if self.completed:
            return
        self.completed = True
        if self.view is not None:
            self.view.disable_all_items()
        if self.message is not None:
            await self.message.edit(
                embed=embeds.warning_embed(reason, title="Trade annulé"),
                view=self.view,
            )
        await self._schedule_thread_close()
        if self.view is not None:
            self.view.stop()


class TradeAddPetModal(discord.ui.Modal):
    def __init__(self, view: "TradeView", user_id: int) -> None:
        super().__init__(title="Ajouter un pet au trade")
        self.view = view
        self.user_id = user_id
        self.pet_id_input = discord.ui.TextInput(
            label="Identifiant du pet",
            placeholder="Ex: 42",
            min_length=1,
            max_length=10,
        )
        self.price_input = discord.ui.TextInput(
            label="Valeur estimée en PB",
            placeholder="Ex: 150000",
            min_length=1,
            max_length=18,
        )
        self.add_item(self.pet_id_input)
        self.add_item(self.price_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            pet_id = int(self.pet_id_input.value)
            price = max(0, int(self.price_input.value))
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci d'indiquer un identifiant et un prix valides."),
                ephemeral=True,
            )
            return
        try:
            await self.view.session.add_pet(interaction.user, pet_id, price)
        except DatabaseError as exc:
            await interaction.response.send_message(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.success_embed("Pet ajouté à ton offre."), ephemeral=True
        )
        await self.view.session.refresh()


class TradePBModal(discord.ui.Modal):
    def __init__(self, view: "TradeView", user_id: int) -> None:
        super().__init__(title="Définir les PB offerts")
        self.view = view
        self.user_id = user_id
        self.amount_input = discord.ui.TextInput(
            label="Montant en PB",
            placeholder="Ex: 250000",
            min_length=1,
            max_length=18,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = max(0, int(self.amount_input.value))
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Montant invalide."), ephemeral=True
            )
            return
        try:
            await self.view.session.set_pb(interaction.user.id, amount)
        except DatabaseError as exc:
            await interaction.response.send_message(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.success_embed("Montant mis à jour."), ephemeral=True
        )
        await self.view.session.refresh()


class TradeView(discord.ui.View):
    def __init__(self, session: TradeSession) -> None:
        super().__init__(timeout=900)
        self.session = session
        session.view = self

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.session.initiator.id, self.session.partner.id):
            await interaction.response.send_message(
                "Seuls les participants au trade peuvent utiliser ces boutons.",
                ephemeral=True,
            )
            return False
        return True

    def disable_all_items(self) -> None:
        for item in self.children:
            item.disabled = True

    async def on_timeout(self) -> None:
        await self.session.cancel("Le trade a expiré par inactivité.")

    @discord.ui.button(label="Ajouter un pet", style=discord.ButtonStyle.primary)
    async def add_pet_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        modal = TradeAddPetModal(self, interaction.user.id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Ajouter des PB", style=discord.ButtonStyle.secondary)
    async def add_pb_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        modal = TradePBModal(self, interaction.user.id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Réinitialiser", style=discord.ButtonStyle.secondary)
    async def reset_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.session.clear_offer(interaction.user.id)
        await interaction.response.send_message(
            embed=embeds.warning_embed("Ton offre a été réinitialisée."),
            ephemeral=True,
        )
        await self.session.refresh()

    @discord.ui.button(label="Prêt", style=discord.ButtonStyle.success)
    async def ready_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        is_ready = self.session.toggle_ready(interaction.user.id)
        if self.session.both_ready():
            await interaction.response.defer()
            await self.session.complete_trade(interaction, self)
            return

        await interaction.response.edit_message(
            embed=self.session.build_embed(), view=self
        )
        message = "Tu es prêt pour le trade." if is_ready else "Tu n'es plus prêt."
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.session.cancel(
            f"Trade annulé par {interaction.user.display_name}."
        )
        await interaction.response.send_message(
            embed=embeds.warning_embed("Trade annulé."), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Pets(bot))
