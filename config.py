"""Configuration centralisée et minimaliste pour EcoBot."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Dict, Final, Mapping, Tuple

try:  # Python 3.9+ fournit zoneinfo, mais nous gardons un repli.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - environnement minimaliste
    ZoneInfo = None  # type: ignore[misc, assignment]

from dotenv import load_dotenv

load_dotenv()


def _load_balance_config(path: str) -> dict[str, object]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parent / config_path
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


_BALANCE_CONFIG = _load_balance_config(os.getenv("BALANCE_CONFIG_PATH", "balance_config.json"))


def _load_economy_config(path: str) -> dict[str, object]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = Path(__file__).resolve().parent / config_path
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


_ECONOMY_CONFIG = _load_economy_config(os.getenv("ECONOMY_CONFIG_PATH", "config/economy.json"))


def _get_economy_value(path: str, default: object) -> object:
    current: object = _ECONOMY_CONFIG
    for key in path.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
    return current


def _get_economy_int(
    path: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = _get_economy_value(path, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _get_economy_float(
    path: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = _get_economy_value(path, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _get_economy_bool(path: str, default: bool) -> bool:
    value = _get_economy_value(path, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
    return bool(value) if isinstance(value, (int, float)) else default


def _get_economy_mapping(
    path: str, default: Mapping[str, float]
) -> Mapping[str, float]:
    value = _get_economy_value(path, default)
    if isinstance(value, dict):
        parsed: dict[str, float] = {}
        for entry_key, entry_value in value.items():
            try:
                parsed[str(entry_key)] = float(entry_value)
            except (TypeError, ValueError):
                continue
        return parsed or default
    return default


def _get_balance_int(
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = _BALANCE_CONFIG.get(key, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _get_balance_float(
    key: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    value = _BALANCE_CONFIG.get(key, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _get_balance_bool(key: str, default: bool) -> bool:
    value = _BALANCE_CONFIG.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
    return bool(value) if isinstance(value, (int, float)) else default


def _get_balance_mapping(key: str, default: Mapping[str, float]) -> Mapping[str, float]:
    value = _BALANCE_CONFIG.get(key, default)
    if isinstance(value, dict):
        parsed: dict[str, float] = {}
        for entry_key, entry_value in value.items():
            try:
                parsed[str(entry_key)] = float(entry_value)
            except (TypeError, ValueError):
                continue
        return parsed or default
    return default


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(0.0, min(1.0, parsed))


def _get_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, parsed)


def _resolve_timezone(name: str, *, fallback_offset_hours: int = 1) -> timezone:
    """Retourne un fuseau horaire robuste, même si zoneinfo est indisponible."""

    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:  # pragma: no cover - dépend de l'environnement système
            pass
    offset = timedelta(hours=fallback_offset_hours)
    return timezone(offset)

# ---------------------------------------------------------------------------
# Informations essentielles
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("PREFIX", "e!")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

if not TOKEN:
    raise ValueError("DISCORD_TOKEN manquant dans le fichier .env")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL manquant dans le fichier .env")

# ---------------------------------------------------------------------------
# Paramètres économie
# ---------------------------------------------------------------------------
GEMS_REBASE_FACTOR = _get_economy_int("GEMS_REBASE_FACTOR", 10_000, minimum=1)
ECONOMY_DEBUG = _get_economy_bool("debug", False)

MARKET_VALUE_RARITY_BASE = _get_economy_mapping(
    "market_value.rarity_base",
    {
        "Commun": 20,
        "Atypique": 60,
        "Rare": 120,
        "Épique": 400,
        "Légendaire": 1_200,
        "Mythique": 4_000,
        "Secret": 12_000,
        "Huge": 150_000,
        "Titanic": 1_500_000,
    },
)
MARKET_VALUE_RARITY_CAP = _get_economy_mapping(
    "market_value.rarity_cap",
    {
        "Commun": 200,
        "Atypique": 500,
        "Rare": 800,
        "Épique": 3_000,
        "Légendaire": 10_000,
        "Mythique": 50_000,
        "Secret": 250_000,
        "Huge": 2_000_000,
        "Titanic": 20_000_000,
    },
)
MARKET_VALUE_POWER_EXPONENT = _get_economy_float(
    "market_value.power_exponent", 0.8, minimum=0.1, maximum=2.0
)
MARKET_VALUE_POWER_BASELINE_GLOBAL = _get_economy_int(
    "market_value.power_baseline_global", 1_000, minimum=1
)
MARKET_VALUE_POWER_BASELINE_BY_ZONE = _get_economy_mapping(
    "market_value.power_baseline_by_zone",
    {
        "starter": 50,
        "foret": 250,
        "manoir_hante": 3_000,
        "robotique": 50_000,
        "animalerie": 1_000_000,
        "mexico": 10_000_000,
        "celeste": 100_000_000,
        "exclusif": 1_000_000,
    },
)
MARKET_VALUE_HUGE_MULTIPLIER = _get_economy_float(
    "market_value.huge_multiplier", 10.0, minimum=0.1
)
MARKET_VALUE_TITANIC_MULTIPLIER = _get_economy_float(
    "market_value.titanic_multiplier", 50.0, minimum=0.1
)
MARKET_VALUE_MIN = _get_economy_int("market_value.min_value", 1, minimum=1)
DISPLAY_GEMS_COMPACT = _get_economy_bool("display.compact", True)

MARKET_VALUE_CONFIG: Final[Mapping[str, object]] = {
    "rarity_base": MARKET_VALUE_RARITY_BASE,
    "rarity_cap": MARKET_VALUE_RARITY_CAP,
    "power_exponent": MARKET_VALUE_POWER_EXPONENT,
    "power_baseline_global": MARKET_VALUE_POWER_BASELINE_GLOBAL,
    "power_baseline_by_zone": MARKET_VALUE_POWER_BASELINE_BY_ZONE,
    "huge_multiplier": MARKET_VALUE_HUGE_MULTIPLIER,
    "titanic_multiplier": MARKET_VALUE_TITANIC_MULTIPLIER,
    "min_value": MARKET_VALUE_MIN,
    "debug": ECONOMY_DEBUG,
}
_daily_min = _get_balance_int("daily_reward_min", 10_000, minimum=0)
_daily_max = _get_balance_int("daily_reward_max", 20_000, minimum=_daily_min)
DAILY_REWARD = (_daily_min, _daily_max)
DAILY_COOLDOWN = 86_400  # 24 heures
DAILY_STREAK_TOLERANCE = _get_balance_int("daily_streak_tolerance_seconds", 7_200, minimum=0)
DAILY_STREAK_BONUS_BASE = _get_balance_float("daily_streak_bonus_base", 0.02, minimum=0.0)
DAILY_STREAK_BONUS_EXPONENT = _get_balance_float(
    "daily_streak_bonus_exponent", 1.05, minimum=0.1
)
DAILY_STREAK_BONUS_CAP = _get_balance_float("daily_streak_bonus_cap", 0.6, minimum=0.0)
DAILY_STREAK_DIMINISH_ENABLED = _get_balance_bool("daily_streak_diminish_enabled", True)
DAILY_STREAK_DIMINISH_START = _get_balance_int("daily_streak_diminish_start", 25, minimum=1)
DAILY_STREAK_DIMINISH_EXPONENT = _get_balance_float(
    "daily_streak_diminish_exponent", 0.75, minimum=0.1, maximum=1.0
)
DAILY_GEMS_BASE = _get_balance_int("daily_gems_base", 5, minimum=0)
DAILY_GEMS_BONUS_CHANCE = _get_balance_float(
    "daily_gems_bonus_chance", 0.15, minimum=0.0, maximum=1.0
)
DAILY_GEMS_BONUS_MIN = _get_balance_int("daily_gems_bonus_min", 1, minimum=0)
DAILY_GEMS_BONUS_MAX = _get_balance_int(
    "daily_gems_bonus_max", 5, minimum=DAILY_GEMS_BONUS_MIN
)
DAILY_GEMS_CAP = _get_balance_int("daily_gems_cap", 12, minimum=0)
MESSAGE_REWARD = _get_balance_int("message_reward", 1, minimum=0)
MESSAGE_COOLDOWN = 60
LEADERBOARD_LIMIT = _get_economy_int("leaderboard_limit", 10, minimum=1)
CACHE_TTL_SECONDS = _get_economy_int("cache_ttl_seconds", 60, minimum=0)
CACHE_MAX_ENTRIES = _get_economy_int("cache_max_entries", 128, minimum=1)
QUERY_TIMEOUT_SECONDS = _get_economy_int("query_timeout_seconds", 3, minimum=1)
DEBUG_SQL_TIMING = _get_economy_bool("debug_sql_timing", False)
SLOT_MIN_BET = _get_balance_int("slots_min_bet", 50, minimum=1)
SLOT_MAX_BET = _get_balance_int("slots_max_bet", 1_000_000_000_000_000, minimum=SLOT_MIN_BET)
CASINO_HUGE_MAX_CHANCE = _get_balance_float("casino_huge_max_chance", 0.10, minimum=0.0, maximum=1.0)
CASINO_TITANIC_MAX_CHANCE = _get_balance_float(
    "casino_titanic_max_chance", 0.01, minimum=0.0, maximum=1.0
)
CASINO_HUGE_CHANCE_PER_PB = CASINO_HUGE_MAX_CHANCE / SLOT_MAX_BET
CASINO_TITANIC_CHANCE_PER_PB = CASINO_TITANIC_MAX_CHANCE / SLOT_MAX_BET
PET_FARM_TIME_FACTOR_MIN = _get_balance_float("pet_farm_time_factor_min", 0.25, minimum=0.0)
PET_FARM_TIME_FACTOR_MAX = _get_balance_float("pet_farm_time_factor_max", 2.0, minimum=0.1)
PET_FARM_GEM_PER_PET_HOUR = _get_balance_float("pet_farm_gem_per_pet_hour", 2.0, minimum=0.0)
PET_FARM_GEM_MAX = _get_balance_int("pet_farm_gem_max", 500, minimum=0)
PET_FARM_GEM_VARIANCE_PER_PET = _get_balance_float("pet_farm_gem_variance_per_pet", 0.5, minimum=0.0)
DAYCARE_MAX_PETS = _get_balance_int("daycare_max_pets", 10, minimum=1)
DAYCARE_GEM_PER_PET_HOUR = _get_balance_float("daycare_gem_per_pet_hour", 4.0, minimum=0.0)
DAYCARE_GEM_MAX = _get_balance_int("daycare_gem_max", 1500, minimum=0)
PET_FARM_TICKET_BASE = _get_balance_float("pet_farm_ticket_base", 0.03, minimum=0.0)
PET_FARM_TICKET_PER_PET = _get_balance_float("pet_farm_ticket_per_pet", 0.004, minimum=0.0)
PET_FARM_TICKET_MAX_CHANCE = _get_balance_float(
    "pet_farm_ticket_max_chance", 0.20, minimum=0.0, maximum=1.0
)
PET_FARM_POTION_BASE = _get_balance_float("pet_farm_potion_base", 0.03, minimum=0.0)
PET_FARM_POTION_PER_PET = _get_balance_float("pet_farm_potion_per_pet", 0.006, minimum=0.0)
PET_FARM_POTION_MAX_CHANCE = _get_balance_float(
    "pet_farm_potion_max_chance", 0.18, minimum=0.0, maximum=1.0
)
PET_FARM_ENCHANT_BASE = _get_balance_float("pet_farm_enchant_base", 0.01, minimum=0.0)
PET_FARM_ENCHANT_PER_PET = _get_balance_float("pet_farm_enchant_per_pet", 0.0025, minimum=0.0)
PET_FARM_ENCHANT_MAX_CHANCE = _get_balance_float(
    "pet_farm_enchant_max_chance", 0.05, minimum=0.0, maximum=1.0
)
FUSION_COST_BASE = _get_balance_int("fusion_cost_base", 5_000, minimum=0)
FUSION_COST_POWER_SCALE = _get_balance_float("fusion_cost_power_scale", 0.35, minimum=0.0)
FUSION_COST_POWER_LOG_BASE = _get_balance_float(
    "fusion_cost_power_log_base", 10.0, minimum=2.0
)
FUSION_COST_COUNT_EXPONENT = _get_balance_float(
    "fusion_cost_count_exponent", 1.1, minimum=1.0
)
FUSION_COST_OUTPUT_MULTIPLIER = _get_balance_float(
    "fusion_cost_output_multiplier", 0.6, minimum=0.0
)
FUSION_COST_MIN = _get_balance_int("fusion_cost_min", 1_000, minimum=0)
FUSION_COST_MAX = _get_balance_int("fusion_cost_max", 5_000_000, minimum=FUSION_COST_MIN)
FUSION_COST_RARITY_MULTIPLIERS = _get_balance_mapping(
    "fusion_cost_rarity_multipliers",
    {
        "Commun": 1.0,
        "Atypique": 1.4,
        "Rare": 2.0,
        "Épique": 3.0,
        "Légendaire": 4.5,
        "Mythique": 6.5,
        "Secret": 9.0,
        "Huge": 16.0,
    },
)
STEAL_BASE_CHANCE = _get_balance_float("steal_base_chance", 0.5, minimum=0.0, maximum=1.0)
STEAL_GRADE_BONUS_PER_LEVEL = _get_balance_float(
    "steal_grade_bonus_per_level", 0.05, minimum=0.0
)
STEAL_GRADE_BONUS_CAP = _get_balance_float(
    "steal_grade_bonus_cap", 0.5, minimum=0.0
)
STEAL_LOG_BASE = _get_balance_float("steal_log_base", 10.0, minimum=2.0)
STEAL_LOG_SCALE = _get_balance_float("steal_log_scale", 1.2, minimum=0.0)
STEAL_MIN_CHANCE = _get_balance_float("steal_min_chance", 0.05, minimum=0.0, maximum=1.0)
STEAL_MAX_CHANCE = _get_balance_float("steal_max_chance", 0.9, minimum=0.0, maximum=1.0)
CACHE_TTL_INVENTORY = _get_balance_int("cache_ttl_inventory_seconds", 20, minimum=0)
CACHE_TTL_PETS = _get_balance_int("cache_ttl_pets_seconds", 20, minimum=0)
CACHE_TTL_PROFILE = _get_balance_int("cache_ttl_profile_seconds", 15, minimum=0)
PETS_PAGE_SIZE = _get_balance_int("pets_page_size", 8, minimum=1, maximum=25)
INVENTORY_POTIONS_PAGE_SIZE = _get_balance_int("inventory_potions_page_size", 6, minimum=1, maximum=25)
INVENTORY_ENCHANTMENTS_PAGE_SIZE = _get_balance_int(
    "inventory_enchantments_page_size", 5, minimum=1, maximum=25
)
INVENTORY_PETS_PAGE_SIZE = _get_balance_int("inventory_pets_page_size", 4, minimum=1, maximum=25)
QUEST_WEEKLY_RESET_WEEKDAY = _get_balance_int("quest_weekly_reset_weekday", 0, minimum=0, maximum=6)
QUEST_WEEKLY_RESET_HOUR = _get_balance_int("quest_weekly_reset_hour", 0, minimum=0, maximum=23)
DEBUG_CACHE = _get_balance_bool("debug_cache", False)

# ---------------------------------------------------------------------------
# Paramètres Clans
# ---------------------------------------------------------------------------

CLAN_CREATION_COST: Final[int] = 25_000
CLAN_BASE_CAPACITY: Final[int] = 3
CLAN_CAPACITY_PER_LEVEL: Final[int] = 2
CLAN_CAPACITY_UPGRADE_COSTS: Final[Tuple[int, ...]] = (
    15_000,
    35_000,
    75_000,
    150_000,
    300_000,
)
CLAN_BOOST_INCREMENT: Final[float] = 0.03


def _generate_clan_boost_costs(
    *, base_cost: int = 50_000, growth_factor: float = 1.65, levels: int = 64
) -> Tuple[int, ...]:
    """Génère une séquence de coûts exponentiels pour les boosts de clan."""

    costs: list[int] = []
    current = float(base_cost)
    for _ in range(levels):
        costs.append(int(round(current // 100 * 100)))
        current *= growth_factor
    return tuple(costs)


CLAN_BOOST_COSTS: Final[Tuple[int, ...]] = _generate_clan_boost_costs()
CLAN_SHINY_LUCK_INCREMENT: Final[float] = 0.002

# ---------------------------------------------------------------------------
# Paramètres Statistiques
# ---------------------------------------------------------------------------

STATS_ACTIVE_WINDOW_DAYS = _get_int_env("STATS_ACTIVE_WINDOW_DAYS", 7, minimum=1)
STATS_TOP_LIMIT = _get_int_env("STATS_TOP_LIMIT", 10, minimum=1)


def compute_daily_streak_bonus(streak: int) -> float:
    """Retourne le bonus multiplicatif pour le daily."""

    safe_streak = max(0, int(streak))
    if DAILY_STREAK_DIMINISH_ENABLED and safe_streak > DAILY_STREAK_DIMINISH_START:
        excess = safe_streak - DAILY_STREAK_DIMINISH_START
        safe_streak = int(
            round(
                DAILY_STREAK_DIMINISH_START
                + (excess ** DAILY_STREAK_DIMINISH_EXPONENT)
            )
        )
    bonus = DAILY_STREAK_BONUS_BASE * (safe_streak ** DAILY_STREAK_BONUS_EXPONENT)
    return max(0.0, min(DAILY_STREAK_BONUS_CAP, bonus))


def compute_steal_success_chance(
    *,
    attacker_balance: int,
    victim_balance: int,
    grade_level: int = 0,
    has_protection: bool = False,
) -> float:
    """Calcule la chance de vol en fonction du ratio et des bonus."""

    base = STEAL_BASE_CHANCE + min(
        max(0, int(grade_level)) * STEAL_GRADE_BONUS_PER_LEVEL,
        STEAL_GRADE_BONUS_CAP,
    )
    attacker_safe = max(1.0, float(attacker_balance))
    victim_safe = max(0.0, float(victim_balance))
    ratio = max(1.0, victim_safe / attacker_safe)
    log_base = max(2.0, STEAL_LOG_BASE)
    try:
        log_factor = math.log(ratio, log_base) if ratio > 1 else 0.0
    except ValueError:
        log_factor = 0.0
    scale = max(0.0, STEAL_LOG_SCALE)
    ratio_multiplier = 1.0 / (1.0 + scale * log_factor) if scale > 0 else 1.0
    chance = base * ratio_multiplier
    if has_protection:
        chance /= 10
    if not math.isfinite(chance):
        chance = STEAL_MIN_CHANCE
    chance = min(max(chance, STEAL_MIN_CHANCE), STEAL_MAX_CHANCE)
    return max(0.0, min(1.0, chance))

# ---------------------------------------------------------------------------
# Paramètres Grades
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GradeDefinition:
    name: str
    mastermind_goal: int
    egg_goal: int
    rap_goal: int
    casino_loss_goal: int
    potion_goal: int
    reward_gems: int


def rebase_gems_amount(value: float | int, *, minimum: int = 0) -> int:
    """Convert a gem value from the legacy scale to the rebased scale."""

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return max(0, int(minimum))
    if numeric <= 0 or math.isnan(numeric):
        return max(0, int(minimum))
    if GEMS_REBASE_FACTOR <= 1:
        return max(int(minimum), int(numeric))
    scaled = int(math.floor(numeric / GEMS_REBASE_FACTOR))
    if scaled < minimum:
        scaled = int(minimum)
    return max(0, scaled)


def rebase_gems_price(value: float | int) -> int:
    """Return a safe rebased gem price (never free when input is positive)."""

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0
    if numeric <= 0 or math.isnan(numeric):
        return 0
    scaled = rebase_gems_amount(numeric, minimum=0)
    return max(1, int(scaled))


BASE_PET_SLOTS: Final[int] = 4
PET_SLOT_MAX_CAPACITY: Final[int] = 40
PET_SLOT_SHOP_BASE_COST: Final[int] = rebase_gems_price(5_000)
PET_SLOT_SHOP_COST_GROWTH: Final[float] = 1.6
PET_SLOT_SHOP_CURRENCY: Final[str] = "gem"
MEXICO_DISTRIBUTOR_COOLDOWN: Final[timedelta] = timedelta(minutes=10)
PET_VALUE_SCALE: Final[int] = _get_economy_int("pet_value_scale", 1, minimum=1)


def scale_pet_value(raw_value: float | int, *, minimum: int = 0) -> int:
    """Rebase a pet-related value according to ``PET_VALUE_SCALE``."""

    try:
        numeric = float(raw_value)
    except (TypeError, ValueError):
        return max(0, int(minimum))
    if numeric <= 0 or math.isnan(numeric):
        return max(0, int(minimum))
    if PET_VALUE_SCALE <= 1:
        return max(int(minimum), int(numeric))
    scaled = int(math.floor(numeric / PET_VALUE_SCALE))
    if scaled <= 0:
        scaled = 1
    return max(int(minimum), scaled)


RAP_GOAL_UNIT: Final[int] = scale_pet_value(50_000, minimum=1)
CASINO_LOSS_GOAL_UNIT: Final[int] = 5_000

_GRADE_BLUEPRINTS: Tuple[tuple[str, int, int, int, int, int], ...] = (
    ("Novice", 0, 3, 0, 0, 125),
    ("Apprenti", 0, 5, 0, 0, 200),
    ("Disciple", 0, 8, 0, 0, 275),
    ("Explorateur", 1, 12, 3, 2, 350),
    ("Aventurier", 2, 16, 5, 2, 450),
    ("Expert", 2, 20, 8, 3, 550),
    ("Champion", 2, 25, 12, 3, 700),
    ("Maître", 3, 30, 18, 4, 850),
    ("Prodige", 3, 36, 27, 4, 1_050),
    ("Élite", 4, 43, 40, 5, 1_300),
    ("Légende", 5, 51, 60, 5, 1_600),
    ("Mythique", 6, 60, 90, 6, 1_950),
    ("Cosmique", 7, 70, 135, 7, 2_350),
    ("Divin", 8, 81, 200, 8, 2_800),
    ("Parangon", 9, 93, 300, 9, 3_300),
)


def _build_grade_definitions() -> Tuple[GradeDefinition, ...]:
    definitions: list[GradeDefinition] = []
    for name, mastermind, eggs, sale_goal, potion, reward in _GRADE_BLUEPRINTS:
        rap_goal = sale_goal * RAP_GOAL_UNIT
        casino_loss_goal = sale_goal * CASINO_LOSS_GOAL_UNIT
        definitions.append(
            GradeDefinition(
                name,
                mastermind_goal=mastermind,
                egg_goal=eggs,
                rap_goal=rap_goal,
                casino_loss_goal=casino_loss_goal,
                potion_goal=potion,
                reward_gems=reward,
            )
        )
    return tuple(definitions)


GRADE_DEFINITIONS: Tuple[GradeDefinition, ...] = _build_grade_definitions()

GRADE_ROLE_IDS: Tuple[int, ...] = (
    1430716817852203128,
    1430721773497876530,
    1430721718497837198,
    1430721544874623016,
    1430721477535334625,
    1430721408849150052,
    1430721364242993302,
    1430721264963817528,
    1430721259020484740,
    1430721200048312493,
    1430721137888985228,
    1430721065524400289,
    1430720939141763092,
    1430720735625609276,
    1430720400203055144,
)

# ---------------------------------------------------------------------------
# Rôles spéciaux
# ---------------------------------------------------------------------------

VIP_ROLE_ID: Final[int] = 1_431_428_621_959_954_623
TOP_PB_ROLE_ID: Final[int] = 1_454_894_276_768_174_244
TOP_PB_ROLE_LIMIT: Final[int] = 30
TOP_PB_ROLE_REFRESH_MINUTES: Final[int] = 10
EGG_MASTERY_MAX_ROLE_ID: Final[int] = 1_433_423_014_065_602_600
PET_MASTERY_MAX_ROLE_ID: Final[int] = 1_433_425_659_182_448_720
MASTERMIND_MASTERY_MAX_ROLE_ID: Final[int] = 1_433_426_656_361_447_646

# ---------------------------------------------------------------------------
# Esthétique
# ---------------------------------------------------------------------------


class Colors:
    PRIMARY = 0x5865F2
    SUCCESS = 0x57F287
    ERROR = 0xED4245
    WARNING = 0xFEE75C
    INFO = PRIMARY
    GOLD = 0xF7B731
    NEUTRAL = 0x99AAB5
    ACCENT = 0xF47FFF


class Emojis:
    MONEY = "💰"
    GEM = os.getenv("GEM_EMOJI", "<:Gem:1437828670923341864>")
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    COOLDOWN = "⏳"
    DAILY = "🎰"
    LEADERBOARD = "🏆"
    XP = "✨"


# ---------------------------------------------------------------------------
# Potions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PotionDefinition:
    slug: str
    name: str
    effect_type: str
    effect_value: float
    description: str
    duration_seconds: int = 3600


POTION_DEFINITIONS: Tuple[PotionDefinition, ...] = (
    PotionDefinition(
        "luck_i",
        "Potion de chance I",
        "egg_luck",
        0.25,
        "Augmente la chance d'œufs de 25% pendant une courte durée.",
    ),
    PotionDefinition(
        "luck_ii",
        "Potion de chance II",
        "egg_luck",
        0.50,
        "Augmente la chance d'œufs de 50% pendant une courte durée.",
    ),
    PotionDefinition(
        "luck_iii",
        "Potion de chance III",
        "egg_luck",
        1.0,
        "Augmente la chance d'œufs de 100% pendant une courte durée.",
    ),
    PotionDefinition(
        "fortune_i",
        "Potion de fortune I",
        "pb_boost",
        0.15,
        "Augmente les gains de PB de 15% pendant une courte durée.",
    ),
    PotionDefinition(
        "fortune_ii",
        "Potion de fortune II",
        "pb_boost",
        0.30,
        "Augmente les gains de PB de 30% pendant une courte durée.",
    ),
    PotionDefinition(
        "fortune_iii",
        "Potion de fortune III",
        "pb_boost",
        0.50,
        "Augmente les gains de PB de 50% pendant une courte durée.",
    ),
    PotionDefinition(
        "fortune_iv",
        "Potion de fortune IV",
        "pb_boost",
        0.75,
        "Augmente les gains de PB de 75% pendant une courte durée.",
    ),
    PotionDefinition(
        "fortune_v",
        "Potion de fortune V",
        "pb_boost",
        1.0,
        "Augmente les gains de PB de 100% pendant une courte durée.",
    ),
    PotionDefinition(
        "mastery_xp",
        "Potion de maîtrise",
        "mastery_xp",
        1.0,
        "Double l'XP de maîtrise pendant 5 minutes.",
        duration_seconds=300,
    ),
)

POTION_DEFINITION_MAP: Dict[str, PotionDefinition] = {
    potion.slug: potion for potion in POTION_DEFINITIONS
}

POTION_SELL_VALUES: Final[Dict[str, int]] = {
    "luck_i": 600,
    "luck_ii": 1_200,
    "luck_iii": 2_500,
    "fortune_i": 1_000,
    "fortune_ii": 2_200,
    "fortune_iii": 3_800,
    "fortune_iv": 5_500,
    "fortune_v": 7_500,
}

RAFFLE_TICKET_SELL_VALUE: Final[int] = 500


# ---------------------------------------------------------------------------
# Animaux (Pets)
# ---------------------------------------------------------------------------


# FIX: Cap pet income values to avoid overflow issues and sanitize drop rates.
MAX_PET_INCOME: Final[int] = 9_223_372_036_854_775_807


@dataclass(frozen=True)
class PetDefinition:
    name: str
    rarity: str
    image_url: str
    base_income_per_hour: int
    drop_rate: float
    is_huge: bool = False

    def __post_init__(self) -> None:
        # FIX: Ensure the stored income stays within signed 64-bit bounds and drop rates are non-negative.
        clamped_income = max(0, min(int(self.base_income_per_hour), MAX_PET_INCOME))
        object.__setattr__(self, "base_income_per_hour", clamped_income)
        object.__setattr__(self, "drop_rate", max(0.0, float(self.drop_rate)))


@dataclass(frozen=True)
class PetEggDefinition:
    name: str
    slug: str
    price: int
    pets: Tuple[PetDefinition, ...]
    zone_slug: str
    aliases: Tuple[str, ...] = ()
    image_url: str | None = None
    currency: str = "pb"


@dataclass(frozen=True)
class PetZoneDefinition:
    name: str
    slug: str
    grade_required: int
    entry_cost: int
    eggs: Tuple[PetEggDefinition, ...]
    egg_mastery_required: int = 0
    pet_mastery_required: int = 0
    rebirth_required: int = 0
    min_income_required: int = 0
    currency: str = "pb"


EGG_FRENZY_LUCK_BONUS: Final[float] = 0.50
REBIRTH_EGG_LUCK_BONUS: Final[float] = 0.50
EGG_FRENZY_START_TIME: Final[time] = time(hour=20, minute=0)
EGG_FRENZY_END_TIME: Final[time] = time(hour=21, minute=0)
EGG_FRENZY_TIMEZONE: Final[timezone] = _resolve_timezone(
    os.getenv("EGG_FRENZY_TIMEZONE", "Europe/Paris")
)


PET_EGG_PRICE: Final[int] = 500
DEFAULT_PET_EGG_SLUG: Final[str] = "basique"
STARTER_ZONE_SLUG: Final[str] = "starter"
FORET_ZONE_SLUG: Final[str] = "foret"
MANOIR_ZONE_SLUG: Final[str] = "manoir_hante"
ROBOT_ZONE_SLUG: Final[str] = "robotique"
ANIMALERIE_ZONE_SLUG: Final[str] = "animalerie"
MEXICO_ZONE_SLUG: Final[str] = "mexico"
ROCKET_ZONE_SLUG: Final[str] = "fusee"
CELESTE_ZONE_SLUG: Final[str] = "celeste"
ZODIAQUE_ZONE_SLUG: Final[str] = "zodiaque"
GOLD_PET_MULTIPLIER: Final[int] = 3
GOLD_PET_CHANCE: Final[float] = _get_float_env("PET_GOLD_CHANCE", 0.0)
GOLD_PET_COMBINE_REQUIRED: Final[int] = _get_int_env(
    "PET_GOLD_COMBINE_REQUIRED", 10, minimum=2
)
RAINBOW_PET_MULTIPLIER: Final[int] = 10
RAINBOW_PET_COMBINE_REQUIRED: Final[int] = 10
RAINBOW_PET_CHANCE: Final[float] = 0.0
GALAXY_PET_MULTIPLIER: Final[int] = 25
GALAXY_PET_COMBINE_REQUIRED: Final[int] = 100
SHINY_PET_MULTIPLIER: Final[int] = 5
HUGE_PET_NAME: Final[str] = "Huge Shelly"
HUGE_PET_MULTIPLIER: Final[float] = 2
HUGE_PET_MIN_INCOME: Final[int] = 600
HUGE_PET_LEVEL_CAP: Final[int] = 99
HUGE_PET_LEVEL_BASE_XP: Final[int] = 200
HUGE_PET_LEVEL_EXPONENT: Final[float] = 2
HUGE_GALE_NAME: Final[str] = "Huge Gale"
HUGE_GRIFF_NAME: Final[str] = "Huge Griff"
HUGE_BULL_NAME: Final[str] = "Huge Bull"
TITANIC_GRIFF_NAME: Final[str] = "Titanic Griff"
TITANIC_COLT_NAME: Final[str] = "Titanic Colt"
HUGE_ASTRALIS_NAME: Final[str] = "Huge Astralis"
TITANIC_ZENITH_NAME: Final[str] = "Titanic Zenith"
HUGE_VIRGO_COLLETTE_NAME: Final[str] = "Huge Virgo Collette"
TITANIC_CAPRICORN_STU_NAME: Final[str] = "Titanic Capricorn Stu"
HUGE_KENJI_ONI_NAME: Final[str] = "Huge Kenji Oni"
HUGE_RED_KING_FRANK_NAME: Final[str] = "Huge Red King Frank"
HUGE_RED_KING_FRANK_MULTIPLIER: Final[float] = 40
HUGE_GRIFF_MULTIPLIER: Final[float] = 6
TITANIC_COLT_MULTIPLIER: Final[float] = 50
TITANIC_GRIFF_MULTIPLIER: Final[float] = 35
HUGE_ASTRALIS_MULTIPLIER: Final[float] = 25
TITANIC_ZENITH_MULTIPLIER: Final[float] = 100
HUGE_GALE_MULTIPLIER: Final[float] = 10
HUGE_KENJI_ONI_MULTIPLIER: Final[float] = 12
HUGE_BULL_MULTIPLIER: Final[float] = 3.5
HUGE_BO_NAME: Final[str] = "Huge Bo"
HUGE_BO_MULTIPLIER: Final[float] = 7
HUGE_SHADE_NAME: Final[str] = "Huge Shade"
HUGE_SHADE_MULTIPLIER: Final[float] = 2.5
HUGE_MORTIS_NAME: Final[str] = "Huge Mortis"
HUGE_MORTIS_MULTIPLIER: Final[float] = 15
HUGE_SURGE_NAME: Final[str] = "Huge Surge"
HUGE_SURGE_MULTIPLIER: Final[float] = 4
TITANIC_MEEPLE_NAME: Final[str] = "Titanic Meeple"
TITANIC_MEEPLE_MULTIPLIER: Final[float] = 100
TITANIC_POCO_NAME: Final[str] = "Titanic Poco"
TITANIC_POCO_MULTIPLIER: Final[float] = TITANIC_MEEPLE_MULTIPLIER
HUGE_ROSA_NAME: Final[str] = "Huge Rosa"
HUGE_ROSA_MULTIPLIER: Final[float] = 15
HUGE_CLANCY_NAME: Final[str] = "Huge Clancy"
HUGE_CLANCY_MULTIPLIER: Final[float] = 10
HUGE_WISHED_NAME: Final[str] = "Huge Wished"
HUGE_WISHED_MULTIPLIER: Final[float] = 20
HUGE_VIRGO_COLLETTE_MULTIPLIER: Final[float] = 25
TITANIC_CAPRICORN_STU_MULTIPLIER: Final[float] = 100
HUGE_PET_CUSTOM_MULTIPLIERS: Final[Dict[str, float]] = {
    HUGE_GRIFF_NAME: HUGE_GRIFF_MULTIPLIER,
HUGE_GALE_NAME: HUGE_GALE_MULTIPLIER,
HUGE_KENJI_ONI_NAME: HUGE_KENJI_ONI_MULTIPLIER,
HUGE_SHADE_NAME: HUGE_SHADE_MULTIPLIER,
HUGE_MORTIS_NAME: HUGE_MORTIS_MULTIPLIER,
TITANIC_GRIFF_NAME: TITANIC_GRIFF_MULTIPLIER,
TITANIC_COLT_NAME: TITANIC_COLT_MULTIPLIER,
HUGE_SURGE_NAME: HUGE_SURGE_MULTIPLIER,
TITANIC_MEEPLE_NAME: TITANIC_MEEPLE_MULTIPLIER,
    HUGE_ASTRALIS_NAME: HUGE_ASTRALIS_MULTIPLIER,
    TITANIC_ZENITH_NAME: TITANIC_ZENITH_MULTIPLIER,
    HUGE_VIRGO_COLLETTE_NAME: HUGE_VIRGO_COLLETTE_MULTIPLIER,
    TITANIC_CAPRICORN_STU_NAME: TITANIC_CAPRICORN_STU_MULTIPLIER,
    HUGE_BULL_NAME: HUGE_BULL_MULTIPLIER,
    HUGE_BO_NAME: HUGE_BO_MULTIPLIER,
    HUGE_CLANCY_NAME: HUGE_CLANCY_MULTIPLIER,
    HUGE_ROSA_NAME: HUGE_ROSA_MULTIPLIER,
    HUGE_WISHED_NAME: HUGE_WISHED_MULTIPLIER,
    TITANIC_POCO_NAME: TITANIC_POCO_MULTIPLIER,
    HUGE_RED_KING_FRANK_NAME: HUGE_RED_KING_FRANK_MULTIPLIER,
}

HUGE_PET_MIN_LEVEL_MULTIPLIERS: Final[Dict[str, float]] = {
    TITANIC_GRIFF_NAME: 12.0,
    HUGE_BULL_NAME: 2.55,
}


def get_huge_multiplier(name: str) -> float:
    """Retourne le multiplicateur personnalisé associé à un énorme pet."""

    normalized = name.strip().lower() if name else ""
    for pet_name, multiplier in HUGE_PET_CUSTOM_MULTIPLIERS.items():
        if pet_name.lower() == normalized:
            return multiplier
    return HUGE_PET_MULTIPLIER


def huge_level_required_xp(level: int) -> int:
    """Calcule l'expérience requise pour monter au niveau suivant."""

    if level >= HUGE_PET_LEVEL_CAP:
        return 0
    if level < 1:
        level = 1
    log_factor = 1.0 + math.log1p(level)
    required = math.ceil(
        HUGE_PET_LEVEL_BASE_XP * (level**HUGE_PET_LEVEL_EXPONENT) * log_factor
    )
    return max(0, int(required))


def get_huge_level_progress(level: int, xp: int) -> float:
    """Retourne la progression (0-1) du niveau actuel d'un énorme pet."""

    if level >= HUGE_PET_LEVEL_CAP:
        return 1.0
    required = huge_level_required_xp(level)
    if required <= 0:
        return 0.0
    return max(0.0, min(1.0, xp / required))


def get_huge_level_multiplier(name: str, level: int) -> float:
    """Calcule le multiplicateur effectif d'un énorme pet à un niveau donné."""

    normalized = name.strip().lower() if name else ""
    min_multiplier = 1.0
    for pet_name, multiplier in HUGE_PET_MIN_LEVEL_MULTIPLIERS.items():
        if pet_name.lower() == normalized:
            min_multiplier = max(1.0, float(multiplier))
            break

    # FIX: Avoid aberrant progression for Titanic Griff by constraining the minimum multiplier.
    if normalized == TITANIC_GRIFF_NAME.lower():
        min_multiplier = min(min_multiplier, float(TITANIC_GRIFF_MULTIPLIER) - 1.0)

    final_multiplier = max(min_multiplier, float(max(1, get_huge_multiplier(name))))
    if final_multiplier <= min_multiplier:
        # FIX: Guarantee growth over levels even if custom multipliers are misconfigured.
        final_multiplier = min_multiplier + 1.0
    if level <= 1:
        return min_multiplier

    clamped_level = max(1, min(level, HUGE_PET_LEVEL_CAP))
    span = max(1, HUGE_PET_LEVEL_CAP - 1)
    progress = (clamped_level - 1) / span
    return min_multiplier + (final_multiplier - min_multiplier) * progress


def clamp_income_value(raw_value: float, *, minimum: int = 0) -> int:
    """Clamp un montant de revenu à la plage signée 64 bits."""

    floor = max(0, int(minimum))
    try:
        numeric = float(raw_value)
    except (TypeError, ValueError):
        return floor
    if numeric <= 0 or math.isnan(numeric):
        return floor
    if not math.isfinite(numeric):
        return MAX_PET_INCOME
    try:
        clamped = int(numeric)
    except (OverflowError, ValueError):
        return MAX_PET_INCOME
    if clamped >= MAX_PET_INCOME:
        return MAX_PET_INCOME
    if clamped <= floor:
        return floor
    return clamped


def safe_multiply_income(value: int, multiplier: float) -> int:
    """Multiplie ``value`` par ``multiplier`` sans dépasser les limites."""

    base = max(0, int(value))
    if base == 0 or multiplier <= 0:
        return 0
    try:
        product = float(multiplier) * base
    except (OverflowError, ValueError):
        return MAX_PET_INCOME
    return clamp_income_value(product)


def compute_huge_income(reference_income: int, multiplier: float) -> int:
    """Calcule le revenu effectif d'un Huge en tenant compte des bornes."""

    scaled = safe_multiply_income(reference_income, multiplier)
    return max(HUGE_PET_MIN_INCOME, scaled)


HUGE_PET_SOURCES: Final[Dict[str, str]] = {
    HUGE_PET_NAME: "Extrêmement rare dans l'œuf basique.",
    "Huge Trunk": "Peut apparaître dans l'œuf bio avec un taux minuscule.",
    HUGE_GRIFF_NAME: "Récompense spéciale lors d'événements ou de giveaways du staff.",
    TITANIC_GRIFF_NAME: "Jackpot quasi impossible du casino, 4 000× plus rare que Huge Griff.",
    TITANIC_COLT_NAME: "Récompense mythique octroyée uniquement par l'équipe via le panneau admin.",
    HUGE_GALE_NAME: "Récompense finale du mode Millionaire Race (étape 20).",
    HUGE_KENJI_ONI_NAME: "Récompense rarissime du Mastermind pour les esprits les plus vifs.",
    HUGE_BULL_NAME: "Tirée chaque jour via la tombola Mastermind (tickets garantis par victoire).",
    HUGE_SHADE_NAME: "Extrêmement rare dans l'Œuf Maudit (0.5%) - Zone Manoir Hanté.",
    HUGE_MORTIS_NAME: "Récompense exclusive pour les membres VIP du serveur.",
    HUGE_SURGE_NAME: "Apparaît dans l'Œuf métallique pour les stratèges les plus assidus.",
    HUGE_BO_NAME: "Récompense du mode King of the Hill : défends ton trône pour tenter ta chance !",
    TITANIC_MEEPLE_NAME: "Récompense quasi mythique de l'Œuf métallique, au-delà du légendaire.",
    HUGE_ASTRALIS_NAME: "Pet stellaire de la Citadelle Céleste.",
    TITANIC_ZENITH_NAME: "Joyau cosmique ultime de la Citadelle Céleste.",
    HUGE_VIRGO_COLLETTE_NAME: "Gardienne céleste de la zone Zodiaque.",
    TITANIC_CAPRICORN_STU_NAME: "Titan zodiacal réservé aux plus persévérants.",
    HUGE_CLANCY_NAME: "Se trouve dans l'Œuf vivant de l'Animalerie après ton premier rebirth.",
    HUGE_ROSA_NAME: "Ultra rare dans l'Œuf Huevo de Mexico — seuls les plus courageux la rencontrent.",
    TITANIC_POCO_NAME: "Récompense mythique de l'Œuf Huevo de Mexico, l'égale du Titanic Meeple.",
    HUGE_WISHED_NAME: "0,1% de chance d'apparaître lorsqu'un vol réussit.",
    HUGE_RED_KING_FRANK_NAME: "Récompense d'événement liée à la Millionaire Race pour les coureurs acharnés.",
}

def get_egg_frenzy_window(
    reference: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Retourne la prochaine fenêtre Egg Frenzy en heure locale."""

    if reference is None:
        reference = datetime.now(timezone.utc)
    elif reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    local_now = reference.astimezone(EGG_FRENZY_TIMEZONE)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _apply_offset(base: datetime, target: time) -> datetime:
        delta = timedelta(
            hours=target.hour,
            minutes=target.minute,
            seconds=target.second,
            microseconds=target.microsecond,
        )
        return base + delta

    start_local = _apply_offset(midnight, EGG_FRENZY_START_TIME)
    end_local = _apply_offset(midnight, EGG_FRENZY_END_TIME)

    if EGG_FRENZY_END_TIME <= EGG_FRENZY_START_TIME:
        if end_local <= start_local:
            end_local += timedelta(days=1)
        if local_now < start_local and local_now >= end_local - timedelta(days=1):
            start_local -= timedelta(days=1)
            end_local -= timedelta(days=1)
        if local_now >= end_local:
            start_local += timedelta(days=1)
            end_local += timedelta(days=1)
    else:
        if local_now >= end_local:
            start_local += timedelta(days=1)
            end_local += timedelta(days=1)

    return start_local, end_local


def is_egg_frenzy_active(reference: datetime | None = None) -> bool:
    """Indique si l'Egg Frenzy est actif pour l'instant donné."""

    if reference is None:
        reference = datetime.now(timezone.utc)
    elif reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    start_local, end_local = get_egg_frenzy_window(reference)
    local_now = reference.astimezone(EGG_FRENZY_TIMEZONE)
    return start_local <= local_now < end_local


_BASIC_EGG_PETS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name="Shelly",
        rarity="Commun",
        image_url="https://cdn.discordapp.com/emojis/1430584949215596654.png",
        base_income_per_hour=10,
        drop_rate=0.50,
    ),
    PetDefinition(
        name="Colt",
        rarity="Atypique",
        image_url="https://cdn.discordapp.com/emojis/1430585480394838196.png",
        base_income_per_hour=15,
        drop_rate=0.25,
    ),
    PetDefinition(
        name="Barley",
        rarity="Rare",
        image_url="https://cdn.discordapp.com/emojis/1430586754041381036.png",
        base_income_per_hour=30,
        drop_rate=0.15,
    ),
    PetDefinition(
        name="Poco",
        rarity="Rare",
        image_url="https://cdn.discordapp.com/emojis/1430586108336672878.png",
        base_income_per_hour=60,
        drop_rate=0.08,
    ),
    PetDefinition(
        name="Rosa",
        rarity="Épique",
        image_url="https://cdn.discordapp.com/emojis/1430584871406928075.png",
        base_income_per_hour=150,
        drop_rate=0.019,
    ),
    PetDefinition(
        name=HUGE_PET_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1430587331819212831.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.00015,
        is_huge=True,
    ),
)

_FOREST_EGG_PETS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name="Angelo",
        rarity="Commun",
        image_url="https://example.com/document49.png",
        base_income_per_hour=35,
        drop_rate=0.40,
    ),
    PetDefinition(
        name="Doug",
        rarity="Atypique",
        image_url="https://example.com/document52.png",
        base_income_per_hour=75,
        drop_rate=0.30,
    ),
    PetDefinition(
        name="Lily",
        rarity="Rare",
        image_url="https://example.com/document50.png",
        base_income_per_hour=160,
        drop_rate=0.20,
    ),
    PetDefinition(
        name="Cordelius",
        rarity="Rare",
        image_url="https://example.com/document51.png",
        base_income_per_hour=280,
        drop_rate=0.0995,
    ),
    PetDefinition(
        name="Huge Trunk",
        rarity="Secret",
        image_url="https://example.com/document53.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0005,
        is_huge=True,
    ),
)

_SPECTRAL_EGG_PETS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name="Gus",
        rarity="Commun",
        image_url="https://cdn.discordapp.com/emojis/1431422788266364999.png",
        base_income_per_hour=450,
        drop_rate=0.50,
    ),
    PetDefinition(
        name="Ghost Squeak",
        rarity="Atypique",
        image_url="https://cdn.discordapp.com/emojis/1431422784537628722.png",
        base_income_per_hour=950,
        drop_rate=0.30,
    ),
    PetDefinition(
        name="Ghost Leon",
        rarity="Rare",
        image_url="https://cdn.discordapp.com/emojis/1431422781110882495.png",
        base_income_per_hour=1_800,
        drop_rate=0.18,
    ),
    PetDefinition(
        name="Inspectrice Colette",
        rarity="Épique",
        image_url="https://cdn.discordapp.com/emojis/1431422778170408960.png",
        base_income_per_hour=4_500,
        drop_rate=0.02,
    ),
)

# FIX: Clone spectral pets to avoid shared references when reusing definitions.
def _clone_pet_definition(source: PetDefinition, **overrides: object) -> PetDefinition:
    return replace(source, **overrides)


_CURSED_EGG_PETS: Tuple[PetDefinition, ...] = (
    _clone_pet_definition(_SPECTRAL_EGG_PETS[0], drop_rate=0.40),
    _clone_pet_definition(_SPECTRAL_EGG_PETS[1], drop_rate=0.35),
    _clone_pet_definition(_SPECTRAL_EGG_PETS[2], drop_rate=0.20),
    _clone_pet_definition(_SPECTRAL_EGG_PETS[3], drop_rate=0.045),
    PetDefinition(
        name=HUGE_SHADE_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1431422771094753310.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.005,
        is_huge=True,
    ),
)

_EXCLUSIVE_PETS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name=HUGE_GALE_NAME,
        rarity="Secret",
        image_url="https://example.com/document54.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
    PetDefinition(
        name=HUGE_GRIFF_NAME,
        rarity="Secret",
        image_url="https://example.com/document55.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
    PetDefinition(
        name=TITANIC_GRIFF_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1432161869342183525.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
    PetDefinition(
        name=TITANIC_COLT_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1442530708810760326.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
    PetDefinition(
        name=HUGE_KENJI_ONI_NAME,
        rarity="Secret",
        image_url="https://example.com/document56.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
    PetDefinition(
        name=HUGE_BO_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1435335892712685628.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
    PetDefinition(
        name=HUGE_MORTIS_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1431435110590189638.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
    PetDefinition(
        name=HUGE_BULL_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1433617222357487748.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
    PetDefinition(
        name=HUGE_WISHED_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1459842344592609414.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
    PetDefinition(
        name=HUGE_RED_KING_FRANK_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1442532497979084890.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0,
        is_huge=True,
    ),
)

_ROBOT_EGG_PETS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name="Darryl",
        rarity="Commun",
        image_url="https://cdn.discordapp.com/emojis/1433376220980187177.png",
        base_income_per_hour=10_000,
        drop_rate=0.85,
    ),
    PetDefinition(
        name="Rico",
        rarity="Rare",
        image_url="https://cdn.discordapp.com/emojis/1433376959127228436.png",
        base_income_per_hour=22_500,
        drop_rate=0.10,
    ),
    PetDefinition(
        name="Nani",
        rarity="Épique",
        image_url="https://cdn.discordapp.com/emojis/1433377774122303582.png",
        base_income_per_hour=40_000,
        drop_rate=0.04,
    ),
    PetDefinition(
        name="RT",
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1433378374650429522.png",
        base_income_per_hour=65_000,
        drop_rate=0.0098999,
    ),
    PetDefinition(
        name=HUGE_SURGE_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1433379423133892608.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0001,
        is_huge=True,
    ),
    PetDefinition(
        name=TITANIC_MEEPLE_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1433380006557646878.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0000005,
        is_huge=True,
    ),
)

_ANIMALERIE_EGG_PETS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name="Kit",
        rarity="Commun",
        image_url="https://cdn.discordapp.com/emojis/1433582351702818897.png",
        base_income_per_hour=120_000,
        drop_rate=0.55,
    ),
    PetDefinition(
        name="Crow",
        rarity="Rare",
        image_url="https://cdn.discordapp.com/emojis/1433582901081018458.png",
        base_income_per_hour=240_000,
        drop_rate=0.33,
    ),
    PetDefinition(
        name="Ruffs",
        rarity="Épique",
        image_url="https://cdn.discordapp.com/emojis/1433583510861385759.png",
        base_income_per_hour=520_000,
        drop_rate=0.11,
    ),
    PetDefinition(
        name="Spike",
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1433581944255287377.png",
        base_income_per_hour=1_200_000,
        drop_rate=0.00999,
    ),
    PetDefinition(
        name=HUGE_CLANCY_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1433616256522649712.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.00005,
        is_huge=True,
    ),
)

_MEXICO_EGG_PETS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name="El Primo",
        rarity="Épique",
        image_url="https://cdn.discordapp.com/emojis/1437826192794321097.png",
        base_income_per_hour=2_800_000,
        drop_rate=0.60,
    ),
    PetDefinition(
        name="Amber",
        rarity="Légendaire",
        image_url="https://cdn.discordapp.com/emojis/1437826234095636490.png",
        base_income_per_hour=5_000_000,
        drop_rate=0.30,
    ),
    PetDefinition(
        name="Mina",
        rarity="Mythique",
        image_url="https://cdn.discordapp.com/emojis/1437826273673089238.png",
        base_income_per_hour=9_000_000,
        drop_rate=0.099999,
    ),
    PetDefinition(
        name=HUGE_ROSA_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1437826071503311010.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.000002,
        is_huge=True,
    ),
    PetDefinition(
        name=TITANIC_POCO_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1437826145486770176.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0000005,
        is_huge=True,
    ),
)

_CELESTE_EGG_PETS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name="Stella",
        rarity="Rare",
        image_url="https://cdn.discordapp.com/emojis/1462049982919213169.png",
        base_income_per_hour=25_000_000,
        drop_rate=0.55,
    ),
    PetDefinition(
        name="Nova",
        rarity="Épique",
        image_url="https://cdn.discordapp.com/emojis/1437826273673089238.png",
        base_income_per_hour=60_000_000,
        drop_rate=0.30,
    ),
    PetDefinition(
        name="Orion",
        rarity="Légendaire",
        image_url="https://media.discordapp.net/attachments/1434252768633290952/1462041976114516031/ChatGPT_Image_16_janv._2026__23_13_25-removebg-preview.png?ex=696cc032&is=696b6eb2&hm=3e9ea8ca015117f9f436ffbed91b4a47d874f8e8514819a12de746fa936360af&=&format=webp&quality=lossless&width=514&height=514",
        base_income_per_hour=120_000_000,
        drop_rate=0.12,
    ),
    PetDefinition(
        name="Lyra",
        rarity="Mythique",
        image_url="https://cdn.discordapp.com/emojis/1462050431198036172.png",
        base_income_per_hour=250_000_000,
        drop_rate=0.029,
    ),
    PetDefinition(
        name=HUGE_ASTRALIS_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1462058164223606926.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.000009,
        is_huge=True,
    ),
    PetDefinition(
        name=TITANIC_ZENITH_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1462057986695499850.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.000001,
        is_huge=True,
    ),
)

_ZODIAQUE_EGG_PETS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name="Pisces Piper",
        rarity="Rare",
        image_url="https://cdn.discordapp.com/emojis/1430584949215596654.png",
        base_income_per_hour=600_000_000,
        drop_rate=0.55,
    ),
    PetDefinition(
        name="Scorpion Bibi",
        rarity="Épique",
        image_url="https://cdn.discordapp.com/emojis/1433582351702818897.png",
        base_income_per_hour=1_200_000_000,
        drop_rate=0.30,
    ),
    PetDefinition(
        name="Aquarius Emz",
        rarity="Légendaire",
        image_url="https://cdn.discordapp.com/emojis/1433582901081018458.png",
        base_income_per_hour=2_500_000_000,
        drop_rate=0.11,
    ),
    PetDefinition(
        name="Sagittarius Bo",
        rarity="Mythique",
        image_url="https://cdn.discordapp.com/emojis/1433378374650429522.png",
        base_income_per_hour=5_000_000_000,
        drop_rate=0.029,
    ),
    PetDefinition(
        name=HUGE_VIRGO_COLLETTE_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1431422778170408960.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.000009,
        is_huge=True,
    ),
    PetDefinition(
        name=TITANIC_CAPRICORN_STU_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1433379423133892608.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.000001,
        is_huge=True,
    ),
)

PET_EGG_DEFINITIONS: Tuple[PetEggDefinition, ...] = (
    PetEggDefinition(
        name="Œuf basique",
        slug=DEFAULT_PET_EGG_SLUG,
        price=PET_EGG_PRICE,
        pets=_BASIC_EGG_PETS,
        zone_slug=STARTER_ZONE_SLUG,
        aliases=("oeuf basique", "basique", "basic", "egg"),
    ),
    PetEggDefinition(
        name="Œuf bio",
        slug="bio",
        price=1_300,
        pets=_FOREST_EGG_PETS,
        zone_slug=FORET_ZONE_SLUG,
        aliases=("oeuf bio", "bio"),
    ),
    PetEggDefinition(
        name="Œuf Spectral",
        slug="spectral",
        price=80_000,
        pets=_SPECTRAL_EGG_PETS,
        zone_slug=MANOIR_ZONE_SLUG,
        aliases=("oeuf spectral", "spectral", "ghost", "fantome"),
    ),
    PetEggDefinition(
        name="Œuf Maudit",
        slug="maudit",
        price=250_000,
        pets=_CURSED_EGG_PETS,
        zone_slug=MANOIR_ZONE_SLUG,
        aliases=("oeuf maudit", "maudit", "cursed"),
    ),
    PetEggDefinition(
        name="Œuf métallique",
        slug="metallique",
        price=5_000_000,
        pets=_ROBOT_EGG_PETS,
        zone_slug=ROBOT_ZONE_SLUG,
        aliases=(
            "oeuf metallique",
            "metallique",
            "metal",
            "metallic",
            "robot",
        ),
    ),
    PetEggDefinition(
        name="Œuf vivant",
        slug="vivant",
        price=90_000_000,
        pets=_ANIMALERIE_EGG_PETS,
        zone_slug=ANIMALERIE_ZONE_SLUG,
        aliases=("oeuf vivant", "vivant", "living", "animalerie"),
    ),
    PetEggDefinition(
        name="Œuf Huevo",
        slug="huevo",
        price=500_000_000_000,
        pets=_MEXICO_EGG_PETS,
        zone_slug=MEXICO_ZONE_SLUG,
        aliases=("oeuf huevo", "huevo", "oeuf mexico", "mexico"),
    ),
    PetEggDefinition(
        name="Œuf Céleste",
        slug="celeste",
        price=2_500_000_000_000,
        pets=_CELESTE_EGG_PETS,
        zone_slug=CELESTE_ZONE_SLUG,
        aliases=("oeuf celeste", "celeste", "celestial"),
    ),
    PetEggDefinition(
        name="Œuf Zodiaque",
        slug="zodiaque",
        price=90_000_000_000_000,
        pets=_ZODIAQUE_EGG_PETS,
        zone_slug=ZODIAQUE_ZONE_SLUG,
        aliases=("oeuf zodiaque", "zodiaque", "zodiac"),
    ),
)


def _eggs_for_zone(slug: str) -> Tuple[PetEggDefinition, ...]:
    return tuple(egg for egg in PET_EGG_DEFINITIONS if egg.zone_slug == slug)


PET_ZONES: Tuple[PetZoneDefinition, ...] = (
    PetZoneDefinition(
        name="Zone de départ",
        slug=STARTER_ZONE_SLUG,
        grade_required=0,
        entry_cost=0,
        eggs=_eggs_for_zone(STARTER_ZONE_SLUG),
    ),
    PetZoneDefinition(
        name="Forêt enchantée",
        slug=FORET_ZONE_SLUG,
        grade_required=3,
        entry_cost=50_000,
        eggs=_eggs_for_zone(FORET_ZONE_SLUG),
        egg_mastery_required=3,
    ),
    PetZoneDefinition(
        name="Manoir Hanté",
        slug=MANOIR_ZONE_SLUG,
        grade_required=7,
        entry_cost=500_000,
        eggs=_eggs_for_zone(MANOIR_ZONE_SLUG),
        egg_mastery_required=5,
        pet_mastery_required=5,
    ),
    PetZoneDefinition(
        name="Zone Robotique",
        slug="robotique",
        grade_required=12,
        entry_cost=20_000_000,
        eggs=_eggs_for_zone(ROBOT_ZONE_SLUG),
        egg_mastery_required=10,
        pet_mastery_required=10,
    ),
    PetZoneDefinition(
        name="Animalerie",
        slug=ANIMALERIE_ZONE_SLUG,
        grade_required=12,
        entry_cost=2_500_000_000,
        eggs=_eggs_for_zone(ANIMALERIE_ZONE_SLUG),
        rebirth_required=1,
    ),
    PetZoneDefinition(
        name="Mexico",
        slug=MEXICO_ZONE_SLUG,
        grade_required=15,
        entry_cost=5_000_000_000_000,
        eggs=_eggs_for_zone(MEXICO_ZONE_SLUG),
        rebirth_required=2,
    ),
    PetZoneDefinition(
        name="Citadelle Céleste",
        slug=CELESTE_ZONE_SLUG,
        grade_required=18,
        entry_cost=25_000_000_000_000,
        eggs=_eggs_for_zone(CELESTE_ZONE_SLUG),
        egg_mastery_required=15,
        pet_mastery_required=15,
        rebirth_required=2,
    ),
    PetZoneDefinition(
        name="Zone Zodiaque",
        slug=ZODIAQUE_ZONE_SLUG,
        grade_required=20,
        entry_cost=300_000_000_000_000,
        eggs=_eggs_for_zone(ZODIAQUE_ZONE_SLUG),
        egg_mastery_required=30,
        pet_mastery_required=30,
        rebirth_required=2,
        min_income_required=300_000_000_000,
    ),
    PetZoneDefinition(
        name="Fusée Orbitale",
        slug=ROCKET_ZONE_SLUG,
        grade_required=15,
        entry_cost=rebase_gems_price(1_000_000),
        eggs=(),
        rebirth_required=2,
        currency="gem",
    ),
)


PET_DEFINITIONS: Tuple[PetDefinition, ...] = tuple(
    pet for egg in PET_EGG_DEFINITIONS for pet in egg.pets
) + _EXCLUSIVE_PETS

# Ensemble utilitaire pour identifier rapidement les pets considérés comme "Huge".
HUGE_PET_NAMES: Final[frozenset[str]] = frozenset(
    pet.name for pet in PET_DEFINITIONS if getattr(pet, "is_huge", False)
)

_ROSA_EMOJI: Final[str] = os.getenv("PET_EMOJI_ROSA", "<:Rosa:1430584871406928075>")


PET_EMOJIS: Final[dict[str, str]] = {
    "Shelly": os.getenv("PET_EMOJI_SHELLY", "<:Shelly:1430584949215596654>"),
    "Colt": os.getenv("PET_EMOJI_COLT", "<:Colt:1430585480394838196>"),
    "Barley": os.getenv("PET_EMOJI_BARLEY", "<:Barley:1430586754041381036>"),
    "Poco": os.getenv("PET_EMOJI_POCO", "<:Poco:1430586108336672878>"),
    "Rosa": _ROSA_EMOJI,
    "Mina": os.getenv("PET_EMOJI_MINA", "<:Mina:1437826273673089238>"),
    HUGE_PET_NAME: os.getenv("PET_EMOJI_HUGE_SHELLY", "<:HugeShelly:1430587331819212831>"),
    "Angelo": os.getenv("PET_EMOJI_ANGELO", "<:Angelo:1430873772583289054>"),
    "Lily": os.getenv("PET_EMOJI_LILY", "<:Lily:1430874351309422674>"),
    "Cordelius": os.getenv("PET_EMOJI_CORDELIUS", "<:Cordelius:1430874643572719728>"),
    "Doug": os.getenv("PET_EMOJI_DOUG", "<:Doug:1430875052202786977>"),
    "Huge Trunk": os.getenv("PET_EMOJI_HUGE_TRUNK", "<:HugeTrunk:1430876043400446013>"),
    HUGE_GALE_NAME: os.getenv("PET_EMOJI_HUGE_GALE", "<:HugeGale:1430981225375600641>"),
    HUGE_GRIFF_NAME: os.getenv("PET_EMOJI_HUGE_GRIFF", "<:HugeGriff:1431005620227670036>"),
    TITANIC_GRIFF_NAME: os.getenv(
        "PET_EMOJI_TITANIC_GRIFF", "<:TITANICGRIFF:1432161869342183525>"
    ),
    HUGE_KENJI_ONI_NAME: os.getenv("PET_EMOJI_HUGE_KENJI_ONI", "<:HugeKenjiOni:1431057254337089576>"),
    "Gus": os.getenv("PET_EMOJI_GUS", "<:Gus:1431422788266364999>"),
    "Ghost Squeak": os.getenv("PET_EMOJI_GHOST_SQUEAK", "<:GhostSqueak:1431422784537628722>"),
    "Ghost Leon": os.getenv("PET_EMOJI_GHOST_LEON", "<:GhostLeon:1431422781110882495>"),
    "Inspectrice Colette": os.getenv("PET_EMOJI_INSPECTRICE_COLETTE", "<:InspectriceColette:1431422778170408960>"),
    HUGE_SHADE_NAME: os.getenv("PET_EMOJI_HUGE_SHADE", "<:HugeShade:1431422771094753310>"),
    HUGE_MORTIS_NAME: os.getenv("PET_EMOJI_HUGE_MORTIS", "<:HugeMortis:1431435110590189638>"),
    HUGE_SURGE_NAME: os.getenv("PET_EMOJI_HUGE_SURGE", "<:HugeSurge:1433379423133892608>"),
    TITANIC_MEEPLE_NAME: os.getenv("PET_EMOJI_TITANIC_MEEPLE", "<:TITANICMEEPLE:1433380006557646878>"),
    TITANIC_COLT_NAME: os.getenv("PET_EMOJI_TITANIC_COLT", "<:TitanicColt:1442530708810760326>"),
    HUGE_BULL_NAME: os.getenv("PET_EMOJI_HUGE_BULL", "<:HugeBull:1433617222357487748>"),
    HUGE_WISHED_NAME: (
        os.getenv("PET_EMOJI_TITANIC_WISHED")
        or os.getenv("PET_EMOJI_HUGE_WISHED")
        or "<:HugeWished:1459842344592609414>"
    ),
    HUGE_BO_NAME: os.getenv("PET_EMOJI_HUGE_BO", "<:HugeBo:1435335892712685628>"),
    HUGE_RED_KING_FRANK_NAME: os.getenv(
        "PET_EMOJI_HUGE_RED_KING_FRANK", "<:HugeRedKingFrank:1442532497979084890>"
    ),
    "Darryl": os.getenv("PET_EMOJI_DARRYL", "<:Darryl:1433376220980187177>"),
    "Rico": os.getenv("PET_EMOJI_RICO", "<:Rico:1433376959127228436>"),
    "Nani": os.getenv("PET_EMOJI_NANI", "<:Nani:1433377774122303582>"),
    "RT": os.getenv("PET_EMOJI_RT", "<:RT:1433378374650429522>"),
    "Kit": os.getenv("PET_EMOJI_KIT", "<:Kit:1433582351702818897>"),
    "Crow": os.getenv("PET_EMOJI_CROW", "<:Crow:1433582901081018458>"),
    "Ruffs": os.getenv("PET_EMOJI_RUFFS", "<:Ruffs:1433583510861385759>"),
    "Spike": os.getenv("PET_EMOJI_SPIKE", "<:Spike:1433581944255287377>"),
    HUGE_CLANCY_NAME: os.getenv("PET_EMOJI_HUGE_CLANCY", "<:HugeClancy:1433616256522649712>"),
    "El Primo": os.getenv("PET_EMOJI_EL_PRIMO", "<:ElPrimo:1437826192794321097>"),
    "Amber": os.getenv("PET_EMOJI_AMBER", "<:Amber:1437826234095636490>"),
    "Stella": os.getenv("PET_EMOJI_STELLA", "<:Stella:1462049982919213169>"),
    "Lyra": os.getenv("PET_EMOJI_LYRA", "<:Lyra:1462050431198036172>"),
    "Orion": os.getenv("PET_EMOJI_ORION", "<:Orion:1462047760479031378>"),
    HUGE_ASTRALIS_NAME: os.getenv(
        "PET_EMOJI_HUGE_ASTRALIS", "<:TitanicAstralis:1462058164223606926>"
    ),
    TITANIC_ZENITH_NAME: os.getenv(
        "PET_EMOJI_TITANIC_ZENITH", "<:HugeZenith:1462057986695499850>"
    ),
    HUGE_ROSA_NAME: os.getenv("PET_EMOJI_HUGE_ROSA", "<:HugeRosa:1437826071503311010>"),
    TITANIC_POCO_NAME: os.getenv("PET_EMOJI_TITANIC_POCO", "<:TITANICPOCO:1437826145486770176>"),
    # FIX: Ensure default emoji falls back when the environment variable is empty.
    "default": os.getenv("PET_EMOJI_DEFAULT") or "🐾",
}

HUGE_MORTIS_ROLE_ID: Final[int] = 1431428621959954623
EGG_LUCK_ROLE_ID: Final[int] = 1388837886924685343
VOICE_XP_ROLE_ID: Final[int] = 1441707209955348542
XP_BOOST_ROLE_ID: Final[int] = 1406356891000639660
STEAL_PROTECTED_ROLE_ID: Final[int] = 1440026901568557056
SELLABLE_ROLE_IDS: Final[Tuple[int, ...]] = (
    HUGE_MORTIS_ROLE_ID,
    EGG_LUCK_ROLE_ID,
    VOICE_XP_ROLE_ID,
    XP_BOOST_ROLE_ID,
    STEAL_PROTECTED_ROLE_ID,
)

PET_RARITY_COLORS: Final[dict[str, int]] = {
    "Commun": 0x95A5A6,
    "Atypique": 0x2ECC71,
    "Rare": 0x3498DB,
    "Épique": 0x9B59B6,
    "Secret": 0xF1C40F,
}

PET_RARITY_ORDER: Final[dict[str, int]] = {
    "Commun": 0,
    "Atypique": 1,
    "Rare": 2,
    "Épique": 3,
    "Légendaire": 4,
    "Mythique": 5,
    "Secret": 6,
}
