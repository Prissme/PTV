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
    """Vue pour le jeu Pierre-Papier-Ciseaux en BO3"""
    
    def __init__(self, challenger, opponent, bet_amount, db):
        super().__init__(timeout=180.0)  # 3 minutes pour un BO3
        self.challenger = challenger
        self.opponent = opponent  
        self.bet_amount = bet_amount
        self.db = db
        
        # Système de rounds BO3
        self.rounds = []  # Historique des rounds
        self.challenger_wins = 0
        self.opponent_wins = 0
        self.current_round = 1
        
        # Choix du round actuel
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
            f"✅ Tu as choisi {emoji} **{choice.capitalize()}** pour le round {self.current_round} !", ephemeral=True
        )

        # Vérifier si les deux ont joué ce round
        if self.challenger_choice and self.opponent_choice:
            await self.resolve_round(interaction)

    async def resolve_round(self, interaction: discord.Interaction):
        """Résout le round actuel"""
        # Répondre à l'interaction du dernier joueur
        await interaction.response.defer(ephemeral=True)
        
        # Déterminer le gagnant du round
        round_winner = self.determine_round_winner()
        
        # Emojis pour l'affichage
        choice_emojis = {
            'pierre': '🗿',
            'papier': '📄', 
            'ciseaux': '✂️'
        }
        
        challenger_display = f"{choice_emojis[self.challenger_choice]} {self.challenger_choice.capitalize()}"
        opponent_display = f"{choice_emojis[self.opponent_choice]} {self.opponent_choice.capitalize()}"
        
        # Enregistrer le résultat du round
        round_result = {
            'round': self.current_round,
            'challenger_choice': self.challenger_choice,
            'opponent_choice': self.opponent_choice,
            'winner': round_winner
        }
        self.rounds.append(round_result)
        
        # Mettre à jour les scores
        if round_winner == self.challenger:
            self.challenger_wins += 1
        elif round_winner == self.opponent:
            self.opponent_wins += 1
        # Les égalités ne comptent pas dans le score
        
        # Vérifier si le jeu est terminé (premier à 2 victoires)
        if self.challenger_wins >= 2 or self.opponent_wins >= 2:
            await self.finish_game()
        else:
            # Continuer au round suivant
            await self.next_round()

    async def next_round(self):
        """Passe au round suivant"""
        self.current_round += 1
        self.challenger_choice = None
        self.opponent_choice = None
        
        # Créer l'embed du round suivant
        embed = self.create_game_embed()
        
        # Mettre à jour le message principal
        try:
            await self.message.edit(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Erreur mise à jour round suivant: {e}")

    async def finish_game(self):
        """Termine le jeu et détermine le gagnant final"""
        self.game_finished = True
        
        # Déterminer le gagnant final
        if self.challenger_wins > self.opponent_wins:
            winner = self.challenger
            loser = self.opponent
        else:
            winner = self.opponent
            loser = self.challenger
        
        # Transférer les PrissBucks
        transfer_msg = ""
        try:
            success = await self.db.transfer(loser.id, winner.id, self.bet_amount)
            if success:
                transfer_msg = f"💰 **{self.bet_amount:,}** PrissBucks transférés de {loser.display_name} vers {winner.display_name} !"
            else:
                transfer_msg = f"⚠️ Erreur lors du transfert des PrissBucks"
        except Exception as e:
            logger.error(f"Erreur transfert PPC: {e}")
            transfer_msg = f"⚠️ Erreur lors du transfert des PrissBucks"
        
        # Créer l'embed de résultat final
        embed = discord.Embed(
            title=f"🏆 {winner.display_name} remporte le BO3 !",
            description=f"**Score final:** {self.challenger_wins}-{self.opponent_wins}\n\n{transfer_msg}",
            color=Colors.SUCCESS
        )
        
        # Ajouter l'historique des rounds
        rounds_text = ""
        for i, round_data in enumerate(self.rounds, 1):
            c_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[round_data['challenger_choice']]
            o_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[round_data['opponent_choice']]]
            
            if round_data['winner'] == self.challenger:
                winner_emoji = "🟢"
            elif round_data['winner'] == self.opponent:
                winner_emoji = "🔴"
            else:
                winner_emoji = "🟡"
            
            rounds_text += f"**Round {i}:** {c_emoji} vs {o_emoji} {winner_emoji}\n"
        
        embed.add_field(
            name="📊 Historique des rounds",
            value=rounds_text + f"\n🟢 = {self.challenger.display_name} | 🔴 = {self.opponent.display_name} | 🟡 = Égalité",
            inline=False
        )
        
        embed.add_field(
            name="🎯 Format",
            value="Best of 3 (BO3) - Premier à 2 victoires\nLes égalités ne comptent pas dans le score",
            inline=False
        )
        
        # Désactiver tous les boutons
        for item in self.children:
            item.disabled = True
        
        # Modifier le message principal pour que tout le monde puisse voir
        try:
            await self.message.edit(embed=embed, view=self)
        except Exception as e:
            logger.error(f"Erreur mise à jour résultat final: {e}")

    def create_game_embed(self):
        """Crée l'embed pour l'état actuel du jeu"""
        # Calculer les victoires nécessaires restantes
        challenger_needed = max(0, 2 - self.challenger_wins)
        opponent_needed = max(0, 2 - self.opponent_wins)
        
        embed = discord.Embed(
            title="🎮 Pierre - Papier - Ciseaux (BO3)",
            description=f"**Round {self.current_round}** en cours !\n\n"
                       f"💰 **Mise:** {self.bet_amount:,} PrissBucks\n"
                       f"🏆 **Format:** Best of 3 (premier à 2 victoires)\n\n"
                       f"**Score actuel:**\n"
                       f"🟢 {self.challenger.display_name}: {self.challenger_wins}/2 victoires\n"
                       f"🔴 {self.opponent.display_name}: {self.opponent_wins}/2 victoires",
            color=Colors.PREMIUM
        )
        
        # Ajouter l'historique des rounds précédents s'il y en a
        if self.rounds:
            rounds_text = ""
            for round_data in self.rounds:
                c_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[round_data['challenger_choice']]
                o_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[round_data['opponent_choice']]
                
                if round_data['winner'] == self.challenger:
                    result = f"🟢 {self.challenger.display_name}"
                elif round_data['winner'] == self.opponent:
                    result = f"🔴 {self.opponent.display_name}"
                else:
                    result = "🟡 Égalité"
                
                rounds_text += f"**R{round_data['round']}:** {c_emoji} vs {o_emoji} → {result}\n"
            
            embed.add_field(
                name="📊 Rounds précédents",
                value=rounds_text,
                inline=False
            )
        
        embed.add_field(
            name="🎯 Règles",
            value="🗿 Pierre bat ✂️ Ciseaux\n📄 Papier bat 🗿 Pierre\n✂️ Ciseaux bat 📄 Papier",
            inline=True
        )
        
        embed.add_field(
            name="👥 Joueurs",
            value=f"🟢 **{self.challenger.display_name}**\n🔴 **{self.opponent.display_name}**",
            inline=True
        )
        
        embed.set_footer(text=f"Round {self.current_round} • Faites vos choix !")
        
        return embed

    def determine_round_winner(self):
        """Détermine le gagnant du round selon les règles du PPC"""
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
            description=f"Le jeu BO3 a expiré au round {self.current_round}.\n"
                       f"Score actuel: {self.challenger_wins}-{self.opponent_wins}\n\n"
                       f"Mise de **{self.bet_amount:,}** PrissBucks non transférée.",
            color=Colors.ERROR
        )
        
        # Ajouter l'historique s'il y en a
        if self.rounds:
            rounds_text = ""
            for round_data in self.rounds:
                c_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[round_data['challenger_choice']]
                o_emoji = {'pierre': '🗿', 'papier': '📄', 'ciseaux': '✂️'}[round_data['opponent_choice']

                
                if round_data['winner'] == self.challenger:
                    result = f"🟢 {self.challenger.display_name}"
                elif round_data['winner'] == self.opponent:
                    result = f"🔴 {self.opponent.display_name}"
                else:
                    result = "🟡 Égalité"
                
                rounds_text += f"**R{round_data['round']}:** {c_emoji} vs {o_emoji} → {result}\n"
            
            embed.add_field(
                name="📊 Rounds joués",
                value=rounds_text,
                inline=False
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
    """Mini-jeu Pierre-Papier-Ciseaux avec mises en BO3"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Pierre-Papier-Ciseaux BO3 initialisé")

    @app_commands.command(name="ppc", description="Défie quelqu'un au Pierre-Papier-Ciseaux en BO3 avec une mise")
    @app_commands.describe(
        adversaire="L'utilisateur que tu veux défier",
        mise="Montant à miser (en PrissBucks)"
    )
    async def ppc_command(self, interaction: discord.Interaction, adversaire: discord.Member, mise: int):
        """Lance un défi Pierre-Papier-Ciseaux en BO3"""
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
            
            # Envoyer le message PUBLIC avec followup
            message = await interaction.followup.send(embed=embed, view=view)
            
            # Sauvegarder la référence du message pour le timeout
            view.message = message
            
        except Exception as e:
            logger.error(f"Erreur PPC {challenger.id} vs {opponent.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la création du jeu.")
            await interaction.followup.send(embed=embed, ephemeral=True)

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
                           f"*Format: Best of 3 (BO3)*\n"
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