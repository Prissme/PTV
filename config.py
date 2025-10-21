"""Configuration centralisée et minimaliste pour EcoBot."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Tuple

from dotenv import load_dotenv

load_dotenv()

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
# Paramètres XP
# ---------------------------------------------------------------------------
XP_PER_MESSAGE = (10, 15)
XP_COOLDOWN = 60
XP_LEVEL_BASE = 100
XP_LEVEL_MULTIPLIER = 1.5

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


PET_EGG_PRICE: Final[int] = 500
HUGE_PET_NAME: Final[str] = "ÉNORME SHELLY"

PET_DEFINITIONS: Tuple[PetDefinition, ...] = (
    PetDefinition(
        name="Shelly",
        rarity="Common",
        image_url="https://example.com/document43.png",
        base_income_per_hour=10,
        drop_rate=0.50,
    ),
    PetDefinition(
        name="Colt",
        rarity="Common",
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
        drop_rate=0.06,
    ),
    PetDefinition(
        name="Rosa",
        rarity="Super Rare",
        image_url="https://example.com/document47.png",
        base_income_per_hour=150,
        drop_rate=0.03,
    ),
    PetDefinition(
        name=HUGE_PET_NAME,
        rarity="Mythic",
        image_url="https://example.com/document48.png",
        base_income_per_hour=500,
        drop_rate=0.01,
        is_huge=True,
    ),
)

PET_RARITY_COLORS: Final[dict[str, int]] = {
    "Common": 0x95A5A6,
    "Rare": 0x3498DB,
    "Super Rare": 0x9B59B6,
    "Mythic": 0xF1C40F,
}

PET_RARITY_ORDER: Final[dict[str, int]] = {
    "Common": 0,
    "Rare": 1,
    "Super Rare": 2,
    "Mythic": 3,
}

