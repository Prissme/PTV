import discord
from discord.ext import commands
from discord import app_commands
import logging

from config import PREFIX, Colors, Emojis, TRANSFER_TAX_RATE, SHOP_TAX_RATE

logger = logging.getLogger(__name__)

class Help(commands.Cog):
    """Aide complète pour toutes les commandes du bot avec support dual (/ et e!)"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        logger.info("✅ Cog Help initialisé avec support commandes duales")

    @commands.command(name='help', aliases=['h', 'aide'])
    async def help_cmd(self, ctx):
        """e!help - Affiche l'aide complète du bot"""
        try:
            embed = discord.Embed(
                title="🤖 Bot Économie - Aide Complète",
                description="**Toutes les commandes sont disponibles en 2 formats :**\n"
                           f"• **Slash Commands:** `/commande` (recommandé)\n"
                           f"• **Préfixe:** `{PREFIX}commande` (classique)\n\n"
                           "**Commandes disponibles :**",
                color=Colors.INFO
            )

            # Commandes Économie avec taxes
            embed.add_field(
                name=f"{Emojis.MONEY} **Économie**",
                value=f"• `/balance [@user]` ou `{PREFIX}balance [@user]` - Voir le solde\n"
                      f"• `/give <@user> <montant>` ou `{PREFIX}give <@user> <montant>` - Donner des PrissBucks\n"
                      f"   {Emojis.TAX} *Taxe {TRANSFER_TAX_RATE*100:.0f}% appliquée sur les transferts*\n"
                      f"• `/daily` ou `{PREFIX}daily` - Récompense quotidienne 24h\n"
                      f"• `/addpb <@user> <montant>` ou `{PREFIX}addpb` - [ADMIN] Ajouter des PrissBucks",
                inline=False
            )
            
            # Commandes Shop avec taxes
            embed.add_field(
                name=f"{Emojis.SHOP} **Boutique**",
                value=f"• `/shop [page]` ou `{PREFIX}shop [page]` - Voir la boutique\n"
                      f"• `/buy <item_id>` ou `{PREFIX}buy <item_id>` - Acheter un item\n"
                      f"   {Emojis.TAX} *Taxe {SHOP_TAX_RATE*100:.0f}% appliquée sur tous les achats*\n"
                      f"• `/inventory [@user]` ou `{PREFIX}inventory [@user]` - Voir l'inventaire\n"
                      f"• `/shopstats` ou `{PREFIX}shopstats` - [ADMIN] Statistiques boutique",
                inline=False
            )
            
            # Commandes Classement
            embed.add_field(
                name="🏆 **Classements**",
                value=f"• `/leaderboard [limite]` ou `{PREFIX}leaderboard [limite]` - Top des plus riches\n"
                      f"• `/rank [@user]` ou `{PREFIX}rank [@user]` - Position dans le classement\n"
                      f"• `/richest` ou `{PREFIX}richest` - Utilisateur le plus riche\n"
                      f"• `/poorest` ou `{PREFIX}poorest` - Utilisateurs les moins riches",
                inline=False
            )
            
            # Mini-jeux
            embed.add_field(
                name="🎮 **Mini-jeux**",
                value=f"• `/ppc <@adversaire> <mise>` - Pierre-Papier-Ciseaux\n"
                      f"• `/roulette <pari> <mise>` ou `{PREFIX}roulette` - Roulette casino\n"
                      f"• `{PREFIX}voler <@utilisateur>` - Tenter de voler (risqué !)\n"
                      f"• `{PREFIX}ppc_stats [@user]` - Statistiques PPC",
                inline=False
            )
            
            # Utilitaires
            embed.add_field(
                name="ℹ️ **Utilitaires**",
                value=f"• `/help` ou `{PREFIX}help` - Cette aide\n"
                      f"• `/ping` ou `{PREFIX}ping` - Latence du bot et infos système\n"
                      f"• `{PREFIX}taxes` - Informations détaillées sur les taxes",
                inline=False
            )

            # Commandes Admin
            embed.add_field(
                name="👮‍♂️ **Administration**",
                value=f"• `/additem <prix> <@role> <nom>` ou `{PREFIX}additem` - Ajouter un item au shop\n"
                      f"• `/removeitem <item_id>` ou `{PREFIX}removeitem` - Désactiver un item\n"
                      f"• Commandes owner: `{PREFIX}reload`, `{PREFIX}sync`, `{PREFIX}cogs`",
                inline=False
            )

            # Section spéciale sur le système de taxes
            embed.add_field(
                name=f"{Emojis.TAX} **Système de Taxes**",
                value=f"• **Transferts:** {TRANSFER_TAX_RATE*100:.0f}% de taxe sur les transferts entre utilisateurs\n"
                      f"• **Boutique:** {SHOP_TAX_RATE*100:.0f}% de taxe sur tous les achats\n"
                      f"• **Utilité:** Les taxes financent le développement du serveur\n"
                      f"• **Exemples:** Give 100 → reçoit {100-int(100*TRANSFER_TAX_RATE)}, Shop 100 → coûte {100+int(100*SHOP_TAX_RATE)}",
                inline=False
            )

            # Détails sur les systèmes
            embed.add_field(
                name="💡 **Détails importants**",
                value="• **Daily:** 50-150 PrissBucks + 10% chance bonus (50-200)\n"
                      "• **PPC:** Jeu avec mise, transfert automatique au gagnant\n"
                      "• **Roulette:** Casino avec différents types de paris\n"
                      "• **Vol:** 70% réussite (vole 30%), 30% échec (perd 40%)\n"
                      "• **Shop:** Rôles automatiquement attribués après achat\n"
                      "• **Cooldowns:** Daily 24h, Give 5s, Buy 3s, Vol 0.5h, Roulette 3s\n"
                      "• **Messages:** +1 PrissBuck par message (CD: 20s)",
                inline=False
            )

            # Footer avec stats
            guild_count = len(self.bot.guilds) if self.bot.guilds else 1
            slash_count = len(self.bot.tree.get_commands())
            embed.set_footer(
                text=f"Préfixe: {PREFIX} • Slash: / • {guild_count} serveur(s) • {slash_count} slash command(s) • Taxes: {TRANSFER_TAX_RATE*100:.0f}%/{SHOP_TAX_RATE*100:.0f}%"
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur help: {e}")
            await ctx.send(f"**❌ Erreur dans l'aide**\n"
                          f"Commandes de base : `/balance`, `/daily`, `/shop`, `/give`, `/buy`, `/ppc`\n"
                          f"Ou avec préfixe : `{PREFIX}balance`, `{PREFIX}daily`, etc.\n"
                          f"**Taxes:** {TRANSFER_TAX_RATE*100:.0f}% sur transferts, {SHOP_TAX_RATE*100:.0f}% sur achats")

    @app_commands.command(name="help", description="Affiche l'aide complète du bot")
    async def help_slash(self, interaction: discord.Interaction):
        """/help - Affiche l'aide complète"""
        await interaction.response.defer()
        
        try:
            embed = discord.Embed(
                title="🤖 Bot Économie - Aide Complète",
                description="**Toutes les commandes sont disponibles en 2 formats :**\n"
                           f"• **Slash Commands:** `/commande` (recommandé)\n"
                           f"• **Préfixe:** `{PREFIX}commande` (classique)\n\n"
                           "**Commandes disponibles :**",
                color=Colors.INFO
            )

            # Commandes Économie avec taxes
            embed.add_field(
                name=f"{Emojis.MONEY} **Économie**",
                value=f"• `/balance [@user]` ou `{PREFIX}balance [@user]` - Voir le solde\n"
                      f"• `/give <@user> <montant>` ou `{PREFIX}give <@user> <montant>` - Donner des PrissBucks\n"
                      f"   {Emojis.TAX} *Taxe {TRANSFER_TAX_RATE*100:.0f}% appliquée sur les transferts*\n"
                      f"• `/daily` ou `{PREFIX}daily` - Récompense quotidienne 24h\n"
                      f"• `/addpb <@user> <montant>` ou `{PREFIX}addpb` - [ADMIN] Ajouter des PrissBucks",
                inline=False
            )
            
            # Commandes Shop avec taxes
            embed.add_field(
                name=f"{Emojis.SHOP} **Boutique**",
                value=f"• `/shop [page]` ou `{PREFIX}shop [page]` - Voir la boutique\n"
                      f"• `/buy <item_id>` ou `{PREFIX}buy <item_id>` - Acheter un item\n"
                      f"   {Emojis.TAX} *Taxe {SHOP_TAX_RATE*100:.0f}% appliquée sur tous les achats*\n"
                      f"• `/inventory [@user]` ou `{PREFIX}inventory [@user]` - Voir l'inventaire\n"
                      f"• `/shopstats` ou `{PREFIX}shopstats` - [ADMIN] Statistiques boutique",
                inline=False
            )
            
            # Commandes Classement
            embed.add_field(
                name="🏆 **Classements**",
                value=f"• `/leaderboard [limite]` ou `{PREFIX}leaderboard [limite]` - Top des plus riches\n"
                      f"• `/rank [@user]` ou `{PREFIX}rank [@user]` - Position dans le classement\n"
                      f"• `/richest` ou `{PREFIX}richest` - Utilisateur le plus riche\n"
                      f"• `/poorest` ou `{PREFIX}poorest` - Utilisateurs les moins riches",
                inline=False
            )
            
            # Mini-jeux
            embed.add_field(
                name="🎮 **Mini-jeux**",
                value=f"• `/ppc <@adversaire> <mise>` - Pierre-Papier-Ciseaux\n"
                      f"• `/roulette <pari> <mise>` ou `{PREFIX}roulette` - Roulette casino\n"
                      f"• `{PREFIX}voler <@utilisateur>` - Tenter de voler (risqué !)\n"
                      f"• `{PREFIX}ppc_stats [@user]` - Statistiques PPC",
                inline=False
            )
            
            # Utilitaires
            embed.add_field(
                name="ℹ️ **Utilitaires**",
                value=f"• `/help` ou `{PREFIX}help` - Cette aide\n"
                      f"• `/ping` ou `{PREFIX}ping` - Latence du bot et infos système\n"
                      f"• `{PREFIX}taxes` - Informations détaillées sur les taxes",
                inline=False
            )

            # Commandes Admin
            embed.add_field(
                name="👮‍♂️ **Administration**",
                value=f"• `/additem <prix> <@role> <nom>` ou `{PREFIX}additem` - Ajouter un item au shop\n"
                      f"• `/removeitem <item_id>` ou `{PREFIX}removeitem` - Désactiver un item\n"
                      f"• Commandes owner: `{PREFIX}reload`, `{PREFIX}sync`, `{PREFIX}cogs`",
                inline=False
            )

            # Section spéciale sur le système de taxes
            embed.add_field(
                name=f"{Emojis.TAX} **Système de Taxes**",
                value=f"• **Transferts:** {TRANSFER_TAX_RATE*100:.0f}% de taxe sur les transferts entre utilisateurs\n"
                      f"• **Boutique:** {SHOP_TAX_RATE*100:.0f}% de taxe sur tous les achats\n"
                      f"• **Utilité:** Les taxes financent le développement du serveur\n"
                      f"• **Exemples:** Give 100 → reçoit {100-int(100*TRANSFER_TAX_RATE)}, Shop 100 → coûte {100+int(100*SHOP_TAX_RATE)}",
                inline=False
            )

            # Footer avec stats
            guild_count = len(self.bot.guilds) if self.bot.guilds else 1
            slash_count = len(self.bot.tree.get_commands())
            embed.set_footer(
                text=f"Préfixe: {PREFIX} • Slash: / • {guild_count} serveur(s) • {slash_count} slash command(s) • Taxes: {TRANSFER_TAX_RATE*100:.0f}%/{SHOP_TAX_RATE*100:.0f}%"
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            await interaction.followup.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur help: {e}")
            await interaction.followup.send(f"**❌ Erreur dans l'aide**\n"
                                          f"Commandes de base : `/balance`, `/daily`, `/shop`, `/give`, `/buy`, `/ppc`\n"
                                          f"Ou avec préfixe : `{PREFIX}balance`, `{PREFIX}daily`, etc.\n"
                                          f"**Taxes:** {TRANSFER_TAX_RATE*100:.0f}% sur transferts, {SHOP_TAX_RATE*100:.0f}% sur achats",
                                          ephemeral=True)

    @commands.command(name='taxes', aliases=['taxe', 'taxinfo'])
    async def tax_info_cmd(self, ctx):
        """e!taxes - Affiche les informations détaillées sur le système de taxes"""
        await self._execute_tax_info(ctx)

    @app_commands.command(name="taxes", description="Affiche les informations détaillées sur le système de taxes")
    async def tax_info_slash(self, interaction: discord.Interaction):
        """/taxes - Informations sur le système de taxes"""
        await interaction.response.defer()
        await self._execute_tax_info(interaction, is_slash=True)

    async def _execute_tax_info(self, ctx_or_interaction, is_slash=False):
        """Logique commune pour tax info"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
        else:
            send_func = ctx_or_interaction.send
            
        try:
            embed = discord.Embed(
                title=f"{Emojis.TAX} Système de Taxes",
                description="Informations complètes sur les taxes du serveur",
                color=Colors.WARNING
            )
            
            # Taxes sur les transferts
            embed.add_field(
                name="💸 **Transferts de PrissBucks**",
                value=f"• **Taux:** {TRANSFER_TAX_RATE*100:.0f}% sur tous les `/give` et `{PREFIX}give`\n"
                      f"• **Exemple:** Donner 100 → Le receveur obtient {100-int(100*TRANSFER_TAX_RATE)}\n"
                      f"• **Taxe collectée:** {int(100*TRANSFER_TAX_RATE)} PrissBucks vers le serveur\n"
                      f"• **Coût pour toi:** 100 PrissBucks (montant demandé)",
                inline=False
            )
            
            # Taxes sur la boutique
            embed.add_field(
                name=f"{Emojis.SHOP} **Achats en Boutique**",
                value=f"• **Taux:** {SHOP_TAX_RATE*100:.0f}% sur tous les achats `/buy` et `{PREFIX}buy`\n"
                      f"• **Exemple:** Item à 100 → Tu paies {100+int(100*SHOP_TAX_RATE)} au total\n"
                      f"• **Taxe collectée:** {int(100*SHOP_TAX_RATE)} PrissBucks vers le serveur\n"
                      f"• **Affichage:** Prix avec taxe visible dans `/shop`",
                inline=False
            )
            
            # Exemptions
            embed.add_field(
                name="✅ **Activités sans taxe**",
                value="• **Daily rewards** - Aucune taxe\n"
                      "• **Récompenses de messages** - Aucune taxe\n"
                      "• **Gains de mini-jeux** (PPC, vol, roulette) - Aucune taxe\n"
                      "• **Ajouts admin** (`addpb`) - Aucune taxe",
                inline=False
            )
            
            # Calculs rapides
            embed.add_field(
                name="🧮 **Calculateur rapide**",
                value=f"• **Give 50** → Reçoit {50-int(50*TRANSFER_TAX_RATE)} (taxe: {int(50*TRANSFER_TAX_RATE)})\n"
                      f"• **Give 100** → Reçoit {100-int(100*TRANSFER_TAX_RATE)} (taxe: {int(100*TRANSFER_TAX_RATE)})\n"
                      f"• **Give 200** → Reçoit {200-int(200*TRANSFER_TAX_RATE)} (taxe: {int(200*TRANSFER_TAX_RATE)})\n"
                      f"• **Buy 100** → Coûte {100+int(100*SHOP_TAX_RATE)} (taxe: {int(100*SHOP_TAX_RATE)})\n"
                      f"• **Buy 500** → Coûte {500+int(500*SHOP_TAX_RATE)} (taxe: {int(500*SHOP_TAX_RATE)})",
                inline=False
            )
            
            embed.set_footer(text="Les taxes contribuent à l'amélioration continue du serveur !")
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur taxes: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage des informations sur les taxes.")
            await send_func(embed=embed)

    @commands.command(name='ping')
    async def ping_cmd(self, ctx):
        """e!ping - Affiche la latence du bot et informations système"""
        await self._execute_ping(ctx)

    @app_commands.command(name="ping", description="Affiche la latence du bot et informations système")
    async def ping_slash(self, interaction: discord.Interaction):
        """/ping - Latence et infos système"""
        await interaction.response.defer()
        await self._execute_ping(interaction, is_slash=True)

    async def _execute_ping(self, ctx_or_interaction, is_slash=False):
        """Logique commune pour ping"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
        else:
            send_func = ctx_or_interaction.send
            
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
            
            # Système de taxes
            embed.add_field(name=f"{Emojis.TAX} Taxes", value=f"Transfer: {TRANSFER_TAX_RATE*100:.0f}% | Shop: {SHOP_TAX_RATE*100:.0f}%", inline=True)
            
            embed.set_footer(text=f"Bot développé avec discord.py • Préfixe: {PREFIX} • Slash: /")
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur ping: {e}")
            await send_func(f"🏓 Pong ! Latence: {round(self.bot.latency * 1000)}ms")

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Help(bot)) Command)\n"
                      f"`{PREFIX}ppc_stats [@user]` - Statistiques PPC\n"
                      f"`/roulette <pari> <mise>` ou `{PREFIX}roulette` - Roulette casino\n"
                      f"`{PREFIX}voler <@utilisateur>` - Tenter de voler (risqué !)",
                inline=False
            )
            
            # Informations & Utilitaires
            embed.add_field(
                name="ℹ️ **Utilitaires**",
                value=f"`{PREFIX}ping` - Latence du bot et infos système\n"
                      f"`{PREFIX}rank [@user]` - Position dans le classement",
                inline=False
            )

            # Commandes Admin
            embed.add_field(
                name="👮‍♂️ **Administration**",
                value=f"`/addpb <utilisateur> <montant>` ou `{PREFIX}addpb` - Ajouter des PrissBucks (Admin)\n"
                      f"`{PREFIX}additem <prix> <@role> <nom>` - Ajouter un item au shop (Admin)\n"
                      f"`{PREFIX}shopstats` - Statistiques de la boutique (Admin)",
                inline=False
            )

            # Section spéciale sur le système de taxes
            embed.add_field(
                name=f"{Emojis.TAX} **Système de Taxes**",
                value=f"• **Transferts:** {TRANSFER_TAX_RATE*100:.0f}% de taxe sur `/give` et `{PREFIX}give`\n"
                      f"• **Boutique:** {SHOP_TAX_RATE*100:.0f}% de taxe sur tous les achats\n"
                      f"• **Utilité:** Les taxes financent le développement du serveur\n"
                      f"• **Exemples:** Give 100 → reçoit {100-int(100*TRANSFER_TAX_RATE)}, Shop 100 → coûte {100+int(100*SHOP_TAX_RATE)}",
                inline=False
            )

            # Détails sur les systèmes
            embed.add_field(
                name="💡 **Détails importants**",
                value="• **Daily:** 50-150 PrissBucks + 10% chance bonus (50-200)\n"
                      "• **PPC:** Jeu avec mise, transfert automatique au gagnant\n"
                      "• **Roulette:** Casino avec différents types de paris\n"
                      "• **Vol:** 70% réussite (vole 30%), 30% échec (perd 40%)\n"
                      "• **Shop:** Rôles automatiquement attribués après achat\n"
                      "• **Cooldowns:** Daily 24h, Give 5s, Buy 3s, Vol 0.5h, Roulette 3s\n"
                      "• **Messages:** +1 PrissBuck par message (CD: 20s)",
                inline=False
            )

            # Footer avec stats
            guild_count = len(self.bot.guilds) if self.bot.guilds else 1
            slash_count = len(self.bot.tree.get_commands())
            embed.set_footer(
                text=f"Préfixe: {PREFIX} • {guild_count} serveur(s) • {slash_count} slash command(s) • Taxes: {TRANSFER_TAX_RATE*100:.0f}%/{SHOP_TAX_RATE*100:.0f}%"
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur help: {e}")
            await ctx.send(f"**❌ Erreur dans l'aide**\n"
                          f"Commandes de base : `{PREFIX}balance`, `{PREFIX}daily`, `/shop`, `/give`, `/buy`, `/ppc`\n"
                          f"**Taxes:** {TRANSFER_TAX_RATE*100:.0f}% sur transferts, {SHOP_TAX_RATE*100:.0f}% sur achats")

    @commands.command(name='taxes', aliases=['taxe', 'taxinfo'])
    async def tax_info_cmd(self, ctx):
        """Affiche les informations détaillées sur le système de taxes"""
        try:
            embed = discord.Embed(
                title=f"{Emojis.TAX} Système de Taxes",
                description="Informations complètes sur les taxes du serveur",
                color=Colors.WARNING
            )
            
            # Taxes sur les transferts
            embed.add_field(
                name="💸 **Transferts de PrissBucks**",
                value=f"• **Taux:** {TRANSFER_TAX_RATE*100:.0f}% sur tous les `/give` et `{PREFIX}give`\n"
                      f"• **Exemple:** Donner 100 → Le receveur obtient {100-int(100*TRANSFER_TAX_RATE)}\n"
                      f"• **Taxe collectée:** {int(100*TRANSFER_TAX_RATE)} PrissBucks vers le serveur\n"
                      f"• **Coût pour toi:** 100 PrissBucks (montant demandé)",
                inline=False
            )
            
            # Taxes sur la boutique
            embed.add_field(
                name=f"{Emojis.SHOP} **Achats en Boutique**",
                value=f"• **Taux:** {SHOP_TAX_RATE*100:.0f}% sur tous les achats `/buy` et `{PREFIX}buy`\n"
                      f"• **Exemple:** Item à 100 → Tu paies {100+int(100*SHOP_TAX_RATE)} au total\n"
                      f"• **Taxe collectée:** {int(100*SHOP_TAX_RATE)} PrissBucks vers le serveur\n"
                      f"• **Affichage:** Prix avec taxe visible dans `/shop`",
                inline=False
            )
            
            # Exemptions
            embed.add_field(
                name="✅ **Activités sans taxe**",
                value="• **Daily rewards** - Aucune taxe\n"
                      "• **Récompenses de messages** - Aucune taxe\n"
                      "• **Gains de mini-jeux** (PPC, vol, roulette) - Aucune taxe\n"
                      "• **Ajouts admin** (`addpb`) - Aucune taxe",
                inline=False
            )
            
            # Utilité des taxes
            embed.add_field(
                name="🎯 **À quoi servent les taxes ?**",
                value="• **Développement du serveur** - Financement des améliorations\n"
                      "• **Équilibrage économique** - Évite l'inflation excessive\n"
                      "• **Maintenance du bot** - Hébergement et mises à jour\n"
                      "• **Événements spéciaux** - Financement de concours",
                inline=False
            )
            
            # Calculs rapides
            embed.add_field(
                name="🧮 **Calculateur rapide**",
                value=f"• **Give 50** → Reçoit {50-int(50*TRANSFER_TAX_RATE)} (taxe: {int(50*TRANSFER_TAX_RATE)})\n"
                      f"• **Give 100** → Reçoit {100-int(100*TRANSFER_TAX_RATE)} (taxe: {int(100*TRANSFER_TAX_RATE)})\n"
                      f"• **Give 200** → Reçoit {200-int(200*TRANSFER_TAX_RATE)} (taxe: {int(200*TRANSFER_TAX_RATE)})\n"
                      f"• **Buy 100** → Coûte {100+int(100*SHOP_TAX_RATE)} (taxe: {int(100*SHOP_TAX_RATE)})\n"
                      f"• **Buy 500** → Coûte {500+int(500*SHOP_TAX_RATE)} (taxe: {int(500*SHOP_TAX_RATE)})",
                inline=False
            )
            
            embed.set_footer(text="Les taxes contribuent à l'amélioration continue du serveur !")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur taxes: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage des informations sur les taxes.")
            await ctx.send(embed=embed)

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
            
            # Système de taxes
            embed.add_field(name=f"{Emojis.TAX} Taxes", value=f"Transfer: {TRANSFER_TAX_RATE*100:.0f}% | Shop: {SHOP_TAX_RATE*100:.0f}%", inline=True)
            
            embed.set_footer(text=f"Bot développé avec discord.py • Préfixe: {PREFIX}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur ping: {e}")
            await ctx.send(f"🏓 Pong ! Latence: {round(self.bot.latency * 1000)}ms")

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Help(bot))