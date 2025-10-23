"""Fonctionnalités économiques essentielles : balance, daily et récompenses."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Sequence

import discord
from discord.ext import commands

from config import (
    Colors,
    DAILY_COOLDOWN,
    DAILY_REWARD,
    GRADE_DEFINITIONS,
    HUGE_GALE_NAME,
    HUGE_PET_NAME,
    MESSAGE_COOLDOWN,
    MESSAGE_REWARD,
    PET_DEFINITIONS,
    PET_EMOJIS,
    PREFIX,
)
from utils import embeds
from database.db import Database, DatabaseError, InsufficientBalanceError

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
SLOT_MAX_BET = 5000

PET_BOOSTER_MULTIPLIERS: tuple[float, ...] = (1.5, 2, 3, 5, 10, 100)
PET_BOOSTER_DURATIONS_MINUTES: tuple[int, ...] = (1, 5, 10, 15, 30, 60, 180)


def _build_booster_pool() -> tuple[tuple[float, int, int], ...]:
    pool: list[tuple[float, int, int]] = []
    for multiplier in PET_BOOSTER_MULTIPLIERS:
        for minutes in PET_BOOSTER_DURATIONS_MINUTES:
            weight = max(1, int(1_000_000 / (multiplier * minutes)))
            pool.append((multiplier, minutes, weight))
    return tuple(pool)


_PET_BOOSTER_POOL = _build_booster_pool()
PET_BOOSTER_CHOICES: tuple[tuple[float, int], ...] = tuple(
    (multiplier, minutes) for multiplier, minutes, _ in _PET_BOOSTER_POOL
)
PET_BOOSTER_WEIGHTS: tuple[int, ...] = tuple(weight for _, _, weight in _PET_BOOSTER_POOL)
PET_BOOSTER_DROP_RATES: dict[str, float] = {
    "slots": 0.05,
    "mastermind": 0.05,
}


@dataclass(frozen=True)
class MillionaireRaceStage:
    label: str
    success_rate: float
    prissbucks: int
    pet_choices: tuple[str, ...]
    booster: tuple[float, int] | None = None


MILLIONAIRE_RACE_COOLDOWN: int = 1_800
MILLIONAIRE_RACE_STAGES: tuple[MillionaireRaceStage, ...] = (
    MillionaireRaceStage("Sprint Émeraude", 0.95, 5_000, ("Shelly", "Colt"), (1.5, 600)),
    MillionaireRaceStage("Relais Rubis", 0.90, 7_500, ("Barley", "Poco"), (1.6, 900)),
    MillionaireRaceStage("Virage Saphir", 0.85, 10_000, ("Rosa",), (1.8, 900)),
    MillionaireRaceStage("Ascension Ambrée", 0.80, 14_000, ("Angelo",), (2.0, 1_200)),
    MillionaireRaceStage("Échappée Turquoise", 0.75, 18_500, ("Doug",), (2.2, 1_200)),
    MillionaireRaceStage("Secteur Améthyste", 0.70, 24_000, ("Lily",), (2.4, 1_500)),
    MillionaireRaceStage("Piste Onyx", 0.65, 30_000, ("Cordelius",), (2.6, 1_500)),
    MillionaireRaceStage(
        "Ciel Prisme",
        0.60,
        37_500,
        ("Shelly", "Barley", "Poco", "Rosa"),
        (2.8, 1_800),
    ),
    MillionaireRaceStage(
        "Spirale Stellaire",
        0.55,
        45_000,
        ("Angelo", "Doug", "Lily", "Cordelius"),
        (3.0, 2_100),
    ),
    MillionaireRaceStage("Portail Titan", 0.50, 60_000, (HUGE_PET_NAME,), (3.5, 2_400)),
    MillionaireRaceStage("Faille Légendaire", 0.45, 80_000, ("Huge Trunk",), (4.0, 3_000)),
    MillionaireRaceStage("Couronne Millionnaire", 0.40, 100_000, (HUGE_GALE_NAME,), (5.0, 3_600)),
)

PET_DEFINITION_MAP: dict[str, object] = {pet.name: pet for pet in PET_DEFINITIONS}


def _format_seconds(seconds: float) -> str:
    seconds_int = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds_int, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if sec or not parts:
        parts.append(f"{sec}s")
    return " ".join(parts)


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
    base_reward: tuple[int, int] = (120, 200)
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


class MastermindSession:
    """Gestion d'une partie de Mastermind avec interface à boutons."""

    def __init__(
        self,
        ctx: commands.Context,
        helper: MastermindHelper,
        database: Database,
        booster_callback: Callable[[commands.Context, str], Awaitable[bool]] | None = None,
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
        self.booster_callback = booster_callback
        self.booster_awarded = False
        self.attempt_history: list[tuple[int, str, int, int]] = []
        self.status_lines: list[str] = []
        self.embed_color = Colors.INFO
        self._logger = logger.getChild("MastermindSession")

    async def start(self) -> None:
        await self.database.ensure_user(self.ctx.author.id)
        self.view = MastermindView(self)
        embed = self.build_embed()
        self.message = await self.ctx.send(embed=embed, view=self.view)
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
        )

    async def process_guess(self, guess: Sequence[str]) -> None:
        self.attempts += 1
        exact, misplaced = self.helper.evaluate_guess(self.secret, guess)
        attempts_left = self.helper.config.max_attempts - self.attempts

        guess_display = self.helper.format_code(guess)
        self.attempt_history.append((self.attempts, guess_display, exact, misplaced))

        if (
            self.booster_callback is not None
            and not self.booster_awarded
            and await self.booster_callback(self.ctx, "mastermind")
        ):
            self.booster_awarded = True

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
        reward = base_reward + attempts_left * self.helper.config.attempt_bonus
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
        self._logger.debug(
            "Mastermind win",
            extra={
                "user_id": self.ctx.author.id,
                "secret": "-".join(self.secret),
                "attempts": self.attempts,
                "reward": reward,
                "base_reward": base_reward,
                "attempts_left": attempts_left,
            },
        )

    async def _handle_timeout(self) -> None:
        self.embed_color = Colors.ERROR
        self.status_lines = [
            "Temps écoulé !",
            f"Code secret : {self._secret_display()}",
            "Reviens tenter ta chance pour gagner des PB !",
        ]
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


class MillionaireRaceSession:
    """Gestion d'une session de Millionaire Race."""

    def __init__(self, ctx: commands.Context, database: Database) -> None:
        self.ctx = ctx
        self.database = database
        self.stage_index = 0
        self.total_pb = 0
        self.pets_awarded: list[str] = []
        self.boosters_awarded: list[str] = []
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
            self.last_feedback = [
                f"❌ Échec sur **{stage.label}** (chance de {chance_label}).",
                "Tu conserves les récompenses déjà gagnées.",
            ]
            return False

        feedback = [f"✅ **{stage.label}** franchie !"]

        if stage.prissbucks:
            try:
                await self.database.increment_balance(
                    self.ctx.author.id,
                    stage.prissbucks,
                    transaction_type="millionaire_race",
                    description=f"Épreuve {self.stage_index + 1} - {stage.label}",
                )
                self.total_pb += stage.prissbucks
                feedback.append(
                    f"+{embeds.format_currency(stage.prissbucks)} remportés."
                )
            except Exception:
                logger.exception("Impossible de créditer la Millionaire Race pour %s", self.ctx.author.id)
                feedback.append("Une erreur est survenue lors du crédit de tes PrissBucks.")

        pet_name: str | None = None
        if stage.pet_choices:
            pet_name = random.choice(stage.pet_choices)
            if await self._award_pet(pet_name):
                self.pets_awarded.append(pet_name)
                feedback.append(f"Tu obtiens {self._format_pet_name(pet_name)} !")
            else:
                feedback.append("Le pet bonus n'a pas pu être ajouté. Signale-le à un admin.")

        if stage.booster:
            multiplier, duration_seconds = stage.booster
            try:
                applied, _, extended, previous = await self.database.grant_pet_booster(
                    self.ctx.author.id,
                    multiplier=multiplier,
                    duration_seconds=duration_seconds,
                )
                label = self._format_booster_label(applied, duration_seconds)
                self.boosters_awarded.append(label)
                if extended and previous >= applied:
                    feedback.append(f"Booster prolongé : {label}")
                else:
                    feedback.append(f"Booster reçu : {label}")
            except Exception:
                logger.exception("Impossible d'attribuer le booster Millionaire Race à %s", self.ctx.author.id)
                feedback.append("Le booster n'a pas pu être activé.")

        self.stage_index += 1
        if self.stage_index >= len(MILLIONAIRE_RACE_STAGES):
            self.finished = True
            feedback.insert(0, "🎉 Tu atteins la ligne d'arrivée de la Millionaire Race !")
            if pet_name == HUGE_GALE_NAME:
                feedback.append(f"**{self._format_pet_name(HUGE_GALE_NAME)}** rejoint ton équipe !")

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
    def _format_booster_label(multiplier: float, duration_seconds: int) -> str:
        minutes = max(1, int(round(duration_seconds / 60)))
        return f"x{multiplier:g} • {minutes} min"

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
            reward_lines = [f"Chance de réussite : **{chance_pct}%**"]
            if stage.prissbucks:
                reward_lines.append(
                    f"PrissBucks : +{embeds.format_currency(stage.prissbucks)}"
                )
            if stage.pet_choices:
                pets_text = ", ".join(self._format_pet_name(name) for name in stage.pet_choices)
                reward_lines.append(f"Pet(s) garanti(s) : {pets_text}")
            if stage.booster:
                reward_lines.append(
                    f"Booster : {self._format_booster_label(stage.booster[0], stage.booster[1])}"
                )
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
        if self.boosters_awarded:
            summary_lines.append("Boosters : " + ", ".join(self.boosters_awarded))
        else:
            summary_lines.append("Boosters : aucun")
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
        self._booster_choices = PET_BOOSTER_CHOICES
        self._booster_weights = PET_BOOSTER_WEIGHTS
        self._active_race_players: set[int] = set()

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

    async def _maybe_award_pet_booster(self, ctx: commands.Context, source: str) -> bool:
        drop_rate = PET_BOOSTER_DROP_RATES.get(source, 0.0)
        if drop_rate <= 0 or random.random() > drop_rate:
            return False

        try:
            multiplier, minutes = random.choices(
                self._booster_choices, weights=self._booster_weights, k=1
            )[0]
        except IndexError:
            return False

        duration_seconds = minutes * 60
        try:
            (
                new_multiplier,
                expires_at,
                extended,
                previous_multiplier,
            ) = await self.database.grant_pet_booster(
                ctx.author.id,
                multiplier=multiplier,
                duration_seconds=duration_seconds,
            )
        except DatabaseError:
            logger.exception("Impossible d'attribuer un booster de pets")
            return False

        remaining_seconds = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
        remaining_display = _format_seconds(remaining_seconds)
        source_label = "Machine à sous" if source == "slots" else "Mastermind"

        lines: list[str] = []
        if extended and new_multiplier == previous_multiplier:
            lines.append(
                f"{ctx.author.mention}, ton booster x{new_multiplier:g} gagne **{minutes} minute(s)** supplémentaires !"
            )
        else:
            lines.append(
                f"{ctx.author.mention} décroche un booster x{new_multiplier:g} pendant **{minutes} minute(s)** grâce à {source_label} !"
            )
            if previous_multiplier > 1 and new_multiplier > previous_multiplier:
                lines.append(
                    f"Ton booster passe de x{previous_multiplier:g} à x{new_multiplier:g} !"
                )
        lines.append(f"Expiration dans {remaining_display}.")

        await ctx.send(
            embed=embeds.success_embed(
                "\n".join(lines), title="🎁 Booster de pets obtenu"
            )
        )

        logger.debug(
            "pet_booster_awarded",
            extra={
                "user_id": ctx.author.id,
                "source": source,
                "multiplier": new_multiplier,
                "minutes": minutes,
                "extended": extended,
            },
        )
        return True

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

    @staticmethod
    def _validate_give_request(author: discord.Member, target: discord.Member, amount: int) -> str | None:
        if target.bot:
            return "Tu ne peux pas donner de PB à un bot."
        if target == author:
            return "Impossible de te donner des PB."
        if amount <= 0:
            return "Le montant doit être supérieur à 0."
        return None

    @commands.command(name="give")
    async def give(self, ctx: commands.Context, member: discord.Member, amount: int) -> None:
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

    @commands.cooldown(1, 6, commands.BucketType.user)
    @commands.command(name="slots", aliases=("slot", "machine"))
    async def slots(self, ctx: commands.Context, bet: int = 100) -> None:
        """Jeu de machine à sous simple pour miser ses PB."""
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
        await self._maybe_award_pet_booster(ctx, "slots")

    @commands.cooldown(1, MASTERMIND_CONFIG.cooldown, commands.BucketType.user)
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

        session = MastermindSession(
            ctx,
            self.mastermind_helper,
            self.database,
            booster_callback=self._maybe_award_pet_booster,
        )
        try:
            await session.start()
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
