"""Banque publique: retraits et statistiques."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    PUBLIC_BANK_DAILY_LIMIT,
    PUBLIC_BANK_WITHDRAW_COOLDOWN,
    PUBLIC_BANK_WITHDRAW_MAX,
    PUBLIC_BANK_WITHDRAW_MIN,
)
from utils import embeds

logger = logging.getLogger(__name__)


class CooldownTracker:
    def __init__(self, duration: int) -> None:
        self.duration = duration
        self._storage: Dict[int, float] = {}

    def remaining(self, user_id: int) -> float:
        now = time.monotonic()
        expires = self._storage.get(user_id, 0.0)
        return max(0.0, expires - now)

    def set(self, user_id: int) -> None:
        self._storage[user_id] = time.monotonic() + self.duration


class PublicBank(commands.Cog):
    """Gère la redistribution des fonds publics."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.cooldown = CooldownTracker(PUBLIC_BANK_WITHDRAW_COOLDOWN)

    async def show_stats(self, ctx_or_inter) -> None:
        stats = await self.database.get_public_bank_stats()
        embed = embeds.public_bank_embed(stats)
        await ctx_or_inter.send(embed=embed)

    @commands.command(name="publicbank")
    async def publicbank_prefix(self, ctx: commands.Context) -> None:
        await self.show_stats(ctx)

    @app_commands.command(name="publicbank", description="Voir l'état de la banque publique")
    async def publicbank_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.show_stats(interaction.followup)

    # ------------------------------------------------------------------
    # Retraits
    # ------------------------------------------------------------------
    async def withdraw(self, member: discord.Member, amount: int) -> discord.Embed:
        if amount < PUBLIC_BANK_WITHDRAW_MIN or amount > PUBLIC_BANK_WITHDRAW_MAX:
            return embeds.error_embed(
                f"Montant invalide. Entre {PUBLIC_BANK_WITHDRAW_MIN} et {PUBLIC_BANK_WITHDRAW_MAX} PB par retrait."
            )
        remaining = self.cooldown.remaining(member.id)
        if remaining > 0:
            return embeds.cooldown_embed("/publicbank withdraw", remaining)

        today = datetime.now(timezone.utc)
        total_today = await self.database.get_public_withdrawals_for_day(member.id, day=today)
        if total_today + amount > PUBLIC_BANK_DAILY_LIMIT:
            return embeds.error_embed("Limite quotidienne atteinte (2000 PB).")

        success, remaining_balance = await self.database.withdraw_public_bank(amount)
        if not success:
            return embeds.error_embed("La banque publique ne dispose pas de suffisamment de fonds.")

        before, after = await self.database.increment_balance(member.id, amount)
        await self.database.record_public_withdrawal(member.id, amount, remaining_balance)
        await self.bot.transaction_logs.log(
            member.id,
            "public_bank_withdraw",
            amount,
            before,
            after,
            description="Retrait de la banque publique",
        )
        self.cooldown.set(member.id)
        return embeds.success_embed(f"Tu as retiré {embeds.format_currency(amount)} de la banque publique.")

    @commands.command(name="pb_withdraw")
    async def withdraw_prefix(self, ctx: commands.Context, amount: int) -> None:
        embed = await self.withdraw(ctx.author, amount)
        await ctx.send(embed=embed)

    @app_commands.command(name="public_withdraw", description="Retirer depuis la banque publique")
    async def withdraw_slash(self, interaction: discord.Interaction, montant: int) -> None:
        embed = await self.withdraw(interaction.user, montant)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PublicBank(bot))
