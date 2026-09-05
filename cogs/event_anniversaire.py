from __future__ import annotations

import asyncio
import random

import discord
from discord.ext import commands

from config import (
    FESTIVE_COIN_INCOME_PER_SECOND,
    FESTIVE_EGG_PRICE,
    FESTIVE_EVENT_PET_NAMES,
    FESTIVE_PET_DROP_RATES,
)
from utils import embeds

FESTIVE_COIN_EMOJI: str = "🎉"
STARTING_FESTIVE_COINS: int = 100

_FESTIVE_PET_IMAGES = {
    "Festive Mandy": "https://cdn.discordapp.com/emojis/1545748676012544070.png",
    "Festive Piper": "https://cdn.discordapp.com/emojis/1545751609743642725.png",
    "Ollie": "https://cdn.discordapp.com/emojis/1545752797482459237.png",
}


def _roll_festive_pet_name() -> str:
    roll = random.random()
    cumulative = 0.0
    for name in FESTIVE_EVENT_PET_NAMES:
        cumulative += FESTIVE_PET_DROP_RATES[name]
        if roll <= cumulative:
            return name
    return FESTIVE_EVENT_PET_NAMES[-1]


class EventAnniversaire(commands.Cog):
    """Event Anniversaire : Festive Coins + œuf festif.

    Les pets festifs sont de vrais pets du catalogue (voir config._FESTIVE_EVENT_PETS) :
    ils sont ajoutés via `add_user_pet` comme n'importe quel autre pet, et doivent être
    équipés avec les commandes normales `e!equip` / `e!unequip` (mêmes slots actifs que
    les pets classiques). Seuls les pets festifs actuellement ÉQUIPÉS rapportent des
    Festive Coins ; leur `base_income_per_hour` est à 0 donc ils ne touchent pas au PB.
    """

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
            await connection.execute(
                """
                ALTER TABLE festive_event_wallet
                ADD COLUMN IF NOT EXISTS last_income_at TIMESTAMPTZ NOT NULL DEFAULT now()
                """
            )
        self._tables_ready = True

    # ------------------------------------------------------------------
    # Pets festifs équipés — lecture directe dans la vraie table user_pets
    # ------------------------------------------------------------------

    async def _equipped_festive_counts_connection(self, connection, user_id: int) -> dict[str, int]:
        rows = await connection.fetch(
            """
            SELECT p.name AS name, COUNT(*) AS active_count
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE up.user_id = $1 AND up.is_active AND p.name = ANY($2::text[])
            GROUP BY p.name
            """,
            user_id,
            list(FESTIVE_EVENT_PET_NAMES),
        )
        return {row["name"]: int(row["active_count"]) for row in rows}

    async def _owned_festive_counts(self, user_id: int) -> dict[str, tuple[int, int]]:
        """Renvoie {nom: (possédés, équipés)} pour chaque pet festif."""
        pool = self.database.pool
        async with pool.acquire() as connection:
            owned_rows = await connection.fetch(
                """
                SELECT p.name AS name, COUNT(*) AS owned_count
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                WHERE up.user_id = $1 AND p.name = ANY($2::text[])
                GROUP BY p.name
                """,
                user_id,
                list(FESTIVE_EVENT_PET_NAMES),
            )
            equipped = await self._equipped_festive_counts_connection(connection, user_id)
        owned = {row["name"]: int(row["owned_count"]) for row in owned_rows}
        return {
            name: (owned.get(name, 0), equipped.get(name, 0))
            for name in FESTIVE_EVENT_PET_NAMES
        }

    async def _income_per_second_for_connection(self, connection, user_id: int) -> int:
        equipped = await self._equipped_festive_counts_connection(connection, user_id)
        return sum(
            FESTIVE_COIN_INCOME_PER_SECOND[name] * count
            for name, count in equipped.items()
        )

    # ------------------------------------------------------------------
    # Solde / gains
    # ------------------------------------------------------------------

    async def _settle_income(self, connection, user_id: int) -> int:
        """Crédite les Festive Coins gagnés depuis la dernière visite (pets équipés uniquement)."""
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

    @commands.command(name="festivecoins", aliases=("festif", "fcoins"))
    async def festivecoins(self, ctx: commands.Context) -> None:
        """Affiche ton solde de Festive Coins et l'état de tes pets festifs."""
        balance = await self.get_balance(ctx.author.id)
        counts = await self._owned_festive_counts(ctx.author.id)
        income = sum(
            FESTIVE_COIN_INCOME_PER_SECOND[name] * equipped
            for name, (_, equipped) in counts.items()
        )

        lines = [f"{FESTIVE_COIN_EMOJI} Solde : **{balance}** Festive Coins"]
        pet_lines = [
            f"• {name} : {owned} possédé(s), {equipped} équipé(s) "
            f"({FESTIVE_COIN_INCOME_PER_SECOND[name]}/s chacun)"
            for name, (owned, equipped) in counts.items()
            if owned > 0
        ]
        if pet_lines:
            lines.append("")
            lines.append("🐾 Tes pets festifs :")
            lines.extend(pet_lines)
            lines.append("")
            lines.append(f"Gains totaux : **{income}** Festive Coins / seconde")
            lines.append(
                "Utilise `e!equip <pet>` / `e!unequip <pet>` (comme pour tes autres "
                "pets) pour gérer lesquels sont actifs."
            )
        else:
            lines.append("")
            lines.append(
                f"Tu n'as encore aucun pet festif. Utilise `!oeuffestif` "
                f"pour en obtenir un ({FESTIVE_EGG_PRICE} Festive Coins)."
            )

        embed = embeds.info_embed("\n".join(lines), title="🎂 Event Anniversaire")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Œuf festif
    # ------------------------------------------------------------------

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

        pet_name = _roll_festive_pet_name()
        pet_id = await self.database.get_pet_id_by_name(pet_name)
        if pet_id is None:
            await ctx.send(
                embed=embeds.error_embed(
                    "Le catalogue des pets festifs n'est pas encore synchronisé, réessaie dans un instant."
                )
            )
            return

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

        # Ajouté via le système de pets standard (mêmes tables que les autres œufs).
        await self.database.add_user_pet(user_id, pet_id)

        message = await self._play_egg_animation(ctx)

        income_per_second = FESTIVE_COIN_INCOME_PER_SECOND[pet_name]
        drop_rate = FESTIVE_PET_DROP_RATES[pet_name]
        embed = embeds.success_embed(
            f"Tu as obtenu **{pet_name}** ! "
            f"({int(drop_rate * 100)}% de chance — {income_per_second} "
            f"Festive Coin{'s' if income_per_second > 1 else ''}/seconde une fois équipé)\n\n"
            f"Il est dans ton inventaire mais pas encore équipé : utilise "
            f"`e!equip {pet_name}` pour qu'il commence à rapporter des Festive Coins "
            f"(mêmes emplacements que tes autres pets).",
            title="🥚 Œuf festif ouvert !",
        )
        embed.set_image(url=_FESTIVE_PET_IMAGES[pet_name])
        await message.edit(content=None, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventAnniversaire(bot))
