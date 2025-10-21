"""Fonctionnalités économiques essentielles : balance, daily et récompenses."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from config import DAILY_COOLDOWN, DAILY_REWARD, MESSAGE_COOLDOWN, MESSAGE_REWARD, PREFIX
from utils import embeds

logger = logging.getLogger(__name__)


class CooldownManager:
    """Gestion simple de cooldowns en mémoire."""

    def __init__(self, duration: int) -> None:
        self.duration = duration
        self._cooldowns: dict[int, float] = {}

    def remaining(self, user_id: int) -> float:
        expires_at = self._cooldowns.get(user_id, 0.0)
        remaining = expires_at - time.monotonic()
        return remaining if remaining > 0 else 0.0

    def trigger(self, user_id: int) -> None:
        self._cooldowns[user_id] = time.monotonic() + self.duration

    def cleanup(self) -> None:
        now = time.monotonic()
        expired = [user_id for user_id, expiry in self._cooldowns.items() if expiry <= now]
        for user_id in expired:
            self._cooldowns.pop(user_id, None)


class Economy(commands.Cog):
    """Commandes économiques réduites au strict nécessaire."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.message_cooldown = CooldownManager(MESSAGE_COOLDOWN)
        self._cleanup_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Cog Economy chargé")

    async def cog_unload(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.message_cooldown.cleanup()

    # ------------------------------------------------------------------
    # Récompenses de messages
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if not message.content.strip():
            return

        remaining = self.message_cooldown.remaining(message.author.id)
        if remaining > 0:
            return

        self.message_cooldown.trigger(message.author.id)
        await self.database.ensure_user(message.author.id)
        await self.database.increment_balance(
            message.author.id,
            MESSAGE_REWARD,
            transaction_type="message_reward",
            description=f"Récompense de message {message.channel.id}:{message.id}",
        )

    # ------------------------------------------------------------------
    # Commandes économie
    # ------------------------------------------------------------------
    @commands.command(name="balance", aliases=("bal",))
    async def balance(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        balance = await self.database.fetch_balance(target.id)
        embed = embeds.balance_embed(target, balance=balance)
        await ctx.send(embed=embed)

    @commands.command(name="daily")
    async def daily(self, ctx: commands.Context) -> None:
        await self.database.ensure_user(ctx.author.id)
        last_daily = await self.database.get_last_daily(ctx.author.id)
        now = datetime.now(timezone.utc)

        if last_daily and (now - last_daily).total_seconds() < DAILY_COOLDOWN:
            remaining = DAILY_COOLDOWN - (now - last_daily).total_seconds()
            embed = embeds.cooldown_embed(f"{PREFIX}daily", remaining)
            await ctx.send(embed=embed)
            return

        reward = random.randint(*DAILY_REWARD)
        before, after = await self.database.increment_balance(
            ctx.author.id,
            reward,
            transaction_type="daily",
            description="Récompense quotidienne",
        )
        await self.database.set_last_daily(ctx.author.id, now)
        logger.debug(
            "Daily claim", extra={"user_id": ctx.author.id, "reward": reward, "before": before, "after": after}
        )
        embed = embeds.daily_embed(ctx.author, amount=reward)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
