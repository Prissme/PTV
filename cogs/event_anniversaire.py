from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import discord
from discord.ext import commands, tasks

from utils import embeds

# ---------------------------------------------------------------------------
# Configuration de l'event Anniversaire
# ---------------------------------------------------------------------------

FESTIVE_COIN_EMOJI: str = "🎉"
STARTING_FESTIVE_COINS: int = 100
FESTIVE_EGG_PRICE: int = 100
INCOME_TICK_SECONDS: int = 60  # on crédite les gains toutes les 60s pour limiter les écritures DB


@dataclass(frozen=True)
class FestivePetDefinition:
    name: str
    image_url: str
    drop_rate: float  # entre 0 et 1
    income_per_second: int


# NOTE : l'émoji d'Ollie n'a pas encore été fourni/uploadé sur le serveur.
# Remplace OLLIE_EMOJI_ID ci-dessous dès que l'émoji "Ollie" existe.
OLLIE_EMOJI_ID: str = "REMPLACE_MOI"

FESTIVE_PETS: Tuple[FestivePetDefinition, ...] = (
    FestivePetDefinition(
        name="Festive Mandy",
        image_url="https://cdn.discordapp.com/emojis/1545748676012544070.png",
        drop_rate=0.70,
        income_per_second=1,
    ),
    FestivePetDefinition(
        name="Festive Piper",
        image_url="https://cdn.discordapp.com/emojis/1545751609743642725.png",
        drop_rate=0.25,
        income_per_second=3,
    ),
    FestivePetDefinition(
        name="Ollie",
        image_url=f"https://cdn.discordapp.com/emojis/{OLLIE_EMOJI_ID}.png",
        drop_rate=0.05,
        income_per_second=7,
    ),
)

FESTIVE_PET_MAP: Dict[str, FestivePetDefinition] = {
    pet.name: pet for pet in FESTIVE_PETS
}


def _roll_festive_pet() -> FestivePetDefinition:
    roll = random.random()
    cumulative = 0.0
    for pet in FESTIVE_PETS:
        cumulative += pet.drop_rate
        if roll <= cumulative:
            return pet
    return FESTIVE_PETS[-1]


class EventAnniversaire(commands.Cog):
    """Event Anniversaire : Festive Coins, œuf festif et pets festifs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._tables_ready = False
        self._last_tick_monotonic: float = time.monotonic()

    async def cog_load(self) -> None:
        await self._ensure_tables()
        self._income_tick.start()

    async def cog_unload(self) -> None:
        self._income_tick.cancel()

    async def _ensure_tables(self) -> None:
        if self._tables_ready:
            return
        pool = self.database.pool
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS festive_event_wallet (
                    user_id BIGINT PRIMARY KEY,
                    festive_coins BIGINT NOT NULL DEFAULT 0
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS festive_event_pets (
                    user_id BIGINT NOT NULL,
                    pet_name TEXT NOT NULL,
                    quantity BIGINT NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, pet_name)
                )
                """
            )
        self._tables_ready = True

    async def _ensure_wallet(self, connection, user_id: int) -> int:
        row = await connection.fetchrow(
            "SELECT festive_coins FROM festive_event_wallet WHERE user_id = $1",
            user_id,
        )
        if row is None:
            await connection.execute(
                """
                INSERT INTO festive_event_wallet (user_id, festive_coins)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
                STARTING_FESTIVE_COINS,
            )
            return STARTING_FESTIVE_COINS
        return int(row["festive_coins"])

    async def _get_balance(self, user_id: int) -> int:
        pool = self.database.pool
        async with pool.acquire() as connection:
            return await self._ensure_wallet(connection, user_id)

    async def _get_pets(self, user_id: int) -> Dict[str, int]:
        pool = self.database.pool
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT pet_name, quantity FROM festive_event_pets WHERE user_id = $1",
                user_id,
            )
        return {row["pet_name"]: int(row["quantity"]) for row in rows}

    async def _income_per_second_for(self, user_id: int) -> int:
        pets = await self._get_pets(user_id)
        total = 0
        for name, quantity in pets.items():
            definition = FESTIVE_PET_MAP.get(name)
            if definition:
                total += definition.income_per_second * quantity
        return total

    @tasks.loop(seconds=INCOME_TICK_SECONDS)
    async def _income_tick(self) -> None:
        elapsed = time.monotonic() - self._last_tick_monotonic
        self._last_tick_monotonic = time.monotonic()
        pool = self.database.pool
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT p.user_id AS user_id,
                       SUM(p.quantity * f.income_per_second) AS income_per_second
                FROM festive_event_pets p
                JOIN (VALUES
                    ('Festive Mandy', 1),
                    ('Festive Piper', 3),
                    ('Ollie', 7)
                ) AS f(pet_name, income_per_second)
                ON f.pet_name = p.pet_name
                GROUP BY p.user_id
                """
            )
            for row in rows:
                gain = int(row["income_per_second"]) * elapsed
                if gain <= 0:
                    continue
                await connection.execute(
                    """
                    INSERT INTO festive_event_wallet (user_id, festive_coins)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id)
                    DO UPDATE SET festive_coins = festive_event_wallet.festive_coins + EXCLUDED.festive_coins
                    """,
                    int(row["user_id"]),
                    int(gain),
                )

    @_income_tick.before_loop
    async def _before_income_tick(self) -> None:
        await self.bot.wait_until_ready()
        self._last_tick_monotonic = time.monotonic()

    def _pet_summary_lines(self, pets: Dict[str, int]) -> list[str]:
        lines: list[str] = []
        for definition in FESTIVE_PETS:
            quantity = pets.get(definition.name, 0)
            if quantity <= 0:
                continue
            lines.append(
                f"• {definition.name} x{quantity} "
                f"({definition.income_per_second}/s chacun)"
            )
        return lines

    @commands.command(name="festivecoins", aliases=("festif", "fcoins"))
    async def festivecoins(self, ctx: commands.Context) -> None:
        """Affiche ton solde de Festive Coins et tes pets festifs."""
        balance = await self._get_balance(ctx.author.id)
        pets = await self._get_pets(ctx.author.id)
        income = await self._income_per_second_for(ctx.author.id)

        lines = [f"{FESTIVE_COIN_EMOJI} Solde : **{balance}** Festive Coins"]
        pet_lines = self._pet_summary_lines(pets)
        if pet_lines:
            lines.append("")
            lines.append("🐾 Tes pets festifs :")
            lines.extend(pet_lines)
            lines.append("")
            lines.append(f"Gains totaux : **{income}** Festive Coins / seconde")
        else:
            lines.append("")
            lines.append(
                f"Tu n'as encore aucun pet festif. Utilise `!oeuffestif` "
                f"pour en obtenir un ({FESTIVE_EGG_PRICE} Festive Coins)."
            )

        embed = embeds.info_embed(
            "\n".join(lines), title="🎂 Event Anniversaire"
        )
        await ctx.send(embed=embed)

    @commands.command(name="oeuffestif", aliases=("festivegg", "oeufanniversaire"))
    async def oeuffestif(self, ctx: commands.Context) -> None:
        """Achète et ouvre un œuf festif pour 100 Festive Coins."""
        user_id = ctx.author.id
        pool = self.database.pool
        async with pool.acquire() as connection:
            async with connection.transaction():
                balance = await self._ensure_wallet(connection, user_id)
                if balance < FESTIVE_EGG_PRICE:
                    await ctx.send(
                        embed=embeds.error_embed(
                            f"Il te faut **{FESTIVE_EGG_PRICE}** Festive Coins pour "
                            f"acheter un œuf festif (tu as {balance})."
                        )
                    )
                    return

                await connection.execute(
                    """
                    UPDATE festive_event_wallet
                    SET festive_coins = festive_coins - $2
                    WHERE user_id = $1
                    """,
                    user_id,
                    FESTIVE_EGG_PRICE,
                )

                pet = _roll_festive_pet()

                await connection.execute(
                    """
                    INSERT INTO festive_event_pets (user_id, pet_name, quantity)
                    VALUES ($1, $2, 1)
                    ON CONFLICT (user_id, pet_name)
                    DO UPDATE SET quantity = festive_event_pets.quantity + 1
                    """,
                    user_id,
                    pet.name,
                )

        embed = embeds.success_embed(
            f"Tu as obtenu **{pet.name}** ! "
            f"({int(pet.drop_rate * 100)}% de chance — {pet.income_per_second} "
            f"Festive Coin{'s' if pet.income_per_second > 1 else ''}/seconde)",
            title="🥚 Œuf festif ouvert !",
        )
        embed.set_thumbnail(url=pet.image_url)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventAnniversaire(bot))
