"""Gestion de la plaza et des stands de vente."""
from __future__ import annotations

import contextlib
import unicodedata
from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, Mapping, Optional

import discord
from discord.ext import commands

from config import PET_DEFINITIONS, PetDefinition
from database.db import DatabaseError, InsufficientBalanceError
from utils import embeds


class Plaza(commands.Cog):
    """Cog remplaçant les échanges par un système de stands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._definitions: list[PetDefinition] = list(PET_DEFINITIONS)
        self._definition_by_slug: Dict[str, PetDefinition] = {}
        for definition in self._definitions:
            keys = {
                definition.name.lower(),
                self._normalize_key(definition.name),
            }
            for key in keys:
                if key:
                    self._definition_by_slug[key] = definition

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_key(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return "".join(ch for ch in normalized.lower() if ch.isalnum())

    @staticmethod
    def _variant_flags(variant: Optional[str]) -> tuple[Optional[bool], Optional[bool]]:
        if variant == "gold":
            return True, False
        if variant == "rainbow":
            return False, True
        if variant == "normal":
            return False, False
        return None, None

    def _parse_pet_query(self, raw: str) -> tuple[str, Optional[int], Optional[str]]:
        tokens = [token for token in raw.replace(",", " ").split() if token]
        if not tokens:
            return "", None, None

        variant: Optional[str] = None
        ordinal: Optional[int] = None
        name_parts: list[str] = []

        gold_aliases = {"gold", "doré", "doree", "or"}
        rainbow_aliases = {"rainbow", "rb", "arcenciel", "arc-en-ciel"}
        normal_aliases = {"normal", "base", "standard"}

        for token in tokens:
            lowered = token.lower()
            if lowered.isdigit():
                ordinal = int(lowered)
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

        slug = self._normalize_key(" ".join(name_parts))
        return slug, ordinal, variant

    def _format_pet_record(self, record: Mapping[str, object]) -> str:
        name = str(record.get("name", "Pet"))
        markers = ""
        if bool(record.get("is_huge")):
            markers += " ✨"
        if bool(record.get("is_rainbow")):
            markers += " 🌈"
        elif bool(record.get("is_gold")):
            markers += " 🥇"
        return f"{name}{markers}"

    def _format_listing_line(self, record: Mapping[str, object]) -> str:
        listing_id = int(record.get("id", 0))
        name = self._format_pet_record(record)
        price = embeds.format_currency(int(record.get("price", 0)))
        created_at = record.get("created_at")
        if isinstance(created_at, datetime):  # pragma: no cover - defensive
            timestamp = discord.utils.format_dt(created_at, style="R")
            return f"#{listing_id} • {name} — {price} • {timestamp}"
        return f"#{listing_id} • {name} — {price}"

    async def _resolve_pet(
        self, user_id: int, raw: str
    ) -> tuple[Optional[Mapping[str, object]], Optional[str], Optional[str]]:
        slug, ordinal, variant = self._parse_pet_query(raw)
        if not slug:
            return None, None, "Merci de préciser le nom du pet à mettre en vente."

        definition = self._definition_by_slug.get(slug)
        if definition is None:
            return None, None, "Ce pet est introuvable dans ton inventaire."

        is_gold, is_rainbow = self._variant_flags(variant)
        rows = await self.database.get_user_pet_by_name(
            user_id,
            definition.name,
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            include_on_market=True,
        )
        if not rows:
            label = definition.name if variant is None else f"{definition.name} ({variant})"
            return None, None, f"Tu n'as aucun {label} disponible."

        if ordinal:
            index = ordinal - 1
            if index < 0 or index >= len(rows):
                return None, None, f"Impossible de trouver ce pet numéro {ordinal}."
        else:
            index = 0
            for idx, candidate in enumerate(rows):
                if not bool(candidate.get("is_active")):
                    index = idx
                    break

        record = rows[index]
        display = self._format_pet_record(record)
        return record, display, None

    async def _build_user_cache(
        self, ctx: commands.Context, user_ids: Iterable[int]
    ) -> Dict[int, discord.abc.User]:
        cache: Dict[int, discord.abc.User] = {}
        for user_id in set(user_ids):
            target = None
            if ctx.guild is not None:
                target = ctx.guild.get_member(user_id)
            if target is None:
                target = self.bot.get_user(user_id)
            if target is None:
                with contextlib.suppress(discord.NotFound):
                    target = await self.bot.fetch_user(user_id)
            if target is not None:
                cache[user_id] = target
        return cache

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------
    @commands.group(name="stand", invoke_without_command=True)
    async def stand(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        target = member or ctx.author
        try:
            listings = await self.database.list_active_market_listings(limit=25, seller_id=target.id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        if not listings:
            message = "Aucune annonce active sur ce stand pour le moment."
            if target.id == ctx.author.id:
                message += " Utilise `e!stand add <prix> <pet>` pour démarrer tes ventes."
            embed = embeds.info_embed(message, title=f"Stand de {target.display_name}")
            await ctx.send(embed=embed)
            return

        lines = [self._format_listing_line(record) for record in listings]
        description = "\n".join(lines)
        embed = embeds.info_embed(description, title=f"Stand de {target.display_name}")
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)

    @stand.command(name="add")
    async def stand_add(self, ctx: commands.Context, price: int, *, pet: str) -> None:
        if price <= 0:
            await ctx.send(embed=embeds.error_embed("Le prix doit être supérieur à zéro."))
            return

        record, display, error = await self._resolve_pet(ctx.author.id, pet)
        if record is None or display is None:
            await ctx.send(embed=embeds.error_embed(error or "Ce pet est introuvable."))
            return
        if bool(record.get("is_active")):
            await ctx.send(embed=embeds.error_embed("Ce pet est actuellement équipé."))
            return
        if bool(record.get("on_market")):
            await ctx.send(embed=embeds.error_embed("Ce pet est déjà en vente sur ton stand."))
            return

        user_pet_id = int(record["id"])
        try:
            listing = await self.database.create_market_listing(ctx.author.id, user_pet_id, price)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        listing_id = int(listing["id"])
        embed = embeds.success_embed(
            f"{display} est maintenant en vente pour {embeds.format_currency(price)} (annonce #{listing_id}).",
            title="Annonce créée",
        )
        await ctx.send(embed=embed)

    @stand.command(name="remove")
    async def stand_remove(self, ctx: commands.Context, listing_id: int) -> None:
        try:
            await self.database.cancel_market_listing(listing_id, ctx.author.id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        await ctx.send(embed=embeds.success_embed(f"Annonce #{listing_id} retirée de ton stand."))

    @stand.command(name="buy")
    async def stand_buy(self, ctx: commands.Context, listing_id: int) -> None:
        try:
            listing = await self.database.get_market_listing(listing_id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        if listing is None:
            await ctx.send(embed=embeds.error_embed("Cette annonce n'existe pas ou n'est plus disponible."))
            return

        seller_id = int(listing["seller_id"])
        if seller_id == ctx.author.id:
            await ctx.send(embed=embeds.error_embed("Tu ne peux pas acheter ta propre annonce."))
            return

        try:
            result = await self.database.purchase_market_listing(listing_id, ctx.author.id)
        except InsufficientBalanceError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        listing_record = result["listing"]
        price = int(listing_record["price"])
        seller = ctx.guild.get_member(seller_id) if ctx.guild else None
        if seller is None:
            seller = self.bot.get_user(seller_id)
        if seller is not None:
            seller_name = getattr(seller, "mention", seller.name)
        else:
            seller_name = f"Utilisateur {seller_id}"

        pet_display = self._format_pet_record(listing)
        embed = embeds.success_embed(
            f"Tu as acheté {pet_display} pour {embeds.format_currency(price)} à {seller_name}.",
            title="Achat confirmé",
        )
        await ctx.send(embed=embed)

    @stand.command(name="history")
    async def stand_history(self, ctx: commands.Context, limit: int = 10) -> None:
        limit = max(1, min(limit, 25))
        try:
            entries = await self.database.get_market_activity(ctx.author.id, limit)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        if not entries:
            await ctx.send(embed=embeds.info_embed("Aucune activité récente sur tes annonces."))
            return

        user_ids = {int(row["seller_id"]) for row in entries}
        user_ids.update(int(row["buyer_id"]) for row in entries if row["buyer_id"] is not None)
        user_cache = await self._build_user_cache(ctx, user_ids)

        lines = []
        for row in entries:
            status = str(row["status"])
            price = embeds.format_currency(int(row["price"]))
            seller_id = int(row["seller_id"])
            buyer_id = int(row["buyer_id"]) if row["buyer_id"] is not None else None
            pet_display = self._format_pet_record(row)

            if status == "sold":
                if seller_id == ctx.author.id:
                    buyer = user_cache.get(buyer_id) if buyer_id is not None else None
                    if buyer is not None:
                        buyer_name = getattr(buyer, "mention", buyer.name)
                    else:
                        buyer_name = f"Utilisateur {buyer_id}"
                    summary = f"Vente de {pet_display} pour {price} à {buyer_name}"
                else:
                    seller = user_cache.get(seller_id)
                    if seller is not None:
                        seller_name = getattr(seller, "mention", seller.name)
                    else:
                        seller_name = f"Utilisateur {seller_id}"
                    summary = f"Achat de {pet_display} pour {price} à {seller_name}"
            elif status == "cancelled":
                if seller_id == ctx.author.id:
                    summary = f"Annonce annulée : {pet_display} ({price})"
                else:
                    summary = f"Annonce annulée par le vendeur : {pet_display}"
            else:
                summary = f"Annonce active : {pet_display} ({price})"

            created_at = row.get("created_at")
            if isinstance(created_at, datetime):
                timestamp = f" — publiée {discord.utils.format_dt(created_at, style='R')}"
            else:
                timestamp = ""
            lines.append(f"• {summary}{timestamp}")

        embed = embeds.info_embed("\n".join(lines), title="Historique de stand")
        await ctx.send(embed=embed)

    @commands.command(name="plaza")
    async def plaza(self, ctx: commands.Context) -> None:
        try:
            listings = await self.database.list_active_market_listings(limit=30)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        if not listings:
            await ctx.send(embed=embeds.info_embed("La plaza est calme pour le moment. Reviens plus tard !"))
            return

        grouped: Dict[int, list[Mapping[str, object]]] = defaultdict(list)
        for record in listings:
            grouped[int(record["seller_id"])].append(record)

        user_cache = await self._build_user_cache(ctx, grouped.keys())

        embed = embeds.info_embed("Découvrez les stands des joueurs !", title="🏬 Plaza des stands")
        for seller_id, records in grouped.items():
            seller = user_cache.get(seller_id)
            seller_name = seller.display_name if seller else f"Utilisateur {seller_id}"
            value_lines = [self._format_listing_line(record) for record in records]
            embed.add_field(
                name=f"{seller_name} ({len(records)} annonce{'s' if len(records) > 1 else ''})",
                value="\n".join(value_lines),
                inline=False,
            )

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Plaza(bot))
