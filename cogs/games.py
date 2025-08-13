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
    """Vue pour le jeu Pierre-Papier-Ciseaux"""
    
    def __init__(self, challenger, opponent, bet_amount, db):
        super().__init__(timeout=60.0)  # 1 minute pour jouer
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

        await interaction.response.send_message(
            f"✅ Tu as choisi {emoji} **{choice.capitalize()}** !", ephemeral=True
        )

        # Vérifier si les deux ont joué
        if self.challenger_choice and self.opponent_choice:
            await self.resolve_game(interaction)

    async def resolve_game(self, interaction: discord.Interaction):
        """Résout le jeu et détermine le gagnant"""
        self.game_finished = True
        
        # Déterminer le gagnant
        winner = self.determine_winner()
        
        # Emojis pour l'affichage
        choice_emojis = {
            'pierre': '🗿',
            'papier': '📄', 
            'ciseaux': '✂️'
        }
        
        challenger_display = f"{choice_emojis[self.challenger_choice]} **{self.challenger_choice.capitalize()}**"
        opponent_display = f"{choice_emojis[self.opponent_choice]} **{self.opponent_choice.capitalize()}**"
        
        # Créer l'embed de résultat
        if winner == 'tie':
            embed = discord.Embed(
                title="🤝 Match nul !",
                description=f"**{self.challenger.display_name}**: {challenger_display}\n"
                           f"**{self.opponent.display_name}**: {opponent_display}\n\n"
                           f"Personne ne gagne les **{self.bet_amount:,}** PrissBucks !",
                color=Colors.WARNING
            )
        else:
            winner_user = winner
            loser_user = self.opponent if winner == self.challenger else self.challenger
            
            # Transférer les PrissBucks
            try:
                success = await self.db.transfer(loser_user.id, winner_user.id, self.bet_amount)
                if success:
                    transfer_msg = f"💰 **{self.bet_amount:,}** PrissBucks transférés de {loser_user.display_name} vers {winner_user.display_name} !"
                else:
                    transfer_msg = f"⚠️ Erreur lors du transfert des PrissBucks"
            except Exception as e:
                logger.error(f"Erreur transfert PPC: {e}")
                transfer_msg = f"⚠️ Erreur lors du transfert des PrissBucks"
            
            embed = discord.Embed(
                title=f"🎉 {winner_user.display_name} gagne !",
                description=f"**{self.challenger.display_name}**: {challenger_display}\n"
                           f"**{self.opponent.display_name}**: {opponent_display}\n\n"
                           f"{transfer_msg}",
                color=Colors.SUCCESS
            )
        
        embed.add_field(
            name="🎯 Règles du jeu",
            value="Pierre bat Ciseaux • Papier bat Pierre • Ciseaux bat Papier",
            inline=False
        )
        
        # Désactiver tous les boutons
        for item in self.children:
            item.disabled = True
        
        # Modifier le message original
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except:
            await interaction.followup.send(embed=embed, view=self)

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
            description="Le jeu a expiré car tous les joueurs n'ont pas fait leur choix à temps.\n"
                       f"Mise de **{self.bet_amount:,}** PrissBucks non transférée.",
            color=Colors.ERROR
        )
        
        # Désactiver les boutons
        for item in self.children:
            item.disabled = True
            
        try:
            # Modifier le message original si possible
            await self.message.edit(embed=embed, view=self)
        except:
            pass

class PierrepapierCiseaux(commands.Cog):
    """Mini-jeu Pierre-Papier-Ciseaux avec mises"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Pierre-Papier-Ciseaux initialisé")

    @app_commands.command(name="ppc", description="Défie quelqu'un au Pierre-Papier-Ciseaux avec une mise")
    @app_commands.describe(
        adversaire="L'utilisateur que tu veux défier",
        mise="Montant à miser (en PrissBucks)"
    )
    async def ppc_command(self, interaction: discord.Interaction, adversaire: discord.Member, mise: int):
        """Lance un défi Pierre-Papier-Ciseaux"""
        challenger = interaction.user
        opponent = adversaire
        bet_amount = mise
        
        # Validations de base
        if bet_amount <= 0:
            embed = create_error_embed("Mise invalide", "La mise doit être positive !")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
            
        if challenger.id == opponent.id:
            embed = create_error_embed("Défi impossible", "Tu ne peux pas te défier toi-même !")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
            
        if opponent.bot:
            embed = create_error_embed("Défi impossible", "Tu ne peux pas défier un bot !")
            await interaction.response.send_message(embed=embed, ephemeral=True)
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
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
                
            if opponent_balance < bet_amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"{opponent.display_name} n'a que {opponent_balance:,} PrissBucks mais la mise est de {bet_amount:,} PrissBucks."
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Créer l'embed du jeu
            embed = discord.Embed(
                title="🎮 Pierre - Papier - Ciseaux",
                description=f"**{challenger.display_name}** défie **{opponent.display_name}** !\n\n"
                           f"💰 **Mise:** {bet_amount:,} PrissBucks\n"
                           f"⏱️ **Temps limite:** 60 secondes\n\n"
                           f"Chacun doit faire son choix en cliquant sur un bouton ci-dessous.",
                color=Colors.PREMIUM
            )
            
            embed.add_field(
                name="🎯 Règles",
                value="🗿 Pierre bat ✂️ Ciseaux\n📄 Papier bat 🗿 Pierre\n✂️ Ciseaux bat 📄 Papier",
                inline=True
            )
            
            embed.add_field(
                name="👥 Joueurs",
                value=f"**Challenger:** {challenger.mention}\n**Adversaire:** {opponent.mention}",
                inline=True
            )
            
            embed.set_footer(text="Seuls les joueurs concernés peuvent faire leur choix !")

            # Créer la vue avec les boutons
            view = PPCView(challenger, opponent, bet_amount, self.db)
            
            # Envoyer le message
            await interaction.response.send_message(embed=embed, view=view)
            
            # Sauvegarder la référence du message pour le timeout
            view.message = await interaction.original_response()
            
        except Exception as e:
            logger.error(f"Erreur PPC {challenger.id} vs {opponent.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la création du jeu.")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(name='ppc_stats')
    async def ppc_stats_cmd(self, ctx, user: discord.Member = None):
        """Affiche des statistiques PPC basiques (optionnel)"""
        target = user or ctx.author
        
        # Pour l'instant, on affiche juste le solde
        # Tu peux étendre avec une vraie table de stats plus tard
        try:
            balance = await self.db.get_balance(target.id)
            embed = discord.Embed(
                title=f"🎮 Statistiques PPC de {target.display_name}",
                description=f"**Solde actuel:** {balance:,} PrissBucks\n\n"
                           f"*Les statistiques détaillées arrivent bientôt !*",
                color=Colors.INFO
            )
            embed.set_thumbnail(url=target.display_avatar.url)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur stats PPC: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(PierrepapierCiseaux(bot))
    
    # Note: La synchronisation se fait automatiquement au démarrage du bot