"""Système économique de base: daily, balance, transferts et récompenses messages."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    DAILY_BONUS_CHANCE,
    DAILY_BONUS_MAX,
    DAILY_BONUS_MIN,
    DAILY_COOLDOWN,
    DAILY_MAX,
    DAILY_MIN,
    MESSAGE_REWARD_AMOUNT,
    MESSAGE_REWARD_COOLDOWN,
    TRANSFER_COOLDOWN,
    TRANSFER_MAX,
    TRANSFER_MIN,
    TRANSFER_TAX_RATE,
)
from utils import embeds

logger = logging.getLogger(__name__)


class CooldownCache:
    """Gestionnaire simple de cooldowns en mémoire."""

    def __init__(self, duration: int) -> None:
        self.duration = duration
        self._storage: Dict[int, float] = {}

    def remaining(self, user_id: int) -> float:
        now = time.monotonic()
        expires = self._storage.get(user_id, 0.0)
        if expires <= now:
            return 0.0
        return expires - now

    def set(self, user_id: int) -> None:
        self._storage[user_id] = time.monotonic() + self.duration

    def cleanup(self) -> None:
        now = time.monotonic()
        to_remove = [user_id for user_id, expiry in self._storage.items() if expiry <= now]
        for user_id in to_remove:
            self._storage.pop(user_id, None)


class Economy(commands.Cog):
    """Cog gérant les interactions économiques de base."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.message_cooldown = CooldownCache(MESSAGE_REWARD_COOLDOWN)
        self.transfer_cooldown = CooldownCache(TRANSFER_COOLDOWN)
        self._cleanup_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cooldown_cleanup())
        logger.info("Economy prêt")

    async def cog_unload(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):  # type: ignore[name-defined]
                await self._cleanup_task

    async def _cooldown_cleanup(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.message_cooldown.cleanup()
            self.transfer_cooldown.cleanup()

    # ------------------------------------------------------------------
    # Récompenses messages
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if len(message.content.strip()) < 3:
            return

        remaining = self.message_cooldown.remaining(message.author.id)
        if remaining > 0:
            return

        self.message_cooldown.set(message.author.id)
        await self.database.ensure_user(message.author.id)
        before, after = await self.database.increment_balance(message.author.id, MESSAGE_REWARD_AMOUNT)
        await self.bot.transaction_logs.log(
            message.author.id,
            "message_reward",
            MESSAGE_REWARD_AMOUNT,
            before,
            after,
            description=f"Récompense de message dans #{message.channel.name}",
        )

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------
    async def _send_balance(self, target: discord.Member, ctx_or_inter) -> None:
        wallet = await self.database.fetch_balance(target.id)
        bank = (await self.database.get_private_bank_account(target.id))["balance"]
        embed = embeds.balance_embed(target, balance=wallet, bank_balance=bank)
        await ctx_or_inter.send(embed=embed)

    @commands.command(name="balance", aliases=("bal",))
    async def balance_prefix(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        member = member or ctx.author
        await self._send_balance(member, ctx)

    @app_commands.command(name="balance", description="Consulter ton solde")
    async def balance_slash(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        member = membre or interaction.user
        await interaction.response.defer(ephemeral=True)
        await self._send_balance(member, interaction.followup)

    # ------------------------------------------------------------------
    # Daily
    # ------------------------------------------------------------------
    async def _daily_reward(self, member: discord.Member) -> discord.Embed:
        last_daily = await self.database.get_last_daily(member.id)
        now = datetime.now(timezone.utc)
        if last_daily and (now - last_daily).total_seconds() < DAILY_COOLDOWN:
            remaining = DAILY_COOLDOWN - (now - last_daily).total_seconds()
            return embeds.cooldown_embed("/daily", remaining)

        base_reward = random.randint(DAILY_MIN, DAILY_MAX)
        bonus = 0
        if random.random() <= DAILY_BONUS_CHANCE:
            bonus = random.randint(DAILY_BONUS_MIN, DAILY_BONUS_MAX)
        total = base_reward + bonus

        before, after = await self.database.increment_balance(member.id, total)
        await self.database.set_last_daily(member.id, now)
        await self.bot.transaction_logs.log(
            member.id,
            "daily",
            total,
            before,
            after,
            description="Récompense quotidienne",
        )
        return embeds.daily_embed(member, amount=total, bonus=bonus)

    @commands.command(name="daily")
    async def daily_prefix(self, ctx: commands.Context) -> None:
        embed = await self._daily_reward(ctx.author)
        await ctx.send(embed=embed)

    @app_commands.command(name="daily", description="Récupérer ta récompense quotidienne")
    async def daily_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        embed = await self._daily_reward(interaction.user)
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Transferts
    # ------------------------------------------------------------------
    async def _perform_transfer(self, sender: discord.Member, receiver: discord.Member, amount: int) -> discord.Embed:
        if sender == receiver:
            return embeds.error_embed("Tu ne peux pas t'envoyer de l'argent.")
        if amount < TRANSFER_MIN or amount > TRANSFER_MAX:
            return embeds.error_embed(
                f"Le transfert doit être compris entre {TRANSFER_MIN:,} et {TRANSFER_MAX:,} PB."
            )

        remaining = self.transfer_cooldown.remaining(sender.id)
        if remaining > 0:
            return embeds.cooldown_embed("/give", remaining)

        tax_amount = int(amount * TRANSFER_TAX_RATE)
        net_amount = amount - tax_amount
        if net_amount <= 0:
            return embeds.error_embed("Le montant est trop faible après la taxe.")

        async with self.database.transaction() as conn:
            sender_balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                sender.id,
            )
            if sender_balance is None or sender_balance < amount:
                return embeds.error_embed("Ton solde est insuffisant pour ce transfert.")

            receiver_balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                receiver.id,
            )
            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", amount, sender.id)
            await conn.execute(
                """
                INSERT INTO users (user_id, balance)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET balance = users.balance + $2
                """,
                receiver.id,
                net_amount,
            )
            await conn.execute(
                """
                INSERT INTO transaction_logs (user_id, transaction_type, amount, balance_before, balance_after, description, related_user_id)
                VALUES ($1, 'transfer_sent', $2, $3, $3 - $2, 'Transfert à $4', $4)
                """,
                sender.id,
                amount,
                sender_balance,
                receiver.id,
            )
            new_sender_balance = sender_balance - amount
            new_receiver_balance = (receiver_balance or 0) + net_amount
            await conn.execute(
                """
                INSERT INTO transaction_logs (user_id, transaction_type, amount, balance_before, balance_after, description, related_user_id)
                VALUES ($1, 'transfer_received', $2, $3, $4, 'Transfert reçu de $5', $5)
                """,
                receiver.id,
                net_amount,
                receiver_balance or 0,
                new_receiver_balance,
                sender.id,
            )

        await self.database.add_public_bank_funds(tax_amount)
        self.transfer_cooldown.set(sender.id)
        return embeds.transfer_embed(
            sender=sender,
            receiver=receiver,
            net_amount=net_amount,
            tax_amount=tax_amount,
            tax_rate=TRANSFER_TAX_RATE * 100,
            new_balance=new_sender_balance,
        )

    @commands.command(name="give")
    async def give_prefix(self, ctx: commands.Context, member: discord.Member, amount: int) -> None:
        embed = await self._perform_transfer(ctx.author, member, amount)
        await ctx.send(embed=embed)

    @app_commands.command(name="give", description="Transférer des PB à un membre")
    @app_commands.describe(membre="Destinataire", montant="Montant à transférer")
    async def give_slash(self, interaction: discord.Interaction, membre: discord.Member, montant: int) -> None:
        embed = await self._perform_transfer(interaction.user, membre, montant)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Commandes admin
    # ------------------------------------------------------------------
    @commands.command(name="addpb")
    @commands.has_permissions(administrator=True)
    async def addpb_prefix(self, ctx: commands.Context, member: discord.Member, amount: int) -> None:
        before, after = await self.database.increment_balance(member.id, amount)
        await self.bot.transaction_logs.log(
            member.id,
            "admin_add",
            amount,
            before,
            after,
            description=f"Ajout manuel par {ctx.author.display_name}",
        )
        await ctx.send(embed=embeds.success_embed(f"{amount:,} PB ajoutés à {member.display_name}"))

    @app_commands.command(name="addpb", description="Ajouter des PB à un membre")
    @app_commands.checks.has_permissions(administrator=True)
    async def addpb_slash(self, interaction: discord.Interaction, membre: discord.Member, montant: int) -> None:
        before, after = await self.database.increment_balance(membre.id, montant)
        await self.bot.transaction_logs.log(
            membre.id,
            "admin_add",
            montant,
            before,
            after,
            description=f"Ajout manuel par {interaction.user.display_name}",
        )
        await interaction.response.send_message(embed=embeds.success_embed(f"{montant:,} PB ajoutés."), ephemeral=True)

    @commands.command(name="reset_economy")
    @commands.is_owner()
    async def reset_economy_prefix(self, ctx: commands.Context, confirmation: Optional[str] = None) -> None:
        if confirmation != "CONFIRMER":
            await ctx.send("Tape `CONFIRMER` pour valider la réinitialisation.")
            return
        await self.database.wipe_economy()
        await ctx.send("Économie réinitialisée.")

    @app_commands.command(name="reset_economy", description="Réinitialiser toute l'économie")
    @app_commands.checks.is_owner()
    async def reset_economy_slash(self, interaction: discord.Interaction, confirmation: bool) -> None:
        if not confirmation:
            await interaction.response.send_message("Action annulée", ephemeral=True)
            return
        await self.database.wipe_economy()
        await interaction.response.send_message("Économie réinitialisée", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
