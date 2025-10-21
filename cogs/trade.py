"""Système d'échange interactif complet pour EcoBot."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import discord
from discord.ext import commands

from database.db import DatabaseError
from utils import embeds

logger = logging.getLogger(__name__)


@dataclass
class TradePetState:
    """Représente un pet proposé dans un échange."""

    trade_pet_id: int
    user_pet_id: int
    name: str
    rarity: str
    is_huge: bool
    from_user_id: int
    to_user_id: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "trade_pet_id": self.trade_pet_id,
            "user_pet_id": self.user_pet_id,
            "name": self.name,
            "rarity": self.rarity,
            "is_huge": self.is_huge,
        }


@dataclass
class TradeOfferState:
    """État courant de l'offre d'un utilisateur."""

    pb: int = 0
    pets: Dict[int, TradePetState] = field(default_factory=dict)
    accepted: bool = False
    confirmed: bool = False

    def to_embed(self) -> dict[str, object]:
        pets = sorted(self.pets.values(), key=lambda pet: pet.user_pet_id)
        return {
            "pb": self.pb,
            "pets": [pet.to_mapping() for pet in pets],
            "accepted": self.accepted,
            "confirmed": self.confirmed,
        }


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
            offer.confirmed = False

    def mark_accepted(self, user_id: int, accepted: bool) -> None:
        offer = self.offers[user_id]
        offer.accepted = accepted
        if not accepted:
            offer.confirmed = False

    def mark_confirmed(self, user_id: int) -> None:
        self.offers[user_id].confirmed = True

    def all_accepted(self) -> bool:
        return all(offer.accepted for offer in self.offers.values())

    def all_confirmed(self) -> bool:
        return all(offer.confirmed for offer in self.offers.values())

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

        self.add_pet_button = discord.ui.Button(emoji="➕", label="Ajouter Pet", style=discord.ButtonStyle.primary)
        self.add_pet_button.callback = self._add_pet
        self.add_item(self.add_pet_button)

        self.add_pb_button = discord.ui.Button(emoji="💰", label="Ajouter PB", style=discord.ButtonStyle.secondary)
        self.add_pb_button.callback = self._add_pb
        self.add_item(self.add_pb_button)

        self.remove_button = discord.ui.Button(emoji="➖", label="Retirer", style=discord.ButtonStyle.secondary)
        self.remove_button.callback = self._remove_item
        self.add_item(self.remove_button)

        self.accept_button = discord.ui.Button(emoji="✅", label="Prêt", style=discord.ButtonStyle.success)
        self.accept_button.callback = self._toggle_ready
        self.add_item(self.accept_button)

        self.confirm_button = discord.ui.Button(emoji="🔒", label="Confirmer", style=discord.ButtonStyle.success)
        self.confirm_button.disabled = True
        self.confirm_button.callback = self._confirm_trade
        self.add_item(self.confirm_button)

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
        self.confirm_button.disabled = not self.session.all_accepted()

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
        self._update_components()
        await self.message.edit(embed=self._build_embed(), view=self)

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
        async with self.lock:
            offer = self.session.offers[interaction.user.id]
            new_state = not offer.accepted
            self.session.mark_accepted(interaction.user.id, new_state)
            if not new_state:
                message = "Ton statut de préparation est annulé."
            else:
                message = "Tu es prêt pour l'échange."
            if not self.session.all_accepted():
                self.session.offers[self.session.other_user_id(interaction.user.id)].confirmed = False
            await self.refresh_message()
            self.reset_timeout()
        await self._send_ephemeral(interaction, message)

    async def _confirm_trade(self, interaction: discord.Interaction) -> None:
        if not self.session.all_accepted():
            await self._send_ephemeral(interaction, "Les deux participants doivent d'abord accepter l'échange.")
            return
        if self.session.offers[interaction.user.id].confirmed:
            await self._send_ephemeral(interaction, "Tu as déjà confirmé cet échange.")
            return
        modal = ConfirmTradeModal(self, interaction.user.id)
        await interaction.response.send_modal(modal)

    async def _cancel_trade(self, interaction: discord.Interaction) -> None:
        await self.cog.cancel_trade(self, interaction, reason="Échange annulé par un participant.")

    # ------------------------------------------------------------------
    # Actions depuis les modals
    # ------------------------------------------------------------------
    async def process_add_pets(self, interaction: discord.Interaction, pet_ids: Iterable[int]) -> None:
        successes: List[int] = []
        errors: List[str] = []
        async with self.lock:
            for pet_id in pet_ids:
                try:
                    record = await self.cog.database.get_user_pet(interaction.user.id, pet_id)
                    if record is None:
                        errors.append(f"Pet #{pet_id} introuvable dans ton inventaire.")
                        continue
                    if bool(record.get("is_active")):
                        errors.append(f"Pet #{pet_id} est équipé. Déséquipe-le avant l'échange.")
                        continue
                    other_id = self.session.other_user_id(interaction.user.id)
                    trade_pet = await self.cog.database.add_trade_pet(
                        self.session.trade_id,
                        pet_id,
                        interaction.user.id,
                        other_id,
                    )
                    pet_state = TradePetState(
                        trade_pet_id=int(trade_pet["id"]),
                        user_pet_id=pet_id,
                        name=str(record.get("name")),
                        rarity=str(record.get("rarity")),
                        is_huge=bool(record.get("is_huge", False)),
                        from_user_id=interaction.user.id,
                        to_user_id=other_id,
                    )
                    self.session.offers[interaction.user.id].pets[pet_state.trade_pet_id] = pet_state
                    successes.append(pet_id)
                except DatabaseError as exc:
                    errors.append(str(exc))
            if successes:
                self.session.reset_acceptances()
                await self.refresh_message()
                self.reset_timeout()
        summary = []
        if successes:
            summary.append(f"Pets ajoutés : {', '.join(str(x) for x in successes)}")
        if errors:
            summary.extend(errors)
        if not summary:
            summary.append("Aucun pet n'a pu être ajouté.")
        await self._send_ephemeral(interaction, "\n".join(summary))

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
            await self.refresh_message()
            self.reset_timeout()
        await self._send_ephemeral(
            interaction,
            f"Tu proposes désormais {embeds.format_currency(amount)} dans l'échange.",
        )

    async def process_remove(self, interaction: discord.Interaction, entry: str) -> None:
        entry = entry.strip().lower()
        async with self.lock:
            if entry in {"pb", "money", "solde"}:
                try:
                    await self.cog.database.update_trade_pb(self.session.trade_id, interaction.user.id, 0)
                except DatabaseError as exc:
                    await self._send_ephemeral(interaction, str(exc))
                    return
                self.session.offers[interaction.user.id].pb = 0
                self.session.reset_acceptances()
                await self.refresh_message()
                self.reset_timeout()
                await self._send_ephemeral(interaction, "Ton offre de PB a été retirée.")
                return

            if not entry.isdigit():
                await self._send_ephemeral(interaction, "Indique `pb` ou l'identifiant d'un pet à retirer.")
                return

            user_pet_id = int(entry)
            offer = self.session.offers[interaction.user.id]
            target: Optional[TradePetState] = None
            for pet_state in offer.pets.values():
                if pet_state.user_pet_id == user_pet_id:
                    target = pet_state
                    break
            if target is None:
                await self._send_ephemeral(interaction, f"Pet #{user_pet_id} n'est pas dans ton offre.")
                return
            try:
                removed = await self.cog.database.remove_trade_pet(
                    self.session.trade_id, target.user_pet_id, interaction.user.id
                )
            except DatabaseError as exc:
                await self._send_ephemeral(interaction, str(exc))
                return
            if not removed:
                await self._send_ephemeral(interaction, "Impossible de retirer ce pet de l'échange.")
                return
            offer.pets.pop(target.trade_pet_id, None)
            self.session.reset_acceptances()
            await self.refresh_message()
            self.reset_timeout()
        await self._send_ephemeral(interaction, f"Pet #{user_pet_id} retiré de l'offre.")

    async def register_confirmation(self, interaction: discord.Interaction, user_id: int) -> None:
        async with self.lock:
            self.session.mark_confirmed(user_id)
            await self.refresh_message()
            everyone_confirmed = self.session.all_confirmed()
        await self._send_ephemeral(interaction, "Confirmation enregistrée.")
        if everyone_confirmed:
            await self.cog.complete_trade(self)


class AddPetModal(discord.ui.Modal):
    """Modal de saisie pour ajouter des pets."""

    def __init__(self, view: TradeView, user_id: int) -> None:
        super().__init__(title="Ajouter des pets")
        self.view = view
        self.user_id = user_id
        self.pet_ids = discord.ui.TextInput(
            label="Identifiants des pets",
            placeholder="Exemple : 12 45 78",
            min_length=1,
            max_length=100,
        )
        self.add_item(self.pet_ids)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ids: List[int] = []
        for chunk in self.pet_ids.value.replace(",", " ").split():
            if chunk.isdigit():
                ids.append(int(chunk))
        if not ids:
            await interaction.response.send_message("Aucun identifiant valide fourni.", ephemeral=True)
            return
        await self.view.process_add_pets(interaction, ids)


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
            placeholder="Tape `pb` ou l'identifiant du pet",
            min_length=1,
            max_length=30,
        )
        self.add_item(self.entry)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.view.process_remove(interaction, self.entry.value)


class ConfirmTradeModal(discord.ui.Modal):
    """Modal de confirmation finale."""

    def __init__(self, view: TradeView, user_id: int) -> None:
        super().__init__(title="Confirmer l'échange")
        self.view = view
        self.user_id = user_id
        self.confirmation = discord.ui.TextInput(
            label="Tape CONFIRMER pour finaliser",
            placeholder="CONFIRMER",
            min_length=3,
            max_length=10,
        )
        self.add_item(self.confirmation)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.confirmation.value.strip().upper() != "CONFIRMER":
            await interaction.response.send_message("Tu dois taper CONFIRMER pour valider.", ephemeral=True)
            return
        await self.view.register_confirmation(interaction, self.user_id)


class Trade(commands.Cog):
    """Cog gérant l'ensemble du système de trade."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.active_trades: Dict[int, TradeView] = {}

    # ------------------------------------------------------------------
    # Gestion interne
    # ------------------------------------------------------------------
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
