"""Fonctionnalités économiques essentielles : balance, daily et récompenses."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Mapping, Optional, Sequence

import discord
from discord.abc import Messageable
from discord.ext import commands, tasks

from config import (
    Colors,
    DAILY_COOLDOWN,
    DAILY_REWARD,
    GRADE_DEFINITIONS,
    HUGE_GALE_NAME,
    HUGE_BULL_NAME,
    HUGE_BO_NAME,
    HUGE_KENJI_ONI_NAME,
    HUGE_MORTIS_NAME,
    HUGE_PET_MIN_INCOME,
    MASTERMIND_MASTERY_MAX_ROLE_ID,
    MESSAGE_COOLDOWN,
    MESSAGE_REWARD,
    PET_DEFINITIONS,
    PET_EMOJIS,
    POTION_DEFINITION_MAP,
    POTION_DEFINITIONS,
    PREFIX,
    TITANIC_GRIFF_NAME,
    VIP_ROLE_ID,
    get_huge_level_multiplier,
)
from utils import embeds
from database.db import Database, DatabaseError, InsufficientBalanceError
from utils.mastery import MASTERMIND_MASTERY, MasteryDefinition

logger = logging.getLogger(__name__)


SLOT_REELS: tuple[str, ...] = ("🍒", "🍋", "🍇", "🔔", "⭐", "💎", "7️⃣")
SLOT_WEIGHTS: tuple[int, ...] = (24, 22, 18, 12, 10, 8, 6)
SLOT_TRIPLE_REWARDS: dict[str, tuple[int, str]] = {
    "🍒": (4, "Triplé de 🍒 ! C'est juteux."),
    "🍋": (5, "Un trio acidulé de 🍋 !"),
    "🍇": (6, "Raisin royal 🍇🍇🍇 !"),
    "🔔": (8, "Les cloches 🔔🔔🔔 sonnent la victoire !"),
    "⭐": (12, "Étoiles alignées ⭐⭐⭐ !"),
    "💎": (18, "Pluie de diamants 💎💎💎 !"),
    "7️⃣": (25, "Jackpot 7️⃣7️⃣7️⃣ !"),
}
SLOT_PAIR_REWARDS: dict[str, tuple[int, str]] = {
    "🍒": (1, "Paire de 🍒 — mise sauvée !"),
    "🍋": (1, "Une paire de 🍋, ça passe tout juste."),
    "🍇": (1, "Deux 🍇 pour rester à flot."),
    "🔔": (2, "Deux cloches 🔔, ça rapporte."),
    "⭐": (2, "Deux ⭐ scintillent pour toi."),
    "💎": (3, "Deux 💎, joli butin !"),
    "7️⃣": (4, "Deux 7️⃣, presque le jackpot !"),
}
SLOT_SPECIAL_COMBOS: dict[tuple[str, ...], tuple[int, str]] = {
    tuple(sorted(("⭐", "💎", "7️⃣"))): (10, "Combo premium ⭐ 💎 7️⃣ !"),
}
SLOT_MIN_BET = 50
SLOT_MAX_BET = 1_000_000_000
CASINO_HUGE_MAX_CHANCE = 0.10
CASINO_HUGE_CHANCE_PER_PB = CASINO_HUGE_MAX_CHANCE / SLOT_MAX_BET
CASINO_TITANIC_MAX_CHANCE = 0.01
CASINO_TITANIC_CHANCE_PER_PB = CASINO_TITANIC_MAX_CHANCE / SLOT_MAX_BET

MASTERMIND_HUGE_MIN_CHANCE = 0.00055
MASTERMIND_HUGE_MAX_CHANCE = 0.0022
# FIX: Allow high-grade players to bypass Mastermind cooldown restrictions.
MASTERMIND_COOLDOWN_GRADE_THRESHOLD = 10

POTION_DROP_RATES: dict[str, float] = {
    "slots": 0.05,
    "mastermind": 0.05,
}

POTION_SLUGS: tuple[str, ...] = tuple(potion.slug for potion in POTION_DEFINITIONS)


TOMBOLA_TICKET_EMOJI = "🎟️"
TOMBOLA_PRIZE_FALLBACK = "<:HugeBull:1433617222357487748>"
TOMBOLA_PRIZE_EMOJI = PET_EMOJIS.get(HUGE_BULL_NAME, TOMBOLA_PRIZE_FALLBACK)
TOMBOLA_PRIZE_LABEL = f"{TOMBOLA_PRIZE_EMOJI} {HUGE_BULL_NAME}"
TOMBOLA_DRAW_INTERVAL = timedelta(hours=24)

KOTH_ROLL_INTERVAL = 10
KOTH_HUGE_CHANCE_DENOMINATOR = 6_000
KOTH_HUGE_EMOJI = PET_EMOJIS.get(HUGE_BO_NAME, "<:HugeBo:1435335892712685628>")
KOTH_HUGE_LABEL = f"{KOTH_HUGE_EMOJI} {HUGE_BO_NAME}"



@dataclass(frozen=True)
class MillionaireRaceStage:
    label: str
    success_rate: float
    prissbucks: int
    pet_choices: tuple[str, ...]
    potion_slugs: tuple[str, ...] = ()


MILLIONAIRE_RACE_COOLDOWN: int = 1_800

# Mode Hardcore : 20 étapes avec une difficulté décroissant par paliers jusqu'à Huge Gale
MILLIONAIRE_RACE_STAGES: tuple[MillionaireRaceStage, ...] = (
    # Étapes 1-5 : 95% → 75%
    MillionaireRaceStage("Sprint Émeraude", 0.95, 1_000, ()),
    MillionaireRaceStage("Relais Rubis", 0.90, 0, ("Shelly",)),
    MillionaireRaceStage("Virage Saphir", 0.85, 0, (), ("fortune_i",)),
    MillionaireRaceStage("Montée Jade", 0.80, 1_500, ()),
    MillionaireRaceStage("Ascension Ambrée", 0.75, 0, ("Colt",)),

    # Étapes 6-10 : 70% → 50%
    MillionaireRaceStage("Échappée Turquoise", 0.70, 0, (), ("luck_i",)),
    MillionaireRaceStage("Secteur Améthyste", 0.65, 2_000, ()),
    MillionaireRaceStage("Piste Onyx", 0.60, 0, ("Barley",)),
    MillionaireRaceStage("Canyon Rubis", 0.55, 0, (), ("fortune_ii",)),
    MillionaireRaceStage("Vallée Cristal", 0.50, 3_000, ()),

    # Étapes 11-15 : 45% → 25%
    MillionaireRaceStage("Ciel Prisme", 0.45, 0, ("Poco",)),
    MillionaireRaceStage("Spirale Stellaire", 0.40, 0, (), ("luck_ii",)),
    MillionaireRaceStage("Portail Titan", 0.35, 4_000, ()),
    MillionaireRaceStage("Faille Temporelle", 0.30, 0, ("Rosa",)),
    MillionaireRaceStage("Nexus Éternel", 0.25, 0, (), ("fortune_iii",)),

    # Étapes 16-19 : 20% → 5%
    MillionaireRaceStage("Abîme Infini", 0.20, 6_000, ()),
    MillionaireRaceStage("Dimension Chaos", 0.15, 0, ("Angelo",)),
    MillionaireRaceStage("Royaume Perdu", 0.10, 0, (), ("luck_iii",)),
    MillionaireRaceStage("Faille Légendaire", 0.05, 10_000, (), ("fortune_iv",)),

    # Étape 20 : FINALE - Huge Gale à 5%
    MillionaireRaceStage("Couronne Millionnaire", 0.05, 0, (HUGE_GALE_NAME,), ("fortune_v",)),
)

PET_DEFINITION_MAP: dict[str, object] = {pet.name: pet for pet in PET_DEFINITIONS}


@dataclass(frozen=True)
class MastermindColor:
    name: str
    emoji: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class MastermindConfig:
    colors: tuple[MastermindColor, ...]
    code_length: int = 4
    max_attempts: int = 8
    response_timeout: int = 60
    base_reward: tuple[int, int] = (170, 250)
    attempt_bonus: int = 20
    cancel_words: frozenset[str] = frozenset({"stop", "annuler", "cancel"})
    cooldown: int = 60

    @property
    def palette(self) -> tuple[tuple[str, str], ...]:
        return tuple((color.name, color.emoji) for color in self.colors)


class MastermindHelper:
    def __init__(self, config: MastermindConfig) -> None:
        self.config = config
        self._color_names: tuple[str, ...] = tuple(color.name for color in config.colors)
        self._emoji_map: dict[str, str] = {color.name: color.emoji for color in config.colors}
        alias_map: dict[str, str] = {}
        for color in config.colors:
            alias_map[color.name.lower()] = color.name
            for alias in color.aliases:
                alias_map[alias.lower()] = color.name
        self._alias_map = alias_map
        self._cancel_words = {word.lower() for word in config.cancel_words}
        self.available_names = ", ".join(color.name.capitalize() for color in config.colors)

    @property
    def palette(self) -> tuple[tuple[str, str], ...]:
        return self.config.palette

    def generate_code(self) -> list[str]:
        return [random.choice(self._color_names) for _ in range(self.config.code_length)]

    def format_code(self, code: Sequence[str], *, include_names: bool = False) -> str:
        if include_names:
            return " ".join(f"{self._emoji_map[color]} {color.capitalize()}" for color in code)
        return " ".join(self._emoji_map[color] for color in code)

    def parse_guess(self, raw: str) -> tuple[list[str], str | None]:
        tokens = [token for token in raw.replace(",", " ").replace(";", " ").split() if token]
        if len(tokens) != self.config.code_length:
            return [], f"Ta combinaison doit contenir exactement {self.config.code_length} couleurs."

        guess: list[str] = []
        for token in tokens:
            canonical = self._alias_map.get(token.lower())
            if canonical is None:
                return [], f"Couleur inconnue `{token}`. Choisis parmi : {self.available_names}."
            guess.append(canonical)

        return guess, None

    @staticmethod
    def evaluate_guess(secret: Sequence[str], guess: Sequence[str]) -> tuple[int, int]:
        exact = sum(s == g for s, g in zip(secret, guess))
        secret_counts = Counter(secret)
        guess_counts = Counter(guess)
        color_matches = sum(min(secret_counts[color], guess_counts[color]) for color in secret_counts)
        misplaced = color_matches - exact
        return exact, misplaced

    def is_cancel_message(self, content: str) -> bool:
        return content.lower() in self._cancel_words


MASTERMIND_COLORS: tuple[MastermindColor, ...] = (
    MastermindColor("rouge", "🔴", ("r", "red")),
    MastermindColor("bleu", "🔵", ("b", "blue")),
    MastermindColor("vert", "🟢", ("v", "green", "g")),
    MastermindColor("jaune", "🟡", ("j", "yellow", "y")),
    MastermindColor("violet", "🟣", ("violet", "violette", "pourpre", "purple", "p")),
    MastermindColor("orange", "🟠", ("o",)),
)
MASTERMIND_CONFIG = MastermindConfig(colors=MASTERMIND_COLORS)
MASTERMIND_HELPER = MastermindHelper(MASTERMIND_CONFIG)


MASTERMIND_GUESS_XP = 4
MASTERMIND_VICTORY_XP = 20
MASTERMIND_FAILURE_XP = 10
MASTERMIND_TIMEOUT_XP = 6


@dataclass(frozen=True)
class MastermindMasteryPerks:
    reward_multiplier: float = 1.0
    potion_multiplier: float = 1.0
    color_reduction: int = 0
    kenji_multiplier: float = 1.0


def _compute_mastermind_perks(level: int) -> MastermindMasteryPerks:
    reward_multiplier = 1.0
    if level >= 5:
        reward_multiplier *= 2
    if level >= 10:
        reward_multiplier *= 4
    if level >= 20:
        reward_multiplier *= 2
    if level >= 40:
        reward_multiplier *= 4
    if level >= 50:
        reward_multiplier *= 4

    potion_multiplier = 2.0 if level >= 10 else 1.0
    kenji_multiplier = 2.0 if level >= 30 else 1.0
    color_reduction = 0
    if level >= 20:
        color_reduction = 1
    if level >= 64:
        color_reduction = 2

    return MastermindMasteryPerks(
        reward_multiplier=reward_multiplier,
        potion_multiplier=potion_multiplier,
        color_reduction=color_reduction,
        kenji_multiplier=kenji_multiplier,
    )


class MastermindSession:
    """Gestion d'une partie de Mastermind avec interface à boutons."""

    def __init__(
        self,
        ctx: commands.Context,
        helper: MastermindHelper,
        database: Database,
        potion_callback: Callable[..., Awaitable[bool]] | None = None,
        mastery_perks: MastermindMasteryPerks | None = None,
        mastery_callback: Callable[[int, str], Awaitable[MastermindMasteryPerks | None]]
        | None = None,
        *,
        channel: discord.abc.Messageable | None = None,
    ) -> None:
        self.ctx = ctx
        self.bot: commands.Bot = ctx.bot
        self.helper = helper
        self.database = database
        self.secret = helper.generate_code()
        self.attempts = 0
        self.finished = False
        self.view: MastermindView | None = None
        self.message: discord.Message | None = None
        self.potion_callback = potion_callback
        self.potion_awarded = False
        self.attempt_history: list[tuple[int, str, int, int]] = []
        self.status_lines: list[str] = []
        self.embed_color = Colors.INFO
        self._logger = logger.getChild("MastermindSession")
        self.mastery_perks = mastery_perks or MastermindMasteryPerks()
        self.mastery_callback = mastery_callback
        self.raffle_ticket_total: int = 0
        self.raffle_pool_total: int | None = None
        self.channel: discord.abc.Messageable = channel or ctx.channel

    async def start(self) -> None:
        await self.database.ensure_user(self.ctx.author.id)
        try:
            self.raffle_ticket_total = await self.database.get_user_raffle_tickets(
                self.ctx.author.id
            )
        except Exception:
            self._logger.exception(
                "Impossible de récupérer les tickets de tombola",
                extra={"user_id": self.ctx.author.id},
            )
            self.raffle_ticket_total = 0
        await self._refresh_raffle_pool_total()
        self.view = MastermindView(self)
        embed = self.build_embed()
        self.message = await self.channel.send(embed=embed, view=self.view)
        self.view.message = self.message
        await self.view.refresh()
        await self.view.wait()

    def build_embed(self, *, current_guess: Sequence[str] | None = None) -> discord.Embed:
        selection = (
            self.helper.format_code(current_guess)
            if current_guess
            else "Aucune sélection"
        )
        attempts_left = max(0, self.helper.config.max_attempts - self.attempts)
        if self.finished:
            attempts_left = 0
        next_draw: datetime | None = None
        economy_cog = self.bot.get_cog("Economy")
        getter = getattr(economy_cog, "get_next_raffle_datetime", None) if economy_cog else None
        if callable(getter):
            try:
                next_draw = getter()
            except Exception:
                next_draw = None
        return embeds.mastermind_board_embed(
            member=self.ctx.author,
            palette=self.helper.palette,
            code_length=self.helper.config.code_length,
            max_attempts=self.helper.config.max_attempts,
            timeout=self.helper.config.response_timeout,
            attempts=tuple(self.attempt_history),
            attempts_left=attempts_left,
            current_selection=selection,
            status_lines=list(self.status_lines),
            color=self.embed_color,
            raffle_ticket_total=self.raffle_ticket_total,
            raffle_pool_total=self.raffle_pool_total,
            next_raffle_draw=next_draw,
            raffle_prize_label=TOMBOLA_PRIZE_LABEL,
            raffle_ticket_emoji=TOMBOLA_TICKET_EMOJI,
        )

    async def _refresh_raffle_pool_total(self) -> None:
        try:
            total = await self.database.get_total_raffle_tickets()
        except Exception:
            self._logger.exception(
                "Impossible de récupérer le total de tickets de tombola",
                extra={"user_id": self.ctx.author.id},
            )
            self.raffle_pool_total = None
        else:
            self.raffle_pool_total = int(total)

    async def process_guess(self, guess: Sequence[str]) -> None:
        self.attempts += 1
        exact, misplaced = self.helper.evaluate_guess(self.secret, guess)
        attempts_left = self.helper.config.max_attempts - self.attempts

        guess_display = self.helper.format_code(guess)
        self.attempt_history.append((self.attempts, guess_display, exact, misplaced))

        if (
            self.potion_callback is not None
            and not self.potion_awarded
            and await self.potion_callback(
                self.ctx,
                "mastermind",
                multiplier=max(1.0, float(self.mastery_perks.potion_multiplier)),
            )
        ):
            self.potion_awarded = True

        await self._award_mastery_xp(MASTERMIND_GUESS_XP, "guess")

        if exact == self.helper.config.code_length:
            await self._handle_victory(attempts_left)
            self.finished = True
        elif self.attempts >= self.helper.config.max_attempts:
            await self._handle_failure()
            self.finished = True

    async def cancel(self) -> None:
        if self.finished:
            return
        await self._handle_cancel()
        self.finished = True

    async def timeout(self) -> None:
        if self.finished:
            return
        await self._handle_timeout()
        self.finished = True

    async def finalize(self, interaction: Optional[discord.Interaction] = None) -> None:
        if self.view is None:
            return
        await self.view.refresh(interaction)

    async def _handle_victory(self, attempts_left: int) -> None:
        base_reward = random.randint(*self.helper.config.base_reward)
        raw_reward = base_reward + attempts_left * self.helper.config.attempt_bonus
        reward_multiplier = max(1.0, float(self.mastery_perks.reward_multiplier))
        reward = int(round(raw_reward * reward_multiplier))
        _, balance_after = await self.database.increment_balance(
            self.ctx.author.id,
            reward,
            transaction_type="mastermind_win",
            description=f"Mastermind gagné en {self.attempts} tentatives",
        )
        self.embed_color = Colors.SUCCESS
        self.status_lines = [
            f"Code craqué en **{self.attempts}** tentative(s) !",
            f"Tentatives restantes : **{attempts_left}**",
            f"Combinaison : {self._secret_display()}",
            f"Récompense : **{embeds.format_currency(reward)}**",
            f"Solde actuel : {embeds.format_currency(balance_after)}",
        ]
        if reward_multiplier != 1.0:
            multiplier_label = f"x{reward_multiplier:.2f}".rstrip("0").rstrip(".")
            self.status_lines.append(
                f"Multiplicateur de maîtrise appliqué : {multiplier_label}"
            )
        try:
            new_total = await self.database.add_raffle_tickets(self.ctx.author.id)
        except Exception:
            self._logger.exception(
                "Impossible d'attribuer un ticket de tombola",
                extra={"user_id": self.ctx.author.id},
            )
        else:
            self.raffle_ticket_total = new_total
            self.status_lines.append(
                f"{TOMBOLA_TICKET_EMOJI} Ticket de tombola obtenu ! Total : **{new_total}**"
            )
            self.status_lines.append(
                f"Lot en jeu : {TOMBOLA_PRIZE_LABEL}"
            )
            await self._refresh_raffle_pool_total()
            if self.raffle_pool_total:
                pool_display = f"{self.raffle_pool_total:,}".replace(",", " ")
                self.status_lines.append(
                    f"Tickets en lice : **{pool_display}**"
                )
        next_draw: datetime | None = None
        economy_cog = self.bot.get_cog("Economy")
        getter = getattr(economy_cog, "get_next_raffle_datetime", None) if economy_cog else None
        if callable(getter):
            try:
                next_draw = getter()
            except Exception:
                next_draw = None
        if isinstance(next_draw, datetime):
            if next_draw.tzinfo is None:
                next_draw = next_draw.replace(tzinfo=timezone.utc)
            self.status_lines.append(
                f"Prochain tirage tombola : {discord.utils.format_dt(next_draw, style='R')}"
            )
        self._logger.debug(
            "Mastermind win",
            extra={
                "user_id": self.ctx.author.id,
                "secret": "-".join(self.secret),
                "attempts": self.attempts,
                "reward": reward,
                "base_reward": base_reward,
                "attempts_left": attempts_left,
                "reward_multiplier": reward_multiplier,
            },
        )
        self.bot.dispatch(
            "grade_quest_progress",
            self.ctx.author,
            "mastermind",
            1,
            self.ctx.channel,
        )
        await self._maybe_award_mastermind_huge()
        await self._award_mastery_xp(MASTERMIND_VICTORY_XP, "victory")

    async def _handle_timeout(self) -> None:
        self.embed_color = Colors.ERROR
        self.status_lines = [
            "Temps écoulé !",
            f"Code secret : {self._secret_display()}",
            "Reviens tenter ta chance pour gagner des PB !",
        ]
        await self._award_mastery_xp(MASTERMIND_TIMEOUT_XP, "timeout")
        self._logger.debug(
            "Mastermind timeout",
            extra={
                "user_id": self.ctx.author.id,
                "secret": "-".join(self.secret),
                "attempts": self.attempts,
            },
        )

    async def _handle_failure(self) -> None:
        self.embed_color = Colors.ERROR
        self.status_lines = [
            "Toutes les tentatives ont été utilisées.",
            f"Code secret : {self._secret_display()}",
            "Reviens tenter ta chance pour gagner des PB !",
        ]
        await self._award_mastery_xp(MASTERMIND_FAILURE_XP, "failure")
        self._logger.debug(
            "Mastermind loss",
            extra={
                "user_id": self.ctx.author.id,
                "secret": "-".join(self.secret),
                "attempts": self.attempts,
            },
        )

    async def _handle_cancel(self) -> None:
        self.embed_color = Colors.WARNING
        self.status_lines = ["Partie annulée. Relance `e!mastermind` quand tu veux retenter ta chance !"]
        self._logger.debug(
            "Mastermind cancelled",
            extra={
                "user_id": self.ctx.author.id,
                "secret": "-".join(self.secret),
                "attempts": self.attempts,
            },
        )

    def _secret_display(self) -> str:
        return self.helper.format_code(self.secret, include_names=True)

    def _mastermind_huge_chance(self) -> float:
        max_attempts = max(1, self.helper.config.max_attempts)
        if max_attempts <= 1:
            return MASTERMIND_HUGE_MAX_CHANCE
        attempts_used = max(1, min(self.attempts, max_attempts))
        span = MASTERMIND_HUGE_MAX_CHANCE - MASTERMIND_HUGE_MIN_CHANCE
        if span <= 0:
            return max(MASTERMIND_HUGE_MIN_CHANCE, MASTERMIND_HUGE_MAX_CHANCE)
        factor = (attempts_used - 1) / (max_attempts - 1)
        base_chance = MASTERMIND_HUGE_MAX_CHANCE - factor * span
        multiplier = max(1.0, float(self.mastery_perks.kenji_multiplier))
        return min(1.0, base_chance * multiplier)

    async def _maybe_award_mastermind_huge(self) -> bool:
        chance = self._mastermind_huge_chance()
        roll = random.random()
        if roll >= chance:
            self._logger.debug(
                "Mastermind huge roll failed",
                extra={
                    "user_id": self.ctx.author.id,
                    "chance": chance,
                    "roll": roll,
                    "attempts": self.attempts,
                    "kenji_multiplier": self.mastery_perks.kenji_multiplier,
                },
            )
            return False

        pet_id = await self.database.get_pet_id_by_name(HUGE_KENJI_ONI_NAME)
        if pet_id is None:
            self._logger.warning("Pet %s introuvable pour le Mastermind", HUGE_KENJI_ONI_NAME)
            return False

        try:
            await self.database.add_user_pet(self.ctx.author.id, pet_id, is_huge=True)
        except DatabaseError:
            self._logger.exception(
                "Impossible d'ajouter %s à %s depuis le Mastermind",
                HUGE_KENJI_ONI_NAME,
                self.ctx.author.id,
            )
            return False

        best_non_huge = await self.database.get_best_non_huge_income(self.ctx.author.id)
        multiplier = get_huge_level_multiplier(HUGE_KENJI_ONI_NAME, 1)
        huge_income = max(HUGE_PET_MIN_INCOME, int(best_non_huge * multiplier))
        emoji = PET_EMOJIS.get(HUGE_KENJI_ONI_NAME, PET_EMOJIS.get("default", "🐾"))
        self.status_lines.append(
            "🔥 Jackpot ! Tu remportes "
            f"{emoji} **{HUGE_KENJI_ONI_NAME}** "
            f"({embeds.format_currency(huge_income)} /h) !"
        )
        self._logger.info(
            "Mastermind huge won",
            extra={
                "user_id": self.ctx.author.id,
                "attempts": self.attempts,
                "chance": chance,
                "roll": roll,
                "income": huge_income,
                "kenji_multiplier": self.mastery_perks.kenji_multiplier,
            },
        )
        return True

    async def _award_mastery_xp(self, amount: int, reason: str) -> None:
        if amount <= 0 or self.mastery_callback is None:
            return
        try:
            perks = await self.mastery_callback(amount, reason)
        except Exception:  # pragma: no cover - defensive logging
            self._logger.exception(
                "Erreur lors de l'attribution d'XP Mastermind",
                extra={"user_id": self.ctx.author.id, "amount": amount, "reason": reason},
            )
            return
        if isinstance(perks, MastermindMasteryPerks):
            self.mastery_perks = perks


class MastermindView(discord.ui.View):
    def __init__(self, session: MastermindSession) -> None:
        super().__init__(timeout=session.helper.config.response_timeout)
        self.session = session
        self.current_guess: list[str] = []
        self.message: Optional[discord.Message] = None
        self.timed_out = False
        self.confirm_button = self.ConfirmButton(self)
        self.clear_button = self.ClearButton(self)

        for index, color in enumerate(session.helper.config.colors):
            self.add_item(self.ColorButton(self, color, row=index // 3))
        self.add_item(self.confirm_button)
        self.add_item(self.clear_button)
        self.add_item(self.CancelButton(self))

    async def refresh(self, interaction: Optional[discord.Interaction] = None) -> None:
        embed = self.session.build_embed(current_guess=self.current_guess)

        if self.session.finished:
            for item in self.children:
                item.disabled = True
        else:
            code_length = self.session.helper.config.code_length
            self.confirm_button.disabled = len(self.current_guess) != code_length
            self.clear_button.disabled = len(self.current_guess) == 0

        if interaction is not None:
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.message.edit(embed=embed, view=self)
        elif self.message is not None:
            await self.message.edit(embed=embed, view=self)

        if self.session.finished:
            self.stop()

    async def handle_color(self, color_name: str, interaction: discord.Interaction) -> None:
        if self.session.finished:
            await interaction.response.send_message("La partie est terminée.", ephemeral=True)
            return
        if len(self.current_guess) >= self.session.helper.config.code_length:
            await interaction.response.send_message(
                "Tu as déjà sélectionné suffisamment de couleurs.", ephemeral=True
            )
            return
        self.current_guess.append(color_name)
        await self.refresh(interaction)

    async def handle_confirm(self, interaction: discord.Interaction) -> None:
        if self.session.finished:
            await interaction.response.send_message("La partie est terminée.", ephemeral=True)
            return
        if len(self.current_guess) != self.session.helper.config.code_length:
            await interaction.response.send_message(
                "Choisis la bonne quantité de couleurs avant de valider.",
                ephemeral=True,
            )
            return

        guess = list(self.current_guess)
        self.current_guess.clear()
        await self.session.process_guess(guess)
        if self.session.finished:
            await self.session.finalize(interaction)
        else:
            await self.refresh(interaction)

    async def handle_clear(self, interaction: discord.Interaction) -> None:
        if not self.current_guess:
            await interaction.response.send_message("Rien à effacer.", ephemeral=True)
            return
        self.current_guess.pop()
        await self.refresh(interaction)

    async def handle_cancel(self, interaction: discord.Interaction) -> None:
        if self.session.finished:
            await interaction.response.send_message("La partie est déjà terminée.", ephemeral=True)
            return
        await self.session.cancel()
        await self.session.finalize(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.session.ctx.author.id

    async def on_timeout(self) -> None:
        self.timed_out = True
        await self.session.timeout()
        await self.session.finalize()

    class ColorButton(discord.ui.Button):
        def __init__(
            self,
            view: MastermindView,
            color: MastermindColor,
            *,
            row: int,
        ) -> None:
            super().__init__(
                style=discord.ButtonStyle.secondary,
                label=color.name.capitalize(),
                emoji=color.emoji,
                row=row,
            )
            self.view_ref = view
            self.color_name = color.name

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            await self.view_ref.handle_color(self.color_name, interaction)

    class ConfirmButton(discord.ui.Button):
        def __init__(self, view: MastermindView) -> None:
            super().__init__(
                style=discord.ButtonStyle.success,
                label="Valider",
                row=2,
            )
            self.view_ref = view

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            await self.view_ref.handle_confirm(interaction)

    class ClearButton(discord.ui.Button):
        def __init__(self, view: MastermindView) -> None:
            super().__init__(
                style=discord.ButtonStyle.secondary,
                label="Effacer",
                row=2,
            )
            self.view_ref = view

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            await self.view_ref.handle_clear(interaction)

    class CancelButton(discord.ui.Button):
        def __init__(self, view: MastermindView) -> None:
            super().__init__(
                style=discord.ButtonStyle.danger,
                label="Abandonner",
                row=2,
            )
            self.view_ref = view

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            await self.view_ref.handle_cancel(interaction)



class CooldownManager:
    """Gestion simple de cooldowns en mémoire."""

    def __init__(self, duration: int) -> None:
        self.duration = duration
        self._cooldowns: dict[int, float] = {}
        # FIX: Guard cooldown reads and writes to make listener access thread-safe.
        self._lock = asyncio.Lock()

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

    async def check_and_trigger(self, user_id: int) -> float:
        # FIX: Ensure remaining and trigger happen atomically to avoid race conditions.
        async with self._lock:
            remaining = self.remaining(user_id)
            if remaining <= 0:
                self.trigger(user_id)
                return 0.0
            return remaining


class MillionaireRaceSession:
    """Gestion d'une session de Millionaire Race."""

    def __init__(self, ctx: commands.Context, database: Database) -> None:
        self.ctx = ctx
        self.database = database
        self.stage_index = 0
        self.total_pb = 0
        self.pets_awarded: list[str] = []
        self.potions_awarded: list[str] = []
        self.finished = False
        self.failed = False
        self.last_feedback: list[str] = []
        self.message: discord.Message | None = None

    @property
    def current_stage(self) -> MillionaireRaceStage | None:
        if self.stage_index >= len(MILLIONAIRE_RACE_STAGES):
            return None
        return MILLIONAIRE_RACE_STAGES[self.stage_index]

    async def attempt_stage(self) -> bool:
        stage = self.current_stage
        if stage is None:
            self.finished = True
            self.last_feedback = ["La course est déjà terminée."]
            return False

        success = random.random() <= stage.success_rate
        chance_label = f"{int(stage.success_rate * 100)}%"
        if not success:
            self.failed = True
            self.finished = True

            if self.total_pb > 0:
                try:
                    await self.database.increment_balance(
                        self.ctx.author.id,
                        -self.total_pb,
                        transaction_type="millionaire_race_loss",
                        description="Échec Millionaire Race - Perte des gains",
                    )
                except Exception:
                    logger.exception(
                        "Impossible de retirer les PrissBucks de %s après un échec de la Millionaire Race",
                        self.ctx.author.id,
                    )

            for pet_name in self.pets_awarded:
                try:
                    pet_id = await self.database.get_pet_id_by_name(pet_name)
                    if pet_id:
                        await self.database.pool.execute(
                            """
                            DELETE FROM user_pets
                            WHERE id = (
                                SELECT id FROM user_pets
                                WHERE user_id = $1 AND pet_id = $2
                                ORDER BY acquired_at DESC
                                LIMIT 1
                            )
                            """,
                            self.ctx.author.id,
                            pet_id,
                        )
                except Exception:
                    logger.exception("Impossible de retirer le pet %s après un échec", pet_name)

            self.last_feedback = [
                f"❌ ÉCHEC sur **{stage.label}** (chance de {chance_label}).",
                "💀 **TU PERDS TOUT !**",
                f"PrissBucks perdus : {embeds.format_currency(self.total_pb)}",
                f"Pets perdus : {len(self.pets_awarded)}",
                "Retente ta chance après le cooldown !",
            ]

            self.total_pb = 0
            self.pets_awarded = []
            self.potions_awarded = []
            return False

        feedback = [f"✅ **{stage.label}** franchie !"]
        pet_name: str | None = None

        if stage.prissbucks > 0:
            try:
                await self.database.increment_balance(
                    self.ctx.author.id,
                    stage.prissbucks,
                    transaction_type="millionaire_race",
                    description=f"Épreuve {self.stage_index + 1} - {stage.label}",
                )
                self.total_pb += stage.prissbucks
                feedback.append(f"💰 +{embeds.format_currency(stage.prissbucks)} remportés.")
            except Exception:
                logger.exception(
                    "Erreur lors du crédit de la Millionaire Race pour %s",
                    self.ctx.author.id,
                )
                feedback.append("Une erreur est survenue.")
        elif stage.pet_choices:
            pet_name = stage.pet_choices[0]
            if await self._award_pet(pet_name):
                self.pets_awarded.append(pet_name)
                feedback.append(f"🐾 Tu obtiens {self._format_pet_name(pet_name)} !")
            else:
                feedback.append("Le pet n'a pas pu être ajouté.")
        elif stage.potion_slugs:
            awarded_displays: list[str] = []
            for slug in stage.potion_slugs:
                definition = POTION_DEFINITION_MAP.get(slug)
                if definition is None:
                    logger.warning("Potion %s introuvable pour la Millionaire Race", slug)
                    continue
                try:
                    await self.database.add_user_potion(self.ctx.author.id, slug)
                except Exception:
                    logger.exception(
                        "Erreur d'attribution de la potion %s pour %s",
                        slug,
                        self.ctx.author.id,
                    )
                    continue
                display = f"🧪 {definition.name}"
                awarded_displays.append(display)
            if awarded_displays:
                for display in awarded_displays:
                    feedback.append(f"Potion obtenue : **{display}**")
                self.potions_awarded.extend(awarded_displays)
            else:
                feedback.append("La potion n'a pas pu être ajoutée.")

        self.stage_index += 1

        if self.stage_index >= len(MILLIONAIRE_RACE_STAGES):
            self.finished = True
            feedback.insert(0, "🎉🎉🎉 TU AS CONQUIS LA MILLIONAIRE RACE ! 🎉🎉🎉")
            if pet_name == HUGE_GALE_NAME:
                feedback.append(
                    f"🔥 **{self._format_pet_name(HUGE_GALE_NAME)}** est maintenant tien ! 🔥"
                )

        self.last_feedback = feedback
        return True

    async def _award_pet(self, pet_name: str) -> bool:
        pet_id = await self.database.get_pet_id_by_name(pet_name)
        if pet_id is None:
            logger.warning("Pet %s introuvable en base pour la Millionaire Race", pet_name)
            return False

        definition = PET_DEFINITION_MAP.get(pet_name)
        is_huge = bool(getattr(definition, "is_huge", False))
        try:
            await self.database.add_user_pet(self.ctx.author.id, pet_id, is_huge=is_huge)
        except DatabaseError:
            logger.exception("Impossible d'ajouter le pet %s pour %s", pet_name, self.ctx.author.id)
            return False
        return True

    @staticmethod
    def _format_pet_name(pet_name: str) -> str:
        emoji = PET_EMOJIS.get(pet_name, PET_EMOJIS.get("default", "🐾"))
        return f"{emoji} {pet_name}"

    @staticmethod
    def _format_potion_display(slug: str) -> str:
        definition = POTION_DEFINITION_MAP.get(slug)
        if definition is None:
            return slug
        return f"🧪 {definition.name}"

    def build_embed(self) -> discord.Embed:
        total_stages = len(MILLIONAIRE_RACE_STAGES)
        if self.finished:
            color = Colors.SUCCESS if not self.failed else Colors.ERROR
            title = "🏁 Millionaire Race — terminée" if not self.failed else "🏁 Millionaire Race — échec"
        else:
            color = Colors.INFO
            title = f"🏁 Millionaire Race — Épreuve {self.stage_index + 1}/{total_stages}"

        embed = discord.Embed(title=title, color=color)
        embed.timestamp = datetime.utcnow()
        embed.set_author(
            name=self.ctx.author.display_name,
            icon_url=self.ctx.author.display_avatar.url,
        )

        stage = self.current_stage
        if stage and not self.finished:
            chance_pct = int(stage.success_rate * 100)
            reward_lines = [
                "⚠️ ATTENTION : En cas d'échec, tu perds TOUT !",
                "20 étapes jusqu'au Huge Gale",
                f"Chance de réussite : **{chance_pct}%**",
            ]
            if stage.prissbucks:
                reward_lines.append(
                    f"PrissBucks : +{embeds.format_currency(stage.prissbucks)}"
                )
            if stage.pet_choices:
                pets_text = ", ".join(self._format_pet_name(name) for name in stage.pet_choices)
                reward_lines.append(f"Pet(s) garanti(s) : {pets_text}")
            if stage.potion_slugs:
                potions_text = ", ".join(
                    self._format_potion_display(slug) for slug in stage.potion_slugs
                )
                reward_lines.append(f"Potion(s) : {potions_text}")
            embed.description = "\n".join(reward_lines)
        elif self.finished and not self.failed:
            embed.description = "Tu as conquis la Millionaire Race !"
        else:
            embed.description = "La course s'arrête ici pour cette fois."

        summary_lines = [f"PrissBucks cumulés : {embeds.format_currency(self.total_pb)}"]
        if self.pets_awarded:
            summary_lines.append(
                "Pets obtenus : "
                + ", ".join(self._format_pet_name(name) for name in self.pets_awarded)
            )
        else:
            summary_lines.append("Pets obtenus : aucun pour le moment")
        if self.potions_awarded:
            summary_lines.append("Potions : " + ", ".join(self.potions_awarded))
        else:
            summary_lines.append("Potions : aucune")
        embed.add_field(name="Progrès", value="\n".join(summary_lines), inline=False)

        if self.last_feedback:
            embed.add_field(name="Dernier résultat", value="\n".join(self.last_feedback), inline=False)

        if not self.finished:
            embed.set_footer(
                text="Clique sur Continuer pour tenter l'épreuve suivante ou Arrêter pour quitter."
            )
        else:
            embed.set_footer(text="Tu peux relancer la course après le cooldown.")
        return embed


class MillionaireRaceView(discord.ui.View):
    """Interface interactive pour la Millionaire Race."""

    def __init__(
        self,
        session: MillionaireRaceSession,
        release_callback: Callable[[], None],
        *,
        timeout: float = 180.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.session = session
        self._release = release_callback
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        finished = self.session.finished
        if hasattr(self, "continue_button"):
            self.continue_button.disabled = finished
        if hasattr(self, "stop_button"):
            self.stop_button.disabled = finished

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.session.ctx.author.id:
            await interaction.response.send_message(
                "Seul le participant peut utiliser ces boutons.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Continuer", emoji="🏃", style=discord.ButtonStyle.success)
    async def continue_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self.session.attempt_stage()
        if self.session.finished:
            self.disable_all_items()
        self._sync_buttons()
        embed = self.session.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)
        if self.session.finished:
            self._release()
            self.stop()

    @discord.ui.button(label="Arrêter", emoji="🛑", style=discord.ButtonStyle.danger)
    async def stop_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.session.finished = True
        self.session.failed = False
        self.session.last_feedback = [
            "⏹️ Tu quittes la course avant la fin.",
            "Tes gains sont conservés.",
        ]
        self.disable_all_items()
        embed = self.session.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)
        self._release()
        self.stop()

    def disable_all_items(self) -> None:
        for item in self.children:
            item.disabled = True

    async def on_timeout(self) -> None:
        if not self.session.finished:
            self.session.finished = True
            self.session.last_feedback = [
                "⏳ La course a expiré. Relance la commande pour retenter ta chance.",
            ]
        self.disable_all_items()
        embed = self.session.build_embed()
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(embed=embed, view=self)
        self._release()
        self.stop()

class Economy(commands.Cog):
    """Commandes économiques réduites au strict nécessaire."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self.message_cooldown = CooldownManager(MESSAGE_COOLDOWN)
        self._cleanup_task: asyncio.Task[None] | None = None
        self.mastermind_helper = MASTERMIND_HELPER
        self._active_race_players: set[int] = set()
        # FIX: Manage Mastermind cooldown manually to support grade-based bypass.
        self._mastermind_cooldown = commands.CooldownMapping.from_cooldown(
            1, MASTERMIND_CONFIG.cooldown, commands.BucketType.user
        )
        self._mastermind_cooldown_lock = asyncio.Lock()
        self._raffle_task: asyncio.Task[None] | None = None
        self._raffle_interval = TOMBOLA_DRAW_INTERVAL
        self._next_raffle_draw: datetime | None = None
        self._last_raffle_draw: datetime | None = None

    async def cog_load(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        now = datetime.now(timezone.utc)
        try:
            last_draw = await self.database.get_last_raffle_draw()
        except Exception:
            logger.exception("Impossible de récupérer la dernière tombola enregistrée")
            last_draw = None
        self._last_raffle_draw = last_draw
        if last_draw is None:
            self._next_raffle_draw = now + self._raffle_interval
        else:
            candidate = last_draw + self._raffle_interval
            if candidate <= now:
                candidate = now + self._raffle_interval
            self._next_raffle_draw = candidate
        self._raffle_task = asyncio.create_task(self._raffle_loop())
        if not self.koth_reward_loop.is_running():
            self.koth_reward_loop.start()
        logger.info("Cog Economy chargé")

    async def cog_unload(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
        if self._raffle_task:
            self._raffle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._raffle_task
        if self.koth_reward_loop.is_running():
            self.koth_reward_loop.cancel()

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            self.message_cooldown.cleanup()

    def get_next_raffle_datetime(self) -> datetime | None:
        return self._next_raffle_draw

    async def _raffle_loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            if self.bot.is_closed():
                return
            next_draw = self._next_raffle_draw or (
                datetime.now(timezone.utc) + self._raffle_interval
            )
            delay = (next_draw - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(5)
            if self.bot.is_closed():
                return
            await self._run_raffle_draw()

    async def _run_raffle_draw(self) -> None:
        now = datetime.now(timezone.utc)
        self._last_raffle_draw = now
        self._next_raffle_draw = now + self._raffle_interval
        try:
            result = await self.database.draw_raffle_winner()
        except Exception:
            logger.exception("Échec du tirage de la tombola Mastermind")
            return
        if result is None:
            logger.debug("Tombola Mastermind : aucun ticket en jeu pour ce tirage.")
            return
        winner_id, total_tickets, winning_ticket = result
        pet_id = await self.database.get_pet_id_by_name(HUGE_BULL_NAME)
        if pet_id is None:
            logger.warning("Pet %s introuvable pour la tombola", HUGE_BULL_NAME)
        else:
            try:
                await self.database.add_user_pet(winner_id, pet_id, is_huge=True)
            except DatabaseError:
                logger.exception(
                    "Impossible d'ajouter %s au gagnant de la tombola", HUGE_BULL_NAME
                )
        try:
            best_non_huge = await self.database.get_best_non_huge_income(winner_id)
        except DatabaseError:
            logger.exception(
                "Impossible de récupérer le meilleur revenu non-huge pour %s",
                winner_id,
            )
            best_non_huge = 0
        multiplier = get_huge_level_multiplier(HUGE_BULL_NAME, 1)
        huge_income = max(HUGE_PET_MIN_INCOME, int(best_non_huge * multiplier))
        try:
            remaining = await self.database.get_user_raffle_tickets(winner_id)
        except Exception:
            logger.exception(
                "Impossible de récupérer le stock de tickets après le tirage",
                extra={"user_id": winner_id},
            )
            remaining = 0
        user = self.bot.get_user(winner_id)
        if user is None:
            with contextlib.suppress(discord.HTTPException):
                user = await self.bot.fetch_user(winner_id)
        winner_display = user.mention if user else f"Utilisateur {winner_id}"
        next_draw = self._next_raffle_draw
        relative_draw = (
            discord.utils.format_dt(next_draw, style="R")
            if isinstance(next_draw, datetime)
            else None
        )
        absolute_draw = (
            discord.utils.format_dt(next_draw, style="f")
            if isinstance(next_draw, datetime)
            else None
        )
        lines = [
            f"{TOMBOLA_TICKET_EMOJI} Un nouveau ticket gagnant a été tiré !",
            f"🥳 Félicitations à {winner_display} !",
            f"Ils remportent {TOMBOLA_PRIZE_LABEL} (jusqu'à {embeds.format_currency(huge_income)} /h).",
            f"Tickets en lice : **{total_tickets}** — Ticket gagnant #{winning_ticket}",
            f"Tickets restants pour le gagnant : **{remaining}**",
            "Plus tu cumules de tickets Mastermind, plus tes chances explosent !",
        ]
        if relative_draw and absolute_draw:
            lines.append(f"Prochain tirage : {relative_draw} ({absolute_draw})")
        embed = embeds.success_embed("\n".join(lines), title="🎟️ Tombola Mastermind")
        await self._broadcast_raffle_result(embed)
        if user is not None:
            personal_lines = [
                "🎉 Tu viens de remporter la tombola Mastermind !",
                f"Le lot **{TOMBOLA_PRIZE_LABEL}** a été ajouté à ton inventaire.",
                f"Reviens jouer au Mastermind pour cumuler encore plus de tickets !",
            ]
            with contextlib.suppress(discord.HTTPException, discord.Forbidden):
                await user.send(
                    embed=embeds.success_embed(
                        "\n".join(personal_lines), title="🎟️ Tombola Mastermind"
                    )
                )
        logger.info(
            "tombola_winner",
            extra={
                "user_id": winner_id,
                "total_tickets": total_tickets,
                "winning_ticket": winning_ticket,
            },
        )

    async def _broadcast_raffle_result(self, embed: discord.Embed) -> None:
        for guild in self.bot.guilds:
            me = guild.me
            if me is None:
                continue
            channel: discord.abc.Messageable | None = guild.system_channel
            if (
                channel is None
                or not isinstance(channel, discord.TextChannel)
                or not channel.permissions_for(me).send_messages
            ):
                channel = None
                for candidate in guild.text_channels:
                    if candidate.permissions_for(me).send_messages:
                        channel = candidate
                        break
            if channel is None:
                continue
            with contextlib.suppress(discord.HTTPException, discord.Forbidden):
                await channel.send(embed=embed)
    def _build_mastermind_helper(
        self, perks: MastermindMasteryPerks
    ) -> MastermindHelper:
        if perks.color_reduction <= 0:
            return self.mastermind_helper

        remaining = max(1, len(MASTERMIND_CONFIG.colors) - int(perks.color_reduction))
        colors = MASTERMIND_CONFIG.colors[:remaining]
        code_length = max(1, min(MASTERMIND_CONFIG.code_length, len(colors)))
        config = replace(MASTERMIND_CONFIG, colors=colors, code_length=code_length)
        return MastermindHelper(config)

    def _evaluate_slots(self, reels: Sequence[str]) -> tuple[int, str]:
        """Calcule le multiplicateur et le texte de résultat pour une combinaison."""
        default_message = "Pas de combinaison gagnante cette fois-ci."
        sorted_combo = tuple(sorted(reels))
        special = SLOT_SPECIAL_COMBOS.get(sorted_combo)
        if special:
            multiplier, message = special
            return multiplier, message or default_message

        if len(set(reels)) == 1:
            symbol = reels[0]
            multiplier, message = SLOT_TRIPLE_REWARDS.get(symbol, (0, ""))
            return multiplier, message or default_message

        counts = Counter(reels)
        most_common_symbol, count = counts.most_common(1)[0]
        if count == 2:
            multiplier, message = SLOT_PAIR_REWARDS.get(most_common_symbol, (0, ""))
            return multiplier, message or default_message

        return 0, default_message

    async def _maybe_award_potion(
        self,
        ctx: commands.Context,
        source: str,
        *,
        multiplier: float = 1.0,
    ) -> bool:
        drop_rate = POTION_DROP_RATES.get(source, 0.0)
        if drop_rate <= 0:
            return False

        effective_rate = min(1.0, drop_rate * max(1.0, float(multiplier)))
        if random.random() > effective_rate:
            return False

        if not POTION_SLUGS:
            return False

        slug = random.choice(POTION_SLUGS)
        definition = POTION_DEFINITION_MAP.get(slug)
        if definition is None:
            logger.warning("Potion %s introuvable lors de l'attribution aléatoire", slug)
            return False

        try:
            await self.database.add_user_potion(ctx.author.id, slug)
        except Exception:
            logger.exception("Impossible d'ajouter la potion %s", slug)
            return False

        source_label = "Machine à sous" if source == "slots" else "Mastermind"
        lines = [
            f"{ctx.author.mention} obtient **{definition.name}** grâce à {source_label} !",
            definition.description,
        ]

        await ctx.send(
            embed=embeds.success_embed("\n".join(lines), title="🧪 Potion trouvée")
        )

        logger.debug(
            "potion_awarded",
            extra={
                "user_id": ctx.author.id,
                "source": source,
                "potion": slug,
                "drop_rate": drop_rate,
                "multiplier": multiplier,
            },
        )
        return True

    async def _maybe_award_casino_titanic(self, ctx: commands.Context, bet: int) -> bool:
        if bet > 1_000:
            return False
        base_chance = min(
            CASINO_HUGE_MAX_CHANCE, max(0.0, bet * CASINO_HUGE_CHANCE_PER_PB)
        )
        chance_by_bet = min(
            CASINO_TITANIC_MAX_CHANCE, max(0.0, bet * CASINO_TITANIC_CHANCE_PER_PB)
        )
        chance = min(base_chance, chance_by_bet) / 10
        if chance <= 0 or random.random() > chance:
            return False

        pet_id = await self.database.get_pet_id_by_name(TITANIC_GRIFF_NAME)
        if pet_id is None:
            logger.warning("Pet %s introuvable pour le casino", TITANIC_GRIFF_NAME)
            return False

        try:
            await self.database.add_user_pet(ctx.author.id, pet_id, is_huge=True)
        except DatabaseError:
            logger.exception("Impossible d'ajouter %s depuis le casino", TITANIC_GRIFF_NAME)
            return False

        emoji = PET_EMOJIS.get(TITANIC_GRIFF_NAME, PET_EMOJIS.get("default", "🐾"))
        chance_pct = chance * 100
        lines = [
            f"{ctx.author.mention} décroche {emoji} **{TITANIC_GRIFF_NAME}** au casino !",
            f"Mise : {embeds.format_currency(bet)} • Chance : {chance_pct:.6f}%",
        ]
        await ctx.send(
            embed=embeds.success_embed(
                "\n".join(lines), title="🎰 Jackpot colossal !"
            )
        )

        logger.info(
            "casino_titanic_awarded",
            extra={
                "user_id": ctx.author.id,
                "pet_id": pet_id,
                "bet": bet,
                "chance": chance,
            },
        )
        return True

    async def _process_mastermind_mastery_xp(
        self,
        ctx: commands.Context,
        amount: int,
        reason: str,
    ) -> MastermindMasteryPerks | None:
        if amount <= 0:
            return None

        update = await self.database.add_mastery_experience(
            ctx.author.id, MASTERMIND_MASTERY.slug, amount
        )
        await self._handle_mastermind_mastery_notifications(ctx, update)
        level = int(update.get("level", 1) or 1)
        logger.debug(
            "mastermind_mastery_xp",
            extra={
                "user_id": ctx.author.id,
                "amount": amount,
                "reason": reason,
                "level": level,
            },
        )
        return _compute_mastermind_perks(level)

    async def _handle_mastermind_mastery_notifications(
        self, ctx: commands.Context, update: Mapping[str, object]
    ) -> None:
        levels_gained = int(update.get("levels_gained", 0) or 0)
        if levels_gained <= 0:
            return

        level = int(update.get("level", 1) or 1)
        previous_level = int(update.get("previous_level", level) or level)
        xp_to_next = int(update.get("xp_to_next_level", 0) or 0)
        current_progress = int(update.get("experience", 0) or 0)

        lines = [
            f"Tu passes niveau **{level}** de {MASTERMIND_MASTERY.display_name} !"
        ]
        if xp_to_next > 0:
            remaining = max(0, xp_to_next - current_progress)
            remaining_text = (
                f"Encore {remaining:,} points d'expérience pour le prochain palier.".replace(",", " ")
            )
            lines.append(remaining_text)
        else:
            lines.append("Tu as atteint le niveau maximal de maîtrise, félicitations !")

        if previous_level < 5 <= level:
            lines.append("Tes gains Mastermind sont désormais **doublés** !")
        if previous_level < 10 <= level:
            lines.append(
                "Tu ajoutes un multiplicateur **x4** (total x8) et ta chance de potion est doublée !"
            )
        if previous_level < 20 <= level:
            lines.append(
                "Nouvelle montée en puissance : multiplicateur total **x16** et une couleur en moins à deviner."
            )
        if previous_level < 30 <= level:
            lines.append(
                "Tes chances de décrocher **Kenji Oni** sont maintenant **doublées** !"
            )
        if previous_level < 40 <= level:
            lines.append(
                "Tes récompenses grimpent à un total **x64** après chaque victoire Mastermind !"
            )
        if previous_level < 50 <= level:
            lines.append(
                "Dernier boost : tu atteins un multiplicateur colossal **x256** sur les PB gagnés !"
            )
        if previous_level < 64 <= level:
            lines.append(
                "Tu obtiens le rôle ultime et affrontes Mastermind avec **deux couleurs en moins** !"
            )

        role_map = {MASTERMIND_MASTERY.slug: MASTERMIND_MASTERY_MAX_ROLE_ID}
        if level >= MASTERMIND_MASTERY.max_level and ctx.guild is not None:
            role = ctx.guild.get_role(role_map.get(MASTERMIND_MASTERY.slug, 0))
            member = ctx.guild.get_member(ctx.author.id) if role else None
            if role is not None and member is not None and role not in member.roles:
                with contextlib.suppress(discord.HTTPException, discord.Forbidden):
                    await member.add_roles(
                        role, reason=f"{MASTERMIND_MASTERY.display_name} niveau {level}"
                    )
                lines.append(f"🎖️ Tu obtiens {role.mention} !")

        await ctx.send("\n".join(lines))

        dm_content = "🧠 " + "\n".join(lines)
        try:
            await ctx.author.send(dm_content)
        except discord.Forbidden:
            logger.debug("Impossible d'envoyer le MP de maîtrise Mastermind à %s", ctx.author.id)
        except discord.HTTPException:
            logger.warning("Échec de l'envoi du MP Mastermind", exc_info=True)

        new_levels = [int(value) for value in update.get("new_levels", [])]
        milestones = [lvl for lvl in new_levels if lvl in MASTERMIND_MASTERY.broadcast_levels]
        if not milestones or ctx.guild is None:
            return

        highest_milestone = max(milestones)
        channel = ctx.channel
        if not hasattr(channel, "permissions_for"):
            return

        me = ctx.guild.me
        if me is None or not channel.permissions_for(me).send_messages:
            return

        announcement_lines = [
            f"🧠 **{ctx.author.display_name}** vient d'atteindre le niveau {highest_milestone} de {MASTERMIND_MASTERY.display_name}!",
        ]
        if highest_milestone >= 64:
            announcement_lines.append(
                "Ils décrochent le rôle légendaire et jouent avec deux couleurs en moins en permanence !"
            )
        elif highest_milestone >= 50:
            announcement_lines.append(
                "Leurs gains Mastermind culminent maintenant à un total x256 !"
            )
        elif highest_milestone >= 40:
            announcement_lines.append(
                "Ils profitent d'un multiplicateur total x64 sur toutes leurs victoires !"
            )
        elif highest_milestone >= 30:
            announcement_lines.append(
                "Leur chance d'obtenir Kenji Oni est désormais doublée !"
            )
        elif highest_milestone >= 20:
            announcement_lines.append(
                "Une couleur disparaît du code et ils atteignent un total x16 sur les récompenses !"
            )
        elif highest_milestone >= 10:
            announcement_lines.append(
                "Leurs gains sont maintenant multipliés par 8 et les potions tombent deux fois plus souvent !"
            )

        await channel.send("\n".join(announcement_lines))

    # ------------------------------------------------------------------
    # Récompenses de messages
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if not message.content.strip():
            return

        # FIX: Atomically verify and apply the cooldown to avoid double rewards.
        remaining = await self.message_cooldown.check_and_trigger(message.author.id)
        if remaining > 0:
            return
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

    @commands.command(name="koth")
    async def koth(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            await ctx.send(
                embed=embeds.error_embed("Cette commande ne peut être utilisée qu'en serveur.")
            )
            return
        if isinstance(ctx.channel, (discord.DMChannel, discord.GroupChannel)):
            await ctx.send(
                embed=embeds.error_embed("La commande King of the Hill ne fonctionne pas en message privé.")
            )
            return
        try:
            previous_state = await self.database.get_koth_state(ctx.guild.id)
        except Exception:
            logger.exception("Impossible de récupérer l'état King of the Hill")
            await ctx.send(
                embed=embeds.error_embed("Impossible de prendre le contrôle de la colline pour le moment.")
            )
            return
        previous_king_id = 0
        if previous_state is not None:
            previous_king_id = int(previous_state.get("king_user_id") or 0)
            if previous_king_id == ctx.author.id:
                await ctx.send(
                    embed=embeds.info_embed(
                        (
                            "Tu es déjà le roi de la colline !\n"
                            f"Chaque {KOTH_ROLL_INTERVAL}s tu as 1/{KOTH_HUGE_CHANCE_DENOMINATOR} de gagner {KOTH_HUGE_LABEL}."
                        ),
                        title="👑 King of the Hill",
                    )
                )
                return
        try:
            await self.database.upsert_koth_state(ctx.guild.id, ctx.author.id, ctx.channel.id)
        except Exception:
            logger.exception("Impossible de mettre à jour le roi de la colline")
            await ctx.send(
                embed=embeds.error_embed("Impossible de prendre le contrôle de la colline pour le moment.")
            )
            return
        description = (
            f"{ctx.author.mention} règne désormais sur la colline !\n"
            f"Chaque {KOTH_ROLL_INTERVAL}s : 1/{KOTH_HUGE_CHANCE_DENOMINATOR} de gagner {KOTH_HUGE_LABEL}."
        )
        if previous_king_id and previous_king_id != ctx.author.id:
            description += f"\n<@{previous_king_id}> cède sa place au nouveau roi."
        embed = embeds.success_embed(description, title="👑 King of the Hill")
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

    @commands.command(
        name="selltickets",
        aliases=("sellticket", "vendretickets", "vendreticket"),
    )
    async def sell_raffle_tickets(self, ctx: commands.Context, amount: int = 1) -> None:
        """Invite les joueurs à utiliser la plaza pour céder leurs tickets."""

        await ctx.send(
            embed=embeds.warning_embed(
                "La vente directe de tickets est désormais fermée. Ouvre `e!stand` pour accéder aux boutons permettant de lister tes tickets sur la plaza !",
                title="🎟️ Vente de tickets",
            )
        )

    @staticmethod
    def _validate_give_request(author: discord.Member, target: discord.Member, amount: int) -> str | None:
        if target.bot:
            return "Tu ne peux pas donner de PB à un bot."
        if target == author:
            return "Impossible de te donner des PB."
        if amount <= 0:
            return "Le montant doit être supérieur à 0."
        return None

    @commands.group(name="give", invoke_without_command=True)
    async def give(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        amount: int | None = None,
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return

        if member is None or amount is None:
            await ctx.send(
                embed=embeds.error_embed(
                    "Utilisation : e!give @membre montant",
                    title="Commande incomplète",
                )
            )
            return

        error = self._validate_give_request(ctx.author, member, amount)
        if error:
            await ctx.send(embed=embeds.error_embed(error))
            return

        await self.database.ensure_user(ctx.author.id)
        await self.database.ensure_user(member.id)

        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < amount:
            await ctx.send(
                embed=embeds.error_embed("Tu n'as pas assez de PB pour ce transfert."),
            )
            return

        try:
            transfer = await self.database.transfer_balance(
                sender_id=ctx.author.id,
                recipient_id=member.id,
                amount=amount,
                send_transaction_type="give_send",
                receive_transaction_type="give_receive",
                send_description=f"Transfert vers {member.id}",
                receive_description=f"Transfert reçu de {ctx.author.id}",
            )
        except InsufficientBalanceError:
            await ctx.send(embed=embeds.error_embed("Tu n'as pas assez de PB pour ce transfert."))
            return
        except DatabaseError:
            await ctx.send(
                embed=embeds.error_embed("Transfert impossible pour le moment. Réessaie plus tard."),
            )
            return

        lines = [
            f"{ctx.author.mention} → {member.mention}",
            f"Montant : {embeds.format_currency(amount)}",
            f"Ton solde : {embeds.format_currency(transfer['sender']['after'])}",
            f"Solde de {member.display_name} : {embeds.format_currency(transfer['recipient']['after'])}",
        ]
        await ctx.send(embed=embeds.success_embed("\n".join(lines), title="Transfert réussi"))

    @give.command(name="mortis")
    @commands.guild_only()
    @commands.is_owner()
    async def give_mortis(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        assert guild is not None  # guild_only garantit la présence de la guilde

        role = guild.get_role(VIP_ROLE_ID)
        if role is None:
            await ctx.send(embed=embeds.error_embed("Le rôle VIP est introuvable sur ce serveur."))
            return

        vip_members = [member for member in role.members if not member.bot]
        if not vip_members:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Aucun membre éligible n'a le rôle VIP pour recevoir la récompense.",
                    title="Aucune distribution",
                )
            )
            return

        pet_id = await self.database.get_pet_id_by_name(HUGE_MORTIS_NAME)
        if pet_id is None:
            await ctx.send(
                embed=embeds.error_embed("Huge Mortis n'est pas encore enregistré dans la base de données."),
            )
            return

        emoji = PET_EMOJIS.get(HUGE_MORTIS_NAME, PET_EMOJIS.get("default", "🐾"))
        awarded = 0
        already_owned = 0
        failed: list[int] = []

        for member in vip_members:
            try:
                existing = await self.database.get_user_pet_by_name(
                    member.id,
                    HUGE_MORTIS_NAME,
                )
            except DatabaseError:
                logger.exception("Impossible de vérifier les pets de %s", member.id)
                failed.append(member.id)
                continue

            if existing:
                already_owned += 1
                continue

            try:
                await self.database.add_user_pet(member.id, pet_id, is_huge=True)
            except DatabaseError:
                logger.exception("Impossible d'ajouter Huge Mortis à %s", member.id)
                failed.append(member.id)
                continue

            awarded += 1

        summary_lines = [
            f"Rôle ciblé : {role.mention} ({len(vip_members)} membres)",
            f"Récompense : {emoji} **{HUGE_MORTIS_NAME}**",
            f"Distribués : **{awarded}**",
        ]
        if already_owned:
            summary_lines.append(f"Déjà équipés : **{already_owned}**")
        if failed:
            summary_lines.append(f"Échecs : **{len(failed)}**")

        await ctx.send(
            embed=embeds.success_embed(
                "\n".join(summary_lines),
                title="Distribution Huge Mortis",
            )
        )

        logger.info(
            "Huge Mortis distribution",
            extra={
                "admin_id": ctx.author.id,
                "role_id": VIP_ROLE_ID,
                "awarded": awarded,
                "already_owned": already_owned,
                "failed": failed,
            },
        )

    @commands.cooldown(1, 6, commands.BucketType.user)
    @commands.command(name="slots", aliases=("slot", "machine"))
    async def slots(self, ctx: commands.Context, bet: float = 100) -> None:
        """Jeu de machine à sous simple pour miser ses PB."""
        # FIX: Reject non-integer wagers explicitly to avoid implicit truncation.
        if isinstance(bet, float):
            if not bet.is_integer():
                await ctx.send(
                    embed=embeds.error_embed("Montant invalide, entiers uniquement."),
                )
                return
            bet = int(bet)
        else:
            bet = int(bet)
        if bet <= 0:
            await ctx.send(embed=embeds.error_embed("La mise doit être un nombre positif."))
            return
        if bet < SLOT_MIN_BET or bet > SLOT_MAX_BET:
            await ctx.send(
                embed=embeds.error_embed(
                    (
                        "La mise doit être comprise entre "
                        f"{embeds.format_currency(SLOT_MIN_BET)} et {embeds.format_currency(SLOT_MAX_BET)}."
                    )
                )
            )
            return

        await self.database.ensure_user(ctx.author.id)
        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < bet:
            await ctx.send(
                embed=embeds.error_embed(
                    "Tu n'as pas assez de PB pour cette mise. Tente un montant plus faible ou récupère ton daily !"
                )
            )
            return

        _, balance_after_bet = await self.database.increment_balance(
            ctx.author.id,
            -bet,
            transaction_type="slots_bet",
            description=f"Mise machine à sous ({embeds.format_currency(bet)})",
        )

        reels = random.choices(SLOT_REELS, weights=SLOT_WEIGHTS, k=3)
        multiplier, message = self._evaluate_slots(reels)
        payout = bet * multiplier
        final_balance = balance_after_bet
        if payout:
            _, final_balance = await self.database.increment_balance(
                ctx.author.id,
                payout,
                transaction_type="slots_win",
                description=f"Gain machine à sous (x{multiplier})",
            )

        logger.debug(
            "Slot machine spin",
            extra={
                "user_id": ctx.author.id,
                "bet": bet,
                "reels": " ".join(reels),
                "multiplier": multiplier,
                "payout": payout,
                "balance_after": final_balance,
            },
        )

        embed = embeds.slot_machine_embed(
            member=ctx.author,
            bet=bet,
            reels=reels,
            payout=payout,
            multiplier=multiplier,
            balance_after=final_balance,
            result_text=message,
        )
        await ctx.send(embed=embed)
        await self._maybe_award_potion(ctx, "slots")
        await self._maybe_award_casino_titanic(ctx, bet)

    @commands.command(name="mastermind", aliases=("mm", "code"))
    async def mastermind(self, ctx: commands.Context) -> None:
        """Mini-jeu de Mastermind pour gagner quelques PB."""
        grade_level = await self.database.get_grade_level(ctx.author.id)
        if grade_level < 1:
            required_name = (
                GRADE_DEFINITIONS[0].name if GRADE_DEFINITIONS else "Novice"
            )
            await ctx.send(
                embed=embeds.error_embed(
                    "Le Mastermind se débloque au grade 1 "
                    f"(**{required_name}**). Progresse encore un peu !"
                )
            )
            return

        bypass_cooldown = grade_level >= MASTERMIND_COOLDOWN_GRADE_THRESHOLD
        if not bypass_cooldown:
            async with self._mastermind_cooldown_lock:
                bucket = self._mastermind_cooldown.get_bucket(ctx.message)
                current = time.monotonic()
                retry_after = bucket.get_retry_after(current)
                if retry_after:
                    await ctx.send(
                        embed=embeds.cooldown_embed(f"{PREFIX}mastermind", retry_after)
                    )
                    return
                bucket.update_rate_limit(current)

        await self.database.ensure_user(ctx.author.id)
        progress = await self.database.get_mastery_progress(
            ctx.author.id, MASTERMIND_MASTERY.slug
        )
        level = int(progress.get("level", 1) or 1)
        mastery_perks = _compute_mastermind_perks(level)
        helper = self._build_mastermind_helper(mastery_perks)

        async def mastery_callback(
            amount: int, reason: str
        ) -> MastermindMasteryPerks | None:
            return await self._process_mastermind_mastery_xp(ctx, amount, reason)

        try:
            dm_channel = await ctx.author.create_dm()
        except discord.Forbidden:
            await ctx.send(
                embed=embeds.error_embed(
                    "Impossible de t'envoyer un message privé. Active tes MP pour jouer au Mastermind."
                )
            )
            return
        else:
            if ctx.guild is not None:
                await ctx.send(
                    embed=embeds.info_embed(
                        "Je t'ai envoyé la partie en message privé. Consulte tes MP pour jouer !"
                    )
                )

        session = MastermindSession(
            ctx,
            helper,
            self.database,
            potion_callback=self._maybe_award_potion,
            mastery_perks=mastery_perks,
            mastery_callback=mastery_callback,
            channel=dm_channel,
        )
        try:
            await session.start()
        except discord.Forbidden:
            await ctx.send(
                embed=embeds.error_embed(
                    "Tes messages privés sont fermés. Active-les puis relance `e!mastermind`."
                )
            )
            return
        except Exception as exc:  # pragma: no cover - log unexpected runtime errors
            logger.exception("Erreur dans Mastermind", exc_info=exc)
            await ctx.send(
                embed=embeds.error_embed("Une erreur est survenue. Réessaie dans quelques instants.")
            )

    @commands.cooldown(1, MILLIONAIRE_RACE_COOLDOWN, commands.BucketType.user)
    @commands.command(name="millionairerace", aliases=("millionaire", "race"))
    async def millionaire_race(self, ctx: commands.Context) -> None:
        """Mini-jeu inspiré de Pet Simulator : enchaîne les étapes pour tout rafler."""

        if ctx.author.id in self._active_race_players:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Tu as déjà une Millionaire Race en cours. Termine-la avant d'en relancer une autre."
                )
            )
            return

        self._active_race_players.add(ctx.author.id)

        release_called = False

        def release() -> None:
            nonlocal release_called
            if not release_called:
                self._active_race_players.discard(ctx.author.id)
                release_called = True

        session = MillionaireRaceSession(ctx, self.database)
        view = MillionaireRaceView(session, release)
        embed = session.build_embed()

        try:
            message = await ctx.send(embed=embed, view=view)
        except Exception:
            release()
            logger.exception("Impossible de démarrer la Millionaire Race pour %s", ctx.author.id)
            await ctx.send(
                embed=embeds.error_embed(
                    "La Millionaire Race n'est pas disponible pour le moment. Réessaie plus tard."
                )
            )
            return

        session.message = message
        view.message = message

    @tasks.loop(seconds=KOTH_ROLL_INTERVAL)
    async def koth_reward_loop(self) -> None:
        try:
            states = await self.database.get_all_koth_states()
        except Exception:
            logger.exception("Impossible de récupérer l'état King of the Hill")
            return

        if not states:
            return

        now = datetime.now(timezone.utc)
        for state in states:
            guild_id = int(state.get("guild_id") or 0)
            king_id = int(state.get("king_user_id") or 0)
            channel_id = int(state.get("channel_id") or 0)
            if not guild_id or not king_id or not channel_id:
                continue

            await self.database.update_koth_roll_timestamp(guild_id, timestamp=now)
            if random.randint(1, KOTH_HUGE_CHANCE_DENOMINATOR) != 1:
                continue

            pet_id = await self.database.get_pet_id_by_name(HUGE_BO_NAME)
            if pet_id is None:
                logger.warning("Pet %s introuvable pour le mode KOTH", HUGE_BO_NAME)
                continue

            try:
                await self.database.add_user_pet(king_id, pet_id, is_huge=True)
            except DatabaseError:
                logger.exception(
                    "Impossible d'ajouter %s au roi de la colline (guild=%s)",
                    HUGE_BO_NAME,
                    guild_id,
                )
                continue

            channel = self.bot.get_channel(channel_id)
            announcement = (
                f"{KOTH_HUGE_LABEL} rejoint <@{king_id}> !\n"
                f"Chance 1/{KOTH_HUGE_CHANCE_DENOMINATOR} toutes les {KOTH_ROLL_INTERVAL}s."
            )
            if isinstance(channel, Messageable):
                with contextlib.suppress(discord.HTTPException):
                    await channel.send(announcement)
            else:
                logger.info(
                    "Canal introuvable pour annoncer la récompense KOTH (guild_id=%s, channel_id=%s)",
                    guild_id,
                    channel_id,
                )

            user = self.bot.get_user(king_id)
            if user is not None:
                with contextlib.suppress(discord.HTTPException):
                    await user.send(
                        f"👑 Tu remportes {KOTH_HUGE_LABEL} grâce à King of the Hill !"
                    )

    @koth_reward_loop.before_loop
    async def before_koth_reward_loop(self) -> None:
        await self.bot.wait_until_ready()



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
