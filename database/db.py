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
    PET_FARM_ENCHANT_BASE,
    PET_FARM_ENCHANT_MAX_CHANCE,
    PET_FARM_ENCHANT_PER_PET,
    PET_FARM_GEM_MAX,
    PET_FARM_GEM_PER_PET_HOUR,
    PET_FARM_GEM_VARIANCE_PER_PET,
    DAYCARE_GEM_MAX,
    DAYCARE_GEM_PER_PET_HOUR,
    DAYCARE_MAX_PETS,
    PET_FARM_POTION_BASE,
    PET_FARM_POTION_MAX_CHANCE,
    PET_FARM_POTION_PER_PET,
    PET_FARM_TICKET_BASE,
    PET_FARM_TICKET_MAX_CHANCE,
    PET_FARM_TICKET_PER_PET,
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
from utils.enchantments import (
    compute_prissbucks_multiplier,
    pick_random_enchantment,
    roll_enchantment_power,
)
from utils.cache import LruTTLCache

__all__ = [
    "Database",
    "DatabaseError",
    "InsufficientBalanceError",
    "InsufficientRaffleTicketsError",
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


class InsufficientRaffleTicketsError(DatabaseError):
    """Erreur levée lorsque l'utilisateur n'a pas assez de tickets en inventaire."""


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
            return scale_pet_value(raw_income)

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
            """
            UPDATE plaza_auctions
            SET min_increment = 1
            WHERE min_increment IS NULL OR min_increment < 1
            """
        )
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
            UPDATE plaza_auctions
            SET starting_bid = GREATEST(
                    1,
                    CAST(FLOOR(COALESCE(starting_bid, 0)::numeric / {factor}) AS BIGINT)
                ),
                min_increment = GREATEST(
                    1,
                    CAST(FLOOR(COALESCE(min_increment, 0)::numeric / {factor}) AS BIGINT)
                ),
                current_bid = GREATEST(
                    0,
                    CAST(FLOOR(COALESCE(current_bid, 0)::numeric / {factor}) AS BIGINT)
                ),
                buyout_price = CASE
                    WHEN buyout_price IS NULL THEN NULL
                    ELSE GREATEST(
                        0,
                        CAST(FLOOR(COALESCE(buyout_price, 0)::numeric / {factor}) AS BIGINT)
                    )
                END
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

            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plaza_auctions (
                    id SERIAL PRIMARY KEY,
                    seller_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    buyer_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
                    item_type TEXT NOT NULL CHECK (item_type IN ('pet', 'ticket', 'potion', 'enchantment')),
                    item_slug TEXT,
                    item_power SMALLINT,
                    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
                    user_pet_id BIGINT REFERENCES user_pets(id) ON DELETE SET NULL,
                    starting_bid BIGINT NOT NULL CHECK (starting_bid > 0),
                    min_increment BIGINT NOT NULL CHECK (min_increment > 0),
                    current_bid BIGINT NOT NULL DEFAULT 0 CHECK (current_bid >= 0),
                    current_bidder_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
                    buyout_price BIGINT,
                    status VARCHAR(20) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    ends_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plaza_auction_status ON plaza_auctions(status)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plaza_auction_ends ON plaza_auctions(ends_at)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plaza_auction_seller ON plaza_auctions(seller_id)"
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
                CREATE TABLE IF NOT EXISTS user_enchantments (
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    slug TEXT NOT NULL,
                    power SMALLINT NOT NULL CHECK (power BETWEEN 1 AND 10),
                    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
                    PRIMARY KEY (user_id, slug, power)
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_equipped_enchantments (
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    slug TEXT NOT NULL,
                    power SMALLINT NOT NULL CHECK (power BETWEEN 1 AND 10),
                    equipped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, slug)
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
                CREATE TABLE IF NOT EXISTS raffle_entries (
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
            await connection.execute(
                "ALTER TABLE raffle_entries ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
            )

            await self._apply_economy_migrations(connection)

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

    async def get_race_personal_best(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            "SELECT race_best_stage FROM users WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return 0
        return int(row.get("race_best_stage") or 0)

    async def update_race_personal_best(
        self, user_id: int, stage_cleared: int
    ) -> int:
        await self.ensure_user(user_id)
        normalized_stage = max(0, int(stage_cleared))
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT race_best_stage FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            current_best = int(row.get("race_best_stage") or 0) if row else 0
            if normalized_stage <= current_best:
                return current_best

            updated = await connection.fetchrow(
                """
                UPDATE users
                SET race_best_stage = $1
                WHERE user_id = $2
                RETURNING race_best_stage
                """,
                normalized_stage,
                user_id,
            )

        return int(updated.get("race_best_stage") or normalized_stage)

    async def get_transaction_count(
        self, user_id: int, *, transaction_type: str
    ) -> int:
        row = await self.pool.fetchrow(
            """
            SELECT COUNT(*) AS total
            FROM transactions
            WHERE user_id = $1 AND transaction_type = $2
            """,
            user_id,
            transaction_type,
        )
        if row is None:
            return 0
        return int(row.get("total") or 0)

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

    async def get_user_raffle_entries(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            "SELECT quantity FROM raffle_entries WHERE user_id = $1",
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

    async def stake_raffle_tickets(
        self,
        user_id: int,
        *,
        amount: int,
    ) -> tuple[int, int]:
        if amount <= 0:
            raise ValueError("La quantité à miser doit être positive")

        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT quantity FROM raffle_tickets WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            current_inventory = int(row.get("quantity") or 0) if row else 0
            if current_inventory < amount:
                raise InsufficientRaffleTicketsError(
                    "Inventaire insuffisant pour miser autant de tickets"
                )

            new_inventory = current_inventory - amount
            if row:
                if new_inventory > 0:
                    await connection.execute(
                        "UPDATE raffle_tickets SET quantity = $2, updated_at = NOW() WHERE user_id = $1",
                        user_id,
                        new_inventory,
                    )
                else:
                    await connection.execute(
                        "DELETE FROM raffle_tickets WHERE user_id = $1",
                        user_id,
                    )

            entry_row = await connection.fetchrow(
                "SELECT quantity FROM raffle_entries WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            current_entries = int(entry_row.get("quantity") or 0) if entry_row else 0
            new_entries = current_entries + amount
            if entry_row:
                await connection.execute(
                    "UPDATE raffle_entries SET quantity = $2, updated_at = NOW() WHERE user_id = $1",
                    user_id,
                    new_entries,
                )
            else:
                await connection.execute(
                    """
                    INSERT INTO raffle_entries (user_id, quantity, updated_at)
                    VALUES ($1, $2, NOW())
                    """,
                    user_id,
                    new_entries,
                )

        return new_inventory, new_entries

    async def withdraw_raffle_entries(
        self,
        user_id: int,
        *,
        amount: int | None = None,
    ) -> tuple[int, int]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            entry_row = await connection.fetchrow(
                "SELECT quantity FROM raffle_entries WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            current_entries = int(entry_row.get("quantity") or 0) if entry_row else 0
            if current_entries <= 0:
                inventory_row = await connection.fetchrow(
                    "SELECT quantity FROM raffle_tickets WHERE user_id = $1",
                    user_id,
                )
                inventory = int(inventory_row.get("quantity") or 0) if inventory_row else 0
                return inventory, 0

            withdraw_amount = current_entries if amount is None else min(amount, current_entries)
            remaining_entries = current_entries - withdraw_amount
            if remaining_entries > 0:
                await connection.execute(
                    "UPDATE raffle_entries SET quantity = $2, updated_at = NOW() WHERE user_id = $1",
                    user_id,
                    remaining_entries,
                )
            else:
                await connection.execute(
                    "DELETE FROM raffle_entries WHERE user_id = $1",
                    user_id,
                )

            inventory_row = await connection.fetchrow(
                "SELECT quantity FROM raffle_tickets WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            current_inventory = int(inventory_row.get("quantity") or 0) if inventory_row else 0
            new_inventory = current_inventory + withdraw_amount
            if inventory_row:
                await connection.execute(
                    "UPDATE raffle_tickets SET quantity = $2, updated_at = NOW() WHERE user_id = $1",
                    user_id,
                    new_inventory,
                )
            else:
                await connection.execute(
                    """
                    INSERT INTO raffle_tickets (user_id, quantity, updated_at)
                    VALUES ($1, $2, NOW())
                    """,
                    user_id,
                    new_inventory,
                )

        return new_inventory, remaining_entries

    async def draw_raffle_winner(self) -> tuple[int, int, int] | None:
        async with self.transaction() as connection:
            rows = await connection.fetch(
                """
                SELECT user_id, quantity
                FROM raffle_entries
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
            if len(totals) > 1:
                random.shuffle(totals)
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
            await connection.execute("DELETE FROM raffle_entries")
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
        value = await self.pool.fetchval("SELECT COALESCE(SUM(quantity), 0) FROM raffle_entries")
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

    async def add_user_enchantment(
        self,
        user_id: int,
        slug: str,
        *,
        power: int,
        quantity: int = 1,
        connection: asyncpg.Connection | None = None,
    ) -> None:
        if quantity <= 0:
            raise ValueError("La quantité doit être positive pour un enchantement")
        if power < 1 or power > 10:
            raise ValueError("Le niveau d'enchantement doit être compris entre 1 et 10")

        executor = connection or self.pool
        await executor.execute(
            """
            INSERT INTO user_enchantments (user_id, slug, power, quantity)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, slug, power)
            DO UPDATE SET quantity = user_enchantments.quantity + EXCLUDED.quantity
            """,
            user_id,
            slug,
            power,
            quantity,
        )

    async def consume_user_enchantment(
        self,
        user_id: int,
        slug: str,
        *,
        power: int,
        quantity: int = 1,
        connection: asyncpg.Connection | None = None,
    ) -> bool:
        if quantity <= 0:
            return False
        if power < 1 or power > 10:
            return False

        executor = connection or self.pool
        row = await executor.fetchrow(
            """
            SELECT quantity
            FROM user_enchantments
            WHERE user_id = $1 AND slug = $2 AND power = $3
            FOR UPDATE
            """,
            user_id,
            slug,
            power,
        )
        if row is None:
            return False

        current_qty = int(row.get("quantity") or 0)
        if current_qty < quantity:
            return False

        new_qty = current_qty - quantity
        if new_qty > 0:
            await executor.execute(
                """
                UPDATE user_enchantments
                SET quantity = $4
                WHERE user_id = $1 AND slug = $2 AND power = $3
                """,
                user_id,
                slug,
                power,
                new_qty,
            )
        else:
            await executor.execute(
                """
                DELETE FROM user_enchantments
                WHERE user_id = $1 AND slug = $2 AND power = $3
                """,
                user_id,
                slug,
                power,
            )
            await executor.execute(
                """
                DELETE FROM user_equipped_enchantments
                WHERE user_id = $1 AND slug = $2 AND power = $3
                """,
                user_id,
                slug,
                power,
            )
        return True

    async def sell_enchantment_for_gems(
        self,
        user_id: int,
        slug: str,
        *,
        power: int,
        quantity: int = 1,
        unit_price: int,
    ) -> tuple[str, int, int, int, int]:
        if quantity <= 0:
            return "invalid_quantity", 0, 0, 0, 0
        if power < 1 or power > 10:
            return "invalid_power", 0, 0, 0, 0
        if unit_price < 0:
            return "invalid_price", 0, 0, 0, 0

        await self.ensure_user(user_id)

        async with self.transaction() as connection:
            owned_row = await connection.fetchrow(
                """
                SELECT quantity
                FROM user_enchantments
                WHERE user_id = $1 AND slug = $2 AND power = $3
                FOR UPDATE
                """,
                user_id,
                slug,
                power,
            )
            if owned_row is None:
                return "missing", 0, 0, 0, 0

            current_qty = int(owned_row.get("quantity") or 0)
            if current_qty < quantity:
                return "insufficient", 0, 0, 0, current_qty

            remaining_qty = current_qty - quantity
            if remaining_qty > 0:
                await connection.execute(
                    """
                    UPDATE user_enchantments
                    SET quantity = $4
                    WHERE user_id = $1 AND slug = $2 AND power = $3
                    """,
                    user_id,
                    slug,
                    power,
                    remaining_qty,
                )
            else:
                await connection.execute(
                    """
                    DELETE FROM user_enchantments
                    WHERE user_id = $1 AND slug = $2 AND power = $3
                    """,
                    user_id,
                    slug,
                    power,
                )
                await connection.execute(
                    """
                    DELETE FROM user_equipped_enchantments
                    WHERE user_id = $1 AND slug = $2 AND power = $3
                    """,
                    user_id,
                    slug,
                    power,
                )

            gems_row = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if gems_row is None:
                raise DatabaseError("Utilisateur introuvable lors de la vente d'enchantements")

            gems_before = int(gems_row.get("gems") or 0)
            payout = max(0, unit_price * quantity)
            tentative_after = gems_before + payout
            gems_after = tentative_after if tentative_after >= 0 else 0

            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                gems_after,
                user_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=user_id,
                transaction_type="enchantment_sell",
                currency="gem",
                amount=gems_after - gems_before,
                balance_before=gems_before,
                balance_after=gems_after,
                description=f"Vente de {slug} niv {power} x{quantity}",
            )

            return "sold", payout, gems_before, gems_after, remaining_qty

    async def get_user_enchantments(self, user_id: int) -> Sequence[asyncpg.Record]:
        await self.ensure_user(user_id)
        return await self.pool.fetch(
            """
            SELECT slug, power, quantity
            FROM user_enchantments
            WHERE user_id = $1 AND quantity > 0
            ORDER BY slug, power
            """,
            user_id,
        )

    async def get_equipped_enchantments(self, user_id: int) -> Sequence[asyncpg.Record]:
        await self.ensure_user(user_id)
        return await self.pool.fetch(
            """
            SELECT equipped.slug, equipped.power, equipped.equipped_at
            FROM user_equipped_enchantments AS equipped
            JOIN user_enchantments AS inventory
                ON inventory.user_id = equipped.user_id
                AND inventory.slug = equipped.slug
                AND inventory.power = equipped.power
                AND inventory.quantity > 0
            WHERE equipped.user_id = $1
            ORDER BY equipped.equipped_at
            """,
            user_id,
        )

    async def equip_user_enchantment(
        self,
        user_id: int,
        slug: str,
        *,
        power: int,
        slot_limit: int,
    ) -> str:
        if power < 1 or power > 10:
            raise ValueError("Le niveau d'enchantement doit être compris entre 1 et 10")
        if slot_limit <= 0:
            return "limit"

        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            owned_row = await connection.fetchrow(
                """
                SELECT quantity
                FROM user_enchantments
                WHERE user_id = $1 AND slug = $2 AND power = $3 AND quantity > 0
                FOR UPDATE
                """,
                user_id,
                slug,
                power,
            )
            if owned_row is None:
                return "missing"

            existing = await connection.fetchrow(
                """
                SELECT power
                FROM user_equipped_enchantments
                WHERE user_id = $1 AND slug = $2
                FOR UPDATE
                """,
                user_id,
                slug,
            )
            if existing:
                previous_power = int(existing.get("power") or 0)
                if previous_power == power:
                    return "unchanged"
                await connection.execute(
                    """
                    UPDATE user_equipped_enchantments
                    SET power = $3, equipped_at = NOW()
                    WHERE user_id = $1 AND slug = $2
                    """,
                    user_id,
                    slug,
                    power,
                )
                return "updated"

            equipped_count = await connection.fetchval(
                "SELECT COUNT(*) FROM user_equipped_enchantments WHERE user_id = $1",
                user_id,
            )
            if int(equipped_count or 0) >= slot_limit:
                return "limit"

            await connection.execute(
                """
                INSERT INTO user_equipped_enchantments (user_id, slug, power)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, slug)
                DO UPDATE SET power = EXCLUDED.power, equipped_at = NOW()
                """,
                user_id,
                slug,
                power,
            )
            return "equipped"

    async def unequip_user_enchantment(self, user_id: int, slug: str) -> bool:
        if not slug:
            return False

        await self.ensure_user(user_id)
        result = await self.pool.execute(
            "DELETE FROM user_equipped_enchantments WHERE user_id = $1 AND slug = $2",
            user_id,
            slug,
        )
        return bool(result and result.endswith("DELETE 1"))

    async def get_enchantment_powers(self, user_id: int) -> Dict[str, int]:
        rows = await self.get_equipped_enchantments(user_id)
        summary: Dict[str, int] = {}
        for row in rows:
            slug = str(row.get("slug") or "")
            power = int(row.get("power") or 0)
            if not slug or power <= 0:
                continue
            current = summary.get(slug, 0)
            summary[slug] = max(current, power)
        return summary

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
        currency: str = "pb",
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
                currency,
                amount,
                balance_before,
                balance_after,
                description,
                related_user_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """
        params = (
            user_id,
            transaction_type,
            currency,
            amount,
            balance_before,
            balance_after,
            description,
            related_user_id,
        )

        if connection is not None:
            try:
                await connection.execute(query, *params)
            except asyncpg.exceptions.UndefinedTableError:
                logger.warning("Transactions table missing — recreating before retry")
                await self._ensure_transactions_table(connection)
                await connection.execute(query, *params)
            return

        try:
            await self.pool.execute(query, *params)
        except asyncpg.exceptions.UndefinedTableError:
            logger.warning("Transactions table missing — recreating before retry")
            await self._ensure_transactions_table(self.pool)
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

    async def fetch_gems(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow("SELECT gems FROM users WHERE user_id = $1", user_id)
        return int(row["gems"]) if row else 0

    async def get_user_balance_rank(self, user_id: int) -> Mapping[str, int]:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            """
            SELECT
                u.balance,
                1 + (
                    SELECT COUNT(*)
                    FROM users other
                    WHERE other.balance > u.balance
                ) AS rank,
                (
                    SELECT COUNT(*) FROM users
                ) AS total
            FROM users u
            WHERE u.user_id = $1
            """,
            user_id,
        )
        if row is None:
            return {"balance": 0, "rank": 0, "total": 0}
        return {
            "balance": int(row.get("balance") or 0),
            "rank": int(row.get("rank") or 0),
            "total": int(row.get("total") or 0),
        }

    async def get_extra_pet_slots(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            "SELECT extra_pet_slots FROM users WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return 0
        return int(row.get("extra_pet_slots") or 0)

    async def get_pet_slot_limit(self, user_id: int) -> int:
        grade_level = await self.get_grade_level(user_id)
        extra_slots = await self.get_extra_pet_slots(user_id)
        return self._compute_pet_slot_limit(grade_level, extra_slots)

    async def add_extra_pet_slot(
        self,
        user_id: int,
        *,
        grade_level: int | None = None,
    ) -> tuple[int, bool]:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            user_row = await connection.fetchrow(
                "SELECT extra_pet_slots FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if grade_level is None:
                grade_row = await connection.fetchrow(
                    "SELECT grade_level FROM user_grades WHERE user_id = $1",
                    user_id,
                )
                grade_level = int(grade_row.get("grade_level") or 0) if grade_row else 0
            current_extra = int(user_row.get("extra_pet_slots") or 0) if user_row else 0
            base_capacity = BASE_PET_SLOTS + (grade_level or 0)
            max_extra_allowed = max(0, PET_SLOT_MAX_CAPACITY - base_capacity)
            if current_extra >= max_extra_allowed:
                return current_extra, False
            new_extra = min(current_extra + 1, max_extra_allowed)
            await connection.execute(
                "UPDATE users SET extra_pet_slots = $1 WHERE user_id = $2",
                new_extra,
                user_id,
            )
        return new_extra, new_extra > current_extra

    async def get_mexico_dispenser_last_claim(self, user_id: int) -> datetime | None:
        await self.ensure_user(user_id)
        value = await self.pool.fetchval(
            "SELECT mexico_dispenser_last_claim FROM users WHERE user_id = $1",
            user_id,
        )
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        raise DatabaseError("Valeur de cooldown Mexico invalide")

    async def record_mexico_dispenser_claim(self, user_id: int) -> None:
        await self.ensure_user(user_id)
        await self.pool.execute(
            "UPDATE users SET mexico_dispenser_last_claim = NOW() WHERE user_id = $1",
            user_id,
        )

    async def get_daily_state(self, user_id: int) -> tuple[datetime | None, int]:
        await self.ensure_user(user_id)
        row = await self.pool.fetchrow(
            "SELECT last_daily, daily_streak FROM users WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return None, 0
        last_daily = row.get("last_daily")
        if last_daily is not None and not isinstance(last_daily, datetime):
            raise DatabaseError("Valeur de cooldown daily invalide")
        return last_daily, int(row.get("daily_streak") or 0)

    async def claim_daily_reward(
        self,
        user_id: int,
        *,
        reward_pb: int,
        reward_gems: int,
        now: datetime,
        cooldown_seconds: float,
        streak_window_seconds: float,
    ) -> dict[str, object]:
        await self.ensure_user(user_id)
        cooldown_seconds = max(0.0, float(cooldown_seconds))
        streak_window_seconds = max(cooldown_seconds, float(streak_window_seconds))

        async with self.transaction() as connection:
            row = await connection.fetchrow(
                """
                SELECT balance, gems, last_daily, daily_streak, rebirth_count
                FROM users
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable lors du daily")

            last_daily = row.get("last_daily")
            if last_daily is not None and not isinstance(last_daily, datetime):
                raise DatabaseError("Valeur de cooldown daily invalide")

            if last_daily is not None:
                elapsed = (now - last_daily).total_seconds()
                if elapsed < cooldown_seconds:
                    return {
                        "status": "cooldown",
                        "remaining": max(0.0, cooldown_seconds - elapsed),
                    }
            else:
                elapsed = None

            current_streak = int(row.get("daily_streak") or 0)
            if elapsed is None:
                new_streak = 1
            elif elapsed <= streak_window_seconds:
                new_streak = max(1, current_streak + 1)
            else:
                new_streak = 1

            before_balance = int(row["balance"])
            before_gems = int(row["gems"])
            rebirth_count = int(row.get("rebirth_count") or 0)
            effective_pb, _ = self._apply_rebirth_multiplier(reward_pb, rebirth_count)
            effective_pb = max(0, int(effective_pb))
            effective_gems = max(0, int(reward_gems))

            after_balance = before_balance + effective_pb
            after_gems = before_gems + effective_gems

            await connection.execute(
                """
                UPDATE users
                SET balance = $1,
                    gems = $2,
                    last_daily = $3,
                    daily_streak = $4
                WHERE user_id = $5
                """,
                after_balance,
                after_gems,
                now,
                new_streak,
                user_id,
            )

            if effective_pb:
                await self.record_transaction(
                    connection=connection,
                    user_id=user_id,
                    transaction_type="daily",
                    currency="pb",
                    amount=effective_pb,
                    balance_before=before_balance,
                    balance_after=after_balance,
                    description="Récompense quotidienne",
                )
            if effective_gems:
                await self.record_transaction(
                    connection=connection,
                    user_id=user_id,
                    transaction_type="daily_gems",
                    currency="gem",
                    amount=effective_gems,
                    balance_before=before_gems,
                    balance_after=after_gems,
                    description="Récompense quotidienne (gemmes)",
                )

        return {
            "status": "claimed",
            "streak": new_streak,
            "before_balance": before_balance,
            "after_balance": after_balance,
            "before_gems": before_gems,
            "after_gems": after_gems,
            "reward_pb": effective_pb,
            "reward_gems": effective_gems,
        }

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
                "SELECT balance, rebirth_count FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable lors de la mise à jour du solde")

            before = int(row["balance"])
            rebirth_count = int(row.get("rebirth_count") or 0)
            effective_amount = amount
            if amount > 0:
                effective_amount, _ = self._apply_rebirth_multiplier(amount, rebirth_count)
            tentative_after = before + effective_amount
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
                currency="pb",
                amount=applied_amount,
                balance_before=before,
                balance_after=after,
                description=description,
                related_user_id=related_user_id,
            )

        return before, after

    async def increment_gems(
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
            gems = await self.fetch_gems(user_id)
            return gems, gems

        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable lors de la mise à jour des gemmes")

            before = int(row["gems"])
            tentative_after = before + amount
            after = tentative_after if tentative_after >= 0 else 0
            applied_amount = after - before
            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                after,
                user_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=user_id,
                transaction_type=transaction_type,
                currency="gem",
                amount=applied_amount,
                balance_before=before,
                balance_after=after,
                description=description,
                related_user_id=related_user_id,
            )

        return before, after

    async def get_gemshop_role_sales(self) -> Dict[int, int]:
        rows = await self.pool.fetch("SELECT role_id, sold FROM gemshop_roles")
        return {int(row["role_id"]): int(row.get("sold", 0) or 0) for row in rows}

    async def purchase_gemshop_role(
        self,
        user_id: int,
        *,
        role_id: int,
        price: int,
        stock: int,
    ) -> dict[str, int]:
        if price <= 0:
            raise DatabaseError("Le prix doit être supérieur à zéro.")
        if stock <= 0:
            raise DatabaseError("Ce rôle n'est plus disponible.")

        await self.ensure_user(user_id)

        async with self.transaction() as connection:
            await connection.execute(
                "INSERT INTO gemshop_roles (role_id, sold) VALUES ($1, 0) ON CONFLICT DO NOTHING",
                role_id,
            )
            stock_row = await connection.fetchrow(
                "SELECT sold FROM gemshop_roles WHERE role_id = $1 FOR UPDATE",
                role_id,
            )
            sold = int(stock_row.get("sold", 0) if stock_row else 0)
            if sold >= stock:
                raise DatabaseError("Ce rôle est en rupture de stock.")

            balance_row = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if balance_row is None:
                raise DatabaseError("Utilisateur introuvable pour l'achat de rôle.")

            buyer_before = int(balance_row["gems"])
            if buyer_before < price:
                raise InsufficientBalanceError(
                    "Solde de gemmes insuffisant pour acheter ce rôle."
                )

            buyer_after = buyer_before - price
            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                buyer_after,
                user_id,
            )
            await connection.execute(
                "UPDATE gemshop_roles SET sold = $1 WHERE role_id = $2",
                sold + 1,
                role_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=user_id,
                transaction_type="gemshop_role",
                currency="gem",
                amount=-price,
                balance_before=buyer_before,
                balance_after=buyer_after,
                description=f"Achat rôle {role_id}",
            )

        return {"buyer_before": buyer_before, "buyer_after": buyer_after, "sold": sold + 1}

    async def refund_gemshop_role_purchase(
        self, user_id: int, *, role_id: int, price: int
    ) -> None:
        if price <= 0:
            return

        async with self.transaction() as connection:
            balance_row = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if balance_row is None:
                raise DatabaseError("Utilisateur introuvable pour le remboursement.")

            before = int(balance_row["gems"])
            after = before + price
            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                after,
                user_id,
            )

            stock_row = await connection.fetchrow(
                "SELECT sold FROM gemshop_roles WHERE role_id = $1 FOR UPDATE",
                role_id,
            )
            if stock_row is not None:
                sold = max(0, int(stock_row.get("sold", 0) or 0))
                if sold > 0:
                    await connection.execute(
                        "UPDATE gemshop_roles SET sold = $1 WHERE role_id = $2",
                        sold - 1,
                        role_id,
                    )

            await self.record_transaction(
                connection=connection,
                user_id=user_id,
                transaction_type="gemshop_role_refund",
                currency="gem",
                amount=price,
                balance_before=before,
                balance_after=after,
                description=f"Remboursement rôle {role_id}",
            )

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
                "SELECT balance, rebirth_count FROM users WHERE user_id = $1 FOR UPDATE",
                sender_id,
            )
            recipient_row = await connection.fetchrow(
                "SELECT balance, rebirth_count FROM users WHERE user_id = $1 FOR UPDATE",
                recipient_id,
            )

            if sender_row is None or recipient_row is None:
                raise DatabaseError("Utilisateur introuvable lors du transfert")

            sender_before = int(sender_row["balance"])
            if sender_before < amount:
                raise InsufficientBalanceError("Solde insuffisant pour effectuer le transfert")

            recipient_before = int(recipient_row["balance"])

            sender_after = sender_before - amount
            recipient_gain = amount
            recipient_after = recipient_before + recipient_gain

            await connection.execute(
                "UPDATE users SET balance = $1 WHERE user_id = $2",
                sender_after,
                sender_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=sender_id,
                transaction_type=send_transaction_type,
                currency="pb",
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
                currency="pb",
                amount=recipient_gain,
                balance_before=recipient_before,
                balance_after=recipient_after,
                description=receive_description,
                related_user_id=sender_id,
            )

        return {
            "sender": {"before": sender_before, "after": sender_after},
            "recipient": {"before": recipient_before, "after": recipient_after},
        }

    async def transfer_gems(
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
            raise DatabaseError("Impossible de transférer des gemmes vers soi-même")
        if amount <= 0:
            raise ValueError("Le montant transféré doit être strictement positif")

        await self.ensure_user(sender_id)
        await self.ensure_user(recipient_id)

        async with self.transaction() as connection:
            sender_row = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                sender_id,
            )
            recipient_row = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                recipient_id,
            )

            if sender_row is None or recipient_row is None:
                raise DatabaseError("Utilisateur introuvable lors du transfert de gemmes")

            sender_before = int(sender_row["gems"])
            if sender_before < amount:
                raise InsufficientBalanceError("Solde de gemmes insuffisant pour effectuer le transfert")

            recipient_before = int(recipient_row["gems"])

            sender_after = sender_before - amount
            recipient_after = recipient_before + amount

            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                sender_after,
                sender_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=sender_id,
                transaction_type=send_transaction_type,
                currency="gem",
                amount=-amount,
                balance_before=sender_before,
                balance_after=sender_after,
                description=send_description,
                related_user_id=recipient_id,
            )

            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                recipient_after,
                recipient_id,
            )
            await self.record_transaction(
                connection=connection,
                user_id=recipient_id,
                transaction_type=receive_transaction_type,
                currency="gem",
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
        clamped_limit = max(0, int(limit))
        cache_key = ("leaderboard_balance", clamped_limit)
        cached = self._leaderboard_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        query = "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT $1"
        rows = await self._fetch(query, clamped_limit)
        self._leaderboard_cache.set(cache_key, rows)
        return rows

    async def get_gem_leaderboard(self, limit: int) -> Sequence[asyncpg.Record]:
        clamped_limit = max(0, int(limit))
        cache_key = ("leaderboard_gems", clamped_limit)
        cached = self._leaderboard_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        query = "SELECT user_id, gems FROM users ORDER BY gems DESC LIMIT $1"
        rows = await self._fetch(query, clamped_limit)
        self._leaderboard_cache.set(cache_key, rows)
        return rows

    async def get_balance_leaderboard_page(
        self, limit: int, offset: int
    ) -> tuple[Sequence[asyncpg.Record], int]:
        clamped_limit = max(0, int(limit))
        clamped_offset = max(0, int(offset))
        cache_key = ("leaderboard_balance_page", clamped_limit, clamped_offset)
        cached = self._leaderboard_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        total_row = await self._fetchrow("SELECT COUNT(*) AS total FROM users")
        total = int(total_row["total"]) if total_row else 0
        if clamped_limit == 0 or total <= 0:
            result = ([], total)
            self._leaderboard_cache.set(cache_key, result)
            return result
        query = """
            SELECT user_id, balance
            FROM users
            ORDER BY balance DESC
            LIMIT $1 OFFSET $2
        """
        rows = await self._fetch(query, clamped_limit, clamped_offset)
        result = (rows, total)
        self._leaderboard_cache.set(cache_key, result)
        return result

    async def get_gem_leaderboard_page(
        self, limit: int, offset: int
    ) -> tuple[Sequence[asyncpg.Record], int]:
        clamped_limit = max(0, int(limit))
        clamped_offset = max(0, int(offset))
        cache_key = ("leaderboard_gems_page", clamped_limit, clamped_offset)
        cached = self._leaderboard_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        total_row = await self._fetchrow("SELECT COUNT(*) AS total FROM users")
        total = int(total_row["total"]) if total_row else 0
        if clamped_limit == 0 or total <= 0:
            result = ([], total)
            self._leaderboard_cache.set(cache_key, result)
            return result
        query = """
            SELECT user_id, gems
            FROM users
            ORDER BY gems DESC
            LIMIT $1 OFFSET $2
        """
        rows = await self._fetch(query, clamped_limit, clamped_offset)
        result = (rows, total)
        self._leaderboard_cache.set(cache_key, result)
        return result

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
        casino_loss_delta: int = 0,
        potion_delta: int = 0,
        mastermind_cap: int | None = None,
        egg_cap: int | None = None,
        casino_loss_cap: int | None = None,
        potion_cap: int | None = None,
    ) -> asyncpg.Record:
        if (
            mastermind_delta <= 0
            and egg_delta <= 0
            and casino_loss_delta <= 0
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
            current_casino_losses = int(row["sale_progress"])
            current_potions = int(row["potion_progress"])

            new_mastermind = max(0, current_mastermind + max(mastermind_delta, 0))
            new_eggs = max(0, current_eggs + max(egg_delta, 0))
            new_casino_losses = max(
                0, current_casino_losses + max(casino_loss_delta, 0)
            )
            new_potions = max(0, current_potions + max(potion_delta, 0))

            if mastermind_cap is not None:
                new_mastermind = min(new_mastermind, mastermind_cap)
            if egg_cap is not None:
                new_eggs = min(new_eggs, egg_cap)
            if casino_loss_cap is not None:
                new_casino_losses = min(new_casino_losses, casino_loss_cap)
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
                new_casino_losses,
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
        casino_loss_goal: int,
        potion_goal: int,
        rap_goal: int,
        current_rap: int | None = None,
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

            rap_total = current_rap
            if rap_total is None:
                rap_total = await self.get_user_pet_rap(user_id, connection=connection)

            if (
                int(row["mastermind_progress"]) < mastermind_goal
                or int(row["egg_progress"]) < egg_goal
                or int(row["sale_progress"]) < casino_loss_goal
                or int(row["potion_progress"]) < potion_goal
                or rap_total < rap_goal
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

    @staticmethod
    def _rap_values_cte() -> str:
        base_code = """
            CASE
                WHEN up.is_galaxy THEN 'galaxy'
                WHEN up.is_rainbow THEN 'rainbow'
                WHEN up.is_gold THEN 'gold'
                ELSE 'normal'
            END
        """
        return f"""
            WITH pet_values AS (
                SELECT
                    up.user_id,
                    COALESCE(
                        mv_primary.value_in_gems,
                        mv_shiny_base.value_in_gems,
                        mv_demote.value_in_gems,
                        mv_normal_shiny.value_in_gems,
                        mv_normal.value_in_gems,
                        0
                    ) AS market_value
                FROM user_pets AS up
                LEFT JOIN pet_market_values AS mv_primary
                    ON mv_primary.pet_id = up.pet_id
                    AND mv_primary.variant_code = (
                        CASE
                            WHEN up.is_shiny THEN {base_code} || '+shiny'
                            ELSE {base_code}
                        END
                    )
                LEFT JOIN pet_market_values AS mv_shiny_base
                    ON up.is_shiny
                    AND mv_shiny_base.pet_id = up.pet_id
                    AND mv_shiny_base.variant_code = {base_code}
                LEFT JOIN pet_market_values AS mv_demote
                    ON up.is_galaxy
                    AND mv_demote.pet_id = up.pet_id
                    AND mv_demote.variant_code = (
                        CASE
                            WHEN up.is_shiny THEN 'rainbow+shiny'
                            ELSE 'rainbow'
                        END
                    )
                LEFT JOIN pet_market_values AS mv_normal_shiny
                    ON (up.is_rainbow OR up.is_gold OR up.is_galaxy)
                    AND up.is_shiny
                    AND mv_normal_shiny.pet_id = up.pet_id
                    AND mv_normal_shiny.variant_code = 'normal+shiny'
                LEFT JOIN pet_market_values AS mv_normal
                    ON mv_normal.pet_id = up.pet_id
                    AND mv_normal.variant_code = 'normal'
            ),
            rap_values AS (
                SELECT
                    user_id,
                    CASE
                        WHEN {PET_VALUE_SCALE} <= 1 THEN market_value
                        WHEN market_value <= 0 THEN 0
                        ELSE GREATEST(
                            1,
                            CAST(FLOOR(market_value::numeric / {PET_VALUE_SCALE}) AS BIGINT)
                        )
                    END AS rap_value
                FROM pet_values
            )
        """

    async def _compute_pet_rap_totals_map(self) -> defaultdict[int, int]:
        market_values = await self.get_pet_market_values()
        query = """
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
                p.name,
                p.rarity
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
        """

        rap_totals: defaultdict[int, int] = defaultdict(int)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                async for row in connection.cursor(query):
                    user_id = int(row["user_id"])
                    pet_id = int(row["pet_id"])
                    base_income = int(row["base_income_per_hour"])
                    name = str(row.get("name", ""))
                    rarity = str(row.get("rarity", ""))
                    value = self._resolve_market_price(
                        pet_id,
                        is_gold=bool(row.get("is_gold")),
                        is_rainbow=bool(row.get("is_rainbow")),
                        is_galaxy=bool(row.get("is_galaxy")),
                        is_shiny=bool(row.get("is_shiny")),
                        market_values=market_values,
                    )
                    if value <= 0:
                        zone_slug = _PET_ZONE_BY_NAME.get(name.lower(), "exclusif")
                        value = self._fallback_market_value(
                            name=name,
                            rarity=rarity,
                            base_income_per_hour=base_income,
                            is_huge=bool(row.get("is_huge")),
                            zone_slug=zone_slug,
                            is_gold=bool(row.get("is_gold")),
                            is_rainbow=bool(row.get("is_rainbow")),
                            is_galaxy=bool(row.get("is_galaxy")),
                            is_shiny=bool(row.get("is_shiny")),
                        )
                    rap_totals[user_id] += max(0, scale_pet_value(value))

        return rap_totals

    async def _compute_pet_rap_totals(self) -> list[tuple[int, int]]:
        rap_totals = await self._compute_pet_rap_totals_map()
        return sorted(rap_totals.items(), key=lambda item: item[1], reverse=True)

    async def get_pet_rap_leaderboard(self, limit: int) -> list[tuple[int, int]]:
        clamped_limit = max(0, int(limit))
        if clamped_limit == 0:
            return []

        cache_key = ("leaderboard_rap", clamped_limit)
        cached = self._leaderboard_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        await self._ensure_market_values_ready()
        query = f"""
            {self._rap_values_cte()}
            SELECT user_id, SUM(rap_value) AS rap_total
            FROM rap_values
            GROUP BY user_id
            HAVING SUM(rap_value) > 0
            ORDER BY rap_total DESC
            LIMIT $1
        """
        rows = await self._fetch(query, clamped_limit)
        result = [(int(row["user_id"]), int(row["rap_total"])) for row in rows]
        self._leaderboard_cache.set(cache_key, result)
        return result

    async def get_pet_rap_leaderboard_page(
        self, limit: int, offset: int
    ) -> tuple[list[tuple[int, int]], int]:
        clamped_limit = max(0, int(limit))
        clamped_offset = max(0, int(offset))
        cache_key = ("leaderboard_rap_page", clamped_limit, clamped_offset)
        cached = self._leaderboard_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        await self._ensure_market_values_ready()
        total_query = f"""
            {self._rap_values_cte()}
            SELECT COUNT(*) AS total
            FROM (
                SELECT user_id
                FROM rap_values
                GROUP BY user_id
                HAVING SUM(rap_value) > 0
            ) AS totals
        """
        total_row = await self._fetchrow(total_query)
        total = int(total_row["total"]) if total_row else 0
        if clamped_limit == 0 or total == 0:
            result = ([], total)
            self._leaderboard_cache.set(cache_key, result)
            return result

        page_query = f"""
            {self._rap_values_cte()}
            SELECT user_id, SUM(rap_value) AS rap_total
            FROM rap_values
            GROUP BY user_id
            HAVING SUM(rap_value) > 0
            ORDER BY rap_total DESC
            LIMIT $1 OFFSET $2
        """
        rows = await self._fetch(page_query, clamped_limit, clamped_offset)
        result = ([(int(row["user_id"]), int(row["rap_total"])) for row in rows], total)
        self._leaderboard_cache.set(cache_key, result)
        return result

    async def get_user_pet_rap_rank(self, user_id: int) -> Mapping[str, int]:
        await self.ensure_user(user_id)
        sorted_totals = await self._compute_pet_rap_totals()
        rap_total = 0
        rank = 0
        for idx, (entry_user_id, value) in enumerate(sorted_totals, start=1):
            if int(entry_user_id) == user_id:
                rap_total = int(value)
                rank = idx
                break
        if rank == 0:
            rap_total = await self.get_user_pet_rap(user_id)
            rank = 1 + sum(1 for _user_id, value in sorted_totals if value > rap_total)

        total_row = await self.pool.fetchrow("SELECT COUNT(*) AS total FROM users")
        total = int(total_row["total"]) if total_row else 0
        return {"rap_total": int(rap_total), "rank": int(rank), "total": int(total)}

    async def get_user_pet_rap(
        self,
        user_id: int,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> int:
        market_values = await self.get_pet_market_values()
        query = """
            SELECT
                up.pet_id,
                up.is_gold,
                up.is_huge,
                up.is_rainbow,
                up.is_galaxy,
                up.is_shiny,
                up.huge_level,
                p.base_income_per_hour,
                p.name,
                p.rarity
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE up.user_id = $1
        """
        fetcher = connection.fetch if connection is not None else self.pool.fetch
        rows = await fetcher(query, user_id)

        rap_total = 0
        for row in rows:
            pet_id = int(row["pet_id"])
            base_income = int(row["base_income_per_hour"])
            name = str(row.get("name", ""))
            rarity = str(row.get("rarity", ""))
            value = self._resolve_market_price(
                pet_id,
                is_gold=bool(row.get("is_gold")),
                is_rainbow=bool(row.get("is_rainbow")),
                is_galaxy=bool(row.get("is_galaxy")),
                is_shiny=bool(row.get("is_shiny")),
                market_values=market_values,
            )
            if value <= 0:
                zone_slug = _PET_ZONE_BY_NAME.get(name.lower(), "exclusif")
                value = self._fallback_market_value(
                    name=name,
                    rarity=rarity,
                    base_income_per_hour=base_income,
                    is_huge=bool(row.get("is_huge")),
                    zone_slug=zone_slug,
                    is_gold=bool(row.get("is_gold")),
                    is_rainbow=bool(row.get("is_rainbow")),
                    is_galaxy=bool(row.get("is_galaxy")),
                    is_shiny=bool(row.get("is_shiny")),
                )
            rap_total += max(0, scale_pet_value(value))
        return rap_total

    async def get_user_best_pet_value(
        self,
        user_id: int,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> tuple[str | None, int]:
        market_values = await self.get_pet_market_values()
        query = """
            SELECT
                up.pet_id,
                up.is_gold,
                up.is_huge,
                up.is_rainbow,
                up.is_galaxy,
                up.is_shiny,
                up.huge_level,
                p.base_income_per_hour,
                p.name,
                p.rarity
            FROM user_pets AS up
            JOIN pets AS p ON p.pet_id = up.pet_id
            WHERE up.user_id = $1
        """
        fetcher = connection.fetch if connection is not None else self.pool.fetch
        rows = await fetcher(query, user_id)

        best_value = 0
        best_name: str | None = None
        for row in rows:
            pet_id = int(row["pet_id"])
            base_income = int(row["base_income_per_hour"])
            name = str(row.get("name", ""))
            rarity = str(row.get("rarity", ""))
            value = self._resolve_market_price(
                pet_id,
                is_gold=bool(row.get("is_gold")),
                is_rainbow=bool(row.get("is_rainbow")),
                is_galaxy=bool(row.get("is_galaxy")),
                is_shiny=bool(row.get("is_shiny")),
                market_values=market_values,
            )
            if value <= 0:
                zone_slug = _PET_ZONE_BY_NAME.get(name.lower(), "exclusif")
                value = self._fallback_market_value(
                    name=name,
                    rarity=rarity,
                    base_income_per_hour=base_income,
                    is_huge=bool(row.get("is_huge")),
                    zone_slug=zone_slug,
                    is_gold=bool(row.get("is_gold")),
                    is_rainbow=bool(row.get("is_rainbow")),
                    is_galaxy=bool(row.get("is_galaxy")),
                    is_shiny=bool(row.get("is_shiny")),
                )
            value = max(0, scale_pet_value(value))
            if value > best_value:
                best_value = value
                best_name = name

        return best_name, best_value

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
                reference_income = reference if reference > 0 else base_income
                income_value = compute_huge_income(reference_income, multiplier)
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
            income_totals[user_id] += scale_pet_value(income_value)

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

    async def get_rebirth_count(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        value = await self.pool.fetchval(
            "SELECT rebirth_count FROM users WHERE user_id = $1",
            user_id,
        )
        return int(value or 0)

    async def perform_rebirth(self, user_id: int) -> int:
        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            user_row = await connection.fetchrow(
                "SELECT balance, rebirth_count FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if user_row is None:
                raise DatabaseError("Utilisateur introuvable pour le rebirth")

            current_rebirths = int(user_row.get("rebirth_count", 0))
            if current_rebirths >= 2:
                raise DatabaseError("Limite de rebirth atteinte pour cet utilisateur")

            await connection.execute(
                """
                UPDATE users
                SET
                    balance = 0,
                    rebirth_count = rebirth_count + 1,
                    active_potion_slug = NULL,
                    active_potion_expires_at = NULL,
                    pet_booster_multiplier = 1,
                    pet_booster_expires_at = NULL,
                    pet_booster_activated_at = NULL
                WHERE user_id = $1
                """,
                user_id,
            )
            await connection.execute(
                "DELETE FROM user_potions WHERE user_id = $1",
                user_id,
            )
            await connection.execute(
                "DELETE FROM raffle_tickets WHERE user_id = $1",
                user_id,
            )
            await connection.execute(
                "DELETE FROM user_zones WHERE user_id = $1",
                user_id,
            )
            await connection.execute(
                "DELETE FROM user_pets WHERE user_id = $1 AND NOT is_huge",
                user_id,
            )
            await connection.execute(
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

        return int(user_row.get("rebirth_count", 0)) + 1

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
        cost: int = 0,
    ) -> asyncpg.Record:
        await self.ensure_user(user_id)
        normalized_ids = self._coerce_positive_ids(
            user_pet_ids, field="identifiants de pet"
        )
        unique_ids = set(normalized_ids)
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

            if cost > 0:
                balance_row = await connection.fetchrow(
                    "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                    user_id,
                )
                if balance_row is None:
                    raise DatabaseError("Utilisateur introuvable lors de la fusion.")
                before_balance = int(balance_row["balance"])
                if before_balance < cost:
                    raise InsufficientBalanceError("Solde insuffisant pour la fusion.")
                after_balance = before_balance - cost
                await connection.execute(
                    "UPDATE users SET balance = $1 WHERE user_id = $2",
                    after_balance,
                    user_id,
                )
                await self.record_transaction(
                    connection=connection,
                    user_id=user_id,
                    transaction_type="pet_fuse",
                    currency="pb",
                    amount=-cost,
                    balance_before=before_balance,
                    balance_after=after_balance,
                    description="Coût de fusion",
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

    async def get_daycare_last_claim(self, user_id: int) -> datetime | None:
        await self.ensure_user(user_id)
        return await self.pool.fetchval(
            "SELECT daycare_last_claim FROM users WHERE user_id = $1",
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

    async def is_pet_in_daycare(self, user_id: int, user_pet_id: int) -> bool:
        if user_pet_id <= 0:
            return False
        row = await self.pool.fetchval(
            """
            SELECT 1
            FROM user_daycare
            WHERE user_id = $1 AND user_pet_id = $2
            """,
            user_id,
            user_pet_id,
        )
        return bool(row)

    async def get_daycare_pets(self, user_id: int) -> Sequence[asyncpg.Record]:
        await self.ensure_user(user_id)
        return await self.pool.fetch(
            """
            SELECT
                ud.user_pet_id,
                ud.deposited_at,
                up.is_huge,
                up.is_gold,
                up.is_rainbow,
                up.is_galaxy,
                up.is_shiny,
                up.huge_level,
                up.huge_xp,
                p.pet_id,
                p.name,
                p.rarity,
                p.image_url,
                p.base_income_per_hour
            FROM user_daycare AS ud
            JOIN user_pets AS up ON ud.user_pet_id = up.id
            JOIN pets AS p ON up.pet_id = p.pet_id
            WHERE ud.user_id = $1
            ORDER BY ud.deposited_at ASC, ud.user_pet_id ASC
            """,
            user_id,
        )

    async def get_daycare_pet_count(
        self, user_id: int, *, connection: asyncpg.Connection | None = None
    ) -> int:
        executor = connection or self.pool
        if executor is None:
            raise DatabaseError("La connexion à la base de données n'est pas initialisée")
        count = await executor.fetchval(
            "SELECT COUNT(*) FROM user_daycare WHERE user_id = $1",
            user_id,
        )
        return int(count or 0)

    async def deposit_daycare_pets(
        self, user_id: int, user_pet_ids: Sequence[int]
    ) -> Sequence[asyncpg.Record]:
        if not user_pet_ids:
            raise DatabaseError("Tu dois fournir au moins un pet à déposer.")

        await self.ensure_user(user_id)
        unique_ids = list(dict.fromkeys(int(pet_id) for pet_id in user_pet_ids if pet_id))
        if not unique_ids:
            raise DatabaseError("Aucun pet valide à déposer.")

        async with self.transaction() as connection:
            existing_count = await self.get_daycare_pet_count(user_id, connection=connection)
            if existing_count >= DAYCARE_MAX_PETS:
                raise DatabaseError("Ta garderie est déjà pleine.")
            if existing_count + len(unique_ids) > DAYCARE_MAX_PETS:
                remaining = max(0, DAYCARE_MAX_PETS - existing_count)
                raise DatabaseError(
                    f"Tu ne peux déposer que {remaining} pet{'s' if remaining > 1 else ''} en plus."
                )

            rows = await connection.fetch(
                """
                SELECT id, is_active, is_huge, on_market
                FROM user_pets
                WHERE user_id = $1 AND id = ANY($2::INT[])
                FOR UPDATE
                """,
                user_id,
                unique_ids,
            )
            if len(rows) < len(unique_ids):
                raise DatabaseError("Tu dois sélectionner des pets qui t'appartiennent.")

            daycare_conflicts = await connection.fetch(
                """
                SELECT user_pet_id
                FROM user_daycare
                WHERE user_id = $1 AND user_pet_id = ANY($2::INT[])
                """,
                user_id,
                unique_ids,
            )
            if daycare_conflicts:
                raise DatabaseError("Un des pets sélectionnés est déjà à la garderie.")

            for row in rows:
                if bool(row.get("is_huge")):
                    raise DatabaseError("Les Huge pets ne peuvent pas aller à la garderie.")
                if bool(row.get("is_active")):
                    raise DatabaseError("Retire tes pets actifs avant de les déposer.")
                if bool(row.get("on_market")):
                    raise DatabaseError("Retire les pets en vente avant de les déposer.")

            await connection.executemany(
                """
                INSERT INTO user_daycare (user_id, user_pet_id)
                VALUES ($1, $2)
                """,
                [(user_id, pet_id) for pet_id in unique_ids],
            )
            await connection.execute(
                """
                UPDATE users
                SET daycare_last_claim = COALESCE(daycare_last_claim, NOW())
                WHERE user_id = $1
                """,
                user_id,
            )

        return await self.get_daycare_pets(user_id)

    async def withdraw_daycare_pets(
        self, user_id: int, user_pet_ids: Sequence[int] | None = None
    ) -> int:
        await self.ensure_user(user_id)
        ids = [int(pet_id) for pet_id in (user_pet_ids or []) if pet_id]
        async with self.transaction() as connection:
            if not ids:
                deleted = await connection.execute(
                    "DELETE FROM user_daycare WHERE user_id = $1",
                    user_id,
                )
            else:
                deleted = await connection.execute(
                    """
                    DELETE FROM user_daycare
                    WHERE user_id = $1 AND user_pet_id = ANY($2::INT[])
                    """,
                    user_id,
                    ids,
                )
        return int(deleted.split()[-1]) if isinstance(deleted, str) else 0

    async def claim_daycare_gems(
        self, user_id: int
    ) -> tuple[int, float, int, int, int]:
        await self.ensure_user(user_id)
        now = datetime.now(timezone.utc)

        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT gems, daycare_last_claim FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if row is None:
                raise DatabaseError("Utilisateur introuvable.")
            gems_before = int(row.get("gems") or 0)
            last_claim = row.get("daycare_last_claim")
            if not isinstance(last_claim, datetime):
                last_claim = now

            pet_count = await self.get_daycare_pet_count(user_id, connection=connection)
            elapsed_seconds = max(0.0, (now - last_claim).total_seconds())
            elapsed_hours = elapsed_seconds / 3600 if elapsed_seconds > 0 else 0.0

            reward = 0
            if pet_count > 0 and elapsed_hours > 0:
                reward = int(round(pet_count * elapsed_hours * DAYCARE_GEM_PER_PET_HOUR))
                if DAYCARE_GEM_MAX > 0:
                    reward = min(reward, DAYCARE_GEM_MAX)

            gems_after = gems_before
            if reward > 0:
                gems_after = gems_before + reward
                await connection.execute(
                    "UPDATE users SET gems = $1 WHERE user_id = $2",
                    gems_after,
                    user_id,
                )
                await self.record_transaction(
                    connection=connection,
                    user_id=user_id,
                    transaction_type="daycare_gems",
                    currency="gem",
                    amount=reward,
                    balance_before=gems_before,
                    balance_after=gems_after,
                    description="Gemmes récoltées via la garderie",
                )

            await connection.execute(
                "UPDATE users SET daycare_last_claim = $1 WHERE user_id = $2",
                now,
                user_id,
            )

        return reward, elapsed_hours, pet_count, gems_before, gems_after

    async def sell_user_pet_for_gems(
        self, user_id: int, user_pet_id: int, price: int
    ) -> tuple[str, int, int, int]:
        if price <= 0:
            raise ValueError("Le prix de vente doit être positif.")

        await self.ensure_user(user_id)
        async with self.transaction() as connection:
            pet_row = await connection.fetchrow(
                """
                SELECT id, user_id, is_active, on_market
                FROM user_pets
                WHERE id = $1
                FOR UPDATE
                """,
                user_pet_id,
            )
            if pet_row is None:
                return "missing", 0, 0, 0
            if int(pet_row.get("user_id") or 0) != user_id:
                return "forbidden", 0, 0, 0
            if bool(pet_row.get("is_active")):
                return "active", 0, 0, 0
            if bool(pet_row.get("on_market")):
                return "on_market", 0, 0, 0
            daycare_conflict = await connection.fetchval(
                "SELECT 1 FROM user_daycare WHERE user_id = $1 AND user_pet_id = $2",
                user_id,
                user_pet_id,
            )
            if daycare_conflict:
                return "daycare", 0, 0, 0

            balance_row = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                user_id,
            )
            if balance_row is None:
                raise DatabaseError("Utilisateur introuvable lors de la vente de pet.")

            before_balance = int(balance_row.get("gems") or 0)
            after_balance = before_balance + price

            await connection.execute(
                "DELETE FROM user_pets WHERE id = $1",
                user_pet_id,
            )
            await connection.execute(
                "UPDATE users SET gems = $2 WHERE user_id = $1",
                user_id,
                after_balance,
            )
            await self.record_transaction(
                user_id=user_id,
                transaction_type="pet_sell",
                currency="gem",
                amount=price,
                balance_before=before_balance,
                balance_after=after_balance,
                description="Revente de pet",
                connection=connection,
            )

        return "sold", price, before_balance, after_balance

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
            daycare_conflict = await connection.fetchval(
                "SELECT 1 FROM user_daycare WHERE user_id = $1 AND user_pet_id = $2",
                source_user_id,
                user_pet_id,
            )
            if daycare_conflict:
                raise DatabaseError("Ce pet est actuellement à la garderie.")

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
        include_daycare: bool = True,
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
        if not include_daycare:
            where_clauses.append(
                "NOT EXISTS (SELECT 1 FROM user_daycare ud WHERE ud.user_pet_id = up.id)"
            )

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
                LEAST(
                    CAST(p.base_income_per_hour AS NUMERIC)
                    * CASE
                        WHEN up.is_rainbow THEN $3::NUMERIC
                        WHEN up.is_gold THEN $2::NUMERIC
                        ELSE 1
                    END
                    * CASE WHEN up.is_galaxy THEN $4::NUMERIC ELSE 1 END
                    * CASE WHEN up.is_shiny THEN $5::NUMERIC ELSE 1 END,
                    9223372036854775807
                )
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
            GALAXY_PET_MULTIPLIER,
            SHINY_PET_MULTIPLIER,
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
                grade_row = await connection.fetchrow(
                    "SELECT grade_level FROM user_grades WHERE user_id = $1",
                    user_id,
                )
                extra_row = await connection.fetchrow(
                    "SELECT extra_pet_slots FROM users WHERE user_id = $1",
                    user_id,
                )
                grade_level = int(grade_row["grade_level"]) if grade_row else 0
                extra_slots = int(extra_row.get("extra_pet_slots") or 0) if extra_row else 0
                return None, 0, self._compute_pet_slot_limit(grade_level, extra_slots)

            if bool(pet_row["is_active"]):
                raise DatabaseError("Ce pet est déjà équipé.")

            if bool(pet_row.get("on_market")):
                raise DatabaseError(
                    "Ce pet est actuellement en vente sur ton stand. Retire-le avant de l'équiper."
                )
            daycare_conflict = await connection.fetchval(
                "SELECT 1 FROM user_daycare WHERE user_id = $1 AND user_pet_id = $2",
                user_id,
                user_pet_id,
            )
            if daycare_conflict:
                raise DatabaseError(
                    "Ce pet est actuellement à la garderie. Retire-le avant de l'équiper."
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
            extra_row = await connection.fetchrow(
                "SELECT extra_pet_slots FROM users WHERE user_id = $1",
                user_id,
            )
            extra_slots = int(extra_row.get("extra_pet_slots") or 0) if extra_row else 0
            max_slots = self._compute_pet_slot_limit(grade_level, extra_slots)

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
                grade_row = await connection.fetchrow(
                    "SELECT grade_level FROM user_grades WHERE user_id = $1",
                    user_id,
                )
                extra_row = await connection.fetchrow(
                    "SELECT extra_pet_slots FROM users WHERE user_id = $1",
                    user_id,
                )
                grade_level = int(grade_row["grade_level"]) if grade_row else 0
                extra_slots = int(extra_row.get("extra_pet_slots") or 0) if extra_row else 0
                return None, 0, self._compute_pet_slot_limit(grade_level, extra_slots)

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
            extra_row = await connection.fetchrow(
                "SELECT extra_pet_slots FROM users WHERE user_id = $1",
                user_id,
            )
            extra_slots = int(extra_row.get("extra_pet_slots") or 0) if extra_row else 0
            max_slots = self._compute_pet_slot_limit(grade_level, extra_slots)

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
            daycare_conflict = await connection.fetchval(
                "SELECT 1 FROM user_daycare WHERE user_id = $1 AND user_pet_id = $2",
                user_id,
                pet_in_id,
            )
            if daycare_conflict:
                raise DatabaseError(
                    "Le pet que tu veux équiper est actuellement à la garderie. Retire-le avant de l'équiper."
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
            extra_row = await connection.fetchrow(
                "SELECT extra_pet_slots FROM users WHERE user_id = $1",
                user_id,
            )
            extra_slots = int(extra_row.get("extra_pet_slots") or 0) if extra_row else 0
            max_slots = self._compute_pet_slot_limit(grade_level, extra_slots)

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
            grade_row = await connection.fetchrow(
                "SELECT grade_level FROM user_grades WHERE user_id = $1",
                user_id,
            )
            extra_row = await connection.fetchrow(
                "SELECT extra_pet_slots FROM users WHERE user_id = $1",
                user_id,
            )
            grade_level = int(grade_row["grade_level"]) if grade_row else 0
            extra_slots = int(extra_row.get("extra_pet_slots") or 0) if extra_row else 0
            max_slots = self._compute_pet_slot_limit(grade_level, extra_slots)
            if pet_row is None:
                return None, False, max_slots

            currently_active = bool(pet_row["is_active"])
            if not currently_active and bool(pet_row.get("on_market")):
                raise DatabaseError(
                    "Ce pet est actuellement en vente sur ton stand. Retire-le avant de l'équiper."
                )
            if not currently_active:
                daycare_conflict = await connection.fetchval(
                    "SELECT 1 FROM user_daycare WHERE user_id = $1 AND user_pet_id = $2",
                    user_id,
                    user_pet_id,
                )
                if daycare_conflict:
                    raise DatabaseError(
                        "Ce pet est actuellement à la garderie. Retire-le avant de l'équiper."
                    )
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
        dict[str, float | int],
    ]:
        await self.ensure_user(user_id)
        enchantments = await self.get_enchantment_powers(user_id)
        # FIX: Fetch best non-huge income outside of the critical transaction to limit lock duration.
        best_non_huge_income = await self.get_best_non_huge_income(user_id)
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
                    u.gems,
                    u.rebirth_count,
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
                FOR UPDATE OF up, u
                """,
                user_id,
            )

            if not rows:
                return self._build_empty_claim_result([], 0.0)

            first_row = rows[0]
            now = datetime.now(timezone.utc)
            last_claim: Optional[datetime] = first_row["pet_last_claim"]
            elapsed_seconds = (now - last_claim).total_seconds() if last_claim else 0.0
            gems_before = int(first_row.get("gems", 0))
            if elapsed_seconds <= 0:
                await connection.execute(
                    "UPDATE users SET pet_last_claim = $1 WHERE user_id = $2",
                    now,
                    user_id,
                )
                return self._build_empty_claim_result(rows, 0.0)

            booster_multiplier = float(first_row.get("pet_booster_multiplier") or 1.0)
            booster_state = _BoosterState(
                multiplier=booster_multiplier,
                activated_at=first_row.get("pet_booster_activated_at"),
                expires_at=first_row.get("pet_booster_expires_at"),
            )
            potion_multiplier, potion_definition, potion_remaining, potion_should_clear = (
                self._evaluate_potion_state(
                    first_row.get("active_potion_slug"),
                    first_row.get("active_potion_expires_at"),
                    now,
                )
            )

            effective_incomes = [
                self._compute_pet_income(row, best_non_huge_income) for row in rows
            ]
            hourly_income = sum(effective_incomes)
            if hourly_income <= 0:
                await connection.execute(
                    "UPDATE users SET pet_last_claim = $1 WHERE user_id = $2",
                    now,
                    user_id,
                )
                return self._build_empty_claim_result(rows, elapsed_seconds)

            elapsed_hours = elapsed_seconds / 3600
            base_income_int = int(hourly_income * elapsed_hours)
            booster_result = booster_state.evaluate(
                now=now, last_claim=last_claim, hourly_income=hourly_income
            )

            raw_income = base_income_int + booster_result.extra_income
            if raw_income <= 0:
                return self._build_empty_claim_result(rows, elapsed_seconds)

            priss_power = int(enchantments.get("prissbucks", 0))
            priss_multiplier = compute_prissbucks_multiplier(priss_power)
            enchantment_info: dict[str, object] = {}
            if priss_multiplier > 1.0:
                boosted_by_enchant = int(round(raw_income * priss_multiplier))
                bonus = max(0, boosted_by_enchant - raw_income)
                raw_income = boosted_by_enchant
                enchantment_info = {
                    "slug": "prissbucks",
                    "power": priss_power,
                    "multiplier": priss_multiplier,
                    "bonus": bonus,
                }

            potion_bonus_amount = 0
            if potion_multiplier > 1.0:
                boosted_by_potion = int(round(raw_income * potion_multiplier))
                potion_bonus_amount = max(0, boosted_by_potion - raw_income)
                raw_income = boosted_by_potion

            base_consumed_seconds = (
                int((base_income_int / hourly_income) * 3600) if hourly_income else 0
            )
            consumed_seconds = max(
                base_consumed_seconds,
                booster_result.consumed_seconds(elapsed_seconds),
            )
            consumed_seconds = min(int(elapsed_seconds), consumed_seconds)

            new_claim_time = now if last_claim is None else last_claim + timedelta(seconds=consumed_seconds)
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

            rebirth_count = int(first_row.get("rebirth_count") or 0)
            income, rebirth_bonus = self._apply_rebirth_multiplier(
                boosted_income, rebirth_count
            )
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
            if rebirth_bonus > 0:
                description += " + bonus rebirth"
            await self.record_transaction(
                connection=connection,
                user_id=user_id,
                transaction_type="pet_income",
                amount=income,
                balance_before=before_balance,
                balance_after=after_balance,
                description=description,
            )

            farm_rewards: dict[str, object] = {
                "gems": 0,
                "potions": {},
                "tickets": 0,
                "enchantments": [],
            }
            pet_count = len(rows)
            time_factor = max(
                PET_FARM_TIME_FACTOR_MIN, min(PET_FARM_TIME_FACTOR_MAX, elapsed_hours)
            )

            gem_reward = int(round(pet_count * time_factor * PET_FARM_GEM_PER_PET_HOUR))
            if PET_FARM_GEM_MAX > 0:
                gem_reward = min(gem_reward, PET_FARM_GEM_MAX)
            if gem_reward > 0:
                variance = max(1, int(round(pet_count * PET_FARM_GEM_VARIANCE_PER_PET)))
                gem_reward = random.randint(max(0, gem_reward - variance), gem_reward + variance)
                gems_after = gems_before + gem_reward
                await connection.execute(
                    "UPDATE users SET gems = $1 WHERE user_id = $2",
                    gems_after,
                    user_id,
                )
                await self.record_transaction(
                    connection=connection,
                    user_id=user_id,
                    transaction_type="pet_farm_gems",
                    currency="gem",
                    amount=gem_reward,
                    balance_before=gems_before,
                    balance_after=gems_after,
                    description="Gemmes récoltées par les pets",
                )
                gems_before = gems_after
                farm_rewards["gems"] = gem_reward

            ticket_chance = min(
                PET_FARM_TICKET_MAX_CHANCE,
                (PET_FARM_TICKET_BASE + PET_FARM_TICKET_PER_PET * pet_count)
                * min(PET_FARM_TIME_FACTOR_MAX, elapsed_hours or 1.0),
            )
            if random.random() < ticket_chance:
                await self.add_raffle_tickets(user_id, amount=1, connection=connection)
                farm_rewards["tickets"] = 1

            potion_chance = min(
                PET_FARM_POTION_MAX_CHANCE,
                (PET_FARM_POTION_BASE + PET_FARM_POTION_PER_PET * pet_count)
                * min(PET_FARM_TIME_FACTOR_MAX, elapsed_hours or 1.0),
            )
            if random.random() < potion_chance:
                available_potions = list(POTION_DEFINITION_MAP.values())
                if available_potions:
                    potion = random.choice(available_potions)
                    await self.add_user_potion(
                        user_id,
                        potion.slug,
                        quantity=1,
                        connection=connection,
                    )
                    farm_rewards["potions"] = {potion.slug: 1}

            enchant_chance = min(
                PET_FARM_ENCHANT_MAX_CHANCE,
                PET_FARM_ENCHANT_BASE + PET_FARM_ENCHANT_PER_PET * pet_count,
            )
            if random.random() < enchant_chance:
                definition = pick_random_enchantment()
                power = roll_enchantment_power()
                await self.add_user_enchantment(
                    user_id,
                    definition.slug,
                    power=power,
                    quantity=1,
                    connection=connection,
                )
                farm_rewards["enchantments"] = [
                    {"slug": definition.slug, "power": power}
                ]

            booster_expires = first_row.get("pet_booster_expires_at")
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

            progress_updates = self._calculate_huge_progress(
                rows, income, effective_incomes, hourly_income, elapsed_hours
            )
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
                    "extra": float(max(0, booster_result.extra_income)),
                    "remaining_seconds": float(max(0.0, booster_result.remaining_seconds)),
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

            rebirth_info = self._build_rebirth_info(rebirth_count, rebirth_bonus)

            return (
                income,
                rows,
                elapsed_seconds,
                booster_info,
                clan_info,
                progress_updates,
                potion_info,
                enchantment_info,
                farm_rewards,
                rebirth_info,
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
            now = datetime.now(timezone.utc)
            user_row = await connection.fetchrow(
                """
                SELECT active_potion_slug, active_potion_expires_at
                FROM users
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )

            potion_multiplier = 1.0
            potion_slug = str(user_row.get("active_potion_slug") or "") if user_row else ""
            potion_expires_at = user_row.get("active_potion_expires_at") if user_row else None
            if potion_slug and isinstance(potion_expires_at, datetime):
                if potion_expires_at > now:
                    potion_definition = POTION_DEFINITION_MAP.get(potion_slug)
                    if potion_definition and potion_definition.effect_type == "mastery_xp":
                        potion_multiplier += float(potion_definition.effect_value)
                else:
                    await self.clear_active_potion(user_id, connection=connection)
            elif potion_slug:
                await self.clear_active_potion(user_id, connection=connection)

            adjusted_amount = int(round(amount * potion_multiplier))

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
            experience += int(adjusted_amount)
            experience = max(0, min(experience, sys.maxsize))
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

    async def get_unique_pet_count(self, user_id: int) -> int:
        """Compte le nombre de pets distincts déjà découverts par un joueur."""

        value = await self.pool.fetchval(
            "SELECT COUNT(DISTINCT pet_id) FROM user_pets WHERE user_id = $1",
            int(user_id),
        )
        return int(value or 0)

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

    @classmethod
    def _market_variant_candidates(
        cls,
        *,
        is_gold: bool,
        is_rainbow: bool,
        is_galaxy: bool,
        is_shiny: bool,
    ) -> tuple[str, ...]:
        primary = cls._build_variant_code(is_gold, is_rainbow, is_galaxy, is_shiny)
        candidates: list[str] = [primary]
        if is_shiny:
            candidates.append(cls._build_variant_code(is_gold, is_rainbow, is_galaxy, False))
        if is_galaxy:
            candidates.append(cls._build_variant_code(is_gold, is_rainbow, False, is_shiny))
        if is_rainbow or is_gold:
            candidates.append(cls._build_variant_code(False, False, False, is_shiny))
        candidates.append(cls._build_variant_code(False, False, False, False))

        seen: set[str] = set()
        ordered: list[str] = []
        for code in candidates:
            if code not in seen:
                seen.add(code)
                ordered.append(code)
        return tuple(ordered)

    @classmethod
    def _resolve_market_price(
        cls,
        pet_id: int,
        *,
        is_gold: bool,
        is_rainbow: bool,
        is_galaxy: bool,
        is_shiny: bool,
        market_values: Mapping[tuple[int, str], int],
    ) -> int:
        for code in cls._market_variant_candidates(
            is_gold=is_gold,
            is_rainbow=is_rainbow,
            is_galaxy=is_galaxy,
            is_shiny=is_shiny,
        ):
            key = (pet_id, code)
            if key in market_values:
                return int(market_values[key])
        return 0

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
        async with self.transaction() as connection:
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
            base_value = self.compute_market_value_gems(
                {
                    "name": pet_name,
                    "rarity": rarity,
                    "base_income_per_hour": base_income,
                    "is_huge": is_huge,
                },
                config=MARKET_VALUE_CONFIG,
                zone_slug=zone_slug,
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
        """Retourne la dernière valeur marché enregistrée pour chaque variante."""

        rows = await self.pool.fetch(
            """
            SELECT pet_id, variant_code, value_in_gems
            FROM pet_market_values
            """
        )
        if rows:
            return {
                (int(row["pet_id"]), str(row["variant_code"])): max(
                    0, int(row["value_in_gems"])
                )
                for row in rows
            }

        return await self._get_trade_history_market_values()

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

        value = base_value * power_factor * size_multiplier * max(0.0, float(variant_multiplier))
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
            base_value = self._compute_pet_base_market_value(
                name=name,
                rarity=rarity,
                base_income_per_hour=base_income,
                zone_slug=zone_slug,
                is_huge=is_huge,
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
        return len(values_to_store)

    async def reset_rich_users_gems(
        self,
        *,
        threshold: int = 1_000_000,
        new_amount: int = 100_000,
    ) -> Dict[str, Any]:
        """Reset les gemmes des utilisateurs riches à un montant fixe."""

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
        await self.ensure_user(user_id)
        query = """
            SELECT
                id,
                transaction_type,
                currency,
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
        try:
            return await self.pool.fetch(query, user_id, limit)
        except asyncpg.exceptions.UndefinedTableError:
            logger.warning("Transactions table missing — recreating before retry")
            await self._ensure_transactions_table(self.pool)
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
                raise InsufficientBalanceError("Solde de gemmes insuffisant pour cet achat.")

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
    ) -> asyncpg.Record:
        if item_type not in {"ticket", "potion", "role"}:
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
            if item_type == "ticket":
                slug = "raffle_ticket"
                remaining = await self.remove_raffle_tickets(
                    seller_id, amount=quantity, connection=connection
                )
                if remaining is None:
                    raise DatabaseError(
                        "Tu n'as pas assez de tickets de tombola pour cette mise en vente."
                    )
            elif item_type == "potion":
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
            elif item_type == "potion":
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
                raise InsufficientBalanceError("Solde de gemmes insuffisant pour cet achat.")

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

            if item_type == "ticket":
                await self.add_raffle_tickets(
                    buyer_id, amount=quantity, connection=connection
                )
            elif item_type == "potion":
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
    # Enchères
    # ------------------------------------------------------------------
    async def _grant_auction_item(
        self,
        connection: asyncpg.Connection,
        auction: Mapping[str, object],
        winner_id: int,
    ) -> None:
        item_type = str(auction.get("item_type"))
        quantity = int(auction.get("quantity", 1))
        slug = auction.get("item_slug")
        power = auction.get("item_power")
        if item_type == "pet":
            user_pet_id = int(auction.get("user_pet_id") or 0)
            if user_pet_id <= 0:
                raise DatabaseError("Pet introuvable pour cette enchère.")
            await connection.execute(
                """
                UPDATE user_pets
                SET user_id = $1, is_active = FALSE, on_market = FALSE
                WHERE id = $2
                """,
                winner_id,
                user_pet_id,
            )
        elif item_type == "ticket":
            await self.add_raffle_tickets(
                winner_id, amount=quantity, connection=connection
            )
        elif item_type == "potion":
            if not slug:
                raise DatabaseError("Potion inconnue pour cette enchère.")
            await self.add_user_potion(
                winner_id,
                str(slug),
                quantity=quantity,
                connection=connection,
            )
        elif item_type == "enchantment":
            if not slug or power is None:
                raise DatabaseError("Enchantement invalide.")
            await self.add_user_enchantment(
                winner_id,
                str(slug),
                power=int(power),
                quantity=quantity,
                connection=connection,
            )

    async def _release_auction_item(
        self,
        connection: asyncpg.Connection,
        auction: Mapping[str, object],
    ) -> None:
        item_type = str(auction.get("item_type"))
        quantity = int(auction.get("quantity", 1))
        slug = auction.get("item_slug")
        power = auction.get("item_power")
        seller_id = int(auction.get("seller_id") or 0)
        if item_type == "pet":
            user_pet_id = int(auction.get("user_pet_id") or 0)
            if user_pet_id > 0:
                await connection.execute(
                    "UPDATE user_pets SET on_market = FALSE WHERE id = $1",
                    user_pet_id,
                )
        elif item_type == "ticket":
            await self.add_raffle_tickets(
                seller_id, amount=quantity, connection=connection
            )
        elif item_type == "potion":
            if slug:
                await self.add_user_potion(
                    seller_id,
                    str(slug),
                    quantity=quantity,
                    connection=connection,
                )
        elif item_type == "enchantment":
            if slug and power is not None:
                await self.add_user_enchantment(
                    seller_id,
                    str(slug),
                    power=int(power),
                    quantity=quantity,
                    connection=connection,
                )

    async def _credit_seller_from_auction(
        self,
        connection: asyncpg.Connection,
        auction: Mapping[str, object],
    ) -> None:
        price = int(auction.get("current_bid") or 0)
        if price <= 0:
            return
        seller_id = int(auction.get("seller_id") or 0)
        buyer_id = int(auction.get("current_bidder_id") or 0)
        seller_row = await connection.fetchrow(
            "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
            seller_id,
        )
        if seller_row is None:
            raise DatabaseError("Vendeur introuvable pour l'enchère.")
        before = int(seller_row.get("gems") or 0)
        after = before + price
        await connection.execute(
            "UPDATE users SET gems = $1 WHERE user_id = $2",
            after,
            seller_id,
        )
        await self.record_transaction(
            connection=connection,
            user_id=seller_id,
            transaction_type="auction_sale",
            currency="gem",
            amount=price,
            balance_before=before,
            balance_after=after,
            description=f"Enchère #{int(auction.get('id') or 0)}",
            related_user_id=buyer_id or None,
        )

    async def _finalize_auction_row(
        self, connection: asyncpg.Connection, auction: Mapping[str, object]
    ) -> asyncpg.Record:
        auction_id = int(auction.get("id") or 0)
        has_bidder = int(auction.get("current_bid") or 0) > 0 and auction.get(
            "current_bidder_id"
        )
        if has_bidder:
            updated = await connection.fetchrow(
                """
                UPDATE plaza_auctions
                SET status = 'sold', buyer_id = current_bidder_id, completed_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                auction_id,
            )
            await self._grant_auction_item(
                connection, updated, int(updated.get("current_bidder_id") or 0)
            )
            await self._credit_seller_from_auction(connection, updated)
            return updated

        await self._release_auction_item(connection, auction)
        return await connection.fetchrow(
            """
            UPDATE plaza_auctions
            SET status = 'cancelled', completed_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            auction_id,
        )

    async def complete_expired_auctions(self, limit: int = 10) -> int:
        async with self.transaction() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM plaza_auctions
                WHERE status = 'active' AND ends_at <= NOW()
                FOR UPDATE SKIP LOCKED
                LIMIT $1
                """,
                limit,
            )
            for row in rows:
                await self._finalize_auction_row(connection, row)
        return len(rows)

    async def list_active_auctions(
        self, *, limit: int = 25
    ) -> Sequence[asyncpg.Record]:
        await self.complete_expired_auctions(limit=limit)
        return await self.pool.fetch(
            """
            SELECT pa.*, p.name AS pet_name, up.is_gold, up.is_rainbow, up.is_shiny
            FROM plaza_auctions AS pa
            LEFT JOIN user_pets AS up ON pa.user_pet_id = up.id
            LEFT JOIN pets AS p ON up.pet_id = p.pet_id
            WHERE pa.status = 'active'
            ORDER BY pa.ends_at ASC
            LIMIT $1
            """,
            limit,
        )

    async def get_auction_listing(self, auction_id: int) -> Optional[asyncpg.Record]:
        return await self.pool.fetchrow(
            """
            SELECT pa.*, p.name AS pet_name, up.is_gold, up.is_rainbow, up.is_shiny
            FROM plaza_auctions AS pa
            LEFT JOIN user_pets AS up ON pa.user_pet_id = up.id
            LEFT JOIN pets AS p ON up.pet_id = p.pet_id
            WHERE pa.id = $1
            """,
            auction_id,
        )

    async def get_user_auctions(
        self, seller_id: int, limit: int = 10
    ) -> Sequence[asyncpg.Record]:
        return await self.pool.fetch(
            """
            SELECT pa.*, p.name AS pet_name, up.is_gold, up.is_rainbow, up.is_shiny
            FROM plaza_auctions AS pa
            LEFT JOIN user_pets AS up ON pa.user_pet_id = up.id
            LEFT JOIN pets AS p ON up.pet_id = p.pet_id
            WHERE pa.seller_id = $1
            ORDER BY pa.created_at DESC
            LIMIT $2
            """,
            seller_id,
            max(1, limit),
        )

    async def create_pet_auction(
        self,
        seller_id: int,
        user_pet_id: int,
        *,
        starting_bid: int,
        duration_minutes: int,
        buyout_price: int | None = None,
        min_increment: int | None = None,
    ) -> asyncpg.Record:
        if starting_bid <= 0:
            raise DatabaseError("Le prix de départ doit être supérieur à zéro.")
        duration_minutes = max(15, min(duration_minutes, 1440))
        increment = max(1, min_increment or max(1, starting_bid // 20))
        if buyout_price is not None and buyout_price < starting_bid:
            buyout_price = None
        ends_at = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)

        async with self.transaction() as connection:
            pet_row = await connection.fetchrow(
                """
                SELECT id, user_id, is_active, on_market
                FROM user_pets
                WHERE id = $1
                FOR UPDATE
                """,
                user_pet_id,
            )
            if pet_row is None:
                raise DatabaseError("Pet introuvable pour cette enchère.")
            if int(pet_row["user_id"]) != seller_id:
                raise DatabaseError("Tu ne possèdes pas ce pet.")
            if bool(pet_row.get("is_active")):
                raise DatabaseError("Retire d'abord ce pet de tes actifs avant de lister une enchère.")
            if bool(pet_row.get("on_market")):
                raise DatabaseError("Ce pet est déjà listé ailleurs.")
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

            record = await connection.fetchrow(
                """
                INSERT INTO plaza_auctions (
                    seller_id, item_type, quantity, user_pet_id,
                    starting_bid, min_increment, buyout_price, ends_at
                )
                VALUES ($1, 'pet', 1, $2, $3, $4, $5, $6)
                RETURNING *
                """,
                seller_id,
                user_pet_id,
                starting_bid,
                increment,
                buyout_price,
                ends_at,
            )

        if record is None:
            raise DatabaseError("Impossible de créer l'enchère.")
        return record

    async def create_item_auction(
        self,
        seller_id: int,
        *,
        item_type: str,
        quantity: int,
        starting_bid: int,
        duration_minutes: int,
        buyout_price: int | None = None,
        min_increment: int | None = None,
        item_slug: str | None = None,
        enchantment_power: int | None = None,
    ) -> asyncpg.Record:
        valid_types = {"ticket", "potion", "enchantment"}
        if item_type not in valid_types:
            raise DatabaseError("Type d'objet d'enchère invalide.")
        if quantity <= 0:
            raise DatabaseError("La quantité doit être positive.")
        if starting_bid <= 0:
            raise DatabaseError("Le prix de départ doit être supérieur à zéro.")
        duration_minutes = max(15, min(duration_minutes, 1440))
        increment = max(1, min_increment or max(1, starting_bid // 20))
        if buyout_price is not None and buyout_price < starting_bid:
            buyout_price = None
        ends_at = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)

        async with self.transaction() as connection:
            if item_type == "ticket":
                removed = await self.remove_raffle_tickets(
                    seller_id, amount=quantity, connection=connection
                )
                if removed is None:
                    raise DatabaseError("Tu n'as pas assez de tickets à miser.")
                slug = "raffle_ticket"
            elif item_type == "potion":
                if not item_slug:
                    raise DatabaseError("Précise la potion à mettre aux enchères.")
                consumed = await self.consume_user_potion(
                    seller_id,
                    item_slug,
                    quantity=quantity,
                    connection=connection,
                )
                if not consumed:
                    raise DatabaseError("Tu n'as pas assez d'exemplaires de cette potion.")
                slug = item_slug
            else:  # enchantment
                if not item_slug or enchantment_power is None:
                    raise DatabaseError("Précise l'enchantement et son niveau.")
                removed = await self.consume_user_enchantment(
                    seller_id,
                    item_slug,
                    power=enchantment_power,
                    quantity=quantity,
                    connection=connection,
                )
                if not removed:
                    raise DatabaseError("Tu ne possèdes pas cet enchantement.")
                slug = item_slug

            record = await connection.fetchrow(
                """
                INSERT INTO plaza_auctions (
                    seller_id, item_type, item_slug, item_power, quantity,
                    starting_bid, min_increment, buyout_price, ends_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING *
                """,
                seller_id,
                item_type,
                slug,
                enchantment_power,
                quantity,
                starting_bid,
                increment,
                buyout_price,
                ends_at,
            )

        if record is None:
            raise DatabaseError("Impossible de créer l'enchère.")
        return record

    async def cancel_auction(self, auction_id: int, seller_id: int) -> asyncpg.Record:
        async with self.transaction() as connection:
            auction = await connection.fetchrow(
                "SELECT * FROM plaza_auctions WHERE id = $1 FOR UPDATE",
                auction_id,
            )
            if auction is None:
                raise DatabaseError("Enchère introuvable.")
            if str(auction.get("status")) != "active":
                raise DatabaseError("Cette enchère est déjà terminée.")
            if int(auction.get("seller_id") or 0) != seller_id:
                raise DatabaseError("Tu ne peux pas annuler cette enchère.")
            if auction.get("current_bidder_id"):
                raise DatabaseError("Impossible d'annuler une enchère qui a déjà reçu une offre.")

            await self._release_auction_item(connection, auction)
            record = await connection.fetchrow(
                """
                UPDATE plaza_auctions
                SET status = 'cancelled', completed_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                auction_id,
            )

        if record is None:
            raise DatabaseError("Impossible d'annuler l'enchère.")
        return record

    async def place_auction_bid(
        self, auction_id: int, bidder_id: int, amount: int
    ) -> Mapping[str, object]:
        if amount <= 0:
            raise DatabaseError("Ton offre doit être supérieure à zéro.")
        await self.ensure_user(bidder_id)

        async with self.transaction() as connection:
            auction = await connection.fetchrow(
                "SELECT * FROM plaza_auctions WHERE id = $1 FOR UPDATE",
                auction_id,
            )
            if auction is None:
                raise DatabaseError("Cette enchère n'existe pas.")
            if str(auction.get("status")) != "active":
                raise DatabaseError("Cette enchère est déjà terminée.")
            ends_at = auction.get("ends_at")
            if isinstance(ends_at, datetime) and ends_at <= datetime.now(timezone.utc):
                await self._finalize_auction_row(connection, auction)
                raise DatabaseError("Trop tard, cette enchère vient de se terminer.")

            current_bid = int(auction.get("current_bid") or 0)
            min_increment = int(auction.get("min_increment") or 1)
            starting_bid = int(auction.get("starting_bid") or 1)
            minimum = starting_bid if current_bid <= 0 else current_bid + min_increment
            if amount < minimum:
                raise DatabaseError(
                    "Ton offre doit être supérieure à la mise minimale en cours."
                )

            buyout_price = auction.get("buyout_price")
            if buyout_price is not None:
                buyout_price = int(buyout_price)
            if buyout_price is not None and amount >= buyout_price:
                amount = buyout_price

            bidder_row = await connection.fetchrow(
                "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                bidder_id,
            )
            if bidder_row is None:
                raise DatabaseError("Impossible de récupérer ton solde.")
            bidder_before = int(bidder_row.get("gems") or 0)
            if bidder_before < amount:
                raise InsufficientBalanceError("Solde de gemmes insuffisant pour cette enchère.")

            new_bidder_balance = bidder_before - amount
            await connection.execute(
                "UPDATE users SET gems = $1 WHERE user_id = $2",
                new_bidder_balance,
                bidder_id,
            )

            previous_bidder = auction.get("current_bidder_id")
            if previous_bidder:
                prev_row = await connection.fetchrow(
                    "SELECT gems FROM users WHERE user_id = $1 FOR UPDATE",
                    int(previous_bidder),
                )
                if prev_row:
                    prev_before = int(prev_row.get("gems") or 0)
                    prev_after = prev_before + current_bid
                    await connection.execute(
                        "UPDATE users SET gems = $1 WHERE user_id = $2",
                        prev_after,
                        int(previous_bidder),
                    )
                    await self.record_transaction(
                        connection=connection,
                        user_id=int(previous_bidder),
                        transaction_type="auction_refund",
                        currency="gem",
                        amount=current_bid,
                        balance_before=prev_before,
                        balance_after=prev_after,
                        description=f"Remboursement enchère #{auction_id}",
                        related_user_id=bidder_id,
                    )

            now = datetime.now(timezone.utc)
            status = "sold" if buyout_price is not None and amount >= buyout_price else "active"
            updated = await connection.fetchrow(
                """
                UPDATE plaza_auctions
                SET current_bid = $2,
                    current_bidder_id = $3,
                    buyer_id = CASE WHEN $4 = 'sold' THEN $3 ELSE buyer_id END,
                    status = $4,
                    completed_at = CASE WHEN $4 = 'sold' THEN $5 ELSE completed_at END
                WHERE id = $1
                RETURNING *
                """,
                auction_id,
                amount,
                bidder_id,
                status,
                now,
            )

            await self.record_transaction(
                connection=connection,
                user_id=bidder_id,
                transaction_type="auction_bid",
                currency="gem",
                amount=-amount,
                balance_before=bidder_before,
                balance_after=new_bidder_balance,
                description=f"Enchère #{auction_id}",
                related_user_id=int(auction.get("seller_id") or 0) or None,
            )

            if status == "sold":
                await self._grant_auction_item(
                    connection, updated, int(updated.get("current_bidder_id") or 0)
                )
                await self._credit_seller_from_auction(connection, updated)

        return updated
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

    async def get_analytics_snapshot(
        self,
    ) -> tuple[Mapping[str, int], Sequence[Mapping[str, int | str]]]:
        cache_key = ("analytics_global",)
        cached = self._analytics_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        totals = await self.get_server_economy_totals()
        pet_values = await self.get_pet_value_overview()
        snapshot = (totals, pet_values)
        self._analytics_cache.set(cache_key, snapshot)
        return snapshot

    async def get_server_economy_totals(self) -> Mapping[str, int]:
        cache_key = ("analytics_totals",)
        cached = self._analytics_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        row = await self._fetchrow(
            """
            SELECT
                COALESCE(SUM(balance), 0) AS total_pb,
                COALESCE(SUM(gems), 0) AS total_gems
            FROM users
            """
        )
        if row is None:
            raise DatabaseError("Impossible de récupérer les statistiques économiques")
        total_rap = await self.get_total_pet_rap()
        result = {
            "total_pb": int(row["total_pb"]),
            "total_gems": int(row["total_gems"]),
            "total_rap": int(total_rap),
        }
        self._analytics_cache.set(cache_key, result)
        return result

    async def get_total_pet_rap(self) -> int:
        await self._ensure_market_values_ready()
        query = f"""
            {self._rap_values_cte()}
            SELECT COALESCE(SUM(rap_total), 0) AS total_rap
            FROM (
                SELECT user_id, SUM(rap_value) AS rap_total
                FROM rap_values
                GROUP BY user_id
            ) AS totals
        """
        value = await self._fetchval(query)
        return int(value or 0)

    async def get_pet_value_overview(self) -> Sequence[Mapping[str, int | str]]:
        cache_key = ("analytics_pet_values",)
        cached = self._analytics_cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]
        rows = await self._fetch(
            """
            SELECT
                p.pet_id,
                p.name,
                p.rarity,
                p.base_income_per_hour,
                COALESCE(m.value_in_gems, 0) AS market_value
            FROM pets AS p
            LEFT JOIN pet_market_values AS m
                ON m.pet_id = p.pet_id
                AND m.variant_code = 'normal'
            ORDER BY p.pet_id
            """
        )
        results: list[dict[str, int | str]] = []
        for row in rows:
            pet_id = int(row["pet_id"])
            base_income = int(row["base_income_per_hour"])
            value = int(row["market_value"] or 0)
            if value <= 0:
                name = str(row.get("name", ""))
                rarity = str(row.get("rarity", ""))
                zone_slug = _PET_ZONE_BY_NAME.get(name.lower(), "exclusif")
                value = self._fallback_market_value(
                    name=name,
                    rarity=rarity,
                    base_income_per_hour=base_income,
                    is_huge=name.lower() in _HUGE_PET_NAME_LOOKUP,
                    zone_slug=zone_slug,
                    is_gold=False,
                    is_rainbow=False,
                    is_galaxy=False,
                    is_shiny=False,
                )
            results.append(
                {
                    "pet_id": pet_id,
                    "name": str(row["name"]),
                    "value": int(value),
                }
            )
        self._analytics_cache.set(cache_key, results)
        return results
