"""Système d'expérience et de niveaux."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    MAX_LEADERBOARD_LIMIT,
    XP_BASE_PER_MESSAGE,
    XP_COOLDOWN,
    XP_LEVEL_BASE,
    XP_LEVEL_MULTIPLIER,
    XP_ROLE_BOOSTS,
)
from utils import embeds

logger = logging.getLogger(__name__)


def compute_level(total_xp: int) -> int:
    """Calcule le niveau à partir de l'expérience totale."""

    level = 1
    xp_needed = XP_LEVEL_BASE
    remaining = total_xp
    while remaining >= xp_needed:
        remaining -= xp_needed
        level += 1
        xp_needed = int(xp_needed * XP_LEVEL_MULTIPLIER)
    return level


class XPCooldown:
    def __init__(self, duration: int) -> None:
        self.duration = duration
        self._storage: Dict[int, float] = {}

    def remaining(self, user_id: int) -> float:
        now = time.monotonic()
        expires = self._storage.get(user_id, 0.0)
        return max(0.0, expires - now)

    def set(self, user_id: int) -> None:
        self._storage[user_id] = time.monotonic() + self.duration

    def cleanup(self) -> None:
        now = time.monotonic()
        for user_id, expiry in list(self._storage.items()):
            if expiry <= now:
                self._storage.pop(user_id, None)


class XPSystem(commands.Cog):
    """Gestion des gains d'XP sur les messages et des classements."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.cooldown = XPCooldown(XP_COOLDOWN)
        self._cleanup_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def cog_unload(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.cooldown.cleanup()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if len(message.content) < 3:
            return
        if self.cooldown.remaining(message.author.id) > 0:
            return

        self.cooldown.set(message.author.id)
        record = await self.database.get_user_xp(message.author.id)
        boost_key = record.get("xp_boost_role")
        multiplier = XP_ROLE_BOOSTS.get(str(boost_key).upper(), 0.0) if boost_key else 0.0
        gained = int(XP_BASE_PER_MESSAGE * (1 + multiplier))

        new_total = record["total_xp"] + gained
        new_level = compute_level(new_total)
        result = await self.database.add_user_xp(message.author.id, gained, new_level=new_level)

        if result["new_level"] > result["previous_level"]:
            await message.channel.send(
                embed=embeds.success_embed(
                    f"{message.author.mention} passe niveau {result['new_level']}!",
                    title="Level Up",
                )
            )

    # ------------------------------------------------------------------
    # Commandes XP
    # ------------------------------------------------------------------
    async def send_profile(self, ctx_or_inter, member: discord.Member) -> None:
        record = await self.database.get_user_xp(member.id)
        level = compute_level(record["total_xp"])
        embed = embeds.info_embed(
            f"Niveau **{level}**\nXP total : {record['total_xp']:,}",
            title=f"Profil XP de {member.display_name}",
        )
        await ctx_or_inter.send(embed=embed)

    @commands.command(name="xp")
    async def xp_prefix(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        await self.send_profile(ctx, member or ctx.author)

    @app_commands.command(name="xp", description="Afficher ton niveau et ton XP")
    async def xp_slash(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.send_profile(interaction.followup, membre or interaction.user)

    @commands.command(name="xpleaderboard", aliases=("xplb",))
    async def xp_leaderboard_prefix(self, ctx: commands.Context, limit: int = 10) -> None:
        limit = max(1, min(limit, MAX_LEADERBOARD_LIMIT))
        rows = await self.database.get_xp_leaderboard(limit=limit)
        embed = embeds.leaderboard_embed(
            "Classement XP",
            [(row["user_id"], row["total_xp"]) for row in rows],
            self.bot,
            symbol="XP",
        )
        await ctx.send(embed=embed)

    @app_commands.command(name="xpleaderboard", description="Voir le classement XP")
    async def xp_leaderboard_slash(self, interaction: discord.Interaction, limit: int = 10) -> None:
        limit = max(1, min(limit, MAX_LEADERBOARD_LIMIT))
        rows = await self.database.get_xp_leaderboard(limit=limit)
        embed = embeds.leaderboard_embed(
            "Classement XP",
            [(row["user_id"], row["total_xp"]) for row in rows],
            self.bot,
            symbol="XP",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(XPSystem(bot))
