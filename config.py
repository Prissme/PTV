"""Configuration centralisée du bot EcoBot.

Toutes les valeurs par défaut proviennent de la spécification utilisateur.  Les
variables sensibles (token, DSN…) sont chargées depuis ``.env`` via
:func:`dotenv.load_dotenv`.
"""
from __future__ import annotations

import os
from typing import Dict

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Informations essentielles
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("PREFIX", "e!")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
HEALTH_PORT = int(os.getenv("PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

if not TOKEN:
    raise ValueError("DISCORD_TOKEN manquant dans le fichier .env")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL manquant dans le fichier .env")

# ---------------------------------------------------------------------------
# Économie générale
# ---------------------------------------------------------------------------
DAILY_MIN = 50
DAILY_MAX = 150
DAILY_COOLDOWN = 86400  # 24h
DAILY_BONUS_CHANCE = 0.10
DAILY_BONUS_MIN = 50
DAILY_BONUS_MAX = 200
MESSAGE_REWARD_AMOUNT = 1
MESSAGE_REWARD_COOLDOWN = 20
TRANSFER_MIN = 1
TRANSFER_MAX = 100_000
TRANSFER_TAX_RATE = 0.05
TRANSFER_COOLDOWN = 5
SHOP_TAX_RATE = 0.05
ITEMS_PER_PAGE = 5
MAX_LEADERBOARD_LIMIT = 20

# ---------------------------------------------------------------------------
# Expérience & rôles
# ---------------------------------------------------------------------------
XP_BASE_PER_MESSAGE = 10
XP_COOLDOWN = 60
XP_LEVEL_BASE = 150
XP_LEVEL_MULTIPLIER = 1.35
XP_ROLE_BOOSTS: Dict[str, float] = {
    "E": 0.05,
    "D": 0.10,
    "C": 0.20,
    "B": 0.35,
    "A": 0.50,
    "S": 0.75,
    "SS": 0.90,
    "SSS": 1.00,
}
XP_ROLE_COSTS: Dict[str, int] = {
    "E": 1_000,
    "D": 2_000,
    "C": 3_500,
    "B": 5_000,
    "A": 7_500,
    "S": 10_000,
    "SS": 15_000,
    "SSS": 20_000,
}

# ---------------------------------------------------------------------------
# Banque privée
# ---------------------------------------------------------------------------
PRIVATE_BANK_MAX_BALANCE = 100_000
PRIVATE_BANK_DAILY_LIMIT = 15_000
PRIVATE_BANK_DEPOSIT_TAX = 0.02
PRIVATE_BANK_MAINTENANCE_FEE = 0.02
PRIVATE_BANK_FEE_THRESHOLD = 500

# ---------------------------------------------------------------------------
# Banque publique
# ---------------------------------------------------------------------------
PUBLIC_BANK_WITHDRAW_MIN = 50
PUBLIC_BANK_WITHDRAW_MAX = 1_000
PUBLIC_BANK_DAILY_LIMIT = 2_000
PUBLIC_BANK_WITHDRAW_COOLDOWN = 1800  # 30 minutes

# ---------------------------------------------------------------------------
# Casino & mini-jeux
# ---------------------------------------------------------------------------
ROULETTE_COOLDOWN = 4
RPS_COOLDOWN = 10
STEAL_COOLDOWN = 1800
STEAL_SUCCESS_RATE = 0.50
STEAL_PERCENTAGE = 0.25
STEAL_FAIL_PENALTY_PERCENTAGE = 0.50

# ---------------------------------------------------------------------------
# Esthétique
# ---------------------------------------------------------------------------

class Colors:
    SUCCESS = 0x27ae60
    ERROR = 0xe74c3c
    WARNING = 0xf39c12
    INFO = 0x3498db
    PREMIUM = 0x9b59b6
    GOLD = 0xf1c40f
    NEUTRAL = 0x95a5a6


class Emojis:
    MONEY = "💰"
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    COOLDOWN = "⏳"
    SHOP = "🛍️"
    ROLE = "🎭"
    DAILY = "🎰"
    LEADERBOARD = "🏆"
    TRANSFER = "💸"
    INVENTORY = "📦"
    PREMIUM = "🌟"
    TAX = "🏛️"
    PUBLIC_BANK = "🏛️"
    BANK = "🏦"
    XP = "✨"

