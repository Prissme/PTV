"""Mini-jeu de roulette."""
from __future__ import annotations

import logging
import random
import time
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import ROULETTE_COOLDOWN
from utils import embeds

logger = logging.getLogger(__name__)

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


class RouletteCooldown:
    def __init__(self, duration: int) -> None:
        self.duration = duration
        self._storage: Dict[int, float] = {}

    def remaining(self, user_id: int) -> float:
        now = time.monotonic()
        expires = self._storage.get(user_id, 0.0)
        return max(0.0, expires - now)

    def set(self, user_id: int) -> None:
        self._storage[user_id] = time.monotonic() + self.duration


class Roulette(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.cooldown = RouletteCooldown(ROULETTE_COOLDOWN)

    def _resolve_bet(self, bet_type: str, target: Optional[int], result: int) -> int:
        bet_type = bet_type.lower()
        if bet_type in {"red", "rouge"}:
            return 2 if result in RED_NUMBERS else 0
        if bet_type in {"black", "noir"}:
            return 2 if result not in RED_NUMBERS and result != 0 else 0
        if bet_type in {"even", "pair"}:
            return 2 if result != 0 and result % 2 == 0 else 0
        if bet_type in {"odd", "impair"}:
            return 2 if result % 2 == 1 else 0
        if bet_type in {"low", "manque"}:
            return 2 if 1 <= result <= 18 else 0
        if bet_type in {"high", "passe"}:
            return 2 if 19 <= result <= 36 else 0
        if bet_type == "number" and target is not None:
            return 36 if result == target else 0
        return 0

    async def play(self, member: discord.Member, amount: int, bet_type: str, target: Optional[int]) -> discord.Embed:
        if amount <= 0:
            return embeds.error_embed("Le montant doit être positif.")
        if bet_type.lower() == "number":
            if target is None or not 0 <= int(target) <= 36:
                return embeds.error_embed("Choisis un numéro entre 0 et 36.")
            target = int(target)
        else:
            target = None
        remaining = self.cooldown.remaining(member.id)
        if remaining > 0:
            return embeds.cooldown_embed("/roulette", remaining)

        async with self.database.transaction() as conn:
            balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                member.id,
            )
            if balance is None or balance < amount:
                return embeds.error_embed("Solde insuffisant pour jouer.")
            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", amount, member.id)
            before_balance = balance
            after_balance = balance - amount

        result = random.randint(0, 36)
        multiplier = self._resolve_bet(bet_type, target, result)
        winnings = amount * multiplier
        description = f"Résultat: **{result}**\n"
        if multiplier:
            before, after = await self.database.increment_balance(member.id, winnings)
            description += f"Tu gagnes {embeds.format_currency(winnings)} !"
            await self.bot.transaction_logs.log(
                member.id,
                "roulette_win",
                winnings,
                before,
                after,
                description=f"Roulette {bet_type}",
            )
        else:
            await self.database.add_public_bank_funds(amount)
            description += "Perdu ! Le montant rejoint la banque publique."
            await self.bot.transaction_logs.log(
                member.id,
                "roulette_loss",
                -amount,
                before_balance,
                after_balance,
                description=f"Roulette {bet_type}",
            )
        self.cooldown.set(member.id)
        return embeds.info_embed(description, title="Roulette")

    @commands.command(name="roulette")
    async def roulette_prefix(self, ctx: commands.Context, amount: int, bet_type: str, number: Optional[int] = None) -> None:
        embed = await self.play(ctx.author, amount, bet_type, number)
        await ctx.send(embed=embed)

    @app_commands.command(name="roulette", description="Jouer à la roulette")
    async def roulette_slash(self, interaction: discord.Interaction, montant: int, bet_type: str, number: Optional[int] = None) -> None:
        embed = await self.play(interaction.user, montant, bet_type, number)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Roulette(bot))
