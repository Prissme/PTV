import os
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

# ==================== BOT SETTINGS ====================
TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("PREFIX", "e!")
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

# Transfer limits et taxes - CORRIGÉ
TRANSFER_MIN = 1
TRANSFER_MAX = 100000  # Limite maximale de transfert
TRANSFER_COOLDOWN = 5  # secondes
TRANSFER_TAX_RATE = 0.05  # 5% de taxe sur les transferts (CORRIGÉ: était 0.02)

# Shop taxes - AJOUTÉ
SHOP_TAX_RATE = 0.05  # 5% de taxe sur les achats (MANQUAIT)

# Message rewards
MESSAGE_REWARD_AMOUNT = 1
MESSAGE_REWARD_COOLDOWN = 20  # 20 secondes

# ==================== SHOP SETTINGS ====================
ITEMS_PER_PAGE = 5
MAX_LEADERBOARD_LIMIT = 20
DEFAULT_LEADERBOARD_LIMIT = 10

# ==================== STEAL SETTINGS ====================
STEAL_SUCCESS_RATE = 70  # 50% de chances de réussite
STEAL_PERCENTAGE = 30  # Vol 10% des pièces
STEAL_FAIL_PENALTY_PERCENTAGE = 40  # Perd 50% si échec
STEAL_COOLDOWN_HOURS = 0.5  # Cooldown de 0.5 heure (CORRIGÉ: était 0,5)
STEAL_COOLDOWN_SECONDS = int(STEAL_COOLDOWN_HOURS * 3600)  # 1800 secondes

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
    TAX = "🏛️"