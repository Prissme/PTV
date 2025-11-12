"""Configuration centralisée et minimaliste pour EcoBot."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta, timezone
from typing import Dict, Final, Tuple

try:  # Python 3.9+ fournit zoneinfo, mais nous gardons un repli.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - environnement minimaliste
    ZoneInfo = None  # type: ignore[misc, assignment]

from dotenv import load_dotenv

load_dotenv()


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
DAILY_REWARD = (10_000, 20_000)
DAILY_COOLDOWN = 86_400  # 24 heures
MESSAGE_REWARD = 1
MESSAGE_COOLDOWN = 60
LEADERBOARD_LIMIT = 10

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


BASE_PET_SLOTS: Final[int] = 4
RAP_GOAL_UNIT: Final[int] = 50_000
CASINO_LOSS_GOAL_UNIT: Final[int] = 5_000

_GRADE_BLUEPRINTS: Tuple[tuple[str, int, int, int, int, int], ...] = (
    ("Novice", 0, 3, 1, 1, 250),
    ("Apprenti", 1, 5, 1, 1, 400),
    ("Disciple", 1, 8, 2, 1, 550),
    ("Explorateur", 1, 12, 3, 2, 700),
    ("Aventurier", 2, 16, 5, 2, 900),
    ("Expert", 2, 20, 8, 3, 1_100),
    ("Champion", 2, 25, 12, 3, 1_400),
    ("Maître", 3, 30, 18, 4, 1_700),
    ("Prodige", 3, 36, 27, 4, 2_100),
    ("Élite", 4, 43, 40, 5, 2_600),
    ("Légende", 5, 51, 60, 5, 3_200),
    ("Mythique", 6, 60, 90, 6, 3_900),
    ("Cosmique", 7, 70, 135, 7, 4_700),
    ("Divin", 8, 81, 200, 8, 5_600),
    ("Parangon", 9, 93, 300, 9, 6_600),
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
    currency: str = "pb"


EGG_FRENZY_LUCK_BONUS: Final[float] = 0.50
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
HUGE_PET_LEVEL_CAP: Final[int] = 80
HUGE_PET_LEVEL_BASE_XP: Final[int] = 200
HUGE_PET_LEVEL_EXPONENT: Final[float] = 2
HUGE_GALE_NAME: Final[str] = "Huge Gale"
HUGE_GRIFF_NAME: Final[str] = "Huge Griff"
HUGE_BULL_NAME: Final[str] = "Huge Bull"
TITANIC_GRIFF_NAME: Final[str] = "Titanic Griff"
HUGE_KENJI_ONI_NAME: Final[str] = "Huge Kenji Oni"
HUGE_GRIFF_MULTIPLIER: Final[float] = 6
TITANIC_GRIFF_MULTIPLIER: Final[float] = 35
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
HUGE_PET_CUSTOM_MULTIPLIERS: Final[Dict[str, float]] = {
    HUGE_GRIFF_NAME: HUGE_GRIFF_MULTIPLIER,
    HUGE_GALE_NAME: HUGE_GALE_MULTIPLIER,
    HUGE_KENJI_ONI_NAME: HUGE_KENJI_ONI_MULTIPLIER,
    HUGE_SHADE_NAME: HUGE_SHADE_MULTIPLIER,
    HUGE_MORTIS_NAME: HUGE_MORTIS_MULTIPLIER,
    TITANIC_GRIFF_NAME: TITANIC_GRIFF_MULTIPLIER,
    HUGE_SURGE_NAME: HUGE_SURGE_MULTIPLIER,
    TITANIC_MEEPLE_NAME: TITANIC_MEEPLE_MULTIPLIER,
    HUGE_BULL_NAME: HUGE_BULL_MULTIPLIER,
    HUGE_BO_NAME: HUGE_BO_MULTIPLIER,
    HUGE_CLANCY_NAME: HUGE_CLANCY_MULTIPLIER,
    HUGE_ROSA_NAME: HUGE_ROSA_MULTIPLIER,
    TITANIC_POCO_NAME: TITANIC_POCO_MULTIPLIER,
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
    required = math.ceil(HUGE_PET_LEVEL_BASE_XP * (level**HUGE_PET_LEVEL_EXPONENT))
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
    HUGE_GALE_NAME: "Récompense finale du mode Millionaire Race (étape 20).",
    HUGE_KENJI_ONI_NAME: "Récompense rarissime du Mastermind pour les esprits les plus vifs.",
    HUGE_BULL_NAME: "Tirée chaque jour via la tombola Mastermind (tickets garantis par victoire).",
    HUGE_SHADE_NAME: "Extrêmement rare dans l'Œuf Maudit (0.5%) - Zone Manoir Hanté.",
    HUGE_MORTIS_NAME: "Récompense exclusive pour les membres VIP du serveur.",
    HUGE_SURGE_NAME: "Apparaît dans l'Œuf métallique pour les stratèges les plus assidus.",
    HUGE_BO_NAME: "Récompense du mode King of the Hill : défends ton trône pour tenter ta chance !",
    TITANIC_MEEPLE_NAME: "Récompense quasi mythique de l'Œuf métallique, au-delà du légendaire.",
    HUGE_CLANCY_NAME: "Se trouve dans l'Œuf vivant de l'Animalerie après ton premier rebirth.",
    HUGE_ROSA_NAME: "Ultra rare dans l'Œuf Huevo de Mexico — seuls les plus courageux la rencontrent.",
    TITANIC_POCO_NAME: "Récompense mythique de l'Œuf Huevo de Mexico, l'égale du Titanic Meeple.",
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
        image_url="https://example.com/document43.png",
        base_income_per_hour=10,
        drop_rate=0.50,
    ),
    PetDefinition(
        name="Colt",
        rarity="Atypique",
        image_url="https://example.com/document44.png",
        base_income_per_hour=15,
        drop_rate=0.25,
    ),
    PetDefinition(
        name="Barley",
        rarity="Rare",
        image_url="https://example.com/document45.png",
        base_income_per_hour=30,
        drop_rate=0.15,
    ),
    PetDefinition(
        name="Poco",
        rarity="Rare",
        image_url="https://example.com/document46.png",
        base_income_per_hour=60,
        drop_rate=0.08,
    ),
    PetDefinition(
        name="Rosa",
        rarity="Épique",
        image_url="https://example.com/document47.png",
        base_income_per_hour=150,
        drop_rate=0.019,
    ),
    PetDefinition(
        name=HUGE_PET_NAME,
        rarity="Secret",
        image_url="https://example.com/document48.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0015,
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
        drop_rate=0.0000001,
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
        drop_rate=0.00001,
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
        image_url="https://cdn.discordapp.com/emojis/1430584871406928075.png",
        base_income_per_hour=9_000_000,
        drop_rate=0.099999,
    ),
    PetDefinition(
        name=HUGE_ROSA_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1437826071503311010.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0000005,
        is_huge=True,
    ),
    PetDefinition(
        name=TITANIC_POCO_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1437826145486770176.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.0000001,
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
        price=77_777_777_777,
        pets=_MEXICO_EGG_PETS,
        zone_slug=MEXICO_ZONE_SLUG,
        aliases=("oeuf huevo", "huevo", "oeuf mexico", "mexico"),
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
    ),
    PetZoneDefinition(
        name="Manoir Hanté",
        slug=MANOIR_ZONE_SLUG,
        grade_required=7,
        entry_cost=500_000,
        eggs=_eggs_for_zone(MANOIR_ZONE_SLUG),
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
        entry_cost=500_000_000,
        eggs=_eggs_for_zone(ANIMALERIE_ZONE_SLUG),
        rebirth_required=1,
    ),
    PetZoneDefinition(
        name="Mexico",
        slug=MEXICO_ZONE_SLUG,
        grade_required=15,
        entry_cost=777_777_777_777,
        eggs=_eggs_for_zone(MEXICO_ZONE_SLUG),
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
    "Mina": os.getenv("PET_EMOJI_MINA", _ROSA_EMOJI),
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
    HUGE_BULL_NAME: os.getenv("PET_EMOJI_HUGE_BULL", "<:HugeBull:1433617222357487748>"),
    HUGE_BO_NAME: os.getenv("PET_EMOJI_HUGE_BO", "<:HugeBo:1435335892712685628>"),
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
    HUGE_ROSA_NAME: os.getenv("PET_EMOJI_HUGE_ROSA", "<:HugeRosa:1437826071503311010>"),
    TITANIC_POCO_NAME: os.getenv("PET_EMOJI_TITANIC_POCO", "<:TITANICPOCO:1437826145486770176>"),
    # FIX: Ensure default emoji falls back when the environment variable is empty.
    "default": os.getenv("PET_EMOJI_DEFAULT") or "🐾",
}

HUGE_MORTIS_ROLE_ID: Final[int] = 1431428621959954623

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
    "Secret": 4,
}

