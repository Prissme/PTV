import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import logging
from datetime import datetime, timezone

from config import Colors, Emojis
from utils.embeds import create_error_embed, create_success_embed, create_info_embed

logger = logging.getLogger(__name__)

class PPCView(discord.ui.View):
    """Vue pour le jeu Pierre-Papier-Ciseaux en BO1 (simple)"""
    
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
        """Termine le jeu et détermine le gagnant"""
        self.game_finished = True
        
        # Déterminer le gagnant
        winner = self.determine_winner()
        
        # Créer l'embed de résultat
        c_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[self.challenger_choice]
        o_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[self.opponent_choice]
        
        if winner == 'tie':
            embed = discord.Embed(
                title="🤝 Égalité !",
                description=f"**{c_emoji} vs {o_emoji}**\n\n"
                           f"{self.challenger.display_name}: **{self.challenger_choice.capitalize()}**\n"
                           f"{self.opponent.display_name}: **{self.opponent_choice.capitalize()}**\n\n"
                           f"Aucun PrissBucks n'est transféré.",
                color=Colors.WARNING
            )
        else:
            loser = self.opponent if winner == self.challenger else self.challenger
            
            # Transférer les PrissBucks
            transfer_msg = ""
            try:
                success = await self.db.transfer(loser.id, winner.id, self.bet_amount)
                if success:
                    transfer_msg = f"💰 **{self.bet_amount:,}** PrissBucks transférés vers {winner.display_name} !"
                else:
                    transfer_msg = f"⚠️ Erreur lors du transfert des PrissBucks"
            except Exception as e:
                logger.error(f"Erreur transfert PPC: {e}")
                transfer_msg = f"⚠️ Erreur lors du transfert des PrissBucks"
            
            embed = discord.Embed(
                title=f"🏆 {winner.display_name} gagne !",
                description=f"**{c_emoji} vs {o_emoji}**\n\n"
                           f"{self.challenger.display_name}: **{self.challenger_choice.capitalize()}**\n"
                           f"{self.opponent.display_name}: **{self.opponent_choice.capitalize()}**\n\n"
                           f"{transfer_msg}",
                color=Colors.SUCCESS
            )
        
        # Ajouter les règles
        embed.add_field(
            name="🎯 Règles",
            value="🗿 Pierre bat ✂️ Ciseaux\n📄 Papier bat 🗿 Pierre\n✂️ Ciseaux bat 📄 Papier",
            inline=True
        )
        
        embed.add_field(
            name="💰 Mise",
            value=f"{self.bet_amount:,} PrissBucks",
            inline=True
        )
        
        # Désactiver tous les boutons
        for item in self.children:
            item.disabled = True
        
        # Modifier le message
        try:
            await self.message.edit(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Erreur mise à jour résultat final: {e}")

    def create_game_embed(self):
        """Crée l'embed pour l'état actuel du jeu"""
        embed = discord.Embed(
            title="🎮 Pierre - Papier - Ciseaux",
            description=f"**Mode BO1** - Un seul round !\n\n"
                       f"💰 **Mise:** {self.bet_amount:,} PrissBucks\n"
                       f"👥 **Joueurs:** {self.challenger.display_name} vs {self.opponent.display_name}\n\n"
                       f"Faites vos choix en cliquant sur les boutons ci-dessous !",
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
        
        embed.set_footer(text="Faites vos choix ! Le gagnant remporte la mise.")
        
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
        """Appelé quand le délai est dépassé"""
        embed = discord.Embed(
            title="⏰ Temps écoulé !",
            description=f"Le jeu PPC a expiré.\n\n"
                       f"**Choix faits:**\n"
                       f"{self.challenger.display_name}: {self.challenger_choice or 'Aucun'}\n"
                       f"{self.opponent.display_name}: {self.opponent_choice or 'Aucun'}\n\n"
                       f"Mise de **{self.bet_amount:,}** PrissBucks non transférée.",
            color=Colors.ERROR
        )
        
        # Désactiver les boutons
        for item in self.children:
            item.disabled = True
            
        try:
            await self.message.edit(embed=embed, view=self)
        except:
            pass

class PierrepapierCiseaux(commands.Cog):
    """Mini-jeu Pierre-Papier-Ciseaux avec mises en BO1"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Pierre-Papier-Ciseaux BO1 initialisé avec slash commands")

    # ==================== PPC COMMANDS ====================

    @app_commands.command(name="ppc", description="Défie quelqu'un au Pierre-Papier-Ciseaux avec une mise")
    @app_commands.describe(
        adversaire="L'utilisateur que tu veux défier",
        mise="Montant à miser (en PrissBucks)"
    )
    async def ppc_command(self, interaction: discord.Interaction, adversaire: discord.Member, mise: int):
        """Lance un défi Pierre-Papier-Ciseaux en BO1"""
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
            
        if challenger.id == opponent.id:
            embed = create_error_embed("Défi impossible", "Tu ne peux pas te défier toi-même !")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
            
        if opponent.bot:
            embed = create_error_embed("Défi impossible", "Tu ne peux pas défier un bot !")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        try:
            # Vérifier les soldes des deux joueurs
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
                    "Solde insuffisant",
                    f"{opponent.display_name} n'a que {opponent_balance:,} PrissBucks mais la mise est de {bet_amount:,} PrissBucks."
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

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
                           f"*Format: Best of 1 (BO1)*\n"
                           f"*Un seul round, le gagnant remporte tout !*",
                color=Colors.INFO
            )
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(
                name="🎯 Comment jouer",
                value="Utilise `/ppc @adversaire <mise>` pour défier quelqu'un !",
                inline=False
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur stats PPC: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)

    @app_commands.command(name="ppc_info", description="Affiche les informations sur le jeu Pierre-Papier-Ciseaux")
    async def ppc_info_slash(self, interaction: discord.Interaction):
        """Slash command pour les infos PPC"""
        embed = discord.Embed(
            title="🎮 Pierre-Papier-Ciseaux",
            description="Défie d'autres utilisateurs dans un duel de PPC avec des mises en PrissBucks !",
            color=Colors.PREMIUM
        )
        
        embed.add_field(
            name="🎯 Règles du jeu",
            value="🗿 Pierre bat ✂️ Ciseaux\n"
                  "📄 Papier bat 🗿 Pierre\n"
                  "✂️ Ciseaux bat 📄 Papier",
            inline=True
        )
        
        embed.add_field(
            name="💰 Système de mise",
            value="• Chaque joueur mise le même montant\n"
                  "• Le gagnant remporte tout\n"
                  "• En cas d'égalité, rien n'est transféré",
            inline=True
        )
        
        embed.add_field(
            name="⚡ Format BO1",
            value="• Un seul round par partie\n"
                  "• Rapide et efficace\n"
                  "• 60 secondes pour choisir",
            inline=True
        )
        
        embed.add_field(
            name="🚀 Comment jouer",
            value="`/ppc @adversaire <mise>` - Lance un défi\n"
                  "`ppc_stats [@user]` - Voir les statistiques",
            inline=False
        )
        
        embed.set_footer(text="Bonne chance dans tes duels !")
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(PierrepapierCiseaux(bot))