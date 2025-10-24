"""Couche d'accès aux données minimaliste pour EcoBot."""
from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, Optional, Sequence, Set

import asyncpg

from config import (
    BASE_PET_SLOTS,
    CLAN_BASE_CAPACITY,
    CLAN_CAPACITY_PER_LEVEL,
    CLAN_CAPACITY_UPGRADE_COSTS,
    CLAN_BOOST_COSTS,
    CLAN_BOOST_INCREMENT,
    GOLD_PET_MULTIPLIER,
    GOLD_PET_COMBINE_REQUIRED,
    HUGE_GALE_NAME,
    HUGE_GRIFF_NAME,
    HUGE_PET_MIN_INCOME,
    HUGE_PET_MULTIPLIER,
    get_huge_multiplier,
)

__all__ = ["Database", "DatabaseError", "InsufficientBalanceError"]

logger = logging.getLogger(__name__)


class DatabaseError(RuntimeError):
    """Erreur levée lorsqu'une opération PostgreSQL échoue."""


class InsufficientBalanceError(DatabaseError):
    """Erreur dédiée lorsqu'un solde utilisateur est insuffisant."""


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
                """
                CREATE TABLE IF NOT EXISTS user_grades (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    grade_level INTEGER NOT NULL DEFAULT 0 CHECK (grade_level >= 0),
                    message_progress INTEGER NOT NULL DEFAULT 0 CHECK (message_progress >= 0),
                    invite_progress INTEGER NOT NULL DEFAULT 0 CHECK (invite_progress >= 0),
                    egg_progress INTEGER NOT NULL DEFAULT 0 CHECK (egg_progress >= 0),
                    gold_progress INTEGER NOT NULL DEFAULT 0 CHECK (gold_progress >= 0),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                """
                ALTER TABLE user_grades
                ADD COLUMN IF NOT EXISTS gold_progress INTEGER NOT NULL DEFAULT 0
                    CHECK (gold_progress >= 0)
                """
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
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    user_a_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    user_b_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    user_a_pb BIGINT NOT NULL DEFAULT 0 CHECK (user_a_pb >= 0),
                    user_b_pb BIGINT NOT NULL DEFAULT 0 CHECK (user_b_pb >= 0),
                    status VARCHAR(20) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trade_pets (
                    id SERIAL PRIMARY KEY,
                    trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
                    user_pet_id INTEGER NOT NULL REFERENCES user_pets(id) ON DELETE CASCADE,
                    from_user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    to_user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_users ON trades(user_a_id, user_b_id)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trade_pets_trade ON trade_pets(trade_id)"
            )
            await connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_pet_unique ON trade_pets(trade_id, user_pet_id)"
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

            updated = await connection.fetchrow(
                """
                UPDATE clans
                SET boost_level = $2, pb_boost_multiplier = $3
                WHERE clan_id = $1
                RETURNING *
                """,
                clan_id,
                new_level,
                multiplier,
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

    # ------------------------------------------------------------------
    # Gestion des grades
    # ------------------------------------------------------------------
    async def get_user_grade(self, user_id: int) -> asyncpg.Record:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            """
            SELECT grade_level, message_progress, invite_progress, egg_progress, gold_progress, updated_at
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
        message_delta: int = 0,
        invite_delta: int = 0,
        egg_delta: int = 0,
        gold_delta: int = 0,
        message_cap: int | None = None,
        invite_cap: int | None = None,
        egg_cap: int | None = None,
        gold_cap: int | None = None,
    ) -> asyncpg.Record:
        if (
            message_delta <= 0
            and invite_delta <= 0
            and egg_delta <= 0
            and gold_delta <= 0
        ):
            return await self.get_user_grade(user_id)

        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                """
                SELECT grade_level, message_progress, invite_progress, egg_progress, gold_progress
                FROM user_grades
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable lors de la mise à jour des grades")

            current_messages = int(row["message_progress"])
            current_invites = int(row["invite_progress"])
            current_eggs = int(row["egg_progress"])
            current_gold = int(row["gold_progress"])

            new_messages = max(0, current_messages + max(message_delta, 0))
            new_invites = max(0, current_invites + max(invite_delta, 0))
            new_eggs = max(0, current_eggs + max(egg_delta, 0))
            new_gold = max(0, current_gold + max(gold_delta, 0))

            if message_cap is not None:
                new_messages = min(new_messages, message_cap)
            if invite_cap is not None:
                new_invites = min(new_invites, invite_cap)
            if egg_cap is not None:
                new_eggs = min(new_eggs, egg_cap)
            if gold_cap is not None:
                new_gold = min(new_gold, gold_cap)

            updated = await connection.fetchrow(
                """
                UPDATE user_grades
                SET message_progress = $1,
                    invite_progress = $2,
                    egg_progress = $3,
                    gold_progress = $4,
                    updated_at = NOW()
                WHERE user_id = $5
                RETURNING *
                """,
                new_messages,
                new_invites,
                new_eggs,
                new_gold,
                user_id,
            )

        if updated is None:
            raise DatabaseError("Impossible de mettre à jour les quêtes de grade")
        return updated

    async def complete_grade_if_ready(
        self,
        user_id: int,
        *,
        message_goal: int,
        invite_goal: int,
        egg_goal: int,
        gold_goal: int,
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
                int(row["message_progress"]) < message_goal
                or int(row["invite_progress"]) < invite_goal
                or int(row["egg_progress"]) < egg_goal
                or int(row["gold_progress"]) < gold_goal
            ):
                return False, row

            new_row = await connection.fetchrow(
                """
                UPDATE user_grades
                SET grade_level = $1,
                    message_progress = 0,
                    invite_progress = 0,
                    egg_progress = 0,
                    gold_progress = 0,
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
            SELECT up.user_id, up.pet_id, up.is_gold, up.is_huge, p.base_income_per_hour, p.name
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
            if bool(row["is_gold"]):
                value = int(value * GOLD_PET_MULTIPLIER)
            if bool(row["is_huge"]):
                multiplier = max(1, get_huge_multiplier(name))
                huge_floor = max(base_income * multiplier * 150, value)
                value = huge_floor
            rap_totals[user_id] += max(0, value)

        sorted_totals = sorted(rap_totals.items(), key=lambda item: item[1], reverse=True)
        return sorted_totals[:clamped_limit]

    async def get_hourly_income_leaderboard(self, limit: int) -> list[tuple[int, int]]:
        clamped_limit = max(0, int(limit))
        if clamped_limit == 0:
            return []

        rows = await self.pool.fetch(
            """
            WITH best_non_huge AS (
                SELECT
                    up.user_id,
                    COALESCE(
                        MAX(p.base_income_per_hour * CASE WHEN up.is_gold THEN $2 ELSE 1 END),
                        0
                    ) AS best_income
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                WHERE NOT up.is_huge
                GROUP BY up.user_id
            ),
            active_income AS (
                SELECT
                    up.user_id,
                    SUM(
                        CASE
                            WHEN up.is_huge THEN GREATEST(
                                $3,
                                COALESCE(b.best_income, 0)
                                    * CASE
                                        WHEN p.name = $5 THEN $6
                                        WHEN p.name = $7 THEN $8
                                        ELSE $4
                                    END
                            )
                            ELSE p.base_income_per_hour * CASE WHEN up.is_gold THEN $2 ELSE 1 END
                        END
                    ) AS hourly_income
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                LEFT JOIN best_non_huge AS b ON b.user_id = up.user_id
                WHERE up.is_active
                GROUP BY up.user_id
            )
            SELECT user_id, hourly_income
            FROM active_income
            WHERE hourly_income > 0
            ORDER BY hourly_income DESC
            LIMIT $1
            """,
            clamped_limit,
            GOLD_PET_MULTIPLIER,
            HUGE_PET_MIN_INCOME,
            HUGE_PET_MULTIPLIER,
            HUGE_GRIFF_NAME,
            get_huge_multiplier(HUGE_GRIFF_NAME),
            HUGE_GALE_NAME,
            get_huge_multiplier(HUGE_GALE_NAME),
        )

        return [(int(row["user_id"]), int(row["hourly_income"])) for row in rows]

    async def reset_user_grade(self, user_id: int) -> None:
        await self.ensure_user(user_id)
        await self.pool.execute(
            """
            UPDATE user_grades
            SET grade_level = 0,
                message_progress = 0,
                invite_progress = 0,
                egg_progress = 0,
                gold_progress = 0,
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
        self, user_id: int, pet_id: int, *, is_huge: bool = False, is_gold: bool = False
    ) -> asyncpg.Record:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            """
            INSERT INTO user_pets (user_id, pet_id, is_huge, is_gold)
            VALUES ($1, $2, $3, $4)
            RETURNING id, user_id, pet_id, is_active, is_huge, is_gold, acquired_at
            """,
            user_id,
            pet_id,
            is_huge,
            is_gold,
        )
        if row is None:
            raise DatabaseError("Impossible de créer l'entrée user_pet")
        return row

    async def upgrade_pet_to_gold(
        self, user_id: int, pet_id: int
    ) -> tuple[asyncpg.Record, int]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            rows = await connection.fetch(
                """
                SELECT up.id
                FROM user_pets AS up
                WHERE up.user_id = $1
                  AND up.pet_id = $2
                  AND NOT up.is_gold
                  AND NOT up.is_active
                  AND NOT EXISTS (
                    SELECT 1
                    FROM trade_pets AS tp
                    JOIN trades AS t ON t.id = tp.trade_id
                    WHERE tp.user_pet_id = up.id AND t.status = 'pending'
                  )
                ORDER BY up.acquired_at
                FOR UPDATE
                """,
                user_id,
                pet_id,
            )

            available_ids: List[int] = [int(row["id"]) for row in rows]
            required = GOLD_PET_COMBINE_REQUIRED
            if len(available_ids) < required:
                raise DatabaseError(
                    f"Tu as besoin d'au moins {required} exemplaires non équipés et non échangés de ce pet pour créer une version or."
                )

            consumed_ids = available_ids[:required]
            await connection.execute(
                "DELETE FROM user_pets WHERE id = ANY($1::INT[])",
                consumed_ids,
            )

            inserted = await connection.fetchrow(
                """
                INSERT INTO user_pets (user_id, pet_id, is_gold)
                VALUES ($1, $2, TRUE)
                RETURNING id
                """,
                user_id,
                pet_id,
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

    async def get_user_pets(self, user_id: int) -> Sequence[asyncpg.Record]:
        return await self.pool.fetch(
            """
            SELECT
                up.id,
                up.nickname,
                up.is_active,
                up.is_huge,
                up.is_gold,
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

    async def get_active_user_pet(self, user_id: int) -> Optional[asyncpg.Record]:
        return await self.pool.fetchrow(
            """
            SELECT
                up.id,
                up.nickname,
                up.is_active,
                up.is_huge,
                up.is_gold,
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
            SELECT MAX(p.base_income_per_hour * CASE WHEN up.is_gold THEN $2 ELSE 1 END) AS best_income
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE up.user_id = $1 AND NOT up.is_huge
            """
        )
        executor = connection or self.pool
        if executor is None:
            raise DatabaseError("La connexion à la base de données n'est pas initialisée")
        row = await executor.fetchrow(query, user_id, GOLD_PET_MULTIPLIER)
        if row is None:
            return 0
        best_value = row.get("best_income")
        if best_value is None:
            return 0
        return int(best_value)

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
                "SELECT id, is_active FROM user_pets WHERE user_id = $1 AND id = $2 FOR UPDATE",
                user_id,
                user_pet_id,
            )
            if pet_row is None:
                return None, False, 0

            currently_active = bool(pet_row["is_active"])
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
                    raise DatabaseError(
                        f"Tu ne peux pas équiper plus de {max_slots} pets simultanément. Monte en grade pour augmenter ta limite !"
                    )
                await connection.execute(
                    "UPDATE user_pets SET is_active = TRUE WHERE id = $1",
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
    ) -> tuple[int, Sequence[asyncpg.Record], float, dict[str, float], dict[str, object]]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            rows = await connection.fetch(
                """
                SELECT
                    up.id,
                    up.nickname,
                    up.is_active,
                    up.is_huge,
                    up.is_gold,
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
                    cm.clan_id AS member_clan_id,
                    c.name AS clan_name,
                    c.pb_boost_multiplier,
                    c.boost_level AS clan_boost_level,
                    c.banner_emoji AS clan_banner
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                JOIN users AS u ON u.user_id = up.user_id
                LEFT JOIN clan_members AS cm ON cm.user_id = up.user_id
                LEFT JOIN clans AS c ON c.clan_id = cm.clan_id
                WHERE up.user_id = $1 AND up.is_active
                ORDER BY up.id
                FOR UPDATE
                """,
                user_id,
            )

            if not rows:
                return 0, [], 0.0, {}, {}

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
                return 0, rows, 0.0, {}, {}

            booster_multiplier = float(first_row.get("pet_booster_multiplier") or 1.0)
            booster_expires = first_row.get("pet_booster_expires_at")
            booster_activated = first_row.get("pet_booster_activated_at")
            booster_seconds = 0.0
            booster_remaining = 0.0
            booster_extra_amount = 0

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

            best_non_huge_income = await self.get_best_non_huge_income(
                user_id, connection=connection
            )
            effective_incomes: list[int] = []
            for row in rows:
                base_income = int(row["base_income_per_hour"])
                if bool(row["is_huge"]):
                    name = str(row.get("name", ""))
                    multiplier = max(1, get_huge_multiplier(name))
                    income_value = max(
                        HUGE_PET_MIN_INCOME,
                        best_non_huge_income * multiplier,
                    )
                else:
                    multiplier = GOLD_PET_MULTIPLIER if bool(row["is_gold"]) else 1
                    income_value = base_income * multiplier
                effective_incomes.append(income_value)

            hourly_income = sum(effective_incomes)
            if hourly_income <= 0:
                await connection.execute(
                    "UPDATE users SET pet_last_claim = $1 WHERE user_id = $2",
                    now,
                    user_id,
                )
                return 0, rows, elapsed_seconds, {}, {}

            elapsed_hours = elapsed_seconds / 3600
            base_amount = hourly_income * elapsed_hours
            base_income_int = int(base_amount)

            if booster_seconds > 0 and booster_multiplier > 1:
                booster_hours = booster_seconds / 3600
                booster_extra_amount = int(hourly_income * booster_hours * (booster_multiplier - 1))

            raw_income = base_income_int + booster_extra_amount
            if raw_income <= 0:
                return 0, rows, elapsed_seconds, {}, {}

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
            if clan_id is not None:
                clan_multiplier = max(1.0, float(first_row.get("pb_boost_multiplier") or 1.0))
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

        booster_info: dict[str, float] = {}
        if booster_multiplier > 1:
            booster_info = {
                "multiplier": booster_multiplier,
                "extra": float(max(0, booster_extra_amount)),
                "remaining_seconds": float(max(0.0, booster_remaining)),
            }

        return income, rows, elapsed_seconds, booster_info, clan_info

    async def record_pet_opening(self, user_id: int, pet_id: int) -> None:
        await self.ensure_user(user_id)
        await self.pool.execute(
            "INSERT INTO pet_openings (user_id, pet_id) VALUES ($1, $2)",
            user_id,
            pet_id,
        )

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

    async def get_pet_market_values(self) -> Dict[int, int]:
        """Calcule le prix moyen des pets selon les échanges terminés."""

        rows = await self.pool.fetch(
            """
            SELECT
                t.id,
                t.user_a_id,
                t.user_b_id,
                t.user_a_pb,
                t.user_b_pb,
                tp.user_pet_id,
                tp.from_user_id,
                tp.to_user_id,
                up.pet_id
            FROM trades AS t
            LEFT JOIN trade_pets AS tp ON tp.trade_id = t.id
            LEFT JOIN user_pets AS up ON up.id = tp.user_pet_id
            WHERE t.status = 'completed'
            ORDER BY t.id
            """
        )

        trade_map: Dict[int, Dict[str, object]] = {}
        for row in rows:
            trade_id = int(row["id"])
            trade = trade_map.setdefault(
                trade_id,
                {
                    "user_a_id": int(row["user_a_id"]),
                    "user_b_id": int(row["user_b_id"]),
                    "user_a_pb": int(row["user_a_pb"]),
                    "user_b_pb": int(row["user_b_pb"]),
                    "pets": [],
                },
            )

            user_pet_id = row["user_pet_id"]
            pet_id = row["pet_id"]
            if user_pet_id is None or pet_id is None:
                continue

            pets: list[dict[str, int]] = trade.setdefault("pets", [])  # type: ignore[assignment]
            pets.append(
                {
                    "pet_id": int(pet_id),
                    "from_user_id": int(row["from_user_id"]),
                    "to_user_id": int(row["to_user_id"]),
                }
            )

        totals: Dict[int, tuple[float, int]] = {}

        for trade in trade_map.values():
            pets: list[dict[str, int]] = trade["pets"]  # type: ignore[assignment]
            if not pets:
                continue

            user_a_id = int(trade["user_a_id"])
            user_b_id = int(trade["user_b_id"])
            user_a_pb = int(trade["user_a_pb"])
            user_b_pb = int(trade["user_b_pb"])

            pets_from_a = [pet for pet in pets if pet["from_user_id"] == user_a_id]
            pets_from_b = [pet for pet in pets if pet["from_user_id"] == user_b_id]

            if (
                user_a_pb > 0
                and user_b_pb == 0
                and pets_from_b
                and not pets_from_a
            ):
                price_per_pet = user_a_pb / len(pets_from_b)
                targets = pets_from_b
            elif (
                user_b_pb > 0
                and user_a_pb == 0
                and pets_from_a
                and not pets_from_b
            ):
                price_per_pet = user_b_pb / len(pets_from_a)
                targets = pets_from_a
            else:
                continue

            for pet in targets:
                pet_id = pet["pet_id"]
                total, count = totals.get(pet_id, (0.0, 0))
                totals[pet_id] = (total + price_per_pet, count + 1)

        return {pet_id: int(round(total / count)) for pet_id, (total, count) in totals.items() if count}

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
    # Gestion des échanges (trades)
    # ------------------------------------------------------------------
    async def create_trade(self, user_a_id: int, user_b_id: int) -> asyncpg.Record:
        if user_a_id == user_b_id:
            raise DatabaseError("Impossible de créer un échange avec soi-même")

        await self.ensure_user(user_a_id)
        await self.ensure_user(user_b_id)

        async with self.transaction() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO trades (user_a_id, user_b_id, status)
                VALUES ($1, $2, 'pending')
                RETURNING id, user_a_id, user_b_id, user_a_pb, user_b_pb, status, created_at, completed_at
                """,
                user_a_id,
                user_b_id,
            )

        if row is None:
            raise DatabaseError("Impossible de créer l'échange")
        return row

    async def update_trade_pb(self, trade_id: int, user_id: int, amount: int) -> asyncpg.Record:
        if amount < 0:
            raise DatabaseError("Le montant proposé doit être positif")

        async with self.transaction() as connection:
            trade = await connection.fetchrow("SELECT * FROM trades WHERE id = $1 FOR UPDATE", trade_id)
            if trade is None:
                raise DatabaseError("Échange introuvable")
            if trade["status"] != "pending":
                raise DatabaseError("Impossible de modifier un échange finalisé")

            user_a_id = int(trade["user_a_id"])
            user_b_id = int(trade["user_b_id"])
            if user_id not in (user_a_id, user_b_id):
                raise DatabaseError("Seuls les participants peuvent modifier l'échange")

            column = "user_a_pb" if user_id == user_a_id else "user_b_pb"
            await connection.execute(f"UPDATE trades SET {column} = $1 WHERE id = $2", amount, trade_id)
            updated = await connection.fetchrow("SELECT * FROM trades WHERE id = $1", trade_id)

        if updated is None:
            raise DatabaseError("Échange introuvable après mise à jour")
        return updated

    async def add_trade_pet(
        self,
        trade_id: int,
        user_pet_id: int,
        from_user_id: int,
        to_user_id: int,
    ) -> asyncpg.Record:
        async with self.transaction() as connection:
            trade = await connection.fetchrow("SELECT * FROM trades WHERE id = $1 FOR UPDATE", trade_id)
            if trade is None:
                raise DatabaseError("Échange introuvable")
            if trade["status"] != "pending":
                raise DatabaseError("Impossible de modifier un échange finalisé")

            user_a_id = int(trade["user_a_id"])
            user_b_id = int(trade["user_b_id"])
            if from_user_id not in (user_a_id, user_b_id) or to_user_id not in (user_a_id, user_b_id):
                raise DatabaseError("Participants invalides pour cet échange")
            if from_user_id == to_user_id:
                raise DatabaseError("Impossible d'envoyer un pet à soi-même dans un échange")

            pet_row = await connection.fetchrow(
                "SELECT id, user_id, is_active FROM user_pets WHERE id = $1 FOR UPDATE",
                user_pet_id,
            )
            if pet_row is None:
                raise DatabaseError("Le pet demandé est introuvable")
            if int(pet_row["user_id"]) != from_user_id:
                raise DatabaseError("Ce pet n'appartient plus à cet utilisateur")
            if bool(pet_row["is_active"]):
                raise DatabaseError("Le pet doit être déséquipé avant d'être échangé")

            exists = await connection.fetchval(
                "SELECT 1 FROM trade_pets WHERE trade_id = $1 AND user_pet_id = $2",
                trade_id,
                user_pet_id,
            )
            if exists:
                raise DatabaseError("Ce pet est déjà proposé dans l'échange")

            row = await connection.fetchrow(
                """
                INSERT INTO trade_pets (trade_id, user_pet_id, from_user_id, to_user_id)
                VALUES ($1, $2, $3, $4)
                RETURNING id, trade_id, user_pet_id, from_user_id, to_user_id
                """,
                trade_id,
                user_pet_id,
                from_user_id,
                to_user_id,
            )

        if row is None:
            raise DatabaseError("Impossible d'ajouter ce pet à l'échange")
        return row

    async def remove_trade_pet(self, trade_id: int, user_pet_id: int, user_id: int) -> bool:
        async with self.transaction() as connection:
            trade = await connection.fetchrow("SELECT status FROM trades WHERE id = $1 FOR UPDATE", trade_id)
            if trade is None:
                raise DatabaseError("Échange introuvable")
            if trade["status"] != "pending":
                raise DatabaseError("Impossible de modifier un échange finalisé")

            result = await connection.execute(
                """
                DELETE FROM trade_pets
                WHERE trade_id = $1 AND user_pet_id = $2 AND from_user_id = $3
                """,
                trade_id,
                user_pet_id,
                user_id,
            )

        return result.endswith("1")

    async def get_trade_state(self, trade_id: int) -> Optional[Dict[str, Any]]:
        trade = await self.pool.fetchrow("SELECT * FROM trades WHERE id = $1", trade_id)
        if trade is None:
            return None

        pets = await self.pool.fetch(
            """
            SELECT
                tp.id,
                tp.trade_id,
                tp.user_pet_id,
                tp.from_user_id,
                tp.to_user_id,
                up.user_id,
                up.is_huge,
                up.is_gold,
                p.name,
                p.rarity,
                p.image_url
            FROM trade_pets AS tp
            JOIN user_pets AS up ON up.id = tp.user_pet_id
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE tp.trade_id = $1
            ORDER BY tp.id
            """,
            trade_id,
        )

        return {"trade": trade, "pets": pets}

    async def finalize_trade(self, trade_id: int) -> Dict[str, Any]:
        async with self.transaction() as connection:
            trade = await connection.fetchrow("SELECT * FROM trades WHERE id = $1 FOR UPDATE", trade_id)
            if trade is None:
                raise DatabaseError("Échange introuvable")
            if trade["status"] != "pending":
                raise DatabaseError("Cet échange a déjà été finalisé ou annulé")

            user_a_id = int(trade["user_a_id"])
            user_b_id = int(trade["user_b_id"])
            user_a_pb = int(trade["user_a_pb"])
            user_b_pb = int(trade["user_b_pb"])

            user_a_row = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                user_a_id,
            )
            user_b_row = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                user_b_id,
            )
            if user_a_row is None or user_b_row is None:
                raise DatabaseError("Impossible de récupérer le solde des participants")

            balance_a_before = int(user_a_row["balance"])
            balance_b_before = int(user_b_row["balance"])
            if balance_a_before < user_a_pb:
                raise DatabaseError("Le solde du premier utilisateur est insuffisant")
            if balance_b_before < user_b_pb:
                raise DatabaseError("Le solde du second utilisateur est insuffisant")

            pet_rows = await connection.fetch(
                """
                SELECT
                    tp.id,
                    tp.user_pet_id,
                    tp.from_user_id,
                    tp.to_user_id,
                    up.user_id,
                    up.is_active,
                    up.is_huge,
                    up.is_gold,
                    p.name,
                    p.rarity
                FROM trade_pets AS tp
                JOIN user_pets AS up ON up.id = tp.user_pet_id
                JOIN pets AS p ON p.pet_id = up.pet_id
                WHERE tp.trade_id = $1
                FOR UPDATE
                """,
                trade_id,
            )

            transfers: list[dict[str, Any]] = []
            for pet in pet_rows:
                current_owner = int(pet["user_id"])
                if current_owner != int(pet["from_user_id"]):
                    raise DatabaseError("Un des pets ne se trouve plus dans l'inventaire de l'offreur")
                if bool(pet["is_active"]):
                    raise DatabaseError("Un des pets est encore équipé. Déséquipe-le avant de finaliser")

                await connection.execute(
                    "UPDATE user_pets SET user_id = $1, is_active = FALSE WHERE id = $2",
                    int(pet["to_user_id"]),
                    int(pet["user_pet_id"]),
                )
                transfers.append(
                    {
                        "user_pet_id": int(pet["user_pet_id"]),
                        "from_user_id": int(pet["from_user_id"]),
                        "to_user_id": int(pet["to_user_id"]),
                        "name": str(pet["name"]),
                        "rarity": str(pet["rarity"]),
                        "is_huge": bool(pet["is_huge"]),
                        "is_gold": bool(pet["is_gold"]),
                    }
                )

            balance_a_after = balance_a_before
            balance_b_after = balance_b_before

            if user_a_pb > 0:
                balance_a_after -= user_a_pb
                await connection.execute(
                    "UPDATE users SET balance = $1 WHERE user_id = $2",
                    balance_a_after,
                    user_a_id,
                )
                await self.record_transaction(
                    connection=connection,
                    user_id=user_a_id,
                    transaction_type="trade_send",
                    amount=-user_a_pb,
                    balance_before=balance_a_before,
                    balance_after=balance_a_after,
                    description=f"Trade #{trade_id}",
                    related_user_id=user_b_id,
                )
                balance_a_before = balance_a_after

            if user_b_pb > 0:
                balance_b_after -= user_b_pb
                await connection.execute(
                    "UPDATE users SET balance = $1 WHERE user_id = $2",
                    balance_b_after,
                    user_b_id,
                )
                await self.record_transaction(
                    connection=connection,
                    user_id=user_b_id,
                    transaction_type="trade_send",
                    amount=-user_b_pb,
                    balance_before=balance_b_before,
                    balance_after=balance_b_after,
                    description=f"Trade #{trade_id}",
                    related_user_id=user_a_id,
                )
                balance_b_before = balance_b_after

            if user_b_pb > 0:
                new_balance_a = balance_a_after + user_b_pb
                await connection.execute(
                    "UPDATE users SET balance = $1 WHERE user_id = $2",
                    new_balance_a,
                    user_a_id,
                )
                await self.record_transaction(
                    connection=connection,
                    user_id=user_a_id,
                    transaction_type="trade_receive",
                    amount=user_b_pb,
                    balance_before=balance_a_after,
                    balance_after=new_balance_a,
                    description=f"Trade #{trade_id}",
                    related_user_id=user_b_id,
                )
                balance_a_after = new_balance_a

            if user_a_pb > 0:
                new_balance_b = balance_b_after + user_a_pb
                await connection.execute(
                    "UPDATE users SET balance = $1 WHERE user_id = $2",
                    new_balance_b,
                    user_b_id,
                )
                await self.record_transaction(
                    connection=connection,
                    user_id=user_b_id,
                    transaction_type="trade_receive",
                    amount=user_a_pb,
                    balance_before=balance_b_after,
                    balance_after=new_balance_b,
                    description=f"Trade #{trade_id}",
                    related_user_id=user_a_id,
                )
                balance_b_after = new_balance_b

            final_trade = await connection.fetchrow(
                "UPDATE trades SET status = 'completed', completed_at = NOW() WHERE id = $1 RETURNING *",
                trade_id,
            )

        if final_trade is None:
            raise DatabaseError("Impossible de finaliser l'échange")

        return {
            "trade": final_trade,
            "transfers": transfers,
            "balances": {
                int(final_trade["user_a_id"]): {
                    "after": balance_a_after,
                },
                int(final_trade["user_b_id"]): {
                    "after": balance_b_after,
                },
            },
        }

    async def cancel_trade(self, trade_id: int) -> asyncpg.Record:
        async with self.transaction() as connection:
            trade = await connection.fetchrow("SELECT * FROM trades WHERE id = $1 FOR UPDATE", trade_id)
            if trade is None:
                raise DatabaseError("Échange introuvable")
            if trade["status"] != "pending":
                return trade

            cancelled = await connection.fetchrow(
                "UPDATE trades SET status = 'cancelled', completed_at = NOW() WHERE id = $1 RETURNING *",
                trade_id,
            )

        if cancelled is None:
            raise DatabaseError("Impossible d'annuler l'échange")
        return cancelled

    async def get_trade_history(self, user_id: int, limit: int = 20) -> Sequence[asyncpg.Record]:
        await self.ensure_user(user_id)
        query = """
            SELECT
                t.id,
                t.status,
                t.created_at,
                t.completed_at,
                CASE WHEN t.user_a_id = $1 THEN t.user_b_id ELSE t.user_a_id END AS partner_id,
                CASE WHEN t.user_a_id = $1 THEN t.user_a_pb ELSE t.user_b_pb END AS pb_sent,
                CASE WHEN t.user_a_id = $1 THEN t.user_b_pb ELSE t.user_a_pb END AS pb_received,
                COALESCE(SUM(CASE WHEN tp.from_user_id = $1 THEN 1 ELSE 0 END), 0) AS pets_sent,
                COALESCE(SUM(CASE WHEN tp.to_user_id = $1 THEN 1 ELSE 0 END), 0) AS pets_received
            FROM trades AS t
            LEFT JOIN trade_pets AS tp ON tp.trade_id = t.id
            WHERE t.user_a_id = $1 OR t.user_b_id = $1
            GROUP BY t.id
            ORDER BY t.created_at DESC
            LIMIT $2
        """
        return await self.pool.fetch(query, user_id, limit)

    async def get_trade_stats(self) -> asyncpg.Record:
        query = """
            SELECT
                COUNT(*) AS total_trades,
                COUNT(*) FILTER (WHERE status = 'completed') AS completed_trades,
                COUNT(*) FILTER (WHERE status = 'pending') AS pending_trades,
                COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled_trades,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN user_a_pb + user_b_pb ELSE 0 END), 0) AS total_pb,
                (
                    SELECT COUNT(*)
                    FROM trade_pets tp
                    JOIN trades t ON t.id = tp.trade_id
                    WHERE t.status = 'completed'
                ) AS total_pets_exchanged
            FROM trades
        """
        row = await self.pool.fetchrow(query)
        if row is None:
            raise DatabaseError("Impossible de récupérer les statistiques des échanges")
        return row

    async def get_database_stats(self) -> asyncpg.Record:
        query = """
            SELECT
                (SELECT COUNT(*) FROM users) AS users_count,
                (SELECT COUNT(*) FROM user_pets) AS pets_count,
                (SELECT COUNT(*) FROM transactions) AS transactions_count,
                (SELECT COUNT(*) FROM trades) AS trades_count,
                (SELECT COUNT(*) FROM trades WHERE status = 'completed') AS trades_completed,
                (SELECT COALESCE(SUM(balance), 0) FROM users) AS total_balance
        """
        row = await self.pool.fetchrow(query)
        if row is None:
            raise DatabaseError("Impossible de récupérer les statistiques de la base")
        return row
