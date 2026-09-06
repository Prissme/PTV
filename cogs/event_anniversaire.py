from __future__ import annotations

import asyncio
import random

import discord
from discord.ext import commands

from config import (
    FESTIVE_COIN_INCOME_PER_SECOND,
    FESTIVE_EGG_PRICE,
    FESTIVE_EVENT_PET_NAMES,
    FESTIVE_GIFT_EGG_PRICE,
    FESTIVE_GIFT_PET_DROP_RATES,
    FESTIVE_PET_DROP_RATES,
    get_huge_multiplier,
)
from database.db import ActivePetLimitError, DatabaseError
from utils import embeds

FESTIVE_COIN_EMOJI: str = "🎉"
STARTING_FESTIVE_COINS: int = 100

_FESTIVE_PET_IMAGES = {
    "Festive Mandy": "https://cdn.discordapp.com/emojis/1545748676012544070.png",
    "Festive Piper": "https://cdn.discordapp.com/emojis/1545751609743642725.png",
    "Ollie": "https://cdn.discordapp.com/emojis/1545752797482459237.png",
    "Huge Festive Mandy": "https://cdn.discordapp.com/emojis/1545748676012544070.png",
    "Huge Festive Piper": "https://cdn.discordapp.com/emojis/1545751609743642725.png",
    "Huge Ollie": "https://cdn.discordapp.com/emojis/1545752797482459237.png",
}


def _roll_festive_pet_name() -> str:
    roll = random.random()
    cumulative = 0.0
    for name, rate in FESTIVE_PET_DROP_RATES.items():
        cumulative += rate
        if roll <= cumulative:
            return name
    return next(iter(FESTIVE_PET_DROP_RATES))


def _roll_festive_gift_pet_name() -> str:
    roll = random.random()
    cumulative = 0.0
    for name, rate in FESTIVE_GIFT_PET_DROP_RATES.items():
        cumulative += rate
        if roll <= cumulative:
            return name
    return next(iter(FESTIVE_GIFT_PET_DROP_RATES))


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

    @commands.command(name="autoequipevent", aliases=("autoequipfestif",))
    async def autoequip_event(self, ctx: commands.Context) -> None:
        """Équipe automatiquement tes meilleurs pets festifs (utilisable via `e!autoequip event`)."""
        await self._run_autoequip_event(ctx)

    async def _run_autoequip_event(self, ctx: commands.Context) -> None:
        user_id = ctx.author.id
        await self.database.ensure_user(user_id)
        rows = await self.database.get_user_pets(user_id)

        festive_rows = [
            row
            for row in rows
            if str(row.get("name", "")) in FESTIVE_COIN_INCOME_PER_SECOND
            and not bool(row.get("on_market"))
        ]
        if not festive_rows:
            await ctx.send(
                embed=embeds.warning_embed(
                    f"Tu n'as encore aucun pet festif. Utilise `!oeuffestif` pour en obtenir un "
                    f"({FESTIVE_EGG_PRICE} Festive Coins)."
                )
            )
            return

        max_slots = await self.database.get_pet_slot_limit(user_id)
        active_total = sum(1 for row in rows if bool(row.get("is_active")))
        free_slots = max(0, max_slots - active_total)

        def income_of(row) -> int:
            return FESTIVE_COIN_INCOME_PER_SECOND[str(row.get("name", ""))]

        inactive_festive = sorted(
            (row for row in festive_rows if not bool(row.get("is_active"))),
            key=lambda row: (-income_of(row), row.get("acquired_at")),
        )
        active_festive = sorted(
            (row for row in festive_rows if bool(row.get("is_active"))),
            key=lambda row: income_of(row),
        )

        equipped_names: list[str] = []
        swapped_names: list[tuple[str, str]] = []

        # 1. On remplit d'abord les slots libres avec les meilleurs pets festifs non équipés.
        while free_slots > 0 and inactive_festive:
            candidate = inactive_festive.pop(0)
            try:
                await self.database.activate_user_pet(user_id, int(candidate["id"]))
            except (DatabaseError, ActivePetLimitError):
                break
            equipped_names.append(str(candidate.get("name", "Pet")))
            free_slots -= 1

        # 2. Plus de slots libres : on échange un pet festif équipé plus faible contre
        #    un meilleur pet festif possédé mais inactif (uniquement si ça améliore le gain).
        while inactive_festive and active_festive:
            best_candidate = inactive_festive[0]
            worst_active = active_festive[0]
            if income_of(best_candidate) <= income_of(worst_active):
                break
            try:
                await self.database.deactivate_user_pet(user_id, int(worst_active["id"]))
                await self.database.activate_user_pet(user_id, int(best_candidate["id"]))
            except DatabaseError:
                break
            swapped_names.append(
                (str(worst_active.get("name", "Pet")), str(best_candidate.get("name", "Pet")))
            )
            inactive_festive.pop(0)
            active_festive.pop(0)

        if not equipped_names and not swapped_names:
            await ctx.send(
                embed=embeds.info_embed(
                    "Tes pets festifs équipés sont déjà les meilleurs disponibles — rien à changer.",
                    title="🎂 Auto-équipement event",
                )
            )
            return

        lines = []
        if equipped_names:
            lines.append("✅ Équipé(s) : " + ", ".join(equipped_names))
        for old, new in swapped_names:
            lines.append(f"🔄 {old} remplacé par {new}")

        await ctx.send(
            embed=embeds.success_embed(
                "\n".join(lines), title="🎂 Auto-équipement des pets festifs"
            )
        )



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


    async def _pinata_maxed(self, user_id: int) -> bool:
        """Vérifie si les 3 upgrades de la piñata (cog EventPinata) sont maxées."""
        pool = self.database.pool
        row = await pool.fetchrow(
            "SELECT cooldown_upgrades, chance_upgrades, cash_upgrades FROM pinata_event WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return False
        pinata_cog = self.bot.get_cog("EventPinata")
        if pinata_cog is not None:
            from cogs.event_pinata import (
                MAX_CASH_UPGRADES,
                MAX_CHANCE_UPGRADES,
                MAX_COOLDOWN_UPGRADES,
            )
        else:
            MAX_COOLDOWN_UPGRADES, MAX_CHANCE_UPGRADES, MAX_CASH_UPGRADES = 20, 20, 50
        return (
            int(row["cooldown_upgrades"]) >= MAX_COOLDOWN_UPGRADES
            and int(row["chance_upgrades"]) >= MAX_CHANCE_UPGRADES
            and int(row["cash_upgrades"]) >= MAX_CASH_UPGRADES
        )

    @commands.command(name="oeufcadeau", aliases=("giftegg", "oeufsecret"))
    async def oeufcadeau(self, ctx: commands.Context) -> None:
        """Œuf cadeau (1 000 000 Festive Coins) — débloqué en maxant les upgrades de la piñata."""
        user_id = ctx.author.id

        if not await self._pinata_maxed(user_id):
            await ctx.send(
                embed=embeds.error_embed(
                    "L'œuf cadeau est réservé à ceux qui ont maxé les 3 upgrades de la "
                    "piñata (`e!pinatashop`). Continue à améliorer ta piñata !"
                )
            )
            return

        pet_name = _roll_festive_gift_pet_name()
        pet_id = await self.database.get_pet_id_by_name(pet_name)
        if pet_id is None:
            await ctx.send(
                embed=embeds.error_embed(
                    "Le catalogue des pets festifs n'est pas encore synchronisé, réessaie dans un instant."
                )
            )
            return

        pool = self.database.pool
        async with pool.acquire() as connection:
            async with connection.transaction():
                balance = await self._settle_income(connection, user_id)
                if balance < FESTIVE_GIFT_EGG_PRICE:
                    await ctx.send(
                        embed=embeds.error_embed(
                            f"Il te faut **{FESTIVE_GIFT_EGG_PRICE:,}** Festive Coins pour "
                            f"acheter l'œuf cadeau (tu as {balance:,})."
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
                    FESTIVE_GIFT_EGG_PRICE,
                )

        await self.database.add_user_pet(user_id, pet_id, is_huge=True)

        message = await self._play_egg_animation(ctx, egg_emoji="🎁")

        drop_rate = FESTIVE_GIFT_PET_DROP_RATES[pet_name]
        multiplier = get_huge_multiplier(pet_name)
        embed = embeds.success_embed(
            f"Tu as obtenu **{pet_name}** ! ({drop_rate * 100:.0f}% de chance)\n\n"
            f"C'est un **vrai Huge** : il rapporte du PB via `e!claim`, avec un "
            f"multiplicateur montant jusqu'à **x{multiplier:.0f}** au niveau max "
            f"(scalé sur ton meilleur pet non-huge, comme tes autres Huges).\n\n"
            f"Il est dans ton inventaire mais pas encore équipé : utilise "
            f"`e!equip {pet_name}` pour l'activer.",
            title="🎁 Œuf cadeau ouvert !",
        )
        embed.set_image(url=_FESTIVE_PET_IMAGES[pet_name])
        await message.edit(content=None, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventAnniversaire(bot))
