"""Système de grades avec quêtes et récompenses progressives."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Mapping, Optional

import discord
from discord.ext import commands

from config import (
    BASE_PET_SLOTS,
    CACHE_TTL_PROFILE,
    DAILY_COOLDOWN,
    DAILY_GEMS_BASE,
    DAILY_GEMS_CAP,
    DAILY_REWARD,
    DEBUG_CACHE,
    GRADE_DEFINITIONS,
    GRADE_ROLE_IDS,
    LEADERBOARD_LIMIT,
    GradeDefinition,
    ANIMALERIE_ZONE_SLUG,
    ROBOT_ZONE_SLUG,
    QUEST_WEEKLY_RESET_WEEKDAY,
    QUEST_WEEKLY_RESET_HOUR,
)
from database.db import DatabaseError
from utils import embeds
from utils.cache import TTLCache

logger = logging.getLogger(__name__)


QuestChannel = Optional[discord.abc.Messageable]


class GradeSystem(commands.Cog):
    """Gestion des grades inspirés de Pet Simulator 99."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._profile_cache = TTLCache[dict[str, object]](CACHE_TTL_PROFILE)

    class RebirthConfirmView(discord.ui.View):
        def __init__(self, author_id: int) -> None:
            super().__init__(timeout=60)
            self.author_id = int(author_id)
            self.value: Optional[bool] = None

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "Seule la personne qui a lancé la commande peut confirmer ce rebirth.",
                    ephemeral=True,
                )
                return False
            return True

        @discord.ui.button(label="Recommencer", style=discord.ButtonStyle.danger)
        async def confirm(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ) -> None:
            self.value = True
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()

        @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
        async def cancel(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ) -> None:
            self.value = False
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(view=self)
            self.stop()
    @property
    def total_grades(self) -> int:
        return len(GRADE_DEFINITIONS)

    async def cog_load(self) -> None:
        logger.info("Cog Grades chargé")

    async def _ack_heavy_command(self, ctx: commands.Context) -> None:
        interaction = getattr(ctx, "interaction", None)
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.defer()
            return
        with contextlib.suppress(discord.HTTPException, AttributeError):
            async with ctx.typing():
                return
        with contextlib.suppress(discord.HTTPException):
            async with ctx.channel.typing():
                return

    async def _get_profile_snapshot(self, user_id: int) -> dict[str, object]:
        cached = self._profile_cache.get(user_id)
        if cached is not None:
            return cached
        try:
            grade_row, rap_total, daily_state = await asyncio.gather(
                self.database.get_user_grade(user_id),
                self.database.get_user_pet_rap(user_id),
                self.database.get_daily_state(user_id),
            )
        except Exception:
            logger.exception("Impossible de charger le profil de quêtes pour %s", user_id)
            raise
        last_daily, daily_streak = daily_state
        snapshot = {
            "grade_row": grade_row,
            "rap_total": rap_total,
            "last_daily": last_daily,
            "daily_streak": daily_streak,
        }
        self._profile_cache.set(user_id, snapshot)
        if DEBUG_CACHE:
            logger.debug("Cache profil mis à jour", extra={"user_id": user_id})
        return snapshot

    @staticmethod
    def _format_progress_line(label: str, current: int, goal: int) -> str:
        if goal <= 0:
            return f"✅ {label} : aucune exigence"
        status = "✅" if current >= goal else "▫️"
        return f"{status} {label} : **{current}/{goal}**"

    @staticmethod
    def _format_currency_progress_line(label: str, current: int, goal: int, formatter) -> str:
        if goal <= 0:
            return f"✅ {label} : aucune exigence"
        status = "✅" if current >= goal else "▫️"
        return f"{status} {label} : **{formatter(current)} / {formatter(goal)}**"

    @staticmethod
    def _next_weekly_reset(now: datetime) -> datetime:
        weekday = max(0, min(6, QUEST_WEEKLY_RESET_WEEKDAY))
        hour = max(0, min(23, QUEST_WEEKLY_RESET_HOUR))
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        days_ahead = (weekday - target.weekday()) % 7
        if days_ahead == 0 and target <= now:
            days_ahead = 7
        return target + timedelta(days=days_ahead)

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
            "casino_loss_cap": definition.casino_loss_goal,
            "potion_cap": definition.potion_goal,
        }

        quest_type = quest_type.lower()
        if quest_type in {"mastermind", "mm"}:
            progress_kwargs["mastermind_delta"] = amount
        elif quest_type in {"egg", "eggs", "oeuf", "oeufs"}:
            progress_kwargs["egg_delta"] = amount
        elif quest_type in {"casino", "casino_loss", "perte", "casino-perte"}:
            progress_kwargs["casino_loss_delta"] = amount
        elif quest_type in {"rap"}:
            # Aucun suivi direct en base : le RAP est recalculé dynamiquement.
            pass
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

        try:
            rap_total = await self.database.get_user_pet_rap(member.id)
        except Exception:
            logger.exception("Impossible de récupérer le RAP pour %s", member.id)
            rap_total = 0

        if not self._is_grade_ready(progress_row, definition, rap_total=rap_total):
            return

        try:
            completed, new_row = await self.database.complete_grade_if_ready(
                member.id,
                mastermind_goal=definition.mastermind_goal,
                egg_goal=definition.egg_goal,
                casino_loss_goal=definition.casino_loss_goal,
                potion_goal=definition.potion_goal,
                rap_goal=definition.rap_goal,
                current_rap=rap_total,
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
    def _is_grade_ready(
        row: Mapping[str, object],
        definition: GradeDefinition,
        *,
        rap_total: int,
    ) -> bool:
        return (
            int(row["mastermind_progress"]) >= definition.mastermind_goal
            and int(row["egg_progress"]) >= definition.egg_goal
            and int(row["sale_progress"]) >= definition.casino_loss_goal
            and int(row["potion_progress"]) >= definition.potion_goal
            and rap_total >= definition.rap_goal
        )

    async def _handle_grade_completion(
        self,
        member: discord.abc.User,
        *,
        grade_definition: GradeDefinition,
        new_grade_level: int,
        channel: QuestChannel,
    ) -> None:
        reward = grade_definition.reward_gems
        pet_slots: int
        if hasattr(self.database, "get_pet_slot_limit"):
            pet_slots = await self.database.get_pet_slot_limit(member.id)
        else:
            pet_slots = BASE_PET_SLOTS + new_grade_level

        try:
            _, gems_after = await self.database.increment_gems(
                member.id,
                reward,
                transaction_type="grade_reward",
                description=f"Grade {new_grade_level}",
            )
        except Exception:
            logger.exception("Impossible de créditer la récompense de grade pour %s", member.id)
            gems_after = 0

        await self._assign_grade_role(member, new_grade_level)

        embed = embeds.grade_completed_embed(
            member=member,
            grade_name=grade_definition.name,
            grade_level=new_grade_level,
            total_grades=self.total_grades,
            reward_gems=reward,
            gems_after=gems_after,
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
    @commands.command(name="grade")
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

        try:
            rap_total = await self.database.get_user_pet_rap(target.id)
        except Exception:
            logger.exception("Impossible de récupérer le RAP pour %s", target.id)
            rap_total = 0

        if next_grade is not None and self._is_grade_ready(row, next_grade, rap_total=rap_total):
            try:
                completed, new_row = await self.database.complete_grade_if_ready(
                    target.id,
                    mastermind_goal=next_grade.mastermind_goal,
                    egg_goal=next_grade.egg_goal,
                    casino_loss_goal=next_grade.casino_loss_goal,
                    potion_goal=next_grade.potion_goal,
                    rap_goal=next_grade.rap_goal,
                    current_rap=rap_total,
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
                    try:
                        rap_total = await self.database.get_user_pet_rap(target.id)
                    except Exception:
                        logger.exception("Impossible de récupérer le RAP pour %s", target.id)
                        rap_total = 0
                    if earned_grade is not None:
                        await self._handle_grade_completion(
                            target,
                            grade_definition=earned_grade,
                            new_grade_level=grade_level,
                            channel=ctx.channel,
                        )

        if hasattr(self.database, "get_pet_slot_limit"):
            slot_limit = await self.database.get_pet_slot_limit(target.id)
        else:
            slot_limit = BASE_PET_SLOTS + grade_level

        embed = embeds.grade_profile_embed(
            member=target,
            grade_level=grade_level,
            total_grades=self.total_grades,
            current_grade=current_grade,
            next_grade=next_grade,
            progress={
                "mastermind": int(row["mastermind_progress"]),
                "eggs": int(row["egg_progress"]),
                "casino_losses": int(row["sale_progress"]),
                "potions": int(row["potion_progress"]),
            },
            rap_total=rap_total,
            pet_slots=slot_limit,
        )
        await ctx.send(embed=embed)

    async def _send_quests_overview(
        self, ctx: commands.Context, target: discord.Member
    ) -> None:
        await self._ack_heavy_command(ctx)
        try:
            snapshot = await self._get_profile_snapshot(target.id)
        except Exception:
            await ctx.send(embed=embeds.error_embed("Impossible de récupérer les quêtes pour le moment."))
            return

        grade_row = snapshot["grade_row"]
        grade_level = int(grade_row["grade_level"])
        current_grade = GRADE_DEFINITIONS[grade_level - 1] if grade_level > 0 else None
        next_grade = GRADE_DEFINITIONS[grade_level] if grade_level < self.total_grades else None
        rap_total = int(snapshot.get("rap_total", 0) or 0)

        progression_lines = []
        if next_grade is None:
            progression_lines.append("✅ Toutes les quêtes de progression sont terminées.")
            reward_line = "🎉 Tu as atteint le grade maximum."
        else:
            progression_lines = [
                self._format_progress_line(
                    "Gagner des parties de Mastermind",
                    int(grade_row["mastermind_progress"]),
                    next_grade.mastermind_goal,
                ),
                self._format_progress_line(
                    "Ouvrir des œufs",
                    int(grade_row["egg_progress"]),
                    next_grade.egg_goal,
                ),
                self._format_currency_progress_line(
                    "Atteindre un RAP total",
                    rap_total,
                    next_grade.rap_goal,
                    embeds.format_gems,
                ),
                self._format_currency_progress_line(
                    "Perdre des Prissbucks au casino",
                    int(grade_row["sale_progress"]),
                    next_grade.casino_loss_goal,
                    embeds.format_currency,
                ),
                self._format_progress_line(
                    "Boire des potions",
                    int(grade_row["potion_progress"]),
                    next_grade.potion_goal,
                ),
            ]
            reward_line = (
                f"Prochaine récompense : **{embeds.format_gems(next_grade.reward_gems)}** + 1 slot"
            )

        now = datetime.now(timezone.utc)
        last_daily = snapshot.get("last_daily")
        daily_streak = int(snapshot.get("daily_streak") or 0)
        daily_lines = []
        if isinstance(last_daily, datetime):
            elapsed = (now - last_daily).total_seconds()
        else:
            elapsed = None
        if elapsed is None or elapsed >= DAILY_COOLDOWN:
            daily_lines.append("✅ Récompense quotidienne disponible.")
        else:
            remaining = max(0, DAILY_COOLDOWN - elapsed)
            daily_lines.append(f"⏳ Disponible dans {embeds._format_duration(remaining)}")
        daily_lines.append(
            f"Récompense : {embeds.format_currency(DAILY_REWARD[0])}"
            f"–{embeds.format_currency(DAILY_REWARD[1])}"
        )
        if DAILY_GEMS_CAP > 0:
            daily_lines.append(
                f"Gemmes : {embeds.format_gems(DAILY_GEMS_BASE)}"
                f" + bonus (cap {embeds.format_gems(DAILY_GEMS_CAP)})"
            )
        daily_lines.append(f"Streak actuelle : **{daily_streak}**")

        weekly_lines = ["Aucune quête hebdomadaire active pour le moment."]
        next_weekly = self._next_weekly_reset(now)
        weekly_remaining = (next_weekly - now).total_seconds()
        weekly_lines.append(f"Prochaine rotation : {embeds._format_duration(weekly_remaining)}")

        title_suffix = f" — {current_grade.name}" if current_grade else ""
        embed = embeds.quests_embed(
            member=target,
            daily_lines=daily_lines,
            weekly_lines=weekly_lines,
            progression_lines=progression_lines,
            reward_line=reward_line,
        )
        if title_suffix:
            embed.title = f"{embed.title}{title_suffix}"
        await ctx.send(embed=embed)

    @commands.command(name="quests", aliases=("quest", "missions"))
    async def quests_command(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ) -> None:
        target = member or ctx.author
        if not isinstance(target, discord.Member):
            await ctx.send(embed=embeds.error_embed("Commande utilisable uniquement sur le serveur."))
            return
        await self._send_quests_overview(ctx, target)

    @commands.command(name="profile", aliases=("profil",))
    async def profile_command(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ) -> None:
        target = member or ctx.author
        if not isinstance(target, discord.Member):
            await ctx.send(embed=embeds.error_embed("Commande utilisable uniquement sur le serveur."))
            return
        await self._send_quests_overview(ctx, target)

    @commands.command(name="rank")
    async def rank_command(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ) -> None:
        target = member or ctx.author
        if not isinstance(target, discord.Member):
            await ctx.send(embed=embeds.error_embed("Commande utilisable uniquement sur le serveur."))
            return

        try:
            balance, gems, best_pet, rank_data = await asyncio.gather(
                self.database.fetch_balance(target.id),
                self.database.fetch_gems(target.id),
                self.database.get_user_best_pet_value(target.id),
                self.database.get_user_pet_rap_rank(target.id),
            )
        except Exception:
            logger.exception("Impossible de récupérer les stats pour %s", target.id)
            await ctx.send(embed=embeds.error_embed("Impossible de récupérer les statistiques du joueur."))
            return

        best_pet_name, best_pet_value = best_pet
        embed = embeds.rank_profile_embed(
            member=target,
            balance=int(balance),
            gems=int(gems),
            rap_total=int(rank_data.get("rap_total", 0)),
            best_pet_name=best_pet_name,
            best_pet_value=int(best_pet_value),
            rap_rank=int(rank_data.get("rank", 0)),
            rap_total_players=int(rank_data.get("total", 0)),
        )
        await ctx.send(embed=embed)

    @commands.command(name="rebirth")
    async def rebirth(self, ctx: commands.Context) -> None:
        if not isinstance(ctx.author, discord.Member):
            await ctx.send(
                embed=embeds.error_embed(
                    "Le rebirth doit être effectué depuis le serveur principal."
                )
            )
            return

        grade_level = await self.database.get_grade_level(ctx.author.id)
        rebirth_count = await self.database.get_rebirth_count(ctx.author.id)

        if grade_level < self.total_grades:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Atteins d'abord le grade maximum avant de pouvoir rebirth."
                )
            )
            return

        if rebirth_count >= 2:
            await ctx.send(
                embed=embeds.error_embed(
                    "Tu as atteint la limite de rebirth disponible."
                )
            )
            return

        required_zone = None
        required_label = None
        if rebirth_count == 0:
            required_zone = ROBOT_ZONE_SLUG
            required_label = "la Zone Robotique"
        elif rebirth_count == 1:
            required_zone = ANIMALERIE_ZONE_SLUG
            required_label = "l'Animalerie"

        if required_zone is not None:
            has_zone = await self.database.has_unlocked_zone(ctx.author.id, required_zone)
            if not has_zone:
                await ctx.send(
                    embed=embeds.warning_embed(
                        f"Atteins {required_label} avant de lancer ce rebirth."
                    )
                )
                return
        description_lines = [
            f"• Tu es sur le point de lancer ton rebirth #{rebirth_count + 1}",
            "• Retour à la zone 1 et au grade 1",
            "• Tes PB, potions actives et tickets de tombola seront perdus",
            "• Tes pets seront tous supprimés, sauf tes Huge et Titanic",
            "• Bonus permanent : +50% de PB gagnés",
            "• Accès au gold garanti en payant 100× le prix d'un œuf",
            "• Double ouverture récapitulée dans un seul embed",
            "• Nouvelle zone : *Coming soon*",
            "• Limite absolue : 2 rebirths par joueur",
        ]
        if required_label is not None:
            description_lines.append(f"• Condition de ce rebirth : atteindre {required_label}")
        prompt_embed = embeds.warning_embed(
            "\n".join(description_lines),
            title="Confirmer le rebirth ?",
        )
        prompt_embed.set_author(
            name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url
        )
        prompt_embed.set_footer(text="Cette action est irréversible.")

        view = self.RebirthConfirmView(ctx.author.id)
        message = await ctx.send(embed=prompt_embed, view=view)
        await view.wait()

        if view.value is not True:
            await message.edit(view=None)
            if view.value is False:
                await ctx.send(embed=embeds.info_embed("Rebirth annulé."))
            else:
                await ctx.send(
                    embed=embeds.info_embed(
                        "Temps écoulé, rebirth annulé.", title="Rebirth"
                    )
                )
            return

        try:
            new_count = await self.database.perform_rebirth(ctx.author.id)
        except DatabaseError as exc:
            logger.exception("Erreur lors du rebirth pour %s", ctx.author.id)
            await ctx.send(
                embed=embeds.error_embed(
                    "Impossible de finaliser le rebirth : {message}".format(message=str(exc))
                )
            )
            return
        if isinstance(ctx.author, discord.Member):
            await self._assign_grade_role(ctx.author, 1)

        multiplier = 1.0 + 0.5 * new_count
        success_lines = [
            "✅ Nouveau départ en zone 1, grade 1",
            "• Solde, potions et tickets ont été réinitialisés",
            "• Tous tes pets non-Huge ont été relâchés",
            "• Tes Huge et Titanic ont été préservés",
            f"• Multiplicateur PB actuel : x{multiplier:.1f}",
            "• Utilise `e!openbox <œuf> gold` pour un pet or garanti",
            "• Les doubles ouvertures s'affichent maintenant dans un seul embed",
            "• Zone suivante : *Coming soon*",
        ]
        success_embed = embeds.success_embed(
            "\n".join(success_lines),
            title=f"Rebirth #{new_count} terminé !",
        )
        success_embed.set_author(
            name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url
        )
        await message.edit(view=None)
        await ctx.send(embed=success_embed)

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
