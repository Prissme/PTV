"""Configuration centralisée et minimaliste pour EcoBot."""
from __future__ import annotations

import os

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

