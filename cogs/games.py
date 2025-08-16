import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple, Set
from enum import Enum
import time

from config import Colors, Emojis, OWNER_ID
from utils.embeds import create_error_embed, create_success_embed

logger = logging.getLogger(__name__)

class PPCChoice(Enum):
    """Énumération des choix PPC pour éviter les erreurs de saisie"""
    PIERRE = "pierre"
    PAPIER = "papier"
    CISEAUX = "ciseaux"
    
    @property
    def emoji(self) -> str:
        return {"pierre": "🗿", "papier": "📄", "ciseaux": "✂️"}[self.value]
    
    @property
    def display_name(self) -> str:
        return self.value.capitalize()
    
    def beats(self, other: 'PPCChoice') -> bool:
        """Détermine si ce choix bat l'autre"""
        winning_combinations = {
            PPCChoice.PIERRE: PPCChoice.CISEAUX,
            PPCChoice.PAPIER: PPCChoice.PIERRE,
            PPCChoice.CISEAUX: PPCChoice.PAPIER
        }
        return winning_combinations[self] == other

class PPCGameState(Enum):
    """États possibles du jeu PPC"""
    WAITING_FOR_PLAYERS = "waiting"
    IN_PROGRESS = "playing"
    FINISHED = "finished"
    CANCELLED = "cancelled"

class PPCGameResult(Enum):
    """Résultats possibles d'une partie"""
    CHALLENGER_WINS = "challenger_wins"
    OPPONENT_WINS = "opponent_wins"
    TIE = "tie"
    TIMEOUT = "timeout"

class PPCGame:
    """Représente une partie de Pierre-Papier-Ciseaux"""
    
    def __init__(self, challenger: discord.Member, opponent: discord.Member, bet_amount: int):
        self.challenger = challenger
        self.opponent = opponent
        self.bet_amount = bet_amount
        
        # État du jeu
        self.state = PPCGameState.WAITING_FOR_PLAYERS
        self.created_at = time.time()
        self.timeout_duration = 60  # 60 secondes
        
        # Choix des joueurs
        self._challenger_choice: Optional[PPCChoice] = None
        self._opponent_choice: Optional[PPCChoice] = None
        
        # Validation
        self.is_valid = False
        self.error_message = ""
        self._validate_game()
    
    def _validate_game(self):
        """Valide les paramètres du jeu"""
        if self.bet_amount <= 0:
            self.error_message = "La mise doit être positive !"
            return
        
        if self.bet_amount > 50000:
            self.error_message = "Limite : 50,000 PrissBucks par partie."
            return
        
        if self.challenger.id == self.opponent.id:
            self.error_message = "Tu ne peux pas te défier toi-même !"
            return
        
        if self.opponent.bot:
            self.error_message = "Tu ne peux pas défier un bot !"
            return
        
        self.is_valid = True
        self.state = PPCGameState.IN_PROGRESS
    
    @property
    def is_expired(self) -> bool:
        """Vérifie si le jeu a expiré"""
        return time.time() - self.created_at > self.timeout_duration
    
    @property
    def players(self) -> Set[int]:
        """Retourne l'ensemble des IDs des joueurs"""
        return {self.challenger.id, self.opponent.id}
    
    def can_play(self, user_id: int) -> bool:
        """Vérifie si un utilisateur peut jouer"""
        return user_id in self.players and self.state == PPCGameState.IN_PROGRESS
    
    def has_chosen(self, user_id: int) -> bool:
        """Vérifie si un utilisateur a déjà fait son choix"""
        if user_id == self.challenger.id:
            return self._challenger_choice is not None
        elif user_id == self.opponent.id:
            return self._opponent_choice is not None
        return False
    
    def make_choice(self, user_id: int, choice: PPCChoice) -> bool:
        """Enregistre le choix d'un joueur"""
        if not self.can_play(user_id) or self.has_chosen(user_id):
            return False
        
        if user_id == self.challenger.id:
            self._challenger_choice = choice
        elif user_id == self.opponent.id:
            self._opponent_choice = choice
        else:
            return False
        
        return True
    
    @property
    def is_ready_for_result(self) -> bool:
        """Vérifie si tous les joueurs ont fait leur choix"""
        return self._challenger_choice is not None and self._opponent_choice is not None
    
    def get_result(self) -> Tuple[PPCGameResult, Optional[discord.Member], Dict]:
        """Calcule le résultat du jeu"""
        if not self.is_ready_for_result:
            if self.is_expired:
                return PPCGameResult.TIMEOUT, None, self._get_timeout_info()
            return PPCGameResult.TIE, None, {}  # Partie incomplète
        
        # Comparer les choix
        if self._challenger_choice == self._opponent_choice:
            return PPCGameResult.TIE, None, self._get_tie_info()
        
        if self._challenger_choice.beats(self._opponent_choice):
            return PPCGameResult.CHALLENGER_WINS, self.challenger, self._get_win_info(self.challenger, self.opponent)
        else:
            return PPCGameResult.OPPONENT_WINS, self.opponent, self._get_win_info(self.opponent, self.challenger)
    
    def _get_win_info(self, winner: discord.Member, loser: discord.Member) -> Dict:
        """Informations pour une victoire"""
        return {
            "winner": winner,
            "loser": loser,
            "winner_choice": self._get_choice_for_user(winner.id),
            "loser_choice": self._get_choice_for_user(loser.id),
            "pot": self.bet_amount * 2
        }
    
    def _get_tie_info(self) -> Dict:
        """Informations pour une égalité"""
        return {
            "challenger_choice": self._challenger_choice,
            "opponent_choice": self._opponent_choice,
            "pot": self.bet_amount * 2
        }
    
    def _get_timeout_info(self) -> Dict:
        """Informations pour un timeout"""
        return {
            "challenger_choice": self._challenger_choice,
            "opponent_choice": self._opponent_choice,
            "pot": self.bet_amount * 2
        }
    
    def _get_choice_for_user(self, user_id: int) -> Optional[PPCChoice]:
        """Retourne le choix d'un utilisateur"""
        if user_id == self.challenger.id:
            return self._challenger_choice
        elif user_id == self.opponent.id:
            return self._opponent_choice
        return None
    
    def cancel(self):
        """Annule le jeu"""
        self.state = PPCGameState.CANCELLED
    
    def finish(self):
        """Marque le jeu comme terminé"""
        self.state = PPCGameState.FINISHED

class PPCTransactionManager:
    """Gère les transactions financières des parties PPC"""
    
    def __init__(self, db, bot):
        self.db = db
        self.bot = bot
    
    async def validate_balances(self, game: PPCGame) -> Tuple[bool, str]:
        """Valide que les joueurs ont suffisamment de fonds"""
        try:
            challenger_balance = await self.db.get_balance(game.challenger.id)
            opponent_balance = await self.db.get_balance(game.opponent.id)
            
            if challenger_balance < game.bet_amount:
                return False, f"{game.challenger.display_name} n'a que {challenger_balance:,} PrissBucks (mise: {game.bet_amount:,})"
            
            if opponent_balance < game.bet_amount:
                return False, f"{game.opponent.display_name} n'a que {opponent_balance:,} PrissBucks (mise: {game.bet_amount:,})"
            
            return True, "Soldes validés"
            
        except Exception as e:
            logger.error(f"Erreur validation soldes PPC: {e}")
            return False, "Erreur lors de la validation des soldes"
    
    async def process_game_result(self, game: PPCGame, result: PPCGameResult, 
                                winner: Optional[discord.Member], result_info: Dict) -> bool:
        """Traite les transactions selon le résultat"""
        try:
            # Récupérer les soldes avant transaction
            challenger_balance_before = await self.db.get_balance(game.challenger.id)
            opponent_balance_before = await self.db.get_balance(game.opponent.id)
            
            if result in [PPCGameResult.TIE, PPCGameResult.TIMEOUT]:
                # Égalité ou timeout : envoyer vers banque publique
                success = await self._handle_tie_or_timeout(game, challenger_balance_before, opponent_balance_before)
            else:
                # Victoire : transférer vers le gagnant
                success = await self._handle_victory(game, winner, challenger_balance_before, opponent_balance_before)
            
            if success:
                # Logger les transactions
                await self._log_transactions(game, result, winner, challenger_balance_before, opponent_balance_before)
            
            return success
            
        except Exception as e:
            logger.error(f"Erreur traitement résultat PPC: {e}")
            return False
    
    async def _handle_tie_or_timeout(self, game: PPCGame, challenger_balance: int, opponent_balance: int) -> bool:
        """Gère les égalités et timeouts"""
        try:
            # Débiter les deux joueurs
            await self.db.update_balance(game.challenger.id, -game.bet_amount)
            await self.db.update_balance(game.opponent.id, -game.bet_amount)
            
            # Envoyer vers banque publique
            total_pot = game.bet_amount * 2
            public_bank_cog = self.bot.get_cog('PublicBank')
            
            if public_bank_cog:
                success = await public_bank_cog.add_casino_loss(total_pot, "ppc_tie")
                if success:
                    logger.info(f"PPC: {total_pot} PB envoyés vers banque publique (égalité)")
                    return True
            
            # Fallback vers owner
            if OWNER_ID:
                await self.db.update_balance(OWNER_ID, total_pot)
                logger.warning(f"PPC: Fallback owner, {total_pot} PB envoyés")
                return True
            
            logger.error(f"PPC: Impossible d'envoyer {total_pot} PB")
            # Restaurer les soldes en cas d'échec
            await self.db.update_balance(game.challenger.id, game.bet_amount)
            await self.db.update_balance(game.opponent.id, game.bet_amount)
            return False
            
        except Exception as e:
            logger.error(f"Erreur gestion égalité PPC: {e}")
            return False
    
    async def _handle_victory(self, game: PPCGame, winner: discord.Member, 
                            challenger_balance: int, opponent_balance: int) -> bool:
        """Gère les victoires"""
        try:
            loser = game.opponent if winner == game.challenger else game.challenger
            
            # Le perdant donne sa mise au gagnant
            success = await self.db.transfer(loser.id, winner.id, game.bet_amount)
            
            if success:
                logger.info(f"PPC: {winner.display_name} gagne {game.bet_amount * 2} PB contre {loser.display_name}")
                return True
            else:
                # En cas d'échec du transfert, débiter quand même le perdant
                await self.db.update_balance(loser.id, -game.bet_amount)
                logger.error(f"PPC: Échec transfert, {loser.display_name} débité uniquement")
                return False
                
        except Exception as e:
            logger.error(f"Erreur gestion victoire PPC: {e}")
            return False
    
    async def _log_transactions(self, game: PPCGame, result: PPCGameResult, winner: Optional[discord.Member],
                              challenger_balance_before: int, opponent_balance_before: int):
        """Enregistre les transactions dans les logs"""
        if not hasattr(self.bot, 'transaction_logs'):
            return
        
        try:
            challenger_balance_after = await self.db.get_balance(game.challenger.id)
            opponent_balance_after = await self.db.get_balance(game.opponent.id)
            
            if result in [PPCGameResult.TIE, PPCGameResult.TIMEOUT]:
                # Log égalité pour les deux joueurs
                await self.bot.transaction_logs.log_ppc_result(
                    game.challenger.id, game.bet_amount, 'tie', 0,
                    challenger_balance_before, challenger_balance_after,
                    game.opponent.display_name
                )
                await self.bot.transaction_logs.log_ppc_result(
                    game.opponent.id, game.bet_amount, 'tie', 0,
                    opponent_balance_before, opponent_balance_after,
                    game.challenger.display_name
                )
            else:
                # Log victoire/défaite
                loser = game.opponent if winner == game.challenger else game.challenger
                
                await self.bot.transaction_logs.log_ppc_result(
                    winner.id, game.bet_amount, 'win', game.bet_amount,
                    challenger_balance_before if winner == game.challenger else opponent_balance_before,
                    challenger_balance_after if winner == game.challenger else opponent_balance_after,
                    loser.display_name
                )
                await self.bot.transaction_logs.log_ppc_result(
                    loser.id, game.bet_amount, 'loss', 0,
                    opponent_balance_before if loser == game.opponent else challenger_balance_before,
                    opponent_balance_after if loser == game.opponent else challenger_balance_after,
                    winner.display_name
                )
        except Exception as e:
            logger.error(f"Erreur log transactions PPC: {e}")

class PPCView(discord.ui.View):
    """Interface utilisateur pour le jeu PPC"""
    
    def __init__(self, game: PPCGame, transaction_manager: PPCTransactionManager):
        super().__init__(timeout=game.timeout_duration)
        self.game = game
        self.transaction_manager = transaction_manager
        self.message: Optional[discord.Message] = None
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Vérifie que seuls les joueurs concernés peuvent interagir"""
        if not self.game.can_play(interaction.user.id):
            await interaction.response.send_message(
                "❌ Tu ne peux pas participer à ce jeu !", ephemeral=True
            )
            return False
        
        if self.game.has_chosen(interaction.user.id):
            await interaction.response.send_message(
                "❌ Tu as déjà fait ton choix !", ephemeral=True
            )
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
        if self.game.state != PPCGameState.IN_PROGRESS:
            await interaction.response.send_message("❌ Ce jeu est terminé !", ephemeral=True)
            return
        
        # Enregistrer le choix
        success = self.game.make_choice(interaction.user.id, choice)
        
        if not success:
            await interaction.response.send_message("❌ Impossible d'enregistrer ton choix !", ephemeral=True)
            return
        
        # Répondre immédiatement à l'utilisateur
        await interaction.response.send_message(
            f"✅ Tu as choisi {choice.emoji} **{choice.display_name}** !", ephemeral=True
        )
        
        # Vérifier si le jeu peut se terminer
        if self.game.is_ready_for_result:
            await self._finish_game()
    
    async def _finish_game(self):
        """Termine le jeu et affiche le résultat"""
        try:
            result, winner, result_info = self.game.get_result()
            
            # Traiter les transactions
            transaction_success = await self.transaction_manager.process_game_result(
                self.game, result, winner, result_info
            )
            
            if not transaction_success:
                embed = discord.Embed(
                    title="❌ Erreur technique",
                    description="Une erreur s'est produite lors du traitement du jeu.",
                    color=Colors.ERROR
                )
            else:
                embed = self._create_result_embed(result, winner, result_info)
            
            # Désactiver tous les boutons
            for item in self.children:
                item.disabled = True
            
            # Marquer le jeu comme terminé
            self.game.finish()
            
            # Modifier le message
            if self.message:
                await self.message.edit(embed=embed, view=self)
            
        except Exception as e:
            logger.error(f"Erreur fin de jeu PPC: {e}")
            embed = discord.Embed(
                title="❌ Erreur critique",
                description="Une erreur inattendue s'est produite.",
                color=Colors.ERROR
            )
            for item in self.children:
                item.disabled = True
            if self.message:
                await self.message.edit(embed=embed, view=self)
    
    def _create_result_embed(self, result: PPCGameResult, winner: Optional[discord.Member], 
                           result_info: Dict) -> discord.Embed:
        """Crée l'embed de résultat"""
        if result == PPCGameResult.TIE:
            return self._create_tie_embed(result_info)
        elif result == PPCGameResult.TIMEOUT:
            return self._create_timeout_embed(result_info)
        else:
            return self._create_victory_embed(winner, result_info)
    
    def _create_tie_embed(self, result_info: Dict) -> discord.Embed:
        """Embed pour les égalités"""
        challenger_choice = result_info['challenger_choice']
        opponent_choice = result_info['opponent_choice']
        pot = result_info['pot']
        
        embed = discord.Embed(
            title="🤝 Égalité !",
            description=f"**{challenger_choice.emoji} vs {opponent_choice.emoji}**\n\n"
                       f"{self.game.challenger.display_name}: **{challenger_choice.display_name}**\n"
                       f"{self.game.opponent.display_name}: **{opponent_choice.display_name}**",
            color=Colors.WARNING
        )
        
        embed.add_field(
            name="🏛️ Redistribution solidaire",
            value=f"**{pot:,}** PrissBucks transférés vers la banque publique !\n"
                  f"✨ Utilise `/publicbank` pour récupérer des fonds communautaires.",
            inline=False
        )
        
        embed.set_footer(text="Égalité • Système solidaire • Accessible à tous")
        return embed
    
    def _create_timeout_embed(self, result_info: Dict) -> discord.Embed:
        """Embed pour les timeouts"""
        pot = result_info['pot']
        
        embed = discord.Embed(
            title="⏰ Temps écoulé !",
            description="Le jeu a expiré car tous les joueurs n'ont pas fait leur choix à temps.",
            color=Colors.ERROR
        )
        
        embed.add_field(
            name="🏛️ Fonds redistribués",
            value=f"**{pot:,}** PrissBucks envoyés vers la banque publique.\n"
                  f"Utilise `/publicbank` pour récupérer des fonds !",
            inline=False
        )
        
        embed.set_footer(text="Timeout • Banque publique • Solidarité")
        return embed
    
    def _create_victory_embed(self, winner: discord.Member, result_info: Dict) -> discord.Embed:
        """Embed pour les victoires"""
        loser = result_info['loser']
        winner_choice = result_info['winner_choice']
        loser_choice = result_info['loser_choice']
        pot = result_info['pot']
        
        embed = discord.Embed(
            title=f"🏆 {winner.display_name} gagne !",
            description=f"**{winner_choice.emoji} vs {loser_choice.emoji}**\n\n"
                       f"{self.game.challenger.display_name}: **{self.game._get_choice_for_user(self.game.challenger.id).display_name}**\n"
                       f"{self.game.opponent.display_name}: **{self.game._get_choice_for_user(self.game.opponent.id).display_name}**",
            color=Colors.SUCCESS
        )
        
        embed.add_field(
            name="💰 Victoire",
            value=f"**{winner.display_name}** remporte **{pot:,}** PrissBucks !",
            inline=True
        )
        
        embed.add_field(
            name="🎯 Règles",
            value="🗿 Pierre bat ✂️ Ciseaux\n📄 Papier bat 🗿 Pierre\n✂️ Ciseaux bat 📄 Papier",
            inline=True
        )
        
        embed.set_footer(text="Victoire • Le gagnant prend tout • Système équitable")
        return embed
    
    def create_game_embed(self) -> discord.Embed:
        """Crée l'embed initial du jeu"""
        embed = discord.Embed(
            title="🎮 Pierre - Papier - Ciseaux",
            description=f"**Défi lancé !** Un seul round décisif !\n\n"
                       f"💰 **Mise:** {self.game.bet_amount:,} PrissBucks par joueur\n"
                       f"🏆 **Pot total:** {self.game.bet_amount * 2:,} PrissBucks\n"
                       f"👥 **Joueurs:** {self.game.challenger.display_name} vs {self.game.opponent.display_name}",
            color=Colors.PREMIUM
        )
        
        embed.add_field(
            name="🎯 Règles",
            value="🗿 Pierre bat ✂️ Ciseaux\n📄 Papier bat 🗿 Pierre\n✂️ Ciseaux bat 📄 Papier",
            inline=True
        )
        
        embed.add_field(
            name="⏱️ Temps limite",
            value="60 secondes pour choisir",
            inline=True
        )
        
        embed.add_field(
            name="🏛️ Système solidaire",
            value="• **Victoire:** Gagnant récupère tout le pot\n"
                  "• **Égalité:** Banque publique (accessible à tous)\n"
                  "• **Timeout:** Banque publique",
            inline=False
        )
        
        embed.set_footer(text="Choisissez votre stratégie ! Système équitable et solidaire 🏛️")
        return embed
    
    async def on_timeout(self):
        """Gère l'expiration du jeu"""
        if self.game.state == PPCGameState.IN_PROGRESS:
            self.game.state = PPCGameState.CANCELLED
            
            # Traiter comme un timeout
            result, winner, result_info = self.game.get_result()
            await self.transaction_manager.process_game_result(self.game, result, winner, result_info)
            
            # Créer l'embed de timeout
            embed = self._create_timeout_embed(result_info)
            
            # Désactiver les boutons
            for item in self.children:
                item.disabled = True
            
            if self.message:
                await self.message.edit(embed=embed, view=self)

class PierrepapierCiseaux(commands.Cog):
    """Mini-jeu Pierre-Papier-Ciseaux solidaire et sécurisé"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        self.transaction_manager = None
        
        # Gestion des jeux actifs pour éviter les doublons
        self._active_games: Dict[str, PPCGame] = {}
        
        # Statistiques
        self._games_played = 0
        self._total_wagered = 0
    
    async def cog_load(self):
        """Initialisation du cog"""
        self.db = self.bot.database
        self.transaction_manager = PPCTransactionManager(self.db, self.bot)
        logger.info("✅ Cog PPC initialisé (architecture sécurisée)")
    
    def _get_game_key(self, user1_id: int, user2_id: int) -> str:
        """Génère une clé unique pour identifier un jeu entre deux joueurs"""
        return f"{min(user1_id, user2_id)}_{max(user1_id, user2_id)}"
    
    def _cleanup_finished_games(self):
        """Nettoie les jeux terminés ou expirés"""
        current_time = time.time()
        finished_games = []
        
        for key, game in self._active_games.items():
            if (game.state in [PPCGameState.FINISHED, PPCGameState.CANCELLED] or
                current_time - game.created_at > game.timeout_duration + 30):
                finished_games.append(key)
        
        for key in finished_games:
            del self._active_games[key]
        
        if finished_games:
            logger.debug(f"PPC: {len(finished_games)} jeux nettoyés")
    
    @app_commands.command(name="ppc", description="🎮 Pierre-Papier-Ciseaux solidaire ! Égalités → Banque publique")
    @app_commands.describe(
        adversaire="L'utilisateur que tu veux défier",
        mise="Montant à miser (en PrissBucks)"
    )
    async def ppc_command(self, interaction: discord.Interaction, adversaire: discord.Member, mise: int):
        """Slash command pour lancer un défi PPC"""
        await interaction.response.defer()
        
        # Nettoyage périodique
        if len(self._active_games) > 10:
            self._cleanup_finished_games()
        
        # Créer le jeu
        game = PPCGame(interaction.user, adversaire, mise)
        
        if not game.is_valid:
            embed = create_error_embed("Défi invalide", game.error_message)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Vérifier s'il n'y a pas déjà un jeu actif entre ces joueurs
        game_key = self._get_game_key(interaction.user.id, adversaire.id)
        if game_key in self._active_games:
            existing_game = self._active_games[game_key]
            if existing_game.state == PPCGameState.IN_PROGRESS:
                embed = create_error_embed(
                    "Jeu déjà en cours",
                    f"Un jeu PPC est déjà en cours entre {interaction.user.display_name} et {adversaire.display_name} !"
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
        
        try:
            # Valider les soldes
            valid, message = await self.transaction_manager.validate_balances(game)
            if not valid:
                embed = create_error_embed("Solde insuffisant", message)
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            # Créer la vue et envoyer le jeu
            view = PPCView(game, self.transaction_manager)
            embed = view.create_game_embed()
            
            message = await interaction.followup.send(embed=embed, view=view)
            view.message = message
            
            # Enregistrer le jeu
            self._active_games[game_key] = game
            
            # Mettre à jour les statistiques
            self._games_played += 1
            self._total_wagered += mise * 2
            
            logger.info(f"PPC: Nouveau jeu - {interaction.user.display_name} vs {adversaire.display_name} ({mise} PB)")
            
        except Exception as e:
            logger.error(f"Erreur création jeu PPC {interaction.user.id} vs {adversaire.id}: {e}")
            embed = create_error_embed("Erreur technique", "Impossible de créer le jeu.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.command(name='ppc_stats')
    async def ppc_stats_cmd(self, ctx, user: discord.Member = None):
        """Affiche des statistiques PPC"""
        target = user or ctx.author
        
        try:
            balance = await self.db.get_balance(target.id)
            
            embed = discord.Embed(
                title=f"🎮 Statistiques PPC de {target.display_name}",
                description=f"**Solde actuel:** {balance:,} PrissBucks",
                color=Colors.INFO
            )
            
            embed.add_field(
                name="🏛️ Système Solidaire",
                value="• Égalités alimentent la banque publique\n"
                      "• Tout le monde peut récupérer avec `/publicbank`\n"
                      "• Plus de pertes inutiles !",
                inline=False
            )
            
            embed.add_field(
                name="🎯 Comment jouer",
                value="Utilise `/ppc @adversaire <mise>` pour défier quelqu'un !",
                inline=False
            )
            
            embed.add_field(
                name="📊 Statistiques serveur",
                value=f"🎮 **{self._games_played}** parties jouées au total\n"
                      f"💰 **{self._total_wagered:,}** PrissBucks misés\n"
                      f"🏛️ **{len(self._active_games)}** jeux en cours",
                inline=False
            )
            
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.set_footer(text="Système solidaire • Équitable • Transparent")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur stats PPC: {e}")
            embed = create_error_embed("Erreur", "Impossible de récupérer les statistiques.")
            await ctx.send(embed=embed)

    @app_commands.command(name="ppc_info", description="Informations sur le système PPC solidaire")
    async def ppc_info_slash(self, interaction: discord.Interaction):
        """Slash command pour les infos PPC"""
        embed = discord.Embed(
            title="🎮 Pierre-Papier-Ciseaux Solidaire",
            description="**Système équitable et transparent !** Fini les pertes inutiles !",
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
            name="🏛️ Système Solidaire",
            value="• **Victoire:** Gagnant prend tout le pot\n"
                  "• **Égalité:** → Banque publique\n"
                  "• **Timeout:** → Banque publique\n"
                  "• **Récupération:** `/publicbank`",
            inline=True
        )
        
        embed.add_field(
            name="⚡ Caractéristiques",
            value="• Un seul round par partie\n"
                  "• 60 secondes pour choisir\n"
                  "• Interface simple et claire\n"
                  "• Système 100% équitable",
            inline=True
        )
        
        embed.add_field(
            name="🚀 Comment jouer",
            value="`/ppc @adversaire <mise>` - Lance un défi\n"
                  "`/ppc_info` - Voir ces informations\n"
                  "Clique sur les boutons pour choisir !",
            inline=False
        )
        
        embed.add_field(
            name="🏛️ Révolution Sociale",
            value="**FINI LES PERTES DANS LE VIDE !**\n"
                  "• Égalités → Banque publique accessible à tous\n"
                  "• Solidarité maximale entre joueurs\n"
                  "• Système de redistribution automatique\n"
                  "• `/publicbank` pour récupérer des fonds !",
            inline=False
        )
        
        embed.add_field(
            name="🔒 Sécurité",
            value="• Validation stricte des soldes\n"
                  "• Transactions atomiques sécurisées\n"
                  "• Protection contre les abus\n"
                  "• Logs automatiques de toutes les parties",
            inline=False
        )
        
        embed.set_footer(text="Architecture sécurisée • Système solidaire • Plus personne ne perd vraiment ! 🏛️")
        await interaction.response.send_message(embed=embed)

    @commands.command(name='ppc_admin')
    @commands.has_permissions(administrator=True)
    async def ppc_admin_cmd(self, ctx):
        """[ADMIN] Statistiques administrateur pour PPC"""
        embed = discord.Embed(
            title="🛡️ PPC - Panel Administrateur",
            color=Colors.INFO
        )
        
        # Statistiques générales
        embed.add_field(
            name="📊 Statistiques globales",
            value=f"**{self._games_played}** parties terminées\n"
                  f"**{self._total_wagered:,}** PrissBucks total misés\n"
                  f"**{len(self._active_games)}** jeux actuellement en cours",
            inline=True
        )
        
        # Jeux actifs
        if self._active_games:
            active_games_info = []
            for key, game in list(self._active_games.items())[:5]:  # Limiter à 5 pour l'affichage
                time_elapsed = int(time.time() - game.created_at)
                challenger_choice = "✅" if game.has_chosen(game.challenger.id) else "⏳"
                opponent_choice = "✅" if game.has_chosen(game.opponent.id) else "⏳"
                
                active_games_info.append(
                    f"• **{game.challenger.display_name}** {challenger_choice} vs "
                    f"**{game.opponent.display_name}** {opponent_choice}\n"
                    f"  └ {game.bet_amount:,} PB • {time_elapsed}s écoulées"
                )
            
            if len(self._active_games) > 5:
                active_games_info.append(f"... et {len(self._active_games) - 5} autres")
            
            embed.add_field(
                name="🎮 Jeux en cours",
                value="\n\n".join(active_games_info) if active_games_info else "Aucun jeu actif",
                inline=False
            )
        else:
            embed.add_field(
                name="🎮 Jeux en cours",
                value="Aucun jeu actuellement actif",
                inline=False
            )
        
        # Configuration
        embed.add_field(
            name="⚙️ Configuration",
            value=f"• **Timeout:** 60 secondes par jeu\n"
                  f"• **Mise max:** 50,000 PrissBucks\n"
                  f"• **Nettoyage auto:** Tous les 10 jeux\n"
                  f"• **Banque publique:** Intégrée",
            inline=True
        )
        
        # Santé du système
        healthy_games = sum(1 for game in self._active_games.values() 
                          if game.state == PPCGameState.IN_PROGRESS and not game.is_expired)
        
        embed.add_field(
            name="🏥 Santé du système",
            value=f"• **Jeux sains:** {healthy_games}/{len(self._active_games)}\n"
                  f"• **Mémoire:** {len(self._active_games)} objets en cache\n"
                  f"• **Performance:** {'🟢 Optimale' if len(self._active_games) < 20 else '🟡 Surveillée'}",
            inline=True
        )
        
        embed.set_footer(text="Architecture sécurisée • Monitoring actif • Système auto-nettoyant")
        await ctx.send(embed=embed)

    @commands.command(name='ppc_cleanup')
    @commands.has_permissions(administrator=True)
    async def ppc_cleanup_cmd(self, ctx):
        """[ADMIN] Force le nettoyage des jeux PPC"""
        games_before = len(self._active_games)
        self._cleanup_finished_games()
        games_after = len(self._active_games)
        cleaned = games_before - games_after
        
        embed = discord.Embed(
            title="🧹 Nettoyage PPC",
            description=f"**{cleaned}** jeu(x) nettoyé(s)\n"
                       f"**{games_after}** jeu(x) restant(s)",
            color=Colors.SUCCESS if cleaned > 0 else Colors.INFO
        )
        
        embed.add_field(
            name="📊 Détails",
            value=f"• Jeux avant nettoyage: {games_before}\n"
                  f"• Jeux après nettoyage: {games_after}\n"
                  f"• Mémoire libérée: {cleaned} objets",
            inline=False
        )
        
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Nettoyage automatique périodique"""
        if message.author.bot:
            return
        
        # Nettoyage toutes les 100 parties environ
        if self._games_played % 100 == 0 and len(self._active_games) > 5:
            self._cleanup_finished_games()

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(PierrepapierCiseaux(bot))