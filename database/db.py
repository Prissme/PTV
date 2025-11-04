"""Couche d'accès aux données minimaliste pour EcoBot."""
from __future__ import annotations

import logging
import math
import random
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import asyncpg

from config import (
    BASE_PET_SLOTS,
    CLAN_BASE_CAPACITY,
    CLAN_CAPACITY_PER_LEVEL,
    CLAN_CAPACITY_UPGRADE_COSTS,
    CLAN_BOOST_COSTS,
    CLAN_BOOST_INCREMENT,
    CLAN_SHINY_LUCK_INCREMENT,
    GOLD_PET_MULTIPLIER,
    GOLD_PET_COMBINE_REQUIRED,
    GALAXY_PET_COMBINE_REQUIRED,
    GALAXY_PET_MULTIPLIER,
    HUGE_PET_LEVEL_CAP,
    HUGE_PET_MIN_INCOME,
    HUGE_GRIFF_NAME,
    HUGE_PET_NAMES,
    HUGE_BO_NAME,
    RAINBOW_PET_COMBINE_REQUIRED,
    RAINBOW_PET_MULTIPLIER,
    SHINY_PET_MULTIPLIER,
    TITANIC_GRIFF_NAME,
    POTION_DEFINITION_MAP,
    PotionDefinition,
    get_huge_level_multiplier,
    huge_level_required_xp,
)
from utils.mastery import get_mastery_definition
from utils.localization import DEFAULT_LANGUAGE, normalize_language

__all__ = [
    "Database",
    "DatabaseError",
    "InsufficientBalanceError",
    "ActivePetLimitError",
]

logger = logging.getLogger(__name__)

_HUGE_PET_NAME_LOOKUP = {name.lower() for name in HUGE_PET_NAMES}


class DatabaseError(RuntimeError):
    """Erreur levée lorsqu'une opération PostgreSQL échoue."""


class InsufficientBalanceError(DatabaseError):
    """Erreur dédiée lorsqu'un solde utilisateur est insuffisant."""


class ActivePetLimitError(DatabaseError):
    """Erreur levée lorsque tous les emplacements de pets actifs sont pleins."""

    def __init__(self, active: int, limit: int) -> None:
        self.active = int(active)
        self.limit = int(limit)
        super().__init__(f"Active pet slots full ({self.active}/{self.limit})")


class Database:
    """Gestionnaire de connexion PostgreSQL réduit aux besoins essentiels."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        if not dsn:
            raise ValueError("Le DSN PostgreSQL est obligatoire")

        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._min_size = min_size
        self._max_size = max_size

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise DatabaseError("La base de données n'est pas connectée")
        return self._pool

    async def connect(self) -> None:
        if self._pool is not None:
            return

        try:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                command_timeout=30,
            )
        except Exception as exc:  # pragma: no cover - log only
            logger.exception("Impossible de créer le pool PostgreSQL")
            raise DatabaseError("Connexion base de données échouée") from exc

        logger.info("Connexion PostgreSQL établie — initialisation du schéma")
        await self._initialise_schema()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Pool PostgreSQL fermé")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                yield connection

    async def _initialise_schema(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    last_daily TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    pet_last_claim TIMESTAMPTZ
                )
                """
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pet_last_claim TIMESTAMPTZ"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS help_dm_sent_at TIMESTAMPTZ"
            )
            await connection.execute(
                f"""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT NOT NULL
                DEFAULT '{DEFAULT_LANGUAGE}'
                """
            )
            await connection.execute(
                """
                ALTER TABLE users ADD COLUMN IF NOT EXISTS pet_booster_multiplier
                DOUBLE PRECISION NOT NULL DEFAULT 1
                """
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pet_booster_expires_at TIMESTAMPTZ"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pet_booster_activated_at TIMESTAMPTZ"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS active_potion_slug TEXT"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS active_potion_expires_at TIMESTAMPTZ"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_grades (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    grade_level INTEGER NOT NULL DEFAULT 0 CHECK (grade_level >= 0),
                    mastermind_progress INTEGER NOT NULL DEFAULT 0 CHECK (mastermind_progress >= 0),
                    egg_progress INTEGER NOT NULL DEFAULT 0 CHECK (egg_progress >= 0),
                    sale_progress INTEGER NOT NULL DEFAULT 0 CHECK (sale_progress >= 0),
                    potion_progress INTEGER NOT NULL DEFAULT 0 CHECK (potion_progress >= 0),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                "ALTER TABLE user_grades ADD COLUMN IF NOT EXISTS mastermind_progress INTEGER NOT NULL DEFAULT 0 CHECK (mastermind_progress >= 0)"
            )
            await connection.execute(
                "ALTER TABLE user_grades ADD COLUMN IF NOT EXISTS sale_progress INTEGER NOT NULL DEFAULT 0 CHECK (sale_progress >= 0)"
            )
            await connection.execute(
                "ALTER TABLE user_grades ADD COLUMN IF NOT EXISTS potion_progress INTEGER NOT NULL DEFAULT 0 CHECK (potion_progress >= 0)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_zones (
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    zone_slug TEXT NOT NULL,
                    unlocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, zone_slug)
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pets (
                    pet_id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    rarity TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    base_income_per_hour INTEGER NOT NULL CHECK (base_income_per_hour >= 0),
                    drop_rate DOUBLE PRECISION NOT NULL CHECK (drop_rate >= 0)
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_pets (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    pet_id INTEGER NOT NULL REFERENCES pets(pet_id) ON DELETE CASCADE,
                    nickname TEXT,
                    acquired_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    is_active BOOLEAN NOT NULL DEFAULT FALSE,
                    is_huge BOOLEAN NOT NULL DEFAULT FALSE,
                    is_gold BOOLEAN NOT NULL DEFAULT FALSE
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_pets_user ON user_pets(user_id)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_pets_active ON user_pets(user_id) WHERE is_active"
            )
            await connection.execute(
                "ALTER TABLE user_pets ADD COLUMN IF NOT EXISTS is_gold BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await connection.execute(
                "ALTER TABLE user_pets ADD COLUMN IF NOT EXISTS is_rainbow BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_pets_rainbow ON user_pets(user_id) WHERE is_rainbow"
            )
            await connection.execute(
                "ALTER TABLE user_pets ADD COLUMN IF NOT EXISTS is_galaxy BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_pets_galaxy ON user_pets(user_id) WHERE is_galaxy"
            )
            await connection.execute(
                "ALTER TABLE user_pets ADD COLUMN IF NOT EXISTS is_shiny BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_pets_shiny ON user_pets(user_id) WHERE is_shiny"
            )
            await connection.execute(
                "ALTER TABLE user_pets ADD COLUMN IF NOT EXISTS huge_level INTEGER NOT NULL DEFAULT 1"
            )
            await connection.execute(
                "ALTER TABLE user_pets ADD COLUMN IF NOT EXISTS huge_xp BIGINT NOT NULL DEFAULT 0"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pet_openings (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    pet_id INTEGER NOT NULL REFERENCES pets(pet_id) ON DELETE CASCADE,
                    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_pet_openings_user ON pet_openings(user_id)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_masteries (
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    mastery_slug TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1),
                    experience BIGINT NOT NULL DEFAULT 0 CHECK (experience >= 0),
                    PRIMARY KEY (user_id, mastery_slug)
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_masteries_slug ON user_masteries(mastery_slug)"
            )

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_pet_preferences (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    auto_goldify_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    auto_rainbowify_enabled BOOLEAN NOT NULL DEFAULT TRUE
                )
                """
            )

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    transaction_type VARCHAR(50) NOT NULL,
                    amount BIGINT NOT NULL,
                    balance_before BIGINT NOT NULL,
                    balance_after BIGINT NOT NULL,
                    description TEXT,
                    related_user_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(transaction_type)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(created_at)"
            )

            await connection.execute(
                "ALTER TABLE user_pets ADD COLUMN IF NOT EXISTS on_market BOOLEAN NOT NULL DEFAULT FALSE"
            )
            # FIX: Speed up market lookups by indexing the on_market flag.
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_pets_on_market ON user_pets(on_market)"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_listings (
                    id SERIAL PRIMARY KEY,
                    seller_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    buyer_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
                    user_pet_id INTEGER NOT NULL REFERENCES user_pets(id) ON DELETE CASCADE,
                    price BIGINT NOT NULL CHECK (price >= 0),
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_market_listings_status ON market_listings(status)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_market_listings_seller ON market_listings(seller_id)"
            )
            await connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_market_listings_pet_active
                ON market_listings(user_pet_id)
                WHERE status = 'active'
                """
            )

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pet_trade_history (
                    id SERIAL PRIMARY KEY,
                    pet_id INTEGER NOT NULL REFERENCES pets(pet_id) ON DELETE CASCADE,
                    is_gold BOOLEAN NOT NULL DEFAULT FALSE,
                    is_rainbow BOOLEAN NOT NULL DEFAULT FALSE,
                    is_galaxy BOOLEAN NOT NULL DEFAULT FALSE,
                    is_shiny BOOLEAN NOT NULL DEFAULT FALSE,
                    price BIGINT NOT NULL CHECK (price >= 0),
                    source TEXT NOT NULL CHECK (source IN ('stand', 'trade')),
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                "ALTER TABLE pet_trade_history ADD COLUMN IF NOT EXISTS is_galaxy BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_pet_trade_history_pet ON pet_trade_history(pet_id)"
            )

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS koth_states (
                    guild_id BIGINT PRIMARY KEY,
                    king_user_id BIGINT,
                    channel_id BIGINT,
                    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_roll_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plaza_consumable_listings (
                    id SERIAL PRIMARY KEY,
                    seller_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    buyer_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
                    item_type TEXT NOT NULL CHECK (item_type IN ('ticket', 'potion')),
                    item_slug TEXT,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    price BIGINT NOT NULL CHECK (price >= 0),
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plaza_consumable_status ON plaza_consumable_listings(status)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plaza_consumable_seller ON plaza_consumable_listings(seller_id)"
            )

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS clans (
                    clan_id SERIAL PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    owner_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    capacity_level INTEGER NOT NULL DEFAULT 0 CHECK (capacity_level >= 0),
                    boost_level INTEGER NOT NULL DEFAULT 0 CHECK (boost_level >= 0),
                    pb_boost_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1 CHECK (pb_boost_multiplier >= 1),
                    banner_emoji TEXT NOT NULL DEFAULT '⚔️'
                )
                """
            )
            await connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_clans_lower_name ON clans (LOWER(name))"
            )
            await connection.execute(
                """
                ALTER TABLE clans ADD COLUMN IF NOT EXISTS capacity_level INTEGER NOT NULL DEFAULT 0
                    CHECK (capacity_level >= 0)
                """
            )
            await connection.execute(
                """
                ALTER TABLE clans ADD COLUMN IF NOT EXISTS boost_level INTEGER NOT NULL DEFAULT 0
                    CHECK (boost_level >= 0)
                """
            )
            await connection.execute(
                """
                ALTER TABLE clans ADD COLUMN IF NOT EXISTS pb_boost_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1
                    CHECK (pb_boost_multiplier >= 1)
                """
            )
            await connection.execute(
                """
                ALTER TABLE clans ADD COLUMN IF NOT EXISTS shiny_luck_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1
                    CHECK (shiny_luck_multiplier >= 1)
                """
            )
            await connection.execute(
                """
                ALTER TABLE clans ADD COLUMN IF NOT EXISTS banner_emoji TEXT NOT NULL DEFAULT '⚔️'
                """
            )

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS clan_members (
                    clan_id INTEGER NOT NULL REFERENCES clans(clan_id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT 'member',
                    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    contribution BIGINT NOT NULL DEFAULT 0 CHECK (contribution >= 0),
                    last_activity TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (clan_id, user_id)
                )
                """
            )
            await connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_clan_members_user ON clan_members(user_id)"
            )
            await connection.execute(
                """
                ALTER TABLE clan_members ADD COLUMN IF NOT EXISTS contribution BIGINT NOT NULL DEFAULT 0
                    CHECK (contribution >= 0)
                """
            )
            await connection.execute(
                """
                ALTER TABLE clan_members ADD COLUMN IF NOT EXISTS last_activity TIMESTAMPTZ NOT NULL DEFAULT NOW()
                """
            )

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_activity (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    message_count BIGINT NOT NULL DEFAULT 0 CHECK (message_count >= 0),
                    last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_activity_guild_count ON user_activity(guild_id, message_count DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_activity_last_message ON user_activity(guild_id, last_message_at DESC)"
            )

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_potions (
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    potion_slug TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                    PRIMARY KEY (user_id, potion_slug)
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS raffle_tickets (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    quantity BIGINT NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS raffle_draws (
                    id SERIAL PRIMARY KEY,
                    winner_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
                    drawn_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    total_tickets BIGINT NOT NULL CHECK (total_tickets >= 0),
                    winning_ticket BIGINT NOT NULL CHECK (winning_ticket >= 1)
                )
                """
            )
            await connection.execute(
                "ALTER TABLE raffle_tickets ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            )

    # ------------------------------------------------------------------
    # Utilitaires généraux
    # ------------------------------------------------------------------
    async def ensure_user(self, user_id: int) -> None:
        await self.pool.execute(
            """
            INSERT INTO users (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )
        await self.pool.execute(
            """
            INSERT INTO user_grades (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )

    async def get_user_language(self, user_id: int) -> str:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            "SELECT language FROM users WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return DEFAULT_LANGUAGE
        return normalize_language(row.get("language"))

    async def set_user_language(self, user_id: int, language: str) -> str:
        normalized = normalize_language(language)
        await self.ensure_user(user_id)
        await self.pool.execute(
            "UPDATE users SET language = $2 WHERE user_id = $1",
            user_id,
            normalized,
        )
        return normalized

    async def add_raffle_tickets(
        self,
        user_id: int,
        *,
        amount: int = 1,
        connection: asyncpg.Connection | None = None,
    ) -> int:
        if amount <= 0:
            raise ValueError("La quantité de tickets doit être positive")

        executor: asyncpg.Connection | asyncpg.pool.Pool
        if connection is None:
            await self.ensure_user(user_id)
            executor = self.pool
        else:
            executor = connection

        row = await executor.fetchrow(
            """
            INSERT INTO raffle_tickets (user_id, quantity, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                quantity = raffle_tickets.quantity + EXCLUDED.quantity,
                updated_at = NOW()
            RETURNING quantity
            """,
            user_id,
            amount,
        )
        if row is None:
            raise DatabaseError("Impossible de mettre à jour les tickets de tombola")
        return int(row.get("quantity", 0) or 0)

    async def get_user_raffle_tickets(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            "SELECT quantity FROM raffle_tickets WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return 0
        quantity = row.get("quantity")
        if quantity is None:
            return 0
        return max(0, int(quantity))

    async def remove_raffle_tickets(
        self,
        user_id: int,
        *,
        amount: int,
        connection: asyncpg.Connection | None = None,
    ) -> int | None:
        if amount <= 0:
            raise ValueError("La quantité à retirer doit être positive")

        if connection is None:
            await self.ensure_user(user_id)
            async with self.transaction() as txn_connection:
                return await self.remove_raffle_tickets(
                    user_id,
                    amount=amount,
                    connection=txn_connection,
                )

        row = await connection.fetchrow(
            "SELECT quantity FROM raffle_tickets WHERE user_id = $1 FOR UPDATE",
            user_id,
        )
        if row is None:
            return None

        current_quantity = int(row.get("quantity") or 0)
        if current_quantity < amount:
            return None

        new_quantity = current_quantity - amount
        if new_quantity > 0:
            await connection.execute(
                "UPDATE raffle_tickets SET quantity = $2, updated_at = NOW() WHERE user_id = $1",
                user_id,
                new_quantity,
            )
        else:
            await connection.execute(
                "DELETE FROM raffle_tickets WHERE user_id = $1",
                user_id,
            )

        return new_quantity

    async def draw_raffle_winner(self) -> tuple[int, int, int] | None:
        async with self.transaction() as connection:
            rows = await connection.fetch(
                """
                SELECT user_id, quantity
                FROM raffle_tickets
                WHERE quantity > 0
                ORDER BY user_id
                FOR UPDATE
                """
            )
            if not rows:
                return None
            totals: list[tuple[int, int]] = []
            total_tickets = 0
            for row in rows:
                quantity = int(row.get("quantity", 0) or 0)
                if quantity <= 0:
                    continue
                user_id = int(row.get("user_id"))
                totals.append((user_id, quantity))
                total_tickets += quantity
            if total_tickets <= 0 or not totals:
                return None
            winning_ticket = random.randint(1, total_tickets)
            cumulative = 0
            winner_id = 0
            for user_id, quantity in totals:
                cumulative += quantity
                if winning_ticket <= cumulative:
                    winner_id = user_id
                    break
            if winner_id == 0:
                return None
            await connection.execute(
                """
                UPDATE raffle_tickets
                SET quantity = GREATEST(quantity - 1, 0), updated_at = NOW()
                WHERE user_id = $1
                """,
                winner_id,
            )
            await connection.execute(
                "DELETE FROM raffle_tickets WHERE user_id = $1 AND quantity <= 0",
                winner_id,
            )
            await connection.execute(
                """
                INSERT INTO raffle_draws (winner_id, total_tickets, winning_ticket)
                VALUES ($1, $2, $3)
                """,
                winner_id,
                total_tickets,
                winning_ticket,
            )
            result = (winner_id, total_tickets, winning_ticket)

        return result

    async def get_total_raffle_tickets(self) -> int:
        value = await self.pool.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM raffle_tickets")
        return int(value or 0)

    async def get_last_raffle_draw(self) -> datetime | None:
        row = await self.pool.fetchrow(
            "SELECT drawn_at FROM raffle_draws ORDER BY drawn_at DESC LIMIT 1"
        )
        if row is None:
            return None
        drawn_at = row.get("drawn_at")
        if isinstance(drawn_at, datetime):
            return drawn_at
        return None

    async def should_send_help_dm(self, user_id: int) -> bool:
        await self.ensure_user(user_id)
        value = await self.pool.fetchval(
            "SELECT help_dm_sent_at FROM users WHERE user_id = $1",
            user_id,
        )
        return value is None

    async def mark_help_dm_sent(self, user_id: int) -> None:
        await self.ensure_user(user_id)
        await self.pool.execute(
            "UPDATE users SET help_dm_sent_at = NOW() WHERE user_id = $1",
            user_id,
        )

    async def add_user_potion(
        self,
        user_id: int,
        potion_slug: str,
        *,
        quantity: int = 1,
        connection: asyncpg.Connection | None = None,
    ) -> None:
        if quantity <= 0:
            raise ValueError("La quantité de potions doit être positive")

        executor: asyncpg.Connection | asyncpg.pool.Pool
        if connection is None:
            await self.ensure_user(user_id)
            executor = self.pool
        else:
            executor = connection

        await executor.execute(
            """
            INSERT INTO user_potions (user_id, potion_slug, quantity)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, potion_slug)
            DO UPDATE SET quantity = user_potions.quantity + EXCLUDED.quantity
            """,
            user_id,
            potion_slug,
            quantity,
        )

    async def get_user_potions(self, user_id: int) -> Sequence[asyncpg.Record]:
        await self.ensure_user(user_id)
        return await self.pool.fetch(
            """
            SELECT potion_slug, quantity
            FROM user_potions
            WHERE user_id = $1 AND quantity > 0
            ORDER BY potion_slug
            """,
            user_id,
        )

    async def consume_user_potion(
        self,
        user_id: int,
        potion_slug: str,
        *,
        quantity: int = 1,
        connection: asyncpg.Connection | None = None,
    ) -> bool:
        if quantity <= 0:
            raise ValueError("La quantité à consommer doit être positive")

        # FIX: Skip redundant ensure_user calls when a transaction connection is provided.
        if connection is None:
            await self.ensure_user(user_id)
            async with self.transaction() as txn_connection:
                return await self.consume_user_potion(
                    user_id,
                    potion_slug,
                    quantity=quantity,
                    connection=txn_connection,
                )

        # Lorsque ``connection`` est fourni, on suppose que l'appelant a déjà
        # vérifié l'existence de l'utilisateur.

        row = await connection.fetchrow(
            "SELECT quantity FROM user_potions WHERE user_id = $1 AND potion_slug = $2 FOR UPDATE",
            user_id,
            potion_slug,
        )
        if row is None:
            return False

        current_quantity = int(row.get("quantity") or 0)
        if current_quantity < quantity:
            return False

        new_quantity = current_quantity - quantity
        if new_quantity > 0:
            await connection.execute(
                "UPDATE user_potions SET quantity = $3 WHERE user_id = $1 AND potion_slug = $2",
                user_id,
                potion_slug,
                new_quantity,
            )
        else:
            await connection.execute(
                "DELETE FROM user_potions WHERE user_id = $1 AND potion_slug = $2",
                user_id,
                potion_slug,
            )

        return True

    async def get_active_potion(
        self, user_id: int
    ) -> Optional[tuple[PotionDefinition, datetime]]:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            "SELECT active_potion_slug, active_potion_expires_at FROM users WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return None

        slug = row.get("active_potion_slug")
        if not slug:
            return None

        expires_at = row.get("active_potion_expires_at")
        if not isinstance(expires_at, datetime):
            await self.clear_active_potion(user_id)
            return None

        now = datetime.now(timezone.utc)
        if expires_at <= now:
            await self.clear_active_potion(user_id)
            return None

        definition = POTION_DEFINITION_MAP.get(str(slug))
        if definition is None:
            await self.clear_active_potion(user_id)
            return None

        return definition, expires_at

    async def set_active_potion(
        self,
        user_id: int,
        potion_slug: str,
        expires_at: datetime,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> None:
        await self.ensure_user(user_id)

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_at.astimezone(timezone.utc)

        executor = connection or self.pool
        if executor is None:
            raise DatabaseError("La base de données n'est pas connectée")

        await executor.execute(
            """
            UPDATE users
            SET active_potion_slug = $2,
                active_potion_expires_at = $3
            WHERE user_id = $1
            """,
            user_id,
            potion_slug,
            expires_at,
        )

    async def clear_active_potion(
        self, user_id: int, *, connection: asyncpg.Connection | None = None
    ) -> None:
        if connection is None:
            await self.ensure_user(user_id)

        executor = connection or self.pool
        if executor is None:
            raise DatabaseError("La base de données n'est pas connectée")

        await executor.execute(
            """
            UPDATE users
            SET active_potion_slug = NULL,
                active_potion_expires_at = NULL
            WHERE user_id = $1
            """,
            user_id,
        )

    async def grant_pet_booster(
        self,
        user_id: int,
        *,
        multiplier: float,
        duration_seconds: int,
    ) -> tuple[float, datetime, bool, float]:
        """Attribue ou prolonge un booster de pets pour un utilisateur.

        Retourne un tuple ``(multiplicateur, expiration, prolongé, ancien_multiplicateur)``.
        """

        if duration_seconds <= 0:
            raise ValueError("La durée d'un booster doit être positive")

        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=duration_seconds)

        async with self.transaction() as connection:
            row = await connection.fetchrow(
                """
                SELECT
                    pet_booster_multiplier,
                    pet_booster_expires_at,
                    pet_booster_activated_at
                FROM users
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable lors de l'attribution du booster")

            current_multiplier = float(row.get("pet_booster_multiplier") or 1.0)
            current_expires = row.get("pet_booster_expires_at")
            current_activated = row.get("pet_booster_activated_at")

            active = (
                current_multiplier > 1
                and isinstance(current_expires, datetime)
                and current_expires > now
            )
            previous_multiplier = current_multiplier if active else 1.0

            if active and multiplier <= current_multiplier:
                extended = True
                multiplier = current_multiplier
                activated_at = current_activated or now
                expires_at = current_expires + timedelta(seconds=duration_seconds)
            else:
                extended = active and multiplier <= current_multiplier
                activated_at = now

            await connection.execute(
                """
                UPDATE users
                SET
                    pet_booster_multiplier = $1,
                    pet_booster_activated_at = $2,
                    pet_booster_expires_at = $3
                WHERE user_id = $4
                """,
                multiplier,
                activated_at,
                expires_at,
                user_id,
            )

        return multiplier, expires_at, extended, previous_multiplier

    async def record_transaction(
        self,
        *,
        user_id: int,
        transaction_type: str,
        amount: int,
        balance_before: int,
        balance_after: int,
        description: str | None = None,
        related_user_id: int | None = None,
        connection: asyncpg.Connection | None = None,
    ) -> None:
        query = """
            INSERT INTO transactions (
                user_id,
                transaction_type,
                amount,
                balance_before,
                balance_after,
                description,
                related_user_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """
        params = (
            user_id,
            transaction_type,
            amount,
            balance_before,
            balance_after,
            description,
            related_user_id,
        )

        if connection is not None:
            await connection.execute(query, *params)
        else:
            await self.pool.execute(query, *params)

    # ------------------------------------------------------------------
    # Statistiques d'activité
    # ------------------------------------------------------------------
    async def record_message_activity(
        self,
        guild_id: int,
        user_id: int,
        *,
        increment: int = 1,
    ) -> None:
        if increment <= 0:
            return

        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc)
        await self.pool.execute(
            """
            INSERT INTO user_activity (guild_id, user_id, message_count, last_message_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET
                message_count = user_activity.message_count + EXCLUDED.message_count,
                last_message_at = GREATEST(user_activity.last_message_at, EXCLUDED.last_message_at)
            """,
            guild_id,
            user_id,
            increment,
            now,
        )

    async def get_guild_activity_overview(
        self,
        guild_id: int,
        *,
        active_since: datetime,
    ) -> tuple[int, int, int]:
        row = await self.pool.fetchrow(
            """
            SELECT
                COALESCE(SUM(message_count), 0) AS total_messages,
                COUNT(*) FILTER (WHERE last_message_at >= $2) AS active_members,
                COUNT(*) AS tracked_members
            FROM user_activity
            WHERE guild_id = $1
            """,
            guild_id,
            active_since,
        )
        if row is None:
            return 0, 0, 0
        return int(row["total_messages"] or 0), int(row["active_members"] or 0), int(row["tracked_members"] or 0)

    async def get_top_message_senders(
        self,
        guild_id: int,
        *,
        limit: int,
    ) -> list[tuple[int, int]]:
        resolved_limit = max(1, limit)
        rows = await self.pool.fetch(
            """
            SELECT user_id, message_count
            FROM user_activity
            WHERE guild_id = $1
            ORDER BY message_count DESC, last_message_at DESC
            LIMIT $2
            """,
            guild_id,
            resolved_limit,
        )
        return [(int(row["user_id"]), int(row["message_count"])) for row in rows]

    async def get_user_activity_details(
        self,
        guild_id: int,
        user_id: int,
    ) -> Mapping[str, object] | None:
        row = await self.pool.fetchrow(
            """
            SELECT
                ua.message_count,
                ua.last_message_at,
                1 + (
                    SELECT COUNT(*)
                    FROM user_activity other
                    WHERE other.guild_id = ua.guild_id AND other.message_count > ua.message_count
                ) AS rank,
                (
                    SELECT COUNT(*)
                    FROM user_activity other
                    WHERE other.guild_id = ua.guild_id
                ) AS total_tracked
            FROM user_activity ua
            WHERE ua.guild_id = $1 AND ua.user_id = $2
            """,
            guild_id,
            user_id,
        )
        return row

    # ------------------------------------------------------------------
    # Gestion des soldes
    # ------------------------------------------------------------------
    async def fetch_balance(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow("SELECT balance FROM users WHERE user_id = $1", user_id)
        return int(row["balance"]) if row else 0

    async def increment_balance(
        self,
        user_id: int,
        amount: int,
        *,
        transaction_type: str,
        description: str | None = None,
        related_user_id: int | None = None,
    ) -> tuple[int, int]:
        await self.ensure_user(user_id)

        if amount == 0:
            balance = await self.fetch_balance(user_id)
            return balance, balance

        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable lors de la mise à jour du solde")

            before = int(row["balance"])
            tentative_after = before + amount
            after = tentative_after if tentative_after >= 0 else 0
            applied_amount = after - before
            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                after,
                user_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=user_id,
                transaction_type=transaction_type,
                amount=applied_amount,
                balance_before=before,
                balance_after=after,
                description=description,
                related_user_id=related_user_id,
            )

        return before, after

    async def transfer_balance(
        self,
        *,
        sender_id: int,
        recipient_id: int,
        amount: int,
        send_transaction_type: str,
        receive_transaction_type: str,
        send_description: str | None = None,
        receive_description: str | None = None,
    ) -> dict[str, dict[str, int]]:
        if sender_id == recipient_id:
            raise DatabaseError("Impossible de transférer des PB vers soi-même")
        if amount <= 0:
            raise ValueError("Le montant transféré doit être strictement positif")

        await self.ensure_user(sender_id)
        await self.ensure_user(recipient_id)

        async with self.transaction() as connection:
            sender_row = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                sender_id,
            )
            recipient_row = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                recipient_id,
            )

            if sender_row is None or recipient_row is None:
                raise DatabaseError("Utilisateur introuvable lors du transfert")

            sender_before = int(sender_row["balance"])
            if sender_before < amount:
                raise InsufficientBalanceError("Solde insuffisant pour effectuer le transfert")

            recipient_before = int(recipient_row["balance"])

            sender_after = sender_before - amount
            recipient_after = recipient_before + amount

            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                sender_after,
                sender_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=sender_id,
                transaction_type=send_transaction_type,
                amount=-amount,
                balance_before=sender_before,
                balance_after=sender_after,
                description=send_description,
                related_user_id=recipient_id,
            )

            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                recipient_after,
                recipient_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=recipient_id,
                transaction_type=receive_transaction_type,
                amount=amount,
                balance_before=recipient_before,
                balance_after=recipient_after,
                description=receive_description,
                related_user_id=sender_id,
            )

        return {
            "sender": {"before": sender_before, "after": sender_after},
            "recipient": {"before": recipient_before, "after": recipient_after},
        }

    # ------------------------------------------------------------------
    # Gestion des clans
    # ------------------------------------------------------------------
    @staticmethod
    def _clan_capacity_from_level(level: int) -> int:
        return CLAN_BASE_CAPACITY + max(0, level) * CLAN_CAPACITY_PER_LEVEL

    async def get_user_clan(self, user_id: int) -> Optional[asyncpg.Record]:
        row = await self.pool.fetchrow(
            """
            SELECT c.*, cm.role, cm.joined_at, cm.contribution
            FROM clan_members AS cm
            JOIN clans AS c ON c.clan_id = cm.clan_id
            WHERE cm.user_id = $1
            """,
            user_id,
        )
        return row

    async def get_clan_by_name(self, name: str) -> Optional[asyncpg.Record]:
        return await self.pool.fetchrow(
            "SELECT * FROM clans WHERE LOWER(name) = LOWER($1)",
            name,
        )

    async def get_clan(self, clan_id: int) -> Optional[asyncpg.Record]:
        return await self.pool.fetchrow("SELECT * FROM clans WHERE clan_id = $1", clan_id)

    async def get_clan_members(self, clan_id: int) -> Sequence[asyncpg.Record]:
        return await self.pool.fetch(
            """
            SELECT clan_id, user_id, role, contribution, joined_at
            FROM clan_members
            WHERE clan_id = $1
            ORDER BY (role = 'leader') DESC, contribution DESC, joined_at
            """,
            clan_id,
        )

    async def get_clan_member_count(self, clan_id: int) -> int:
        count = await self.pool.fetchval(
            "SELECT COUNT(*) FROM clan_members WHERE clan_id = $1",
            clan_id,
        )
        return int(count or 0)

    async def get_clan_contribution_leaderboard(
        self, clan_id: int, limit: int = 3
    ) -> Sequence[asyncpg.Record]:
        return await self.pool.fetch(
            """
            SELECT user_id, contribution
            FROM clan_members
            WHERE clan_id = $1
            ORDER BY contribution DESC, joined_at
            LIMIT $2
            """,
            clan_id,
            max(1, limit),
        )

    async def create_clan(self, owner_id: int, name: str, *, banner: str = "⚔️") -> asyncpg.Record:
        await self.ensure_user(owner_id)
        async with self.transaction() as connection:
            membership = await connection.fetchrow(
                "SELECT clan_id FROM clan_members WHERE user_id = $1 FOR UPDATE",
                owner_id,
            )
            if membership is not None:
                raise DatabaseError("Tu fais déjà partie d'un clan.")

            conflict = await connection.fetchrow(
                "SELECT 1 FROM clans WHERE LOWER(name) = LOWER($1)",
                name,
            )
            if conflict is not None:
                raise DatabaseError("Ce nom de clan est déjà réservé.")

            clan_row = await connection.fetchrow(
                """
                INSERT INTO clans (name, owner_id, banner_emoji)
                VALUES ($1, $2, $3)
                RETURNING *
                """,
                name,
                owner_id,
                banner,
            )
            if clan_row is None:
                raise DatabaseError("Impossible de créer le clan. Réessaie plus tard.")

            await connection.execute(
                """
                INSERT INTO clan_members (clan_id, user_id, role)
                VALUES ($1, $2, 'leader')
                ON CONFLICT (clan_id, user_id) DO NOTHING
                """,
                clan_row["clan_id"],
                owner_id,
            )

        return clan_row

    async def add_member_to_clan(self, clan_id: int, user_id: int) -> asyncpg.Record:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            existing = await connection.fetchrow(
                "SELECT clan_id FROM clan_members WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if existing is not None:
                raise DatabaseError("Ce joueur est déjà dans un clan.")

            clan_row = await connection.fetchrow(
                "SELECT * FROM clans WHERE clan_id = $1 FOR UPDATE",
                clan_id,
            )
            if clan_row is None:
                raise DatabaseError("Ce clan n'existe plus.")

            member_count = await connection.fetchval(
                "SELECT COUNT(*) FROM clan_members WHERE clan_id = $1",
                clan_id,
            )
            capacity_level = int(clan_row.get("capacity_level", 0) or 0)
            capacity = self._clan_capacity_from_level(capacity_level)
            if int(member_count or 0) >= capacity:
                raise DatabaseError("Ce clan est déjà complet. Achète plus de slots !")

            inserted = await connection.fetchrow(
                """
                INSERT INTO clan_members (clan_id, user_id)
                VALUES ($1, $2)
                RETURNING clan_id, user_id, role, contribution, joined_at
                """,
                clan_id,
                user_id,
            )
            if inserted is None:
                raise DatabaseError("Impossible de rejoindre le clan pour le moment.")

        return inserted

    async def leave_clan(self, user_id: int) -> bool:
        async with self.transaction() as connection:
            membership = await connection.fetchrow(
                "SELECT clan_id, role FROM clan_members WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if membership is None:
                return False

            clan_id = int(membership["clan_id"])
            clan_row = await connection.fetchrow(
                "SELECT clan_id, owner_id FROM clans WHERE clan_id = $1 FOR UPDATE",
                clan_id,
            )
            if clan_row is None:
                await connection.execute(
                    "DELETE FROM clan_members WHERE user_id = $1",
                    user_id,
                )
                return True

            owner_id = int(clan_row["owner_id"])
            await connection.execute(
                "DELETE FROM clan_members WHERE clan_id = $1 AND user_id = $2",
                clan_id,
                user_id,
            )

            if owner_id == user_id:
                remaining = await connection.fetch(
                    """
                    SELECT user_id
                    FROM clan_members
                    WHERE clan_id = $1
                    ORDER BY contribution DESC, joined_at
                    """,
                    clan_id,
                )
                if not remaining:
                    await connection.execute(
                        "DELETE FROM clans WHERE clan_id = $1",
                        clan_id,
                    )
                else:
                    new_owner = int(remaining[0]["user_id"])
                    await connection.execute(
                        "UPDATE clans SET owner_id = $1 WHERE clan_id = $2",
                        new_owner,
                        clan_id,
                    )
                    await connection.execute(
                        "UPDATE clan_members SET role = 'leader' WHERE clan_id = $1 AND user_id = $2",
                        clan_id,
                        new_owner,
                    )

        return True

    async def upgrade_clan_capacity(self, clan_id: int) -> asyncpg.Record:
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT capacity_level FROM clans WHERE clan_id = $1 FOR UPDATE",
                clan_id,
            )
            if row is None:
                raise DatabaseError("Clan introuvable.")

            current_level = int(row["capacity_level"] or 0)
            if current_level >= len(CLAN_CAPACITY_UPGRADE_COSTS):
                raise DatabaseError("La capacité du clan est déjà au maximum.")

            updated = await connection.fetchrow(
                """
                UPDATE clans
                SET capacity_level = capacity_level + 1
                WHERE clan_id = $1
                RETURNING *
                """,
                clan_id,
            )
            if updated is None:
                raise DatabaseError("Impossible d'améliorer la capacité maintenant.")

        return updated

    async def upgrade_clan_boost(self, clan_id: int) -> asyncpg.Record:
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT boost_level FROM clans WHERE clan_id = $1 FOR UPDATE",
                clan_id,
            )
            if row is None:
                raise DatabaseError("Clan introuvable.")

            current_level = int(row["boost_level"] or 0)
            if current_level >= len(CLAN_BOOST_COSTS):
                raise DatabaseError("Le boost PB du clan est déjà maxé.")

            new_level = current_level + 1
            multiplier = 1 + new_level * CLAN_BOOST_INCREMENT
            shiny_multiplier = 1 + new_level * CLAN_SHINY_LUCK_INCREMENT

            updated = await connection.fetchrow(
                """
                UPDATE clans
                SET boost_level = $2, pb_boost_multiplier = $3, shiny_luck_multiplier = $4
                WHERE clan_id = $1
                RETURNING *
                """,
                clan_id,
                new_level,
                multiplier,
                shiny_multiplier,
            )
            if updated is None:
                raise DatabaseError("Impossible d'améliorer le boost maintenant.")

        return updated

    async def record_clan_contribution(self, clan_id: int, user_id: int, amount: int) -> None:
        if amount <= 0:
            return
        await self.pool.execute(
            """
            UPDATE clan_members
            SET contribution = contribution + $3, last_activity = NOW()
            WHERE clan_id = $1 AND user_id = $2
            """,
            clan_id,
            user_id,
            amount,
        )

    async def get_clan_profile(
        self, clan_id: int
    ) -> tuple[asyncpg.Record, Sequence[asyncpg.Record]]:
        clan = await self.get_clan(clan_id)
        if clan is None:
            raise DatabaseError("Clan introuvable.")
        members = await self.get_clan_members(clan_id)
        return clan, members

    async def get_last_daily(self, user_id: int) -> Optional[datetime]:
        row = await self.pool.fetchrow("SELECT last_daily FROM users WHERE user_id = $1", user_id)
        return row["last_daily"] if row else None

    async def set_last_daily(self, user_id: int, when: datetime) -> None:
        await self.pool.execute(
            "UPDATE users SET last_daily = $1 WHERE user_id = $2",
            when,
            user_id,
        )

    async def get_balance_leaderboard(self, limit: int) -> Sequence[asyncpg.Record]:
        query = "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT $1"
        return await self.pool.fetch(query, limit)

    async def get_mastery_leaderboard(
        self, mastery_slug: str, limit: int
    ) -> Sequence[asyncpg.Record]:
        query = """
            SELECT user_id, level, experience
            FROM user_masteries
            WHERE mastery_slug = $1
            ORDER BY level DESC, experience DESC, user_id ASC
            LIMIT $2
        """
        return await self.pool.fetch(query, mastery_slug, limit)

    # ------------------------------------------------------------------
    # Gestion des grades
    # ------------------------------------------------------------------
    async def get_user_grade(self, user_id: int) -> asyncpg.Record:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            """
            SELECT
                grade_level,
                mastermind_progress,
                egg_progress,
                sale_progress,
                potion_progress,
                updated_at
            FROM user_grades
            WHERE user_id = $1
            """,
            user_id,
        )
        if row is None:
            raise DatabaseError("Utilisateur introuvable dans user_grades")
        return row

    async def increment_grade_progress(
        self,
        user_id: int,
        *,
        mastermind_delta: int = 0,
        egg_delta: int = 0,
        sale_delta: int = 0,
        potion_delta: int = 0,
        mastermind_cap: int | None = None,
        egg_cap: int | None = None,
        sale_cap: int | None = None,
        potion_cap: int | None = None,
    ) -> asyncpg.Record:
        if (
            mastermind_delta <= 0
            and egg_delta <= 0
            and sale_delta <= 0
            and potion_delta <= 0
        ):
            return await self.get_user_grade(user_id)

        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                """
                SELECT grade_level, mastermind_progress, egg_progress, sale_progress, potion_progress
                FROM user_grades
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable lors de la mise à jour des grades")

            current_mastermind = int(row["mastermind_progress"])
            current_eggs = int(row["egg_progress"])
            current_sales = int(row["sale_progress"])
            current_potions = int(row["potion_progress"])

            new_mastermind = max(0, current_mastermind + max(mastermind_delta, 0))
            new_eggs = max(0, current_eggs + max(egg_delta, 0))
            new_sales = max(0, current_sales + max(sale_delta, 0))
            new_potions = max(0, current_potions + max(potion_delta, 0))

            if mastermind_cap is not None:
                new_mastermind = min(new_mastermind, mastermind_cap)
            if egg_cap is not None:
                new_eggs = min(new_eggs, egg_cap)
            if sale_cap is not None:
                new_sales = min(new_sales, sale_cap)
            if potion_cap is not None:
                new_potions = min(new_potions, potion_cap)

            updated = await connection.fetchrow(
                """
                UPDATE user_grades
                SET mastermind_progress = $1,
                    egg_progress = $2,
                    sale_progress = $3,
                    potion_progress = $4,
                    updated_at = NOW()
                WHERE user_id = $5
                RETURNING *
                """,
                new_mastermind,
                new_eggs,
                new_sales,
                new_potions,
                user_id,
            )

        if updated is None:
            raise DatabaseError("Impossible de mettre à jour les quêtes de grade")
        return updated

    async def complete_grade_if_ready(
        self,
        user_id: int,
        *,
        mastermind_goal: int,
        egg_goal: int,
        sale_goal: int,
        potion_goal: int,
        max_grade: int,
    ) -> tuple[bool, asyncpg.Record]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM user_grades WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable lors de la validation de grade")

            grade_level = int(row["grade_level"])
            if grade_level >= max_grade:
                return False, row

            if (
                int(row["mastermind_progress"]) < mastermind_goal
                or int(row["egg_progress"]) < egg_goal
                or int(row["sale_progress"]) < sale_goal
                or int(row["potion_progress"]) < potion_goal
            ):
                return False, row

            new_row = await connection.fetchrow(
                """
                UPDATE user_grades
                SET grade_level = $1,
                    mastermind_progress = 0,
                    egg_progress = 0,
                    sale_progress = 0,
                    potion_progress = 0,
                    updated_at = NOW()
                WHERE user_id = $2
                RETURNING *
                """,
                grade_level + 1,
                user_id,
            )

        if new_row is None:
            raise DatabaseError("Impossible de finaliser le passage de grade")
        return True, new_row

    async def get_grade_leaderboard(self, limit: int) -> Sequence[asyncpg.Record]:
        query = """
            SELECT user_id, grade_level
            FROM user_grades
            ORDER BY grade_level DESC, updated_at ASC
            LIMIT $1
        """
        return await self.pool.fetch(query, limit)

    async def get_pet_rap_leaderboard(self, limit: int) -> list[tuple[int, int]]:
        clamped_limit = max(0, int(limit))
        if clamped_limit == 0:
            return []

        market_values = await self.get_pet_market_values()
        rows = await self.pool.fetch(
            """
            SELECT
                up.user_id,
                up.pet_id,
                up.is_gold,
                up.is_huge,
                up.is_rainbow,

                up.is_galaxy,
                up.is_shiny,
                up.huge_level,
                p.base_income_per_hour,
                p.name
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            """
        )

        rap_totals: defaultdict[int, int] = defaultdict(int)
        for row in rows:
            user_id = int(row["user_id"])
            pet_id = int(row["pet_id"])
            base_income = int(row["base_income_per_hour"])
            name = str(row.get("name", ""))
            value = int(market_values.get(pet_id, 0))
            if value <= 0:
                value = max(base_income * 120, 1_000)
            is_galaxy = bool(row.get("is_galaxy"))
            is_rainbow = bool(row.get("is_rainbow"))
            if is_galaxy:
                value = int(value * GALAXY_PET_MULTIPLIER)
            elif is_rainbow:
                value = int(value * RAINBOW_PET_MULTIPLIER)
            elif bool(row["is_gold"]):
                value = int(value * GOLD_PET_MULTIPLIER)
            if bool(row.get("is_shiny")):
                value = int(value * SHINY_PET_MULTIPLIER)
            if bool(row["is_huge"]):
                level = int(row.get("huge_level") or 1)
                multiplier = get_huge_level_multiplier(name, level)
                huge_floor = max(base_income * multiplier * 150, value)
                value = int(huge_floor)
            rap_totals[user_id] += max(0, value)

        sorted_totals = sorted(rap_totals.items(), key=lambda item: item[1], reverse=True)
        return sorted_totals[:clamped_limit]

    async def get_hourly_income_leaderboard(self, limit: int) -> list[tuple[int, int]]:
        clamped_limit = max(0, int(limit))
        if clamped_limit == 0:
            return []

        rows = await self.pool.fetch(
            """
            SELECT
                up.user_id,
                up.is_active,
                up.is_huge,
                up.is_gold,
                up.is_rainbow,

                up.is_galaxy,
                up.is_shiny,
                up.huge_level,
                p.name,
                p.base_income_per_hour
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            """
        )

        best_non_huge: defaultdict[int, int] = defaultdict(int)
        for row in rows:
            if bool(row.get("is_huge")):
                continue
            base_income = int(row["base_income_per_hour"])
            is_galaxy = bool(row.get("is_galaxy"))
            is_rainbow = bool(row.get("is_rainbow"))
            if is_galaxy:
                base_income *= GALAXY_PET_MULTIPLIER
            elif is_rainbow:
                base_income *= RAINBOW_PET_MULTIPLIER
            elif bool(row.get("is_gold")):
                base_income *= GOLD_PET_MULTIPLIER
            if bool(row.get("is_shiny")):
                base_income *= SHINY_PET_MULTIPLIER
            user_id = int(row["user_id"])
            if base_income > best_non_huge[user_id]:
                best_non_huge[user_id] = base_income

        income_totals: defaultdict[int, int] = defaultdict(int)
        for row in rows:
            if not bool(row.get("is_active")):
                continue
            user_id = int(row["user_id"])
            base_income = int(row["base_income_per_hour"])
            if bool(row.get("is_huge")):
                level = int(row.get("huge_level") or 1)
                multiplier = get_huge_level_multiplier(str(row.get("name", "")), level)
                reference = best_non_huge.get(user_id, 0)
                if reference <= 0:
                    base_income = int(row["base_income_per_hour"])
                    scaled = int(base_income * multiplier)
                else:
                    scaled = int(reference * multiplier)
                income_value = max(HUGE_PET_MIN_INCOME, scaled)
            else:
                if bool(row.get("is_galaxy")):
                    income_value = base_income * GALAXY_PET_MULTIPLIER
                elif bool(row.get("is_rainbow")):
                    income_value = base_income * RAINBOW_PET_MULTIPLIER
                elif bool(row.get("is_gold")):
                    income_value = base_income * GOLD_PET_MULTIPLIER
                else:
                    income_value = base_income
                if bool(row.get("is_shiny")):
                    income_value *= SHINY_PET_MULTIPLIER
            income_totals[user_id] += income_value

        sorted_totals = sorted(
            ((user_id, income) for user_id, income in income_totals.items() if income > 0),
            key=lambda item: item[1],
            reverse=True,
        )
        return sorted_totals[:clamped_limit]

    async def reset_user_grade(self, user_id: int) -> None:
        await self.ensure_user(user_id)
        await self.pool.execute(
            """
            UPDATE user_grades
            SET grade_level = 0,
                mastermind_progress = 0,
                egg_progress = 0,
                sale_progress = 0,
                potion_progress = 0,
                updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id,
        )

    async def get_grade_level(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        value = await self.pool.fetchval(
            "SELECT grade_level FROM user_grades WHERE user_id = $1",
            user_id,
        )
        return int(value or 0)

    # ------------------------------------------------------------------
    # Gestion des zones
    # ------------------------------------------------------------------
    async def has_unlocked_zone(self, user_id: int, zone_slug: str) -> bool:
        await self.ensure_user(user_id)
        value = await self.pool.fetchval(
            "SELECT 1 FROM user_zones WHERE user_id = $1 AND zone_slug = $2",
            user_id,
            zone_slug,
        )
        return value is not None

    async def unlock_zone(self, user_id: int, zone_slug: str) -> None:
        await self.ensure_user(user_id)
        await self.pool.execute(
            """
            INSERT INTO user_zones (user_id, zone_slug)
            VALUES ($1, $2)
            ON CONFLICT (user_id, zone_slug) DO NOTHING
            """,
            user_id,
            zone_slug,
        )

    async def get_unlocked_zones(self, user_id: int) -> Set[str]:
        await self.ensure_user(user_id)
        rows = await self.pool.fetch(
            "SELECT zone_slug FROM user_zones WHERE user_id = $1",
            user_id,
        )
        return {str(row["zone_slug"]) for row in rows}

    # ------------------------------------------------------------------
    # Utilitaires génériques
    # ------------------------------------------------------------------
    async def fetch_value(self, query: str, *parameters: Any) -> Any:
        return await self.pool.fetchval(query, *parameters)

    # ------------------------------------------------------------------
    # Gestion des pets
    # ------------------------------------------------------------------
    async def sync_pets(self, pets: Iterable[Any]) -> dict[str, int]:
        """Synchronise les définitions de pets dans la base.

        Retourne un mapping {nom: pet_id} afin de faciliter les références.
        """

        pet_ids: dict[str, int] = {}
        async with self.transaction() as connection:
            for pet in pets:
                if hasattr(pet, "name"):
                    name = getattr(pet, "name")  # type: ignore[attr-defined]
                    rarity = getattr(pet, "rarity")  # type: ignore[attr-defined]
                    image_url = getattr(pet, "image_url")  # type: ignore[attr-defined]
                    base_income = getattr(pet, "base_income_per_hour")  # type: ignore[attr-defined]
                    drop_rate = getattr(pet, "drop_rate")  # type: ignore[attr-defined]
                else:
                    name = pet["name"]  # type: ignore[index]
                    rarity = pet["rarity"]  # type: ignore[index]
                    image_url = pet["image_url"]  # type: ignore[index]
                    base_income = pet["base_income_per_hour"]  # type: ignore[index]
                    drop_rate = pet["drop_rate"]  # type: ignore[index]
                row = await connection.fetchrow(
                    """
                    INSERT INTO pets (name, rarity, image_url, base_income_per_hour, drop_rate)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (name) DO UPDATE
                    SET rarity = EXCLUDED.rarity,
                        image_url = EXCLUDED.image_url,
                        base_income_per_hour = EXCLUDED.base_income_per_hour,
                        drop_rate = EXCLUDED.drop_rate
                    RETURNING pet_id
                    """,
                    name,
                    rarity,
                    image_url,
                    base_income,
                    drop_rate,
                )
                if row is None:
                    raise DatabaseError(f"Échec de l'insertion du pet {name}")
                pet_ids[str(name)] = int(row["pet_id"])
        return pet_ids

    async def get_pet_auto_settings(self, user_id: int) -> Dict[str, bool]:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            """
            SELECT auto_goldify_enabled, auto_rainbowify_enabled
            FROM user_pet_preferences
            WHERE user_id = $1
            """,
            user_id,
        )
        if row is None:
            row = await self.pool.fetchrow(
                """
                INSERT INTO user_pet_preferences (user_id)
                VALUES ($1)
                RETURNING auto_goldify_enabled, auto_rainbowify_enabled
                """,
                user_id,
            )
        if row is None:
            raise DatabaseError("Impossible de récupérer les préférences de pets")
        return {
            "auto_goldify": bool(row["auto_goldify_enabled"]),
            "auto_rainbowify": bool(row["auto_rainbowify_enabled"]),
        }

    async def set_pet_auto_settings(
        self,
        user_id: int,
        *,
        auto_goldify: bool | None = None,
        auto_rainbowify: bool | None = None,
    ) -> Dict[str, bool]:
        if auto_goldify is None and auto_rainbowify is None:
            return await self.get_pet_auto_settings(user_id)
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            """
            INSERT INTO user_pet_preferences (
                user_id,
                auto_goldify_enabled,
                auto_rainbowify_enabled
            )
            VALUES ($1, COALESCE($2, TRUE), COALESCE($3, TRUE))
            ON CONFLICT (user_id) DO UPDATE
            SET auto_goldify_enabled = COALESCE($2, user_pet_preferences.auto_goldify_enabled),
                auto_rainbowify_enabled = COALESCE($3, user_pet_preferences.auto_rainbowify_enabled)
            RETURNING auto_goldify_enabled, auto_rainbowify_enabled
            """,
            user_id,
            auto_goldify,
            auto_rainbowify,
        )
        if row is None:
            raise DatabaseError("Impossible de mettre à jour les préférences de pets")
        return {
            "auto_goldify": bool(row["auto_goldify_enabled"]),
            "auto_rainbowify": bool(row["auto_rainbowify_enabled"]),
        }

    async def get_all_pets(self) -> Sequence[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT pet_id, name, rarity, image_url, base_income_per_hour, drop_rate FROM pets ORDER BY pet_id"
        )

    async def get_pet_id_by_name(self, name: str) -> Optional[int]:
        row = await self.pool.fetchrow(
            "SELECT pet_id FROM pets WHERE LOWER(name) = LOWER($1)",
            name,
        )
        if row is None:
            return None
        return int(row["pet_id"])

    async def add_user_pet(
        self,
        user_id: int,
        pet_id: int,
        *,
        is_huge: bool = False,
        is_gold: bool = False,
        is_rainbow: bool = False,
        is_galaxy: bool = False,
        is_shiny: bool = False,
    ) -> asyncpg.Record:
        await self.ensure_user(user_id)
        if is_galaxy:
            is_gold = False
            is_rainbow = False
        elif is_rainbow:
            is_gold = False
        row = await self.pool.fetchrow(
            """
            INSERT INTO user_pets (user_id, pet_id, is_huge, is_gold, is_rainbow, is_galaxy, is_shiny)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, user_id, pet_id, is_active, is_huge, is_gold, is_rainbow, is_galaxy, is_shiny, acquired_at
            """,
            user_id,
            pet_id,
            is_huge,
            is_gold,
            is_rainbow,
            is_galaxy,
            is_shiny,
        )
        if row is None:
            raise DatabaseError("Impossible de créer l'entrée user_pet")
        return row

    async def upgrade_pet_to_gold(
        self, user_id: int, pet_id: int, *, make_shiny: bool = False
    ) -> tuple[asyncpg.Record, int]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            # FIX: Prevent rainbow pets from being consumed and guarantee deterministic ordering.
            rows = await connection.fetch(
                """
                SELECT up.id
                FROM user_pets AS up
                WHERE up.user_id = $1
                  AND up.pet_id = $2
                  AND NOT up.is_gold
                  AND NOT up.is_rainbow
                  AND NOT up.is_galaxy
                  AND NOT up.is_active
                  AND NOT up.on_market
                ORDER BY up.acquired_at, up.id
                FOR UPDATE
                """,
                user_id,
                pet_id,
            )

            available_ids: List[int] = [int(row["id"]) for row in rows]
            required = GOLD_PET_COMBINE_REQUIRED
            if len(available_ids) < required:
                raise DatabaseError(
                    (
                        "Tu as besoin d'au moins {required} exemplaires non équipés et non listés sur ton stand "
                        "pour créer une version or."
                    ).format(required=required)
                )

            consumed_ids = available_ids[:required]
            await connection.execute(
                "DELETE FROM user_pets WHERE id = ANY($1::INT[])",
                consumed_ids,
            )

            inserted = await connection.fetchrow(
                """
                INSERT INTO user_pets (user_id, pet_id, is_gold, is_shiny)
                VALUES ($1, $2, TRUE, $3)
                RETURNING id
                """,
                user_id,
                pet_id,
                make_shiny,
            )
            if inserted is None:
                raise DatabaseError("Impossible de créer le pet doré")

            new_record = await connection.fetchrow(
                """
                SELECT
                    up.id,
                    up.nickname,
                    up.is_active,
                    up.is_huge,
                    up.is_gold,
                    up.is_rainbow,
                    up.is_galaxy,
                    up.is_shiny,
                    up.huge_level,
                    up.huge_xp,
                    up.acquired_at,
                    p.pet_id,
                    p.name,
                    p.rarity,
                    p.image_url,
                    p.base_income_per_hour
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                WHERE up.id = $1
                """,
                int(inserted["id"]),
            )

        if new_record is None:
            raise DatabaseError("Impossible de récupérer le pet doré généré")
        return new_record, required

    async def upgrade_pet_to_rainbow(
        self, user_id: int, pet_id: int, *, make_shiny: bool = False
    ) -> tuple[asyncpg.Record, int]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            # FIX: Ensure deterministic rainbow fusion selection order.
            rows = await connection.fetch(
                """
                SELECT up.id
                FROM user_pets AS up
                WHERE up.user_id = $1
                  AND up.pet_id = $2
                  AND up.is_gold
                  AND NOT up.is_rainbow
                  AND NOT up.is_galaxy
                  AND NOT up.is_active
                  AND NOT up.on_market
                ORDER BY up.acquired_at, up.id
                FOR UPDATE
                """,
                user_id,
                pet_id,
            )

            available_ids = [int(row["id"]) for row in rows]
            required = RAINBOW_PET_COMBINE_REQUIRED

            if len(available_ids) < required:
                raise DatabaseError(
                    f"Tu as besoin de {required} exemplaires GOLD pour créer un Rainbow. Tu en as seulement {len(available_ids)}."
                )

            consumed_ids = available_ids[:required]
            await connection.execute(
                "DELETE FROM user_pets WHERE id = ANY($1::INT[])",
                consumed_ids,
            )

            inserted = await connection.fetchrow(
                """
                INSERT INTO user_pets (user_id, pet_id, is_rainbow, is_shiny)
                VALUES ($1, $2, TRUE, $3)
                RETURNING id
                """,
                user_id,
                pet_id,
                make_shiny,
            )

            if inserted is None:
                raise DatabaseError("Impossible de créer le pet rainbow")

            new_record = await connection.fetchrow(
                """
                SELECT
                    up.id,
                    up.nickname,
                    up.is_active,
                    up.is_huge,
                    up.is_gold,
                    up.is_rainbow,
                    up.is_galaxy,
                    up.is_shiny,
                    up.huge_level,
                    up.huge_xp,
                    up.acquired_at,
                    p.pet_id,
                    p.name,
                    p.rarity,
                    p.image_url,
                    p.base_income_per_hour
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                WHERE up.id = $1
                """,
                int(inserted["id"]),
            )

        if new_record is None:
            raise DatabaseError("Impossible de récupérer le pet rainbow")

        return new_record, required

    async def upgrade_pet_to_galaxy(
        self, user_id: int, pet_id: int, *, make_shiny: bool = False
    ) -> tuple[asyncpg.Record, int]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            rows = await connection.fetch(
                """
                SELECT up.id
                FROM user_pets AS up
                WHERE up.user_id = $1
                  AND up.pet_id = $2
                  AND up.is_rainbow
                  AND NOT up.is_galaxy
                  AND NOT up.is_active
                  AND NOT up.on_market
                ORDER BY up.acquired_at, up.id
                FOR UPDATE
                """,
                user_id,
                pet_id,
            )

            available_ids = [int(row["id"]) for row in rows]
            required = GALAXY_PET_COMBINE_REQUIRED

            if len(available_ids) < required:
                raise DatabaseError(
                    f"Il te faut {required} exemplaires RAINBOW pour créer un Galaxy. Tu n'en as que {len(available_ids)}."
                )

            consumed_ids = available_ids[:required]
            await connection.execute(
                "DELETE FROM user_pets WHERE id = ANY($1::INT[])",
                consumed_ids,
            )

            inserted = await connection.fetchrow(
                """
                INSERT INTO user_pets (user_id, pet_id, is_galaxy, is_shiny)
                VALUES ($1, $2, TRUE, $3)
                RETURNING id
                """,
                user_id,
                pet_id,
                make_shiny,
            )

            if inserted is None:
                raise DatabaseError("Impossible de créer le pet galaxy")

            new_record = await connection.fetchrow(
                """
                SELECT
                    up.id,
                    up.nickname,
                    up.is_active,
                    up.is_huge,
                    up.is_gold,
                    up.is_rainbow,
                    up.is_galaxy,
                    up.is_shiny,
                    up.huge_level,
                    up.huge_xp,
                    up.acquired_at,
                    p.pet_id,
                    p.name,
                    p.rarity,
                    p.image_url,
                    p.base_income_per_hour
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                WHERE up.id = $1
                """,
                int(inserted["id"]),
            )

        if new_record is None:
            raise DatabaseError("Impossible de récupérer le pet galaxy")

        return new_record, required

    async def fuse_user_pets(
        self,
        user_id: int,
        user_pet_ids: Sequence[int],
        result_pet_id: int,
        *,
        make_shiny: bool = False,
        allow_huge: bool = False,
        result_is_huge: bool | None = None,
    ) -> asyncpg.Record:
        await self.ensure_user(user_id)
        unique_ids = {int(pet_id) for pet_id in user_pet_ids if int(pet_id) > 0}
        if len(unique_ids) < 10:
            raise DatabaseError("La machine de fusion nécessite 10 pets distincts.")

        async with self.transaction() as connection:
            pet_row = await connection.fetchrow(
                "SELECT name FROM pets WHERE pet_id = $1",
                result_pet_id,
            )
            if pet_row is None:
                raise DatabaseError("Pet de résultat introuvable.")
            pet_name = str(pet_row.get("name") or "")
            resolved_is_huge = bool(result_is_huge)
            if result_is_huge is None:
                resolved_is_huge = pet_name.lower() in _HUGE_PET_NAME_LOOKUP
            if resolved_is_huge and not allow_huge:
                raise DatabaseError(
                    "La fusion ne permet pas de créer ce pet titanesque pour le moment."
                )

            rows = await connection.fetch(
                """
                SELECT id, is_active, is_huge, on_market
                FROM user_pets
                WHERE user_id = $1 AND id = ANY($2::INT[])
                FOR UPDATE
                """,
                user_id,
                list(unique_ids),
            )
            if len(rows) < len(unique_ids):
                raise DatabaseError("Tu dois sélectionner des pets qui t'appartiennent et sont disponibles.")

            for row in rows:
                if bool(row.get("is_huge")) and not allow_huge:
                    raise DatabaseError("Les Huge pets ne peuvent pas être fusionnés ici.")
                if bool(row.get("is_active")) or bool(row.get("on_market")):
                    raise DatabaseError("Les pets actifs ou en vente ne peuvent pas être fusionnés.")

            await connection.execute(
                "DELETE FROM user_pets WHERE id = ANY($1::INT[])",
                list(unique_ids),
            )

            inserted = await connection.fetchrow(
                """
                INSERT INTO user_pets (user_id, pet_id, is_huge, is_shiny)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                user_id,
                result_pet_id,
                resolved_is_huge,
                make_shiny,
            )
            if inserted is None:
                raise DatabaseError("Impossible de créer le pet fusionné.")

            new_record = await connection.fetchrow(
                """
                SELECT
                    up.id,
                    up.nickname,
                    up.is_active,
                    up.is_huge,
                    up.is_gold,
                    up.is_rainbow,

                    up.is_galaxy,
                    up.is_shiny,
                    up.huge_level,
                    up.huge_xp,
                    up.acquired_at,
                    p.pet_id,
                    p.name,
                    p.rarity,
                    p.image_url,
                    p.base_income_per_hour
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                WHERE up.id = $1
                """,
                int(inserted["id"]),
            )

        if new_record is None:
            raise DatabaseError("Impossible de récupérer le pet fusionné.")
        return new_record

    async def get_user_pets(self, user_id: int) -> Sequence[asyncpg.Record]:
        return await self.pool.fetch(
            """
            SELECT
                up.id,
                up.nickname,
                up.is_active,
                up.is_huge,
                up.is_gold,
                up.is_rainbow,

                up.is_galaxy,
                up.is_shiny,
                up.on_market,
                up.huge_level,
                up.huge_xp,
                up.acquired_at,
                p.pet_id,
                p.name,
                p.rarity,
                p.image_url,
                p.base_income_per_hour
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE up.user_id = $1
            ORDER BY p.base_income_per_hour DESC, up.acquired_at ASC
            """,
            user_id,
        )

    async def get_user_pet(self, user_id: int, user_pet_id: int) -> Optional[asyncpg.Record]:
        return await self.pool.fetchrow(
            """
            SELECT
                up.id,
                up.nickname,
                up.is_active,
                up.is_huge,
                up.is_gold,
                up.is_rainbow,

                up.is_galaxy,
                up.is_shiny,
                up.on_market,
                up.huge_level,
                up.huge_xp,
                up.acquired_at,
                p.pet_id,
                p.name,
                p.rarity,
                p.image_url,
                p.base_income_per_hour
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE up.user_id = $1 AND up.id = $2
            """,
            user_id,
            user_pet_id,
        )

    async def admin_transfer_user_pet(
        self,
        source_user_id: int,
        target_user_id: int,
        user_pet_id: int,
    ) -> asyncpg.Record:
        if source_user_id == target_user_id:
            raise DatabaseError("L'utilisateur source et la cible doivent être différents.")

        await self.ensure_user(source_user_id)
        await self.ensure_user(target_user_id)

        async with self.transaction() as connection:
            pet_row = await connection.fetchrow(
                """
                SELECT
                    up.id,
                    up.user_id,
                    up.is_active,
                    up.is_huge,
                    up.is_gold,
                    up.is_rainbow,

                    up.is_galaxy,
                    up.is_shiny,
                    up.on_market,
                    up.huge_level,
                    up.huge_xp,
                    up.acquired_at,
                    p.pet_id,
                    p.name,
                    p.rarity,
                    p.base_income_per_hour
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                WHERE up.id = $1
                FOR UPDATE
                """,
                user_pet_id,
            )
            if pet_row is None:
                raise DatabaseError("Ce pet est introuvable.")

            if int(pet_row["user_id"]) != int(source_user_id):
                raise DatabaseError("Ce pet n'appartient pas à l'utilisateur indiqué.")

            if bool(pet_row.get("on_market")):
                raise DatabaseError("Ce pet est actuellement en vente sur un stand.")

            conflict = await connection.fetchval(
                """
                SELECT 1
                FROM market_listings
                WHERE user_pet_id = $1 AND status = 'active'
                LIMIT 1
                """,
                user_pet_id,
            )
            if conflict:
                raise DatabaseError("Ce pet est listé sur un stand actif.")

            await connection.execute(
                "UPDATE user_pets SET user_id = $1, is_active = FALSE, on_market = FALSE WHERE id = $2",
                int(target_user_id),
                user_pet_id,
            )

            updated = await connection.fetchrow(
                """
                SELECT
                    up.id,
                    up.user_id,
                    up.is_active,
                    up.is_huge,
                    up.is_gold,
                    up.is_rainbow,

                    up.is_galaxy,
                    up.is_shiny,
                    up.huge_level,
                    up.huge_xp,
                    up.acquired_at,
                    p.pet_id,
                    p.name,
                    p.rarity,
                    p.base_income_per_hour
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                WHERE up.id = $1
                """,
                user_pet_id,
            )

        if updated is None:
            raise DatabaseError("Impossible de récupérer le pet transféré.")

        return updated

    async def transfer_user_pet(
        self,
        source_user_id: int,
        target_user_id: int,
        user_pet_id: int,
    ) -> asyncpg.Record:
        """Transfère un pet entre deux joueurs en vérifiant les contraintes de base."""

        return await self.admin_transfer_user_pet(source_user_id, target_user_id, user_pet_id)

    async def get_user_pet_by_name(
        self,
        user_id: int,
        pet_name: str,
        *,
        is_gold: bool | None = None,
        is_rainbow: bool | None = None,
        is_shiny: bool | None = None,
        include_active: bool = True,
        include_inactive: bool = True,
        include_on_market: bool = True,
    ) -> Sequence[asyncpg.Record]:
        if not include_active and not include_inactive:
            return []

        where_clauses = ["up.user_id = $1", "LOWER(p.name) = LOWER($2)"]
        params: list[object] = [user_id, pet_name]
        index = 3

        if is_gold is not None:
            where_clauses.append(f"up.is_gold = ${index}")
            params.append(is_gold)
            index += 1
        if is_rainbow is not None:
            where_clauses.append(f"up.is_rainbow = ${index}")
            params.append(is_rainbow)
            index += 1
        if is_shiny is not None:
            where_clauses.append(f"up.is_shiny = ${index}")
            params.append(is_shiny)
            index += 1
        if not include_active:
            where_clauses.append("NOT up.is_active")
        if not include_inactive:
            where_clauses.append("up.is_active")
        if not include_on_market:
            where_clauses.append("NOT up.on_market")

        where_sql = " AND ".join(where_clauses)
        # FIX: Ensure deterministic ordering when listing user pets.
        query = f"""
            SELECT
                up.id,
                up.nickname,
                up.is_active,
                up.is_huge,
                up.is_gold,
                up.is_rainbow,

                up.is_galaxy,
                up.is_shiny,
                up.on_market,
                up.huge_level,
                up.huge_xp,
                up.acquired_at,
                p.pet_id,
                p.name,
                p.rarity,
                p.image_url,
                p.base_income_per_hour
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE {where_sql}
            ORDER BY up.acquired_at, up.id
        """

        return await self.pool.fetch(query, *params)

    async def get_active_user_pet(self, user_id: int) -> Optional[asyncpg.Record]:
        return await self.pool.fetchrow(
            """
            SELECT
                up.id,
                up.nickname,
                up.is_active,
                up.is_huge,
                up.is_gold,
                up.is_rainbow,

                up.is_galaxy,
                up.is_shiny,
                up.huge_level,
                up.huge_xp,
                up.acquired_at,
                p.pet_id,
                p.name,
                p.rarity,
                p.image_url,
                p.base_income_per_hour
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE up.user_id = $1 AND up.is_active
            LIMIT 1
            """,
            user_id,
        )

    async def get_best_non_huge_income(
        self, user_id: int, *, connection: asyncpg.Connection | None = None
    ) -> int:
        query = (
            """
            SELECT MAX(
                p.base_income_per_hour
                * CASE
                    WHEN up.is_rainbow THEN $3
                    WHEN up.is_gold THEN $2
                    ELSE 1
                END
            ) AS best_income
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE up.user_id = $1 AND NOT up.is_huge
            """
        )
        executor = connection or self.pool
        if executor is None:
            raise DatabaseError("La connexion à la base de données n'est pas initialisée")
        row = await executor.fetchrow(
            query,
            user_id,
            GOLD_PET_MULTIPLIER,
            RAINBOW_PET_MULTIPLIER,
        )
        if row is None:
            return 0
        best_value = row.get("best_income")
        if best_value is None:
            return 0
        return int(best_value)

    async def activate_user_pet(
        self, user_id: int, user_pet_id: int
    ) -> tuple[Optional[asyncpg.Record], int, int]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            pet_row = await connection.fetchrow(
                "SELECT id, is_active, on_market FROM user_pets WHERE user_id = $1 AND id = $2 FOR UPDATE",
                user_id,
                user_pet_id,
            )
            if pet_row is None:
                return None, 0, BASE_PET_SLOTS

            if bool(pet_row["is_active"]):
                raise DatabaseError("Ce pet est déjà équipé.")

            if bool(pet_row.get("on_market")):
                raise DatabaseError(
                    "Ce pet est actuellement en vente sur ton stand. Retire-le avant de l'équiper."
                )

            active_count = int(
                await connection.fetchval(
                    "SELECT COUNT(*) FROM user_pets WHERE user_id = $1 AND is_active",
                    user_id,
                )
                or 0
            )

            grade_row = await connection.fetchrow(
                "SELECT grade_level FROM user_grades WHERE user_id = $1",
                user_id,
            )
            grade_level = int(grade_row["grade_level"]) if grade_row else 0
            max_slots = BASE_PET_SLOTS + grade_level

            if active_count >= max_slots:
                raise ActivePetLimitError(active_count, max_slots)

            await connection.execute(
                "UPDATE user_pets SET is_active = TRUE, on_market = FALSE WHERE id = $1",
                user_pet_id,
            )
            new_active_count = active_count + 1

            await connection.execute(
                "UPDATE users SET pet_last_claim = NOW() WHERE user_id = $1",
                user_id,
            )

        record = await self.get_user_pet(user_id, user_pet_id)
        return record, new_active_count, max_slots

    async def deactivate_user_pet(
        self, user_id: int, user_pet_id: int
    ) -> tuple[Optional[asyncpg.Record], int, int]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            pet_row = await connection.fetchrow(
                "SELECT id, is_active FROM user_pets WHERE user_id = $1 AND id = $2 FOR UPDATE",
                user_id,
                user_pet_id,
            )
            if pet_row is None:
                return None, 0, BASE_PET_SLOTS

            if not bool(pet_row["is_active"]):
                raise DatabaseError("Ce pet n'est pas équipé.")

            active_count = int(
                await connection.fetchval(
                    "SELECT COUNT(*) FROM user_pets WHERE user_id = $1 AND is_active",
                    user_id,
                )
                or 0
            )

            grade_row = await connection.fetchrow(
                "SELECT grade_level FROM user_grades WHERE user_id = $1",
                user_id,
            )
            grade_level = int(grade_row["grade_level"]) if grade_row else 0
            max_slots = BASE_PET_SLOTS + grade_level

            await connection.execute(
                "UPDATE user_pets SET is_active = FALSE WHERE id = $1",
                user_pet_id,
            )
            new_active_count = max(0, active_count - 1)

            await connection.execute(
                "UPDATE users SET pet_last_claim = NOW() WHERE user_id = $1",
                user_id,
            )

        record = await self.get_user_pet(user_id, user_pet_id)
        return record, new_active_count, max_slots

    async def swap_active_pets(
        self, user_id: int, pet_out_id: int, pet_in_id: int
    ) -> tuple[asyncpg.Record, asyncpg.Record, int, int]:
        if pet_out_id == pet_in_id:
            raise DatabaseError("Tu dois sélectionner deux pets différents pour le swap.")

        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            out_row = await connection.fetchrow(
                "SELECT id, is_active, on_market FROM user_pets WHERE user_id = $1 AND id = $2 FOR UPDATE",
                user_id,
                pet_out_id,
            )
            if out_row is None:
                raise DatabaseError("Le pet à retirer est introuvable.")
            if not bool(out_row["is_active"]):
                raise DatabaseError("Le pet à retirer n'est pas actuellement équipé.")

            in_row = await connection.fetchrow(
                "SELECT id, is_active, on_market FROM user_pets WHERE user_id = $1 AND id = $2 FOR UPDATE",
                user_id,
                pet_in_id,
            )
            if in_row is None:
                raise DatabaseError("Le pet à équiper est introuvable.")
            if bool(in_row["is_active"]):
                raise DatabaseError("Le pet à équiper est déjà actif.")
            if bool(in_row.get("on_market")):
                raise DatabaseError(
                    "Le pet que tu veux équiper est actuellement en vente sur ton stand. Retire-le avant de l'équiper."
                )

            active_count = int(
                await connection.fetchval(
                    "SELECT COUNT(*) FROM user_pets WHERE user_id = $1 AND is_active",
                    user_id,
                )
                or 0
            )

            grade_row = await connection.fetchrow(
                "SELECT grade_level FROM user_grades WHERE user_id = $1",
                user_id,
            )
            grade_level = int(grade_row["grade_level"]) if grade_row else 0
            max_slots = BASE_PET_SLOTS + grade_level

            await connection.execute(
                "UPDATE user_pets SET is_active = FALSE WHERE id = $1",
                pet_out_id,
            )
            await connection.execute(
                "UPDATE user_pets SET is_active = TRUE, on_market = FALSE WHERE id = $1",
                pet_in_id,
            )
            await connection.execute(
                "UPDATE users SET pet_last_claim = NOW() WHERE user_id = $1",
                user_id,
            )

        removed = await self.get_user_pet(user_id, pet_out_id)
        added = await self.get_user_pet(user_id, pet_in_id)
        if removed is None or added is None:
            raise DatabaseError("Impossible de récupérer les informations du swap.")
        return removed, added, active_count, max_slots

    async def set_active_pet(
        self, user_id: int, user_pet_id: int
    ) -> tuple[Optional[asyncpg.Record], bool, int]:
        """Active ou désactive un pet pour un utilisateur.

        Retourne un tuple ``(record, activé, total_actifs)``. ``record`` vaut ``None``
        si le pet n'existe pas. ``activé`` indique si le pet a été équipé suite à
        l'appel et ``total_actifs`` représente le nombre total de pets actuellement
        équipés par l'utilisateur.
        """

        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            pet_row = await connection.fetchrow(
                "SELECT id, is_active, on_market FROM user_pets WHERE user_id = $1 AND id = $2 FOR UPDATE",
                user_id,
                user_pet_id,
            )
            if pet_row is None:
                return None, False, 0

            currently_active = bool(pet_row["is_active"])
            if not currently_active and bool(pet_row.get("on_market")):
                raise DatabaseError(
                    "Ce pet est actuellement en vente sur ton stand. Retire-le avant de l'équiper."
                )
            active_count = int(
                await connection.fetchval(
                    "SELECT COUNT(*) FROM user_pets WHERE user_id = $1 AND is_active",
                    user_id,
                )
                or 0
            )

            grade_row = await connection.fetchrow(
                "SELECT grade_level FROM user_grades WHERE user_id = $1",
                user_id,
            )
            grade_level = int(grade_row["grade_level"]) if grade_row else 0
            max_slots = BASE_PET_SLOTS + grade_level

            if currently_active:
                await connection.execute(
                    "UPDATE user_pets SET is_active = FALSE WHERE id = $1",
                    user_pet_id,
                )
                new_active_count = max(0, active_count - 1)
            else:
                if active_count >= max_slots:
                    # FIX: Provide a clearer, modern formatted error when slots are full.
                    raise DatabaseError(
                        f"Tu ne peux pas équiper plus de {max_slots} pets simultanément. Monte en grade pour augmenter ta limite !"
                    )
                await connection.execute(
                    "UPDATE user_pets SET is_active = TRUE, on_market = FALSE WHERE id = $1",
                    user_pet_id,
                )
                new_active_count = active_count + 1

            await connection.execute(
                "UPDATE users SET pet_last_claim = NOW() WHERE user_id = $1",
                user_id,
            )

        record = await self.get_user_pet(user_id, user_pet_id)
        return record, not currently_active, new_active_count

    async def claim_active_pet_income(
        self, user_id: int
    ) -> tuple[
        int,
        Sequence[asyncpg.Record],
        float,
        dict[str, float],
        dict[str, object],
        Dict[int, tuple[int, int]],
        dict[str, object],
    ]:
        await self.ensure_user(user_id)
        # FIX: Fetch best non-huge income outside of the critical transaction to limit lock duration.
        best_non_huge_income = await self.get_best_non_huge_income(user_id)
        progress_updates: Dict[int, tuple[int, int]] = {}
        async with self.transaction() as connection:
            rows = await connection.fetch(
                """
                SELECT
                    up.id,
                    up.nickname,
                    up.is_active,
                    up.is_huge,
                    up.is_gold,
                    up.is_rainbow,

                    up.is_galaxy,
                    up.is_shiny,
                    up.huge_level,
                    up.huge_xp,
                    up.acquired_at,
                    p.pet_id,
                    p.name,
                    p.rarity,
                    p.image_url,
                    p.base_income_per_hour,
                    u.pet_last_claim,
                    u.balance,
                    u.pet_booster_multiplier,
                    u.pet_booster_expires_at,
                    u.pet_booster_activated_at,
                    u.active_potion_slug,
                    u.active_potion_expires_at,
                    cm.clan_id AS member_clan_id,
                    c.name AS clan_name,
                    c.pb_boost_multiplier,
                    c.boost_level AS clan_boost_level,
                    c.banner_emoji AS clan_banner,
                    c.shiny_luck_multiplier
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                JOIN users AS u ON u.user_id = up.user_id
                LEFT JOIN clan_members AS cm ON cm.user_id = up.user_id
                LEFT JOIN clans AS c ON c.clan_id = cm.clan_id
                WHERE up.user_id = $1 AND up.is_active
                ORDER BY up.id
                FOR UPDATE OF up
                """,
                user_id,
            )

            if not rows:
                return 0, [], 0.0, {}, {}, {}, {}

            first_row = rows[0]
            now = datetime.now(timezone.utc)
            last_claim: Optional[datetime] = first_row["pet_last_claim"]
            elapsed_seconds = (now - last_claim).total_seconds() if last_claim else 0.0
            if elapsed_seconds <= 0:
                await connection.execute(
                    "UPDATE users SET pet_last_claim = $1 WHERE user_id = $2",
                    now,
                    user_id,
                )
                return 0, rows, 0.0, {}, {}, {}, {}

            booster_multiplier = float(first_row.get("pet_booster_multiplier") or 1.0)
            booster_expires = first_row.get("pet_booster_expires_at")
            booster_activated = first_row.get("pet_booster_activated_at")
            active_potion_slug = first_row.get("active_potion_slug")
            active_potion_expires_at = first_row.get("active_potion_expires_at")
            booster_seconds = 0.0
            booster_remaining = 0.0
            booster_extra_amount = 0
            potion_multiplier = 1.0
            potion_bonus_amount = 0
            potion_remaining = 0.0
            potion_definition: PotionDefinition | None = None
            potion_should_clear = False

            if isinstance(booster_expires, datetime):
                booster_remaining = (booster_expires - now).total_seconds()

            if (
                booster_multiplier > 1
                and isinstance(booster_expires, datetime)
                and isinstance(booster_activated, datetime)
                and last_claim is not None
            ):
                interval_start = last_claim
                interval_end = now
                overlap_start = max(interval_start, booster_activated)
                overlap_end = min(interval_end, booster_expires)
                if overlap_end > overlap_start:
                    booster_seconds = (overlap_end - overlap_start).total_seconds()

            if active_potion_slug:
                potion_definition = POTION_DEFINITION_MAP.get(str(active_potion_slug))
                if isinstance(active_potion_expires_at, datetime) and potion_definition is not None:
                    if active_potion_expires_at > now:
                        potion_remaining = (
                            active_potion_expires_at - now
                        ).total_seconds()
                        if potion_definition.effect_type == "pb_boost":
                            potion_multiplier += float(potion_definition.effect_value)
                    else:
                        potion_should_clear = True
                else:
                    potion_should_clear = True

            effective_incomes: list[int] = []
            for row in rows:
                base_income = int(row["base_income_per_hour"])
                if bool(row["is_huge"]):
                    name = str(row.get("name", ""))
                    level = int(row.get("huge_level") or 1)
                    multiplier = get_huge_level_multiplier(name, level)
                    scaled_income = int(best_non_huge_income * multiplier)
                    income_value = max(HUGE_PET_MIN_INCOME, scaled_income)
                else:
                    if bool(row.get("is_galaxy")):
                        income_value = base_income * GALAXY_PET_MULTIPLIER
                    elif bool(row.get("is_rainbow")):
                        income_value = base_income * RAINBOW_PET_MULTIPLIER
                    elif bool(row["is_gold"]):
                        income_value = base_income * GOLD_PET_MULTIPLIER
                    else:
                        income_value = base_income
                    if bool(row.get("is_shiny")):
                        income_value *= SHINY_PET_MULTIPLIER
                effective_incomes.append(income_value)

            hourly_income = sum(effective_incomes)
            if hourly_income <= 0:
                await connection.execute(
                    "UPDATE users SET pet_last_claim = $1 WHERE user_id = $2",
                    now,
                    user_id,
                )
                return 0, rows, elapsed_seconds, {}, {}, {}, {}

            elapsed_hours = elapsed_seconds / 3600
            base_amount = hourly_income * elapsed_hours
            base_income_int = int(base_amount)

            if booster_seconds > 0 and booster_multiplier > 1:
                booster_hours = booster_seconds / 3600
                booster_extra_amount = int(hourly_income * booster_hours * (booster_multiplier - 1))

            raw_income = base_income_int + booster_extra_amount
            if raw_income <= 0:
                return 0, rows, elapsed_seconds, {}, {}, {}, {}

            if potion_multiplier > 1.0:
                boosted_by_potion = int(round(raw_income * potion_multiplier))
                potion_bonus_amount = max(0, boosted_by_potion - raw_income)
                raw_income = boosted_by_potion

            base_consumed_seconds = (
                int((base_income_int / hourly_income) * 3600) if hourly_income else 0
            )
            booster_consumed_seconds = (
                int(min(elapsed_seconds, booster_seconds)) if booster_extra_amount > 0 else 0
            )
            consumed_seconds = max(base_consumed_seconds, booster_consumed_seconds)
            consumed_seconds = min(int(elapsed_seconds), consumed_seconds)

            if last_claim is None:
                new_claim_time = now
            else:
                new_claim_time = last_claim + timedelta(seconds=consumed_seconds)
                if new_claim_time > now:
                    new_claim_time = now

            clan_info: dict[str, object] = {}
            clan_id = first_row.get("member_clan_id")
            clan_multiplier = 1.0
            clan_shiny_multiplier = 1.0
            if clan_id is not None:
                clan_multiplier = max(1.0, float(first_row.get("pb_boost_multiplier") or 1.0))
                clan_shiny_multiplier = max(
                    1.0, float(first_row.get("shiny_luck_multiplier") or 1.0)
                )
            boosted_income = int(raw_income * clan_multiplier)
            clan_bonus = max(0, boosted_income - raw_income)
            if clan_id is not None:
                clan_info = {
                    "id": int(clan_id),
                    "name": str(first_row.get("clan_name") or "Clan"),
                    "multiplier": clan_multiplier,
                    "bonus": clan_bonus,
                    "boost_level": int(first_row.get("clan_boost_level") or 0),
                    "banner": str(first_row.get("clan_banner") or "⚔️"),
                    "shiny_multiplier": clan_shiny_multiplier,
                }

            income = boosted_income
            before_balance = int(first_row["balance"])
            after_balance = before_balance + income

            await connection.execute(
                "UPDATE users SET pet_last_claim = $1, balance = $2 WHERE user_id = $3",
                new_claim_time,
                after_balance,
                user_id,
            )
            description = f"Revenus passifs ({len(rows)} pets)"
            if clan_bonus > 0:
                description += " + boost clan"
            await self.record_transaction(
                connection=connection,
                user_id=user_id,
                transaction_type="pet_income",
                amount=income,
                balance_before=before_balance,
                balance_after=after_balance,
                description=description,
            )

            if (
                booster_multiplier > 1
                and isinstance(booster_expires, datetime)
                and booster_expires <= now
            ):
                await connection.execute(
                    """
                    UPDATE users
                    SET
                        pet_booster_multiplier = 1,
                        pet_booster_activated_at = NULL,
                        pet_booster_expires_at = NULL
                    WHERE user_id = $1
                    """,
                    user_id,
                )

            shares: list[int] = [0 for _ in rows]
            if income > 0 and hourly_income > 0 and rows:
                remaining_income = income
                for index, effective in enumerate(effective_incomes):
                    if index == len(rows) - 1:
                        share_amount = remaining_income
                    else:
                        proportion = effective / hourly_income if hourly_income else 0.0
                        share_amount = int(round(income * proportion))
                        share_amount = max(0, min(remaining_income, share_amount))
                        remaining_income -= share_amount
                    shares[index] = share_amount

            for share_amount, row in zip(shares, rows):
                if not bool(row.get("is_huge")):
                    continue
                # FIX: Grant a minimum XP point even when the huge pet's share rounds to zero.
                xp_gain = share_amount // 1_000
                if xp_gain <= 0:
                    xp_gain = 1
                level = max(1, int(row.get("huge_level") or 1))
                current_xp = max(0, int(row.get("huge_xp") or 0))
                new_level = level
                accumulated_xp = current_xp + xp_gain
                while new_level < HUGE_PET_LEVEL_CAP:
                    required = huge_level_required_xp(new_level)
                    if required <= 0 or accumulated_xp < required:
                        break
                    accumulated_xp -= required
                    new_level += 1
                if new_level >= HUGE_PET_LEVEL_CAP:
                    new_level = HUGE_PET_LEVEL_CAP
                    accumulated_xp = 0
                if new_level != level or accumulated_xp != current_xp:
                    user_pet_id = int(row["id"])
                    progress_updates[user_pet_id] = (new_level, accumulated_xp)

            for pet_id, (new_level, new_xp) in progress_updates.items():
                await connection.execute(
                    "UPDATE user_pets SET huge_level = $2, huge_xp = $3 WHERE id = $1",
                    pet_id,
                    new_level,
                    new_xp,
                )

        booster_info: dict[str, float] = {}
        if booster_multiplier > 1:
            booster_info = {
                "multiplier": booster_multiplier,
                "extra": float(max(0, booster_extra_amount)),
                "remaining_seconds": float(max(0.0, booster_remaining)),
            }

        potion_info: dict[str, object] = {}
        if potion_definition and potion_multiplier > 1.0:
            potion_info = {
                "name": potion_definition.name,
                "slug": potion_definition.slug,
                "multiplier": potion_multiplier,
                "bonus": int(max(0, potion_bonus_amount)),
                "remaining_seconds": float(max(0.0, potion_remaining)),
            }

        if potion_should_clear:
            # FIX: Clear expired potions within the active transaction for consistency.
            await self.clear_active_potion(user_id, connection=connection)

        return (
            income,
            rows,
            elapsed_seconds,
            booster_info,
            clan_info,
            progress_updates,
            potion_info,
        )

    async def record_pet_opening(self, user_id: int, pet_id: int) -> None:
        await self.ensure_user(user_id)
        await self.pool.execute(
            "INSERT INTO pet_openings (user_id, pet_id) VALUES ($1, $2)",
            user_id,
            pet_id,
        )

    async def get_mastery_progress(self, user_id: int, mastery_slug: str) -> Dict[str, int]:
        definition = get_mastery_definition(mastery_slug)
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            """
            SELECT level, experience
            FROM user_masteries
            WHERE user_id = $1 AND mastery_slug = $2
            """,
            user_id,
            mastery_slug,
        )
        if row is None:
            await self.pool.execute(
                "INSERT INTO user_masteries (user_id, mastery_slug) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                user_id,
                mastery_slug,
            )
            level = 1
            experience = 0
        else:
            level = int(row["level"])
            experience = int(row["experience"])

        xp_to_next = definition.required_xp(level) if level < definition.max_level else 0
        return {
            "level": level,
            "experience": experience,
            "max_level": definition.max_level,
            "xp_to_next_level": xp_to_next,
        }

    async def add_mastery_experience(
        self, user_id: int, mastery_slug: str, amount: int
    ) -> Dict[str, object]:
        definition = get_mastery_definition(mastery_slug)
        if amount <= 0:
            progress = await self.get_mastery_progress(user_id, mastery_slug)
            progress.update(
                previous_level=progress["level"],
                levels_gained=0,
                new_levels=[],
            )
            return progress

        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                """
                SELECT level, experience
                FROM user_masteries
                WHERE user_id = $1 AND mastery_slug = $2
                FOR UPDATE
                """,
                user_id,
                mastery_slug,
            )
            if row is None:
                level = 1
                experience = 0
                await connection.execute(
                    """
                    INSERT INTO user_masteries (user_id, mastery_slug, level, experience)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id, mastery_slug) DO UPDATE
                    SET level = EXCLUDED.level, experience = EXCLUDED.experience
                    """,
                    user_id,
                    mastery_slug,
                    level,
                    experience,
                )
            else:
                level = int(row["level"])
                experience = int(row["experience"])

            previous_level = level
            experience += int(amount)
            levels_gained = 0
            new_levels: List[int] = []

            while level < definition.max_level:
                required = definition.required_xp(level)
                if required <= 0 or experience < required:
                    break
                experience -= required
                level += 1
                levels_gained += 1
                new_levels.append(level)

            if level >= definition.max_level:
                level = definition.max_level
                experience = 0

            await connection.execute(
                """
                UPDATE user_masteries
                SET level = $3, experience = $4
                WHERE user_id = $1 AND mastery_slug = $2
                """,
                user_id,
                mastery_slug,
                level,
                experience,
            )

        xp_to_next = definition.required_xp(level) if level < definition.max_level else 0
        return {
            "level": level,
            "experience": experience,
            "max_level": definition.max_level,
            "xp_to_next_level": xp_to_next,
            "previous_level": previous_level,
            "levels_gained": levels_gained,
            "new_levels": new_levels,
        }

    async def get_pet_opening_counts(self) -> tuple[int, Mapping[int, int]]:
        rows = await self.pool.fetch(
            "SELECT pet_id, COUNT(*) AS total FROM pet_openings GROUP BY pet_id"
        )
        totals = {int(row["pet_id"]): int(row["total"]) for row in rows}
        total_openings = sum(totals.values())
        return total_openings, totals

    async def count_huge_pets(self) -> int:
        value = await self.pool.fetchval("SELECT COUNT(*) FROM user_pets WHERE is_huge")
        return int(value or 0)

    async def count_gold_pets(self) -> int:
        value = await self.pool.fetchval("SELECT COUNT(*) FROM user_pets WHERE is_gold")
        return int(value or 0)

    async def get_pet_counts(self) -> Dict[int, int]:
        """Retourne le nombre total d'exemplaires pour chaque pet."""

        rows = await self.pool.fetch(
            "SELECT pet_id, COUNT(*) AS total FROM user_pets GROUP BY pet_id"
        )
        return {int(row["pet_id"]): int(row["total"]) for row in rows}

    @staticmethod
    def _build_variant_code(is_gold: bool, is_rainbow: bool, is_galaxy: bool, is_shiny: bool) -> str:
        if is_galaxy:
            base = "galaxy"
        elif is_rainbow:
            base = "rainbow"
        elif is_gold:
            base = "gold"
        else:
            base = "normal"
        if is_shiny:
            return f"{base}+shiny"
        return base

    async def record_pet_trade_value(
        self,
        *,
        pet_id: int,
        is_gold: bool,
        is_rainbow: bool,
        is_galaxy: bool,
        is_shiny: bool,
        price: int,
        source: str,
        connection: asyncpg.Connection | None = None,
    ) -> None:
        if price < 0:
            raise ValueError("Le prix enregistré doit être positif")
        if source not in {"stand", "trade"}:
            raise ValueError("Source de trade inconnue")

        params = (pet_id, is_gold, is_rainbow, is_galaxy, is_shiny, price, source)
        query = (
            """
            INSERT INTO pet_trade_history (pet_id, is_gold, is_rainbow, is_galaxy, is_shiny, price, source)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """
        )

        if connection is not None:
            await connection.execute(query, *params)
            return

        async with self.transaction() as txn_connection:
            await txn_connection.execute(query, *params)

    async def get_pet_market_values(self) -> Dict[Tuple[int, str], int]:
        """Calcule le prix moyen par pet et variante en se basant sur les échanges recensés."""

        rows = await self.pool.fetch(
            """
            SELECT pet_id, is_gold, is_rainbow, is_galaxy, is_shiny, price
            FROM pet_trade_history
        """
        )

        totals: Dict[Tuple[int, str], tuple[int, int]] = {}
        for row in rows:
            pet_id = int(row["pet_id"])
            code = self._build_variant_code(
                bool(row.get("is_gold")),
                bool(row.get("is_rainbow")),
                bool(row.get("is_galaxy")),
                bool(row.get("is_shiny")),
            )
            key = (pet_id, code)
            price = int(row["price"])
            total, count = totals.get(key, (0, 0))
            totals[key] = (total + price, count + 1)

        averages: Dict[Tuple[int, str], int] = {}
        for key, (total, count) in totals.items():
            if count:
                averages[key] = int(round(total / count))
        return averages

    # ------------------------------------------------------------------
    # King of the Hill
    # ------------------------------------------------------------------
    async def get_koth_state(self, guild_id: int) -> Optional[asyncpg.Record]:
        row = await self.pool.fetchrow(
            """
            SELECT guild_id, king_user_id, channel_id, claimed_at, last_roll_at
            FROM koth_states
            WHERE guild_id = $1
            """,
            guild_id,
        )
        return row

    async def upsert_koth_state(
        self, guild_id: int, king_user_id: int, channel_id: int
    ) -> asyncpg.Record:
        now = datetime.now(timezone.utc)
        row = await self.pool.fetchrow(
            """
            INSERT INTO koth_states (guild_id, king_user_id, channel_id, claimed_at, last_roll_at)
            VALUES ($1, $2, $3, $4, $4)
            ON CONFLICT (guild_id) DO UPDATE
            SET king_user_id = EXCLUDED.king_user_id,
                channel_id = EXCLUDED.channel_id,
                claimed_at = EXCLUDED.claimed_at,
                last_roll_at = EXCLUDED.last_roll_at
            RETURNING guild_id, king_user_id, channel_id, claimed_at, last_roll_at
            """,
            guild_id,
            king_user_id,
            channel_id,
            now,
        )
        if row is None:
            raise DatabaseError("Impossible de mettre à jour l'état King of the Hill")
        return row

    async def get_all_koth_states(self) -> Sequence[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT guild_id, king_user_id, channel_id, claimed_at, last_roll_at FROM koth_states"
        )

    async def update_koth_roll_timestamp(
        self, guild_id: int, *, timestamp: datetime | None = None
    ) -> None:
        moment = timestamp or datetime.now(timezone.utc)
        await self.pool.execute(
            "UPDATE koth_states SET last_roll_at = $2 WHERE guild_id = $1",
            guild_id,
            moment,
        )
    # ------------------------------------------------------------------
    # Historique financier
    # ------------------------------------------------------------------
    async def get_recent_transactions(self, user_id: int, limit: int = 20) -> Sequence[asyncpg.Record]:
        await self.ensure_user(user_id)
        query = """
            SELECT
                id,
                transaction_type,
                amount,
                balance_before,
                balance_after,
                description,
                related_user_id,
                created_at
            FROM transactions
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
        """
        return await self.pool.fetch(query, user_id, limit)

    # ------------------------------------------------------------------
    # Stand de la plaza (listings)
    # ------------------------------------------------------------------
    async def create_market_listing(
        self, seller_id: int, user_pet_id: int, price: int
    ) -> asyncpg.Record:
        if price < 0:
            raise DatabaseError("Le prix doit être positif")

        await self.ensure_user(seller_id)

        async with self.transaction() as connection:
            pet_row = await connection.fetchrow(
                """
                SELECT id, user_id, pet_id, is_active, on_market, is_gold, is_rainbow, is_shiny
                FROM user_pets
                WHERE id = $1
                FOR UPDATE
                """,
                user_pet_id,
            )
            if pet_row is None:
                raise DatabaseError("Ce pet est introuvable.")
            if int(pet_row["user_id"]) != seller_id:
                raise DatabaseError("Ce pet ne t'appartient pas.")
            if bool(pet_row["is_active"]):
                raise DatabaseError("Ce pet est actuellement équipé.")
            if bool(pet_row["on_market"]):
                raise DatabaseError("Ce pet est déjà en vente sur ton stand.")

            await connection.execute(
                "UPDATE user_pets SET on_market = TRUE WHERE id = $1",
                user_pet_id,
            )
            listing = await connection.fetchrow(
                """
                INSERT INTO market_listings (seller_id, user_pet_id, price)
                VALUES ($1, $2, $3)
                RETURNING *
                """,
                seller_id,
                user_pet_id,
                price,
            )

        if listing is None:
            raise DatabaseError("Impossible de créer la mise en vente.")
        return listing

    async def execute_trade(
        self,
        initiator_id: int,
        partner_id: int,
        initiator_offer: Mapping[str, Any],
        partner_offer: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if initiator_id == partner_id:
            raise DatabaseError("Impossible de trader avec toi-même.")

        async with self.transaction() as connection:
            await self.ensure_user(initiator_id)
            await self.ensure_user(partner_id)

            async def _prepare_pets(
                owner_id: int, offer: Mapping[str, Any]
            ) -> list[tuple[asyncpg.Record, int]]:
                pets_data: list[tuple[asyncpg.Record, int]] = []
                for entry in offer.get("pets", []):
                    user_pet_id = int(entry.get("id", 0))
                    price = int(max(0, int(entry.get("price", 0))))
                    row = await connection.fetchrow(
                        """
                        SELECT id, user_id, pet_id, is_active, on_market, is_gold, is_rainbow, is_shiny
                        FROM user_pets
                        WHERE id = $1
                        FOR UPDATE
                        """,
                        user_pet_id,
                    )
                    if row is None:
                        raise DatabaseError("Un des pets sélectionnés est introuvable.")
                    if int(row["user_id"]) != owner_id:
                        raise DatabaseError("Un des pets sélectionnés ne t'appartient plus.")
                    if bool(row.get("is_active")):
                        raise DatabaseError("Un des pets sélectionnés est actuellement équipé.")
                    if bool(row.get("on_market")):
                        raise DatabaseError("Un des pets sélectionnés est listé sur un stand.")
                    pets_data.append((row, price))
                return pets_data

            initiator_pets = await _prepare_pets(initiator_id, initiator_offer)
            partner_pets = await _prepare_pets(partner_id, partner_offer)

            initiator_balance_row = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                initiator_id,
            )
            partner_balance_row = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                partner_id,
            )
            if initiator_balance_row is None or partner_balance_row is None:
                raise DatabaseError("Impossible de récupérer les soldes pour le trade.")

            initiator_before = int(initiator_balance_row["balance"])
            partner_before = int(partner_balance_row["balance"])
            initiator_pb_out = max(0, int(initiator_offer.get("pb", 0) or 0))
            partner_pb_out = max(0, int(partner_offer.get("pb", 0) or 0))

            initiator_mid = initiator_before - initiator_pb_out
            partner_mid = partner_before - partner_pb_out
            if initiator_mid < 0:
                raise InsufficientBalanceError("Tu n'as pas assez de PB pour finaliser ce trade.")
            if partner_mid < 0:
                raise DatabaseError("Ton partenaire n'a plus assez de PB pour ce trade.")

            initiator_final = initiator_mid + partner_pb_out
            partner_final = partner_mid + initiator_pb_out

            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                initiator_final,
                initiator_id,
            )
            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                partner_final,
                partner_id,
            )

            if initiator_pb_out:
                await self.record_transaction(
                    connection=connection,
                    user_id=initiator_id,
                    transaction_type="trade",
                    amount=-initiator_pb_out,
                    balance_before=initiator_before,
                    balance_after=initiator_mid,
                    description=f"Trade avec {partner_id}",
                    related_user_id=partner_id,
                )
                await self.record_transaction(
                    connection=connection,
                    user_id=partner_id,
                    transaction_type="trade",
                    amount=initiator_pb_out,
                    balance_before=partner_mid,
                    balance_after=partner_mid + initiator_pb_out,
                    description=f"Trade avec {initiator_id}",
                    related_user_id=initiator_id,
                )

            if partner_pb_out:
                await self.record_transaction(
                    connection=connection,
                    user_id=partner_id,
                    transaction_type="trade",
                    amount=-partner_pb_out,
                    balance_before=partner_before,
                    balance_after=partner_mid,
                    description=f"Trade avec {initiator_id}",
                    related_user_id=initiator_id,
                )
                await self.record_transaction(
                    connection=connection,
                    user_id=initiator_id,
                    transaction_type="trade",
                    amount=partner_pb_out,
                    balance_before=initiator_mid,
                    balance_after=initiator_final,
                    description=f"Trade avec {partner_id}",
                    related_user_id=partner_id,
                )

            async def _transfer(
                target_id: int, pets: list[tuple[asyncpg.Record, int]]
            ) -> list[dict[str, Any]]:
                transferred: list[dict[str, Any]] = []
                for row, price in pets:
                    await connection.execute(
                        "UPDATE user_pets SET user_id = $1, is_active = FALSE, on_market = FALSE WHERE id = $2",
                        target_id,
                        int(row["id"]),
                    )
                    await self.record_pet_trade_value(
                        pet_id=int(row["pet_id"]),
                        is_gold=bool(row.get("is_gold")),
                        is_rainbow=bool(row.get("is_rainbow")),
                        is_galaxy=bool(row.get("is_galaxy")),
                        is_shiny=bool(row.get("is_shiny")),
                        price=max(0, price),
                        source="trade",
                        connection=connection,
                    )
                    transferred.append(
                        {
                            "user_pet_id": int(row["id"]),
                            "pet_id": int(row["pet_id"]),
                            "is_gold": bool(row.get("is_gold")),
                            "is_rainbow": bool(row.get("is_rainbow")),
                            "is_galaxy": bool(row.get("is_galaxy")),
                            "is_shiny": bool(row.get("is_shiny")),
                            "price": max(0, price),
                        }
                    )
                return transferred

            initiator_transferred = await _transfer(partner_id, initiator_pets)
            partner_transferred = await _transfer(initiator_id, partner_pets)

        return {
            "initiator_before": initiator_before,
            "initiator_after": initiator_final,
            "partner_before": partner_before,
            "partner_after": partner_final,
            "initiator_pets": initiator_transferred,
            "partner_pets": partner_transferred,
            "initiator_pb_out": initiator_pb_out,
            "partner_pb_out": partner_pb_out,
        }

    async def cancel_market_listing(self, listing_id: int, seller_id: int) -> asyncpg.Record:
        async with self.transaction() as connection:
            listing = await connection.fetchrow(
                "SELECT * FROM market_listings WHERE id = $1 FOR UPDATE",
                listing_id,
            )
            if listing is None:
                raise DatabaseError("Annonce introuvable.")
            if listing["status"] != "active":
                raise DatabaseError("Cette annonce n'est plus active.")
            if int(listing["seller_id"]) != seller_id:
                raise DatabaseError("Tu ne peux annuler que tes propres annonces.")

            await connection.execute(
                "UPDATE user_pets SET on_market = FALSE WHERE id = $1",
                int(listing["user_pet_id"]),
            )
            cancelled = await connection.fetchrow(
                """
                UPDATE market_listings
                SET status = 'cancelled', completed_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                listing_id,
            )

        if cancelled is None:
            raise DatabaseError("Impossible d'annuler l'annonce.")
        return cancelled

    async def purchase_market_listing(
        self, listing_id: int, buyer_id: int
    ) -> Dict[str, Any]:
        await self.ensure_user(buyer_id)

        async with self.transaction() as connection:
            listing = await connection.fetchrow(
                """
                SELECT *
                FROM market_listings
                WHERE id = $1
                FOR UPDATE
                """,
                listing_id,
            )
            if listing is None:
                raise DatabaseError("Cette annonce n'existe pas.")
            if listing["status"] != "active":
                raise DatabaseError("Cette annonce n'est plus disponible.")

            seller_id = int(listing["seller_id"])
            if seller_id == buyer_id:
                raise DatabaseError("Tu ne peux pas acheter ta propre annonce.")

            user_pet_id = int(listing["user_pet_id"])
            pet_row = await connection.fetchrow(
                """
                SELECT id, user_id, pet_id, is_active, on_market, is_gold, is_rainbow, is_shiny
                FROM user_pets
                WHERE id = $1
                FOR UPDATE
                """,
                user_pet_id,
            )
            if pet_row is None:
                raise DatabaseError("Le pet mis en vente est introuvable.")
            if int(pet_row["user_id"]) != seller_id:
                raise DatabaseError("Le vendeur ne possède plus ce pet.")
            if bool(pet_row["is_active"]):
                raise DatabaseError("Le pet mis en vente est actuellement équipé.")
            if not bool(pet_row["on_market"]):
                raise DatabaseError("Le pet n'est plus disponible à la vente.")

            price = int(listing["price"])
            seller_balance = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                seller_id,
            )
            buyer_balance = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                buyer_id,
            )
            if seller_balance is None or buyer_balance is None:
                raise DatabaseError("Impossible de récupérer les soldes.")

            seller_before = int(seller_balance["balance"])
            buyer_before = int(buyer_balance["balance"])
            if buyer_before < price:
                raise InsufficientBalanceError("Solde insuffisant pour cet achat.")

            seller_after = seller_before + price
            buyer_after = buyer_before - price

            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                buyer_after,
                buyer_id,
            )
            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                seller_after,
                seller_id,
            )

            await connection.execute(
                """
                UPDATE user_pets
                SET user_id = $1, is_active = FALSE, on_market = FALSE
                WHERE id = $2
                """,
                buyer_id,
                user_pet_id,
            )

            completed = await connection.fetchrow(
                """
                UPDATE market_listings
                SET status = 'sold', buyer_id = $1, completed_at = NOW()
                WHERE id = $2
                RETURNING *
                """,
                buyer_id,
                listing_id,
            )

            await self.record_pet_trade_value(
                pet_id=int(pet_row["pet_id"]),
                is_gold=bool(pet_row.get("is_gold")),
                is_rainbow=bool(pet_row.get("is_rainbow")),
                is_galaxy=bool(pet_row.get("is_galaxy")),
                is_shiny=bool(pet_row.get("is_shiny")),
                price=price,
                source="stand",
                connection=connection,
            )

            await self.record_transaction(
                connection=connection,
                user_id=buyer_id,
                transaction_type="stand_purchase",
                amount=-price,
                balance_before=buyer_before,
                balance_after=buyer_after,
                description=f"Achat annonce #{listing_id}",
                related_user_id=seller_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=seller_id,
                transaction_type="stand_sale",
                amount=price,
                balance_before=seller_before,
                balance_after=seller_after,
                description=f"Vente annonce #{listing_id}",
                related_user_id=buyer_id,
        )

        if completed is None:
            raise DatabaseError("Impossible de finaliser l'achat.")
        # FIX: Log successful market transfers for auditability.
        logger.info(
            "Market listing sold",
            extra={
                "listing_id": listing_id,
                "seller_id": seller_id,
                "buyer_id": buyer_id,
                "price": price,
            },
        )
        return {"listing": completed, "seller_before": seller_before, "buyer_before": buyer_before}

    async def get_market_listing(self, listing_id: int) -> Optional[asyncpg.Record]:
        query = """
            SELECT
                ml.*,
                up.is_active,
                up.is_huge,
                up.is_gold,
                up.is_rainbow,

                up.is_galaxy,
                up.on_market,
                p.name,
                p.rarity,
                p.base_income_per_hour
            FROM market_listings AS ml
            JOIN user_pets AS up ON up.id = ml.user_pet_id
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE ml.id = $1
        """
        return await self.pool.fetchrow(query, listing_id)

    async def list_active_market_listings(
        self, *, limit: int = 25, seller_id: int | None = None
    ) -> Sequence[asyncpg.Record]:
        limit = max(1, limit)
        query = """
            SELECT
                ml.id,
                ml.seller_id,
                ml.user_pet_id,
                ml.price,
                ml.created_at,
                up.is_huge,
                up.is_gold,
                up.is_rainbow,

                up.is_galaxy,
                p.name,
                p.rarity,
                p.base_income_per_hour
            FROM market_listings AS ml
            JOIN user_pets AS up ON up.id = ml.user_pet_id
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE ml.status = 'active'
              AND ($2::BIGINT IS NULL OR ml.seller_id = $2)
            ORDER BY ml.created_at ASC
            LIMIT $1
        """
        return await self.pool.fetch(query, limit, seller_id)

    async def get_market_activity(
        self, user_id: int, limit: int = 20
    ) -> Sequence[asyncpg.Record]:
        await self.ensure_user(user_id)
        query = """
            SELECT
                ml.id,
                ml.seller_id,
                ml.buyer_id,
                ml.price,
                ml.status,
                ml.created_at,
                ml.completed_at,
                up.is_huge,
                up.is_gold,
                up.is_rainbow,

                up.is_galaxy,
                p.name,
                p.rarity
            FROM market_listings AS ml
            JOIN user_pets AS up ON up.id = ml.user_pet_id
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE ml.seller_id = $1 OR ml.buyer_id = $1
            ORDER BY ml.created_at DESC
            LIMIT $2
        """
        return await self.pool.fetch(query, user_id, limit)

    async def create_consumable_listing(
        self,
        seller_id: int,
        *,
        item_type: str,
        quantity: int,
        price: int,
        item_slug: str | None = None,
    ) -> asyncpg.Record:
        if item_type not in {"ticket", "potion"}:
            raise DatabaseError("Type d'objet invalide pour la plaza.")
        if quantity <= 0:
            raise DatabaseError("La quantité doit être positive.")
        if price < 0:
            raise DatabaseError("Le prix doit être positif.")

        await self.ensure_user(seller_id)

        async with self.transaction() as connection:
            slug = item_slug
            if item_type == "ticket":
                slug = "raffle_ticket"
                remaining = await self.remove_raffle_tickets(
                    seller_id, amount=quantity, connection=connection
                )
                if remaining is None:
                    raise DatabaseError(
                        "Tu n'as pas assez de tickets de tombola pour cette mise en vente."
                    )
            else:
                if not slug:
                    raise DatabaseError("Merci de préciser la potion à mettre en vente.")
                consumed = await self.consume_user_potion(
                    seller_id,
                    slug,
                    quantity=quantity,
                    connection=connection,
                )
                if not consumed:
                    raise DatabaseError("Tu n'as pas assez d'exemplaires de cette potion.")

            listing = await connection.fetchrow(
                """
                INSERT INTO plaza_consumable_listings (seller_id, item_type, item_slug, quantity, price)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
                """,
                seller_id,
                item_type,
                slug,
                quantity,
                price,
            )

        if listing is None:
            raise DatabaseError("Impossible de créer l'annonce de consommable.")
        return listing

    async def cancel_consumable_listing(
        self, listing_id: int, seller_id: int
    ) -> asyncpg.Record:
        async with self.transaction() as connection:
            listing = await connection.fetchrow(
                "SELECT * FROM plaza_consumable_listings WHERE id = $1 FOR UPDATE",
                listing_id,
            )
            if listing is None:
                raise DatabaseError("Annonce introuvable.")
            if listing["status"] != "active":
                raise DatabaseError("Cette annonce n'est plus active.")
            if int(listing["seller_id"]) != seller_id:
                raise DatabaseError("Tu ne peux annuler que tes propres annonces.")

            quantity = int(listing["quantity"])
            item_type = str(listing["item_type"])
            item_slug = listing.get("item_slug")
            if item_type == "ticket":
                await self.add_raffle_tickets(
                    seller_id, amount=quantity, connection=connection
                )
            else:
                if not item_slug:
                    raise DatabaseError("Potion inconnue pour cette annonce.")
                await self.add_user_potion(
                    seller_id,
                    str(item_slug),
                    quantity=quantity,
                    connection=connection,
                )

            cancelled = await connection.fetchrow(
                """
                UPDATE plaza_consumable_listings
                SET status = 'cancelled', completed_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                listing_id,
            )

        if cancelled is None:
            raise DatabaseError("Impossible d'annuler l'annonce.")
        return cancelled

    async def purchase_consumable_listing(
        self, listing_id: int, buyer_id: int
    ) -> Dict[str, Any]:
        await self.ensure_user(buyer_id)

        async with self.transaction() as connection:
            listing = await connection.fetchrow(
                "SELECT * FROM plaza_consumable_listings WHERE id = $1 FOR UPDATE",
                listing_id,
            )
            if listing is None:
                raise DatabaseError("Cette annonce n'existe pas.")
            if listing["status"] != "active":
                raise DatabaseError("Cette annonce n'est plus disponible.")

            seller_id = int(listing["seller_id"])
            if seller_id == buyer_id:
                raise DatabaseError("Tu ne peux pas acheter ta propre annonce.")

            price = int(listing["price"])
            quantity = int(listing["quantity"])
            item_type = str(listing["item_type"])
            item_slug = str(listing.get("item_slug") or "")

            seller_balance = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                seller_id,
            )
            buyer_balance = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                buyer_id,
            )
            if seller_balance is None or buyer_balance is None:
                raise DatabaseError("Impossible de récupérer les soldes.")

            seller_before = int(seller_balance["balance"])
            buyer_before = int(buyer_balance["balance"])
            if buyer_before < price:
                raise InsufficientBalanceError("Solde insuffisant pour cet achat.")

            seller_after = seller_before + price
            buyer_after = buyer_before - price

            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                buyer_after,
                buyer_id,
            )
            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                seller_after,
                seller_id,
            )

            if item_type == "ticket":
                await self.add_raffle_tickets(
                    buyer_id, amount=quantity, connection=connection
                )
            else:
                await self.add_user_potion(
                    buyer_id,
                    item_slug,
                    quantity=quantity,
                    connection=connection,
                )

            completed = await connection.fetchrow(
                """
                UPDATE plaza_consumable_listings
                SET status = 'sold', buyer_id = $1, completed_at = NOW()
                WHERE id = $2
                RETURNING *
                """,
                buyer_id,
                listing_id,
            )

            await self.record_transaction(
                connection=connection,
                user_id=buyer_id,
                transaction_type="stand_purchase",
                amount=-price,
                balance_before=buyer_before,
                balance_after=buyer_after,
                description=f"Achat consommable #{listing_id}",
                related_user_id=seller_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=seller_id,
                transaction_type="stand_sale",
                amount=price,
                balance_before=seller_before,
                balance_after=seller_after,
                description=f"Vente consommable #{listing_id}",
                related_user_id=buyer_id,
            )

        if completed is None:
            raise DatabaseError("Impossible de finaliser l'achat.")
        return {
            "listing": completed,
            "seller_before": seller_before,
            "buyer_before": buyer_before,
        }

    async def get_consumable_listing(
        self, listing_id: int
    ) -> Optional[asyncpg.Record]:
        return await self.pool.fetchrow(
            "SELECT * FROM plaza_consumable_listings WHERE id = $1",
            listing_id,
        )

    async def list_active_consumable_listings(
        self,
        *,
        limit: int = 25,
        item_type: str | None = None,
    ) -> Sequence[asyncpg.Record]:
        limit = max(1, limit)
        return await self.pool.fetch(
            """
            SELECT *
            FROM plaza_consumable_listings
            WHERE status = 'active'
              AND ($2::TEXT IS NULL OR item_type = $2)
            ORDER BY created_at ASC
            LIMIT $1
            """,
            limit,
            item_type,
        )

    async def get_consumable_activity(
        self, user_id: int, limit: int = 20
    ) -> Sequence[asyncpg.Record]:
        await self.ensure_user(user_id)
        return await self.pool.fetch(
            """
            SELECT *
            FROM plaza_consumable_listings
            WHERE seller_id = $1 OR buyer_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
    async def get_database_stats(self) -> asyncpg.Record:
        query = """
            SELECT
                (SELECT COUNT(*) FROM users) AS users_count,
                (SELECT COUNT(*) FROM user_pets) AS pets_count,
                (SELECT COUNT(*) FROM transactions) AS transactions_count,
                (SELECT COUNT(*) FROM market_listings) AS listings_count,
                (SELECT COUNT(*) FROM market_listings WHERE status = 'active') AS listings_active,
                (SELECT COALESCE(SUM(balance), 0) FROM users) AS total_balance
        """
        row = await self.pool.fetchrow(query)
        if row is None:
            raise DatabaseError("Impossible de récupérer les statistiques de la base")
        return row
