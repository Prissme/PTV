import discord
from discord.ext import commands
import logging

from config import Colors, MAX_LEADERBOARD_LIMIT, DEFAULT_LEADERBOARD_LIMIT
from utils.embeds import create_error_embed

logger = logging.getLogger(__name__)

class Leaderboard(commands.Cog):
    """Système de classement des PrissBucks ultra clean"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Leaderboard initialisé")

    @commands.command(name='leaderboard', aliases=['top', 'lb', 'rich', 'classement'])
    async def leaderboard_cmd(self, ctx, limit: int = DEFAULT_LEADERBOARD_LIMIT):
        """Affiche le classement des plus riches"""
        
        # Validation de la limite
        if limit < 1:
            limit = DEFAULT_LEADERBOARD_LIMIT
        elif limit > MAX_LEADERBOARD_LIMIT:
            limit = MAX_LEADERBOARD_LIMIT
        
        try:
            # Récupérer les top utilisateurs
            top_users = await self.db.get_top_users(limit)
            
            if not top_users:
                embed = create_error_embed(
                    "Classement vide", 
                    "Aucun utilisateur n'a encore de PrissBucks."
                )
                await ctx.send(embed=embed)
                return
            
            # Créer l'embed ultra clean
            embed = discord.Embed(
                title="Classement PrissBucks",
                color=Colors.GOLD
            )
            
            # Construire la description avec le classement
            description = ""
            for i, (user_id, balance) in enumerate(top_users, 1):
                try:
                    user = self.bot.get_user(user_id)
                    if user:
                        username = user.display_name
                    else:
                        # Fallback si l'utilisateur n'est pas en cache
                        try:
                            user = await self.bot.fetch_user(user_id)
                            username = user.display_name
                        except:
                            username = f"Utilisateur {user_id}"
                except:
                    username = f"Utilisateur {user_id}"
                
                # Format simple et clean
                description += f"`{i:2d}.` **{username}** — {balance:,}\n"
            
            embed.description = description
            
            # Footer minimaliste
            embed.set_footer(text=f"Top {len(top_users)} • PrissBucks")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur leaderboard: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage du classement.")
            await ctx.send(embed=embed)

    @commands.command(name='rank', aliases=['rang', 'position'])
    async def rank_cmd(self, ctx, user: discord.Member = None):
        """Affiche le rang d'un utilisateur dans le classement"""
        target = user or ctx.author
        
        try:
            # Récupérer tous les utilisateurs pour calculer le rang
            all_users = await self.db.get_top_users(1000)  # Limite élevée pour avoir tout le monde
            
            if not all_users:
                embed = create_error_embed(
                    "Aucun classement",
                    "Aucun utilisateur n'a encore de PrissBucks."
                )
                await ctx.send(embed=embed)
                return
            
            # Trouver la position de l'utilisateur
            user_rank = None
            user_balance = 0
            
            for i, (user_id, balance) in enumerate(all_users, 1):
                if user_id == target.id:
                    user_rank = i
                    user_balance = balance
                    break
            
            # Si l'utilisateur n'est pas dans le classement
            if user_rank is None:
                user_balance = await self.db.get_balance(target.id)
                if user_balance == 0:
                    embed = discord.Embed(
                        title=f"Rang de {target.display_name}",
                        description=f"**Position :** Non classé\n**Solde :** 0 PrissBucks",
                        color=Colors.WARNING
                    )
                else:
                    # Cas rare où l'utilisateur a des PrissBucks mais n'apparaît pas dans le top
                    embed = discord.Embed(
                        title=f"Rang de {target.display_name}",
                        description=f"**Position :** 1000+\n**Solde :** {user_balance:,} PrissBucks",
                        color=Colors.INFO
                    )
            else:
                # Afficher le rang
                embed = discord.Embed(
                    title=f"Rang de {target.display_name}",
                    description=f"**Position :** #{user_rank}\n**Solde :** {user_balance:,} PrissBucks",
                    color=Colors.SUCCESS if user_rank <= 10 else Colors.INFO
                )
            
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.set_footer(text="Utilise 'leaderboard' pour voir le classement complet")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur rank pour {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération du rang.")
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Leaderboard(bot))
