"""Gestion de la plaza et des stands de vente."""
from __future__ import annotations

import contextlib
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Mapping, Optional, Sequence, cast

import discord
from discord.ext import commands

from config import (
    POTION_DEFINITION_MAP,
    SELLABLE_ROLE_IDS,
    PET_DEFINITIONS,
    PetDefinition,
    PotionDefinition,
)
from database.db import DatabaseError, InsufficientBalanceError
from utils import embeds
from utils.enchantments import ENCHANTMENT_DEFINITION_MAP, format_enchantment
from utils.pet_formatting import pet_emoji


@dataclass(frozen=True)
class SellerListings:
    """Représente un stand et les annonces associées."""

    seller_id: int
    seller_name: str
    listings: tuple[str, ...]
    total: int
    cheapest: int
    priciest: int
    latest_at: Optional[datetime]


def _chunk_lines(lines: Sequence[str], *, limit: int = 1024) -> list[str]:
    """Découpe une séquence de lignes pour respecter les limites Discord."""

    chunks: list[str] = []
    buffer: list[str] = []
    length = 0

    for line in lines:
        addition = len(line) + (1 if buffer else 0)
        if buffer and length + addition > limit:
            chunks.append("\n".join(buffer))
            buffer = [line]
            length = len(line)
            continue

        buffer.append(line)
        length += addition

    if buffer:
        chunks.append("\n".join(buffer))

    return chunks


class PlazaListingsSelect(discord.ui.Select):
    """Menu déroulant pour naviguer entre les stands disponibles."""

    def __init__(self, view: "PlazaListingsView") -> None:
        options = [
            discord.SelectOption(
                label="🌐 Toutes les annonces",
                value="all",
                description="Voir un aperçu des stands actifs.",
            )
        ]

        for seller in view.sellers[:24]:
            label = seller.seller_name[:95]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(seller.seller_id),
                    description=f"{seller.total} annonce{'s' if seller.total > 1 else ''}",
                )
            )

        super().__init__(
            placeholder="Sélectionne un stand pour voir ses détails…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is None:  # pragma: no cover - defensive
            return

        view = cast(PlazaListingsView, self.view)
        embed = view.get_embed(self.values[0])
        await interaction.response.edit_message(embed=embed, view=view)


class PlazaListingsView(discord.ui.View):
    """Vue interactive permettant d'explorer les stands de la plaza."""

    def __init__(
        self,
        *,
        plaza: "Plaza",
        author: discord.abc.User,
        sellers: Sequence[SellerListings],
        recent_lines: Sequence[str],
        total_listings: int,
        total_sellers: int,
        hidden_count: int = 0,
    ) -> None:
        super().__init__(timeout=180)
        self.plaza = plaza
        self.author = author
        self.sellers: tuple[SellerListings, ...] = tuple(sellers)
        self.recent_lines: tuple[str, ...] = tuple(recent_lines)
        self.total_listings = total_listings
        self.total_sellers = total_sellers
        self.hidden_count = hidden_count
        self.message: Optional[discord.Message] = None
        self._seller_map = {str(seller.seller_id): seller for seller in self.sellers}
        self.add_item(PlazaListingsSelect(self))

    def _build_overview_embed(self) -> discord.Embed:
        embed = embeds.info_embed(
            "Sélectionne un stand dans le menu pour consulter ses annonces.",
            title="🏬 Plaza des stands",
        )

        stats_lines = [
            f"**{self.total_listings}** annonce{'s' if self.total_listings > 1 else ''} actives",
            f"Aperçu des **{min(len(self.recent_lines), self.total_listings)}** plus récentes",
            f"**{self.total_sellers}** stand{'s' if self.total_sellers > 1 else ''} en activité",
        ]
        if self.hidden_count:
            stats_lines.append(
                f"{self.hidden_count} stand{'s' if self.hidden_count > 1 else ''} supplémentaires ne peuvent pas être affichés (limite Discord)."
            )

        embed.add_field(name="Statistiques", value="\n".join(stats_lines), inline=False)

        if self.recent_lines:
            for index, chunk in enumerate(_chunk_lines(self.recent_lines)):
                name = "🆕 Dernières annonces" if index == 0 else "\u200b"
                embed.add_field(name=name, value=chunk, inline=False)

        embed.set_footer(text="Astuce : tu peux rouvrir la plaza avec e!plaza à tout moment.")
        return embed

    def _build_seller_embed(self, seller: SellerListings) -> discord.Embed:
        embed = embeds.info_embed(
            f"{seller.seller_name} propose actuellement {seller.total} annonce{'s' if seller.total > 1 else ''}.",
            title=f"🛒 Stand de {seller.seller_name}",
        )

        stats_lines = [
            f"Prix : {embeds.format_gems(seller.cheapest)}",
        ]
        if seller.cheapest != seller.priciest:
            stats_lines[-1] += f" → {embeds.format_gems(seller.priciest)}"

        if seller.latest_at:
            stats_lines.append(
                f"Dernière mise à jour {discord.utils.format_dt(seller.latest_at, style='R')}"
            )

        embed.add_field(name="Informations", value="\n".join(stats_lines), inline=False)

        for index, chunk in enumerate(_chunk_lines(seller.listings)):
            name = "Annonces disponibles" if index == 0 else "\u200b"
            embed.add_field(name=name, value=chunk, inline=False)

        embed.set_footer(text="Utilise le menu déroulant pour changer de stand.")
        return embed

    def get_embed(self, key: str) -> discord.Embed:
        if key == "all":
            return self._build_overview_embed()

        seller = self._seller_map.get(key)
        if seller is None:
            return self._build_overview_embed()

        return self._build_seller_embed(seller)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author.id:
            return True

        await interaction.response.send_message(
            "Seule la personne qui a ouvert la plaza peut utiliser ce menu.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        if self.message is None:
            return

        for child in self.children:
            if isinstance(child, discord.ui.Select):
                child.disabled = True

        await self.message.edit(view=self)

class StandPetListingModal(discord.ui.Modal):
    def __init__(self, view: "StandManagementView") -> None:
        super().__init__(title="Lister un pet")
        self.view = view
        self.price_input = discord.ui.TextInput(
            label="Prix", placeholder="Ex: 150000", min_length=1, max_length=18
        )
        self.pet_input = discord.ui.TextInput(
            label="Pet à vendre",
            placeholder="Ex: Huge Shelly ou Shelly gold",
            min_length=1,
            max_length=100,
        )
        self.add_item(self.price_input)
        self.add_item(self.pet_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            price = int(self.price_input.value.replace(" ", ""))
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci d'indiquer un prix valide."),
                ephemeral=True,
            )
            return

        success, embed = await self.view.plaza._create_pet_listing_embed(
            interaction.user, price, self.pet_input.value
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        if success:
            await self.view.mark_dirty()
            await self.view.refresh_if_needed()


class AuctionBidModal(discord.ui.Modal):
    def __init__(self, plaza: "Plaza", author: discord.abc.User) -> None:
        super().__init__(title="Faire une offre")
        self.plaza = plaza
        self.author = author
        self.auction_id_input = discord.ui.TextInput(
            label="ID de l'enchère", placeholder="Ex: 12", min_length=1, max_length=10
        )
        self.amount_input = discord.ui.TextInput(
            label="Montant de l'offre", placeholder="Ex: 50000", min_length=1, max_length=18
        )
        self.add_item(self.auction_id_input)
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            auction_id = int(self.auction_id_input.value.replace(" ", ""))
            amount = int(self.amount_input.value.replace(" ", ""))
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci d'indiquer des valeurs numériques."),
                ephemeral=True,
            )
            return
        if amount <= 0:
            await interaction.response.send_message(
                embed=embeds.error_embed("Le montant doit être positif."), ephemeral=True
            )
            return
        try:
            await self.plaza.database.place_auction_bid(
                auction_id, self.author.id, amount
            )
        except InsufficientBalanceError as exc:
            await interaction.response.send_message(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            return
        except DatabaseError as exc:
            await interaction.response.send_message(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            return

        detailed = await self.plaza.database.get_auction_listing(auction_id)
        if detailed is None:
            await interaction.response.send_message(
                embed=embeds.success_embed(
                    "Ton offre a été enregistrée.", title="Enchère mise à jour"
                ),
                ephemeral=True,
            )
            return

        status = str(detailed.get("status", "active"))
        if status == "sold":
            summary = "Tu remportes immédiatement cette enchère !"
        else:
            summary = "Tu es désormais l'enchérisseur principal."
        line = self.plaza._format_auction_line(detailed)
        embed = embeds.success_embed(
            f"{line}\n{summary}",
            title="Offre enregistrée",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class _BaseAuctionModal(discord.ui.Modal):
    def __init__(self, plaza: "Plaza", author: discord.abc.User, title: str) -> None:
        super().__init__(title=title)
        self.plaza = plaza
        self.author = author

    @staticmethod
    def _parse_optional_int(raw: str) -> int | None:
        cleaned = raw.replace(" ", "").strip()
        if not cleaned:
            return None
        return int(cleaned)

    @staticmethod
    def _parse_int(raw: str) -> int | None:
        try:
            value = int(raw.replace(" ", ""))
        except ValueError:
            return None
        if value <= 0:
            return None
        return value


class PetAuctionModal(_BaseAuctionModal):
    def __init__(self, plaza: "Plaza", author: discord.abc.User) -> None:
        super().__init__(plaza, author, "Créer une enchère - Pet")
        self.pet_input = discord.ui.TextInput(
            label="Pet à vendre",
            placeholder="Ex: Huge Shelly ou Shelly gold",
            min_length=1,
            max_length=100,
        )
        self.starting_bid_input = discord.ui.TextInput(
            label="Mise de départ", placeholder="Ex: 100000", min_length=1, max_length=18
        )
        self.duration_input = discord.ui.TextInput(
            label="Durée (minutes)",
            placeholder="Ex: 120",
            min_length=1,
            max_length=4,
        )
        self.buyout_input = discord.ui.TextInput(
            label="Achat direct (optionnel)",
            placeholder="Laisse vide si pas d'achat direct",
            required=False,
            max_length=18,
        )
        self.add_item(self.pet_input)
        self.add_item(self.starting_bid_input)
        self.add_item(self.duration_input)
        self.add_item(self.buyout_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        starting_bid = self._parse_int(self.starting_bid_input.value)
        duration_minutes = self._parse_int(self.duration_input.value)
        if starting_bid is None or duration_minutes is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci de saisir des valeurs numériques positives."),
                ephemeral=True,
            )
            return
        try:
            buyout = self._parse_optional_int(self.buyout_input.value)
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Le prix d'achat direct doit être numérique."),
                ephemeral=True,
            )
            return

        record, _, error = await self.plaza._resolve_pet(self.author.id, self.pet_input.value)
        if error:
            await interaction.response.send_message(
                embed=embeds.error_embed(error), ephemeral=True
            )
            return
        if record is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Pet introuvable."), ephemeral=True
            )
            return

        user_pet_id = int(record.get("id") or 0)
        try:
            listing = await self.plaza.database.create_pet_auction(
                self.author.id,
                user_pet_id,
                starting_bid=starting_bid,
                duration_minutes=duration_minutes,
                buyout_price=buyout,
            )
        except DatabaseError as exc:
            await interaction.response.send_message(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            return

        embed = await self.plaza._build_auction_creation_embed(int(listing["id"]))
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TicketAuctionModal(_BaseAuctionModal):
    def __init__(self, plaza: "Plaza", author: discord.abc.User) -> None:
        super().__init__(plaza, author, "Créer une enchère - Tickets")
        self.quantity_input = discord.ui.TextInput(
            label="Quantité", placeholder="Ex: 5", min_length=1, max_length=10
        )
        self.starting_bid_input = discord.ui.TextInput(
            label="Mise de départ", placeholder="Ex: 20000", min_length=1, max_length=18
        )
        self.duration_input = discord.ui.TextInput(
            label="Durée (minutes)", placeholder="Ex: 180", min_length=1, max_length=4
        )
        self.buyout_input = discord.ui.TextInput(
            label="Achat direct (optionnel)",
            placeholder="Laisse vide si pas d'achat direct",
            required=False,
            max_length=18,
        )
        self.add_item(self.quantity_input)
        self.add_item(self.starting_bid_input)
        self.add_item(self.duration_input)
        self.add_item(self.buyout_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        quantity = self._parse_int(self.quantity_input.value)
        starting_bid = self._parse_int(self.starting_bid_input.value)
        duration_minutes = self._parse_int(self.duration_input.value)
        if quantity is None or starting_bid is None or duration_minutes is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci de saisir des valeurs numériques positives."),
                ephemeral=True,
            )
            return
        try:
            buyout = self._parse_optional_int(self.buyout_input.value)
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Le prix d'achat direct doit être numérique."),
                ephemeral=True,
            )
            return
        if quantity <= 0:
            await interaction.response.send_message(
                embed=embeds.error_embed("La quantité doit être positive."), ephemeral=True
            )
            return
        try:
            listing = await self.plaza.database.create_item_auction(
                self.author.id,
                item_type="ticket",
                item_slug="raffle_ticket",
                quantity=quantity,
                starting_bid=starting_bid,
                duration_minutes=duration_minutes,
                buyout_price=buyout,
            )
        except DatabaseError as exc:
            await interaction.response.send_message(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            return
        embed = await self.plaza._build_auction_creation_embed(int(listing["id"]))
        await interaction.response.send_message(embed=embed, ephemeral=True)


class PotionAuctionModal(_BaseAuctionModal):
    def __init__(self, plaza: "Plaza", author: discord.abc.User) -> None:
        super().__init__(plaza, author, "Créer une enchère - Potion")
        self.slug_input = discord.ui.TextInput(
            label="Potion à vendre",
            placeholder="Slug exact de la potion",
            min_length=1,
            max_length=50,
        )
        self.quantity_input = discord.ui.TextInput(
            label="Quantité", placeholder="Ex: 3", min_length=1, max_length=10
        )
        self.starting_bid_input = discord.ui.TextInput(
            label="Mise de départ", placeholder="Ex: 150000", min_length=1, max_length=18
        )
        self.duration_input = discord.ui.TextInput(
            label="Durée (minutes)", placeholder="Ex: 240", min_length=1, max_length=4
        )
        self.buyout_input = discord.ui.TextInput(
            label="Achat direct (optionnel)",
            placeholder="Laisse vide si pas d'achat direct",
            required=False,
            max_length=18,
        )
        self.add_item(self.slug_input)
        self.add_item(self.quantity_input)
        self.add_item(self.starting_bid_input)
        self.add_item(self.duration_input)
        self.add_item(self.buyout_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        quantity = self._parse_int(self.quantity_input.value)
        starting_bid = self._parse_int(self.starting_bid_input.value)
        duration_minutes = self._parse_int(self.duration_input.value)
        if quantity is None or starting_bid is None or duration_minutes is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci de saisir des valeurs numériques positives."),
                ephemeral=True,
            )
            return
        try:
            buyout = self._parse_optional_int(self.buyout_input.value)
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Le prix d'achat direct doit être numérique."),
                ephemeral=True,
            )
            return
        if quantity <= 0:
            await interaction.response.send_message(
                embed=embeds.error_embed("La quantité doit être positive."), ephemeral=True
            )
            return
        try:
            listing = await self.plaza.database.create_item_auction(
                self.author.id,
                item_type="potion",
                item_slug=self.slug_input.value.lower(),
                quantity=quantity,
                starting_bid=starting_bid,
                duration_minutes=duration_minutes,
                buyout_price=buyout,
            )
        except DatabaseError as exc:
            await interaction.response.send_message(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            return
        embed = await self.plaza._build_auction_creation_embed(int(listing["id"]))
        await interaction.response.send_message(embed=embed, ephemeral=True)


class EnchantAuctionModal(_BaseAuctionModal):
    def __init__(self, plaza: "Plaza", author: discord.abc.User) -> None:
        super().__init__(plaza, author, "Créer une enchère - Enchantement")
        self.slug_input = discord.ui.TextInput(
            label="Enchantement", placeholder="Slug de l'enchantement", min_length=1, max_length=50
        )
        self.power_input = discord.ui.TextInput(
            label="Niveau", placeholder="Entre 1 et 10", min_length=1, max_length=2
        )
        self.starting_bid_input = discord.ui.TextInput(
            label="Mise de départ", placeholder="Ex: 75000", min_length=1, max_length=18
        )
        self.duration_input = discord.ui.TextInput(
            label="Durée (minutes)", placeholder="Ex: 90", min_length=1, max_length=4
        )
        self.buyout_input = discord.ui.TextInput(
            label="Achat direct (optionnel)",
            placeholder="Laisse vide si pas d'achat direct",
            required=False,
            max_length=18,
        )
        self.add_item(self.slug_input)
        self.add_item(self.power_input)
        self.add_item(self.starting_bid_input)
        self.add_item(self.duration_input)
        self.add_item(self.buyout_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        power = self._parse_int(self.power_input.value)
        starting_bid = self._parse_int(self.starting_bid_input.value)
        duration_minutes = self._parse_int(self.duration_input.value)
        if power is None or starting_bid is None or duration_minutes is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci de saisir des valeurs numériques positives."),
                ephemeral=True,
            )
            return
        if self.slug_input.value not in ENCHANTMENT_DEFINITION_MAP:
            await interaction.response.send_message(
                embed=embeds.error_embed("Cet enchantement est inconnu."),
                ephemeral=True,
            )
            return
        if power < 1 or power > 10:
            await interaction.response.send_message(
                embed=embeds.error_embed("Le niveau doit être compris entre 1 et 10."),
                ephemeral=True,
            )
            return
        try:
            buyout = self._parse_optional_int(self.buyout_input.value)
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Le prix d'achat direct doit être numérique."),
                ephemeral=True,
            )
            return
        try:
            listing = await self.plaza.database.create_item_auction(
                self.author.id,
                item_type="enchantment",
                item_slug=self.slug_input.value,
                enchantment_power=power,
                quantity=1,
                starting_bid=starting_bid,
                duration_minutes=duration_minutes,
                buyout_price=buyout,
            )
        except DatabaseError as exc:
            await interaction.response.send_message(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            return
        embed = await self.plaza._build_auction_creation_embed(int(listing["id"]))
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AuctionCreationView(discord.ui.View):
    """Vue guidant la création d'une enchère via des formulaires dédiés."""

    def __init__(self, plaza: "Plaza", author: discord.abc.User) -> None:
        super().__init__(timeout=180)
        self.plaza = plaza
        self.author = author
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author.id:
            return True
        await interaction.response.send_message(
            "Seule la personne qui a ouvert ce formulaire peut créer une enchère.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        with contextlib.suppress(discord.HTTPException):
            await self.message.edit(view=self)

    @discord.ui.select(
        placeholder="Choisis ce que tu veux vendre",
        options=[
            discord.SelectOption(
                label="Pet", value="pet", description="Mettre un pet aux enchères"
            ),
            discord.SelectOption(
                label="Tickets", value="ticket", description="Vendre des tickets de loterie"
            ),
            discord.SelectOption(
                label="Potion", value="potion", description="Vendre une potion"
            ),
            discord.SelectOption(
                label="Enchantement", value="enchant", description="Vendre un enchantement"
            ),
        ],
    )
    async def auction_type_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        value = select.values[0]
        if value == "pet":
            await interaction.response.send_modal(PetAuctionModal(self.plaza, self.author))
            return
        if value == "ticket":
            await interaction.response.send_modal(
                TicketAuctionModal(self.plaza, self.author)
            )
            return
        if value == "potion":
            await interaction.response.send_modal(
                PotionAuctionModal(self.plaza, self.author)
            )
            return
        await interaction.response.send_modal(
            EnchantAuctionModal(self.plaza, self.author)
        )

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.secondary)
    async def close_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        del button
        for child in self.children:
            child.disabled = True
        self.stop()
        if interaction.response.is_done():
            if self.message is not None:
                await self.message.edit(view=self)
        else:
            await interaction.response.edit_message(view=self)


class StandTicketListingModal(discord.ui.Modal):
    def __init__(self, view: "StandManagementView") -> None:
        super().__init__(title="Lister des tickets")
        self.view = view
        self.quantity_input = discord.ui.TextInput(
            label="Quantité de tickets",
            placeholder="Ex: 5",
            min_length=1,
            max_length=5,
        )
        self.price_input = discord.ui.TextInput(
            label="Prix total (Gemmes)",
            placeholder="Ex: 5000",
            min_length=1,
            max_length=18,
        )
        self.add_item(self.quantity_input)
        self.add_item(self.price_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            quantity = int(self.quantity_input.value)
            price = int(self.price_input.value.replace(" ", ""))
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Indique une quantité et un prix valides."),
                ephemeral=True,
            )
            return

        success, embed = await self.view.plaza._create_ticket_listing_embed(
            interaction.user, quantity, price
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        if success:
            await self.view.mark_dirty()
            await self.view.refresh_if_needed()


class StandPotionListingModal(discord.ui.Modal):
    def __init__(self, view: "StandManagementView") -> None:
        super().__init__(title="Lister une potion")
        self.view = view
        self.slug_input = discord.ui.TextInput(
            label="Potion",
            placeholder="Slug ou nom (ex: chance, vitesse)",
            min_length=1,
            max_length=50,
        )
        self.quantity_input = discord.ui.TextInput(
            label="Quantité",
            placeholder="Ex: 1",
            min_length=1,
            max_length=5,
        )
        self.price_input = discord.ui.TextInput(
            label="Prix total (Gemmes)",
            placeholder="Ex: 25000",
            min_length=1,
            max_length=18,
        )
        self.add_item(self.slug_input)
        self.add_item(self.quantity_input)
        self.add_item(self.price_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            quantity = int(self.quantity_input.value)
            price = int(self.price_input.value.replace(" ", ""))
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci d'indiquer une quantité et un prix valides."),
                ephemeral=True,
            )
            return

        success, embed = await self.view.plaza._create_potion_listing_embed(
            interaction.user,
            self.slug_input.value,
            quantity,
            price,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        if success:
            await self.view.mark_dirty()
            await self.view.refresh_if_needed()


class StandRemoveListingModal(discord.ui.Modal):
    def __init__(self, view: "StandManagementView") -> None:
        super().__init__(title="Retirer une annonce")
        self.view = view
        self.listing_input = discord.ui.TextInput(
            label="Identifiant de l'annonce",
            placeholder="Ex: 42",
            min_length=1,
            max_length=10,
        )
        self.add_item(self.listing_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            listing_id = int(self.listing_input.value)
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Identifiant d'annonce invalide."),
                ephemeral=True,
            )
            return

        success, embed = await self.view.plaza._cancel_listing_embed(
            interaction.user, listing_id
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        if success:
            await self.view.mark_dirty()
            await self.view.refresh_if_needed()


class StandRoleListingModal(discord.ui.Modal):
    def __init__(self, view: "StandManagementView", role: discord.Role) -> None:
        super().__init__(title="Mettre un rôle en vente")
        self.view = view
        self.role = role
        self.price_input = discord.ui.TextInput(
            label="Prix total (Gemmes)",
            placeholder="Ex: 50000",
            min_length=1,
            max_length=18,
        )
        self.add_item(self.price_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            price = int(self.price_input.value.replace(" ", ""))
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci d'indiquer un prix numérique valide."),
                ephemeral=True,
            )
            return

        success, embed = await self.view.plaza._create_role_listing_embed(
            interaction.user, self.role, price
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        if success:
            await self.view.mark_dirty()
            await self.view.refresh_if_needed()


class StandManagementView(discord.ui.View):
    def __init__(self, plaza: "Plaza", author: discord.abc.User) -> None:
        super().__init__(timeout=180)
        self.plaza = plaza
        self.author = author
        self.message: Optional[discord.Message] = None
        self._dirty = False

    async def mark_dirty(self) -> None:
        self._dirty = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Seul le propriétaire du stand peut utiliser ces boutons.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        await self.message.edit(view=self)
        self.plaza._clear_stand_view(self.author.id, self)

    def stop(self) -> None:  # pragma: no cover - gestion du cycle de vie
        self.plaza._clear_stand_view(self.author.id, self)
        super().stop()

    async def refresh_if_needed(self) -> None:
        if not self._dirty or self.message is None:
            return
        self._dirty = False
        embed, ok = await self.plaza._build_stand_overview_embed(
            self.author, include_instructions=True
        )
        await self.message.edit(embed=embed, view=self)
        if not ok:
            self.stop()

    @discord.ui.button(label="Lister un pet", style=discord.ButtonStyle.primary)
    async def list_pet(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(StandPetListingModal(self))

    @discord.ui.button(label="Lister des tickets", style=discord.ButtonStyle.secondary)
    async def list_tickets(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(StandTicketListingModal(self))

    @discord.ui.button(label="Lister une potion", style=discord.ButtonStyle.secondary)
    async def list_potion(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(StandPotionListingModal(self))

    @discord.ui.button(label="Vendre un rôle", style=discord.ButtonStyle.secondary)
    async def list_role(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Impossible de vérifier tes rôles sur ce serveur."),
                ephemeral=True,
            )
            return

        seller = guild.get_member(self.author.id)
        if seller is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Impossible de vérifier tes rôles actuellement."),
                ephemeral=True,
            )
            return

        roles = self.plaza._get_sellable_roles(seller)
        if not roles:
            await interaction.response.send_message(
                embed=embeds.error_embed("Tu n'as aucun rôle vendable."),
                ephemeral=True,
            )
            return

        if len(roles) == 1:
            await interaction.response.send_modal(StandRoleListingModal(self, roles[0]))
            return

        options = [
            discord.SelectOption(label=role.name[:100], value=str(role.id)) for role in roles
        ]
        select = discord.ui.Select(placeholder="Choisis le rôle à vendre", options=options)

        async def _callback(select_interaction: discord.Interaction) -> None:
            role_id = int(select.values[0])
            role = guild.get_role(role_id)
            if role is None:
                await select_interaction.response.send_message(
                    embed=embeds.error_embed("Ce rôle n'existe plus."),
                    ephemeral=True,
                )
                return
            await select_interaction.response.send_modal(StandRoleListingModal(self, role))

        select.callback = _callback
        role_view = discord.ui.View()
        role_view.add_item(select)
        await interaction.response.send_message(
            embed=embeds.info_embed("Choisis le rôle que tu veux mettre en vente."),
            view=role_view,
            ephemeral=True,
        )

    @discord.ui.button(label="Retirer une annonce", style=discord.ButtonStyle.danger)
    async def remove_listing(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(StandRemoveListingModal(self))


class ConsumableFilterSelect(discord.ui.Select):
    def __init__(self, view: "ConsumableListingsView") -> None:
        super().__init__(
            placeholder="Filtre les consommables en vente…",
            min_values=1,
            max_values=1,
            options=view._build_filter_options(),
        )
        for option in self.options:
            option.default = option.value == view.current_filter

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is None:
            return
        view = cast(ConsumableListingsView, self.view)
        key = self.values[0]
        view.current_filter = key
        for option in self.options:
            option.default = option.value == key
        embed = view.get_embed(key)
        await interaction.response.edit_message(embed=embed, view=view)


class ConsumablePurchaseModal(discord.ui.Modal):
    def __init__(self, view: "ConsumableListingsView") -> None:
        super().__init__(title="Acheter un consommable")
        self.view = view
        self.listing_id = discord.ui.TextInput(
            label="ID de l'annonce",
            placeholder="123",
            min_length=1,
            max_length=10,
        )
        self.add_item(self.listing_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = self.listing_id.value.strip()
        try:
            listing_id = int(value)
            if listing_id <= 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed("Merci d'indiquer un identifiant valide."),
                ephemeral=True,
            )
            return

        await self.view.handle_purchase(interaction, listing_id)


class ConsumableListingsView(discord.ui.View):
    def __init__(
        self,
        plaza: "Plaza",
        author: discord.abc.User,
        listings: Sequence[Mapping[str, object]],
        user_cache: Mapping[int, discord.abc.User],
    ) -> None:
        super().__init__(timeout=180)
        self.plaza = plaza
        self.author = author
        self.listings: tuple[Mapping[str, object], ...] = tuple(listings)
        self.user_cache = user_cache
        self.message: Optional[discord.Message] = None
        self.potion_filters: list[tuple[str, str]] = self._collect_potion_filters()
        self.current_filter: str = "all"
        self.filter_select = ConsumableFilterSelect(self)
        self.add_item(self.filter_select)
        self.update_filters()

    def _collect_potion_filters(self) -> list[tuple[str, str]]:
        filters: dict[str, str] = {}
        for record in self.listings:
            if str(record.get("item_type")) != "potion":
                continue
            slug = str(record.get("item_slug") or "")
            definition = POTION_DEFINITION_MAP.get(slug)
            name = definition.name if definition else slug
            filters[slug] = name
        return sorted(filters.items(), key=lambda item: item[1].lower())

    def _build_filter_options(self) -> list[discord.SelectOption]:
        options = [
            discord.SelectOption(
                label="🌐 Tous les consommables",
                value="all",
                description="Voir chaque annonce active.",
            ),
            discord.SelectOption(
                label="🎟️ Tickets",
                value="ticket",
                description="Afficher uniquement les tickets.",
            ),
        ]

        has_role_listing = any(
            str(record.get("item_type")) == "role" for record in self.listings
        )
        if has_role_listing:
            options.append(
                discord.SelectOption(
                    label="🛡️ Rôles",
                    value="role",
                    description="Afficher les rôles en vente.",
                )
            )

        for slug, name in self.potion_filters[:23]:
            options.append(
                discord.SelectOption(
                    label=f"🧪 {name}",
                    value=f"potion:{slug}",
                )
            )
        return options

    def _format_line(self, record: Mapping[str, object]) -> str:
        listing_id = int(record.get("id", 0))
        quantity = int(record.get("quantity", 0))
        price = embeds.format_gems(int(record.get("price", 0)))
        seller_id = int(record.get("seller_id", 0))
        seller = self.user_cache.get(seller_id)
        seller_name = seller.display_name if seller else f"Utilisateur {seller_id}"
        created_at = record.get("created_at")
        timestamp = ""
        if isinstance(created_at, datetime):
            timestamp = f" • {discord.utils.format_dt(created_at, style='R')}"

        item_type = str(record.get("item_type", ""))
        if item_type == "ticket":
            label = f"🎟️ Tickets ×{quantity}"
        elif item_type == "role":
            slug = str(record.get("item_slug") or "")
            label = f"🛡️ {self.plaza._role_label(slug, getattr(self.author, 'guild', None))}"
        else:
            slug = str(record.get("item_slug") or "")
            definition = POTION_DEFINITION_MAP.get(slug)
            name = definition.name if definition else slug or "Potion"
            label = f"🧪 {name} ×{quantity}"
        return f"#{listing_id} • {label} — {price}{timestamp} • Vendeur : {seller_name}"

    def get_embed(self, key: str) -> discord.Embed:
        if key == "ticket":
            filtered = [
                record
                for record in self.listings
                if str(record.get("item_type")) == "ticket"
            ]
            title = "🎟️ Tickets en vente"
        elif key == "role":
            filtered = [
                record for record in self.listings if str(record.get("item_type")) == "role"
            ]
            title = "🛡️ Rôles en vente"
        elif key.startswith("potion:"):
            slug = key.split(":", 1)[1]
            filtered = [
                record
                for record in self.listings
                if str(record.get("item_type")) == "potion"
                and str(record.get("item_slug") or "") == slug
            ]
            definition = POTION_DEFINITION_MAP.get(slug)
            name = definition.name if definition else slug
            title = f"🧪 {name} en vente"
        else:
            filtered = list(self.listings)
            title = "🛍️ Consommables de la plaza"

        if not filtered:
            embed = embeds.info_embed(
                "Aucune annonce ne correspond à ce filtre pour le moment.",
                title=title,
            )
            return embed

        lines = [self._format_line(record) for record in filtered]
        chunks = _chunk_lines(lines)
        embed = embeds.info_embed("\n".join(chunks[:1]), title=title)
        for chunk in chunks[1:]:
            embed.add_field(name="\u200b", value=chunk, inline=False)
        embed.set_footer(text="Utilise le menu pour changer de filtre.")
        return embed

    def update_filters(self) -> None:
        self.potion_filters = self._collect_potion_filters()
        valid_filters = {"all", "ticket", "role"}
        valid_filters.update(f"potion:{slug}" for slug, _ in self.potion_filters)
        if self.current_filter not in valid_filters:
            self.current_filter = "all"
        options = self._build_filter_options()
        for option in options:
            option.default = option.value == self.current_filter
        self.filter_select.options = options
        if hasattr(self, "purchase_button"):
            self.purchase_button.disabled = not self.listings

    def remove_listing(self, listing_id: int) -> None:
        self.listings = tuple(
            record
            for record in self.listings
            if int(record.get("id", 0)) != listing_id
        )
        self.update_filters()

    async def refresh(self) -> None:
        if self.message is None:
            return
        embed = self.get_embed(self.current_filter)
        await self.message.edit(embed=embed, view=self)

    async def handle_purchase(
        self, interaction: discord.Interaction, listing_id: int
    ) -> None:
        success, embed, seller_id = await self.plaza._complete_consumable_purchase(
            interaction.user,
            listing_id,
            guild=interaction.guild,
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=not success)
        else:
            await interaction.followup.send(embed=embed, ephemeral=not success)

        if not success:
            return

        if seller_id is not None:
            await self.plaza._refresh_active_stand_view(seller_id)

        self.remove_listing(listing_id)
        await self.refresh()

    @discord.ui.button(
        label="Acheter un consommable", style=discord.ButtonStyle.success
    )
    async def purchase_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(ConsumablePurchaseModal(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author.id:
            return True
        await interaction.response.send_message(
            "Seule la personne ayant ouvert la plaza peut filtrer ces annonces.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        await self.message.edit(view=self)

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
        self._stand_views: Dict[int, StandManagementView] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_key(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return "".join(ch for ch in normalized.lower() if ch.isalnum())

    @staticmethod
    def _variant_flags(
        variant: Mapping[str, Optional[bool]]
    ) -> tuple[Optional[bool], Optional[bool], Optional[bool]]:
        return (
            variant.get("gold"),
            variant.get("rainbow"),
            variant.get("shiny"),
        )

    @staticmethod
    def _pick_preferred_listing_index(rows: Sequence[Mapping[str, object]]) -> int:
        normal_candidates: list[int] = []
        fallback_inactive: list[int] = []
        for index, record in enumerate(rows):
            is_active = bool(record.get("is_active"))
            is_gold = bool(record.get("is_gold"))
            is_rainbow = bool(record.get("is_rainbow"))
            if not is_gold and not is_rainbow:
                if not is_active:
                    return index
                normal_candidates.append(index)
            if not is_active:
                fallback_inactive.append(index)
        if fallback_inactive:
            return fallback_inactive[0]
        if normal_candidates:
            return normal_candidates[0]
        return 0

    def _parse_pet_query(
        self, raw: str
    ) -> tuple[str, Optional[int], Mapping[str, Optional[bool]]]:
        tokens = [token for token in raw.replace(",", " ").split() if token]
        if not tokens:
            return "", None, {}

        ordinal: Optional[int] = None
        name_parts: list[str] = []
        variant_flags: dict[str, Optional[bool]] = {
            "gold": None,
            "rainbow": None,
            "shiny": None,
        }

        gold_aliases = {"gold", "doré", "doree", "or"}
        rainbow_aliases = {"rainbow", "rb", "arcenciel", "arc-en-ciel"}
        normal_aliases = {"normal", "base", "standard"}
        shiny_aliases = {"shiny", "brillant", "etincelant", "étincelant"}
        dull_aliases = {"noshiny", "nonshiny", "sansshiny"}

        for token in tokens:
            lowered = token.lower()
            if lowered.isdigit():
                ordinal = int(lowered)
                continue
            if lowered in gold_aliases:
                variant_flags["gold"] = True
                variant_flags["rainbow"] = False
                continue
            if lowered in rainbow_aliases:
                variant_flags["rainbow"] = True
                variant_flags["gold"] = False
                continue
            if lowered in normal_aliases:
                variant_flags["gold"] = False
                variant_flags["rainbow"] = False
                continue
            if lowered in shiny_aliases:
                variant_flags["shiny"] = True
                continue
            if lowered in dull_aliases:
                variant_flags["shiny"] = False
                continue
            name_parts.append(token)

        if not name_parts:
            name_parts = tokens

        slug = self._normalize_key(" ".join(name_parts))
        return slug, ordinal, variant_flags

    # Gestion des vues de stand actives
    def _register_stand_view(self, view: StandManagementView) -> None:
        previous = self._stand_views.get(view.author.id)
        if previous is view:
            return
        if previous is not None:
            previous.stop()
        self._stand_views[view.author.id] = view

    def _clear_stand_view(
        self, user_id: int, view: StandManagementView
    ) -> None:  # pragma: no cover - simple cache
        if self._stand_views.get(user_id) is view:
            self._stand_views.pop(user_id, None)

    async def _refresh_active_stand_view(self, user_id: int) -> None:
        view = self._stand_views.get(user_id)
        if view is None or view.message is None:
            return
        await view.mark_dirty()
        await view.refresh_if_needed()

    def _format_pet_record(self, record: Mapping[str, object]) -> str:
        name = str(record.get("name", "Pet"))
        markers: list[str] = []
        if bool(record.get("is_huge")):
            markers.append("🌟")
        if bool(record.get("is_rainbow")):
            markers.append("🌈")
        elif bool(record.get("is_gold")):
            markers.append("🥇")
        if bool(record.get("is_shiny")):
            markers.append("✨")
        marker_text = " ".join(markers)
        emoji = pet_emoji(name)
        if marker_text:
            return f"{emoji} {marker_text}".strip()
        return emoji

    def _format_listing_line(self, record: Mapping[str, object]) -> str:
        listing_id = int(record.get("id", 0))
        name = self._format_pet_record(record)
        price = embeds.format_gems(int(record.get("price", 0)))
        created_at = record.get("created_at")
        if isinstance(created_at, datetime):  # pragma: no cover - defensive
            timestamp = discord.utils.format_dt(created_at, style="R")
            return f"#{listing_id} • {name} — {price} • {timestamp}"
        return f"#{listing_id} • {name} — {price}"

    def _format_enchantment_label(self, slug: str, power: int) -> str:
        definition = ENCHANTMENT_DEFINITION_MAP.get(slug)
        if definition:
            return f"✨ {format_enchantment(definition, power)}"
        return f"✨ {slug} (puissance {power})"

    def _format_auction_item(self, record: Mapping[str, object]) -> str:
        item_type = str(record.get("item_type", ""))
        quantity = int(record.get("quantity", 1))
        if item_type == "pet":
            return self._format_pet_record(record)
        if item_type == "ticket":
            return f"🎟️ Tickets x{quantity}"
        if item_type == "potion":
            slug = str(record.get("item_slug") or "")
            definition = POTION_DEFINITION_MAP.get(slug)
            name = definition.name if definition else slug
            return f"🧪 {name} x{quantity}"
        if item_type == "enchantment":
            slug = str(record.get("item_slug") or "")
            power = int(record.get("item_power") or 0)
            return self._format_enchantment_label(slug, power)
        return "🛒 Objet mystère"

    def _format_auction_line(
        self, record: Mapping[str, object], *, include_status: bool = False
    ) -> str:
        listing_id = int(record.get("id", 0))
        item_label = self._format_auction_item(record)
        current_bid = int(record.get("current_bid") or 0)
        starting_bid = int(record.get("starting_bid") or 0)
        price_label = embeds.format_gems(current_bid or starting_bid)
        price_text = (
            f"Offre actuelle {price_label}"
            if current_bid > 0
            else f"Mise départ {price_label}"
        )
        ends_at = record.get("ends_at")
        if isinstance(ends_at, datetime):
            end_text = discord.utils.format_dt(ends_at, style="R")
        else:
            end_text = "bientôt"
        seller_id = int(record.get("seller_id") or 0)
        buyout = record.get("buyout_price")
        buyout_text = (
            f" • Achat direct {embeds.format_gems(int(buyout))}"
            if buyout
            else ""
        )
        line = (
            f"#{listing_id} • {item_label} — {price_text}{buyout_text} • Fin {end_text}"
            f" • Vendeur : <@{seller_id}>"
        )
        if include_status:
            status = str(record.get("status", "active"))
            if status != "active":
                status_label = "Terminé" if status == "sold" else "Annulé"
                line += f" • Statut : {status_label}"
        return line

    async def _build_auction_overview_embed(
        self, member: discord.abc.User
    ) -> discord.Embed:
        try:
            listings = await self.database.list_active_auctions(limit=25)
        except DatabaseError as exc:
            return embeds.error_embed(str(exc))

        if not listings:
            embed = embeds.info_embed(
                "Aucune enchère active pour le moment.",
                title="⚖️ Ventes aux enchères",
            )
            embed.set_footer(
                text="Crée ta première enchère avec e!auction pet/potion/enchant."
            )
            if getattr(member, "display_avatar", None):
                embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
            return embed

        lines = [self._format_auction_line(record) for record in listings]
        embed = embeds.info_embed(
            "\n".join(lines),
            title="⚖️ Ventes aux enchères",
        )
        if getattr(member, "display_avatar", None):
            embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_footer(text="Participe avec e!auction bid <id> <montant>.")
        return embed

    async def _open_auction_browser(
        self, interaction: discord.Interaction, member: discord.abc.User
    ) -> None:
        view = AuctionBrowserView(self, member)
        embed = await self._build_auction_overview_embed(member)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=embed, view=view, ephemeral=True
            )
        with contextlib.suppress(discord.HTTPException):
            view.message = await interaction.original_response()

    async def _build_user_auction_embed(
        self, member: discord.abc.User
    ) -> discord.Embed:
        try:
            listings = await self.database.get_user_auctions(member.id, limit=15)
        except DatabaseError as exc:
            return embeds.error_embed(str(exc))

        if not listings:
            embed = embeds.info_embed(
                "Tu n'as aucune enchère active pour le moment.",
                title="⚖️ Mes enchères",
            )
        else:
            lines = [
                self._format_auction_line(record, include_status=True)
                for record in listings
            ]
            embed = embeds.info_embed("\n".join(lines), title="⚖️ Mes enchères")
        if getattr(member, "display_avatar", None):
            embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_footer(text="Crée une enchère avec e!auction pet/potion/ticket/enchant.")
        return embed

    async def _send_auction_creation_embed(
        self, ctx: commands.Context, listing_id: int
    ) -> None:
        embed = await self._build_auction_creation_embed(listing_id)
        await ctx.send(embed=embed)

    async def _build_auction_creation_embed(self, listing_id: int) -> discord.Embed:
        detailed = await self.database.get_auction_listing(listing_id)
        if detailed is None:
            return embeds.success_embed(
                "Enchère créée avec succès.", title="Nouvelle enchère"
            )
        line = self._format_auction_line(detailed)
        return embeds.success_embed(line, title="Enchère publiée")

    @staticmethod
    def _listing_sort_key(record: Mapping[str, object]) -> tuple[int, float]:
        price = int(record.get("price", 0))
        created_at = record.get("created_at")
        timestamp = 0.0
        if isinstance(created_at, datetime):
            timestamp = -created_at.timestamp()
        return price, timestamp

    @staticmethod
    def _recent_listing_sort(record: Mapping[str, object]) -> tuple[float, int]:
        created_at = record.get("created_at")
        timestamp = 0.0
        if isinstance(created_at, datetime):
            timestamp = -created_at.timestamp()
        price = int(record.get("price", 0))
        return timestamp, price

    async def _resolve_pet(
        self, user_id: int, raw: str
    ) -> tuple[Optional[Mapping[str, object]], Optional[str], Optional[str]]:
        slug, ordinal, variant_flags = self._parse_pet_query(raw)
        if not slug:
            return None, None, "Merci de préciser le nom du pet à mettre en vente."

        definition = self._definition_by_slug.get(slug)
        if definition is None:
            return None, None, "Ce pet est introuvable dans ton inventaire."

        is_gold, is_rainbow, is_shiny = self._variant_flags(variant_flags)
        rows = list(
            await self.database.get_user_pet_by_name(
                user_id,
                definition.name,
                is_gold=is_gold,
                is_rainbow=is_rainbow,
                is_shiny=is_shiny,
                include_on_market=True,
            )
        )
        if not rows:
            adjectives: list[str] = []
            if is_shiny:
                adjectives.append("shiny")
            if is_rainbow:
                adjectives.append("rainbow")
            elif is_gold:
                adjectives.append("doré")
            elif is_gold is False and is_rainbow is False:
                adjectives.append("classique")
            label = definition.name
            if adjectives:
                label += " (" + " ".join(adjectives) + ")"
            return None, None, f"Tu n'as aucun {label} disponible."

        if ordinal:
            index = ordinal - 1
            if index < 0 or index >= len(rows):
                return None, None, f"Impossible de trouver ce pet numéro {ordinal}."
        else:
            index = self._pick_preferred_listing_index(rows)

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

    def _resolve_potion_slug(self, raw: str) -> tuple[str | None, PotionDefinition | None]:
        candidate = raw.strip().lower()
        normalized = self._normalize_key(raw)
        for slug, definition in POTION_DEFINITION_MAP.items():
            aliases = {
                slug.lower(),
                definition.name.lower(),
                self._normalize_key(definition.name),
            }
            if candidate in aliases or normalized in aliases:
                return slug, definition
        return None, None

    def _get_sellable_roles(self, member: discord.Member | None) -> list[discord.Role]:
        roles: list[discord.Role] = []
        if member is None or member.guild is None:
            return roles

        for role_id in SELLABLE_ROLE_IDS:
            role = member.guild.get_role(role_id)
            if role is not None and role in member.roles:
                roles.append(role)
        return roles

    @staticmethod
    def _role_label(slug: str, guild: discord.Guild | None = None) -> str:
        if slug and slug.isdigit() and guild is not None:
            role = guild.get_role(int(slug))
            if role is not None:
                return role.mention
        return f"Rôle {slug}" if slug else "Rôle"

    async def _create_pet_listing_embed(
        self,
        user: discord.abc.User,
        price: int,
        pet_query: str,
    ) -> tuple[bool, discord.Embed]:
        if price <= 0:
            return False, embeds.error_embed("Le prix doit être supérieur à zéro.")

        record, display, error = await self._resolve_pet(user.id, pet_query)
        if record is None or display is None:
            return False, embeds.error_embed(error or "Ce pet est introuvable.")
        if bool(record.get("is_active")):
            return False, embeds.error_embed("Ce pet est actuellement équipé.")
        if bool(record.get("on_market")):
            return False, embeds.error_embed("Ce pet est déjà en vente sur ton stand.")

        user_pet_id = int(record["id"])
        try:
            listing = await self.database.create_market_listing(user.id, user_pet_id, price)
        except DatabaseError as exc:
            return False, embeds.error_embed(str(exc))

        listing_id = int(listing["id"])
        embed = embeds.success_embed(
            f"{display} est maintenant en vente pour {embeds.format_gems(price)} (annonce #{listing_id}).",
            title="Annonce créée",
        )
        return True, embed

    async def _create_ticket_listing_embed(
        self,
        user: discord.abc.User,
        quantity: int,
        price: int,
    ) -> tuple[bool, discord.Embed]:
        if quantity <= 0:
            return False, embeds.error_embed("Indique une quantité positive de tickets.")
        if price <= 0:
            return False, embeds.error_embed("Le prix doit être supérieur à zéro.")

        try:
            listing = await self.database.create_consumable_listing(
                user.id,
                item_type="ticket",
                quantity=quantity,
                price=price,
            )
        except DatabaseError as exc:
            return False, embeds.error_embed(str(exc))

        listing_id = int(listing["id"])
        embed = embeds.success_embed(
            f"🎟️ {quantity} ticket(s) mis en vente pour {embeds.format_gems(price)} (annonce #{listing_id}).",
            title="Annonce créée",
        )
        return True, embed

    async def _create_potion_listing_embed(
        self,
        user: discord.abc.User,
        raw_slug: str,
        quantity: int,
        price: int,
    ) -> tuple[bool, discord.Embed]:
        if quantity <= 0:
            return False, embeds.error_embed("Indique une quantité positive.")
        if price <= 0:
            return False, embeds.error_embed("Le prix doit être supérieur à zéro.")

        slug, definition = self._resolve_potion_slug(raw_slug)
        if slug is None or definition is None:
            return False, embeds.error_embed("Potion inconnue. Vérifie le nom ou le slug.")

        try:
            listing = await self.database.create_consumable_listing(
                user.id,
                item_type="potion",
                item_slug=slug,
                quantity=quantity,
                price=price,
            )
        except DatabaseError as exc:
            return False, embeds.error_embed(str(exc))

        listing_id = int(listing["id"])
        embed = embeds.success_embed(
            f"🧪 {definition.name} x{quantity} listé pour {embeds.format_gems(price)} (annonce #{listing_id}).",
            title="Annonce créée",
        )
        return True, embed

    async def _create_role_listing_embed(
        self,
        user: discord.abc.User,
        role: discord.Role,
        price: int,
    ) -> tuple[bool, discord.Embed]:
        if price <= 0:
            return False, embeds.error_embed("Le prix doit être supérieur à zéro.")
        if not isinstance(user, discord.Member):
            return False, embeds.error_embed("Impossible de vérifier tes rôles.")
        if role.id not in SELLABLE_ROLE_IDS:
            return False, embeds.error_embed("Ce rôle ne peut pas être vendu sur la plaza.")
        if role not in user.roles:
            return False, embeds.error_embed("Tu ne possèdes pas ce rôle.")
        if role.guild is None or role.guild != user.guild:
            return False, embeds.error_embed("Impossible de mettre ce rôle en vente ici.")
        if not role.is_assignable():
            return False, embeds.error_embed("Je n'ai pas la permission de gérer ce rôle.")

        try:
            await user.remove_roles(role, reason="Mise en vente du rôle sur la plaza")
        except (discord.Forbidden, discord.HTTPException):
            return False, embeds.error_embed("Impossible de retirer ce rôle pour le mettre en vente.")

        try:
            listing = await self.database.create_consumable_listing(
                user.id,
                item_type="role",
                item_slug=str(role.id),
                quantity=1,
                price=price,
            )
        except DatabaseError as exc:
            with contextlib.suppress(discord.HTTPException, discord.Forbidden):
                await user.add_roles(role, reason="Annulation de la mise en vente du rôle")
            return False, embeds.error_embed(str(exc))

        listing_id = int(listing["id"])
        embed = embeds.success_embed(
            f"{role.mention} listé pour {embeds.format_gems(price)} (annonce #{listing_id}).",
            title="Annonce créée",
        )
        return True, embed

    async def _complete_consumable_purchase(
        self,
        buyer: discord.abc.User,
        listing_id: int,
        *,
        guild: discord.Guild | None = None,
    ) -> tuple[bool, discord.Embed, Optional[int]]:
        try:
            consumable = await self.database.get_consumable_listing(listing_id)
        except DatabaseError as exc:
            return False, embeds.error_embed(str(exc)), None

        if consumable is None:
            return (
                False,
                embeds.error_embed("Cette annonce n'existe pas ou n'est plus active."),
                None,
            )

        seller_id = int(consumable.get("seller_id", 0))
        if seller_id == buyer.id:
            return False, embeds.error_embed("Tu ne peux pas acheter ta propre annonce."), None

        try:
            result = await self.database.purchase_consumable_listing(
                listing_id, buyer.id
            )
        except InsufficientBalanceError as exc:
            return False, embeds.error_embed(str(exc)), None
        except DatabaseError as exc:
            return False, embeds.error_embed(str(exc)), None

        listing_record = result["listing"]
        price = int(listing_record.get("price", 0))
        quantity = int(listing_record.get("quantity", 0))
        item_type = str(listing_record.get("item_type", ""))
        slug = str(listing_record.get("item_slug", ""))
        if item_type == "ticket":
            item_label = f"🎟️ Tickets ×{quantity}"
        elif item_type == "role":
            item_label = f"🛡️ {self._role_label(slug, guild)}"
        else:
            definition = POTION_DEFINITION_MAP.get(slug)
            name = definition.name if definition else slug or "Potion"
            item_label = f"🧪 {name} ×{quantity}"

        seller = None
        if guild is not None:
            seller = guild.get_member(seller_id)
        if seller is None:
            seller = self.bot.get_user(seller_id)
        seller_name = (
            getattr(seller, "mention", seller.name)
            if seller is not None
            else f"Utilisateur {seller_id}"
        )

        buyer_before = int(result.get("buyer_before", 0))
        lines = [
            f"Achat : {item_label}",
            f"Prix : {embeds.format_gems(price)}",
            f"Vendeur : {seller_name}",
            f"Tes gemmes avant achat : {embeds.format_gems(buyer_before)}",
        ]
        embed = embeds.success_embed("\n".join(lines), title="Achat confirmé")
        if item_type == "role":
            role_assignment_error = False
            if guild is not None and isinstance(buyer, discord.Member) and slug.isdigit():
                role = guild.get_role(int(slug))
                if role is not None:
                    try:
                        await buyer.add_roles(role, reason="Achat de rôle sur la plaza")
                    except (discord.Forbidden, discord.HTTPException):
                        role_assignment_error = True
                else:
                    role_assignment_error = True
            else:
                role_assignment_error = True

            if role_assignment_error:
                embed.set_footer(
                    text="Impossible d'attribuer le rôle automatiquement. Contacte un administrateur."
                )
        seller_id = int(listing_record.get("seller_id", seller_id))
        return True, embed, seller_id

    async def _cancel_listing_embed(
        self, user: discord.abc.User, listing_id: int
    ) -> tuple[bool, discord.Embed]:
        market_listing = await self.database.get_market_listing(listing_id)
        if market_listing is not None:
            try:
                await self.database.cancel_market_listing(listing_id, user.id)
            except DatabaseError as exc:
                return False, embeds.error_embed(str(exc))
            name = self._format_pet_record(market_listing)
            embed = embeds.success_embed(
                f"Annonce #{listing_id} retirée ({name}).", title="Annonce retirée"
            )
            return True, embed

        consumable = await self.database.get_consumable_listing(listing_id)
        if consumable is not None:
            try:
                await self.database.cancel_consumable_listing(listing_id, user.id)
            except DatabaseError as exc:
                return False, embeds.error_embed(str(exc))

            item_type = str(consumable.get("item_type", ""))
            quantity = int(consumable.get("quantity", 0))
            if item_type == "ticket":
                label = f"{quantity} ticket(s)"
            elif item_type == "role":
                slug = str(consumable.get("item_slug", ""))
                if isinstance(user, discord.Member) and user.guild is not None and slug.isdigit():
                    role = user.guild.get_role(int(slug))
                    if role is not None:
                        with contextlib.suppress(discord.HTTPException, discord.Forbidden):
                            await user.add_roles(role, reason="Annonce de rôle annulée")
                label = self._role_label(slug, getattr(user, "guild", None))
            else:
                slug = str(consumable.get("item_slug", ""))
                definition = POTION_DEFINITION_MAP.get(slug)
                potion_name = definition.name if definition else slug
                label = f"{potion_name} x{quantity}"
            embed = embeds.success_embed(
                f"Annonce #{listing_id} retirée ({label}).", title="Annonce retirée"
            )
            return True, embed

        return False, embeds.error_embed("Annonce introuvable.")

    async def _build_stand_overview_embed(
        self, user: discord.abc.User, *, include_instructions: bool = False
    ) -> tuple[discord.Embed, bool]:
        try:
            listings = await self.database.list_active_market_listings(
                limit=25, seller_id=user.id
            )
        except DatabaseError as exc:
            return embeds.error_embed(str(exc)), False

        title = f"Stand de {user.display_name}"
        if not listings:
            message = "Aucune annonce active sur ce stand pour le moment."
            if include_instructions:
                message += " Utilise les boutons pour créer ta première offre !"
            embed = embeds.info_embed(message, title=title)
            if getattr(user, "display_avatar", None):
                embed.set_thumbnail(url=user.display_avatar.url)
            return embed, True

        lines = [self._format_listing_line(record) for record in listings]
        embed = embeds.info_embed("\n".join(lines), title=title)
        if getattr(user, "display_avatar", None):
            embed.set_thumbnail(url=user.display_avatar.url)
        if include_instructions:
            embed.set_footer(text="Astuce : utilise les boutons ci-dessous pour gérer ton stand.")
        return embed, True

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------
    @commands.group(name="stand", invoke_without_command=True)
    async def stand(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        target = member or ctx.author
        embed, ok = await self._build_stand_overview_embed(
            target, include_instructions=target.id == ctx.author.id
        )
        view: StandManagementView | None = None
        if ok and target.id == ctx.author.id:
            view = StandManagementView(self, ctx.author)
        message = await ctx.send(embed=embed, view=view)
        if view is not None:
            view.message = message
            self._register_stand_view(view)

    @stand.command(name="add")
    async def stand_add(self, ctx: commands.Context, price: int, *, pet: str) -> None:
        success, embed = await self._create_pet_listing_embed(ctx.author, price, pet)
        await ctx.send(embed=embed)
        if success:
            await self._refresh_active_stand_view(ctx.author.id)

    @stand.command(name="remove")
    async def stand_remove(self, ctx: commands.Context, listing_id: int) -> None:
        success, embed = await self._cancel_listing_embed(ctx.author, listing_id)
        await ctx.send(embed=embed)
        if success:
            await self._refresh_active_stand_view(ctx.author.id)

    @stand.command(name="buy")
    async def stand_buy(self, ctx: commands.Context, listing_id: int) -> None:
        try:
            listing = await self.database.get_market_listing(listing_id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        if listing is None:
            success, embed, seller_id = await self._complete_consumable_purchase(
                ctx.author, listing_id, guild=ctx.guild
            )
            await ctx.send(embed=embed)
            if success and seller_id is not None:
                await self._refresh_active_stand_view(seller_id)
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
        seller_name = getattr(seller, "mention", seller.name) if seller else f"Utilisateur {seller_id}"

        pet_display = self._format_pet_record(listing)
        embed = embeds.success_embed(
            f"Tu as acheté {pet_display} pour {embeds.format_gems(price)} à {seller_name}.",
            title="Achat confirmé",
        )
        await ctx.send(embed=embed)

    @commands.group(name="auction", invoke_without_command=True)
    async def auction_group(self, ctx: commands.Context) -> None:
        embed = await self._build_user_auction_embed(ctx.author)
        await ctx.send(embed=embed)

    @auction_group.command(name="pet")
    async def auction_pet(
        self,
        ctx: commands.Context,
        starting_bid: int,
        duration_minutes: int,
        buyout: int | None = None,
        *,
        pet: str,
    ) -> None:
        record, display, error = await self._resolve_pet(ctx.author.id, pet)
        if error:
            await ctx.send(embed=embeds.error_embed(error))
            return
        if record is None:
            await ctx.send(embed=embeds.error_embed("Pet introuvable."))
            return
        user_pet_id = int(record.get("id") or 0)
        try:
            listing = await self.database.create_pet_auction(
                ctx.author.id,
                user_pet_id,
                starting_bid=starting_bid,
                duration_minutes=duration_minutes,
                buyout_price=buyout,
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        await self._send_auction_creation_embed(ctx, int(listing["id"]))

    @auction_group.command(name="ticket")
    async def auction_ticket(
        self,
        ctx: commands.Context,
        quantity: int,
        starting_bid: int,
        duration_minutes: int,
        buyout: int | None = None,
    ) -> None:
        if quantity <= 0:
            await ctx.send(embed=embeds.error_embed("La quantité doit être positive."))
            return
        try:
            listing = await self.database.create_item_auction(
                ctx.author.id,
                item_type="ticket",
                item_slug="raffle_ticket",
                quantity=quantity,
                starting_bid=starting_bid,
                duration_minutes=duration_minutes,
                buyout_price=buyout,
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        await self._send_auction_creation_embed(ctx, int(listing["id"]))

    @auction_group.command(name="potion")
    async def auction_potion(
        self,
        ctx: commands.Context,
        slug: str,
        quantity: int,
        starting_bid: int,
        duration_minutes: int,
        buyout: int | None = None,
    ) -> None:
        if quantity <= 0:
            await ctx.send(embed=embeds.error_embed("La quantité doit être positive."))
            return
        try:
            listing = await self.database.create_item_auction(
                ctx.author.id,
                item_type="potion",
                item_slug=slug.lower(),
                quantity=quantity,
                starting_bid=starting_bid,
                duration_minutes=duration_minutes,
                buyout_price=buyout,
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        await self._send_auction_creation_embed(ctx, int(listing["id"]))

    @auction_group.command(name="enchant", aliases=("enchantment", "enchantement"))
    async def auction_enchantment(
        self,
        ctx: commands.Context,
        slug: str,
        power: int,
        starting_bid: int,
        duration_minutes: int,
        buyout: int | None = None,
    ) -> None:
        if slug not in ENCHANTMENT_DEFINITION_MAP:
            await ctx.send(embed=embeds.error_embed("Cet enchantement est inconnu."))
            return
        if power < 1 or power > 10:
            await ctx.send(
                embed=embeds.error_embed("Le niveau doit être compris entre 1 et 10.")
            )
            return
        try:
            listing = await self.database.create_item_auction(
                ctx.author.id,
                item_type="enchantment",
                item_slug=slug,
                enchantment_power=power,
                quantity=1,
                starting_bid=starting_bid,
                duration_minutes=duration_minutes,
                buyout_price=buyout,
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        await self._send_auction_creation_embed(ctx, int(listing["id"]))

    @auction_group.command(name="bid")
    async def auction_bid(
        self, ctx: commands.Context, auction_id: int, amount: int
    ) -> None:
        try:
            await self.database.place_auction_bid(auction_id, ctx.author.id, amount)
        except InsufficientBalanceError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        detailed = await self.database.get_auction_listing(auction_id)
        if detailed is None:
            await ctx.send(
                embed=embeds.success_embed(
                    "Ton offre a été enregistrée.", title="Enchère mise à jour"
                )
            )
            return
        status = str(detailed.get("status", "active"))
        if status == "sold":
            summary = "Tu remportes immédiatement cette enchère !"
        else:
            summary = "Tu es désormais l'enchérisseur principal."
        line = self._format_auction_line(detailed)
        embed = embeds.success_embed(
            f"{line}\n{summary}",
            title="Offre enregistrée",
        )
        await ctx.send(embed=embed)

    @auction_group.command(name="cancel")
    async def auction_cancel(self, ctx: commands.Context, auction_id: int) -> None:
        try:
            listing = await self.database.cancel_auction(auction_id, ctx.author.id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        line = self._format_auction_line(listing, include_status=True)
        await ctx.send(
            embed=embeds.success_embed(
                f"{line}\nTes objets ont été restitués.", title="Enchère annulée"
            )
        )

    @auction_group.command(name="list")
    async def auction_list(self, ctx: commands.Context) -> None:
        embed = await self._build_auction_overview_embed(ctx.author)
        await ctx.send(embed=embed)
        seller_user: Optional[discord.abc.User]
        if seller is not None:
            seller_user = seller
        else:
            try:
                seller_user = await self.bot.fetch_user(seller_id)
            except discord.HTTPException:
                seller_user = None
        await self._refresh_active_stand_view(seller_id)

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
            price = embeds.format_gems(int(row["price"]))
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
            pet_listings = await self.database.list_active_market_listings(limit=30)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            pet_listings = []

        try:
            consumable_listings = await self.database.list_active_consumable_listings(limit=30)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            consumable_listings = []

        if not pet_listings and not consumable_listings:
            await ctx.send(
                embed=embeds.info_embed(
                    "La plaza est calme pour le moment. Reviens plus tard !",
                    title="🏬 Plaza des stands",
                )
            )
            return

        if pet_listings:
            grouped: Dict[int, list[Mapping[str, object]]] = defaultdict(list)
            for record in pet_listings:
                grouped[int(record["seller_id"])].append(record)

            user_cache = await self._build_user_cache(ctx, grouped.keys())

            seller_sections: list[SellerListings] = []
            for seller_id, records in grouped.items():
                seller = user_cache.get(seller_id)
                seller_name = seller.display_name if seller else f"Utilisateur {seller_id}"

                sorted_records = sorted(records, key=self._listing_sort_key)
                lines = [self._format_listing_line(record) for record in sorted_records]

                prices = [int(record.get("price", 0)) for record in sorted_records]
                cheapest = min(prices) if prices else 0
                priciest = max(prices) if prices else 0

                latest_at: Optional[datetime] = None
                for record in records:
                    created_at = record.get("created_at")
                    if isinstance(created_at, datetime) and (
                        latest_at is None or created_at > latest_at
                    ):
                        latest_at = created_at

                seller_sections.append(
                    SellerListings(
                        seller_id=seller_id,
                        seller_name=seller_name,
                        listings=tuple(lines),
                        total=len(records),
                        cheapest=cheapest,
                        priciest=priciest,
                        latest_at=latest_at,
                    )
                )

            seller_sections.sort(key=lambda entry: entry.seller_name.lower())

            total_sellers = len(seller_sections)
            visible_sellers = seller_sections[:24]
            hidden_count = total_sellers - len(visible_sellers)

            recent_lines = [
                self._format_listing_line(record)
                for record in sorted(pet_listings, key=self._recent_listing_sort)
            ][:10]

            view = PlazaListingsView(
                plaza=self,
                author=ctx.author,
                sellers=visible_sellers,
                recent_lines=recent_lines,
                total_listings=len(pet_listings),
                total_sellers=total_sellers,
                hidden_count=hidden_count,
            )

            message = await ctx.send(embed=view.get_embed("all"), view=view)
            view.message = message
        else:
            await ctx.send(
                embed=embeds.info_embed(
                    "Aucune annonce de pets active pour le moment.",
                    title="🏬 Plaza des stands",
                )
            )

        if consumable_listings:
            seller_ids = {int(record.get("seller_id", 0)) for record in consumable_listings}
            user_cache = await self._build_user_cache(ctx, seller_ids)
            conso_view = ConsumableListingsView(
                self, ctx.author, consumable_listings, user_cache
            )
            conso_message = await ctx.send(
                embed=conso_view.get_embed("all"), view=conso_view
            )
            conso_view.message = conso_message


class AuctionBrowserView(discord.ui.View):
    """Vue permettant de rafraîchir la liste des enchères actives."""

    def __init__(self, plaza: "Plaza", author: discord.abc.User) -> None:
        super().__init__(timeout=120)
        self.plaza = plaza
        self.author = author
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="Faire une offre", style=discord.ButtonStyle.primary)
    async def bid_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        del button
        await interaction.response.send_modal(AuctionBidModal(self.plaza, self.author))

    @discord.ui.button(label="Créer une enchère", style=discord.ButtonStyle.success)
    async def create_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        del button
        view = AuctionCreationView(self.plaza, self.author)
        embed = embeds.info_embed(
            "Sélectionne le type d'objet pour ouvrir le formulaire correspondant.",
            title="Créer une enchère",
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        with contextlib.suppress(discord.HTTPException):
            view.message = await interaction.original_response()

    async def refresh(self, interaction: discord.Interaction | None = None) -> None:
        embed = await self.plaza._build_auction_overview_embed(self.author)
        if interaction is not None:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            await self.message.edit(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Seul l'initiateur de la plaza peut actualiser ces enchères.",
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

    @discord.ui.button(label="Actualiser", style=discord.ButtonStyle.primary)
    async def refresh_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        del button
        await self.refresh(interaction)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.secondary)
    async def close_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        del button
        for child in self.children:
            child.disabled = True
        self.stop()
        if interaction.response.is_done():
            if self.message is not None:
                await self.message.edit(view=self)
        else:
            await interaction.response.edit_message(view=self)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Plaza(bot))
