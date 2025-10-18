"""Système de vol entre joueurs."""
from __future__ import annotations

import logging
import random
import time
from typing import Dict

import discord
from discord import app_commands
from discord.ext import commands

from config import STEAL_COOLDOWN, STEAL_FAIL_PENALTY_PERCENTAGE, STEAL_PERCENTAGE, STEAL_SUCCESS_RATE
from utils import embeds

logger = logging.getLogger(__name__)


class StealCooldown:
    def __init__(self, duration: int) -> None:
        self.duration = duration
        self._storage: Dict[int, float] = {}

    def remaining(self, user_id: int) -> float:
        now = time.monotonic()
        expires = self._storage.get(user_id, 0.0)
        return max(0.0, expires - now)

    def set(self, user_id: int) -> None:
        self._storage[user_id] = time.monotonic() + self.duration


class Steal(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.cooldown = StealCooldown(STEAL_COOLDOWN)

    async def attempt(self, thief: discord.Member, target: discord.Member) -> discord.Embed:
        if thief == target:
            return embeds.error_embed("Tu ne peux pas te voler toi-même.")
        if target.bot:
            return embeds.error_embed("Les bots ne peuvent pas être volés.")

        remaining = self.cooldown.remaining(thief.id)
        if remaining > 0:
            return embeds.cooldown_embed("/steal", remaining)

        target_balance = await self.database.fetch_balance(target.id)
        if target_balance <= 0:
            return embeds.error_embed("La cible n'a pas d'argent.")

        if await self.database.has_defense(target.id):
            penalty = max(int(target_balance * STEAL_PERCENTAGE * STEAL_FAIL_PENALTY_PERCENTAGE), 1)
            before, after = await self.database.increment_balance(thief.id, -penalty)
            await self.database.add_public_bank_funds(penalty)
            await self.bot.transaction_logs.log(
                thief.id,
                "steal_blocked",
                -penalty,
                before,
                after,
                description=f"Défense active sur {target.display_name}",
            )
            self.cooldown.set(thief.id)
            return embeds.error_embed("La défense de ta cible t'a repoussé !")

        steal_amount = max(int(target_balance * STEAL_PERCENTAGE), 1)
        success = random.random() < STEAL_SUCCESS_RATE

        penalty_amount = 0
        async with self.database.transaction() as conn:
            thief_balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                thief.id,
            ) or 0
            victim_balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                target.id,
            ) or 0
            if victim_balance < steal_amount:
                steal_amount = victim_balance
            if steal_amount <= 0:
                return embeds.error_embed("La cible est fauchée.")

            if success:
                await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", steal_amount, target.id)
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                    steal_amount,
                    thief.id,
                )
            else:
                penalty = max(int(steal_amount * STEAL_FAIL_PENALTY_PERCENTAGE), 1)
                if thief_balance < penalty:
                    penalty = thief_balance
                await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", penalty, thief.id)
                penalty_amount = penalty

        if not success and penalty_amount:
            await self.database.add_public_bank_funds(penalty_amount)

        if success:
            await self.bot.transaction_logs.log(
                thief.id,
                "steal_success",
                steal_amount,
                thief_balance,
                thief_balance + steal_amount,
                description=f"Vol réussi sur {target.display_name}",
            )
            await self.bot.transaction_logs.log(
                target.id,
                "steal_loss",
                -steal_amount,
                victim_balance,
                victim_balance - steal_amount,
                description=f"Vol par {thief.display_name}",
            )
            self.cooldown.set(thief.id)
            return embeds.success_embed(f"Tu as volé {embeds.format_currency(steal_amount)} à {target.display_name} !")
        else:
            await self.bot.transaction_logs.log(
                thief.id,
                "steal_fail",
                -penalty_amount,
                thief_balance,
                thief_balance - penalty_amount,
                description=f"Échec de vol sur {target.display_name}",
            )
            self.cooldown.set(thief.id)
            return embeds.error_embed("Échec du vol, tu payes une lourde amende.")

    @commands.command(name="steal")
    async def steal_prefix(self, ctx: commands.Context, target: discord.Member) -> None:
        embed = await self.attempt(ctx.author, target)
        await ctx.send(embed=embed)

    @app_commands.command(name="steal", description="Tenter de voler un joueur")
    async def steal_slash(self, interaction: discord.Interaction, cible: discord.Member) -> None:
        embed = await self.attempt(interaction.user, cible)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Steal(bot))
