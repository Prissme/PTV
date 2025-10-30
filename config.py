"""Configuration centralisée et minimaliste pour EcoBot."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace
from typing import Dict, Final, Tuple

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
CLAN_BOOST_INCREMENT: Final[float] = 0.05
CLAN_BOOST_COSTS: Final[Tuple[int, ...]] = (
    50_000,
    125_000,
    250_000,
    450_000,
    750_000,
)

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
# Rôles spéciaux
# ---------------------------------------------------------------------------

VIP_ROLE_ID: Final[int] = 1_431_428_621_959_954_623

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
MANOIR_ZONE_SLUG: Final[str] = "manoir_hante"
GOLD_PET_MULTIPLIER: Final[int] = 3
GOLD_PET_CHANCE: Final[float] = _get_float_env("PET_GOLD_CHANCE", 0.05)
GOLD_PET_COMBINE_REQUIRED: Final[int] = _get_int_env(
    "PET_GOLD_COMBINE_REQUIRED", 10, minimum=2
)
RAINBOW_PET_MULTIPLIER: Final[int] = 10
RAINBOW_PET_COMBINE_REQUIRED: Final[int] = 10
RAINBOW_PET_CHANCE: Final[float] = 0.01
HUGE_PET_NAME: Final[str] = "Huge Shelly"
HUGE_PET_MULTIPLIER: Final[int] = 6
HUGE_PET_MIN_INCOME: Final[int] = 600
HUGE_PET_LEVEL_CAP: Final[int] = 80
HUGE_PET_LEVEL_BASE_XP: Final[int] = 120
HUGE_PET_LEVEL_EXPONENT: Final[float] = 1.5
HUGE_GALE_NAME: Final[str] = "Huge Gale"
HUGE_GRIFF_NAME: Final[str] = "Huge Griff"
TITANIC_GRIFF_NAME: Final[str] = "Titanic Griff"
HUGE_KENJI_ONI_NAME: Final[str] = "Huge Kenji Oni"
HUGE_GRIFF_MULTIPLIER: Final[int] = 4
TITANIC_GRIFF_MULTIPLIER: Final[int] = 400
HUGE_GALE_MULTIPLIER: Final[int] = 80
HUGE_KENJI_ONI_MULTIPLIER: Final[int] = 9
HUGE_SHADE_NAME: Final[str] = "Huge Shade"
HUGE_SHADE_MULTIPLIER: Final[int] = 6
HUGE_MORTIS_NAME: Final[str] = "Huge Mortis"
HUGE_MORTIS_MULTIPLIER: Final[int] = 9
HUGE_PET_CUSTOM_MULTIPLIERS: Final[Dict[str, int]] = {
    HUGE_GRIFF_NAME: HUGE_GRIFF_MULTIPLIER,
    HUGE_GALE_NAME: HUGE_GALE_MULTIPLIER,
    HUGE_KENJI_ONI_NAME: HUGE_KENJI_ONI_MULTIPLIER,
    HUGE_SHADE_NAME: HUGE_SHADE_MULTIPLIER,
    HUGE_MORTIS_NAME: HUGE_MORTIS_MULTIPLIER,
    TITANIC_GRIFF_NAME: TITANIC_GRIFF_MULTIPLIER,
}

HUGE_PET_MIN_LEVEL_MULTIPLIERS: Final[Dict[str, float]] = {
    TITANIC_GRIFF_NAME: 12.0,
}


def get_huge_multiplier(name: str) -> int:
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


HUGE_PET_SOURCES: Final[Dict[str, str]] = {
    HUGE_PET_NAME: "Extrêmement rare dans l'œuf basique.",
    "Huge Trunk": "Peut apparaître dans l'œuf bio avec un taux minuscule.",
    HUGE_GRIFF_NAME: "Récompense spéciale lors d'événements ou de giveaways du staff.",
    TITANIC_GRIFF_NAME: "Jackpot quasi impossible du casino, 4 000× plus rare que Huge Griff.",
    HUGE_GALE_NAME: "Récompense finale du mode Millionaire Race (étape 20).",
    HUGE_KENJI_ONI_NAME: "Récompense rarissime du Mastermind pour les esprits les plus vifs.",
    HUGE_SHADE_NAME: "Extrêmement rare dans l'Œuf Maudit (0.5%) - Zone Manoir Hanté.",
    HUGE_MORTIS_NAME: "Récompense exclusive pour les membres VIP du serveur.",
}

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
        drop_rate=0.095,
    ),
    PetDefinition(
        name="Huge Trunk",
        rarity="Secret",
        image_url="https://example.com/document53.png",
        base_income_per_hour=HUGE_PET_MIN_INCOME,
        drop_rate=0.004,
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
        name=HUGE_MORTIS_NAME,
        rarity="Secret",
        image_url="https://cdn.discordapp.com/emojis/1431435110590189638.png",
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
    PetEggDefinition(
        name="Œuf Spectral",
        slug="spectral",
        price=8_000,
        pets=_SPECTRAL_EGG_PETS,
        zone_slug=MANOIR_ZONE_SLUG,
        aliases=("oeuf spectral", "spectral", "ghost", "fantome"),
    ),
    PetEggDefinition(
        name="Œuf Maudit",
        slug="maudit",
        price=25_000,
        pets=_CURSED_EGG_PETS,
        zone_slug=MANOIR_ZONE_SLUG,
        aliases=("oeuf maudit", "maudit", "cursed"),
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
    PetZoneDefinition(
        name="Manoir Hanté",
        slug=MANOIR_ZONE_SLUG,
        grade_required=8,
        entry_cost=50_000,
        eggs=_eggs_for_zone(MANOIR_ZONE_SLUG),
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

