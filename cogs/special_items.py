import discord
from discord.ext import commands
import logging

from config import Colors, Emojis
from utils.embeds import create_error_embed, create_success_embed

logger = logging.getLogger(__name__)

class SpecialItems(commands.Cog):
    """Gestionnaire des items spéciaux du shop"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog SpecialItems initialisé")
    
    @commands.command(name='cooldowns', aliases=['cd', 'mycooldowns'])
    async def check_cooldowns(self, ctx):
        """Affiche tous les cooldowns actifs de l'utilisateur"""
        user_id = ctx.author.id
        active_cooldowns = []
        
        try:
            # Vérifier les cooldowns dans tous les cogs
            for cog_name, cog in self.bot.cogs.items():
                try:
                    # Cooldowns personnalisés (MessageRewards, Steal, etc.)
                    if hasattr(cog, 'cooldowns') and hasattr(cog, 'get_cooldown_remaining'):
                        remaining = cog.get_cooldown_remaining(user_id)
                        if remaining > 0:
                            time_str = self.format_cooldown_time(remaining)
                            active_cooldowns.append(f"🔸 **{cog_name}**: {time_str}")
                    
                    # Cooldowns Discord.py
                    for command in cog.get_commands():
                        if hasattr(command, '_buckets') and command._buckets:
                            bucket = command._buckets.get_bucket(ctx.message)
                            if bucket and bucket._tokens < bucket._per:
                                retry_after = bucket.get_retry_after()
                                if retry_after > 0:
                                    time_str = self.format_cooldown_time(retry_after)
                                    active_cooldowns.append(f"🔸 **{command.name}**: {time_str}")
                                    
                except Exception as e:
                    logger.error(f"Erreur vérification cooldown {cog_name}: {e}")
                    continue
            
            # Créer l'embed
            if active_cooldowns:
                embed = discord.Embed(
                    title=f"⏰ Cooldowns de {ctx.author.display_name}",
                    description="\n".join(active_cooldowns),
                    color=Colors.WARNING
                )
            else:
                embed = discord.Embed(
                    title=f"✅ Aucun cooldown actif",
                    description=f"**{ctx.author.display_name}** peut utiliser toutes les commandes !",
                    color=Colors.SUCCESS
                )
            
            embed.set_thumbnail(url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur check cooldowns {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la vérification des cooldowns.")
            await ctx.send(embed=embed)
    
    def format_cooldown_time(self, seconds: int) -> str:
        """Formate le temps de cooldown en format lisible"""
        if seconds <= 0:
            return "Disponible"
            
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes}min {secs}s"
        elif minutes > 0:
            return f"{minutes}min {secs}s"
        else:
            return f"{secs}s"

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(SpecialItems(bot))