import os
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

# ==================== BOT SETTINGS ====================
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("PREFIX", "!")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Vérification des variables critiques
if not TOKEN:
    raise ValueError("❌ DISCORD_TOKEN manquant dans le fichier .env")

# ==================== DATABASE SETTINGS ====================
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL manquant dans le fichier .env")

# ==================== ECONOMY SETTINGS ====================
# Daily rewards
DAILY_MIN = 50
DAILY_MAX = 150
DAILY_BONUS_CHANCE = 10  # 10% de chance de bonus
DAILY_BONUS_MIN = 50
DAILY_BONUS_MAX = 200
DAILY_COOLDOWN = 86400  # 24 heures

# Transfer limits
TRANSFER_MIN = 1
TRANSFER_MAX = 100000
TRANSFER_COOLDOWN = 5  # secondes

# ==================== SHOP SETTINGS ====================
ITEMS_PER_PAGE = 5
MAX_LEADERBOARD_LIMIT = 20
DEFAULT_LEADERBOARD_LIMIT = 10

# ==================== HEALTH SERVER SETTINGS ====================
HEALTH_PORT = int(os.getenv("PORT", 8000))

# ==================== LOGGING SETTINGS ====================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ==================== BOT COLORS ====================
class Colors:
    SUCCESS = 0x00ff00
    ERROR = 0xff0000
    WARNING = 0xff9900
    INFO = 0x0099ff
    PREMIUM = 0x9932cc
    GOLD = 0xffd700

# ==================== BOT EMOJIS ====================
class Emojis:
    MONEY = "💰"
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    LOADING = "⏳"
    COOLDOWN = "⏰"
    SHOP = "🛍️"
    ROLE = "🎭"
    DAILY = "🎰"
    LEADERBOARD = "🏆"
    TRANSFER = "💸"
    INVENTORY = "📦"
    PREMIUM = "🌟"
