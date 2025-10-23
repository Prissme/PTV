"""Système de grades avec quêtes et récompenses progressives."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Dict, Mapping, Optional

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
        self._invite_cache: Dict[int, Dict[str, int]] = {}
        self._invite_lock = asyncio.Lock()

    @property
    def total_grades(self) -> int:
        return len(GRADE_DEFINITIONS)

    async def cog_load(self) -> None:
        await self._sync_all_invites()
        logger.info("Cog Grades chargé")

    # ------------------------------------------------------------------
    # Gestion des invites (pour la quête "Inviter")
    # ------------------------------------------------------------------
    async def _sync_all_invites(self) -> None:
        if not self.bot.guilds:
            return
        for guild in self.bot.guilds:
            await self._sync_guild_invites(guild)

    async def _sync_guild_invites(self, guild: discord.Guild) -> None:
        if guild is None:
            return
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return
        async with self._invite_lock:
            self._invite_cache[guild.id] = {
                invite.code: invite.uses or 0 for invite in invites
            }

    async def _resolve_inviter(self, guild: discord.Guild) -> Optional[int]:
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None

        async with self._invite_lock:
            previous = self._invite_cache.get(guild.id, {})
            self._invite_cache[guild.id] = {
                invite.code: invite.uses or 0 for invite in invites
            }

        for invite in invites:
            previous_uses = previous.get(invite.code, 0)
            current_uses = invite.uses or 0
            if current_uses > previous_uses and invite.inviter:
                return invite.inviter.id
        return None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._sync_all_invites()

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        async with self._invite_lock:
            cache = self._invite_cache.setdefault(invite.guild.id, {})
            cache[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        if invite.guild is None:
            return
        async with self._invite_lock:
            cache = self._invite_cache.get(invite.guild.id)
            if cache is not None:
                cache.pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        inviter_id = await self._resolve_inviter(member.guild)
        if inviter_id is None:
            return

        inviter: Optional[discord.Member] = member.guild.get_member(inviter_id)
        if inviter is None:
            with contextlib.suppress(discord.HTTPException):
                inviter = await member.guild.fetch_member(inviter_id)
        if inviter is None:
            return

        await self._apply_progress(inviter, "invite", 1, None)

    # ------------------------------------------------------------------
    # Listeners de progression
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not isinstance(message.author, discord.Member):
            return
        if message.guild is None or not message.content.strip():
            return
        await self._apply_progress(message.author, "message", 1, message.channel)

    @commands.Cog.listener()
    async def on_grade_quest_progress(
        self,
        member: discord.Member,
        quest_type: str,
        amount: int,
        channel: QuestChannel = None,
    ) -> None:
        if not isinstance(member, discord.Member):
            return
        await self._apply_progress(member, quest_type, amount, channel)

    async def _apply_progress(
        self,
        member: discord.Member,
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
            "message_cap": definition.message_goal,
            "invite_cap": definition.invite_goal,
            "egg_cap": definition.egg_goal,
        }

        quest_type = quest_type.lower()
        if quest_type in {"message", "messages"}:
            progress_kwargs["message_delta"] = amount
        elif quest_type in {"invite", "invites"}:
            progress_kwargs["invite_delta"] = amount
        elif quest_type in {"egg", "eggs", "oeuf", "oeufs"}:
            progress_kwargs["egg_delta"] = amount
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
                message_goal=definition.message_goal,
                invite_goal=definition.invite_goal,
                egg_goal=definition.egg_goal,
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
            int(row["message_progress"]) >= definition.message_goal
            and int(row["invite_progress"]) >= definition.invite_goal
            and int(row["egg_progress"]) >= definition.egg_goal
        )

    async def _handle_grade_completion(
        self,
        member: discord.Member,
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
            if destination is None and member.guild and member.guild.system_channel:
                destination = member.guild.system_channel

        if destination is not None:
            with contextlib.suppress(discord.HTTPException):
                await destination.send(embed=embed)

    async def _assign_grade_role(self, member: discord.Member, grade_level: int) -> None:
        if member.guild is None:
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
        embed = embeds.grade_profile_embed(
            member=target,
            grade_level=grade_level,
            total_grades=self.total_grades,
            current_grade=current_grade,
            next_grade=next_grade,
            progress={
                "messages": int(row["message_progress"]),
                "invites": int(row["invite_progress"]),
                "eggs": int(row["egg_progress"]),
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
