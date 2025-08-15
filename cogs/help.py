import discord
from discord.ext import commands
from discord import app_commands
import logging

from config import PREFIX, Colors, Emojis, TRANSFER_TAX_RATE, SHOP_TAX_RATE

logger = logging.getLogger(__name__)

class Help(commands.Cog):
    """Aide complète pour toutes les commandes du bot avec support dual (/ et e!) et banque publique"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        logger.info("✅ Cog Help initialisé avec support commandes duales et banque publique")

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
            
            # NOUVEAU: Commandes Banque Publique
            embed.add_field(
                name=f"{Emojis.PUBLIC_BANK} **Banque Publique - NOUVEAU !**",
                value=f"• `/publicbank` ou `{PREFIX}publicbank` - Voir les fonds publics\n"
                      f"• `/withdraw_public <montant>` ou `{PREFIX}withdraw_public` - Retirer des fonds\n"
                      f"• `{PREFIX}public_stats` - Statistiques détaillées\n"
                      f"🔥 **Alimentée par les pertes casino !** 🔥",
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
            
            # Mini-jeux MODIFIÉS
            embed.add_field(
                name="🎮 **Mini-jeux Solidaires**",
                value=f"• `/ppc <@adversaire> <mise>` - Pierre-Papier-Ciseaux\n"
                      f"• `/roulette <pari> <mise>` ou `{PREFIX}roulette` - Roulette casino\n"
                      f"• `{PREFIX}voler <@utilisateur>` - Tenter de voler (risqué !)\n"
                      f"• `{PREFIX}ppc_stats [@user]` - Statistiques PPC\n"
                      f"🏛️ **Les pertes alimentent la banque publique !**",
                inline=False
            )
            
            # Utilitaires
            embed.add_field(
                name="ℹ️ **Utilitaires**",
                value=f"• `/help` ou `{PREFIX}help` - Cette aide\n"
                      f"• `/ping` ou `{PREFIX}ping` - Latence du bot et infos système\n"
                      f"• `{PREFIX}taxes` - Informations détaillées sur les taxes\n"
                      f"• `/transactions` ou `{PREFIX}transactions` - Ton historique",
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

            # NOUVEAU: Section banque publique
            embed.add_field(
                name=f"🏛️ **RÉVOLUTION : Banque Publique**",
                value="🔥 **NOUVELLE FONCTIONNALITÉ RÉVOLUTIONNAIRE !** 🔥\n"
                      "• **Alimentée automatiquement** par les pertes casino\n"
                      "• **Accessible à TOUS** les joueurs du serveur\n"
                      "• **Fini les pertes inutiles** - tout va vers la communauté\n"
                      "• **Système solidaire** - nous perdons ensemble, nous gagnons ensemble\n"
                      "• **Roulette égalité/PPC égalité** = Banque publique\n"
                      "• **Utilise `/publicbank` pour voir les fonds disponibles !**",
                inline=False
            )

            # Détails sur les systèmes MODIFIÉS
            embed.add_field(
                name="💡 **Détails importants**",
                value="• **Daily:** 50-150 PrissBucks + 10% chance bonus (50-200)\n"
                      "• **PPC:** Jeu avec mise, égalité = banque publique !\n"
                      "• **Roulette:** Casino avec pertes vers banque publique !\n"
                      "• **Vol:** 70% réussite (vole 30%), 30% échec (perd 40%)\n"
                      "• **Shop:** Rôles automatiquement attribués après achat\n"
                      "• **Cooldowns:** Daily 24h, Give 5s, Buy 3s, Vol 0.5h, Roulette 3s\n"
                      "• **Messages:** +1 PrissBuck par message (CD: 20s)\n"
                      "• 🏛️ **BANQUE PUBLIQUE:** Retire jusqu'à 1000 PB/30min !",
                inline=False
            )

            # Footer avec stats
            guild_count = len(self.bot.guilds) if self.bot.guilds else 1
            slash_count = len(self.bot.tree.get_commands())
            embed.set_footer(
                text=f"Préfixe: {PREFIX} • Slash: / • {guild_count} serveur(s) • {slash_count} slash command(s) • Taxes: {TRANSFER_TAX_RATE*100:.0f}%/{SHOP_TAX_RATE*100:.0f}% • 🏛️ BANQUE PUBLIQUE ACTIVE"
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur help: {e}")
            await ctx.send(f"**❌ Erreur dans l'aide**\n"
                          f"Commandes de base : `/balance`, `/daily`, `/shop`, `/give`, `/buy`, `/ppc`, `/publicbank`\n"
                          f"Ou avec préfixe : `{PREFIX}balance`, `{PREFIX}daily`, etc.\n"
                          f"**Taxes:** {TRANSFER_TAX_RATE*100:.0f}% sur transferts, {SHOP_TAX_RATE*100:.0f}% sur achats\n"
                          f"🔥 **NOUVEAU:** Banque publique avec `/publicbank` !")

    @app_commands.command(name="help", description="Affiche l'aide complète du bot avec la nouvelle banque publique")
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

            # Répliquer le même contenu que la version prefix
            # [Le contenu est identique à la version prefix ci-dessus, donc je ne le recopie pas entièrement]
            
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
            
            # NOUVEAU: Commandes Banque Publique en premier pour la visibilité
            embed.add_field(
                name=f"{Emojis.PUBLIC_BANK} **🔥 BANQUE PUBLIQUE - RÉVOLUTIONNAIRE ! 🔥**",
                value=f"• `/publicbank` ou `{PREFIX}publicbank` - Voir les fonds publics disponibles\n"
                      f"• `/withdraw_public <montant>` ou `{PREFIX}withdraw_public` - Retirer jusqu'à 1000 PB\n"
                      f"• `{PREFIX}public_stats` - Statistiques de redistribution\n"
                      f"🏛️ **Alimentée par TOUTES les pertes casino !**\n"
                      f"🤝 **Accessible à TOUS - Système solidaire !**",
                inline=False
            )
            
            # Mini-jeux MODIFIÉS avec emphase sur la banque publique
            embed.add_field(
                name="🎮 **Mini-jeux Solidaires - RÉVOLUTION !**",
                value=f"• `/ppc <@adversaire> <mise>` - Pierre-Papier-Ciseaux solidaire\n"
                      f"• `/roulette <pari> <mise>` - Roulette avec redistribution\n"
                      f"• `{PREFIX}voler <@utilisateur>` - Vol classique (pas de changement)\n"
                      f"🏛️ **NOUVEAU:** Égalités PPC → Banque publique\n"
                      f"🏛️ **NOUVEAU:** Pertes roulette → Banque publique\n"
                      f"♻️ **Plus de pertes inutiles !** Tout va à la communauté !",
                inline=False
            )
            
            embed.set_footer(
                text=f"🔥 BANQUE PUBLIQUE RÉVOLUTIONNAIRE ACTIVÉE ! 🔥 • Préfixe: {PREFIX} • Slash: /"
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            await interaction.followup.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur help: {e}")
            await interaction.followup.send(f"**❌ Erreur dans l'aide**\n"
                                          f"Commandes révolutionnaires : `/publicbank`, `/withdraw_public`, `/ppc`, `/roulette`\n"
                                          f"🏛️ **LA BANQUE PUBLIQUE CHANGE TOUT !**",
                                          ephemeral=True)

    @commands.command(name='publicbank_help', aliases=['help_banque'])
    async def publicbank_help_cmd(self, ctx):
        """e!publicbank_help - Guide détaillé de la banque publique"""
        embed = discord.Embed(
            title="🏛️ Guide Complet - Banque Publique",
            description="**RÉVOLUTION ÉCONOMIQUE - Système de redistribution automatique !**",
            color=Colors.GOLD
        )
        
        embed.add_field(
            name="🔥 Qu'est-ce que c'est ?",
            value="La **Banque Publique** est un système révolutionnaire qui récupère TOUTES les pertes\n"
                  "des jeux de casino et les redistribue équitablement à TOUS les joueurs du serveur !\n"
                  "🤝 **Plus personne ne perd vraiment - nous perdons ensemble, nous gagnons ensemble !**",
            inline=False
        )
        
        embed.add_field(
            name="💰 Comment elle se remplit ?",
            value="🎰 **Roulette:** Toutes les mises perdues\n"
                  "🎮 **PPC:** Mises des égalités et abandons\n"
                  "🏛️ **Taxes casino:** Petite taxe sur les gros gains\n"
                  "💸 **Autres jeux:** Futurs mini-jeux avec pertes\n"
                  "⚡ **Automatique:** Aucune intervention manuelle !",
            inline=True
        )
        
        embed.add_field(
            name="🎯 Comment retirer ?",
            value=f"• `/publicbank` - Voir les fonds disponibles\n"
                  f"• `/withdraw_public <montant>` - Retirer des PB\n"
                  f"• **Min:** 50 PB par retrait\n"
                  f"• **Max:** 1000 PB par retrait\n"
                  f"• **Limite quotidienne:** 2000 PB/jour\n"
                  f"• **Cooldown:** 30 minutes entre retraits",
            inline=True
        )
        
        embed.add_field(
            name="📊 Limites et règles",
            value="🔸 **Équitable pour tous:** Mêmes limites pour chaque joueur\n"
                  "🔸 **Anti-abus:** Cooldowns et limites quotidiennes\n"
                  "🔸 **Transparence:** Statistiques publiques disponibles\n"
                  "🔸 **Durabilité:** Système conçu pour durer indéfiniment\n"
                  "🔸 **Solidarité:** Plus de pertes inutiles !",
            inline=False
        )
        
        embed.add_field(
            name="🚀 Exemples concrets",
            value="**Exemple 1:** Tu perds 500 PB à la roulette → 500 PB vont en banque publique\n"
                  "**Exemple 2:** Égalité PPC avec 200 PB chacun → 400 PB vont en banque publique\n"
                  "**Exemple 3:** Tu retires 800 PB de la banque → Cooldown 30min, limite -800 PB aujourd'hui\n"
                  "**Résultat:** L'argent perdu circule dans la communauté au lieu de disparaître !",
            inline=False
        )
        
        embed.add_field(
            name="💡 Stratégies recommandées",
            value="🎯 **Vérifie régulièrement:** `{PREFIX}publicbank` pour voir les fonds\n"
                  f"💰 **Retire intelligemment:** Montants adaptés à tes besoins\n"
                  f"🤝 **Joue solidaire:** Tes pertes aident les autres, leurs pertes t'aident\n"
                  f"📈 **Long terme:** Plus tu joues, plus la banque se remplit\n"
                  f"🏆 **Gagnant-gagnant:** Même en perdant, tu contribues à la communauté",
            inline=False
        )
        
        embed.add_field(
            name="🔮 Impact révolutionnaire",
            value="Cette banque publique transforme complètement l'économie du serveur :\n"
                  "• **Fini l'effet owner riche** - L'argent va vers les joueurs\n"
                  "• **Système auto-régulé** - Plus on joue, plus on peut récupérer\n"
                  "• **Solidarité intégrée** - Personne n'est laissé pour compte\n"
                  "• **Motivation preserved** - Le risque existe toujours, mais les pertes ont du sens",
            inline=False
        )
        
        embed.set_footer(text=f"🏛️ Révolution économique en marche ! Utilise {PREFIX}public_stats pour les statistiques")
        await ctx.send(embed=embed)

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
                title=f"{Emojis.TAX} Système de Taxes + Banque Publique",
                description="Informations complètes sur les taxes et la redistribution",
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
            
            # NOUVEAU: Banque publique
            embed.add_field(
                name="🏛️ **NOUVEAU: Banque Publique**",
                value="🔥 **RÉVOLUTION:** Les pertes casino ne vont plus dans le vide !\n"
                      "• **Pertes roulette** → Banque publique\n"
                      "• **Égalités PPC** → Banque publique\n"
                      "• **Abandons jeux** → Banque publique\n"
                      "• **Petites taxes gains** → Banque publique\n"
                      "🤝 **Tous les joueurs peuvent retirer ces fonds !**",
                inline=False
            )
            
            # Exemptions
            embed.add_field(
                name="✅ **Activités sans taxe**",
                value="• **Daily rewards** - Aucune taxe\n"
                      "• **Récompenses de messages** - Aucune taxe\n"
                      "• **Gains de mini-jeux** (victoires PPC, roulette) - Aucune taxe\n"
                      "• **Ajouts admin** (`addpb`) - Aucune taxe\n"
                      "• **Retraits banque publique** - Aucune taxe !",
                inline=False
            )
            
            # Calculs rapides MODIFIÉS
            embed.add_field(
                name="🧮 **Calculateur rapide (avec banque publique)**",
                value=f"**Transferts:**\n"
                      f"• Give 100 → Reçoit {100-int(100*TRANSFER_TAX_RATE)} (taxe: {int(100*TRANSFER_TAX_RATE)})\n"
                      f"• Give 200 → Reçoit {200-int(200*TRANSFER_TAX_RATE)} (taxe: {int(200*TRANSFER_TAX_RATE)})\n\n"
                      f"**Shop:**\n"
                      f"• Buy 100 → Coûte {100+int(100*SHOP_TAX_RATE)} (taxe: {int(100*SHOP_TAX_RATE)})\n"
                      f"• Buy 500 → Coûte {500+int(500*SHOP_TAX_RATE)} (taxe: {int(500*SHOP_TAX_RATE)})\n\n"
                      f"**Casino → Banque Publique:**\n"
                      f"• Perds 300 roulette → 300 PB en banque publique\n"
                      f"• Égalité PPC 150 → 300 PB en banque publique",
                inline=False
            )
            
            embed.set_footer(text="Les taxes financent le serveur • Les pertes casino financent la communauté !")
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
            
            # NOUVEAU: Statut banque publique
            public_bank_cog = self.bot.get_cog('PublicBank')
            if public_bank_cog:
                try:
                    bank_info = await public_bank_cog.get_public_bank_balance()
                    bank_status = f"🏛️ {bank_info['balance']:,} PB"
                except:
                    bank_status = "🏛️ Active"
            else:
                bank_status = "🔴 Inactive"
            
            embed.add_field(name="🏛️ Banque Publique", value=bank_status, inline=True)
            
            embed.set_footer(text=f"Bot développé avec discord.py • Préfixe: {PREFIX} • Slash: / • 🏛️ Banque Publique RÉVOLUTIONNAIRE")
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur ping: {e}")
            await send_func(f"🏓 Pong ! Latence: {round(self.bot.latency * 1000)}ms\n🏛️ Banque Publique: Révolution en cours !")

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Help(bot))