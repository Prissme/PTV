import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple
from enum import Enum
import time

from config import Colors, Emojis, OWNER_ID
from utils.embeds import create_error_embed, create_success_embed

logger = logging.getLogger(__name__)

class PPCChoice(Enum):
    """Choix pour Pierre-Papier-Ciseaux"""
    PIERRE = ("pierre", "🗿")
    PAPIER = ("papier", "📄") 
    CISEAUX = ("ciseaux", "✂️")
    
    def __init__(self, name, emoji):
        self.choice_name = name
        self.emoji = emoji
    
    def beats(self, other) -> bool:
        """Détermine si ce choix bat l'autre"""
        winning = {
            PPCChoice.PIERRE: PPCChoice.CISEAUX,
            PPCChoice.PAPIER: PPCChoice.PIERRE,
            PPCChoice.CISEAUX: PPCChoice.PAPIER
        }
        return winning[self] == other
    
    @classmethod
    def from_string(cls, s: str):
        """Convertit une string en PPCChoice"""
        s = s.lower().strip()
        for choice in cls:
            if choice.choice_name == s:
                return choice
        return None

class PierrepapierCiseaux(commands.Cog):
    """Mini-jeu Pierre-Papier-Ciseaux simplifié et optimisé"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Gestion simple des jeux actifs
        self.active_games: Dict[str, Dict] = {}
        
        # Statistiques légères
        self.stats = {"games": 0, "total_wagered": 0}
        
        # Cooldowns optimisés
        self.cooldowns = {}
        self.COOLDOWN = 5  # 5 secondes
    
    async def cog_load(self):
        """Initialisation du cog"""
        self.db = self.bot.database
        logger.info("✅ Cog PPC réécrit et optimisé - Architecture simplifiée")
    
    def _get_game_key(self, user1_id: int, user2_id: int) -> str:
        """Clé unique pour un jeu entre deux joueurs"""
        return f"{min(user1_id, user2_id)}_{max(user1_id, user2_id)}"
    
    def _check_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown utilisateur"""
        now = time.time()
        if user_id in self.cooldowns:
            elapsed = now - self.cooldowns[user_id]
            if elapsed < self.COOLDOWN:
                return self.COOLDOWN - elapsed
        self.cooldowns[user_id] = now
        return 0
    
    def _cleanup(self):
        """Nettoyage léger des données expirées"""
        now = time.time()
        
        # Nettoyer les jeux expirés (5 minutes)
        expired_games = [
            key for key, game in self.active_games.items()
            if now - game.get('created', 0) > 300
        ]
        for key in expired_games:
            del self.active_games[key]
        
        # Nettoyer les cooldowns expirés
        expired_cooldowns = [
            user_id for user_id, timestamp in self.cooldowns.items()
            if now - timestamp > 60
        ]
        for user_id in expired_cooldowns:
            del self.cooldowns[user_id]
    
    async def _validate_players_and_bet(self, challenger: discord.Member, 
                                      opponent: discord.Member, bet: int) -> Tuple[bool, str]:
        """Validation consolidée"""
        # Validations de base
        if bet <= 0 or bet > 50000:
            return False, "Mise invalide (1-50,000 PrissBucks)"
        
        if challenger.id == opponent.id:
            return False, "Tu ne peux pas te défier toi-même !"
        
        if opponent.bot:
            return False, "Tu ne peux pas défier un bot !"
        
        # Vérifier les soldes
        try:
            challenger_balance = await self.db.get_balance(challenger.id)
            opponent_balance = await self.db.get_balance(opponent.id)
            
            if challenger_balance < bet:
                return False, f"{challenger.display_name} n'a que {challenger_balance:,} PB (mise: {bet:,})"
            
            if opponent_balance < bet:
                return False, f"{opponent.display_name} n'a que {opponent_balance:,} PB (mise: {bet:,})"
            
            return True, ""
            
        except Exception as e:
            logger.error(f"Erreur validation PPC: {e}")
            return False, "Erreur de validation des soldes"
    
    async def _process_game_result(self, game_data: Dict) -> bool:
        """Traite le résultat du jeu de manière atomique"""
        try:
            challenger = game_data['challenger']
            opponent = game_data['opponent']
            bet = game_data['bet']
            challenger_choice = game_data['challenger_choice']
            opponent_choice = game_data['opponent_choice']
            
            # Récupérer les soldes avant transaction
            challenger_balance = await self.db.get_balance(challenger.id)
            opponent_balance = await self.db.get_balance(opponent.id)
            
            if challenger_choice == opponent_choice:
                # ÉGALITÉ - Vers banque publique
                await self.db.update_balance(challenger.id, -bet)
                await self.db.update_balance(opponent.id, -bet)
                
                # Envoyer vers banque publique
                public_bank_cog = self.bot.get_cog('PublicBank')
                if public_bank_cog:
                    await public_bank_cog.add_casino_loss(bet * 2, "ppc_tie")
                
                game_data['result'] = 'tie'
                game_data['pot'] = bet * 2
                
            elif challenger_choice.beats(opponent_choice):
                # CHALLENGER GAGNE
                await self.db.transfer(opponent.id, challenger.id, bet)
                game_data['result'] = 'challenger_wins'
                game_data['winner'] = challenger
                game_data['pot'] = bet * 2
                
            else:
                # OPPONENT GAGNE
                await self.db.transfer(challenger.id, opponent.id, bet)
                game_data['result'] = 'opponent_wins'
                game_data['winner'] = opponent
                game_data['pot'] = bet * 2
            
            # Logger les transactions si disponible
            if hasattr(self.bot, 'transaction_logs'):
                await self._log_ppc_results(game_data, challenger_balance, opponent_balance)
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur traitement résultat PPC: {e}")
            return False
    
    async def _log_ppc_results(self, game_data: Dict, challenger_balance: int, opponent_balance: int):
        """Log les résultats PPC"""
        try:
            challenger = game_data['challenger']
            opponent = game_data['opponent']
            bet = game_data['bet']
            result = game_data['result']
            
            # Calculer les nouveaux soldes
            if result == 'tie':
                challenger_new = challenger_balance - bet
                opponent_new = opponent_balance - bet
                
                await self.bot.transaction_logs.log_ppc_result(
                    challenger.id, bet, 'tie', 0, challenger_balance, challenger_new, opponent.display_name
                )
                await self.bot.transaction_logs.log_ppc_result(
                    opponent.id, bet, 'tie', 0, opponent_balance, opponent_new, challenger.display_name
                )
                
            elif result == 'challenger_wins':
                challenger_new = challenger_balance + bet
                opponent_new = opponent_balance - bet
                
                await self.bot.transaction_logs.log_ppc_result(
                    challenger.id, bet, 'win', bet, challenger_balance, challenger_new, opponent.display_name
                )
                await self.bot.transaction_logs.log_ppc_result(
                    opponent.id, bet, 'loss', 0, opponent_balance, opponent_new, challenger.display_name
                )
                
            else:  # opponent_wins
                challenger_new = challenger_balance - bet
                opponent_new = opponent_balance + bet
                
                await self.bot.transaction_logs.log_ppc_result(
                    challenger.id, bet, 'loss', 0, challenger_balance, challenger_new, opponent.display_name
                )
                await self.bot.transaction_logs.log_ppc_result(
                    opponent.id, bet, 'win', bet, opponent_balance, opponent_new, challenger.display_name
                )
        except Exception as e:
            logger.error(f"Erreur log PPC: {e}")
    
    def _create_game_embed(self, challenger: discord.Member, opponent: discord.Member, bet: int) -> discord.Embed:
        """Crée l'embed initial du jeu"""
        embed = discord.Embed(
            title="🎮 Pierre-Papier-Ciseaux",
            description=f"**{challenger.display_name}** défie **{opponent.display_name}** !\n\n"
                       f"💰 **Mise:** {bet:,} PrissBucks chacun\n"
                       f"🏆 **Pot total:** {bet * 2:,} PrissBucks",
            color=Colors.PREMIUM
        )
        
        embed.add_field(
            name="🎯 Règles",
            value="🗿 Pierre bat ✂️ Ciseaux\n📄 Papier bat 🗿 Pierre\n✂️ Ciseaux bat 📄 Papier",
            inline=True
        )
        
        embed.add_field(
            name="🏛️ Système solidaire", 
            value="• **Victoire:** Gagnant prend tout\n• **Égalité:** → Banque publique",
            inline=True
        )
        
        embed.set_footer(text="Cliquez sur les boutons pour choisir ! Timeout: 60s")
        return embed
    
    def _create_result_embed(self, game_data: Dict) -> discord.Embed:
        """Crée l'embed de résultat optimisé"""
        challenger = game_data['challenger']
        opponent = game_data['opponent']
        challenger_choice = game_data['challenger_choice']
        opponent_choice = game_data['opponent_choice']
        result = game_data['result']
        pot = game_data['pot']
        
        # Titre et couleur selon le résultat
        if result == 'tie':
            title = "🤝 Égalité !"
            color = Colors.WARNING
            description = f"**{challenger_choice.emoji} vs {opponent_choice.emoji}**\n\n" \
                         f"Même choix ! **{pot:,} PB** → Banque publique 🏛️"
        else:
            winner = game_data['winner']
            loser = opponent if winner == challenger else challenger
            title = f"🏆 {winner.display_name} gagne !"
            color = Colors.SUCCESS
            description = f"**{challenger_choice.emoji} vs {opponent_choice.emoji}**\n\n" \
                         f"**{winner.display_name}** remporte **{pot:,} PrissBucks** !"
        
        embed = discord.Embed(title=title, description=description, color=color)
        
        # Détail des choix
        embed.add_field(
            name="⚔️ Combat",
            value=f"{challenger.display_name}: {challenger_choice.choice_name.title()} {challenger_choice.emoji}\n"
                  f"{opponent.display_name}: {opponent_choice.choice_name.title()} {opponent_choice.emoji}",
            inline=False
        )
        
        if result == 'tie':
            embed.add_field(
                name="🏛️ Redistribution solidaire",
                value="Utilise `/publicbank` pour récupérer des fonds communautaires !",
                inline=False
            )
        
        return embed

    class PPCView(discord.ui.View):
        """Interface utilisateur simplifiée"""
        
        def __init__(self, cog, game_key: str, game_data: Dict):
            super().__init__(timeout=60)
            self.cog = cog
            self.game_key = game_key
            self.game_data = game_data
        
        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            """Vérifications d'interaction"""
            user_id = interaction.user.id
            challenger_id = self.game_data['challenger'].id
            opponent_id = self.game_data['opponent'].id
            
            if user_id not in [challenger_id, opponent_id]:
                await interaction.response.send_message("❌ Tu ne participes pas à ce jeu !", ephemeral=True)
                return False
            
            # Vérifier si le joueur a déjà choisi
            if user_id == challenger_id and 'challenger_choice' in self.game_data:
                await interaction.response.send_message("❌ Tu as déjà fait ton choix !", ephemeral=True)
                return False
            
            if user_id == opponent_id and 'opponent_choice' in self.game_data:
                await interaction.response.send_message("❌ Tu as déjà fait ton choix !", ephemeral=True)
                return False
            
            return True
        
        @discord.ui.button(label='🗿 Pierre', style=discord.ButtonStyle.secondary)
        async def pierre_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._handle_choice(interaction, PPCChoice.PIERRE)
        
        @discord.ui.button(label='📄 Papier', style=discord.ButtonStyle.secondary)
        async def papier_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._handle_choice(interaction, PPCChoice.PAPIER)
        
        @discord.ui.button(label='✂️ Ciseaux', style=discord.ButtonStyle.secondary)
        async def ciseaux_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            await self._handle_choice(interaction, PPCChoice.CISEAUX)
        
        async def _handle_choice(self, interaction: discord.Interaction, choice: PPCChoice):
            """Gère le choix d'un joueur"""
            user_id = interaction.user.id
            challenger_id = self.game_data['challenger'].id
            
            # Enregistrer le choix
            if user_id == challenger_id:
                self.game_data['challenger_choice'] = choice
            else:
                self.game_data['opponent_choice'] = choice
            
            await interaction.response.send_message(f"✅ Tu as choisi {choice.emoji} **{choice.choice_name.title()}** !", ephemeral=True)
            
            # Vérifier si le jeu peut se terminer
            if 'challenger_choice' in self.game_data and 'opponent_choice' in self.game_data:
                await self._finish_game()
        
        async def _finish_game(self):
            """Termine le jeu et affiche le résultat"""
            try:
                # Traiter les transactions
                success = await self.cog._process_game_result(self.game_data)
                
                if success:
                    # Créer l'embed de résultat
                    result_embed = self.cog._create_result_embed(self.game_data)
                else:
                    result_embed = discord.Embed(
                        title="❌ Erreur technique",
                        description="Une erreur s'est produite lors du traitement du jeu.",
                        color=Colors.ERROR
                    )
                
                # Désactiver les boutons et modifier le message
                for item in self.children:
                    item.disabled = True
                
                await self.message.edit(embed=result_embed, view=self)
                
                # Nettoyer le jeu actif
                if self.game_key in self.cog.active_games:
                    del self.cog.active_games[self.game_key]
                
                # Mettre à jour les stats
                self.cog.stats['games'] += 1
                self.cog.stats['total_wagered'] += self.game_data['bet'] * 2
                
                logger.info(f"PPC terminé: {self.game_data['result']} - {self.game_data['bet']} PB")
                
            except Exception as e:
                logger.error(f"Erreur fin PPC: {e}")
        
        async def on_timeout(self):
            """Gère l'expiration du jeu"""
            try:
                # Rembourser les joueurs si personne n'a choisi
                if 'challenger_choice' not in self.game_data and 'opponent_choice' not in self.game_data:
                    # Aucun choix fait - pas de transaction
                    embed = discord.Embed(
                        title="⏰ Jeu expiré",
                        description="Aucun joueur n'a fait de choix. Aucune transaction effectuée.",
                        color=Colors.ERROR
                    )
                else:
                    # Au moins un choix fait - envoyer vers banque publique
                    bet = self.game_data['bet']
                    await self.cog.db.update_balance(self.game_data['challenger'].id, -bet)
                    await self.cog.db.update_balance(self.game_data['opponent'].id, -bet)
                    
                    public_bank_cog = self.cog.bot.get_cog('PublicBank')
                    if public_bank_cog:
                        await public_bank_cog.add_casino_loss(bet * 2, "ppc_timeout")
                    
                    embed = discord.Embed(
                        title="⏰ Timeout !",
                        description=f"Jeu expiré. **{bet * 2:,} PB** envoyés vers la banque publique.",
                        color=Colors.ERROR
                    )
                
                for item in self.children:
                    item.disabled = True
                
                await self.message.edit(embed=embed, view=self)
                
                # Nettoyer
                if self.game_key in self.cog.active_games:
                    del self.cog.active_games[self.game_key]
                    
            except Exception as e:
                logger.error(f"Erreur timeout PPC: {e}")

    @app_commands.command(name="ppc", description="🎮 Pierre-Papier-Ciseaux optimisé !")
    @app_commands.describe(
        adversaire="L'utilisateur que tu veux défier",
        mise="Montant à miser (en PrissBucks)"
    )
    async def ppc_command(self, interaction: discord.Interaction, adversaire: discord.Member, mise: int):
        """Slash command PPC optimisé"""
        await interaction.response.defer()
        
        # Nettoyage léger si nécessaire
        if len(self.active_games) > 20:
            self._cleanup()
        
        # Vérifier le cooldown
        cooldown = self._check_cooldown(interaction.user.id)
        if cooldown > 0:
            embed = create_error_embed("Cooldown", f"Attends **{cooldown:.1f}s** avant de relancer !")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Validation
        valid, error_msg = await self._validate_players_and_bet(interaction.user, adversaire, mise)
        if not valid:
            embed = create_error_embed("Jeu invalide", error_msg)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Vérifier jeu déjà actif
        game_key = self._get_game_key(interaction.user.id, adversaire.id)
        if game_key in self.active_games:
            embed = create_error_embed("Jeu en cours", "Un jeu est déjà en cours entre vous deux !")
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        try:
            # Créer le jeu
            game_data = {
                'challenger': interaction.user,
                'opponent': adversaire,
                'bet': mise,
                'created': time.time()
            }
            
            # Enregistrer le jeu
            self.active_games[game_key] = game_data
            
            # Créer l'interface
            view = self.PPCView(self, game_key, game_data)
            embed = self._create_game_embed(interaction.user, adversaire, mise)
            
            message = await interaction.followup.send(embed=embed, view=view)
            view.message = message
            
            logger.info(f"PPC créé: {interaction.user.display_name} vs {adversaire.display_name} ({mise} PB)")
            
        except Exception as e:
            logger.error(f"Erreur création PPC: {e}")
            embed = create_error_embed("Erreur", "Impossible de créer le jeu.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.command(name='ppc_stats')
    async def ppc_stats_cmd(self, ctx):
        """Statistiques PPC simplifiées"""
        embed = discord.Embed(
            title="🎮 Statistiques PPC",
            color=Colors.INFO
        )
        
        embed.add_field(
            name="📊 Session",
            value=f"**{self.stats['games']}** parties\n"
                  f"**{self.stats['total_wagered']:,}** PB misés\n"
                  f"**{len(self.active_games)}** jeux en cours",
            inline=True
        )
        
        embed.add_field(
            name="🏛️ Système solidaire",
            value="• Égalités → Banque publique\n• Plus de pertes inutiles !\n• `/publicbank` pour récupérer",
            inline=True
        )
        
        await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(PierrepapierCiseaux(bot))
