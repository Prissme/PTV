"""Système d'échange interactif complet pour EcoBot."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

import discord
from discord.ext import commands

from database.db import DatabaseError
from utils import embeds
from config import PET_DEFINITIONS, PetDefinition

logger = logging.getLogger(__name__)


@dataclass
class TradePetState:
    """Représente un pet proposé dans un échange."""

    trade_pet_id: int
    user_pet_id: int
    name: str
    rarity: str
    is_huge: bool
    is_gold: bool
    is_rainbow: bool
    from_user_id: int
    to_user_id: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "trade_pet_id": self.trade_pet_id,
            "user_pet_id": self.user_pet_id,
            "name": self.name,
            "rarity": self.rarity,
            "is_huge": self.is_huge,
            "is_gold": self.is_gold,
            "is_rainbow": self.is_rainbow,
        }


@dataclass
class TradeOfferState:
    """État courant de l'offre d'un utilisateur."""

    pb: int = 0
    pets: Dict[int, TradePetState] = field(default_factory=dict)
    accepted: bool = False

    def to_embed(self) -> dict[str, object]:
        pets = sorted(self.pets.values(), key=lambda pet: pet.user_pet_id)
        return {
            "pb": self.pb,
            "pets": [pet.to_mapping() for pet in pets],
            "accepted": self.accepted,
        }


class TradePetSelectionView(discord.ui.View):
    """Vue éphémère permettant de choisir un pet lorsqu'il y a ambiguïté."""

    def __init__(
        self,
        user_id: int,
        candidates: Sequence[Mapping[str, Any]],
        *,
        label_builder: Callable[[int, Mapping[str, Any]], str],
    ) -> None:
        super().__init__(timeout=60)
        self.user_id = user_id
        self.candidates: List[Mapping[str, Any]] = [dict(candidate) for candidate in candidates]
        self._label_builder = label_builder
        self.selection: Optional[Mapping[str, Any]] = None
        self.cancelled = False
        self.message: Optional[discord.Message] = None

        for index, candidate in enumerate(self.candidates, start=1):
            label = self._label_builder(index, candidate)
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
            button.callback = self._make_callback(index - 1)
            self.add_item(button)

        cancel_button = discord.ui.Button(label="Annuler", style=discord.ButtonStyle.secondary)
        cancel_button.callback = self._cancel
        self.add_item(cancel_button)

    def _make_callback(self, index: int):
        async def _callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "Tu ne peux pas sélectionner un pet pour un autre joueur.",
                    ephemeral=True,
                )
                return
            self.selection = self.candidates[index]
            self._disable_all()
            if interaction.response.is_done():
                with contextlib.suppress(discord.HTTPException):
                    await interaction.followup.edit_message(interaction.message.id, view=self)
            else:
                with contextlib.suppress(discord.HTTPException):
                    await interaction.response.edit_message(view=self)
            self.stop()

        return _callback

    def _disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Tu ne peux pas annuler cette sélection.", ephemeral=True
            )
            return
        self.cancelled = True
        self.selection = None
        self._disable_all()
        if interaction.response.is_done():
            with contextlib.suppress(discord.HTTPException):
                await interaction.followup.edit_message(interaction.message.id, view=self)
        else:
            with contextlib.suppress(discord.HTTPException):
                await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self) -> None:  # pragma: no cover - callback Discord
        self._disable_all()
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)

@dataclass
class TradeSession:
    """Modèle en mémoire décrivant un échange en cours."""

    trade_id: int
    user_a: discord.abc.User
    user_b: discord.abc.User
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    offers: Dict[int, TradeOfferState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.offers.setdefault(self.user_a.id, TradeOfferState())
        self.offers.setdefault(self.user_b.id, TradeOfferState())

    def participants(self) -> Sequence[int]:
        return (self.user_a.id, self.user_b.id)

    def other_user_id(self, user_id: int) -> int:
        if user_id == self.user_a.id:
            return self.user_b.id
        if user_id == self.user_b.id:
            return self.user_a.id
        raise ValueError("Utilisateur inconnu dans cette session")

    def reset_acceptances(self) -> None:
        for offer in self.offers.values():
            offer.accepted = False

    def mark_accepted(self, user_id: int, accepted: bool) -> None:
        offer = self.offers[user_id]
        offer.accepted = accepted

    def all_accepted(self) -> bool:
        return all(offer.accepted for offer in self.offers.values())

    def offer_mapping(self, user_id: int) -> dict[str, object]:
        return self.offers[user_id].to_embed()


class TradeView(discord.ui.View):
    """Vue interactive contrôlant un échange."""

    def __init__(self, cog: "Trade", session: TradeSession) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.session = session
        self.message: Optional[discord.Message] = None
        self.lock = asyncio.Lock()
        self._started_at = datetime.now(timezone.utc)
        self.countdown_task: Optional[asyncio.Task] = None
        self.countdown_button: Optional[CancelCountdownButton] = None

        self.add_pet_button = discord.ui.Button(emoji="➕", label="Ajouter Pet", style=discord.ButtonStyle.primary)
        self.add_pet_button.callback = self._add_pet
        self.add_item(self.add_pet_button)

        self.add_pb_button = discord.ui.Button(emoji="💰", label="Ajouter PB", style=discord.ButtonStyle.secondary)
        self.add_pb_button.callback = self._add_pb
        self.add_item(self.add_pb_button)

        self.remove_button = discord.ui.Button(emoji="➖", label="Retirer", style=discord.ButtonStyle.secondary)
        self.remove_button.callback = self._remove_item
        self.add_item(self.remove_button)

        self.accept_button = discord.ui.Button(
            emoji="✅", label="Accepter", style=discord.ButtonStyle.success
        )
        self.accept_button.callback = self._toggle_ready
        self.add_item(self.accept_button)

        self.cancel_button = discord.ui.Button(emoji="❌", label="Annuler", style=discord.ButtonStyle.danger)
        self.cancel_button.callback = self._cancel_trade
        self.add_item(self.cancel_button)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _expires_in(self) -> Optional[float]:
        if self.timeout is None:
            return None
        elapsed = (datetime.now(timezone.utc) - self._started_at).total_seconds()
        return max(0.0, float(self.timeout) - elapsed)

    def _update_components(self) -> None:
        countdown_active = self.countdown_task is not None and not self.countdown_task.done()
        self.add_pet_button.disabled = countdown_active
        self.add_pb_button.disabled = countdown_active
        self.remove_button.disabled = countdown_active
        self.accept_button.disabled = countdown_active
        if self.countdown_button is not None:
            self.countdown_button.disabled = False

    def _build_embed(self) -> discord.Embed:
        return embeds.trade_embed(
            trade_id=self.session.trade_id,
            user_a=self.session.user_a,
            user_b=self.session.user_b,
            offer_a=self.session.offer_mapping(self.session.user_a.id),
            offer_b=self.session.offer_mapping(self.session.user_b.id),
            expires_in=self._expires_in(),
        )

    async def refresh_message(self) -> None:
        if self.message is None:
            return
        self._sync_countdown_button()
        self._update_components()
        embed = (
            self._build_countdown_embed()
            if self.countdown_task is not None and not self.countdown_task.done()
            else self._build_embed()
        )
        await self.message.edit(embed=embed, view=self)

    def _sync_countdown_button(self) -> None:
        countdown_active = self.countdown_task is not None and not self.countdown_task.done()
        if countdown_active and self.countdown_button is None:
            self.countdown_button = CancelCountdownButton(self)
            self.add_item(self.countdown_button)
        elif not countdown_active and self.countdown_button is not None:
            self.remove_item(self.countdown_button)
            self.countdown_button = None

    async def _cancel_countdown(self) -> None:
        task = self.countdown_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.countdown_task = None
        self._sync_countdown_button()
        await self.refresh_message()

    async def _begin_countdown(self) -> None:
        if self.countdown_task is not None and not self.countdown_task.done():
            return
        self.countdown_task = asyncio.create_task(self._countdown_worker())
        self._sync_countdown_button()
        await self.refresh_message()

    async def _countdown_worker(self) -> None:
        try:
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            return
        self.countdown_task = None
        await self.cog.complete_trade(self)

    def _build_countdown_embed(self) -> discord.Embed:
        lines = ["⏳ Trade en cours de finalisation..."]
        summary_a = self._offer_summary(self.session.user_a.id)
        summary_b = self._offer_summary(self.session.user_b.id)
        lines.append(f"{self.session.user_a.display_name} donne : {summary_a}")
        lines.append(f"{self.session.user_b.display_name} donne : {summary_b}")
        lines.append("⚠️ Dernière chance d'annuler ! (3s)")
        embed = embeds.info_embed("\n".join(lines), title="Échange validé")
        return embed

    def _offer_summary(self, user_id: int) -> str:
        offer = self.session.offers[user_id]
        parts: list[str] = []
        if offer.pb:
            parts.append(embeds.format_currency(offer.pb))
        for pet in sorted(
            offer.pets.values(),
            key=lambda item: (
                self.cog._normalize_pet_key(item.name),
                item.user_pet_id,
            ),
        ):
            parts.append(self.cog._format_pet_state_display(pet))
        return ", ".join(parts) if parts else "Rien"

    async def _handle_countdown_cancel(self, interaction: discord.Interaction) -> None:
        async with self.lock:
            if self.countdown_task is None or self.countdown_task.done():
                await self._send_ephemeral(interaction, "Aucun compte à rebours en cours.")
                return
            self.session.reset_acceptances()
            self.reset_timeout()
        await self._cancel_countdown()
        await self._send_ephemeral(interaction, "Le compte à rebours est annulé.")

    async def _send_ephemeral(self, interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def on_timeout(self) -> None:  # pragma: no cover - callback Discord
        await self.cog.timeout_trade(self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in self.session.participants():
            await interaction.response.send_message(
                "Tu n'es pas participant de cet échange.", ephemeral=True, delete_after=5
            )
            return False
        return True

    def disable(self) -> None:
        for item in self.children:
            item.disabled = True

    # ------------------------------------------------------------------
    # Button callbacks
    # ------------------------------------------------------------------
    async def _add_pet(self, interaction: discord.Interaction) -> None:
        modal = AddPetModal(self, interaction.user.id)
        await interaction.response.send_modal(modal)

    async def _add_pb(self, interaction: discord.Interaction) -> None:
        modal = AddPBModal(self, interaction.user.id)
        await interaction.response.send_modal(modal)

    async def _remove_item(self, interaction: discord.Interaction) -> None:
        modal = RemoveItemModal(self, interaction.user.id)
        await interaction.response.send_modal(modal)

    async def _toggle_ready(self, interaction: discord.Interaction) -> None:
        cancel_countdown = False
        start_countdown = False
        async with self.lock:
            offer = self.session.offers[interaction.user.id]
            new_state = not offer.accepted
            self.session.mark_accepted(interaction.user.id, new_state)
            if new_state:
                message = "Tu as accepté l'échange."
                if self.session.all_accepted():
                    start_countdown = True
            else:
                message = "Tu as retiré ton acceptation."
                cancel_countdown = True
            if not self.session.all_accepted():
                cancel_countdown = True
            self.reset_timeout()

        if cancel_countdown:
            await self._cancel_countdown()
        if start_countdown:
            await self._begin_countdown()
        elif not cancel_countdown:
            await self.refresh_message()

        await self._send_ephemeral(interaction, message)

    async def _cancel_trade(self, interaction: discord.Interaction) -> None:
        await self.cog.cancel_trade(self, interaction, reason="Échange annulé par un participant.")

    # ------------------------------------------------------------------
    # Actions depuis les modals
    # ------------------------------------------------------------------
    async def process_add_pets(
        self, interaction: discord.Interaction, pet_queries: Iterable[str]
    ) -> None:
        queries = [query.strip() for query in pet_queries if query and query.strip()]
        if not queries:
            if interaction.response.is_done():
                await interaction.followup.send("Aucun pet à ajouter.", ephemeral=True)
            else:
                await interaction.response.send_message("Aucun pet à ajouter.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        resolved: List[tuple[Mapping[str, Any], str]] = []
        errors: List[str] = []

        for raw_query in queries:
            record, display, error = await self.cog.resolve_pet_for_trade(interaction, raw_query)
            if record is None:
                if error:
                    errors.append(error)
                continue
            resolved.append((record, display))

        added: List[str] = []
        async with self.lock:
            offer = self.session.offers[interaction.user.id]
            other_id = self.session.other_user_id(interaction.user.id)
            for record, display in resolved:
                user_pet_id = int(record.get("id") or 0)
                if user_pet_id <= 0:
                    errors.append(f"{display} est introuvable.")
                    continue
                if bool(record.get("is_active")):
                    errors.append(
                        f"{display} est équipé. Déséquipe-le avant de l'échanger."
                    )
                    continue
                try:
                    trade_pet = await self.cog.database.add_trade_pet(
                        self.session.trade_id,
                        user_pet_id,
                        interaction.user.id,
                        other_id,
                    )
                except DatabaseError as exc:
                    errors.append(f"{display} : {exc}")
                    continue
                pet_state = TradePetState(
                    trade_pet_id=int(trade_pet["id"]),
                    user_pet_id=user_pet_id,
                    name=str(record.get("name", "Pet")),
                    rarity=str(record.get("rarity", "?")),
                    is_huge=bool(record.get("is_huge", False)),
                    is_gold=bool(record.get("is_gold", False)),
                    is_rainbow=bool(record.get("is_rainbow", False)),
                    from_user_id=interaction.user.id,
                    to_user_id=other_id,
                )
                offer.pets[pet_state.trade_pet_id] = pet_state
                added.append(display)
            if added:
                self.session.reset_acceptances()
                self.reset_timeout()

        if added:
            await self._cancel_countdown()
        else:
            await self.refresh_message()

        summary: List[str] = []
        if added:
            summary.append(f"Pets ajoutés : {', '.join(added)}")
        if errors:
            summary.extend(errors)
        if not summary:
            summary.append("Aucun pet n'a pu être ajouté.")

        await interaction.followup.send("\n".join(summary), ephemeral=True)

    async def process_update_pb(self, interaction: discord.Interaction, amount: int) -> None:
        if amount < 0:
            await self._send_ephemeral(interaction, "Le montant doit être positif ou nul.")
            return
        balance = await self.cog.database.fetch_balance(interaction.user.id)
        if amount > balance:
            await self._send_ephemeral(
                interaction,
                f"Tu n'as pas assez de PB. Solde actuel : {embeds.format_currency(balance)}.",
            )
            return
        async with self.lock:
            try:
                await self.cog.database.update_trade_pb(self.session.trade_id, interaction.user.id, amount)
            except DatabaseError as exc:
                await self._send_ephemeral(interaction, str(exc))
                return
            offer = self.session.offers[interaction.user.id]
            offer.pb = amount
            self.session.reset_acceptances()
            self.reset_timeout()
        await self._cancel_countdown()
        await self._send_ephemeral(
            interaction,
            f"Tu proposes désormais {embeds.format_currency(amount)} dans l'échange.",
        )

    async def process_remove(self, interaction: discord.Interaction, entry: str) -> None:
        raw_entry = entry.strip()
        if not raw_entry:
            await self._send_ephemeral(
                interaction, "Indique `pb` ou le nom d'un pet à retirer."
            )
            return

        lowered = raw_entry.lower()
        async with self.lock:
            if lowered in {"pb", "money", "solde"}:
                try:
                    await self.cog.database.update_trade_pb(
                        self.session.trade_id, interaction.user.id, 0
                    )
                except DatabaseError as exc:
                    await self._send_ephemeral(interaction, str(exc))
                    return
                self.session.offers[interaction.user.id].pb = 0
                self.session.reset_acceptances()
                self.reset_timeout()
                should_cancel = True
            else:
                should_cancel = False
                offer = self.session.offers[interaction.user.id]
                target: Optional[TradePetState] = None
                error_message: Optional[str] = None

                if lowered.isdigit():
                    user_pet_id = int(lowered)
                    for pet_state in offer.pets.values():
                        if pet_state.user_pet_id == user_pet_id:
                            target = pet_state
                            break
                    if target is None:
                        error_message = (
                            f"Aucun pet avec l'identifiant {user_pet_id} dans ton offre."
                        )
                else:
                    slug, ordinal, variant = self.cog._parse_pet_query(raw_entry)
                    if not slug:
                        error_message = f"{raw_entry} n'est pas dans ton offre."
                    else:
                        matches = [
                            pet
                            for pet in offer.pets.values()
                            if self.cog._matches_pet_state(pet, slug, variant)
                        ]
                        if not matches:
                            variant_label = self.cog._format_variant_label_from_slug(slug, variant)
                            error_message = f"{variant_label} n'est pas dans ton offre."
                        else:
                            matches.sort(
                                key=lambda pet: (
                                    self.cog._normalize_pet_key(pet.name),
                                    pet.trade_pet_id,
                                )
                            )
                            index = (ordinal - 1) if ordinal else 0
                            if index < 0 or index >= len(matches):
                                error_message = (
                                    f"Tu as seulement {len(matches)} exemplaire(s) correspondant(s) dans l'offre."
                                )
                            else:
                                target = matches[index]
            if lowered in {"pb", "money", "solde"}:
                removed_display = None
            else:
                if target is None:
                    await self._send_ephemeral(
                        interaction, error_message or f"{raw_entry} n'est pas dans ton offre."
                    )
                    return

                try:
                    removed = await self.cog.database.remove_trade_pet(
                        self.session.trade_id, target.user_pet_id, interaction.user.id
                    )
                except DatabaseError as exc:
                    await self._send_ephemeral(interaction, str(exc))
                    return

                if not removed:
                    await self._send_ephemeral(
                        interaction, "Impossible de retirer ce pet de l'échange."
                    )
                    return

                offer.pets.pop(target.trade_pet_id, None)
                removed_display = self.cog._format_pet_state_display(target)
                self.session.reset_acceptances()
                self.reset_timeout()
                should_cancel = True

        if lowered in {"pb", "money", "solde"}:
            await self._cancel_countdown()
            await self._send_ephemeral(interaction, "Ton offre de PB a été retirée.")
            return

        if should_cancel:
            await self._cancel_countdown()
        await self._send_ephemeral(
            interaction, f"{removed_display} retiré de l'offre."
        )


class CancelCountdownButton(discord.ui.Button):
    def __init__(self, view: TradeView) -> None:
        super().__init__(emoji="❌", label="ANNULER", style=discord.ButtonStyle.danger)
        self.view_instance = view

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await self.view_instance._handle_countdown_cancel(interaction)


class AddPetModal(discord.ui.Modal):
    """Modal de saisie pour ajouter des pets."""

    def __init__(self, view: TradeView, user_id: int) -> None:
        super().__init__(title="Ajouter des pets")
        self.view = view
        self.user_id = user_id
        self.pet_names = discord.ui.TextInput(
            label="Pets à ajouter",
            placeholder="Exemple : Shelly, Rosa gold, Barley 2",
            min_length=1,
            max_length=200,
            style=discord.TextStyle.long,
        )
        self.add_item(self.pet_names)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_value = self.pet_names.value
        entries = [chunk.strip() for chunk in raw_value.replace("\n", ",").split(",") if chunk.strip()]
        if not entries:
            await interaction.response.send_message("Aucun nom de pet valide fourni.", ephemeral=True)
            return
        await self.view.process_add_pets(interaction, entries)


class AddPBModal(discord.ui.Modal):
    """Modal de saisie pour proposer un montant de PB."""

    def __init__(self, view: TradeView, user_id: int) -> None:
        super().__init__(title="Ajouter des PB")
        self.view = view
        self.user_id = user_id
        self.amount = discord.ui.TextInput(label="Montant", placeholder="Ex: 500", min_length=1, max_length=10)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = self.amount.value.strip().replace(" ", "")
        if not value.isdigit():
            await interaction.response.send_message("Entre un nombre entier positif.", ephemeral=True)
            return
        await self.view.process_update_pb(interaction, int(value))


class RemoveItemModal(discord.ui.Modal):
    """Modal pour retirer un élément de l'offre."""

    def __init__(self, view: TradeView, user_id: int) -> None:
        super().__init__(title="Retirer un élément")
        self.view = view
        self.user_id = user_id
        self.entry = discord.ui.TextInput(
            label="À retirer",
            placeholder="Tape `pb` ou le nom du pet",
            min_length=1,
            max_length=30,
        )
        self.add_item(self.entry)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.view.process_remove(interaction, self.entry.value)


class Trade(commands.Cog):
    """Cog gérant l'ensemble du système de trade."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.active_trades: Dict[int, TradeView] = {}
        self._definitions: List[PetDefinition] = list(PET_DEFINITIONS)
        self._definition_by_slug: Dict[str, PetDefinition] = {}
        self._definition_by_name: Dict[str, PetDefinition] = {}
        for definition in self._definitions:
            self._definition_by_name[definition.name] = definition
            keys = {
                definition.name.lower(),
                self._normalize_pet_key(definition.name),
            }
            for key in keys:
                if key:
                    self._definition_by_slug[key] = definition

    # ------------------------------------------------------------------
    # Gestion interne
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_pet_key(value: str) -> str:
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

    def _format_variant_label(self, name: str, variant: Optional[str]) -> str:
        if variant == "gold":
            return f"{name} 🥇"
        if variant == "rainbow":
            return f"{name} 🌈"
        if variant == "normal":
            return f"{name} classique"
        return name

    def _format_variant_label_from_slug(self, slug: str, variant: Optional[str]) -> str:
        definition = self._definition_by_slug.get(slug)
        name = definition.name if definition is not None else slug
        return self._format_variant_label(name, variant)

    def _format_pet_state_display(self, pet_state: TradePetState) -> str:
        name = pet_state.name
        markers = ""
        if pet_state.is_huge:
            markers += " ✨"
        if pet_state.is_rainbow:
            markers += " 🌈"
        elif pet_state.is_gold:
            markers += " 🥇"
        return f"{name}{markers}"

    def _format_pet_record_display(self, record: Mapping[str, Any]) -> str:
        name = str(record.get("name", "Pet"))
        markers = ""
        if bool(record.get("is_huge")):
            markers += " ✨"
        if bool(record.get("is_rainbow")):
            markers += " 🌈"
        elif bool(record.get("is_gold")):
            markers += " 🥇"
        return f"{name}{markers}"

    def _parse_pet_query(self, raw: str) -> tuple[str, Optional[int], Optional[str]]:
        tokens = [token for token in raw.replace(",", " " ).split() if token]
        if not tokens:
            return "", None, None

        variant: Optional[str] = None
        ordinal: Optional[int] = None
        name_parts: List[str] = []

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

        normalized = self._normalize_pet_key(" ".join(name_parts))
        return normalized, ordinal, variant

    def _matches_pet_state(
        self, pet_state: TradePetState, slug: str, variant: Optional[str]
    ) -> bool:
        expected_definition = self._definition_by_slug.get(slug)
        expected_key = (
            self._normalize_pet_key(expected_definition.name)
            if expected_definition is not None
            else slug
        )
        if self._normalize_pet_key(pet_state.name) != expected_key:
            return False
        if variant == "gold":
            return pet_state.is_gold and not pet_state.is_rainbow
        if variant == "rainbow":
            return pet_state.is_rainbow
        if variant == "normal":
            return not pet_state.is_gold and not pet_state.is_rainbow
        return True

    def _candidate_button_label(self, index: int, record: Mapping[str, Any]) -> str:
        name = str(record.get("name", "Pet"))
        markers = ""
        if bool(record.get("is_huge")):
            markers += " ✨"
        if bool(record.get("is_rainbow")):
            markers += " 🌈"
        elif bool(record.get("is_gold")):
            markers += " 🥇"
        income = int(record.get("base_income_per_hour", 0))
        label = f"{index}. {name}{markers}"
        if income:
            label += f" • {income:,} PB/h"
        return label.replace(",", " ")

    def _candidate_embed_line(self, index: int, record: Mapping[str, Any]) -> str:
        label = self._candidate_button_label(index, record)
        status = "Disponible"
        if bool(record.get("is_active")):
            status = "⭐ Équipé"
        acquired_at = record.get("acquired_at")
        if isinstance(acquired_at, datetime):
            status += f" • Obtenu le {acquired_at.strftime('%d/%m/%Y')}"
        return f"**{label}**\n{status}"

    def _build_selection_embed(
        self,
        user: discord.abc.User,
        definition: PetDefinition,
        variant: Optional[str],
        candidates: Sequence[Mapping[str, Any]],
    ) -> discord.Embed:
        variant_label = self._format_variant_label(definition.name, variant)
        description = (
            f"Tu as {len(candidates)} {variant_label} disponible(s). "
            "Choisis lequel ajouter à l'échange."
        )
        lines = [
            self._candidate_embed_line(index, record)
            for index, record in enumerate(candidates, start=1)
        ]
        body = description if not lines else f"{description}\n\n" + "\n\n".join(lines)
        embed = embeds.info_embed(body, title="Sélectionne un pet")
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        return embed

    async def _prompt_trade_pet_selection(
        self,
        interaction: discord.Interaction,
        definition: PetDefinition,
        variant: Optional[str],
        candidates: Sequence[Mapping[str, Any]],
    ) -> tuple[Optional[Mapping[str, Any]], Optional[str]]:
        view = TradePetSelectionView(
            interaction.user.id,
            candidates,
            label_builder=self._candidate_button_label,
        )
        embed = self._build_selection_embed(
            interaction.user,
            definition,
            variant,
            candidates,
        )
        message = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = message
        timed_out = await view.wait()
        if view.selection is not None:
            return view.selection, None
        variant_label = self._format_variant_label(definition.name, variant)
        if view.cancelled:
            return None, f"Sélection annulée pour {variant_label}."
        if timed_out:
            return None, "Temps écoulé pour sélectionner un pet."
        return None, "Sélection impossible."

    async def _resolve_pet_candidates(
        self,
        user_id: int,
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

        is_gold, is_rainbow = self._variant_flags(variant)
        rows = await self.database.get_user_pet_by_name(
            user_id,
            definition.name,
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            include_active=include_active,
            include_inactive=include_inactive,
        )
        return definition, [dict(row) for row in rows], ordinal, variant

    async def resolve_pet_for_trade(
        self, interaction: discord.Interaction, raw_query: str
    ) -> tuple[Optional[Mapping[str, Any]], str, Optional[str]]:
        definition, rows, ordinal, variant = await self._resolve_pet_candidates(
            interaction.user.id,
            raw_query,
            include_active=True,
            include_inactive=True,
        )

        if definition is None:
            slug, _, variant_hint = self._parse_pet_query(raw_query)
            label = (
                self._format_variant_label_from_slug(slug, variant_hint)
                if slug
                else raw_query.strip()
            )
            return None, "", f"{label} est introuvable."

        if not rows:
            variant_label = self._format_variant_label(definition.name, variant)
            return None, "", f"Tu ne possèdes aucun {variant_label} correspondant."

        available_rows = [row for row in rows if not bool(row.get("is_active"))]
        if not available_rows:
            variant_label = self._format_variant_label(definition.name, variant)
            return None, "", (
                f"Tous tes {variant_label} sont actuellement équipés. Déséquipe-les avant de les échanger."
            )

        selected_record: Optional[Mapping[str, Any]] = None
        if ordinal is not None:
            index = ordinal - 1
            if index < 0 or index >= len(available_rows):
                variant_label = self._format_variant_label(definition.name, variant)
                return None, "", (
                    f"Tu as seulement {len(available_rows)} exemplaire(s) de {variant_label} disponible(s)."
                )
            selected_record = available_rows[index]
        elif len(available_rows) == 1:
            selected_record = available_rows[0]
        else:
            selection, error = await self._prompt_trade_pet_selection(
                interaction,
                definition,
                variant,
                available_rows,
            )
            if selection is None:
                return None, "", error or "Sélection annulée."
            selected_record = selection

        display = self._format_pet_record_display(selected_record)
        return selected_record, display, None

    def _register_view(self, view: TradeView) -> None:
        for user_id in view.session.participants():
            self.active_trades[user_id] = view

    def _unregister_view(self, view: TradeView) -> None:
        for user_id in list(view.session.participants()):
            self.active_trades.pop(user_id, None)

    def _get_view(self, user_id: int) -> Optional[TradeView]:
        return self.active_trades.get(user_id)

    async def complete_trade(self, view: TradeView) -> None:
        try:
            result = await self.database.finalize_trade(view.session.trade_id)
        except DatabaseError as exc:
            logger.exception("Impossible de finaliser le trade %s", view.session.trade_id)
            view.session.reset_acceptances()
            await view.refresh_message()
            view.reset_timeout()
            channel = view.message.channel if view.message else None
            if channel:
                await channel.send(
                    embeds.error_embed(
                        "Finalisation impossible : " + str(exc),
                        title="Erreur de trade",
                    )
                )
            return

        trade_record = result["trade"]
        transfers = result.get("transfers", [])

        def _build_offer(user_id: int, *, sent: bool) -> Mapping[str, object]:
            if sent:
                pb_amount = int(trade_record["user_a_pb" if user_id == trade_record["user_a_id"] else "user_b_pb"])
                pets = [
                    {
                        "user_pet_id": item["user_pet_id"],
                        "name": item["name"],
                        "rarity": item["rarity"],
                        "is_huge": item["is_huge"],
                        "is_gold": item["is_gold"],
                        "is_rainbow": item["is_rainbow"],
                    }
                    for item in transfers
                    if item["from_user_id"] == user_id
                ]
            else:
                pb_amount = int(trade_record["user_b_pb" if user_id == trade_record["user_a_id"] else "user_a_pb"])
                pets = [
                    {
                        "user_pet_id": item["user_pet_id"],
                        "name": item["name"],
                        "rarity": item["rarity"],
                        "is_huge": item["is_huge"],
                        "is_gold": item["is_gold"],
                        "is_rainbow": item["is_rainbow"],
                    }
                    for item in transfers
                    if item["to_user_id"] == user_id
                ]
            return {"pb": pb_amount, "pets": pets}

        user_a_id = int(trade_record["user_a_id"])
        user_b_id = int(trade_record["user_b_id"])
        embed = embeds.trade_completed_embed(
            trade_id=int(trade_record["id"]),
            user_a=view.session.user_a,
            user_b=view.session.user_b,
            sent_a=_build_offer(user_a_id, sent=True),
            sent_b=_build_offer(user_b_id, sent=True),
            received_a=_build_offer(user_a_id, sent=False),
            received_b=_build_offer(user_b_id, sent=False),
        )

        view.disable()
        if view.message:
            await view.message.edit(embed=embed, view=None)
            await view.message.channel.send(
                f"✅ Trade #{trade_record['id']} complété entre {view.session.user_a.mention} et {view.session.user_b.mention}!"
            )
        view.stop()
        self._unregister_view(view)

    async def cancel_trade(
        self,
        view: TradeView,
        interaction: Optional[discord.Interaction] = None,
        *,
        reason: str | None = None,
    ) -> None:
        try:
            await self.database.cancel_trade(view.session.trade_id)
        except DatabaseError:
            logger.exception("Impossible d'annuler le trade %s", view.session.trade_id)
        embed = embeds.trade_cancelled_embed(trade_id=view.session.trade_id, reason=reason)
        view.disable()
        if view.message:
            await view.message.edit(embed=embed, view=None)
        if interaction:
            await self._send_cancel_feedback(interaction, reason)
        view.stop()
        self._unregister_view(view)

    async def timeout_trade(self, view: TradeView) -> None:
        if view.session.trade_id in (v.session.trade_id for v in self.active_trades.values()):
            await self.cancel_trade(view, reason="Temps écoulé.")

    async def _send_cancel_feedback(
        self, interaction: discord.Interaction, reason: Optional[str]
    ) -> None:
        message = reason or "Échange annulé."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    # ------------------------------------------------------------------
    # Commandes publiques
    # ------------------------------------------------------------------
    @commands.command(name="trade")
    async def trade(self, ctx: commands.Context, member: discord.Member) -> None:
        if member.bot:
            await ctx.send(embed=embeds.error_embed("Tu ne peux pas échanger avec un bot."))
            return
        if member.id == ctx.author.id:
            await ctx.send(embed=embeds.error_embed("Tu ne peux pas échanger avec toi-même."))
            return
        if self._get_view(ctx.author.id) or self._get_view(member.id):
            await ctx.send(embed=embeds.error_embed("Un des deux participants a déjà un trade en cours."))
            return

        try:
            trade_row = await self.database.create_trade(ctx.author.id, member.id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        session = TradeSession(
            trade_id=int(trade_row["id"]),
            user_a=ctx.author,
            user_b=member,
        )
        view = TradeView(self, session)
        embed = view._build_embed()
        message = await ctx.send(
            content=f"{member.mention}, {ctx.author.mention} souhaite échanger avec toi !",
            embed=embed,
            view=view,
        )
        view.message = message
        self._register_view(view)
        logger.info(
            "Trade %s créé entre %s et %s",
            session.trade_id,
            ctx.author.id,
            member.id,
        )

    @commands.command(name="tradehistory")
    async def trade_history(self, ctx: commands.Context, limit: int = 10) -> None:
        limit = max(1, min(limit, 25))
        records = await self.database.get_trade_history(ctx.author.id, limit)
        entries: List[Mapping[str, object]] = []
        for record in records:
            partner_id = int(record["partner_id"])
            partner = ctx.guild.get_member(partner_id) if ctx.guild else None
            if partner is None:
                partner = self.bot.get_user(partner_id)
            partner_name = partner.display_name if partner else f"Utilisateur {partner_id}"
            entries.append(
                {
                    "id": int(record["id"]),
                    "status": str(record["status"]),
                    "created_at": record["created_at"],
                    "completed_at": record["completed_at"],
                    "partner_id": partner_id,
                    "partner_name": partner_name,
                    "pb_sent": int(record["pb_sent"]),
                    "pb_received": int(record["pb_received"]),
                    "pets_sent": int(record["pets_sent"]),
                    "pets_received": int(record["pets_received"]),
                }
            )
        embed = embeds.transaction_history_embed(user=ctx.author, entries=entries)
        await ctx.send(embed=embed)

    @commands.command(name="tradestats")
    async def trade_stats(self, ctx: commands.Context) -> None:
        try:
            stats = await self.database.get_trade_stats()
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        description = (
            f"Total d'échanges : **{int(stats['total_trades'])}**\n"
            f"Complétés : **{int(stats['completed_trades'])}** • "
            f"Annulés : **{int(stats['cancelled_trades'])}** • "
            f"En cours : **{int(stats['pending_trades'])}**\n"
            f"PB échangés : **{embeds.format_currency(int(stats['total_pb']))}**\n"
            f"Pets échangés : **{int(stats['total_pets_exchanged'])}**"
        )
        embed = embeds.info_embed(description, title="Statistiques des échanges")
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Trade(bot))
