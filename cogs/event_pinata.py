from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from utils import embeds

# ---------------------------------------------------------------------------
# Configuration — facilement ajustable
# ---------------------------------------------------------------------------

PINATA_BASE_INCOME_PER_SECOND: float = 50.0  # niveau 1
PINATA_LEVEL_MULTIPLIER: float = 2.0  # revenu x2 par niveau

BASE_COOLDOWN_SECONDS: float = 10.0
COOLDOWN_REDUCTION_PER_UPGRADE: float = 0.4
MAX_COOLDOWN_UPGRADES: int = 20  # -> plancher 10 - 20*0.4 = 2s

BASE_UPGRADE_CHANCE: float = 1 / 1000
CHANCE_BONUS_PER_UPGRADE: float = 0.04  # +4 points de % par achat
MAX_CHANCE_UPGRADES: int = 20  # -> +80 points de % max

CASH_BONUS_PER_UPGRADE: float = 0.10  # +10% de revenu par achat
MAX_CASH_UPGRADES: int = 50  # -> +500% max

# Coût de départ + facteur exponentiel par type d'upgrade.
UPGRADE_BASE_COSTS: dict[str, float] = {
    "cooldown": 50.0,
    "chance": 100.0,
    "cash": 21_350.0,  # calibré pour ~1 semaine de grind total (voir calcul), même durée que l'event
}
UPGRADE_COST_RATIO: float = 1.15

UPGRADE_LABELS: dict[str, str] = {
    "cooldown": "cooldown (-0.4s/achat)",
    "chance": "chance d'upgrade (+4%/achat)",
    "cash": "production (+10%/achat)",
}


def _upgrade_cost(upgrade_type: str, current_count: int) -> int:
    base = UPGRADE_BASE_COSTS[upgrade_type]
    return int(round(base * (UPGRADE_COST_RATIO ** current_count)))


def _max_for(upgrade_type: str) -> int:
    return {
        "cooldown": MAX_COOLDOWN_UPGRADES,
        "chance": MAX_CHANCE_UPGRADES,
        "cash": MAX_CASH_UPGRADES,
    }[upgrade_type]


class EventPinata(commands.Cog):
    """Event Anniversaire : la Piñata (dollars, upgrades de niveau et de stats)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._tables_ready = False

    async def cog_load(self) -> None:
        await self._ensure_tables()

    async def _ensure_tables(self) -> None:
        if self._tables_ready:
            return
        pool = self.database.pool
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pinata_event (
                    user_id BIGINT PRIMARY KEY,
                    dollars DOUBLE PRECISION NOT NULL DEFAULT 0,
                    last_income_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    pinata_level INT NOT NULL DEFAULT 1,
                    last_attempt_at TIMESTAMPTZ,
                    cooldown_upgrades INT NOT NULL DEFAULT 0,
                    chance_upgrades INT NOT NULL DEFAULT 0,
                    cash_upgrades INT NOT NULL DEFAULT 0,
                    gift_announced BOOLEAN NOT NULL DEFAULT FALSE
                )
                """
            )
            await connection.execute(
                """
                ALTER TABLE pinata_event
                ADD COLUMN IF NOT EXISTS gift_announced BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
        self._tables_ready = True

    # ------------------------------------------------------------------
    # Calculs
    # ------------------------------------------------------------------

    @staticmethod
    def _income_per_second(level: int, cash_upgrades: int) -> float:
        base = PINATA_BASE_INCOME_PER_SECOND * (PINATA_LEVEL_MULTIPLIER ** (level - 1))
        return base * (1 + CASH_BONUS_PER_UPGRADE * cash_upgrades)

    @staticmethod
    def _cooldown_seconds(cooldown_upgrades: int) -> float:
        return max(
            0.0,
            BASE_COOLDOWN_SECONDS - COOLDOWN_REDUCTION_PER_UPGRADE * cooldown_upgrades,
        )

    @staticmethod
    def _upgrade_chance(chance_upgrades: int) -> float:
        return min(1.0, BASE_UPGRADE_CHANCE + CHANCE_BONUS_PER_UPGRADE * chance_upgrades)

    async def _ensure_row(self, connection, user_id: int):
        row = await connection.fetchrow(
            "SELECT * FROM pinata_event WHERE user_id = $1",
            user_id,
        )
        if row is None:
            await connection.execute(
                """
                INSERT INTO pinata_event (user_id, dollars, last_income_at)
                VALUES ($1, 0, now())
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
            )
            row = await connection.fetchrow(
                "SELECT * FROM pinata_event WHERE user_id = $1",
                user_id,
            )
        return row

    async def _settle_income(self, connection, user_id: int):
        """Crédite les dollars accumulés depuis la dernière visite. Renvoie la row à jour."""
        row = await self._ensure_row(connection, user_id)
        elapsed = (datetime.now(timezone.utc) - row["last_income_at"]).total_seconds()
        elapsed = max(0.0, elapsed)
        income_per_second = self._income_per_second(row["pinata_level"], row["cash_upgrades"])
        new_dollars = float(row["dollars"]) + income_per_second * elapsed

        await connection.execute(
            """
            UPDATE pinata_event
            SET dollars = $2, last_income_at = now()
            WHERE user_id = $1
            """,
            user_id,
            new_dollars,
        )
        return await connection.fetchrow(
            "SELECT * FROM pinata_event WHERE user_id = $1", user_id
        )

    async def get_dollars(self, user_id: int) -> float:
        pool = self.database.pool
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await self._settle_income(connection, user_id)
                return float(row["dollars"])

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------

    @commands.command(name="pinata")
    async def pinata(self, ctx: commands.Context) -> None:
        """Affiche l'état de ta piñata, ou tente un upgrade si le cooldown est écoulé."""
        user_id = ctx.author.id
        pool = self.database.pool

        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await self._settle_income(connection, user_id)

                cooldown = self._cooldown_seconds(row["cooldown_upgrades"])
                last_attempt = row["last_attempt_at"]
                now = datetime.now(timezone.utc)
                remaining = (
                    cooldown - (now - last_attempt).total_seconds()
                    if last_attempt is not None
                    else 0.0
                )

                if remaining > 0:
                    income = self._income_per_second(row["pinata_level"], row["cash_upgrades"])
                    await ctx.send(
                        embed=embeds.info_embed(
                            f"🪅 Piñata niveau **{row['pinata_level']}** — "
                            f"**{income:.1f}$/s**\n"
                            f"💵 Solde : **{row['dollars']:.1f}$**\n"
                            f"⏳ Prochain essai dans **{remaining:.1f}s**.",
                            title="Piñata de l'event",
                        )
                    )
                    return

                await connection.execute(
                    "UPDATE pinata_event SET last_attempt_at = now() WHERE user_id = $1",
                    user_id,
                )
                chance = self._upgrade_chance(row["chance_upgrades"])
                success = random.random() < chance
                level = int(row["pinata_level"])
                cash_upgrades = int(row["cash_upgrades"])
                cooldown_upgrades = int(row["cooldown_upgrades"])

                if success:
                    await connection.execute(
                        "UPDATE pinata_event SET pinata_level = pinata_level + 1 WHERE user_id = $1",
                        user_id,
                    )
                    level += 1

        income = self._income_per_second(level, cash_upgrades)
        if success:
            await ctx.send(
                embed=embeds.success_embed(
                    f"🎉 Ta piñata passe au **niveau {level}** ! "
                    f"Elle rapporte maintenant **{income:.1f}$/s**.",
                    title="🪅 Upgrade réussi !",
                )
            )
        else:
            await ctx.send(
                embed=embeds.info_embed(
                    f"🪅 Pas d'upgrade cette fois... Ta piñata niveau **{level}** "
                    f"continue de rapporter **{income:.1f}$/s**.\n"
                    f"Réessaie dans {self._cooldown_seconds(cooldown_upgrades):.1f}s.",
                    title="Piñata secouée",
                )
            )

    @commands.command(name="pinatashop", aliases=("pinataupgrade", "pinataboutique"))
    async def pinatashop(self, ctx: commands.Context, *, args: str | None = None) -> None:
        """Achète une amélioration de piñata : `e!pinatashop cooldown|chance|cash`."""
        user_id = ctx.author.id
        choice = (args or "").strip().lower()

        if choice not in UPGRADE_BASE_COSTS:
            pool = self.database.pool
            async with pool.acquire() as connection:
                async with connection.transaction():
                    row = await self._settle_income(connection, user_id)

            lines = [f"💵 Solde : **{row['dollars']:.1f}$**", ""]
            for key in ("cooldown", "chance", "cash"):
                count = row[f"{key}_upgrades"]
                cap = _max_for(key)
                cost = _upgrade_cost(key, count) if count < cap else None
                cost_text = f"{cost}$" if cost is not None else "MAX"
                lines.append(
                    f"• `{key}` — {UPGRADE_LABELS[key]} : {count}/{cap} "
                    f"(prochain : {cost_text})"
                )
            lines.append("")
            lines.append("Achète avec `e!pinatashop cooldown`, `e!pinatashop chance` ou `e!pinatashop cash`.")
            await ctx.send(
                embed=embeds.info_embed("\n".join(lines), title="🪅 Boutique de la piñata")
            )
            return

        pool = self.database.pool
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await self._settle_income(connection, user_id)
                count = int(row[f"{choice}_upgrades"])
                cap = _max_for(choice)

                if count >= cap:
                    await ctx.send(
                        embed=embeds.error_embed(
                            f"Tu as déjà atteint le max pour `{choice}` ({cap}/{cap})."
                        )
                    )
                    return

                cost = _upgrade_cost(choice, count)
                if float(row["dollars"]) < cost:
                    await ctx.send(
                        embed=embeds.error_embed(
                            f"Il te faut **{cost}$** pour cet upgrade (tu as {row['dollars']:.1f}$)."
                        )
                    )
                    return

                await connection.execute(
                    f"""
                    UPDATE pinata_event
                    SET dollars = dollars - $2, {choice}_upgrades = {choice}_upgrades + 1
                    WHERE user_id = $1
                    """,
                    user_id,
                    cost,
                )

                new_counts = {
                    "cooldown": int(row["cooldown_upgrades"]) + (1 if choice == "cooldown" else 0),
                    "chance": int(row["chance_upgrades"]) + (1 if choice == "chance" else 0),
                    "cash": int(row["cash_upgrades"]) + (1 if choice == "cash" else 0),
                }
                fully_maxed = (
                    new_counts["cooldown"] >= MAX_COOLDOWN_UPGRADES
                    and new_counts["chance"] >= MAX_CHANCE_UPGRADES
                    and new_counts["cash"] >= MAX_CASH_UPGRADES
                )
                already_announced = bool(row["gift_announced"])
                if fully_maxed and not already_announced:
                    await connection.execute(
                        "UPDATE pinata_event SET gift_announced = TRUE WHERE user_id = $1",
                        user_id,
                    )

        await ctx.send(
            embed=embeds.success_embed(
                f"Upgrade **{UPGRADE_LABELS[choice]}** acheté pour **{cost}$** "
                f"({count + 1}/{cap}).",
                title="🪅 Piñata améliorée !",
            )
        )

        if fully_maxed and not already_announced:
            await ctx.send(
                embed=embeds.success_embed(
                    "🎉 **Félicitations !** Tu as maxé les 3 upgrades de ta piñata !\n\n"
                    "Tu débloques l'**œuf cadeau** (`e!oeufcadeau`) — 1 000 000 Festive Coins "
                    "pour tenter d'obtenir un pet Huge festif exclusif.",
                    title="🏆 Piñata entièrement maîtrisée !",
                )
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventPinata(bot))
