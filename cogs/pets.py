"""Système d'ouverture d'œufs et de gestion des pets Brawl Stars."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

import discord
from discord.ext import commands

from config import (
    BASE_PET_SLOTS,
    DEFAULT_PET_EGG_SLUG,
    GOLD_PET_CHANCE,
    GOLD_PET_COMBINE_REQUIRED,
    GOLD_PET_MULTIPLIER,
    GRADE_DEFINITIONS,
    PET_DEFINITIONS,
    PET_EGG_DEFINITIONS,
    PET_RARITY_ORDER,
    PET_ZONES,
    RAINBOW_PET_CHANCE,
    RAINBOW_PET_COMBINE_REQUIRED,
    RAINBOW_PET_MULTIPLIER,
    SHINY_PET_MULTIPLIER,
    HUGE_PET_LEVEL_CAP,
    HUGE_PET_NAME,
    HUGE_PET_MIN_INCOME,
    HUGE_PET_SOURCES,
    PotionDefinition,
    get_huge_level_multiplier,
    get_huge_level_progress,
    huge_level_required_xp,
    PetDefinition,
    PetEggDefinition,
    PetZoneDefinition,
    EGG_MASTERY_ROLE_ID,
    PET_MASTERY_ROLE_ID,
)
from utils import embeds
from utils.mastery import EGG_MASTERY, PET_MASTERY, get_mastery_perks, iter_masteries
from database.db import ActivePetLimitError, DatabaseError

logger = logging.getLogger(__name__)


HUGE_SHELLY_ALERT_CHANNEL_ID = 1236724293631611022
EGG_OPEN_EMOJI = "<:Egg:1433411763944296518>"


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
    """Bonus liés à la maîtrise des pets."""

    fuse_machine_unlocked: bool = False
    auto_goldify: bool = False
    auto_rainbow: bool = False
    shiny_egg_chance: float = 0.0
    shiny_goldify_chance: float = 0.0
    shiny_rainbow_chance: float = 0.0
    fuse_double_chance: float = 0.0
    fuse_triple_chance: float = 0.0
    gold_luck_multiplier: float = 1.0
    shiny_luck_multiplier: float = 1.0
    rainbow_luck_multiplier: float = 1.0


def _compute_pet_mastery_perks(level: int) -> PetMasteryPerks:
    """Calcule les bonus actifs pour la maîtrise des pets."""

    fuse_machine_unlocked = False
    auto_goldify = False
    auto_rainbow = False
    shiny_egg_chance = 0.0
    shiny_goldify_chance = 0.0
    shiny_rainbow_chance = 0.0
    fuse_double_chance = 0.0
    fuse_triple_chance = 0.0
    gold_luck_multiplier = 1.0
    shiny_luck_multiplier = 1.0
    rainbow_luck_multiplier = 1.0

    if level >= 5:
        fuse_machine_unlocked = True
        auto_goldify = True
        shiny_egg_chance = 0.01
    if level >= 10:
        fuse_double_chance = 0.10
    if level >= 20:
        shiny_goldify_chance = 0.03
        shiny_rainbow_chance = 0.01
    if level >= 30:
        auto_rainbow = True
        shiny_egg_chance = max(shiny_egg_chance, 0.03)
    if level >= 40:
        fuse_double_chance = max(fuse_double_chance, 0.35)
        fuse_triple_chance = 0.10
    if level >= 50:
        fuse_double_chance = max(fuse_double_chance, 0.50)
        fuse_triple_chance = max(fuse_triple_chance, 0.10)
        shiny_egg_chance = max(shiny_egg_chance, 0.05)
        shiny_goldify_chance = max(shiny_goldify_chance, 0.05)
        shiny_rainbow_chance = max(shiny_rainbow_chance, 0.03)
    if level >= 64:
        gold_luck_multiplier = 1.5
        shiny_luck_multiplier = 1.2
        rainbow_luck_multiplier = 1.3

    return PetMasteryPerks(
        fuse_machine_unlocked=fuse_machine_unlocked,
        auto_goldify=auto_goldify,
        auto_rainbow=auto_rainbow,
        shiny_egg_chance=shiny_egg_chance,
        shiny_goldify_chance=shiny_goldify_chance,
        shiny_rainbow_chance=shiny_rainbow_chance,
        fuse_double_chance=fuse_double_chance,
        fuse_triple_chance=fuse_triple_chance,
        gold_luck_multiplier=gold_luck_multiplier,
        shiny_luck_multiplier=shiny_luck_multiplier,
        rainbow_luck_multiplier=rainbow_luck_multiplier,
    )


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
        is_rainbow = bool(data.get("is_rainbow"))
        is_gold = bool(data.get("is_gold"))
        is_active = bool(data.get("is_active"))
        income = int(data.get("base_income_per_hour", 0))
        marker = " 🌈" if is_rainbow else (" 🥇" if is_gold else "")
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


class PetFusionView(discord.ui.View):
    """Vue permettant de sélectionner exactement dix pets pour une fusion."""

    def __init__(
        self,
        ctx: commands.Context,
        candidates: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(timeout=120)
        self.ctx = ctx
        self.candidates = list(candidates)
        self.message: discord.Message | None = None
        self.selected_ids: list[int] = []
        self.confirmed = False
        self.cancelled = False
        options: list[discord.SelectOption] = []
        max_entries = min(len(self.candidates), 25)
        self._value_map: Dict[str, Mapping[str, Any]] = {}
        for candidate in self.candidates[:max_entries]:
            record = candidate.get("record", {})
            data = candidate.get("data", {})
            user_pet_id = int(record.get("id") or 0)
            if user_pet_id <= 0:
                continue
            name = str(data.get("name", "Pet"))
            markers: list[str] = []
            if data.get("is_rainbow"):
                markers.append("🌈")
            elif data.get("is_gold"):
                markers.append("🥇")
            if data.get("is_shiny"):
                markers.append("✨")
            label = f"{' '.join(markers)} {name}".strip()
            if len(label) > 90:
                label = label[:87] + "…"
            income = int(data.get("base_income_per_hour", 0))
            description = f"{income:,} PB/h".replace(",", " ")
            options.append(
                discord.SelectOption(label=label or name[:90], value=str(user_pet_id), description=description)
            )
            self._value_map[str(user_pet_id)] = candidate

        selectable = min(10, len(options))
        self.select = discord.ui.Select(
            placeholder="Choisis exactement 10 pets à fusionner",
            options=options,
            min_values=selectable,
            max_values=selectable,
        )

        async def _on_select(interaction: discord.Interaction) -> None:
            self.selected_ids = [int(value) for value in self.select.values]
            await interaction.response.defer()

        self.select.callback = _on_select  # type: ignore[assignment]
        self.add_item(self.select)

    def disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Fusionner", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if len(self.select.values) != self.select.max_values:
            await interaction.response.send_message(
                "Tu dois sélectionner exactement 10 pets pour lancer la fusion.",
                ephemeral=True,
            )
            return
        self.selected_ids = [int(value) for value in self.select.values]
        self.confirmed = True
        self.disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.cancelled = True
        self.disable_all()
        await interaction.response.edit_message(view=self)
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Seule la personne qui a lancé la commande peut choisir les pets à fusionner.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        self.disable_all()
        if self.message:
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
        self._default_egg_slug: str = (
            DEFAULT_PET_EGG_SLUG if DEFAULT_PET_EGG_SLUG in self._eggs else next(iter(self._eggs), "")
        )
        self._egg_lookup: Dict[str, str] = {}
        for egg in self._eggs.values():
            aliases = {egg.slug, egg.name.lower()}
            aliases.update(alias.lower() for alias in egg.aliases)
            normalized = {token.replace("œ", "oe").strip() for token in aliases}
            for token in aliases | normalized:
                key = token.strip().lower()
                if key:
                    self._egg_lookup[key] = egg.slug
        if self._default_egg_slug:
            self._egg_lookup.setdefault(self._default_egg_slug, self._default_egg_slug)
        self._fusion_pool: List[PetDefinition] = [pet for pet in self._definitions if not pet.is_huge]

    @staticmethod
    def _normalize_pet_key(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return "".join(ch for ch in normalized.lower() if ch.isalnum())

    async def cog_load(self) -> None:
        self._pet_ids = await self.database.sync_pets(self._definitions)
        self._definition_by_id = {pet_id: self._definition_by_name[name] for name, pet_id in self._pet_ids.items()}
        logger.info("Catalogue de pets synchronisé (%d entrées)", len(self._definition_by_id))

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

    def _choose_fusion_pet(self) -> PetDefinition:
        if not self._fusion_pool:
            self._fusion_pool = [pet for pet in self._definitions if not pet.is_huge]
        weights = []
        for pet in self._fusion_pool:
            weight = getattr(pet, "drop_rate", 0.0)
            if weight <= 0:
                weight = 1.0
            weights.append(weight)
        return random.choices(self._fusion_pool, weights=weights, k=1)[0]

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

    @staticmethod
    def _compute_huge_income(
        best_non_huge_income: int | None,
        *,
        pet_name: str | None = None,
        level: int = 1,
    ) -> int:
        best_value = max(0, int(best_non_huge_income or 0))
        multiplier = get_huge_level_multiplier(pet_name or "", level)
        if best_value <= 0:
            return HUGE_PET_MIN_INCOME
        scaled = int(best_value * multiplier)
        return max(HUGE_PET_MIN_INCOME, scaled)

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
        is_shiny = bool(record.get("is_shiny"))
        is_huge = bool(record.get("is_huge"))
        data["is_gold"] = is_gold
        data["is_rainbow"] = is_rainbow
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
            multiplier = 1
            if is_rainbow:
                multiplier *= RAINBOW_PET_MULTIPLIER
            elif is_gold:
                multiplier *= GOLD_PET_MULTIPLIER
            if is_shiny:
                multiplier *= SHINY_PET_MULTIPLIER
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
            data["market_value"] = int(market_values.get(pet_identifier, 0))
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

    def _dispatch_grade_progress(
        self, ctx: commands.Context, quest_type: str, amount: int
    ) -> None:
        member = self._resolve_member(ctx)
        if member is None:
            return
        self.bot.dispatch("grade_quest_progress", member, quest_type, amount, ctx.channel)

    @staticmethod
    def _roll_shiny(perks: PetMasteryPerks | None, base_chance: float) -> bool:
        chance = max(0.0, float(base_chance))
        if chance <= 0:
            return False
        multiplier = 1.0
        if perks is not None:
            multiplier = max(0.0, float(perks.shiny_luck_multiplier))
        chance = min(1.0, chance * multiplier)
        return random.random() < chance

    async def _apply_auto_pet_upgrades(
        self,
        ctx: commands.Context,
        pet_definition: PetDefinition,
        pet_perks: PetMasteryPerks | None,
    ) -> None:
        if (
            pet_perks is None
            or (not pet_perks.auto_goldify and not pet_perks.auto_rainbow)
            or pet_definition.is_huge
        ):
            return

        pet_id = self._pet_ids.get(pet_definition.name)
        if pet_id is None:
            return

        market_values = await self.database.get_pet_market_values()
        notifications: list[discord.Embed] = []
        total_xp = 0

        if pet_perks.auto_goldify:
            while True:
                try:
                    make_shiny = self._roll_shiny(pet_perks, pet_perks.shiny_goldify_chance)
                    record, consumed = await self.database.upgrade_pet_to_gold(
                        ctx.author.id, pet_id, make_shiny=make_shiny
                    )
                except DatabaseError:
                    break
                total_xp += consumed
                best_income = await self.database.get_best_non_huge_income(ctx.author.id)
                pet_data = self._convert_record(record, best_non_huge_income=best_income)
                pet_identifier = int(pet_data.get("pet_id", pet_id))
                pet_data["market_value"] = int(market_values.get(pet_identifier, 0))
                embed = embeds.pet_reveal_embed(
                    name=str(pet_data.get("name", pet_definition.name)),
                    rarity=str(pet_data.get("rarity", pet_definition.rarity)),
                    image_url=str(pet_data.get("image_url", pet_definition.image_url)),
                    income_per_hour=int(pet_data.get("base_income_per_hour", pet_definition.base_income_per_hour)),
                    is_huge=False,
                    is_gold=True,
                    is_rainbow=False,
                    is_shiny=bool(pet_data.get("is_shiny")),
                    market_value=int(pet_data.get("market_value", 0)),
                )
                embed.add_field(
                    name="Auto Goldify",
                    value=f"{consumed} pets fusionnés automatiquement !",
                    inline=False,
                )
                notifications.append(embed)

        if pet_perks.auto_rainbow:
            while True:
                try:
                    make_shiny = self._roll_shiny(pet_perks, pet_perks.shiny_rainbow_chance)
                    record, consumed = await self.database.upgrade_pet_to_rainbow(
                        ctx.author.id, pet_id, make_shiny=make_shiny
                    )
                except DatabaseError:
                    break
                total_xp += consumed
                best_income = await self.database.get_best_non_huge_income(ctx.author.id)
                pet_data = self._convert_record(record, best_non_huge_income=best_income)
                pet_identifier = int(pet_data.get("pet_id", pet_id))
                pet_data["market_value"] = int(market_values.get(pet_identifier, 0))
                embed = embeds.pet_reveal_embed(
                    name=str(pet_data.get("name", pet_definition.name)),
                    rarity=str(pet_data.get("rarity", pet_definition.rarity)),
                    image_url=str(pet_data.get("image_url", pet_definition.image_url)),
                    income_per_hour=int(pet_data.get("base_income_per_hour", pet_definition.base_income_per_hour)),
                    is_huge=False,
                    is_gold=False,
                    is_rainbow=True,
                    is_shiny=bool(pet_data.get("is_shiny")),
                    market_value=int(pet_data.get("market_value", 0)),
                )
                embed.add_field(
                    name="Auto Rainbowify",
                    value=f"{consumed} pets GOLD combinés automatiquement !",
                    inline=False,
                )
                notifications.append(embed)

        if total_xp > 0:
            mastery_update = await self.database.add_mastery_experience(
                ctx.author.id, PET_MASTERY.slug, total_xp
            )
            await self._handle_mastery_notifications(ctx, mastery_update, PET_MASTERY)

        for embed in notifications:
            await ctx.send(embed=embed)

    async def _grant_mastery_role(self, ctx: commands.Context, role_id: int) -> None:
        if role_id <= 0 or ctx.guild is None:
            return
        role = ctx.guild.get_role(role_id)
        if role is None:
            return
        member = ctx.guild.get_member(ctx.author.id)
        if member is None or role in member.roles:
            return
        try:
            await member.add_roles(role, reason="Maîtrise maximale atteinte")
        except discord.Forbidden:
            logger.debug("Impossible d'ajouter le rôle %s à %s", role_id, ctx.author.id)
        except discord.HTTPException:
            logger.warning("Échec de l'ajout du rôle de maîtrise", exc_info=True)

    async def _handle_mastery_notifications(
        self,
        ctx: commands.Context,
        update: Mapping[str, object],
        mastery,
    ) -> None:
        levels_gained = int(update.get("levels_gained", 0) or 0)
        if levels_gained <= 0:
            return

        level = int(update.get("level", 1) or 1)
        previous_level = int(update.get("previous_level", level) or level)
        xp_to_next = int(update.get("xp_to_next_level", 0) or 0)
        current_progress = int(update.get("experience", 0) or 0)

        if mastery.slug == EGG_MASTERY.slug:
            lines = [f"Tu passes niveau **{level}** de {EGG_MASTERY.display_name} !"]
        elif mastery.slug == PET_MASTERY.slug:
            lines = [f"Tu passes niveau **{level}** de {PET_MASTERY.display_name} !"]
        else:
            lines = [f"Niveau **{level}** atteint sur {mastery.display_name} !"]

        if xp_to_next > 0:
            remaining = max(0, xp_to_next - current_progress)
            lines.append(f"Encore {remaining:,} XP pour le prochain niveau.".replace(",", " "))
        else:
            lines.append("Tu as atteint le niveau maximal de maîtrise, félicitations !")

        prefix = "✨ "
        if mastery.slug == EGG_MASTERY.slug:
            prefix = "🥚 "
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
                lines.append("Tu obtiens le rôle ultime avec **x2 chance** permanente sur tes ouvertures d'œufs !")
                await self._grant_mastery_role(ctx, EGG_MASTERY_ROLE_ID)
        elif mastery.slug == PET_MASTERY.slug:
            prefix = "🐾 "
            if previous_level < 5 <= level:
                lines.append(
                    "La fuse machine est débloquée, l'auto goldify arrive et tu gagnes **1%** de shiny dans les œufs !"
                )
            if previous_level < 10 <= level:
                lines.append("La fuse machine peut désormais donner un **deuxième pet** (10%).")
            if previous_level < 20 <= level:
                lines.append(
                    "Tes goldify offrent **3%** de shiny et les rainbowify **1%** supplémentaires !"
                )
            if previous_level < 30 <= level:
                lines.append("Auto rainbowify activé et les œufs passent à **3%** de shiny !")
            if previous_level < 40 <= level:
                lines.append("La fuse machine grimpe à **35%** de double et **10%** de triple !")
            if previous_level < 50 <= level:
                lines.append(
                    "50% de double, **5%** de shiny dans les œufs et boosts shiny sur goldify/rainbowify !"
                )
            if previous_level < 64 <= level:
                lines.append(
                    "Tu débloques le rôle ultime : chance **Gold x1,5 / Shiny x1,2 / Rainbow x1,3** permanente !"
                )
                await self._grant_mastery_role(ctx, PET_MASTERY_ROLE_ID)

        dm_content = prefix + "\n".join(lines)
        try:
            await ctx.author.send(dm_content)
        except discord.Forbidden:
            logger.debug("Impossible d'envoyer le MP de maîtrise à %s", ctx.author.id)
        except discord.HTTPException:
            logger.warning("Échec de l'envoi du MP de maîtrise", exc_info=True)

        if mastery.slug != EGG_MASTERY.slug:
            return

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
        pet_data["market_value"] = int(market_values.get(pet_identifier, 0))
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

    async def _ensure_zone_access(
        self, ctx: commands.Context, zone: PetZoneDefinition
    ) -> bool:
        if zone.entry_cost > 0:
            unlocked = await self.database.has_unlocked_zone(ctx.author.id, zone.slug)
            if unlocked:
                return True

        grade_level = await self.database.get_grade_level(ctx.author.id)
        if grade_level < zone.grade_required:
            grade_label = self._grade_label(zone.grade_required)
            await ctx.send(
                embed=embeds.error_embed(
                    f"Tu dois atteindre le grade {zone.grade_required} "
                    f"(**{grade_label}**) pour explorer {zone.name}."
                )
            )
            return False

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

        if zone.entry_cost <= 0:
            return True

        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < zone.entry_cost:
            await ctx.send(
                embed=embeds.error_embed(
                    f"Il te faut {embeds.format_currency(zone.entry_cost)} pour débloquer {zone.name}."
                )
            )
            return False

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
            f"Coût : {embeds.format_currency(zone.entry_cost)}.",
        ]
        if eggs_commands:
            lines.append(f"Œufs disponibles : {eggs_commands}")
        await ctx.send(
            embed=embeds.success_embed("\n".join(lines), title="Nouvelle zone débloquée")
        )
        return True

    async def _send_egg_overview(self, ctx: commands.Context) -> None:
        grade_level = await self.database.get_grade_level(ctx.author.id)
        unlocked_zones = await self.database.get_unlocked_zones(ctx.author.id)
        egg_mastery = await self.database.get_mastery_progress(ctx.author.id, EGG_MASTERY.slug)
        pet_mastery = await self.database.get_mastery_progress(ctx.author.id, PET_MASTERY.slug)
        egg_level = int(egg_mastery.get("level", 1))
        pet_level = int(pet_mastery.get("level", 1))
        lines: List[str] = []
        for zone in PET_ZONES:
            zone_unlocked = zone.entry_cost <= 0 or zone.slug in unlocked_zones
            meets_grade = grade_level >= zone.grade_required
            meets_egg_mastery = egg_level >= zone.egg_mastery_required
            meets_pet_mastery = pet_level >= zone.pet_mastery_required
            status = (
                "✅"
                if zone_unlocked and meets_grade and meets_egg_mastery and meets_pet_mastery
                else "🔒"
            )
            requirements: List[str] = []
            if not meets_grade:
                requirements.append(
                    f"Grade {zone.grade_required} ({self._grade_label(zone.grade_required)})"
                )
            if zone.egg_mastery_required > 0 and not meets_egg_mastery:
                requirements.append(
                    f"{EGG_MASTERY.display_name} niveau {zone.egg_mastery_required}"
                )
            if zone.pet_mastery_required > 0 and not meets_pet_mastery:
                requirements.append(
                    f"{PET_MASTERY.display_name} niveau {zone.pet_mastery_required}"
                )
            if zone.entry_cost > 0:
                if zone.slug in unlocked_zones:
                    requirements.append("Accès payé")
                else:
                    requirements.append(
                        f"Entrée {embeds.format_currency(zone.entry_cost)}"
                    )
            if not requirements:
                requirements.append("Accessible")
            lines.append(f"**{status} {zone.name}** — {', '.join(requirements)}")
            for egg in zone.eggs:
                lines.append(
                    f"  • {egg.name} — {embeds.format_currency(egg.price)} (`e!openbox {egg.slug}`)"
                )

        description = (
            "\n".join(lines) if lines else "Aucun œuf n'est disponible pour le moment."
        )
        embed = embeds.info_embed(description, title="Œufs & zones disponibles")
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(
        name="masteries",
        aliases=("maitrises", "mastery", "maitrise"),
    )
    async def masteries_overview(self, ctx: commands.Context) -> None:
        """Affiche une synthèse des maîtrises et des prochains bonus."""

        mastery_definitions = list(iter_masteries())
        async with ctx.typing():
            progress_list = await asyncio.gather(
                *(
                    self.database.get_mastery_progress(ctx.author.id, mastery.slug)
                    for mastery in mastery_definitions
                )
            )

        overview_entries: List[Dict[str, object]] = []
        for mastery, progress in zip(mastery_definitions, progress_list):
            level = int(progress.get("level", 1))
            max_level = int(progress.get("max_level", mastery.max_level))
            current_xp = int(progress.get("experience", 0))
            xp_to_next = int(progress.get("xp_to_next_level", 0))
            perks = list(get_mastery_perks(mastery.slug))
            active_perks = [perk.description for perk in perks if perk.level <= level]
            next_perk_text = ""
            for perk in perks:
                if perk.level > level:
                    levels_remaining = perk.level - level
                    if levels_remaining == 1:
                        next_perk_text = (
                            f"**Prochain niveau ({perk.level}) :** {perk.description}"
                        )
                    else:
                        next_perk_text = (
                            f"**Dans {levels_remaining} niveaux (niveau {perk.level}) :** {perk.description}"
                        )
                    break

            overview_entries.append(
                {
                    "definition": mastery,
                    "slug": mastery.slug,
                    "display_name": mastery.display_name,
                    "level": level,
                    "max_level": max_level,
                    "experience": current_xp,
                    "xp_to_next_level": xp_to_next,
                    "active_perks": active_perks,
                    "next_perk": next_perk_text,
                }
            )

        embed = embeds.mastery_overview_embed(ctx.author, overview_entries)
        await ctx.send(embed=embed)

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

    async def _open_pet_egg(
        self,
        ctx: commands.Context,
        egg: PetEggDefinition,
        *,
        mastery_perks: EggMasteryPerks | None = None,
        pet_mastery_perks: PetMasteryPerks | None = None,
        active_potion: tuple[PotionDefinition, datetime] | None = None,
        charge_cost: bool = True,
        bonus: bool = False,
    ) -> bool:
        await self.database.ensure_user(ctx.author.id)
        if charge_cost:
            balance = await self.database.fetch_balance(ctx.author.id)
            if balance < egg.price:
                await ctx.send(
                    embed=embeds.error_embed(
                        "Tu n'as pas assez de PB. Il te faut "
                        f"**{embeds.format_currency(egg.price)}** pour acheter {egg.name}."
                    )
                )
                return False

            await self.database.increment_balance(
                ctx.author.id,
                -egg.price,
                transaction_type="pet_purchase",
                description=f"Achat de {egg.name}",
            )
        effective_luck_bonus = 0.0
        if mastery_perks:
            effective_luck_bonus += max(0.0, float(mastery_perks.luck_bonus))
        if active_potion:
            potion_definition, potion_expires_at = active_potion
            if (
                potion_definition.effect_type == "egg_luck"
                and potion_expires_at > datetime.now(timezone.utc)
            ):
                effective_luck_bonus += max(0.0, float(potion_definition.effect_value))
        pet_definition, pet_id = self._choose_pet(egg, luck_bonus=effective_luck_bonus)
        is_gold = False
        is_rainbow = False
        is_shiny = False
        if not pet_definition.is_huge:
            gold_chance = (
                max(0.0, float(mastery_perks.gold_chance))
                if mastery_perks is not None
                else max(0.0, float(GOLD_PET_CHANCE))
            )
            rainbow_chance = (
                max(0.0, float(mastery_perks.rainbow_chance))
                if mastery_perks is not None
                else max(0.0, float(RAINBOW_PET_CHANCE))
            )
            gold_multiplier = 1.0
            rainbow_multiplier = 1.0
            shiny_multiplier = 1.0
            shiny_egg_chance = 0.0
            if pet_mastery_perks is not None:
                gold_multiplier = max(0.0, float(pet_mastery_perks.gold_luck_multiplier))
                rainbow_multiplier = max(0.0, float(pet_mastery_perks.rainbow_luck_multiplier))
                shiny_multiplier = max(0.0, float(pet_mastery_perks.shiny_luck_multiplier))
                shiny_egg_chance = max(0.0, float(pet_mastery_perks.shiny_egg_chance))

            gold_chance = min(1.0, gold_chance * gold_multiplier)
            rainbow_chance = min(1.0, rainbow_chance * rainbow_multiplier)

            if gold_chance > 0:
                is_gold = random.random() < gold_chance
            if rainbow_chance > 0:
                is_rainbow = random.random() < rainbow_chance
                if is_rainbow:
                    is_gold = False
            shiny_chance = min(1.0, shiny_egg_chance * shiny_multiplier)
            if shiny_chance > 0:
                is_shiny = random.random() < shiny_chance
        _user_pet = await self.database.add_user_pet(
            ctx.author.id,
            pet_id,
            is_huge=pet_definition.is_huge,
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            is_shiny=is_shiny,
        )
        await self.database.record_pet_opening(ctx.author.id, pet_id)

        egg_title = f"{egg.name} (bonus)" if bonus else egg.name
        animation_steps = (
            (egg_title, "L'œuf commence à bouger…"),
            (egg_title, "Des fissures apparaissent !"),
            (egg_title, "Ça y est, il est sur le point d'éclore !"),
        )
        showcase_image = self._egg_showcase_image(egg)
        speed_factor = 1.0
        if mastery_perks is not None:
            speed_factor = max(0.5, min(5.0, float(mastery_perks.animation_speed)))
        step_delay = max(0.2, 1.1 / speed_factor)
        reveal_delay = max(0.2, 1.2 / speed_factor)
        message = await ctx.send(
            content=EGG_OPEN_EMOJI,
            embed=embeds.pet_animation_embed(
                title=animation_steps[0][0],
                description=animation_steps[0][1],
                image_url=showcase_image,
            )
        )
        for title, description in animation_steps[1:]:
            await asyncio.sleep(step_delay)
            await message.edit(
                content=EGG_OPEN_EMOJI,
                embed=embeds.pet_animation_embed(
                    title=title,
                    description=description,
                    image_url=showcase_image,
                )
            )

        await asyncio.sleep(reveal_delay)
        market_values = await self.database.get_pet_market_values()
        market_value = int(market_values.get(pet_id, 0))
        if pet_definition.is_huge:
            best_non_huge_income = await self.database.get_best_non_huge_income(ctx.author.id)
            income_per_hour = self._compute_huge_income(
                best_non_huge_income, pet_name=pet_definition.name, level=1
            )
        else:
            multiplier = 1
            if is_rainbow:
                multiplier = RAINBOW_PET_MULTIPLIER
            elif is_gold:
                multiplier = GOLD_PET_MULTIPLIER
            if is_shiny:
                multiplier *= SHINY_PET_MULTIPLIER
            income_per_hour = int(pet_definition.base_income_per_hour * multiplier)
        reveal_embed = embeds.pet_reveal_embed(
            name=pet_definition.name,
            rarity=pet_definition.rarity,
            image_url=pet_definition.image_url,
            income_per_hour=income_per_hour,
            is_huge=pet_definition.is_huge,
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            is_shiny=is_shiny,
            market_value=market_value,
        )
        reveal_embed.set_footer(text=f"Utilise e!equip {pet_definition.name} pour l'équiper !")
        await message.edit(content=EGG_OPEN_EMOJI, embed=reveal_embed)

        if pet_definition.name == HUGE_PET_NAME:
            await self._send_huge_shelly_alert(ctx)

        await self._apply_auto_pet_upgrades(ctx, pet_definition, pet_mastery_perks)

        mastery_update = await self.database.add_mastery_experience(
            ctx.author.id, EGG_MASTERY.slug, 1
        )
        await self._handle_mastery_notifications(ctx, mastery_update, EGG_MASTERY)

        self._dispatch_grade_progress(ctx, "egg", 1)
        return True

    def _sort_pets_for_display(
        self,
        records: Iterable[Mapping[str, Any]],
        market_values: Mapping[int, int] | None = None,
    ) -> List[Dict[str, Any]]:
        record_list = list(records)
        best_non_huge_income = 0
        for record in record_list:
            if not bool(record.get("is_huge")):
                base_income = int(record.get("base_income_per_hour", 0))
                if bool(record.get("is_rainbow")):
                    base_income *= RAINBOW_PET_MULTIPLIER
                elif bool(record.get("is_gold")):
                    base_income *= GOLD_PET_MULTIPLIER
                if base_income > best_non_huge_income:
                    best_non_huge_income = base_income

        converted = []
        for record in record_list:
            data = self._convert_record(record, best_non_huge_income=best_non_huge_income)
            data["income"] = int(data.get("base_income_per_hour", 0))
            pet_identifier = int(data.get("pet_id", 0))
            if market_values:
                data["market_value"] = int(market_values.get(pet_identifier, 0))
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
        raw_request = (egg or "").strip()
        double_request = False
        if raw_request:
            tokens = raw_request.split()
            if tokens and tokens[-1].lower() in {"x2", "2", "double"}:
                double_request = True
                tokens = tokens[:-1]
                raw_request = " ".join(tokens).strip()

        normalized_request = raw_request or None

        if normalized_request and normalized_request.lower() in {"list", "liste", "eggs", "oeufs"}:
            await self._send_egg_overview(ctx)
            return

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

        zone = self._get_zone_for_egg(egg_definition)
        if zone is None:
            await ctx.send(embed=embeds.error_embed("La zone associée à cet œuf est introuvable."))
            return

        if not await self._ensure_zone_access(ctx, zone):
            return

        mastery_progress = await self.database.get_mastery_progress(
            ctx.author.id, EGG_MASTERY.slug
        )
        mastery_level = int(mastery_progress.get("level", 1))
        perks = _compute_egg_mastery_perks(mastery_level)
        pet_mastery_progress = await self.database.get_mastery_progress(
            ctx.author.id, PET_MASTERY.slug
        )
        pet_mastery_level = int(pet_mastery_progress.get("level", 1))
        pet_perks = _compute_pet_mastery_perks(pet_mastery_level)

        if double_request:
            if perks.double_chance > 0:
                await ctx.send(
                    embed=embeds.info_embed(
                        "Le mode double est désormais automatique : tu as "
                        f"{perks.double_chance * 100:.0f}% de chances d'obtenir un œuf bonus gratuitement à chaque ouverture."
                    )
                )
            else:
                await ctx.send(
                    embed=embeds.warning_embed(
                        "Atteins le niveau 5 de Maîtrise des œufs pour débloquer 5% de chance d'obtenir un deuxième œuf gratuit."
                    )
                )

        active_potion = await self.database.get_active_potion(ctx.author.id)
        opened = await self._open_pet_egg(
            ctx,
            egg_definition,
            active_potion=active_potion,
            mastery_perks=perks,
            pet_mastery_perks=pet_perks,
        )
        if not opened:
            return

        bonus_eggs = 0
        triple_triggered = False
        if perks.triple_chance > 0 and random.random() < perks.triple_chance:
            bonus_eggs = 2
            triple_triggered = True
        elif perks.double_chance > 0 and random.random() < perks.double_chance:
            bonus_eggs = 1

        if bonus_eggs:
            if triple_triggered:
                await ctx.send(
                    f"{EGG_OPEN_EMOJI} 🎉 **Chance triple !** Tu ouvres deux œufs bonus gratuitement !"
                )
            else:
                await ctx.send(
                    f"{EGG_OPEN_EMOJI} 🎉 **Chance !** Tu ouvres un œuf bonus gratuitement !"
                )
            for _ in range(bonus_eggs):
                await self._open_pet_egg(
                    ctx,
                    egg_definition,
                    active_potion=active_potion,
                    mastery_perks=perks,
                    charge_cost=False,
                    bonus=True,
                )

    @commands.command(name="eggs", aliases=("zones", "zone"))
    async def eggs(self, ctx: commands.Context) -> None:
        await self._send_egg_overview(ctx)

    @commands.command(name="pets", aliases=("collection", "inventory"))
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
            market_lookup[key] = int(market_values_by_id.get(pet_id, 0))
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

        grade_level = await self.database.get_grade_level(ctx.author.id)
        max_slots = max(0, BASE_PET_SLOTS + grade_level)
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

        mastery_progress = await self.database.get_mastery_progress(
            ctx.author.id, PET_MASTERY.slug
        )
        mastery_level = int(mastery_progress.get("level", 1))
        pet_perks = _compute_pet_mastery_perks(mastery_level)
        make_shiny = self._roll_shiny(pet_perks, pet_perks.shiny_goldify_chance)

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
        pet_data["market_value"] = int(market_values.get(pet_identifier, 0))
        reveal_embed = embeds.pet_reveal_embed(
            name=str(pet_data.get("name", definition.name)),
            rarity=str(pet_data.get("rarity", definition.rarity)),
            image_url=str(pet_data.get("image_url", definition.image_url)),
            income_per_hour=int(pet_data.get("base_income_per_hour", definition.base_income_per_hour)),
            is_huge=bool(pet_data.get("is_huge", False)),
            is_gold=True,
            is_shiny=bool(pet_data.get("is_shiny")),
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
            ctx.author.id, PET_MASTERY.slug, consumed
        )
        await self._handle_mastery_notifications(ctx, mastery_update, PET_MASTERY)

        self._dispatch_grade_progress(ctx, "gold", 1)

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

        mastery_progress = await self.database.get_mastery_progress(
            ctx.author.id, PET_MASTERY.slug
        )
        mastery_level = int(mastery_progress.get("level", 1))
        pet_perks = _compute_pet_mastery_perks(mastery_level)
        make_shiny = self._roll_shiny(pet_perks, pet_perks.shiny_rainbow_chance)

        try:
            record, consumed = await self.database.upgrade_pet_to_rainbow(
                ctx.author.id, pet_id, make_shiny=make_shiny
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        best_non_huge_income = await self.database.get_best_non_huge_income(ctx.author.id)
        pet_data = self._convert_record(record, best_non_huge_income=best_non_huge_income)
        market_values = await self.database.get_pet_market_values()
        pet_identifier = int(pet_data.get("pet_id", 0))
        pet_data["market_value"] = int(market_values.get(pet_identifier, 0))
        rainbow_income = int(pet_data.get("base_income_per_hour", definition.base_income_per_hour))
        base_income_value = max(1, int(definition.base_income_per_hour))
        effective_multiplier = rainbow_income / base_income_value

        embed = embeds.pet_reveal_embed(
            name=str(pet_data.get("name", definition.name)),
            rarity=str(pet_data.get("rarity", definition.rarity)),
            image_url=str(pet_data.get("image_url", definition.image_url)),
            income_per_hour=rainbow_income,
            is_huge=False,
            is_gold=False,
            is_rainbow=True,
            is_shiny=bool(pet_data.get("is_shiny")),
            market_value=int(pet_data.get("market_value", 0)),
        )
        embed.add_field(
            name="🌈 Fusion Rainbow",
            value=(
                f"🎉 {consumed} pets GOLD fusionnés en 1 RAINBOW !\n"
                f"Puissance : **{rainbow_income:,} PB/h** ({effective_multiplier:.1f}x le pet de base)\n"
                "Les pets utilisés ont été retirés de ton inventaire."
            ).replace(",", " "),
            inline=False,
        )

        await ctx.send(embed=embed)

        mastery_update = await self.database.add_mastery_experience(
            ctx.author.id, PET_MASTERY.slug, consumed
        )
        await self._handle_mastery_notifications(ctx, mastery_update, PET_MASTERY)

        self._dispatch_grade_progress(ctx, "gold", 1)

    @commands.command(name="fuse", aliases=("fusion", "fusemachine"))
    async def fuse(self, ctx: commands.Context) -> None:
        mastery_progress = await self.database.get_mastery_progress(
            ctx.author.id, PET_MASTERY.slug
        )
        mastery_level = int(mastery_progress.get("level", 1))
        pet_perks = _compute_pet_mastery_perks(mastery_level)
        if not pet_perks.fuse_machine_unlocked:
            await ctx.send(
                embed=embeds.error_embed(
                    "Atteins le niveau 5 de Maîtrise des pets pour débloquer la machine de fusion."
                )
            )
            return

        rows = await self.database.get_fusible_pets(ctx.author.id)
        if len(rows) < 10:
            await ctx.send(
                embed=embeds.error_embed(
                    "Tu as besoin d'au moins 10 pets non équipés et non listés pour utiliser la machine."
                )
            )
            return

        best_income = await self.database.get_best_non_huge_income(ctx.author.id)
        candidates = [
            {"record": row, "data": self._convert_record(row, best_non_huge_income=best_income)}
            for row in rows
        ]

        displayed_candidates = candidates[:25]
        view = PetFusionView(ctx, displayed_candidates)
        instructions = [
            "Sélectionne **10** pets à fusionner. Ils seront définitivement consommés.",
        ]
        if pet_perks.fuse_double_chance > 0 or pet_perks.fuse_triple_chance > 0:
            chance_lines: list[str] = []
            if pet_perks.fuse_double_chance > 0:
                chance_lines.append(f"{pet_perks.fuse_double_chance * 100:.0f}% de chance de double pet")
            if pet_perks.fuse_triple_chance > 0:
                chance_lines.append(f"{pet_perks.fuse_triple_chance * 100:.0f}% de chance de triple pet")
            instructions.append("Bonus: " + " et ".join(chance_lines) + ".")
        if len(rows) > len(displayed_candidates):
            instructions.append("Seuls les 25 premiers pets disponibles sont affichés dans le menu.")

        embed = embeds.info_embed("\n".join(instructions), title="Machine de fusion")
        message = await ctx.send(embed=embed, view=view)
        view.message = message
        await view.wait()

        if view.cancelled:
            await ctx.send(embed=embeds.warning_embed("Fusion annulée."))
            return

        if not view.confirmed or len(view.selected_ids) != 10:
            await ctx.send(embed=embeds.warning_embed("Aucune fusion n'a été effectuée."))
            return

        consumed_ids = view.selected_ids
        results_count = 1
        triple_triggered = False
        if pet_perks.fuse_triple_chance > 0 and random.random() < pet_perks.fuse_triple_chance:
            results_count = 3
            triple_triggered = True
        elif pet_perks.fuse_double_chance > 0 and random.random() < pet_perks.fuse_double_chance:
            results_count = 2

        results_payload: list[Mapping[str, object]] = []
        for _ in range(results_count):
            definition = self._choose_fusion_pet()
            pet_id = self._pet_ids.get(definition.name)
            if pet_id is None:
                continue
            shiny = self._roll_shiny(pet_perks, pet_perks.shiny_egg_chance)
            results_payload.append(
                {
                    "pet_id": pet_id,
                    "is_gold": False,
                    "is_rainbow": False,
                    "is_shiny": shiny,
                }
            )

        fused_records = await self.database.fuse_user_pets(
            ctx.author.id, consumed_ids, results_payload
        )

        if not fused_records:
            await ctx.send(embed=embeds.error_embed("La fusion a échoué. Réessaie plus tard."))
            return

        market_values = await self.database.get_pet_market_values()
        for index, record in enumerate(fused_records):
            data = self._convert_record(record, best_non_huge_income=best_income)
            pet_identifier = int(data.get("pet_id", 0))
            data["market_value"] = int(market_values.get(pet_identifier, 0))
            embed = embeds.pet_reveal_embed(
                name=str(data.get("name", "Pet")),
                rarity=str(data.get("rarity", "?")),
                image_url=str(data.get("image_url", "")),
                income_per_hour=int(data.get("base_income_per_hour", 0)),
                is_huge=bool(data.get("is_huge", False)),
                is_gold=bool(data.get("is_gold", False)),
                is_rainbow=bool(data.get("is_rainbow", False)),
                is_shiny=bool(data.get("is_shiny", False)),
                market_value=int(data.get("market_value", 0)),
            )
            if index == 0:
                summary_lines = ["10 pets ont été consommés par la machine."]
                if results_count > 1:
                    bonus_text = "triple" if triple_triggered else "double"
                    summary_lines.append(f"Chance {bonus_text} activée !")
                embed.add_field(
                    name="Machine de fusion",
                    value="\n".join(summary_lines),
                    inline=False,
                )
            await ctx.send(embed=embed)
            definition = self._definition_by_id.get(pet_identifier)
            if definition is not None:
                await self._apply_auto_pet_upgrades(ctx, definition, pet_perks)

        mastery_update = await self.database.add_mastery_experience(
            ctx.author.id, PET_MASTERY.slug, len(consumed_ids)
        )
        await self._handle_mastery_notifications(ctx, mastery_update, PET_MASTERY)

        self._dispatch_grade_progress(ctx, "gold", results_count)

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
        )
        await ctx.send(embed=embed)

        if level_up_lines:
            await ctx.send("\n".join(level_up_lines))

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Pets(bot))
