"""Database access layer for the EcoBot project.

This module exposes the :class:`Database` class which centralises **all**
PostgreSQL interactions required by the Discord bot.  The class has been
carefully designed to satisfy the specification provided in the user request:

* asynchronous access through ``asyncpg`` with a connection pool
* automatic schema creation for every table mentioned in the requirements
* atomic transactions for all money related operations
* utility helpers for pagination, cooldown management and leaderboards
* extensive logging and docstrings for maintainability

The goal of this file is to be the single source of truth for the data access
layer; all cogs rely on the methods defined here.  Keeping all SQL queries in a
single module simplifies auditing and makes it easier to share transactions
between features.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence, Tuple

import asyncpg

__all__ = ["Database", "DatabaseError", "TransactionLogEntry"]


logger = logging.getLogger(__name__)


class DatabaseError(RuntimeError):
    """Exception levée lorsqu'une opération de base de données échoue."""


@dataclass(slots=True)
class TransactionLogEntry:
    """Structure représentant une entrée du journal des transactions."""

    user_id: int
    transaction_type: str
    amount: int
    balance_before: int
    balance_after: int
    description: str
    related_user_id: Optional[int]
    timestamp: datetime


class Database:
    """Gestionnaire d'accès PostgreSQL.

    Parameters
    ----------
    dsn:
        Chaîne de connexion PostgreSQL.
    min_size, max_size:
        Paramètres du pool ``asyncpg``.  Les valeurs par défaut conviennent à
        un hébergement sur Replit mais peuvent être ajustées par configuration.
    """

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        if not dsn:
            raise ValueError("Le DSN de connexion PostgreSQL est obligatoire")

        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._min_size = min_size
        self._max_size = max_size

    # ------------------------------------------------------------------
    # Gestion du cycle de vie
    # ------------------------------------------------------------------
    @property
    def pool(self) -> asyncpg.Pool:
        """Renvoie le pool et s'assure qu'il est initialisé."""

        if self._pool is None:
            raise DatabaseError("La base de données n'est pas connectée")
        return self._pool

    async def connect(self) -> None:
        """Initialise la connexion et crée les tables si nécessaire."""

        if self._pool is not None:
            return

        try:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                command_timeout=60,
            )
        except Exception as exc:  # pragma: no cover - logging only
            logger.exception("Impossible de créer le pool PostgreSQL")
            raise DatabaseError("Connexion base de données échouée") from exc

        logger.info("Connexion PostgreSQL établie - initialisation du schéma")
        await self._initialise_schema()

    async def close(self) -> None:
        """Ferme proprement le pool."""

        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Pool PostgreSQL fermé")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """Context manager pour exécuter une transaction atomique.

        Exemple::

            async with database.transaction() as conn:
                await conn.execute("UPDATE ...")

        Toutes les commandes d'argent dans le bot utilisent ce context manager
        afin de garantir la cohérence des soldes.
        """

        async with self.pool.acquire() as connection:
            async with connection.transaction():
                yield connection

    # ------------------------------------------------------------------
    # Initialisation du schéma
    # ------------------------------------------------------------------
    async def _initialise_schema(self) -> None:
        """Crée toutes les tables et index nécessaires."""

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    last_daily TIMESTAMPTZ,
                    message_cooldown TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shop_items (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    price BIGINT NOT NULL CHECK (price > 0),
                    type TEXT NOT NULL,
                    data JSONB NOT NULL DEFAULT '{}'::jsonb,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_purchases (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    item_id INTEGER NOT NULL REFERENCES shop_items(id),
                    purchase_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    price_paid BIGINT NOT NULL,
                    tax_paid BIGINT NOT NULL DEFAULT 0
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_xp (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    xp BIGINT NOT NULL DEFAULT 0,
                    level INTEGER NOT NULL DEFAULT 1,
                    total_xp BIGINT NOT NULL DEFAULT 0,
                    xp_boost_role TEXT,
                    last_xp_gain TIMESTAMPTZ
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_bank (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    total_deposited BIGINT NOT NULL DEFAULT 0,
                    total_withdrawn BIGINT NOT NULL DEFAULT 0,
                    total_fees_paid BIGINT NOT NULL DEFAULT 0,
                    last_fee_payment TIMESTAMPTZ,
                    daily_deposit BIGINT NOT NULL DEFAULT 0,
                    last_deposit_reset DATE
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS public_bank (
                    id SMALLINT PRIMARY KEY DEFAULT 1,
                    balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    total_deposited BIGINT NOT NULL DEFAULT 0,
                    total_withdrawn BIGINT NOT NULL DEFAULT 0,
                    last_activity TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                INSERT INTO public_bank (id)
                VALUES (1)
                ON CONFLICT (id) DO NOTHING
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS public_bank_withdrawals (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    amount BIGINT NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    remaining_balance BIGINT NOT NULL
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_defenses (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    active BOOLEAN NOT NULL DEFAULT FALSE,
                    purchased_at TIMESTAMPTZ
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_timeout_tokens (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
                    timeout_tokens INTEGER NOT NULL DEFAULT 0,
                    last_used TIMESTAMPTZ
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transaction_logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    amount BIGINT NOT NULL,
                    balance_before BIGINT NOT NULL,
                    balance_after BIGINT NOT NULL,
                    description TEXT,
                    related_user_id BIGINT,
                    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cooldowns (
                    user_id BIGINT NOT NULL,
                    command_type TEXT NOT NULL,
                    last_used TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (user_id, command_type)
                )
                """
            )

            # Indexes pour accélérer les requêtes usuelles
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_balance ON users(balance DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_transaction_user ON transaction_logs(user_id, timestamp DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_public_withdrawals_user ON public_bank_withdrawals(user_id, timestamp DESC)"
            )
            logger.info("Schéma PostgreSQL initialisé")

    # ------------------------------------------------------------------
    # Utilitaires généraux
    # ------------------------------------------------------------------
    async def ensure_user(self, user_id: int) -> None:
        """Crée l'utilisateur s'il n'existe pas."""

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id)
                VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
            )

    async def fetch_balance(self, user_id: int) -> int:
        """Retourne le solde actuel du portefeuille."""

        async with self.pool.acquire() as conn:
            balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1",
                user_id,
            )
            return balance or 0

    async def set_balance(self, user_id: int, new_balance: int) -> None:
        """Fixe le solde exact d'un utilisateur."""

        if new_balance < 0:
            raise ValueError("Le solde ne peut pas être négatif")

        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id, balance)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE
                SET balance = EXCLUDED.balance
                """,
                user_id,
                new_balance,
            )

    async def increment_balance(self, user_id: int, amount: int) -> Tuple[int, int]:
        """Incrémente le solde du portefeuille.

        Parameters
        ----------
        user_id:
            Identifiant Discord de l'utilisateur.
        amount:
            Montant à ajouter (peut être négatif).  Le solde final ne peut pas
            être inférieur à ``0``.
        """

        async with self.transaction() as conn:
            row = await conn.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )

            previous_balance = 0 if row is None else row[0]
            new_balance = max(previous_balance + amount, 0)

            if row is None:
                await conn.execute(
                    "INSERT INTO users (user_id, balance) VALUES ($1, $2)",
                    user_id,
                    new_balance,
                )
            else:
                await conn.execute(
                    "UPDATE users SET balance = $2 WHERE user_id = $1",
                    user_id,
                    new_balance,
                )

            return int(previous_balance), int(new_balance)

    async def transfer(self, sender_id: int, receiver_id: int, amount: int) -> Tuple[bool, int, int]:
        """Transfert brut d'un montant entre deux utilisateurs.

        Returns
        -------
        success: bool
            ``True`` si la transaction a réussi.
        sender_balance: int
            Solde du sender après transaction.
        receiver_balance: int
            Solde du receiver après transaction.
        """

        if amount <= 0:
            raise ValueError("Le montant doit être positif")

        async with self.transaction() as conn:
            sender_balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                sender_id,
            )
            if sender_balance is None or sender_balance < amount:
                return False, sender_balance or 0, await self.fetch_balance(receiver_id)

            receiver_balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                receiver_id,
            )

            await conn.execute(
                "UPDATE users SET balance = balance - $1 WHERE user_id = $2",
                amount,
                sender_id,
            )
            await conn.execute(
                """
                INSERT INTO users (user_id, balance)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET balance = users.balance + $2
                """,
                receiver_id,
                amount,
            )

            new_sender_balance = sender_balance - amount
            new_receiver_balance = (receiver_balance or 0) + amount
            return True, new_sender_balance, new_receiver_balance

    # ------------------------------------------------------------------
    # Daily & récompenses messages
    # ------------------------------------------------------------------
    async def get_last_daily(self, user_id: int) -> Optional[datetime]:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT last_daily FROM users WHERE user_id = $1",
                user_id,
            )
            return value

    async def set_last_daily(self, user_id: int, timestamp: datetime) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id, last_daily)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET last_daily = EXCLUDED.last_daily
                """,
                user_id,
                timestamp,
            )

    async def get_message_reward_cooldown(self, user_id: int) -> Optional[datetime]:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT message_cooldown FROM users WHERE user_id = $1",
                user_id,
            )
            return value

    async def set_message_reward_cooldown(self, user_id: int, timestamp: datetime) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id, message_cooldown)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET message_cooldown = EXCLUDED.message_cooldown
                """,
                user_id,
                timestamp,
            )

    # ------------------------------------------------------------------
    # Cooldowns génériques
    # ------------------------------------------------------------------
    async def get_cooldown(self, user_id: int, command_type: str) -> Optional[datetime]:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT last_used FROM cooldowns WHERE user_id = $1 AND command_type = $2",
                user_id,
                command_type,
            )

    async def set_cooldown(self, user_id: int, command_type: str, timestamp: Optional[datetime] = None) -> None:
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cooldowns (user_id, command_type, last_used)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, command_type)
                DO UPDATE SET last_used = EXCLUDED.last_used
                """,
                user_id,
                command_type,
                timestamp,
            )

    # ------------------------------------------------------------------
    # Gestion des transactions
    # ------------------------------------------------------------------
    async def log_transaction(
        self,
        user_id: int,
        transaction_type: str,
        amount: int,
        balance_before: int,
        balance_after: int,
        *,
        description: str = "",
        related_user_id: Optional[int] = None,
    ) -> None:
        """Ajoute une entrée dans ``transaction_logs``."""

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO transaction_logs (
                    user_id, transaction_type, amount, balance_before,
                    balance_after, description, related_user_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                user_id,
                transaction_type,
                amount,
                balance_before,
                balance_after,
                description,
                related_user_id,
            )

    async def fetch_transactions(
        self,
        user_id: int,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[TransactionLogEntry], int]:
        """Retourne l'historique paginé des transactions."""

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, transaction_type, amount, balance_before,
                       balance_after, description, related_user_id, timestamp
                FROM transaction_logs
                WHERE user_id = $1
                ORDER BY timestamp DESC
                LIMIT $2 OFFSET $3
                """,
                user_id,
                limit,
                offset,
            )
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM transaction_logs WHERE user_id = $1",
                user_id,
            )

        entries = [
            TransactionLogEntry(
                user_id=row[1],
                transaction_type=row[2],
                amount=row[3],
                balance_before=row[4],
                balance_after=row[5],
                description=row[6] or "",
                related_user_id=row[7],
                timestamp=row[8],
            )
            for row in rows
        ]
        return entries, int(total or 0)

    # ------------------------------------------------------------------
    # Gestion du shop
    # ------------------------------------------------------------------
    async def list_shop_items(
        self,
        *,
        page: int = 1,
        per_page: int = 5,
        active_only: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Renvoie les items du shop et le total."""

        offset = max(page - 1, 0) * per_page
        async with self.pool.acquire() as conn:
            base_query = "SELECT id, name, description, price, type, data, is_active FROM shop_items"
            if active_only:
                base_query += " WHERE is_active = TRUE"
            base_query += " ORDER BY price ASC LIMIT $1 OFFSET $2"
            rows = await conn.fetch(base_query, per_page, offset)

            count_query = "SELECT COUNT(*) FROM shop_items"
            if active_only:
                count_query += " WHERE is_active = TRUE"
            total = await conn.fetchval(count_query)

        items: List[Dict[str, Any]] = []
        for row in rows:
            data = row[5]
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    data = {}
            items.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "price": row[3],
                    "type": row[4],
                    "data": data,
                    "is_active": row[6],
                }
            )
        return items, int(total or 0)

    async def get_shop_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name, description, price, type, data, is_active FROM shop_items WHERE id = $1",
                item_id,
            )
        if not row:
            return None
        data = row[5]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}
        return {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "price": row[3],
            "type": row[4],
            "data": data,
            "is_active": row[6],
        }

    async def add_shop_item(
        self,
        name: str,
        description: str,
        price: int,
        item_type: str,
        data: Optional[Dict[str, Any]] = None,
        *,
        is_active: bool = True,
    ) -> int:
        if price <= 0:
            raise ValueError("Le prix doit être strictement positif")
        payload = json.dumps(data or {})
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO shop_items (name, description, price, type, data, is_active)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                RETURNING id
                """,
                name,
                description,
                price,
                item_type,
                payload,
                is_active,
            )
        return int(row[0])

    async def set_shop_item_active(self, item_id: int, active: bool) -> None:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE shop_items SET is_active = $2 WHERE id = $1",
                item_id,
                active,
            )
        if result == "UPDATE 0":
            raise DatabaseError("Item de boutique introuvable")

    async def record_purchase(
        self,
        user_id: int,
        item_id: int,
        *,
        price_paid: int,
        tax_paid: int,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_purchases (user_id, item_id, price_paid, tax_paid)
                VALUES ($1, $2, $3, $4)
                """,
                user_id,
                item_id,
                price_paid,
                tax_paid,
            )

    async def get_user_inventory(self, user_id: int) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT si.id, si.name, si.description, si.type, si.data,
                       up.purchase_date, up.price_paid
                FROM user_purchases up
                JOIN shop_items si ON si.id = up.item_id
                WHERE up.user_id = $1
                ORDER BY up.purchase_date DESC
                LIMIT 100
                """,
                user_id,
            )
        inventory: List[Dict[str, Any]] = []
        for row in rows:
            data = row[4]
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    data = {}
            inventory.append(
                {
                    "item_id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "type": row[3],
                    "data": data,
                    "purchase_date": row[5],
                    "price_paid": row[6],
                }
            )
        return inventory

    # ------------------------------------------------------------------
    # XP & niveaux
    # ------------------------------------------------------------------
    async def get_user_xp(self, user_id: int) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO user_xp (user_id)
                VALUES ($1)
                ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
                RETURNING xp, level, total_xp, xp_boost_role, last_xp_gain
                """,
                user_id,
            )
        return {
            "xp": row[0],
            "level": row[1],
            "total_xp": row[2],
            "xp_boost_role": row[3],
            "last_xp_gain": row[4],
        }

    async def add_user_xp(self, user_id: int, xp_amount: int, *, new_level: Optional[int] = None) -> Dict[str, Any]:
        async with self.transaction() as conn:
            row = await conn.fetchrow(
                "SELECT xp, level, total_xp FROM user_xp WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row is None:
                current_xp = total_xp = 0
                level = 1
                await conn.execute(
                    "INSERT INTO user_xp (user_id, xp, level, total_xp) VALUES ($1, 0, 1, 0)",
                    user_id,
                )
            else:
                current_xp, level, total_xp = row

            new_xp = current_xp + xp_amount
            new_total = total_xp + xp_amount
            if new_level is None:
                new_level = level

            await conn.execute(
                """
                UPDATE user_xp
                SET xp = $2, total_xp = $3, level = $4, last_xp_gain = NOW()
                WHERE user_id = $1
                """,
                user_id,
                new_xp,
                new_total,
                new_level,
            )

            return {
                "xp": new_xp,
                "previous_level": level,
                "new_level": new_level,
                "total_xp": new_total,
            }

    async def set_xp_boost_role(self, user_id: int, role_name: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_xp (user_id, xp_boost_role)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET xp_boost_role = EXCLUDED.xp_boost_role
                """,
                user_id,
                role_name,
            )

    async def get_xp_leaderboard(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, total_xp, level
                FROM user_xp
                ORDER BY total_xp DESC
                LIMIT $1
                """,
                limit,
            )
        return [
            {"user_id": row[0], "total_xp": row[1], "level": row[2]}
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Banque privée
    # ------------------------------------------------------------------
    async def get_private_bank_account(self, user_id: int) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO user_bank (user_id)
                VALUES ($1)
                ON CONFLICT (user_id) DO UPDATE SET user_id = EXCLUDED.user_id
                RETURNING balance, total_deposited, total_withdrawn, total_fees_paid,
                          last_fee_payment, daily_deposit, last_deposit_reset
                """,
                user_id,
            )
        return {
            "balance": row[0],
            "total_deposited": row[1],
            "total_withdrawn": row[2],
            "total_fees_paid": row[3],
            "last_fee_payment": row[4],
            "daily_deposit": row[5],
            "last_deposit_reset": row[6],
        }

    async def update_private_bank(
        self,
        user_id: int,
        *,
        delta_balance: int,
        deposit: int = 0,
        withdraw: int = 0,
        fees_paid: int = 0,
        reset_daily: bool = False,
    ) -> Dict[str, Any]:
        async with self.transaction() as conn:
            account = await conn.fetchrow(
                "SELECT balance, total_deposited, total_withdrawn, total_fees_paid, daily_deposit FROM user_bank WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if account is None:
                await conn.execute(
                    "INSERT INTO user_bank (user_id) VALUES ($1)",
                    user_id,
                )
                balance = total_deposited = total_withdrawn = total_fees = daily_deposit = 0
            else:
                balance, total_deposited, total_withdrawn, total_fees, daily_deposit = account

            new_balance = max(balance + delta_balance, 0)
            new_total_deposited = total_deposited + deposit
            new_total_withdrawn = total_withdrawn + withdraw
            new_total_fees = total_fees + fees_paid
            new_daily_deposit = 0 if reset_daily else (daily_deposit + deposit)

            await conn.execute(
                """
                UPDATE user_bank
                SET balance = $2,
                    total_deposited = $3,
                    total_withdrawn = $4,
                    total_fees_paid = $5,
                    daily_deposit = $6,
                    last_fee_payment = CASE WHEN $7 THEN NOW() ELSE last_fee_payment END,
                    last_deposit_reset = CASE WHEN $8 THEN CURRENT_DATE ELSE last_deposit_reset END
                WHERE user_id = $1
                """,
                user_id,
                new_balance,
                new_total_deposited,
                new_total_withdrawn,
                new_total_fees,
                new_daily_deposit,
                fees_paid > 0,
                reset_daily,
            )

            return {
                "balance": new_balance,
                "total_deposited": new_total_deposited,
                "total_withdrawn": new_total_withdrawn,
                "total_fees_paid": new_total_fees,
                "daily_deposit": new_daily_deposit,
            }

    # ------------------------------------------------------------------
    # Banque publique
    # ------------------------------------------------------------------
    async def add_public_bank_funds(self, amount: int) -> int:
        if amount <= 0:
            return await self.get_public_bank_balance()

        async with self.transaction() as conn:
            row = await conn.fetchrow(
                "SELECT balance, total_deposited FROM public_bank WHERE id = 1 FOR UPDATE"
            )
            balance, total_deposited = row
            balance += amount
            total_deposited += amount
            await conn.execute(
                """
                UPDATE public_bank
                SET balance = $1, total_deposited = $2, last_activity = NOW()
                WHERE id = 1
                """,
                balance,
                total_deposited,
            )
            return balance

    async def withdraw_public_bank(self, amount: int) -> Tuple[bool, int]:
        async with self.transaction() as conn:
            row = await conn.fetchrow(
                "SELECT balance, total_withdrawn FROM public_bank WHERE id = 1 FOR UPDATE"
            )
            balance, total_withdrawn = row
            if balance < amount:
                return False, balance

            balance -= amount
            total_withdrawn += amount
            await conn.execute(
                """
                UPDATE public_bank
                SET balance = $1, total_withdrawn = $2, last_activity = NOW()
                WHERE id = 1
                """,
                balance,
                total_withdrawn,
            )
            return True, balance

    async def record_public_withdrawal(self, user_id: int, amount: int, remaining_balance: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public_bank_withdrawals (user_id, amount, remaining_balance)
                VALUES ($1, $2, $3)
                """,
                user_id,
                amount,
                remaining_balance,
            )

    async def get_public_bank_balance(self) -> int:
        async with self.pool.acquire() as conn:
            balance = await conn.fetchval("SELECT balance FROM public_bank WHERE id = 1")
        return balance or 0

    async def get_public_bank_stats(self) -> Dict[str, Any]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT balance, total_deposited, total_withdrawn, last_activity FROM public_bank WHERE id = 1"
            )
        if row is None:
            return {"balance": 0, "total_deposited": 0, "total_withdrawn": 0, "last_activity": None}
        return {
            "balance": row[0],
            "total_deposited": row[1],
            "total_withdrawn": row[2],
            "last_activity": row[3],
        }

    async def get_public_withdrawals_for_day(self, user_id: int, *, day: datetime) -> int:
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM public_bank_withdrawals
                WHERE user_id = $1 AND timestamp >= $2 AND timestamp < $3
                """,
                user_id,
                start,
                end,
            )
        return value or 0

    # ------------------------------------------------------------------
    # Défense et tokens timeout
    # ------------------------------------------------------------------
    async def set_defense_status(self, user_id: int, active: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_defenses (user_id, active, purchased_at)
                VALUES ($1, $2, CASE WHEN $2 THEN NOW() ELSE purchased_at END)
                ON CONFLICT (user_id) DO UPDATE
                SET active = EXCLUDED.active,
                    purchased_at = CASE WHEN EXCLUDED.active THEN NOW() ELSE user_defenses.purchased_at END
                """,
                user_id,
                active,
            )

    async def has_defense(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT active FROM user_defenses WHERE user_id = $1",
                user_id,
            )
        return bool(value)

    async def add_timeout_tokens(self, user_id: int, amount: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_timeout_tokens (user_id, timeout_tokens)
                VALUES ($1, $2)
                ON CONFLICT (user_id) DO UPDATE SET timeout_tokens = user_timeout_tokens.timeout_tokens + $2
                """,
                user_id,
                amount,
            )

    async def consume_timeout_token(self, user_id: int) -> bool:
        async with self.transaction() as conn:
            row = await conn.fetchrow(
                "SELECT timeout_tokens FROM user_timeout_tokens WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row is None or row[0] <= 0:
                return False
            await conn.execute(
                "UPDATE user_timeout_tokens SET timeout_tokens = timeout_tokens - 1, last_used = NOW() WHERE user_id = $1",
                user_id,
            )
            return True

    async def get_timeout_tokens(self, user_id: int) -> int:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT timeout_tokens FROM user_timeout_tokens WHERE user_id = $1",
                user_id,
            )
        return value or 0

    # ------------------------------------------------------------------
    # Leaderboards & statistiques
    # ------------------------------------------------------------------
    async def get_balance_leaderboard(self, *, limit: int = 20, ascending: bool = False) -> List[Dict[str, Any]]:
        order = "ASC" if ascending else "DESC"
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT user_id, balance FROM users ORDER BY balance {order} LIMIT $1",
                limit,
            )
        return [
            {"user_id": row[0], "balance": row[1]}
            for row in rows
        ]

    async def get_total_wealth(self) -> int:
        async with self.pool.acquire() as conn:
            value = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        return value or 0

    # ------------------------------------------------------------------
    # Outils casino / divers
    # ------------------------------------------------------------------
    async def adjust_balance_with_public_bank(
        self,
        *,
        user_id: int,
        amount: int,
        public_bank_delta: int = 0,
        transaction_type: str,
        description: str,
        related_user_id: Optional[int] = None,
    ) -> Tuple[int, int]:
        """Met à jour le solde utilisateur et celui de la banque publique dans une seule transaction."""

        async with self.transaction() as conn:
            balance_before = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if balance_before is None:
                balance_before = 0
                await conn.execute(
                    "INSERT INTO users (user_id, balance) VALUES ($1, 0) ON CONFLICT (user_id) DO NOTHING",
                    user_id,
                )

            new_balance = max(balance_before + amount, 0)
            await conn.execute(
                "UPDATE users SET balance = $2 WHERE user_id = $1",
                user_id,
                new_balance,
            )

            if public_bank_delta != 0:
                row = await conn.fetchrow(
                    "SELECT balance, total_deposited, total_withdrawn FROM public_bank WHERE id = 1 FOR UPDATE"
                )
                balance, deposited, withdrawn = row
                balance += public_bank_delta
                if public_bank_delta > 0:
                    deposited += public_bank_delta
                else:
                    withdrawn += abs(public_bank_delta)
                await conn.execute(
                    """
                    UPDATE public_bank
                    SET balance = $1,
                        total_deposited = $2,
                        total_withdrawn = $3,
                        last_activity = NOW()
                    WHERE id = 1
                    """,
                    balance,
                    deposited,
                    withdrawn,
                )

            await conn.execute(
                """
                INSERT INTO transaction_logs (
                    user_id, transaction_type, amount, balance_before,
                    balance_after, description, related_user_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                user_id,
                transaction_type,
                amount,
                balance_before,
                new_balance,
                description,
                related_user_id,
            )

            return balance_before, new_balance

    # ------------------------------------------------------------------
    # Fonctions d'administration
    # ------------------------------------------------------------------
    async def wipe_economy(self) -> None:
        """Réinitialise l'économie (sauf shop)."""

        async with self.transaction() as conn:
            await conn.execute("TRUNCATE TABLE users CASCADE")
            await conn.execute("TRUNCATE TABLE user_xp CASCADE")
            await conn.execute("TRUNCATE TABLE user_bank CASCADE")
            await conn.execute("TRUNCATE TABLE user_defenses CASCADE")
            await conn.execute("TRUNCATE TABLE user_timeout_tokens CASCADE")
            await conn.execute("TRUNCATE TABLE transaction_logs")
            await conn.execute("TRUNCATE TABLE cooldowns")
            await conn.execute("TRUNCATE TABLE public_bank_withdrawals")
            await conn.execute(
                "UPDATE public_bank SET balance = 0, total_deposited = 0, total_withdrawn = 0, last_activity = NOW() WHERE id = 1"
            )
        logger.warning("L'économie a été réinitialisée par un administrateur")

    # ------------------------------------------------------------------
    # Helpers diverses
    # ------------------------------------------------------------------
    async def fetch_value(self, query: str, *args: Any) -> Any:
        """Helper pour exécuter rapidement une requête ``SELECT`` retournant une valeur."""

        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        """Helper pour exécuter ``execute`` en centralisant la gestion des erreurs."""

        try:
            async with self.pool.acquire() as conn:
                return await conn.execute(query, *args)
        except Exception as exc:  # pragma: no cover - logging only
            logger.exception("Erreur d'exécution SQL")
            raise DatabaseError("Erreur SQL") from exc
