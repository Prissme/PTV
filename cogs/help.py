import discord
from discord.ext import commands
import logging

from config import PREFIX, Colors, Emojis, TRANSFER_TAX_RATE

logger = logging.getLogger(__name__)

class Help(commands.Cog):
    """Aide simplifiée pour toutes les commandes du bot"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        logger.info("✅ Cog Help initialisé (avec infos taxe)")

    @commands.command(name='help', aliases=['h', 'aide'])
    async def help_cmd(self, ctx):
        """Affiche l'aide complète du bot"""
        try:
            tax_percentage = TRANSFER_TAX_RATE * 100
            
            embed = discord.Embed(
                title="🤖 Bot Économie - Aide Complète",
                description="**Toutes les commandes disponibles :**",
                color=Colors.INFO
            )

            # Commandes Économie
            embed.add_field(
                name=f"{Emojis.MONEY} **Économie**",
                value=f"`{PREFIX}balance [@user]` - Voir le solde (aliases: `bal`, `money`)\n"
                      f"`{PREFIX}give <@user> <montant>` - Donner des PrissBucks {Emojis.TAX} *Taxe {tax_percentage:.0f}%* (aliases: `pay`, `transfer`)\n"
                      f"`{PREFIX}daily` - Récompense quotidienne 24h (aliases: `dailyspin`, `spin`)\n"
                      f"`{PREFIX}leaderboard [limite]` - Top des plus riches (aliases: `top`, `lb`, `rich`)",
                inline=False
            )
            
            # Commandes Shop
            embed.add_field(
                name=f"{Emojis.SHOP} **Boutique**",
                value=f"`{PREFIX}shop [page]` - Voir la boutique (aliases: `boutique`, `store`)\n"
                      f"`{PREFIX}buy <id>` - Acheter un item (aliases: `acheter`, `purchase`)\n"
                      f"`{PREFIX}inventory [@user]` - Voir l'inventaire (aliases: `inv`)",
                inline=False
            )
            
            # Mini-jeux
            embed.add_field(
                name="🎮 **Mini-jeux**",
                value=f"`/ppc <@adversaire> <mise>` - Pierre-Papier-Ciseaux (Slash Command)\n"
                      f"`{PREFIX}ppc_stats [@user]` - Statistiques PPC\n"
                      f"`{PREFIX}voler <@user>` - Tenter de voler des PrissBucks (aliases: `steal`, `rob`)",
                inline=False
            )
            
            # Informations & Utilitaires
            embed.add_field(
                name="ℹ️ **Utilitaires**",
                value=f"`{PREFIX}ping` - Latence du bot et infos système\n"
                      f"`{PREFIX}taxinfo` - Informations sur la taxe de transfert",
                inline=False
            )

            # Détails sur les systèmes
            embed.add_field(
                name="💡 **Détails importants**",
                value=f"• **Daily:** 50-150 PrissBucks + 10% chance bonus (50-200)\n"
                      f"• **Transferts:** Taxe de {tax_percentage:.0f}% appliquée automatiquement\n"
                      f"• **Vol:** 50% réussite, 10% gain ou 40% perte, CD 1h\n"
                      f"• **PPC:** Jeu avec mise, transfert automatique au gagnant\n"
                      f"• **Shop:** Rôles automatiquement attribués après achat\n"
                      f"• **Messages:** 1 PrissBuck toutes les 20s pour activité",
                inline=False
            )

            # Footer avec stats
            guild_count = len(self.bot.guilds) if self.bot.guilds else 1
            slash_count = len(self.bot.tree.get_commands())
            embed.set_footer(
                text=f"Préfixe: {PREFIX} • {guild_count} serveur(s) • {slash_count} slash command(s) • Taxe: {tax_percentage:.0f}%"
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur help: {e}")
            await ctx.send(f"**❌ Erreur dans l'aide**\n"
                          f"Commandes de base : `{PREFIX}balance`, `{PREFIX}daily`, `{PREFIX}shop`, `/ppc`")

    @commands.command(name='ping')
    async def ping_cmd(self, ctx):
        """Affiche la latence du bot et informations système"""
        try:
            latency = round(self.bot.latency * 1000)
            
            # Couleur selon la latence
            if latency < 100:
                color = Colors.SUCCESS
                status = "🟢 Excellente"
            elif latency < 300:
                color = Colors.WARNING  
                status = "🟡 Correcte"
            else:
                color = Colors.ERROR
                status = "🔴 Élevée"
            
            embed = discord.Embed(
                title="🏓 Pong !",
                description=f"**Latence:** {latency}ms ({status})",
                color=color
            )
            
            # Statistiques du bot
            embed.add_field(name="🤖 Statut", value="En ligne ✅", inline=True)
            embed.add_field(name="📊 Serveurs", value=f"{len(self.bot.guilds)}", inline=True)
            
            # Slash commands
            slash_count = len(self.bot.tree.get_commands())
            embed.add_field(name="⚡ Slash Commands", value=f"{slash_count}", inline=True)
            
            # Extensions chargées
            cogs_count = len(self.bot.extensions)
            embed.add_field(name="🔧 Extensions", value=f"{cogs_count} chargées", inline=True)
            
            # Base de données
            db_status = "🟢 Connectée" if hasattr(self.bot, 'database') and self.bot.database else "🔴 Déconnectée"
            embed.add_field(name="💾 Base de données", value=db_status, inline=True)
            
            # Taxe
            tax_percentage = TRANSFER_TAX_RATE * 100
            embed.add_field(name=f"{Emojis.TAX} Taxe transferts", value=f"{tax_percentage:.0f}%", inline=True)
            
            embed.set_footer(text=f"Bot développé avec discord.py • Préfixe: {PREFIX}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur ping: {e}")
            await ctx.send(f"🏓 Pong ! Latence: {round(self.bot.latency * 1000)}ms")

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Help(bot))