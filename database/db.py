"""Couche d'accès aux données minimaliste pour EcoBot."""
from __future__ import annotations

import logging
import math
import asyncio
import os
import random
import sys
import statistics
import time
from collections import defaultdict
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import asyncpg

from config import (
    BASE_PET_SLOTS,
    PET_SLOT_MAX_CAPACITY,
    CLAN_BASE_CAPACITY,
    CLAN_MAX_MEMBERS,
    CLAN_LEVEL_BASE_COST,
    CLAN_LEVEL_COST_GROWTH,
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
    GEMS_REBASE_FACTOR,
    HUGE_PET_NAMES,
    HUGE_BO_NAME,
    MARKET_VALUE_CONFIG,
    PET_DEFINITIONS,
    PET_EGG_DEFINITIONS,
    CACHE_MAX_ENTRIES,
    CACHE_TTL_SECONDS,
    DEBUG_SQL_TIMING,
    PET_FARM_GEM_MAX,
    PET_FARM_GEM_PER_PET_HOUR,
    PET_FARM_GEM_VARIANCE_PER_PET,
    DAYCARE_GEM_MAX,
    DAYCARE_GEM_PER_PET_HOUR,
    DAYCARE_MAX_PETS,
    PET_FARM_POTION_BASE,
    PET_FARM_POTION_MAX_CHANCE,
    PET_FARM_POTION_PER_PET,
    PET_FARM_TIME_FACTOR_MAX,
    PET_FARM_TIME_FACTOR_MIN,
    PET_VALUE_SCALE,
    RAINBOW_PET_COMBINE_REQUIRED,
    RAINBOW_PET_MULTIPLIER,
    SHINY_PET_MULTIPLIER,
    TITANIC_GRIFF_NAME,
    POTION_DEFINITION_MAP,
    PotionDefinition,
    compute_huge_income,
    get_huge_level_multiplier,
    scale_pet_value,
    huge_level_required_xp,
    QUERY_TIMEOUT_SECONDS,
)
from utils.mastery import get_mastery_definition
from utils.localization import DEFAULT_LANGUAGE, normalize_language
from utils.cache import LruTTLCache

__all__ = [
    "Database",
    "DatabaseError",
    "InsufficientBalanceError",
    "ActivePetLimitError",
]

logger = logging.getLogger(__name__)

_HUGE_PET_NAME_LOOKUP = {name.lower() for name in HUGE_PET_NAMES}
_MARKET_HISTORY_SAMPLE = 20
_MARKET_BASE_MULTIPLIER = 80
_MARKET_MIN_MULTIPLIER = 0.6
_MARKET_MAX_MULTIPLIER = 2.5
_MARKET_MAX_VALUE = 100_000_000
_MARKET_RARITY_BASE = {
    "Commun": 3,
    "Atypique": 5,
    "Rare": 8,
    "Épique": 15,
    "Légendaire": 40,
    "Mythique": 80,
    "Secret": 150,
}
_MARKET_ZONE_MULTIPLIERS = {
    "starter": 0.02,
    "foret": 0.05,
    "manoir_hante": 0.2,
    "robotique": 0.6,
    "animalerie": 2.5,
    "mexico": 6.0,
    "celeste": 15.0,
    "exclusif": 1.0,
}
_MARKET_VARIANTS: tuple[tuple[str, float], ...] = (
    ("normal", 1.0),
    ("gold", 3.0),
    ("rainbow", 10.0),
    ("galaxy", 25.0),
    ("normal+shiny", 5.0),
    ("gold+shiny", 15.0),
    ("rainbow+shiny", 50.0),
    ("galaxy+shiny", 125.0),
)
_MARKET_VARIANT_MULTIPLIERS = {code: multiplier for code, multiplier in _MARKET_VARIANTS}
_PET_ZONE_BY_NAME = {
    pet.name.lower(): egg.zone_slug
    for egg in PET_EGG_DEFINITIONS
    for pet in egg.pets
}


@dataclass(frozen=True)
class _BoosterComputation:
    extra_income: int = 0
    overlap_seconds: float = 0.0
    remaining_seconds: float = 0.0

    def consumed_seconds(self, elapsed_seconds: float) -> int:
        if self.extra_income <= 0:
            return 0
        return int(min(elapsed_seconds, self.overlap_seconds))


@dataclass(frozen=True)
class _BoosterState:
    multiplier: float
    activated_at: datetime | None
    expires_at: datetime | None

    def evaluate(
        self,
        *,
        now: datetime,
        last_claim: datetime | None,
        hourly_income: float,
    ) -> _BoosterComputation:
        remaining = 0.0
        if isinstance(self.expires_at, datetime):
            remaining = max(0.0, (self.expires_at - now).total_seconds())

        if self.multiplier <= 1 or not isinstance(self.expires_at, datetime):
            return _BoosterComputation(remaining_seconds=remaining)

        if not isinstance(self.activated_at, datetime) or last_claim is None:
            return _BoosterComputation(remaining_seconds=remaining)

        overlap_start = max(last_claim, self.activated_at)
        overlap_end = min(now, self.expires_at)
        if overlap_end <= overlap_start:
            return _BoosterComputation(remaining_seconds=remaining)

        overlap_seconds = (overlap_end - overlap_start).total_seconds()
        if hourly_income <= 0:
            return _BoosterComputation(
                overlap_seconds=overlap_seconds, remaining_seconds=remaining
            )

        booster_hours = overlap_seconds / 3600
        extra = int(hourly_income * booster_hours * (self.multiplier - 1))
        if extra <= 0:
            return _BoosterComputation(
                overlap_seconds=overlap_seconds, remaining_seconds=remaining
            )
        return _BoosterComputation(
            extra_income=extra,
            overlap_seconds=overlap_seconds,
            remaining_seconds=remaining,
        )


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

    _INSTANCE_LOCK_KEY = (0x45534F42, 0x4F54504C)  # "ESOB"/"OTPL" packed into int32 pairs
    _LOCK_WAIT_SECONDS = max(0, int(os.getenv("DB_LOCK_WAIT", "25")))
    _LOCK_FORCE_TAKEOVER = os.getenv("DB_LOCK_FORCE", "1").lower() not in {"0", "false", "no"}

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        if not dsn:
            raise ValueError("Le DSN PostgreSQL est obligatoire")

        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None
        self._min_size = min_size
        self._max_size = max_size
        self._lock_connection: asyncpg.Connection | None = None
        self._leaderboard_cache: LruTTLCache[object] = LruTTLCache(
            CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES
        )
        self._analytics_cache: LruTTLCache[object] = LruTTLCache(
            CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES
        )
        self._market_values_ready = False
        self._market_values_cache: LruTTLCache[object] = LruTTLCache(
            CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES
        )
        self._MARKET_VALUES_CACHE_KEY = "pet_market_values"

    async def _fetch(
        self, query: str, *args: object, timeout: float | None = QUERY_TIMEOUT_SECONDS
    ) -> Sequence[asyncpg.Record]:
        start = time.monotonic()
        rows = await self.pool.fetch(query, *args, timeout=timeout)
        if DEBUG_SQL_TIMING:
            logger.info("SQL fetch in %.3fs", time.monotonic() - start)
        return rows

    async def _fetchrow(
        self, query: str, *args: object, timeout: float | None = QUERY_TIMEOUT_SECONDS
    ) -> asyncpg.Record | None:
        start = time.monotonic()
        row = await self.pool.fetchrow(query, *args, timeout=timeout)
        if DEBUG_SQL_TIMING:
            logger.info("SQL fetchrow in %.3fs", time.monotonic() - start)
        return row

    async def _fetchval(
        self, query: str, *args: object, timeout: float | None = QUERY_TIMEOUT_SECONDS
    ) -> object:
        start = time.monotonic()
        value = await self.pool.fetchval(query, *args, timeout=timeout)
        if DEBUG_SQL_TIMING:
            logger.info("SQL fetchval in %.3fs", time.monotonic() - start)
        return value

    async def _ensure_market_values_ready(self) -> None:
        if self._market_values_ready:
            return
        count = await self._fetchval("SELECT COUNT(*) FROM pet_market_values")
        if int(count or 0) <= 0:
            await self.sync_pet_market_values()
        self._market_values_ready = True

    @staticmethod
    def _rebirth_multiplier(count: int) -> float:
        base = 1.0 + 0.5 * max(0, int(count))
        return min(1.5, base)

    @classmethod
    def _apply_rebirth_multiplier(cls, amount: int, count: int) -> tuple[int, int]:
        if amount <= 0 or count <= 0:
            return amount, 0
        multiplier = cls._rebirth_multiplier(count)
        adjusted = int(round(amount * multiplier))
        adjusted = max(amount, adjusted)
        return adjusted, max(0, adjusted - amount)

    @staticmethod
    def _compute_pet_slot_limit(grade_level: int, extra_slots: int) -> int:
        grade = max(0, int(grade_level))
        extra = max(0, int(extra_slots))
        return max(0, min(PET_SLOT_MAX_CAPACITY, BASE_PET_SLOTS + grade + extra))

    @staticmethod
    def _build_empty_claim_result(
        rows: Sequence[asyncpg.Record], elapsed_seconds: float
    ) -> tuple[
        int,
        Sequence[asyncpg.Record],
        float,
        dict[str, float],
        dict[str, object],
        Dict[int, tuple[int, int]],
        dict[str, object],
        dict[str, object],
        dict[str, object],
        dict[str, float | int],
    ]:
        return (
            0,
            rows,
            float(elapsed_seconds),
            {},
            {},
            {},
            {},
            {},
            {},
            {
                "count": 0,
                "bonus": 0,
                "multiplier": 1.0,
            },
        )

    @staticmethod
    def _compute_pet_income(
        row: asyncpg.Record, best_non_huge_income: int
    ) -> int:
        base_income = int(row["base_income_per_hour"])
        if bool(row["is_huge"]):
            name = str(row.get("name", ""))
            level = int(row.get("huge_level") or 1)
            multiplier = get_huge_level_multiplier(name, level)
            reference_income = (
                best_non_huge_income if best_non_huge_income > 0 else base_income
            )
            raw_income = compute_huge_income(reference_income, multiplier)
            income_value = raw_income
            if bool(row.get("is_galaxy")):
                income_value *= GALAXY_PET_MULTIPLIER
            elif bool(row.get("is_rainbow")):
                income_value *= RAINBOW_PET_MULTIPLIER
            elif bool(row["is_gold"]):
                income_value *= GOLD_PET_MULTIPLIER
            if bool(row.get("is_shiny")):
                income_value *= SHINY_PET_MULTIPLIER
            return scale_pet_value(income_value)

        income_value = base_income
        if bool(row.get("is_galaxy")):
            income_value *= GALAXY_PET_MULTIPLIER
        elif bool(row.get("is_rainbow")):
            income_value *= RAINBOW_PET_MULTIPLIER
        elif bool(row["is_gold"]):
            income_value *= GOLD_PET_MULTIPLIER
        if bool(row.get("is_shiny")):
            income_value *= SHINY_PET_MULTIPLIER
        return scale_pet_value(income_value)

    @staticmethod
    def _calculate_income_shares(
        rows: Sequence[asyncpg.Record],
        effective_incomes: Sequence[int],
        hourly_income: int,
        total_income: int,
    ) -> List[int]:
        if total_income <= 0 or hourly_income <= 0 or not rows:
            return [0 for _ in rows]

        shares: List[int] = [0 for _ in rows]
        remaining_income = total_income
        for index, effective in enumerate(effective_incomes):
            if index == len(rows) - 1:
                share_amount = remaining_income
            else:
                proportion = effective / hourly_income if hourly_income else 0.0
                share_amount = int(round(total_income * proportion))
                share_amount = max(0, min(remaining_income, share_amount))
                remaining_income -= share_amount
            shares[index] = share_amount
        return shares

    def _calculate_huge_progress(
        self,
        rows: Sequence[asyncpg.Record],
        total_income: int,
        effective_incomes: Sequence[int],
        hourly_income: int,
        elapsed_hours: float,
    ) -> Dict[int, tuple[int, int]]:
        if total_income <= 0 or hourly_income <= 0 or not rows:
            return {}
        shares = self._calculate_income_shares(
            rows, effective_incomes, hourly_income, total_income
        )
        progress_updates: Dict[int, tuple[int, int]] = {}
        time_weight = max(1.0, float(elapsed_hours))
        for share_amount, row in zip(shares, rows):
            if not bool(row.get("is_huge")):
                continue
            base_xp_gain = max(share_amount / 1_000, 1.0)
            xp_gain = int(round(base_xp_gain * time_weight))
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
        return progress_updates

    @staticmethod
    def _evaluate_potion_state(
        slug: object,
        expires_at: object,
        now: datetime,
    ) -> tuple[float, PotionDefinition | None, float, bool]:
        potion_multiplier = 1.0
        potion_definition: PotionDefinition | None = None
        potion_remaining = 0.0
        potion_should_clear = False

        if slug:
            potion_definition = POTION_DEFINITION_MAP.get(str(slug))
            if isinstance(expires_at, datetime) and potion_definition is not None:
                if expires_at > now:
                    potion_remaining = (expires_at - now).total_seconds()
                    if potion_definition.effect_type == "pb_boost":
                        potion_multiplier += float(potion_definition.effect_value)
                else:
                    potion_should_clear = True
            else:
                potion_should_clear = True

        return potion_multiplier, potion_definition, potion_remaining, potion_should_clear

    @classmethod
    def _build_rebirth_info(cls, count: int, bonus: int) -> dict[str, float]:
        return {
            "count": count,
            "bonus": max(0, bonus),
            "multiplier": cls._rebirth_multiplier(count),
        }

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
        try:
            await self._acquire_instance_lock()
        except Exception:
            await self.close()
            raise

    async def close(self) -> None:
        if self._pool is not None:
            await self._release_instance_lock()
            await self._pool.close()
            self._pool = None
            logger.info("Pool PostgreSQL fermé")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                yield connection

    @staticmethod
    def _coerce_positive_ids(values: Sequence[object], *, field: str = "identifiants") -> list[int]:
        normalized: list[int] = []
        for raw in values:
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise DatabaseError(f"Les {field} fournis sont invalides.") from exc
            if value <= 0:
                raise DatabaseError(f"Les {field} fournis doivent être strictement positifs.")
            normalized.append(value)
        return normalized

    async def _acquire_instance_lock(self) -> None:
        """Acquiert le verrou d'instance avec option de force."""
        if self._lock_connection is not None:
            return

        connection = await self.pool.acquire()
        key_class, key_object = self._INSTANCE_LOCK_KEY
        try:
            async def _current_lock_pid() -> Optional[int]:
                return await connection.fetchval(
                    """
                    SELECT pid
                    FROM pg_locks
                    WHERE locktype = 'advisory'
                      AND classid = $1::integer
                      AND objid = $2::integer
                    LIMIT 1
                    """,
                    key_class,
                    key_object,
                )

            existing_lock = await _current_lock_pid()

            if existing_lock is not None:
                process_exists = await connection.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM pg_stat_activity WHERE pid = $1
                    )
                    """,
                    int(existing_lock),
                )

                if not process_exists:
                    logger.warning(
                        "Verrou orphelin détecté (PID %s mort), libération forcée...",
                        existing_lock,
                    )
                    await connection.execute(
                        "SELECT pg_advisory_unlock($1::integer, $2::integer)",
                        key_class,
                        key_object,
                    )
                    existing_lock = None

            locked = await connection.fetchval(
                "SELECT pg_try_advisory_lock($1::integer, $2::integer)",
                key_class,
                key_object,
            )

            wait_seconds = self._LOCK_WAIT_SECONDS
            while not locked and wait_seconds > 0:
                await asyncio.sleep(1)
                wait_seconds -= 1
                locked = await connection.fetchval(
                    "SELECT pg_try_advisory_lock($1::integer, $2::integer)",
                    key_class,
                    key_object,
                )

            if (not locked and existing_lock is not None and self._LOCK_FORCE_TAKEOVER):
                logger.warning(
                    "Instance précédente toujours active (PID %s), tentative de prise de contrôle...",
                    existing_lock,
                )
                with suppress(Exception):
                    await connection.execute(
                        "SELECT pg_terminate_backend($1::integer)",
                        int(existing_lock),
                    )
                await asyncio.sleep(2)
                locked = await connection.fetchval(
                    "SELECT pg_try_advisory_lock($1::integer, $2::integer)",
                    key_class,
                    key_object,
                )
        except Exception as exc:
            await self.pool.release(connection)
            logger.exception("Impossible de récupérer le verrou d'instance")
            raise DatabaseError("Vérification d'instance échouée") from exc

        if not locked:
            await self.pool.release(connection)
            logger.error(
                "Une autre instance du bot est en cours d'exécution. "
                "Si vous êtes certain qu'aucune autre instance ne tourne, "
                "connectez-vous à PostgreSQL et exécutez : SELECT pg_advisory_unlock_all();"
            )
            raise DatabaseError(
                "Une autre instance du bot est déjà en cours d'exécution."
            )

        self._lock_connection = connection
        logger.info(
            "Verrou d'instance PostgreSQL acquis (PID: %s)",
            await connection.fetchval("SELECT pg_backend_pid()"),
        )

    async def _release_instance_lock(self) -> None:
        """Libère le verrou d'instance de manière robuste."""
        if self._lock_connection is None:
            return

        try:
            # Vérifier que la connexion est toujours active
            if not self._lock_connection.is_closed():
                try:
                    await self._lock_connection.execute(
                        "SELECT pg_advisory_unlock($1::integer, $2::integer)",
                        *self._INSTANCE_LOCK_KEY,
                    )
                    logger.info("Verrou d'instance PostgreSQL libéré")
                except Exception:
                    logger.exception("Erreur lors de la libération du verrou")
                    # En cas d'échec, forcer la libération
                    try:
                        await self._lock_connection.execute(
                            "SELECT pg_advisory_unlock_all()"
                        )
                        logger.warning("Libération forcée de tous les verrous advisory")
                    except Exception:
                        logger.exception("Impossible de forcer la libération des verrous")
        finally:
            try:
                await self.pool.release(self._lock_connection)
            except Exception:
                logger.exception("Erreur lors de la libération de la connexion")
            self._lock_connection = None

    async def _ensure_transactions_table(
        self, executor: asyncpg.Connection | asyncpg.Pool
    ) -> None:
        await executor.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                transaction_type VARCHAR(50) NOT NULL,
                currency TEXT NOT NULL DEFAULT 'pb',
                amount BIGINT NOT NULL,
                balance_before BIGINT NOT NULL,
                balance_after BIGINT NOT NULL,
                description TEXT,
                related_user_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await executor.execute(
            "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'pb'"
        )
        await executor.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)"
        )
        await executor.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(transaction_type)"
        )
        await executor.execute(
            "CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(created_at)"
        )

    async def _get_config_flag_in_connection(
        self, connection: asyncpg.Connection, flag_name: str
    ) -> bool:
        row = await connection.fetchrow(
            "SELECT value FROM config_flags WHERE flag_name = $1",
            flag_name,
        )
        return bool(row["value"]) if row else False

    async def _set_config_flag_in_connection(
        self, connection: asyncpg.Connection, flag_name: str, value: bool
    ) -> None:
        await connection.execute(
            """
            INSERT INTO config_flags (flag_name, value, updated_at)
            VALUES ($1, $2, CURRENT_TIMESTAMP)
            ON CONFLICT (flag_name)
            DO UPDATE SET value = $2, updated_at = CURRENT_TIMESTAMP
            """,
            flag_name,
            value,
        )

    async def _apply_power_unnerf_migration(
        self, connection: asyncpg.Connection
    ) -> None:
        flag_name = "economy_power_unnerf_v1"
        if await self._get_config_flag_in_connection(connection, flag_name):
            return
        row = await connection.fetchrow(
            "SELECT COUNT(*) AS total, MAX(base_income_per_hour) AS max_income FROM pets"
        )
        if row is None:
            return
        total = int(row["total"] or 0)
        if total <= 0:
            return
        max_income = int(row["max_income"] or 0)
        if max_income <= 0:
            return
        max_config_income = max(
            pet.base_income_per_hour for pet in PET_DEFINITIONS
        )
        ratio = max_config_income / max_income if max_income > 0 else 0
        if ratio >= 100:
            await connection.execute(
                """
                UPDATE pets
                SET base_income_per_hour = base_income_per_hour * 1000
                WHERE base_income_per_hour > 0
                """
            )
        await self._set_config_flag_in_connection(connection, flag_name, True)

    async def _apply_gems_rebase_migration(
        self, connection: asyncpg.Connection
    ) -> None:
        if GEMS_REBASE_FACTOR <= 1:
            return
        flag_name = f"economy_gems_rebase_v1_{GEMS_REBASE_FACTOR}"
        if await self._get_config_flag_in_connection(connection, flag_name):
            return
        factor = int(GEMS_REBASE_FACTOR)
        await connection.execute(
            f"""
            UPDATE users
            SET gems = GREATEST(0, CAST(FLOOR(gems::numeric / {factor}) AS BIGINT))
            """
        )
        await connection.execute(
            f"""
            UPDATE pet_market_values
            SET value_in_gems = GREATEST(0, CAST(FLOOR(value_in_gems::numeric / {factor}) AS BIGINT))
            """
        )
        self._invalidate_market_values_cache()
        await connection.execute(
            f"""
            UPDATE pet_trade_history
            SET price = GREATEST(0, CAST(FLOOR(price::numeric / {factor}) AS BIGINT))
            """
        )
        await connection.execute(
            f"""
            UPDATE market_listings
            SET price = GREATEST(0, CAST(FLOOR(price::numeric / {factor}) AS BIGINT))
            """
        )
        await connection.execute(
            f"""
            UPDATE plaza_consumable_listings
            SET price = GREATEST(0, CAST(FLOOR(price::numeric / {factor}) AS BIGINT))
            """
        )
        await connection.execute(
            f"""
            UPDATE transactions
            SET amount = CASE
                    WHEN amount < 0 THEN -CAST(FLOOR(ABS(amount)::numeric / {factor}) AS BIGINT)
                    ELSE CAST(FLOOR(amount::numeric / {factor}) AS BIGINT)
                END,
                balance_before = GREATEST(0, CAST(FLOOR(balance_before::numeric / {factor}) AS BIGINT)),
                balance_after = GREATEST(0, CAST(FLOOR(balance_after::numeric / {factor}) AS BIGINT))
            WHERE currency = 'gem'
            """
        )
        await self._set_config_flag_in_connection(connection, flag_name, True)

    async def _apply_economy_migrations(self, connection: asyncpg.Connection) -> None:
        await self._apply_power_unnerf_migration(connection)
        await self._apply_gems_rebase_migration(connection)

    async def _initialise_schema(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0),
                    gems BIGINT NOT NULL DEFAULT 0 CHECK (gems >= 0),
                    last_daily TIMESTAMPTZ,
                    daily_streak INTEGER NOT NULL DEFAULT 0 CHECK (daily_streak >= 0),
                    mastermind_winstreak INTEGER NOT NULL DEFAULT 0 CHECK (mastermind_winstreak >= 0),
                    mastermind_best_winstreak INTEGER NOT NULL DEFAULT 0 CHECK (mastermind_best_winstreak >= 0),
                    mastermind_wins INTEGER NOT NULL DEFAULT 0 CHECK (mastermind_wins >= 0),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    pet_last_claim TIMESTAMPTZ
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_balance_desc ON users(balance DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_gems_desc ON users(gems DESC)"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS gems BIGINT NOT NULL DEFAULT 0"
                " CHECK (gems >= 0)"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS pet_last_claim TIMESTAMPTZ"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS daily_streak INTEGER NOT NULL DEFAULT 0"
                " CHECK (daily_streak >= 0)"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS mastermind_winstreak INTEGER NOT NULL DEFAULT 0"
                " CHECK (mastermind_winstreak >= 0)"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS mastermind_best_winstreak INTEGER NOT NULL DEFAULT 0"
                " CHECK (mastermind_best_winstreak >= 0)"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS mastermind_wins INTEGER NOT NULL DEFAULT 0"
                " CHECK (mastermind_wins >= 0)"
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
                ALTER TABLE users ADD COLUMN IF NOT EXISTS extra_pet_slots
                INTEGER NOT NULL DEFAULT 0 CHECK (extra_pet_slots >= 0)
                """
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS mexico_dispenser_last_claim TIMESTAMPTZ"
            )
            await connection.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS daycare_last_claim TIMESTAMPTZ"
            )
            await connection.execute(
                """
                ALTER TABLE users ADD COLUMN IF NOT EXISTS race_best_stage
                INTEGER NOT NULL DEFAULT 0 CHECK (race_best_stage >= 0)
                """
            )
            await connection.execute(
                """
                ALTER TABLE users ADD COLUMN IF NOT EXISTS rebirth_count
                INTEGER NOT NULL DEFAULT 0 CHECK (rebirth_count >= 0)
                """
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
                    base_income_per_hour BIGINT NOT NULL CHECK (base_income_per_hour >= 0),
                    drop_rate DOUBLE PRECISION NOT NULL CHECK (drop_rate >= 0)
                )
                """
            )
            await connection.execute(
                "ALTER TABLE pets ALTER COLUMN base_income_per_hour TYPE BIGINT"
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
                """
                CREATE TABLE IF NOT EXISTS user_daycare (
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    user_pet_id INTEGER NOT NULL REFERENCES user_pets(id) ON DELETE CASCADE,
                    deposited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, user_pet_id)
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_daycare_user ON user_daycare(user_id)"
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

            await self._ensure_transactions_table(connection)

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS gemshop_roles (
                    role_id BIGINT PRIMARY KEY,
                    sold INTEGER NOT NULL DEFAULT 0
                )
                """
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
                CREATE TABLE IF NOT EXISTS pet_market_values (
                    pet_id INTEGER NOT NULL REFERENCES pets(pet_id) ON DELETE CASCADE,
                    variant_code TEXT NOT NULL,
                    value_in_gems BIGINT NOT NULL CHECK (value_in_gems >= 0),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (pet_id, variant_code)
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS config_flags (
                    flag_name TEXT PRIMARY KEY,
                    value BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_config_flags_name ON config_flags(flag_name)"
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
                    item_type TEXT NOT NULL CHECK (item_type IN ('ticket', 'potion', 'role')),
                    item_slug TEXT,
                    item_power SMALLINT,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    price BIGINT NOT NULL CHECK (price >= 0),
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
                """
            )
            await connection.execute(
                """
                ALTER TABLE plaza_consumable_listings
                DROP CONSTRAINT IF EXISTS plaza_consumable_listings_item_type_check
                """
            )
            await connection.execute(
                "ALTER TABLE plaza_consumable_listings ADD COLUMN IF NOT EXISTS item_power SMALLINT"
            )
            await connection.execute(
                """
                ALTER TABLE plaza_consumable_listings
                ADD CONSTRAINT plaza_consumable_listings_item_type_check
                CHECK (item_type IN ('ticket', 'potion', 'role'))
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plaza_consumable_status ON plaza_consumable_listings(status)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plaza_consumable_seller ON plaza_consumable_listings(seller_id)"
            )

        if connection is not None:
            await connection.execute(query, *params)
            return

        async with self.transaction() as txn_connection:
            await txn_connection.execute(query, *params)

    async def _get_trade_history_market_values(self) -> Dict[Tuple[int, str], int]:
        query = """
            SELECT
                h.pet_id,
                h.is_gold,
                h.is_rainbow,
                h.is_galaxy,
                h.is_shiny,
                h.price,
                p.base_income_per_hour,
                p.name,
                p.rarity
            FROM pet_trade_history AS h
            JOIN pets AS p ON p.pet_id = h.pet_id
            ORDER BY h.recorded_at DESC, h.id DESC
        """

        prices_by_key: Dict[Tuple[int, str], list[int]] = defaultdict(list)
        base_income_by_pet: Dict[int, int] = {}
        name_by_pet: Dict[int, str] = {}
        rarity_by_pet: Dict[int, str] = {}
        owner_counts: Dict[int, int] = {}
        async with self.transaction() as connection:
            owner_rows = await connection.fetch(
                "SELECT pet_id, COUNT(DISTINCT user_id) AS owners FROM user_pets GROUP BY pet_id"
            )
            owner_counts = {
                int(row["pet_id"]): int(row.get("owners") or 0) for row in owner_rows
            }
            async for row in connection.cursor(query):
                pet_id = int(row["pet_id"])
                price = int(row["price"])
                if price <= 0:
                    continue
                code = self._build_variant_code(
                    bool(row.get("is_gold")),
                    bool(row.get("is_rainbow")),
                    bool(row.get("is_galaxy")),
                    bool(row.get("is_shiny")),
                )
                key = (pet_id, code)
                prices = prices_by_key[key]
                if len(prices) >= _MARKET_HISTORY_SAMPLE:
                    continue
                prices.append(price)
                if pet_id not in base_income_by_pet:
                    base_income_by_pet[pet_id] = int(row.get("base_income_per_hour") or 0)
                if pet_id not in name_by_pet:
                    name_by_pet[pet_id] = str(row.get("name", ""))
                if pet_id not in rarity_by_pet:
                    rarity_by_pet[pet_id] = str(row.get("rarity", ""))

        market_values: Dict[Tuple[int, str], int] = {}
        for (pet_id, code), prices in prices_by_key.items():
            if not prices:
                continue
            median_price = statistics.median(prices)
            value = int(round(median_price))
            base_income = base_income_by_pet.get(pet_id, 0)
            pet_name = name_by_pet.get(pet_id, "")
            rarity = rarity_by_pet.get(pet_id, "")
            is_huge = pet_name.lower() in _HUGE_PET_NAME_LOOKUP
            zone_slug = _PET_ZONE_BY_NAME.get(pet_name.lower(), "exclusif")
            variant_multiplier = float(_MARKET_VARIANT_MULTIPLIERS.get(code, 1.0))
            owner_count = owner_counts.get(pet_id, 0)
            base_value = self.compute_market_value_gems(
                {
                    "name": pet_name,
                    "rarity": rarity,
                    "base_income_per_hour": base_income,
                    "is_huge": is_huge,
                },
                config=MARKET_VALUE_CONFIG,
                zone_slug=zone_slug,
                owner_count=owner_count,
                variant_multiplier=variant_multiplier,
            )
            min_value = max(1, int(base_value * _MARKET_MIN_MULTIPLIER))
            max_value = max(min_value, int(base_value * _MARKET_MAX_MULTIPLIER))
            if value < min_value:
                value = min_value
            elif value > max_value:
                value = max_value
            rarity_key = self._market_rarity_key(
                name=pet_name, rarity=rarity, is_huge=is_huge
            )
            cap_value = MARKET_VALUE_CONFIG.get("rarity_cap", {}).get(
                rarity_key, MARKET_VALUE_CONFIG.get("rarity_cap", {}).get(rarity)
            )
            if cap_value is not None:
                try:
                    cap_int = int(cap_value)
                except (TypeError, ValueError):
                    cap_int = 0
                if cap_int > 0:
                    value = min(value, cap_int)
            market_values[(pet_id, code)] = max(0, int(value))

        return market_values

    async def get_pet_market_values(self) -> Dict[Tuple[int, str], int]:
        """Retourne la dernière valeur marché enregistrée pour chaque variante.

        Cette méthode est appelée à chaque ouverture d'œuf (potentiellement
        plusieurs fois par commande en cas d'œufs bonus) alors que les
        valeurs de marché ne changent que rarement (resync manuel ou
        rebase de l'économie). On la met donc en cache en mémoire avec le
        même TTL que le reste du cache applicatif, pour éviter de refaire
        cette requête à chaque hatch.
        """

        cached = self._market_values_cache.get(self._MARKET_VALUES_CACHE_KEY)
        if cached is not None:
            return cached  # type: ignore[return-value]

        rows = await self.pool.fetch(
            """
            SELECT pet_id, variant_code, value_in_gems
            FROM pet_market_values
            """
        )
        if rows:
            values = {
                (int(row["pet_id"]), str(row["variant_code"])): max(
                    0, int(row["value_in_gems"])
                )
                for row in rows
            }
        else:
            values = await self._get_trade_history_market_values()

        self._market_values_cache.set(self._MARKET_VALUES_CACHE_KEY, values)
        return values

    def _invalidate_market_values_cache(self) -> None:
        self._market_values_cache.clear()

    @staticmethod
    def _market_rarity_key(*, name: str, rarity: str, is_huge: bool) -> str:
        lowered = name.lower()
        if "titanic" in lowered:
            return "Titanic"
        if is_huge:
            return "Huge"
        return rarity

    @classmethod
    def _market_variant_multiplier(
        cls,
        *,
        is_gold: bool,
        is_rainbow: bool,
        is_galaxy: bool,
        is_shiny: bool,
    ) -> float:
        code = cls._build_variant_code(is_gold, is_rainbow, is_galaxy, is_shiny)
        return float(_MARKET_VARIANT_MULTIPLIERS.get(code, 1.0))

    @classmethod
    def compute_market_value_gems(
        cls,
        pet: Mapping[str, object],
        *,
        config: Mapping[str, object],
        zone_slug: str | None = None,
        owner_count: int | None = None,
        variant_multiplier: float = 1.0,
    ) -> int:
        name = str(pet.get("name") or "")
        rarity = str(pet.get("rarity") or "")
        is_huge = bool(pet.get("is_huge"))
        rarity_key = cls._market_rarity_key(name=name, rarity=rarity, is_huge=is_huge)

        rarity_base = config.get("rarity_base", {})
        rarity_cap = config.get("rarity_cap", {})
        base_value = float(rarity_base.get(rarity_key, rarity_base.get(rarity, 1.0)))
        power_value = float(pet.get("base_income_per_hour") or pet.get("power") or 0)

        baseline_by_zone = config.get("power_baseline_by_zone", {})
        baseline = 0.0
        if zone_slug and isinstance(baseline_by_zone, Mapping):
            baseline = float(baseline_by_zone.get(zone_slug, 0.0) or 0.0)
        if baseline <= 0:
            baseline = float(config.get("power_baseline_global", 0.0) or 0.0)

        exponent = float(config.get("power_exponent", 1.0) or 1.0)
        if baseline > 0 and power_value > 0:
            ratio = power_value / baseline
            power_factor = ratio ** exponent if ratio > 0 else 0.0
        else:
            power_factor = 1.0

        size_multiplier = 1.0
        lowered = name.lower()
        if "titanic" in lowered:
            size_multiplier = float(config.get("titanic_multiplier", 1.0) or 1.0)
        elif is_huge:
            size_multiplier = float(config.get("huge_multiplier", 1.0) or 1.0)

        owner_multiplier = 1.0
        if owner_count is not None and owner_count > 1:
            exponent = float(config.get("owner_exponent", 0.0) or 0.0)
            min_multiplier = float(config.get("owner_min_multiplier", 0.0) or 0.0)
            if exponent > 0:
                owner_multiplier = max(min_multiplier, owner_count ** (-exponent))

        value = (
            base_value
            * power_factor
            * size_multiplier
            * max(0.0, float(variant_multiplier))
            * owner_multiplier
        )
        min_value = int(config.get("min_value", 1) or 1)
        if value < min_value:
            value = float(min_value)
        value_int = max(0, int(round(value)))
        if isinstance(rarity_cap, Mapping):
            cap_value = rarity_cap.get(rarity_key, rarity_cap.get(rarity))
            if cap_value is not None:
                try:
                    cap_int = int(cap_value)
                except (TypeError, ValueError):
                    cap_int = 0
                if cap_int > 0:
                    value_int = min(value_int, cap_int)
        return max(min_value, value_int)

    @classmethod
    def _fallback_market_value(
        cls,
        *,
        name: str,
        rarity: str,
        base_income_per_hour: int,
        is_huge: bool,
        zone_slug: str,
        is_gold: bool,
        is_rainbow: bool,
        is_galaxy: bool,
        is_shiny: bool,
    ) -> int:
        variant_multiplier = cls._market_variant_multiplier(
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            is_galaxy=is_galaxy,
            is_shiny=is_shiny,
        )
        return cls.compute_market_value_gems(
            {
                "name": name,
                "rarity": rarity,
                "base_income_per_hour": base_income_per_hour,
                "is_huge": is_huge,
            },
            config=MARKET_VALUE_CONFIG,
            zone_slug=zone_slug,
            variant_multiplier=variant_multiplier,
        )

    @staticmethod
    def _round_market_value(value: float) -> int:
        if value < 100:
            return max(1, int(round(value)))
        return max(1, int(round(value / 10) * 10))

    @classmethod
    def _compute_pet_base_market_value(
        cls,
        *,
        name: str,
        rarity: str,
        base_income_per_hour: int,
        zone_slug: str,
        is_huge: bool,
        owner_count: int | None = None,
    ) -> float:
        return float(
            cls.compute_market_value_gems(
                {
                    "name": name,
                    "rarity": rarity,
                    "base_income_per_hour": base_income_per_hour,
                    "is_huge": is_huge,
                },
                config=MARKET_VALUE_CONFIG,
                zone_slug=zone_slug,
                owner_count=owner_count,
            )
        )

    async def sync_pet_market_values(self) -> int:
        """Recalcule et stocke les valeurs marché pour chaque pet et variante."""

        pets = await self.pool.fetch(
            """
            SELECT
                p.pet_id,
                p.name,
                p.rarity,
                p.base_income_per_hour
            FROM pets AS p
            """
        )
        owner_rows = await self.pool.fetch(
            "SELECT pet_id, COUNT(DISTINCT user_id) AS owners FROM user_pets GROUP BY pet_id"
        )
        owner_counts = {
            int(row["pet_id"]): int(row.get("owners") or 0) for row in owner_rows
        }

        is_huge_lookup = {pet.name.lower(): pet.is_huge for pet in PET_DEFINITIONS}
        zone_by_pet = {
            pet.name.lower(): egg.zone_slug
            for egg in PET_EGG_DEFINITIONS
            for pet in egg.pets
        }

        values_to_store: list[tuple[int, str, int]] = []
        for row in pets:
            pet_id = int(row["pet_id"])
            name = str(row["name"])
            rarity = str(row["rarity"])
            base_income = int(row.get("base_income_per_hour") or 0)
            is_huge = bool(is_huge_lookup.get(name.lower(), False))
            zone_slug = zone_by_pet.get(name.lower(), "exclusif")
            owner_count = owner_counts.get(pet_id, 0)
            base_value = self._compute_pet_base_market_value(
                name=name,
                rarity=rarity,
                base_income_per_hour=base_income,
                zone_slug=zone_slug,
                is_huge=is_huge,
                owner_count=owner_count,
            )
            rarity_key = self._market_rarity_key(
                name=name, rarity=rarity, is_huge=is_huge
            )
            cap_value = MARKET_VALUE_CONFIG.get("rarity_cap", {}).get(
                rarity_key, MARKET_VALUE_CONFIG.get("rarity_cap", {}).get(rarity)
            )
            cap_int = 0
            if cap_value is not None:
                try:
                    cap_int = int(cap_value)
                except (TypeError, ValueError):
                    cap_int = 0

            for code, multiplier in _MARKET_VARIANTS:
                value = base_value * multiplier
                value = self._round_market_value(value)
                if cap_int > 0:
                    value = min(value, cap_int)
                values_to_store.append((pet_id, code, int(value)))

        if not values_to_store:
            return 0

        await self.pool.executemany(
            """
            INSERT INTO pet_market_values (pet_id, variant_code, value_in_gems)
            VALUES ($1, $2, $3)
            ON CONFLICT (pet_id, variant_code)
            DO UPDATE SET value_in_gems = $3, updated_at = CURRENT_TIMESTAMP
            """,
            values_to_store,
        )
        self._invalidate_market_values_cache()
        return len(values_to_store)

    async def reset_rich_users_gems(
        self,
        *,
        threshold: int = 1_000_000,
        new_amount: int = 100_000,
    ) -> Dict[str, Any]:
        """Reset les :Gem: des utilisateurs riches à un montant fixe."""

        query_select = """
            SELECT user_id, gems
            FROM users
            WHERE gems >= $1
            ORDER BY gems DESC
        """
        query_update = """
            UPDATE users
            SET gems = $1
            WHERE gems >= $2
            RETURNING user_id
        """

        async with self.pool.acquire() as conn:
            rows_before = await conn.fetch(query_select, threshold)

            if not rows_before:
                return {
                    "affected_count": 0,
                    "total_gems_removed": 0,
                    "users": [],
                }

            total_removed = sum(row["gems"] - new_amount for row in rows_before)
            await conn.execute(query_update, new_amount, threshold)

        users_details = [
            {
                "user_id": row["user_id"],
                "old_gems": row["gems"],
                "new_gems": new_amount,
                "removed": row["gems"] - new_amount,
            }
            for row in rows_before
        ]

        return {
            "affected_count": len(rows_before),
            "total_gems_removed": total_removed,
            "users": users_details,
        }

    async def reset_all_progress(self) -> dict[str, int]:
        """Réinitialise TOUTE la progression du serveur.

        Supprime la ligne de chaque utilisateur dans `users` : grâce aux
        contraintes `ON DELETE CASCADE`, cela vide en cascade toutes les
        tables liées à la progression individuelle (grades, zones, pets
        possédés, historique d'ouvertures, masteries, préférences, listings
        de marché/plaza, clans, membres de clan, activité, potions,
        transactions...).

        Les tables de catalogue globales (`pets`, `pet_market_values`,
        `pet_trade_history`, `gemshop_roles`, `config_flags`) ne sont pas
        affectées : elles décrivent le jeu lui-même, pas la progression
        d'un joueur. Le KOTH (`koth_states`) est vidé séparément car il
        référence un `king_user_id` sans contrainte de clé étrangère.

        Retourne un résumé (nombre d'utilisateurs supprimés) pour le log.
        """

        async with self.transaction() as connection:
            user_count = await connection.fetchval("SELECT COUNT(*) FROM users")
            await connection.execute("TRUNCATE TABLE users CASCADE")
            await connection.execute("DELETE FROM koth_states")

        return {"users_removed": int(user_count or 0)}

    async def get_config_flag(self, flag_name: str) -> bool:
        """Récupère un flag de configuration booléen."""

        row = await self.pool.fetchrow(
            "SELECT value FROM config_flags WHERE flag_name = $1",
            flag_name,
        )
        return bool(row["value"]) if row else False

    async def set_config_flag(self, flag_name: str, value: bool) -> None:
        """Définit un flag de configuration."""

        await self.pool.execute(
            """
            INSERT INTO config_flags (flag_name, value, updated_at)
            VALUES ($1, $2, CURRENT_TIMESTAMP)
            ON CONFLICT (flag_name)
            DO UPDATE SET value = $2, updated_at = CURRENT_TIMESTAMP
            """,
            flag_name,
            value,
        )

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
        _ = (user_id, limit)
        return []

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
            daycare_conflict = await connection.fetchval(
                "SELECT 1 FROM user_daycare WHERE user_id = $1 AND user_pet_id = $2",
                seller_id,
                user_pet_id,
            )
            if daycare_conflict:
                raise DatabaseError("Ce pet est actuellement à la garderie.")

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
                    daycare_conflict = await connection.fetchval(
                        "SELECT 1 FROM user_daycare WHERE user_id = $1 AND user_pet_id = $2",
                        owner_id,
                        user_pet_id,
                    )
                    if daycare_conflict:
                        raise DatabaseError("Un des pets sélectionnés est à la garderie.")
                    pets_data.append((row, price))
                return pets_data

            initiator_pets = await _prepare_pets(initiator_id, initiator_offer)
            partner_pets = await _prepare_pets(partner_id, partner_offer)

            initiator_balance_row = await connection.fetchrow(
                "SELECT balance, rebirth_count FROM users WHERE user_id = $1 FOR UPDATE",
                initiator_id,
            )
            partner_balance_row = await connection.fetchrow(
                "SELECT balance, rebirth_count FROM users WHERE user_id = $1 FOR UPDATE",
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

            initiator_rebirth = int(initiator_balance_row.get("rebirth_count") or 0)
            partner_rebirth = int(partner_balance_row.get("rebirth_count") or 0)
            initiator_gain, _ = self._apply_rebirth_multiplier(
                partner_pb_out, initiator_rebirth
            )
            partner_gain, _ = self._apply_rebirth_multiplier(
                initiator_pb_out, partner_rebirth
            )

            initiator_final = initiator_mid + initiator_gain
            partner_final = partner_mid + partner_gain

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
                    amount=partner_gain,
                    balance_before=partner_mid,
                    balance_after=partner_mid + partner_gain,
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
                    amount=initiator_gain,
                    balance_before=initiator_mid,
                    balance_after=initiator_mid + initiator_gain,
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
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                seller_id,
            )
            buyer_balance = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                buyer_id,
            )
            if seller_balance is None or buyer_balance is None:
                raise DatabaseError("Impossible de récupérer les soldes.")

            seller_before = int(seller_balance["gems"])
            buyer_before = int(buyer_balance["gems"])
            if buyer_before < price:
                raise InsufficientBalanceError("Solde de :Gem: insuffisant pour cet achat.")

            seller_after = seller_before + price
            buyer_after = buyer_before - price

            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                buyer_after,
                buyer_id,
            )
            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
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
                currency="gem",
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
                currency="gem",
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
        item_power: int | None = None,
    ) -> asyncpg.Record:
        if item_type not in {"potion", "role"}:
            raise DatabaseError("Type d'objet invalide pour la plaza.")
        if quantity <= 0:
            raise DatabaseError("La quantité doit être positive.")
        if price < 0:
            raise DatabaseError("Le prix doit être positif.")
        if item_type == "role" and item_slug is None:
            raise DatabaseError("Merci de préciser le rôle à mettre en vente.")
        if item_type == "role" and quantity != 1:
            raise DatabaseError("Tu ne peux vendre qu'un rôle à la fois.")

        await self.ensure_user(seller_id)

        async with self.transaction() as connection:
            slug = item_slug
            if item_type == "potion":
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
            else:
                if not slug:
                    raise DatabaseError("Merci de préciser le rôle à mettre en vente.")

            listing = await connection.fetchrow(
                """
                INSERT INTO plaza_consumable_listings (
                    seller_id,
                    item_type,
                    item_slug,
                    item_power,
                    quantity,
                    price
                )
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                seller_id,
                item_type,
                slug,
                item_power,
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
            if item_type == "potion":
                if not item_slug:
                    raise DatabaseError("Potion inconnue pour cette annonce.")
                await self.add_user_potion(
                    seller_id,
                    str(item_slug),
                    quantity=quantity,
                    connection=connection,
                )
            # Les rôles sont rendus côté bot.

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
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                seller_id,
            )
            buyer_balance = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                buyer_id,
            )
            if seller_balance is None or buyer_balance is None:
                raise DatabaseError("Impossible de récupérer les soldes.")

            seller_before = int(seller_balance["gems"])
            buyer_before = int(buyer_balance["gems"])
            if buyer_before < price:
                raise InsufficientBalanceError("Solde de :Gem: insuffisant pour cet achat.")

            seller_after = seller_before + price
            buyer_after = buyer_before - price

            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                buyer_after,
                buyer_id,
            )
            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                seller_after,
                seller_id,
            )

            if item_type == "potion":
                await self.add_user_potion(
                    buyer_id,
                    item_slug,
                    quantity=quantity,
                    connection=connection,
                )
            # Les rôles sont attribués côté bot pour respecter les permissions Discord.

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
                currency="gem",
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
                currency="gem",
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
        seller_id: int | None = None,
    ) -> Sequence[asyncpg.Record]:
        limit = max(1, limit)
        return await self.pool.fetch(
            """
            SELECT *
            FROM plaza_consumable_listings
            WHERE status = 'active'
              AND ($2::TEXT IS NULL OR item_type = $2)
              AND ($3::BIGINT IS NULL OR seller_id = $3)
            ORDER BY created_at ASC
            LIMIT $1
            """,
            limit,
            item_type,
            seller_id,
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

    # ------------------------------------------------------------------
