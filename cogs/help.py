import discord
from discord.ext import commands
import logging

from config import PREFIX, Colors, Emojis

logger = logging.getLogger(__name__)

class Help(commands.Cog):
    """Aide simplifiée pour toutes les commandes du bot"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        logger.info("✅ Cog Help initialisé (simplifié)")

    @commands.command(name='help', aliases=['h', 'aide'])
    async def help_cmd(self, ctx):
        """Affiche l'aide complète du bot"""
        try:
            embed = discord.Embed(
                title="🤖 Bot Économie - Aide Complète",
                description="**Toutes les commandes disponibles :**",
                color=Colors.INFO
            )

            # Commandes Économie
            embed.add_field(
                name=f"{Emojis.MONEY} **Économie**",
                value=f"`{PREFIX}balance [@user]` - Voir le solde (aliases: `bal`, `money`)\n"
                      f"`{PREFIX}give <@user> <montant>` - Donner des PrissBucks (aliases: `pay`, `transfer`)\n"
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
                      f"`{PREFIX}ppc_stats [@user]` - Statistiques PPC",
                inline=False
            )
            
            # Informations & Utilitaires
            embed.add_field(
                name="ℹ️ **Utilitaires**",
                value=f"`{PREFIX}ping` - Latence du bot et infos système",
                inline=False
            )

            # Détails sur les systèmes
            embed.add_field(
                name="💡 **Détails importants**",
                value="• **Daily:** 50-150 PrissBucks + 10% chance bonus (50-200)\n"
                      "• **PPC:** Jeu avec mise, transfert automatique au gagnant\n"
                      "• **Shop:** Rôles automatiquement attribués après achat\n"
                      "• **Cooldowns:** Daily 24h, Give 5s, Buy 3s, PPC 60s timeout",
                inline=False
            )

            # Footer avec stats
            guild_count = len(self.bot.guilds) if self.bot.guilds else 1
            slash_count = len(self.bot.tree.get_commands())
            embed.set_footer(
                text=f"Préfixe: {PREFIX} • {guild_count} serveur(s) • {slash_count} slash command(s)"
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
            
            embed.set_footer(text=f"Bot développé avec discord.py • Préfixe: {PREFIX}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur ping: {e}")
            await ctx.send(f"🏓 Pong ! Latence: {round(self.bot.latency * 1000)}ms")

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Help(bot))