"""Système d'ouverture d'œufs et de gestion des pets Brawl Stars."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

import discord
from discord.ext import commands

from config import (
    DEFAULT_PET_EGG_SLUG,
    GOLD_PET_CHANCE,
    GOLD_PET_COMBINE_REQUIRED,
    GOLD_PET_MULTIPLIER,
    GRADE_DEFINITIONS,
    PET_DEFINITIONS,
    PET_EGG_DEFINITIONS,
    PET_RARITY_ORDER,
    PET_ZONES,
    HUGE_PET_NAME,
    HUGE_PET_MIN_INCOME,
    HUGE_PET_SOURCES,
    get_huge_multiplier,
    PetDefinition,
    PetEggDefinition,
    PetZoneDefinition,
)
from utils import embeds
from database.db import DatabaseError

logger = logging.getLogger(__name__)


HUGE_SHELLY_ALERT_CHANNEL_ID = 1236724293631611022


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

class Pets(commands.Cog):
    """Commande de collection de pets inspirée de Brawl Stars."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._definitions: List[PetDefinition] = list(PET_DEFINITIONS)
        self._definition_by_name: Dict[str, PetDefinition] = {pet.name: pet for pet in self._definitions}
        self._definition_by_slug: Dict[str, PetDefinition] = {
            pet.name.lower(): pet for pet in self._definitions
        }
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

    async def cog_load(self) -> None:
        self._pet_ids = await self.database.sync_pets(self._definitions)
        self._definition_by_id = {pet_id: self._definition_by_name[name] for name, pet_id in self._pet_ids.items()}
        logger.info("Catalogue de pets synchronisé (%d entrées)", len(self._definition_by_id))

    # ------------------------------------------------------------------
    # Utilitaires internes
    # ------------------------------------------------------------------
    def _choose_pet(self, egg: PetEggDefinition) -> tuple[PetDefinition, int]:
        weights = [pet.drop_rate for pet in egg.pets]
        pet = random.choices(egg.pets, weights=weights, k=1)[0]
        pet_id = self._pet_ids[pet.name]
        return pet, pet_id

    @staticmethod
    def _compute_huge_income(
        best_non_huge_income: int | None, *, pet_name: str | None = None
    ) -> int:
        best_value = max(0, int(best_non_huge_income or 0))
        multiplier = get_huge_multiplier(pet_name or "")
        if best_value <= 0:
            return HUGE_PET_MIN_INCOME
        return max(HUGE_PET_MIN_INCOME, best_value * multiplier)

    def _convert_record(
        self, record: Mapping[str, Any], *, best_non_huge_income: int | None = None
    ) -> Dict[str, Any]:
        data = dict(record)
        pet_identifier = int(record.get("pet_id", 0))
        definition = self._definition_by_id.get(pet_identifier)
        is_gold = bool(record.get("is_gold"))
        is_huge = bool(record.get("is_huge"))
        data["is_gold"] = is_gold
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
            effective_income = self._compute_huge_income(
                best_non_huge_income, pet_name=pet_name
            )
        else:
            multiplier = GOLD_PET_MULTIPLIER if is_gold else 1
            effective_income = base_income * multiplier

        data["base_income_per_hour"] = int(effective_income)
        return data

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
        lines: List[str] = []
        for zone in PET_ZONES:
            zone_unlocked = zone.entry_cost <= 0 or zone.slug in unlocked_zones
            meets_grade = grade_level >= zone.grade_required
            status = "✅" if zone_unlocked and meets_grade else "🔒"
            requirements: List[str] = []
            if not meets_grade:
                requirements.append(
                    f"Grade {zone.grade_required} ({self._grade_label(zone.grade_required)})"
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
        await channel.send(hype_message)

    async def _open_pet_egg(self, ctx: commands.Context, egg: PetEggDefinition) -> None:
        await self.database.ensure_user(ctx.author.id)
        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < egg.price:
            await ctx.send(
                embed=embeds.error_embed(
                    "Tu n'as pas assez de PB. Il te faut "
                    f"**{embeds.format_currency(egg.price)}** pour acheter {egg.name}."
                )
            )
            return

        await self.database.increment_balance(
            ctx.author.id,
            -egg.price,
            transaction_type="pet_purchase",
            description=f"Achat de {egg.name}",
        )
        pet_definition, pet_id = self._choose_pet(egg)
        is_gold = False
        if not pet_definition.is_huge and GOLD_PET_CHANCE > 0:
            is_gold = random.random() < GOLD_PET_CHANCE
        user_pet = await self.database.add_user_pet(
            ctx.author.id,
            pet_id,
            is_huge=pet_definition.is_huge,
            is_gold=is_gold,
        )
        await self.database.record_pet_opening(ctx.author.id, pet_id)

        animation_steps = (
            (egg.name, "L'œuf commence à bouger…"),
            (egg.name, "Des fissures apparaissent !"),
            (egg.name, "Ça y est, il est sur le point d'éclore !"),
        )
        message = await ctx.send(embed=embeds.pet_animation_embed(title=animation_steps[0][0], description=animation_steps[0][1]))
        for title, description in animation_steps[1:]:
            await asyncio.sleep(1.1)
            await message.edit(embed=embeds.pet_animation_embed(title=title, description=description))

        await asyncio.sleep(1.2)
        market_values = await self.database.get_pet_market_values()
        market_value = int(market_values.get(pet_id, 0))
        if pet_definition.is_huge:
            best_non_huge_income = await self.database.get_best_non_huge_income(ctx.author.id)
            income_per_hour = self._compute_huge_income(
                best_non_huge_income, pet_name=pet_definition.name
            )
        else:
            income_per_hour = int(
                pet_definition.base_income_per_hour
                * (GOLD_PET_MULTIPLIER if is_gold else 1)
            )
        reveal_embed = embeds.pet_reveal_embed(
            name=pet_definition.name,
            rarity=pet_definition.rarity,
            image_url=pet_definition.image_url,
            income_per_hour=income_per_hour,
            is_huge=pet_definition.is_huge,
            is_gold=is_gold,
            market_value=market_value,
        )
        reveal_embed.set_footer(
            text=(
                f"ID du pet : #{user_pet['id']} • Utilise e!equip {user_pet['id']} pour l'équiper !"
            )
        )
        await message.edit(embed=reveal_embed)

        if pet_definition.name == HUGE_PET_NAME:
            await self._send_huge_shelly_alert(ctx)

        self._dispatch_grade_progress(ctx, "egg", 1)

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
                if bool(record.get("is_gold")):
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
        if egg and egg.strip().lower() in {"list", "liste", "eggs", "oeufs"}:
            await self._send_egg_overview(ctx)
            return

        egg_definition = self._resolve_egg(egg)
        if egg_definition is None:
            if egg:
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

        await self._open_pet_egg(ctx, egg_definition)

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
        records = await self.database.get_user_pets(ctx.author.id)
        owned_names: Set[str] = {
            str(record.get("name", "")).strip()
            for record in records
            if str(record.get("name", "")).strip()
        }
        embed = embeds.pet_index_embed(
            member=ctx.author,
            pet_definitions=self._definitions,
            owned_names=owned_names,
            huge_descriptions=HUGE_PET_SOURCES,
        )
        await ctx.send(embed=embed)

    @commands.command(name="equip")
    async def equip(self, ctx: commands.Context, pet_id: int) -> None:
        try:
            row, activated, active_count = await self.database.set_active_pet(ctx.author.id, pet_id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        if row is None:
            await ctx.send(embed=embeds.error_embed("Pet introuvable. Vérifie l'identifiant avec `e!pets`."))
            return

        best_non_huge_income = await self.database.get_best_non_huge_income(ctx.author.id)
        pet_data = self._convert_record(row, best_non_huge_income=best_non_huge_income)
        market_values = await self.database.get_pet_market_values()
        pet_identifier = int(pet_data.get("pet_id", 0))
        pet_data["market_value"] = int(market_values.get(pet_identifier, 0))
        embed = embeds.pet_equip_embed(member=ctx.author, pet=pet_data, activated=activated, active_count=active_count)
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

        try:
            record, consumed = await self.database.upgrade_pet_to_gold(ctx.author.id, pet_id)
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
                text=(
                    f"ID du pet : #{user_pet_id} • Utilise e!equip {user_pet_id} pour l'équiper !"
                )
            )
        await ctx.send(embed=reveal_embed)

        self._dispatch_grade_progress(ctx, "gold", 1)

    @commands.command(name="claim")
    async def claim(self, ctx: commands.Context) -> None:
        amount, rows, elapsed, booster_info = await self.database.claim_active_pet_income(ctx.author.id)
        if not rows:
            await ctx.send(embed=embeds.error_embed("Tu dois équiper un pet avant de pouvoir collecter ses revenus."))
            return

        market_values = await self.database.get_pet_market_values()
        best_non_huge_income = await self.database.get_best_non_huge_income(ctx.author.id)
        pets_data: List[Dict[str, Any]] = []
        for row in rows:
            data = self._convert_record(row, best_non_huge_income=best_non_huge_income)
            pet_identifier = int(data.get("pet_id", 0))
            data["market_value"] = int(market_values.get(pet_identifier, 0))
            pets_data.append(data)

        embed = embeds.pet_claim_embed(
            member=ctx.author,
            pets=pets_data,
            amount=amount,
            elapsed_seconds=elapsed,
            booster=booster_info,
        )
        await ctx.send(embed=embed)

    @commands.command(name="petstats", aliases=("petsstats",))
    async def pets_stats(self, ctx: commands.Context) -> None:
        total_openings, counts = await self.database.get_pet_opening_counts()
        counts = {int(key): int(value) for key, value in counts.items()}
        huge_count = await self.database.count_huge_pets()
        gold_count = await self.database.count_gold_pets()
        stats = []
        for pet in self._definitions:
            pet_id = self._pet_ids.get(pet.name, 0)
            obtained = counts.get(pet_id, 0)
            actual_rate = (obtained / total_openings * 100) if total_openings else 0.0
            stats.append((pet.name, obtained, actual_rate, pet.drop_rate * 100))
        embed = embeds.pet_stats_embed(
            total_openings=total_openings,
            stats=stats,
            huge_count=huge_count,
            gold_count=gold_count,
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Pets(bot))
