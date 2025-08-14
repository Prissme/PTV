import discord
from discord.ext import commands
from discord import app_commands
import logging

from config import Colors, MAX_LEADERBOARD_LIMIT, DEFAULT_LEADERBOARD_LIMIT
from utils.embeds import create_error_embed, create_success_embed

logger = logging.getLogger(__name__)

class Leaderboard(commands.Cog):
    """Système de classement des PrissBucks complet"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Leaderboard initialisé avec slash commands")

    # ==================== LEADERBOARD COMMANDS ====================

    @commands.command(name='leaderboard', aliases=['top', 'lb', 'rich', 'classement'])
    async def leaderboard_cmd(self, ctx, limit: int = DEFAULT_LEADERBOARD_LIMIT):
        """Affiche le class