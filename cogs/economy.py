"""Fonctionnalités économiques essentielles : balance, daily et récompenses."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

import discord
from discord.ext import commands

from config import DAILY_COOLDOWN, DAILY_REWARD, MESSAGE_COOLDOWN, MESSAGE_REWARD, PREFIX
from utils import embeds
from database.db import DatabaseError

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

MASTERMIND_COLOR_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("rouge", "🔴", ("r", "red")),
    ("bleu", "🔵", ("b", "blue")),
    ("vert", "🟢", ("v", "green", "g")),
    ("jaune", "🟡", ("j", "yellow", "y")),
    ("violet", "🟣", ("violet", "violette", "pourpre", "purple", "p")),
    ("orange", "🟠", ("o",)),
)
MASTERMIND_COLORS: tuple[tuple[str, str], ...] = tuple(
    (name, emoji) for name, emoji, _ in MASTERMIND_COLOR_DEFINITIONS
)
MASTERMIND_ALIASES: dict[str, str] = {}
for color_name, _emoji, aliases in MASTERMIND_COLOR_DEFINITIONS:
    MASTERMIND_ALIASES[color_name] = color_name
    for alias in aliases:
        MASTERMIND_ALIASES[alias.lower()] = color_name

MASTERMIND_COLOR_NAMES: tuple[str, ...] = tuple(name for name, _ in MASTERMIND_COLORS)
MASTERMIND_EMOJIS: dict[str, str] = {name: emoji for name, emoji in MASTERMIND_COLORS}
MASTERMIND_CODE_LENGTH = 4
MASTERMIND_MAX_ATTEMPTS = 8
MASTERMIND_RESPONSE_TIMEOUT = 60
MASTERMIND_BASE_REWARD = (120, 200)
MASTERMIND_ATTEMPT_BONUS = 20
MASTERMIND_CANCEL_WORDS = {"stop", "annuler", "cancel"}
MASTERMIND_COOLDOWN = 60
MASTERMIND_AVAILABLE_NAMES = ", ".join(name.capitalize() for name in MASTERMIND_COLOR_NAMES)


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

    def _generate_mastermind_code(self) -> list[str]:
        return [random.choice(MASTERMIND_COLOR_NAMES) for _ in range(MASTERMIND_CODE_LENGTH)]

    def _format_mastermind_code(self, code: Sequence[str], *, include_names: bool = False) -> str:
        if include_names:
            return " ".join(f"{MASTERMIND_EMOJIS[color]} {color.capitalize()}" for color in code)
        return " ".join(MASTERMIND_EMOJIS[color] for color in code)

    def _parse_mastermind_guess(self, raw: str) -> tuple[list[str], str | None]:
        tokens = [token for token in raw.replace(",", " ").replace(";", " ").split() if token]
        if len(tokens) != MASTERMIND_CODE_LENGTH:
            return [], f"Ta combinaison doit contenir exactement {MASTERMIND_CODE_LENGTH} couleurs."

        guess: list[str] = []
        for token in tokens:
            canonical = MASTERMIND_ALIASES.get(token.lower())
            if canonical is None:
                return [], f"Couleur inconnue `{token}`. Choisis parmi : {MASTERMIND_AVAILABLE_NAMES}."
            guess.append(canonical)

        return guess, None

    @staticmethod
    def _evaluate_mastermind_guess(secret: Sequence[str], guess: Sequence[str]) -> tuple[int, int]:
        exact = sum(s == g for s, g in zip(secret, guess))
        secret_counts = Counter(secret)
        guess_counts = Counter(guess)
        color_matches = sum(min(secret_counts[color], guess_counts[color]) for color in secret_counts)
        misplaced = color_matches - exact
        return exact, misplaced

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

    @commands.command(name="give")
    async def give(self, ctx: commands.Context, member: discord.Member, amount: int) -> None:
        if member.bot:
            await ctx.send(embed=embeds.error_embed("Tu ne peux pas donner de PB à un bot."))
            return
        if member == ctx.author:
            await ctx.send(embed=embeds.error_embed("Impossible de te donner des PB."))
            return
        if amount <= 0:
            await ctx.send(embed=embeds.error_embed("Le montant doit être supérieur à 0."))
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

    @commands.cooldown(1, MASTERMIND_COOLDOWN, commands.BucketType.user)
    @commands.command(name="mastermind", aliases=("mm", "code"))
    async def mastermind(self, ctx: commands.Context) -> None:
        """Mini-jeu de Mastermind pour gagner quelques PB."""
        try:
            await self.database.ensure_user(ctx.author.id)
            secret = self._generate_mastermind_code()
            
            # Envoyer le message de départ
            await ctx.send(
                embed=embeds.mastermind_start_embed(
                    member=ctx.author,
                    palette=MASTERMIND_COLORS,
                    code_length=MASTERMIND_CODE_LENGTH,
                    max_attempts=MASTERMIND_MAX_ATTEMPTS,
                    timeout=MASTERMIND_RESPONSE_TIMEOUT,
                )
            )

            attempts = 0
            while attempts < MASTERMIND_MAX_ATTEMPTS:
                try:
                    guess_message = await self.bot.wait_for(
                        "message",
                        timeout=MASTERMIND_RESPONSE_TIMEOUT,
                        check=lambda message: message.author == ctx.author and message.channel == ctx.channel,
                    )
                except asyncio.TimeoutError:
                    secret_display = self._format_mastermind_code(secret, include_names=True)
                    await ctx.send(
                        embed=embeds.mastermind_failure_embed(
                            member=ctx.author,
                            reason="Temps écoulé !",
                            secret_display=secret_display,
                        )
                    )
                    logger.debug(
                        "Mastermind timeout",
                        extra={
                            "user_id": ctx.author.id,
                            "secret": "-".join(secret),
                            "attempts": attempts,
                        },
                    )
                    return

                content = guess_message.content.strip()
                if not content:
                    continue

                lowered = content.lower()
                if lowered in MASTERMIND_CANCEL_WORDS:
                    await ctx.send(embed=embeds.mastermind_cancelled_embed(member=ctx.author))
                    logger.debug(
                        "Mastermind cancelled",
                        extra={
                            "user_id": ctx.author.id,
                            "secret": "-".join(secret),
                            "attempts": attempts,
                        },
                    )
                    return

                guess, error = self._parse_mastermind_guess(content)
                if error:
                    await ctx.send(embed=embeds.warning_embed(error))
                    continue

                attempts += 1
                exact, misplaced = self._evaluate_mastermind_guess(secret, guess)
                attempts_left = MASTERMIND_MAX_ATTEMPTS - attempts

                guess_display = self._format_mastermind_code(guess)
                await ctx.send(
                    embed=embeds.mastermind_feedback_embed(
                        member=ctx.author,
                        attempt=attempts,
                        max_attempts=MASTERMIND_MAX_ATTEMPTS,
                        guess_display=guess_display,
                        well_placed=exact,
                        misplaced=misplaced,
                        attempts_left=attempts_left,
                    )
                )

                if exact == MASTERMIND_CODE_LENGTH:
                    base_reward = random.randint(*MASTERMIND_BASE_REWARD)
                    reward = base_reward + attempts_left * MASTERMIND_ATTEMPT_BONUS
                    _, balance_after = await self.database.increment_balance(
                        ctx.author.id,
                        reward,
                        transaction_type="mastermind_win",
                        description=f"Mastermind gagné en {attempts} tentatives",
                    )
                    secret_display = self._format_mastermind_code(secret, include_names=True)
                    await ctx.send(
                        embed=embeds.mastermind_victory_embed(
                            member=ctx.author,
                            attempts_used=attempts,
                            attempts_left=attempts_left,
                            secret_display=secret_display,
                            reward=reward,
                            balance_after=balance_after,
                        )
                    )
                    logger.debug(
                        "Mastermind win",
                        extra={
                            "user_id": ctx.author.id,
                            "secret": "-".join(secret),
                            "attempts": attempts,
                            "reward": reward,
                            "base_reward": base_reward,
                            "attempts_left": attempts_left,
                        },
                    )
                    return

            secret_display = self._format_mastermind_code(secret, include_names=True)
            await ctx.send(
                embed=embeds.mastermind_failure_embed(
                    member=ctx.author,
                    reason="Toutes les tentatives ont été utilisées.",
                    secret_display=secret_display,
                )
            )
            logger.debug(
                "Mastermind loss",
                extra={
                    "user_id": ctx.author.id,
                    "secret": "-".join(secret),
                    "attempts": attempts,
                },
            )
        except Exception as e:
            logger.exception("Erreur dans Mastermind", exc_info=e)
            await ctx.send(embed=embeds.error_embed("Une erreur est survenue. Réessaie dans quelques instants."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
