"""Gestion de l'expérience : gains automatiques et classements XP."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time

import discord
from discord.ext import commands

from config import LEADERBOARD_LIMIT, XP_COOLDOWN, XP_LEVEL_BASE, XP_LEVEL_MULTIPLIER, XP_PER_MESSAGE
from utils import embeds

logger = logging.getLogger(__name__)


def compute_level(total_xp: int) -> int:
    """Calcule le niveau en appliquant une progression exponentielle simple."""

    level = 1
    requirement = XP_LEVEL_BASE
    remaining = total_xp

    while remaining >= requirement:
        remaining -= requirement
        level += 1
        requirement = int(requirement * XP_LEVEL_MULTIPLIER)
    return level


class CooldownCache:
    """Cache en mémoire pour limiter les gains d'XP."""

    def __init__(self, duration: int) -> None:
        self.duration = duration
        self._storage: dict[int, float] = {}

    def remaining(self, user_id: int) -> float:
        return max(0.0, self._storage.get(user_id, 0.0) - time.monotonic())

    def trigger(self, user_id: int) -> None:
        self._storage[user_id] = time.monotonic() + self.duration

    def cleanup(self) -> None:
        now = time.monotonic()
        for user_id, expiry in list(self._storage.items()):
            if expiry <= now:
                self._storage.pop(user_id, None)


class XPSystem(commands.Cog):
    """Système XP minimaliste : gain automatique, profil et classement."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.cooldown = CooldownCache(XP_COOLDOWN)
        self._cleanup_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Cog XP chargé")

    async def cog_unload(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.cooldown.cleanup()

    # ------------------------------------------------------------------
    # Gestion des gains automatiques
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if not message.content.strip():
            return
        if self.cooldown.remaining(message.author.id) > 0:
            return

        self.cooldown.trigger(message.author.id)
        await self.database.ensure_user(message.author.id)

        gain = random.randint(*XP_PER_MESSAGE)
        total_xp, current_level = await self.database.get_user_xp(message.author.id)
        new_total = total_xp + gain
        new_level = compute_level(new_total)

        previous_total, previous_level = await self.database.update_user_xp(
            message.author.id,
            total_xp=new_total,
            level=new_level,
        )
        logger.debug(
            "XP gain",
            extra={
                "user_id": message.author.id,
                "gain": gain,
                "total_before": previous_total,
                "total_after": new_total,
                "level_before": previous_level,
                "level_after": new_level,
            },
        )

        if new_level > current_level:
            await message.channel.send(
                embed=embeds.success_embed(
                    f"{message.author.mention} est désormais niveau {new_level}!",
                    title="Level up",
                )
            )

    # ------------------------------------------------------------------
    # Commandes XP
    # ------------------------------------------------------------------
    @commands.command(name="rank")
    async def rank(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        total_xp, level = await self.database.get_user_xp(target.id)
        next_level_requirement = _next_requirement(level)
        embed = embeds.xp_profile_embed(
            member=target,
            level=level,
            total_xp=total_xp,
            next_requirement=next_level_requirement,
        )
        await ctx.send(embed=embed)

    @commands.command(name="xpleaderboard", aliases=("xplb",))
    async def xp_leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.database.get_xp_leaderboard(LEADERBOARD_LIMIT)
        embed = embeds.leaderboard_embed(
            title="Classement XP",
            entries=[(row["user_id"], row["total_xp"]) for row in rows],
            bot=self.bot,
            symbol="XP",
        )
        await ctx.send(embed=embed)


def _next_requirement(level: int) -> int:
    """Calcule l'XP nécessaire pour atteindre le niveau suivant."""

    requirement = XP_LEVEL_BASE
    for _ in range(1, level):
        requirement = int(requirement * XP_LEVEL_MULTIPLIER)
    return requirement


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(XPSystem(bot))
