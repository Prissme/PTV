import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import logging
from datetime import datetime, timezone

from config import Colors, Emojis, OWNER_ID
from utils.embeds import create_error_embed, create_success_embed, create_info_embed

logger = logging.getLogger(__name__)

class PPCView(discord.ui.View):
    """Vue pour le jeu Pierre-Papier-Ciseaux en BO1 avec système CORRIGÉ"""
    
    def __init__(self, challenger, opponent, bet_amount, db):
        super().__init__(timeout=60.0)  # 1 minute pour un BO1
        self.challenger = challenger
        self.opponent = opponent  
        self.bet_amount = bet_amount
        self.db = db
        
        # Choix des joueurs
        self.challenger_choice = None
        self.opponent_choice = None
        
        # Status du jeu
        self.game_finished = False
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie que seuls les joueurs concernés peuvent interagir"""
        if interaction.user.id not in [self.challenger.id, self.opponent.id]:
            await interaction.response.send_message(
                "❌ Tu ne peux pas participer à ce jeu !", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label='🗿 Pierre', style=discord.ButtonStyle.secondary)
    async def pierre_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.make_choice(interaction, 'pierre', '🗿')

    @discord.ui.button(label='📄 Papier', style=discord.ButtonStyle.secondary) 
    async def papier_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.make_choice(interaction, 'papier', '📄')

    @discord.ui.button(label='✂️ Ciseaux', style=discord.ButtonStyle.secondary)
    async def ciseaux_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.make_choice(interaction, 'ciseaux', '✂️')

    async def make_choice(self, interaction: discord.Interaction, choice: str, emoji: str):
        """Gère le choix d'un joueur"""
        if self.game_finished:
            await interaction.response.send_message("❌ Ce jeu est terminé !", ephemeral=True)
            return

        user = interaction.user
        
        # Enregistrer le choix
        if user.id == self.challenger.id:
            if self.challenger_choice is not None:
                await interaction.response.send_message(
                    f"❌ Tu as déjà choisi {self.challenger_choice}!", ephemeral=True
                )
                return
            self.challenger_choice = choice
        elif user.id == self.opponent.id:
            if self.opponent_choice is not None:
                await interaction.response.send_message(
                    f"❌ Tu as déjà choisi {self.opponent_choice}!", ephemeral=True
                )
                return
            self.opponent_choice = choice

        # Répondre à l'interaction immédiatement
        await interaction.response.send_message(
            f"✅ Tu as choisi {emoji} **{choice.capitalize()}** !", ephemeral=True
        )

        # Vérifier si les deux ont joué
        if self.challenger_choice and self.opponent_choice:
            await self.finish_game()

    async def finish_game(self):
        """Termine le jeu et détermine le gagnant avec système CORRIGÉ"""
        self.game_finished = True
        
        try:
            # Récupérer les soldes AVANT les transferts
            challenger_balance_before = await self.db.get_balance(self.challenger.id)
            opponent_balance_before = await self.db.get_balance(self.opponent.id)
            
            # Déterminer le gagnant
            winner = self.determine_winner()
            
            # Créer l'embed de résultat
            c_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[self.challenger_choice]
            o_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[self.opponent_choice]
            
            if winner == 'tie':
                # ÉGALITÉ: Envoyer vers banque publique
                logger.info(f"PPC: Égalité - Transfert de {self.bet_amount * 2} PB vers banque publique")
                
                # Débiter les deux joueurs
                await self.db.update_balance(self.challenger.id, -self.bet_amount)
                await self.db.update_balance(self.opponent.id, -self.bet_amount)
                
                # Envoyer vers banque publique
                public_bank_cog = self.db.bot.get_cog('PublicBank') if hasattr(self.db, 'bot') else None
                if not public_bank_cog:
                    # Chercher dans les cogs du bot
                    for cog_name, cog in self.db.bot.cogs.items() if hasattr(self.db, 'bot') else []:
                        if cog_name == 'PublicBank':
                            public_bank_cog = cog
                            break
                
                if public_bank_cog and hasattr(public_bank_cog, 'add_casino_loss'):
                    success = await public_bank_cog.add_casino_loss(self.bet_amount * 2, "ppc_tie")
                    if success:
                        transfer_msg = f"🏛️ **{self.bet_amount * 2:,}** PrissBucks transférés vers la banque publique."
                    else:
                        transfer_msg = f"⚠️ **{self.bet_amount * 2:,}** PrissBucks perdus (erreur transfert)."
                else:
                    # Fallback vers owner
                    if OWNER_ID:
                        await self.db.update_balance(OWNER_ID, self.bet_amount * 2)
                        transfer_msg = f"💰 **{self.bet_amount * 2:,}** PrissBucks vers le casino."
                    else:
                        transfer_msg = f"💸 **{self.bet_amount * 2:,}** PrissBucks perdus."
                
                embed = discord.Embed(
                    title="🤝 Égalité !",
                    description=f"**{c_emoji} vs {o_emoji}**\n\n"
                               f"{self.challenger.display_name}: **{self.challenger_choice.capitalize()}**\n"
                               f"{self.opponent.display_name}: **{self.opponent_choice.capitalize()}**\n\n"
                               f"{transfer_msg}",
                    color=Colors.WARNING
                )
                
                # Nouveaux soldes après égalité
                challenger_balance_after = challenger_balance_before - self.bet_amount
                opponent_balance_after = opponent_balance_before - self.bet_amount
                
            else:
                # VICTOIRE: Le gagnant prend tout
                loser = self.opponent if winner == self.challenger else self.challenger
                total_pot = self.bet_amount * 2
                
                logger.info(f"PPC: {winner.display_name} GAGNE - Récupère {total_pot} PB")
                
                # Système CORRECT: Le perdant donne sa mise au gagnant, le gagnant garde la sienne
                success = await self.db.transfer(loser.id, winner.id, self.bet_amount)
                
                if success:
                    transfer_msg = f"💰 **{winner.display_name}** remporte **{total_pot:,}** PrissBucks !"
                    
                    # Calculer les nouveaux soldes
                    if winner == self.challenger:
                        challenger_balance_after = challenger_balance_before + self.bet_amount  # Gagne la mise adverse
                        opponent_balance_after = opponent_balance_before - self.bet_amount      # Perd sa mise
                    else:
                        challenger_balance_after = challenger_balance_before - self.bet_amount  # Perd sa mise
                        opponent_balance_after = opponent_balance_before + self.bet_amount      # Gagne la mise adverse
                        
                else:
                    # Si le transfert échoue, débiter quand même le perdant
                    await self.db.update_balance(loser.id, -self.bet_amount)
                    transfer_msg = f"⚠️ **{winner.display_name}** gagne mais problème de transfert."
                    
                    # Calculer les soldes en cas d'erreur
                    if winner == self.challenger:
                        challenger_balance_after = challenger_balance_before  # Pas de changement
                        opponent_balance_after = opponent_balance_before - self.bet_amount
                    else:
                        challenger_balance_after = challenger_balance_before - self.bet_amount
                        opponent_balance_after = opponent_balance_before  # Pas de changement
                
                embed = discord.Embed(
                    title=f"🏆 {winner.display_name} gagne !",
                    description=f"**{c_emoji} vs {o_emoji}**\n\n"
                               f"{self.challenger.display_name}: **{self.challenger_choice.capitalize()}**\n"
                               f"{self.opponent.display_name}: **{self.opponent_choice.capitalize()}**\n\n"
                               f"{transfer_msg}",
                    color=Colors.SUCCESS
                )
            
            # Logger les transactions si le système de logs existe
            bot = None
            if hasattr(self.db, 'bot'):
                bot = self.db.bot
            
            if bot and hasattr(bot, 'transaction_logs'):
                try:
                    if winner == 'tie':
                        # Log égalité pour les deux joueurs
                        await bot.transaction_logs.log_ppc_result(
                            self.challenger.id, self.bet_amount, 'tie', 0,
                            challenger_balance_before, challenger_balance_after,
                            self.opponent.display_name
                        )
                        await bot.transaction_logs.log_ppc_result(
                            self.opponent.id, self.bet_amount, 'tie', 0,
                            opponent_balance_before, opponent_balance_after,
                            self.challenger.display_name
                        )
                    else:
                        # Log victoire/défaite
                        loser = self.opponent if winner == self.challenger else self.challenger
                        
                        await bot.transaction_logs.log_ppc_result(
                            winner.id, self.bet_amount, 'win', self.bet_amount,
                            challenger_balance_before if winner == self.challenger else opponent_balance_before,
                            challenger_balance_after if winner == self.challenger else opponent_balance_after,
                            loser.display_name
                        )
                        await bot.transaction_logs.log_ppc_result(
                            loser.id, self.bet_amount, 'loss', 0,
                            opponent_balance_before if loser == self.opponent else challenger_balance_before,
                            opponent_balance_after if loser == self.opponent else challenger_balance_after,
                            winner.display_name
                        )
                except Exception as e:
                    logger.error(f"Erreur log PPC: {e}")
            
            # Ajouter les règles
            embed.add_field(
                name="🎯 Règles",
                value="🗿 Pierre bat ✂️ Ciseaux\n📄 Papier bat 🗿 Pierre\n✂️ Ciseaux bat 📄 Papier",
                inline=True
            )
            
            embed.add_field(
                name="💰 Mise",
                value=f"{self.bet_amount:,} PrissBucks chacun",
                inline=True
            )
            
            # Nouveaux soldes
            embed.add_field(
                name="💳 Nouveaux soldes",
                value=f"{self.challenger.display_name}: {challenger_balance_after:,} PB\n"
                      f"{self.opponent.display_name}: {opponent_balance_after:,} PB",
                inline=False
            )
            
            # Désactiver tous les boutons
            for item in self.children:
                item.disabled = True
            
            # Modifier le message
            try:
                await self.message.edit(embed=embed, view=self)
            except Exception as e:
                logger.error(f"Erreur mise à jour résultat final: {e}")
                
        except Exception as e:
            logger.error(f"Erreur critique finish_game: {e}")
            # Embed d'erreur
            embed = discord.Embed(
                title="❌ Erreur de jeu",
                description="Une erreur s'est produite lors de la finalisation. Contactez un admin.",
                color=Colors.ERROR
            )
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(embed=embed, view=self)
            except:
                pass

    def create_game_embed(self):
        """Crée l'embed pour l'état actuel du jeu"""
        embed = discord.Embed(
            title="🎮 Pierre - Papier - Ciseaux",
            description=f"**Mode BO1** - Un seul round !\n\n"
                       f"💰 **Mise:** {self.bet_amount:,} PrissBucks par joueur\n"
                       f"🏆 **Pot total:** {self.bet_amount * 2:,} PrissBucks\n"
                       f"👥 **Joueurs:** {self.challenger.display_name} vs {self.opponent.display_name}\n\n"
                       f"⚡ **NOUVEAU SYSTÈME SOLIDAIRE:**\n"
                       f"• Victoire = Gagnant récupère tout le pot\n"
                       f"• Égalité = Banque publique (accessible à tous)\n"
                       f"• Abandon = Banque publique",
            color=Colors.PREMIUM
        )
        
        embed.add_field(
            name="🎯 Règles",
            value="🗿 Pierre bat ✂️ Ciseaux\n📄 Papier bat 🗿 Pierre\n✂️ Ciseaux bat 📄 Papier",
            inline=True
        )
        
        embed.add_field(
            name="⏱️ Temps limite",
            value="60 secondes pour jouer",
            inline=True
        )
        
        embed.add_field(
            name="🏛️ Révolution Sociale",
            value="• **Plus de pertes inutiles !**\n• Égalités → Banque publique\n• Tout le monde peut récupérer avec `/publicbank`",
            inline=False
        )
        
        embed.set_footer(text="Système équitable révolutionnaire ! 🏛️")
        
        return embed

    def determine_winner(self):
        """Détermine le gagnant selon les règles du PPC"""
        c_choice = self.challenger_choice
        o_choice = self.opponent_choice
        
        if c_choice == o_choice:
            return 'tie'
        
        winning_combinations = {
            ('pierre', 'ciseaux'): True,
            ('papier', 'pierre'): True,
            ('ciseaux', 'papier'): True
        }
        
        if winning_combinations.get((c_choice, o_choice), False):
            return self.challenger
        else:
            return self.opponent

    async def on_timeout(self):
        """En cas de timeout, les mises vont à la banque publique"""
        try:
            # Débiter les deux joueurs
            await self.db.update_balance(self.challenger.id, -self.bet_amount)
            await self.db.update_balance(self.opponent.id, -self.bet_amount)
            
            # Envoyer vers banque publique
            public_bank_cog = None
            if hasattr(self.db, 'bot'):
                public_bank_cog = self.db.bot.get_cog('PublicBank')
            
            if public_bank_cog and hasattr(public_bank_cog, 'add_casino_loss'):
                success = await public_bank_cog.add_casino_loss(self.bet_amount * 2, "ppc_timeout")
                if success:
                    timeout_msg = f"🏛️ **{self.bet_amount * 2:,}** PrissBucks transférés vers la banque publique par abandon."
                else:
                    timeout_msg = f"💸 **{self.bet_amount * 2:,}** PrissBucks perdus par abandon."
            else:
                # Fallback vers owner
                if OWNER_ID:
                    await self.db.update_balance(OWNER_ID, self.bet_amount * 2)
                    timeout_msg = f"💰 **{self.bet_amount * 2:,}** PrissBucks vers le casino par abandon."
                else:
                    timeout_msg = f"💸 **{self.bet_amount * 2:,}** PrissBucks perdus par abandon."

            embed = discord.Embed(
                title="⏰ Temps écoulé !",
                description=f"Le jeu PPC a expiré.\n\n"
                           f"**Choix faits:**\n"
                           f"{self.challenger.display_name}: {self.challenger_choice or 'Aucun'}\n"
                           f"{self.opponent.display_name}: {self.opponent_choice or 'Aucun'}\n\n"
                           f"{timeout_msg}\n"
                           f"🏛️ Utilise `/publicbank` pour récupérer des fonds !",
                color=Colors.ERROR
            )
            
            # Désactiver les boutons
            for item in self.children:
                item.disabled = True
                
            try:
                await self.message.edit(embed=embed, view=self)
            except:
                pass
                
        except Exception as e:
            logger.error(f"Erreur timeout PPC: {e}")

class PierrepapierCiseaux(commands.Cog):
    """Mini-jeu Pierre-Papier-Ciseaux avec système SOLIDAIRE révolutionnaire"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        # Attacher le bot à la DB pour accès aux cogs
        if hasattr(self.db, 'bot'):
            pass
        else:
            self.db.bot = self.bot
        logger.info("✅ Cog Pierre-Papier-Ciseaux SOLIDAIRE initialisé avec système révolutionnaire")

    # ==================== PPC COMMANDS ====================

    @app_commands.command(name="ppc", description="🎮 Pierre-Papier-Ciseaux SOLIDAIRE ! Égalités → Banque publique !")
    @app_commands.describe(
        adversaire="L'utilisateur que tu veux défier",
        mise="Montant à miser (en PrissBucks)"
    )
    async def ppc_command(self, interaction: discord.Interaction, adversaire: discord.Member, mise: int):
        """Lance un défi Pierre-Papier-Ciseaux SOLIDAIRE"""
        # Répondre immédiatement pour éviter le timeout
        await interaction.response.defer()
        
        challenger = interaction.user
        opponent = adversaire
        bet_amount = mise
        
        # Validations de base
        if bet_amount <= 0:
            embed = create_error_embed("Mise invalide", "La mise doit être positive !")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
            
        if bet_amount > 50000:  # Limite raisonnable
            embed = create_error_embed("Mise trop élevée", "Limite: 50,000 PrissBucks par partie.")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
            
        if challenger.id == opponent.id:
            embed = create_error_embed("Défi impossible", "Tu ne peux pas te défier toi-même !")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
            
        if opponent.bot:
            embed = create_error_embed("Défi impossible", "Tu ne peux pas défier un bot !")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        try:
            # Vérifier les soldes SANS débiter (le débit se fera selon le résultat)
            challenger_balance = await self.db.get_balance(challenger.id)
            opponent_balance = await self.db.get_balance(opponent.id)
            
            if challenger_balance < bet_amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Tu as {challenger_balance:,} PrissBucks mais tu essaies de miser {bet_amount:,} PrissBucks."
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
                
            if opponent_balance < bet_amount:
                embed = create_error_embed(
                    "Adversaire sans fonds",
                    f"{opponent.display_name} n'a que {opponent_balance:,} PrissBucks mais la mise est de {bet_amount:,} PrissBucks."
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            logger.info(f"PPC SOLIDAIRE: Défi lancé - {challenger} vs {opponent} pour {bet_amount} PB chacun")

            # Créer la vue avec les boutons
            view = PPCView(challenger, opponent, bet_amount, self.db)
            
            # Créer l'embed initial
            embed = view.create_game_embed()
            
            # Envoyer le message PUBLIC
            message = await interaction.followup.send(embed=embed, view=view)
            
            # Sauvegarder la référence du message pour les modifications
            view.message = message
            
        except Exception as e:
            logger.error(f"Erreur PPC {challenger.id} vs {opponent.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la création du jeu.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================== STATS COMMANDS ====================

    @commands.command(name='ppc_stats')
    async def ppc_stats_cmd(self, ctx, user: discord.Member = None):
        """Affiche des statistiques PPC basiques"""
        target = user or ctx.author
        
        try:
            balance = await self.db.get_balance(target.id)
            embed = discord.Embed(
                title=f"🎮 Statistiques PPC de {target.display_name}",
                description=f"**Solde actuel:** {balance:,} PrissBucks\n\n"
                           f"*🏛️ SYSTÈME SOLIDAIRE RÉVOLUTIONNAIRE*\n"
                           f"• Égalités alimentent la banque publique\n"
                           f"• Tout le monde peut récupérer avec `/publicbank`\n"
                           f"• Plus de pertes inutiles !",
                color=Colors.INFO
            )
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(
                name="🎯 Comment jouer",
                value="Utilise `/ppc @adversaire <mise>` pour défier quelqu'un !",
                inline=False
            )
            embed.add_field(
                name="🏛️ Révolution PPC",
                value="• Victoire = Tu récupères tout le pot\n"
                      "• Égalité = Banque publique\n"
                      "• Abandon = Banque publique\n"
                      "• Solidarité maximale !",
                inline=False
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur stats PPC: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)

    @app_commands.command(name="ppc_info", description="Affiche les informations sur le système PPC SOLIDAIRE")
    async def ppc_info_slash(self, interaction: discord.Interaction):
        """Slash command pour les infos PPC"""
        embed = discord.Embed(
            title="🎮 Pierre-Papier-Ciseaux SOLIDAIRE",
            description="**RÉVOLUTION SOCIALE !** Fini les pertes inutiles !\n\n"
                       "🏛️ Système solidaire où les égalités alimentent une banque publique accessible à tous !",
            color=Colors.GOLD
        )
        
        embed.add_field(
            name="🎯 Règles du jeu",
            value="🗿 Pierre bat ✂️ Ciseaux\n"
                  "📄 Papier bat 🗿 Pierre\n"
                  "✂️ Ciseaux bat 📄 Papier",
            inline=True
        )
        
        embed.add_field(
            name="🏛️ NOUVEAU: Système Solidaire",
            value="• **Victoire:** Gagnant prend tout le pot\n"
                  "• **Égalité:** → Banque publique\n"
                  "• **Abandon:** → Banque publique\n"
                  "• **Récupération:** `/publicbank`",
            inline=True
        )
        
        embed.add_field(
            name="⚡ Format BO1",
            value="• Un seul round par partie\n"
                  "• Rapide et efficace\n"
                  "• 60 secondes pour choisir\n"
                  "• **Système équitable révolutionnaire**",
            inline=True
        )
        
        embed.add_field(
            name="🚀 Comment jouer",
            value="`/ppc @adversaire <mise>` - Lance un défi\n"
                  "`ppc_stats [@user]` - Voir les statistiques",
            inline=False
        )
        
        embed.add_field(
            name="🏛️ Révolution PPC",
            value="**FINI LES PERTES DANS LE VIDE !**\n"
                  "• Égalités → Banque publique\n"
                  "• Tous les joueurs peuvent récupérer\n"
                  "• Solidarité maximale entre joueurs\n"
                  "• `/withdraw_public` pour récupérer !",
            inline=False
        )
        
        embed.set_footer(text="Système solidaire révolutionnaire ! Plus personne ne perd vraiment ! 🏛️")
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(PierrepapierCiseaux(bot))