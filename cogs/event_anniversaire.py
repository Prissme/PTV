from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Dict, Tuple

import discord
from discord.ext import commands

from utils import embeds

# ---------------------------------------------------------------------------
# Configuration de l'event Anniversaire
# ---------------------------------------------------------------------------

FESTIVE_COIN_EMOJI: str = "🎉"
STARTING_FESTIVE_COINS: int = 100
FESTIVE_EGG_PRICE: int = 100


@dataclass(frozen=True)
class FestivePetDefinition:
    name: str
    image_url: str
    drop_rate: float  # entre 0 et 1
    income_per_second: int


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
        image_url="https://cdn.discordapp.com/emojis/1545752797482459237.png",
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

    async def cog_load(self) -> None:
        await self._ensure_tables()

    async def _ensure_tables(self) -> None:
        if self._tables_ready:
            return
        pool = self.database.pool
        async with pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS festive_event_wallet (
                    user_id BIGINT PRIMARY KEY,
                    festive_coins BIGINT NOT NULL DEFAULT 0,
                    last_income_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # Ancienne version de la table créée sans cette colonne : on la rajoute si besoin.
            await connection.execute(
                """
                ALTER TABLE festive_event_wallet
                ADD COLUMN IF NOT EXISTS last_income_at TIMESTAMPTZ NOT NULL DEFAULT now()
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

    async def _income_per_second_for_connection(self, connection, user_id: int) -> int:
        rows = await connection.fetch(
            "SELECT pet_name, quantity FROM festive_event_pets WHERE user_id = $1",
            user_id,
        )
        total = 0
        for row in rows:
            definition = FESTIVE_PET_MAP.get(row["pet_name"])
            if definition:
                total += definition.income_per_second * int(row["quantity"])
        return total

    async def _settle_income(self, connection, user_id: int) -> int:
        """Crédite les Festive Coins accumulés depuis la dernière visite et renvoie le solde à jour."""
        row = await connection.fetchrow(
            """
            SELECT festive_coins, EXTRACT(EPOCH FROM (now() - last_income_at)) AS elapsed
            FROM festive_event_wallet
            WHERE user_id = $1
            """,
            user_id,
        )
        if row is None:
            await connection.execute(
                """
                INSERT INTO festive_event_wallet (user_id, festive_coins, last_income_at)
                VALUES ($1, $2, now())
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
                STARTING_FESTIVE_COINS,
            )
            return STARTING_FESTIVE_COINS

        elapsed = max(0.0, float(row["elapsed"] or 0.0))
        income_per_second = await self._income_per_second_for_connection(connection, user_id)
        gained = int(income_per_second * elapsed)
        new_balance = int(row["festive_coins"]) + gained

        await connection.execute(
            """
            UPDATE festive_event_wallet
            SET festive_coins = $2, last_income_at = now()
            WHERE user_id = $1
            """,
            user_id,
            new_balance,
        )
        return new_balance

    async def get_balance(self, user_id: int) -> int:
        pool = self.database.pool
        async with pool.acquire() as connection:
            async with connection.transaction():
                return await self._settle_income(connection, user_id)

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
        balance = await self.get_balance(ctx.author.id)
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

    async def _play_egg_animation(
        self, ctx: commands.Context, *, egg_emoji: str = "🥚"
    ) -> discord.Message:
        """Reproduit l'animation d'ouverture standard utilisée pour les autres œufs."""
        animation_steps = (
            ("Œuf festif", "L'œuf commence à bouger…"),
            ("Œuf festif", "Des fissures apparaissent !"),
            ("Œuf festif", "Ça y est, il est sur le point d'éclore !"),
        )
        step_delay = 1.1
        reveal_delay = 1.2

        message = await ctx.send(
            content=egg_emoji,
            embed=embeds.pet_animation_embed(
                title=animation_steps[0][0],
                description=animation_steps[0][1],
                emoji=egg_emoji,
            ),
        )
        for title, description in animation_steps[1:]:
            await asyncio.sleep(step_delay)
            await message.edit(
                content=egg_emoji,
                embed=embeds.pet_animation_embed(
                    title=title,
                    description=description,
                    emoji=egg_emoji,
                ),
            )
        await asyncio.sleep(reveal_delay)
        return message

    @commands.command(name="oeuffestif", aliases=("festivegg", "oeufanniversaire"))
    async def oeuffestif(self, ctx: commands.Context) -> None:
        """Achète et ouvre un œuf festif pour 100 Festive Coins."""
        user_id = ctx.author.id
        pool = self.database.pool
        async with pool.acquire() as connection:
            async with connection.transaction():
                balance = await self._settle_income(connection, user_id)
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

        message = await self._play_egg_animation(ctx)

        embed = embeds.success_embed(
            f"Tu as obtenu **{pet.name}** ! "
            f"({int(pet.drop_rate * 100)}% de chance — {pet.income_per_second} "
            f"Festive Coin{'s' if pet.income_per_second > 1 else ''}/seconde)",
            title="🥚 Œuf festif ouvert !",
        )
        embed.set_image(url=pet.image_url)
        await message.edit(content=None, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventAnniversaire(bot))
