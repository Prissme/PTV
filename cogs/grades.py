"""Système de grades avec quêtes et récompenses progressives."""
from __future__ import annotations

import contextlib
import logging
from typing import Mapping, Optional

import discord
from discord.ext import commands

from config import (
    BASE_PET_SLOTS,
    GRADE_DEFINITIONS,
    GRADE_ROLE_IDS,
    LEADERBOARD_LIMIT,
    GradeDefinition,
)
from utils import embeds

logger = logging.getLogger(__name__)


QuestChannel = Optional[discord.abc.Messageable]


class GradeSystem(commands.Cog):
    """Gestion des grades inspirés de Pet Simulator 99."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
    @property
    def total_grades(self) -> int:
        return len(GRADE_DEFINITIONS)

    async def cog_load(self) -> None:
        logger.info("Cog Grades chargé")

    # ------------------------------------------------------------------
    # Listeners de progression
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_grade_quest_progress(
        self,
        member: discord.abc.User,
        quest_type: str,
        amount: int,
        channel: QuestChannel = None,
    ) -> None:
        resolved = await self._resolve_member(member, channel)
        await self._apply_progress(resolved or member, quest_type, amount, channel)

    async def _resolve_member(
        self, user: discord.abc.User, channel: QuestChannel
    ) -> Optional[discord.Member]:
        if isinstance(user, discord.Member):
            return user

        guild: Optional[discord.Guild] = None
        if isinstance(channel, discord.abc.GuildChannel):
            guild = channel.guild

        if guild is None:
            for candidate in self.bot.guilds:
                member = candidate.get_member(user.id)
                if member is not None:
                    return member
        else:
            member = guild.get_member(user.id)
            if member is not None:
                return member
            with contextlib.suppress(discord.HTTPException):
                fetched = await guild.fetch_member(user.id)
                if isinstance(fetched, discord.Member):
                    return fetched

        return None

    async def _apply_progress(
        self,
        member: discord.abc.User,
        quest_type: str,
        amount: int,
        channel: QuestChannel,
    ) -> None:
        if amount <= 0:
            return

        try:
            grade_row = await self.database.get_user_grade(member.id)
        except Exception:
            logger.exception("Impossible de récupérer le grade de l'utilisateur %s", member.id)
            return

        grade_level = int(grade_row["grade_level"])
        if grade_level >= self.total_grades:
            return

        definition = GRADE_DEFINITIONS[grade_level]
        progress_kwargs = {
            "mastermind_cap": definition.mastermind_goal,
            "egg_cap": definition.egg_goal,
            "sale_cap": definition.sale_goal,
            "potion_cap": definition.potion_goal,
        }

        quest_type = quest_type.lower()
        if quest_type in {"mastermind", "mm"}:
            progress_kwargs["mastermind_delta"] = amount
        elif quest_type in {"egg", "eggs", "oeuf", "oeufs"}:
            progress_kwargs["egg_delta"] = amount
        elif quest_type in {"sale", "sell", "vente", "vendre"}:
            progress_kwargs["sale_delta"] = amount
        elif quest_type in {"potion", "potions", "boire", "drink"}:
            progress_kwargs["potion_delta"] = amount
        else:
            logger.warning("Type de quête inconnu: %s", quest_type)
            return

        try:
            progress_row = await self.database.increment_grade_progress(
                member.id, **progress_kwargs
            )
        except Exception:
            logger.exception("Impossible de mettre à jour les quêtes pour %s", member.id)
            return

        if not self._is_grade_ready(progress_row, definition):
            return

        try:
            completed, new_row = await self.database.complete_grade_if_ready(
                member.id,
                mastermind_goal=definition.mastermind_goal,
                egg_goal=definition.egg_goal,
                sale_goal=definition.sale_goal,
                potion_goal=definition.potion_goal,
                max_grade=self.total_grades,
            )
        except Exception:
            logger.exception("Impossible de valider le grade pour %s", member.id)
            return

        if completed:
            new_grade_level = int(new_row["grade_level"])
            await self._handle_grade_completion(
                member,
                grade_definition=definition,
                new_grade_level=new_grade_level,
                channel=channel,
            )

    @staticmethod
    def _is_grade_ready(row: Mapping[str, object], definition: GradeDefinition) -> bool:
        return (
            int(row["mastermind_progress"]) >= definition.mastermind_goal
            and int(row["egg_progress"]) >= definition.egg_goal
            and int(row["sale_progress"]) >= definition.sale_goal
            and int(row["potion_progress"]) >= definition.potion_goal
        )

    async def _handle_grade_completion(
        self,
        member: discord.abc.User,
        *,
        grade_definition: GradeDefinition,
        new_grade_level: int,
        channel: QuestChannel,
    ) -> None:
        reward = grade_definition.reward_pb
        pet_slots = BASE_PET_SLOTS + new_grade_level

        try:
            _, balance_after = await self.database.increment_balance(
                member.id,
                reward,
                transaction_type="grade_reward",
                description=f"Grade {new_grade_level}",
            )
        except Exception:
            logger.exception("Impossible de créditer la récompense de grade pour %s", member.id)
            balance_after = 0

        await self._assign_grade_role(member, new_grade_level)

        embed = embeds.grade_completed_embed(
            member=member,
            grade_name=grade_definition.name,
            grade_level=new_grade_level,
            total_grades=self.total_grades,
            reward_pb=reward,
            balance_after=balance_after,
            pet_slots=pet_slots,
        )

        destination: QuestChannel = channel
        if destination is None:
            with contextlib.suppress(discord.HTTPException):
                destination = await member.create_dm()
            if destination is None and isinstance(member, discord.Member):
                if member.guild and member.guild.system_channel:
                    destination = member.guild.system_channel

        if destination is not None:
            with contextlib.suppress(discord.HTTPException):
                await destination.send(embed=embed)

    async def _assign_grade_role(self, member: discord.abc.User, grade_level: int) -> None:
        if not isinstance(member, discord.Member) or member.guild is None:
            return
        roles_to_remove = []
        target_role: Optional[discord.Role] = None

        for index, role_id in enumerate(GRADE_ROLE_IDS, start=1):
            role = member.guild.get_role(role_id)
            if role is None:
                continue
            if index == grade_level:
                target_role = role
            elif role in member.roles:
                roles_to_remove.append(role)

        if roles_to_remove:
            with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                await member.remove_roles(*roles_to_remove, reason="Mise à jour des grades EcoBot")

        if target_role and target_role not in member.roles:
            with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                await member.add_roles(target_role, reason="Grade EcoBot débloqué")

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------
    @commands.command(name="grade", aliases=("rank",))
    async def grade_command(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ) -> None:
        target = member or ctx.author
        if not isinstance(target, discord.Member):
            await ctx.send(embed=embeds.error_embed("Commande utilisable uniquement sur le serveur."))
            return

        try:
            row = await self.database.get_user_grade(target.id)
        except Exception:
            await ctx.send(embed=embeds.error_embed("Impossible de récupérer les informations de grade."))
            logger.exception("Erreur lors de la récupération du grade pour %s", target.id)
            return

        grade_level = int(row["grade_level"])
        current_grade = (
            GRADE_DEFINITIONS[grade_level - 1] if grade_level > 0 else None
        )
        next_grade = (
            GRADE_DEFINITIONS[grade_level]
            if grade_level < self.total_grades
            else None
        )

        if next_grade is not None and self._is_grade_ready(row, next_grade):
            try:
                completed, new_row = await self.database.complete_grade_if_ready(
                    target.id,
                    mastermind_goal=next_grade.mastermind_goal,
                    egg_goal=next_grade.egg_goal,
                    sale_goal=next_grade.sale_goal,
                    potion_goal=next_grade.potion_goal,
                    max_grade=self.total_grades,
                )
            except Exception:
                logger.exception("Impossible de valider le grade pour %s", target.id)
            else:
                if completed:
                    earned_grade = next_grade
                    row = new_row
                    grade_level = int(row["grade_level"])
                    current_grade = (
                        GRADE_DEFINITIONS[grade_level - 1]
                        if grade_level > 0
                        else None
                    )
                    next_grade = (
                        GRADE_DEFINITIONS[grade_level]
                        if grade_level < self.total_grades
                        else None
                    )
                    if earned_grade is not None:
                        await self._handle_grade_completion(
                            target,
                            grade_definition=earned_grade,
                            new_grade_level=grade_level,
                            channel=ctx.channel,
                        )

        embed = embeds.grade_profile_embed(
            member=target,
            grade_level=grade_level,
            total_grades=self.total_grades,
            current_grade=current_grade,
            next_grade=next_grade,
            progress={
                "mastermind": int(row["mastermind_progress"]),
                "eggs": int(row["egg_progress"]),
                "sales": int(row["sale_progress"]),
                "potions": int(row["potion_progress"]),
            },
            pet_slots=BASE_PET_SLOTS + grade_level,
        )
        await ctx.send(embed=embed)

    @commands.command(name="gradeleaderboard", aliases=("gradelb", "xpleaderboard", "xplb"))
    async def grade_leaderboard(self, ctx: commands.Context) -> None:
        try:
            rows = await self.database.get_grade_leaderboard(LEADERBOARD_LIMIT)
        except Exception:
            await ctx.send(embed=embeds.error_embed("Impossible de récupérer le classement des grades."))
            logger.exception("Erreur lors de la récupération du classement des grades")
            return

        embed = embeds.leaderboard_embed(
            title="Classement des grades",
            entries=[(int(row["user_id"]), int(row["grade_level"])) for row in rows],
            bot=self.bot,
            symbol="Grade",
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GradeSystem(bot))
