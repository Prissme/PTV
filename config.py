"""Configuration centralisée et minimaliste pour EcoBot."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Tuple

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
DAILY_REWARD = (100, 200)
DAILY_COOLDOWN = 86_400  # 24 heures
MESSAGE_REWARD = 1
MESSAGE_COOLDOWN = 60
LEADERBOARD_LIMIT = 10

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
    message_goal: int
    invite_goal: int
    egg_goal: int
    gold_goal: int
    reward_pb: int


BASE_PET_SLOTS: Final[int] = 4

GRADE_DEFINITIONS: Tuple[GradeDefinition, ...] = (
    GradeDefinition("Novice", 15, 0, 3, 0, 250),
    GradeDefinition("Apprenti", 30, 0, 5, 0, 400),
    GradeDefinition("Disciple", 60, 0, 8, 1, 550),
    GradeDefinition("Explorateur", 90, 0, 12, 2, 700),
    GradeDefinition("Aventurier", 140, 0, 16, 3, 900),
    GradeDefinition("Expert", 200, 0, 20, 4, 1_100),
    GradeDefinition("Champion", 280, 0, 25, 5, 1_400),
    GradeDefinition("Maître", 360, 0, 30, 6, 1_700),
    GradeDefinition("Prodige", 460, 0, 36, 7, 2_100),
    GradeDefinition("Élite", 580, 0, 43, 8, 2_600),
    GradeDefinition("Légende", 720, 0, 51, 9, 3_200),
    GradeDefinition("Mythique", 880, 0, 60, 10, 3_900),
    GradeDefinition("Cosmique", 1_060, 0, 70, 12, 4_700),
    GradeDefinition("Divin", 1_260, 0, 81, 14, 5_600),
    GradeDefinition("Parangon", 1_500, 0, 93, 16, 6_600),
)

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
# Esthétique
# ---------------------------------------------------------------------------


class Colors:
    SUCCESS = 0x27AE60
    ERROR = 0xE74C3C
    WARNING = 0xF39C12
    INFO = 0x3498DB
    GOLD = 0xF1C40F
    NEUTRAL = 0x95A5A6


class Emojis:
    MONEY = "💰"
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    COOLDOWN = "⏳"
    DAILY = "🎰"
    LEADERBOARD = "🏆"
    XP = "✨"


# ---------------------------------------------------------------------------
# Animaux (Pets)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PetDefinition:
    name: str
    rarity: str
    image_url: str
    base_income_per_hour: int
    drop_rate: float
    is_huge: bool = False


@dataclass(frozen=True)
class PetEggDefinition:
    name: str
    slug: str
    price: int
    pets: Tuple[PetDefinition, ...]
    zone_slug: str
    aliases: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PetZoneDefinition:
    name: str
    slug: str
    grade_required: int
    entry_cost: int
    eggs: Tuple[PetEggDefinition, ...]


PET_EGG_PRICE: Final[int] = 500
DEFAULT_PET_EGG_SLUG: Final[str] = "basique"
STARTER_ZONE_SLUG: Final[str] = "starter"
FORET_ZONE_SLUG: Final[str] = "foret"
GOLD_PET_MULTIPLIER: Final[int] = 3
GOLD_PET_CHANCE: Final[float] = _get_float_env("PET_GOLD_CHANCE", 0.05)
GOLD_PET_COMBINE_REQUIRED: Final[int] = _get_int_env(
    "PET_GOLD_COMBINE_REQUIRED", 10, minimum=2
)
HUGE_PET_NAME: Final[str] = "Huge Shelly"
HUGE_PET_MULTIPLIER: Final[int] = 5
HUGE_PET_MIN_INCOME: Final[int] = 500
HUGE_GALE_NAME: Final[str] = "Huge Gale"
HUGE_GRIFF_NAME: Final[str] = "Huge Griff"

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
        drop_rate=0.001,
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
        drop_rate=0.095,
    ),
    PetDefinition(
        name="Huge Trunk",
        rarity="Secret",
        image_url="https://example.com/document53.png",
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
        grade_required=1,
        entry_cost=5_000,
        eggs=_eggs_for_zone(FORET_ZONE_SLUG),
    ),
)


PET_DEFINITIONS: Tuple[PetDefinition, ...] = tuple(
    pet for egg in PET_EGG_DEFINITIONS for pet in egg.pets
) + _EXCLUSIVE_PETS

PET_EMOJIS: Final[dict[str, str]] = {
    "Shelly": os.getenv("PET_EMOJI_SHELLY", "<:Shelly:1430584949215596654>"),
    "Colt": os.getenv("PET_EMOJI_COLT", "<:Colt:1430585480394838196>"),
    "Barley": os.getenv("PET_EMOJI_BARLEY", "<:Barley:1430586754041381036>"),
    "Poco": os.getenv("PET_EMOJI_POCO", "<:Poco:1430586108336672878>"),
    "Rosa": os.getenv("PET_EMOJI_ROSA", "<:Rosa:1430584871406928075>"),
    HUGE_PET_NAME: os.getenv("PET_EMOJI_HUGE_SHELLY", "<:HugeShelly:1430587331819212831>"),
    "Angelo": os.getenv("PET_EMOJI_ANGELO", "<:Angelo:1430873772583289054>"),
    "Lily": os.getenv("PET_EMOJI_LILY", "<:Lily:1430874351309422674>"),
    "Cordelius": os.getenv("PET_EMOJI_CORDELIUS", "<:Cordelius:1430874643572719728>"),
    "Doug": os.getenv("PET_EMOJI_DOUG", "<:Doug:1430875052202786977>"),
    "Huge Trunk": os.getenv("PET_EMOJI_HUGE_TRUNK", "<:HugeTrunk:1430876043400446013>"),
    HUGE_GALE_NAME: os.getenv("PET_EMOJI_HUGE_GALE", "<:HugeGale:1430981225375600641>"),
    HUGE_GRIFF_NAME: os.getenv("PET_EMOJI_HUGE_GRIFF", "<:HugeGriff:1431005620227670036>"),
    "default": os.getenv("PET_EMOJI_DEFAULT", "🐾"),
}

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

