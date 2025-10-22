"""Couche d'accès aux données minimaliste pour EcoBot."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Iterable, Mapping, Optional, Sequence

import asyncpg

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
                CREATE TABLE IF NOT EXISTS user_xp (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    total_xp BIGINT NOT NULL DEFAULT 0 CHECK (total_xp >= 0),
                    level INTEGER NOT NULL DEFAULT 1 CHECK (level >= 1)
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
                    is_huge BOOLEAN NOT NULL DEFAULT FALSE
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
            INSERT INTO user_xp (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )

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
    # Gestion de l'expérience
    # ------------------------------------------------------------------
    async def get_user_xp(self, user_id: int) -> tuple[int, int]:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            "SELECT total_xp, level FROM user_xp WHERE user_id = $1",
            user_id,
        )
        if row is None:
            raise DatabaseError("Utilisateur introuvable dans user_xp")
        return int(row["total_xp"]), int(row["level"])

    async def update_user_xp(self, user_id: int, *, total_xp: int, level: int) -> tuple[int, int]:
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT total_xp, level FROM user_xp WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable lors de la mise à jour XP")

            previous_total = int(row["total_xp"])
            previous_level = int(row["level"])
            await connection.execute(
                "UPDATE user_xp SET total_xp = $1, level = $2 WHERE user_id = $3",
                total_xp,
                level,
                user_id,
            )
        return previous_total, previous_level

    async def get_xp_leaderboard(self, limit: int) -> Sequence[asyncpg.Record]:
        query = "SELECT user_id, total_xp, level FROM user_xp ORDER BY total_xp DESC LIMIT $1"
        return await self.pool.fetch(query, limit)

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
                name = getattr(pet, "name", None) or pet["name"]  # type: ignore[index]
                rarity = getattr(pet, "rarity", None) or pet["rarity"]  # type: ignore[index]
                image_url = getattr(pet, "image_url", None) or pet["image_url"]  # type: ignore[index]
                base_income = getattr(pet, "base_income_per_hour", None) or pet["base_income_per_hour"]  # type: ignore[index]
                drop_rate = getattr(pet, "drop_rate", None) or pet["drop_rate"]  # type: ignore[index]
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

    async def add_user_pet(self, user_id: int, pet_id: int, *, is_huge: bool = False) -> asyncpg.Record:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            """
            INSERT INTO user_pets (user_id, pet_id, is_huge)
            VALUES ($1, $2, $3)
            RETURNING id, user_id, pet_id, is_active, is_huge, acquired_at
            """,
            user_id,
            pet_id,
            is_huge,
        )
        if row is None:
            raise DatabaseError("Impossible de créer l'entrée user_pet")
        return row

    async def get_user_pets(self, user_id: int) -> Sequence[asyncpg.Record]:
        return await self.pool.fetch(
            """
            SELECT
                up.id,
                up.nickname,
                up.is_active,
                up.is_huge,
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

    async def set_active_pet(
        self, user_id: int, user_pet_id: int
    ) -> tuple[Optional[asyncpg.Record], bool, int]:
        """Active ou désactive un pet pour un utilisateur.

        Retourne un tuple ``(record, activé, total_actifs)``. ``record`` vaut ``None``
        si le pet n'existe pas. ``activé`` indique si le pet a été équipé suite à
        l'appel et ``total_actifs`` représente le nombre total de pets actuellement
        équipés par l'utilisateur.
        """

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

            if currently_active:
                await connection.execute(
                    "UPDATE user_pets SET is_active = FALSE WHERE id = $1",
                    user_pet_id,
                )
                new_active_count = max(0, active_count - 1)
            else:
                if active_count >= 4:
                    raise DatabaseError("Tu ne peux pas équiper plus de 4 pets simultanément.")
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
    ) -> tuple[int, Sequence[asyncpg.Record], float]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            rows = await connection.fetch(
                """
                SELECT
                    up.id,
                    up.nickname,
                    up.is_active,
                    up.is_huge,
                    up.acquired_at,
                    p.pet_id,
                    p.name,
                    p.rarity,
                    p.image_url,
                    p.base_income_per_hour,
                    u.pet_last_claim,
                    u.balance
                FROM user_pets AS up
                JOIN pets AS p ON p.pet_id = up.pet_id
                JOIN users AS u ON u.user_id = up.user_id
                WHERE up.user_id = $1 AND up.is_active
                ORDER BY up.id
                FOR UPDATE
                """,
                user_id,
            )

            if not rows:
                return 0, [], 0.0

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
                return 0, rows, 0.0

            hourly_income = sum(int(row["base_income_per_hour"]) for row in rows)
            if hourly_income <= 0:
                await connection.execute(
                    "UPDATE users SET pet_last_claim = $1 WHERE user_id = $2",
                    now,
                    user_id,
                )
                return 0, rows, elapsed_seconds

            income = int(hourly_income * (elapsed_seconds / 3600))
            if income <= 0:
                return 0, rows, elapsed_seconds

            consumed_seconds = int((income / hourly_income) * 3600)
            if last_claim is None:
                new_claim_time = now
            else:
                new_claim_time = last_claim + timedelta(seconds=consumed_seconds)
                if new_claim_time > now:
                    new_claim_time = now

            before_balance = int(first_row["balance"])
            after_balance = before_balance + income

            await connection.execute(
                "UPDATE users SET pet_last_claim = $1, balance = $2 WHERE user_id = $3",
                new_claim_time,
                after_balance,
                user_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=user_id,
                transaction_type="pet_income",
                amount=income,
                balance_before=before_balance,
                balance_after=after_balance,
                description=f"Revenus passifs ({len(rows)} pets)",
            )

        return income, rows, elapsed_seconds

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
