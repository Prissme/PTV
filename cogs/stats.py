"""Fonctionnalités de statistiques d'activité pour le serveur."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from config import STATS_ACTIVE_WINDOW_DAYS, STATS_TOP_LIMIT
from utils import embeds

logger = logging.getLogger(__name__)


class ActivityStats(commands.Cog):
    """Collecte et expose les statistiques de messages du serveur."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._active_window = timedelta(days=STATS_ACTIVE_WINDOW_DAYS)

    @property
    def active_window(self) -> timedelta:
        return self._active_window

    # ------------------------------------------------------------------
    # Collecte d'activité
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        try:
            await self.database.record_message_activity(message.guild.id, message.author.id)
        except Exception:
            logger.exception(
                "Impossible d'enregistrer l'activité de %s dans le serveur %s",
                message.author.id,
                message.guild.id,
            )

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------
    @commands.command(name="stats", aliases=("statistiques",))
    async def guild_stats(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send(embed=embeds.error_embed("Cette commande doit être utilisée dans un serveur."))
            return

        active_since = datetime.now(timezone.utc) - self.active_window
        try:
            total_messages, active_members, tracked_members = await self.database.get_guild_activity_overview(
                ctx.guild.id, active_since=active_since
            )
        except Exception:
            logger.exception("Impossible de récupérer les statistiques pour le serveur %s", ctx.guild.id)
            await ctx.send(embed=embeds.error_embed("Impossible de récupérer les statistiques pour le moment."))
            return

        embed = embeds.stats_overview_embed(
            guild=ctx.guild,
            total_messages=total_messages,
            active_members=active_members,
            tracked_members=tracked_members,
            active_window=self.active_window,
        )
        await ctx.send(embed=embed)

    @commands.command(name="topactifs", aliases=("topactive", "topmessages", "topmsg"))
    async def top_active_members(self, ctx: commands.Context, limit: int | None = None) -> None:
        if ctx.guild is None:
            await ctx.send(embed=embeds.error_embed("Cette commande doit être utilisée dans un serveur."))
            return

        resolved_limit = limit if limit is not None else STATS_TOP_LIMIT
        resolved_limit = max(1, min(50, resolved_limit))

        try:
            rows = await self.database.get_top_message_senders(ctx.guild.id, limit=resolved_limit)
        except Exception:
            logger.exception("Impossible de récupérer le top actifs pour le serveur %s", ctx.guild.id)
            await ctx.send(embed=embeds.error_embed("Impossible de récupérer le classement des membres actifs."))
            return

        if not rows:
            await ctx.send(embed=embeds.info_embed("Aucune activité suivie pour le moment.", title="📊 Statistiques"))
            return

        embed = embeds.leaderboard_embed(
            title=f"Top {len(rows)} des membres les plus actifs",
            entries=[(user_id, count) for user_id, count in rows],
            bot=self.bot,
            symbol="messages",
        )
        embed.set_footer(text="Classement basé sur le nombre total de messages suivis.")
        await ctx.send(embed=embed)

    @commands.command(name="mystats", aliases=("monactivite", "myactivity"))
    async def user_stats(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        if ctx.guild is None:
            await ctx.send(embed=embeds.error_embed("Cette commande doit être utilisée dans un serveur."))
            return

        target = member or ctx.author
        if not isinstance(target, discord.Member) or target.guild != ctx.guild:
            await ctx.send(embed=embeds.error_embed("Ce membre n'appartient pas à ce serveur."))
            return

        try:
            row = await self.database.get_user_activity_details(ctx.guild.id, target.id)
        except Exception:
            logger.exception(
                "Impossible de récupérer les statistiques pour l'utilisateur %s dans le serveur %s",
                target.id,
                ctx.guild.id,
            )
            await ctx.send(embed=embeds.error_embed("Impossible de récupérer les statistiques de ce membre."))
            return

        if row is None:
            if target == ctx.author:
                message = "Aucune activité suivie pour toi pour le moment."
            else:
                message = f"Aucune activité suivie pour {target.display_name}."
            await ctx.send(embed=embeds.info_embed(message, title="📊 Statistiques"))
            return

        embed = embeds.user_activity_embed(
            member=target,
            message_count=int(row["message_count"]),
            last_message_at=row["last_message_at"],
            rank=int(row["rank"]),
            total_tracked=int(row["total_tracked"]),
            active_window=self.active_window,
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActivityStats(bot))
