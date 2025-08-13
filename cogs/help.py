import discord
from discord.ext import commands
import logging

from config import PREFIX, Colors, Emojis

logger = logging.getLogger(__name__)

class Help(commands.Cog):
    """Aide basique pour les 5 commandes essentielles"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        logger.info("✅ Cog Help initialisé (minimal)")

    @commands.command(name='help', aliases=['h', 'aide'])
    async def help_cmd(self, ctx):
        """Affiche l'aide du bot (5 commandes essentielles)"""
        try:
            embed = discord.Embed(
                title="🤖 Bot Économie - Aide",
                description="**5 commandes essentielles disponibles :**",
                color=Colors.INFO
            )

            # Commandes économie
            embed.add_field(
                name=f"{Emojis.MONEY} Commandes Économie",
                value=f"`{PREFIX}balance [@user]` - Affiche le solde\n"
                      f"`{PREFIX}give <@user> <montant>` - Donne des pièces\n"
                      f"`{PREFIX}daily` - Récupère tes pièces quotidiennes",
                inline=False
            )
            
            # Commandes shop
            embed.add_field(
                name=f"{Emojis.SHOP} Commandes Boutique",
                value=f"`{PREFIX}shop [page]` - Affiche la boutique\n"
                      f"`{PREFIX}buy <id>` - Achète un item",
                inline=False
            )
            
            # Aliases populaires
            embed.add_field(
                name="🔄 Aliases",
                value="`balance` → `bal`, `money`\n"
                      "`give` → `pay`, `transfer`\n"
                      "`daily` → `dailyspin`, `spin`\n"
                      "`shop` → `boutique`, `store`\n"
                      "`buy` → `acheter`, `purchase`",
                inline=False
            )

            embed.set_footer(text=f"Préfixe: {PREFIX} | Version simplifiée")
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur help: {e}")
            await ctx.send(f"**Commandes disponibles :**\n"
                          f"`{PREFIX}daily` - Pièces quotidiennes\n"
                          f"`{PREFIX}balance` - Voir le solde\n"
                          f"`{PREFIX}give @user montant` - Donner des pièces\n"
                          f"`{PREFIX}shop` - Voir la boutique\n"
                          f"`{PREFIX}buy id` - Acheter un item")

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Help(bot))