"""Fonctionnalités de statistiques d'activité pour le serveur."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from config import (
    EGG_FRENZY_LUCK_BONUS,
    EGG_LUCK_ROLE_ID,
    STATS_ACTIVE_WINDOW_DAYS,
    STATS_TOP_LIMIT,
    STEAL_PROTECTED_ROLE_ID,
    compute_steal_success_chance,
    is_egg_frenzy_active,
)
from cogs.economy import MILLIONAIRE_RACE_STAGES
from utils import embeds
from utils.enchantments import compute_egg_luck_bonus
from utils.mastery import EGG_MASTERY

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
    @commands.group(
        name="stats",
        aliases=("statistiques",),
        invoke_without_command=True,
    )
    async def player_stats(
        self, ctx: commands.Context, member: discord.Member | None = None
    ) -> None:
        target = member or ctx.author
        if not isinstance(target, discord.Member):
            await ctx.send(
                embed=embeds.error_embed(
                    "Impossible de trouver ce membre dans le serveur actuel."
                )
            )
            return

        embed = await self._build_player_stats_embed(ctx, target)
        await ctx.send(embed=embed)

    async def _build_player_stats_embed(
        self, ctx: commands.Context, member: discord.Member
    ) -> discord.Embed:
        await self.database.ensure_user(member.id)

        grade_level = await self.database.get_grade_level(member.id)
        has_protection = any(
            role.id == STEAL_PROTECTED_ROLE_ID for role in getattr(member, "roles", [])
        )
        base_steal_success = compute_steal_success_chance(
            attacker_balance=1,
            victim_balance=1,
            grade_level=grade_level,
            has_protection=False,
        )
        steal_risk = compute_steal_success_chance(
            attacker_balance=1,
            victim_balance=1,
            grade_level=grade_level,
            has_protection=has_protection,
        )

        mastery = await self.database.get_mastery_progress(member.id, EGG_MASTERY.slug)
        mastery_level = int(mastery.get("level", 1) or 1)
        mastery_bonus = 1.0 if mastery_level >= 64 else 0.0
        enchantments = await self.database.get_enchantment_powers(member.id)
        enchantment_bonus = compute_egg_luck_bonus(enchantments.get("egg_luck", 0))
        active_potion = await self.database.get_active_potion(member.id)
        potion_bonus = 0.0
        potion_label = None
        if active_potion:
            potion_definition, potion_expires_at = active_potion
            potion_bonus = max(0.0, float(potion_definition.effect_value))
            remaining_seconds = max(
                0, int((potion_expires_at - datetime.now(timezone.utc)).total_seconds())
            )
            potion_label = embeds.format_duration(remaining_seconds)
        frenzy_active = is_egg_frenzy_active()
        frenzy_bonus = EGG_FRENZY_LUCK_BONUS if frenzy_active else 0.0
        role_bonus = 0.0
        if any(role.id == EGG_LUCK_ROLE_ID for role in getattr(member, "roles", [])):
            role_bonus = 0.10

        egg_luck_total = (
            mastery_bonus
            + enchantment_bonus
            + potion_bonus
            + frenzy_bonus
            + role_bonus
        )
        luck_breakdown: list[str] = []
        if mastery_bonus:
            luck_breakdown.append(f"Maîtrise des œufs : +{mastery_bonus * 100:.0f}%")
        if enchantment_bonus:
            luck_breakdown.append(
                f"Enchantements : +{enchantment_bonus * 100:.0f}%"
            )
        if potion_bonus:
            suffix = f" ({potion_label})" if potion_label else ""
            luck_breakdown.append(
                f"Potion chance d'œuf : +{potion_bonus * 100:.0f}%{suffix}"
            )
        if frenzy_bonus:
            luck_breakdown.append(
                f"Folie des œufs : +{frenzy_bonus * 100:.0f}% (actif)"
            )
        if role_bonus:
            luck_breakdown.append(f"Rôle bonus luck : +{role_bonus * 100:.0f}%")
        if not luck_breakdown:
            luck_breakdown.append("Aucun bonus de luck actif pour le moment.")

        mastermind_wins = await self.database.get_mastermind_wins(member.id)
        race_best_stage = await self.database.get_race_personal_best(member.id)
        bounded_stage = min(
            max(0, race_best_stage), max(0, len(MILLIONAIRE_RACE_STAGES))
        )
        race_stage_label = (
            MILLIONAIRE_RACE_STAGES[bounded_stage - 1].label
            if bounded_stage >= 1 and bounded_stage <= len(MILLIONAIRE_RACE_STAGES)
            else "Aucun record pour l'instant"
        )

        return embeds.player_stats_embed(
            member=member,
            steal_success=base_steal_success,
            steal_risk=steal_risk,
            steal_protected=has_protection,
            egg_luck_total=egg_luck_total,
            egg_luck_breakdown=luck_breakdown,
            mastermind_wins=mastermind_wins,
            race_best_stage=bounded_stage,
            race_best_label=race_stage_label,
            race_total_stages=len(MILLIONAIRE_RACE_STAGES),
        )

    @player_stats.command(name="serveur", aliases=("server",))
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
