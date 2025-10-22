"""Système d'ouverture d'œufs et de gestion des pets Brawl Stars."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, Iterable, List, Mapping

import discord
from discord.ext import commands

from config import PET_DEFINITIONS, PET_EGG_PRICE, PET_RARITY_ORDER, PetDefinition
from utils import embeds
from database.db import DatabaseError

logger = logging.getLogger(__name__)


class Pets(commands.Cog):
    """Commande de collection de pets inspirée de Brawl Stars."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._definitions: List[PetDefinition] = list(PET_DEFINITIONS)
        self._definition_by_name: Dict[str, PetDefinition] = {pet.name: pet for pet in self._definitions}
        self._definition_by_id: Dict[int, PetDefinition] = {}
        self._pet_ids: Dict[str, int] = {}
        self._weights: List[float] = [pet.drop_rate for pet in self._definitions]

    async def cog_load(self) -> None:
        self._pet_ids = await self.database.sync_pets(self._definitions)
        self._definition_by_id = {pet_id: self._definition_by_name[name] for name, pet_id in self._pet_ids.items()}
        logger.info("Catalogue de pets synchronisé (%d entrées)", len(self._definition_by_id))

    # ------------------------------------------------------------------
    # Utilitaires internes
    # ------------------------------------------------------------------
    def _choose_pet(self) -> tuple[PetDefinition, int]:
        pet = random.choices(self._definitions, weights=self._weights, k=1)[0]
        pet_id = self._pet_ids[pet.name]
        return pet, pet_id

    def _convert_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        data = dict(record)
        definition = self._definition_by_id.get(int(record["pet_id"]))
        if definition is not None:
            data.setdefault("image_url", definition.image_url)
            data.setdefault("base_income_per_hour", definition.base_income_per_hour)
            data.setdefault("rarity", definition.rarity)
            data.setdefault("name", definition.name)
            data.setdefault("is_huge", definition.is_huge)
        return data

    async def _open_pet_egg(self, ctx: commands.Context) -> None:
        await self.database.ensure_user(ctx.author.id)
        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < PET_EGG_PRICE:
            await ctx.send(
                embed=embeds.error_embed(
                    f"Tu n'as pas assez de PB. Il te faut **{PET_EGG_PRICE:,} PB** pour acheter un œuf basique.".replace(",", " ")
                )
            )
            return

        await self.database.increment_balance(
            ctx.author.id,
            -PET_EGG_PRICE,
            transaction_type="pet_purchase",
            description="Achat d'un œuf basique",
        )
        pet_definition, pet_id = self._choose_pet()
        user_pet = await self.database.add_user_pet(ctx.author.id, pet_id, is_huge=pet_definition.is_huge)
        await self.database.record_pet_opening(ctx.author.id, pet_id)

        animation_steps = (
            ("Œuf basique", "L'œuf commence à bouger…"),
            ("Œuf basique", "Des fissures apparaissent !"),
            ("Œuf basique", "Ça y est, il est sur le point d'éclore !"),
        )
        message = await ctx.send(embed=embeds.pet_animation_embed(title=animation_steps[0][0], description=animation_steps[0][1]))
        for title, description in animation_steps[1:]:
            await asyncio.sleep(1.1)
            await message.edit(embed=embeds.pet_animation_embed(title=title, description=description))

        await asyncio.sleep(1.2)
        market_values = await self.database.get_pet_market_values()
        market_value = int(market_values.get(pet_id, 0))
        reveal_embed = embeds.pet_reveal_embed(
            name=pet_definition.name,
            rarity=pet_definition.rarity,
            image_url=pet_definition.image_url,
            income_per_hour=pet_definition.base_income_per_hour,
            is_huge=pet_definition.is_huge,
            market_value=market_value,
        )
        reveal_embed.set_footer(
            text=(
                f"ID du pet : #{user_pet['id']} • Utilise e!equip {user_pet['id']} pour l'équiper !"
            )
        )
        await message.edit(embed=reveal_embed)

    def _sort_pets_for_display(
        self,
        records: Iterable[Mapping[str, Any]],
        market_values: Mapping[int, int] | None = None,
    ) -> List[Dict[str, Any]]:
        converted = []
        for record in records:
            data = self._convert_record(record)
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
        if item and item.lower() == "egg":
            await ctx.invoke(self.openbox)
            return
        await ctx.send(embed=embeds.info_embed("Utilise `e!buy egg` pour acheter un œuf basique."))

    @buy.command(name="egg")
    async def buy_egg(self, ctx: commands.Context) -> None:
        await ctx.invoke(self.openbox)

    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.command(name="openbox", aliases=("buyegg", "openegg", "egg"))
    async def openbox(self, ctx: commands.Context) -> None:
        await self._open_pet_egg(ctx)

    @commands.command(name="pets", aliases=("collection", "inventory"))
    async def pets_command(self, ctx: commands.Context) -> None:
        records = await self.database.get_user_pets(ctx.author.id)
        market_values = await self.database.get_pet_market_values()
        pets = self._sort_pets_for_display(records, market_values)
        active_income = sum(int(pet["income"]) for pet in pets if pet.get("is_active"))
        embed = embeds.pet_collection_embed(
            member=ctx.author,
            pets=pets,
            total_count=len(pets),
            total_income_per_hour=active_income,
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

        pet_data = self._convert_record(row)
        market_values = await self.database.get_pet_market_values()
        pet_identifier = int(pet_data.get("pet_id", 0))
        pet_data["market_value"] = int(market_values.get(pet_identifier, 0))
        embed = embeds.pet_equip_embed(member=ctx.author, pet=pet_data, activated=activated, active_count=active_count)
        await ctx.send(embed=embed)

    @commands.command(name="claim")
    async def claim(self, ctx: commands.Context) -> None:
        amount, rows, elapsed = await self.database.claim_active_pet_income(ctx.author.id)
        if not rows:
            await ctx.send(embed=embeds.error_embed("Tu dois équiper un pet avant de pouvoir collecter ses revenus."))
            return

        market_values = await self.database.get_pet_market_values()
        pets_data: List[Dict[str, Any]] = []
        for row in rows:
            data = self._convert_record(row)
            pet_identifier = int(data.get("pet_id", 0))
            data["market_value"] = int(market_values.get(pet_identifier, 0))
            pets_data.append(data)

        embed = embeds.pet_claim_embed(
            member=ctx.author,
            pets=pets_data,
            amount=amount,
            elapsed_seconds=elapsed,
        )
        await ctx.send(embed=embed)

    @commands.command(name="petstats", aliases=("petsstats",))
    async def pets_stats(self, ctx: commands.Context) -> None:
        total_openings, counts = await self.database.get_pet_opening_counts()
        counts = {int(key): int(value) for key, value in counts.items()}
        huge_count = await self.database.count_huge_pets()
        stats = []
        for pet in self._definitions:
            pet_id = self._pet_ids.get(pet.name, 0)
            obtained = counts.get(pet_id, 0)
            actual_rate = (obtained / total_openings * 100) if total_openings else 0.0
            stats.append((pet.name, obtained, actual_rate, pet.drop_rate * 100))
        embed = embeds.pet_stats_embed(total_openings=total_openings, stats=stats, huge_count=huge_count)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Pets(bot))
