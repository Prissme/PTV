import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple
import time

from config import Colors, Emojis, OWNER_ID
from utils.embeds import create_error_embed, create_success_embed

logger = logging.getLogger(__name__)

class RouletteConfig:
    """Configuration centralisée de la roulette"""
    MIN_BET = 10
    MAX_BET = 100000
    TAX_RATE = 0.01  # 1% de taxe sur les gains
    COOLDOWN_SECONDS = 4  # MODIFIÉ: 3 → 4 secondes
    
    # Numéros et couleurs de la roulette européenne
    RED_NUMBERS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})
    BLACK_NUMBERS = frozenset({2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35})
    GREEN_NUMBERS = frozenset({0})
    
    # Messages d'animation optimisés
    ANIMATION_PHASES = [
        "🎰 **La roulette tourne...**",
        "🌀 **La bille roule...**", 
        "⚡ **Ralentissement...**",
        "🎯 **Résultat imminent...**"
    ]

class RouletteGame:
    """Classe représentant une partie de roulette"""
    
    def __init__(self, user_id: int, bet_type: str, bet_amount: int):
        self.user_id = user_id
        self.bet_type = self._normalize_bet_type(bet_type)
        self.bet_amount = bet_amount
        self.winning_number: Optional[int] = None
        self.winnings = 0
        self.is_valid = False
        self.error_message = ""
        
        self._validate_game()
    
    def _normalize_bet_type(self, bet_type: str) -> str:
        """Normalise et valide le type de pari"""
        bet_type = bet_type.lower().strip()
        
        # Pari sur un numéro spécifique
        if bet_type.isdigit():
            number = int(bet_type)
            if 0 <= number <= 36:
                return f"number_{number}"
        
        # Paris simples
        valid_bets = {"red", "black", "even", "odd", "low", "high"}
        if bet_type in valid_bets:
            return bet_type
            
        return "invalid"
    
    def _validate_game(self):
        """Valide les paramètres du jeu"""
        # Validation du type de pari
        if self.bet_type == "invalid":
            self.error_message = (
                "🎯 **Pari invalide !**\n\n"
                "**Paris sur numéros (36:1) :** 0-36\n"
                "**Paris simples (2:1) :** red, black, even, odd, low (1-18), high (19-36)"
            )
            return
        
        # Validation du montant
        if self.bet_amount < RouletteConfig.MIN_BET:
            self.error_message = f"💰 **Mise trop petite !** Minimum : {RouletteConfig.MIN_BET:,} PrissBucks"
            return
        
        if self.bet_amount > RouletteConfig.MAX_BET:
            self.error_message = f"💰 **Mise trop élevée !** Maximum : {RouletteConfig.MAX_BET:,} PrissBucks"
            return
        
        self.is_valid = True
    
    def spin(self) -> int:
        """Effectue le spin et calcule les gains"""
        self.winning_number = random.randint(0, 36)
        self.winnings = self._calculate_winnings()
        return self.winning_number
    
    def _calculate_winnings(self) -> int:
        """Calcule les gains selon le type de pari"""
        if not self.winning_number:
            return 0
        
        # Pari sur un numéro spécifique (35:1 + mise remboursée)
        if self.bet_type.startswith("number_"):
            bet_number = int(self.bet_type.split("_")[1])
            if self.winning_number == bet_number:
                gross_winnings = self.bet_amount * 36
                tax = int(gross_winnings * RouletteConfig.TAX_RATE)
                return max(0, gross_winnings - tax)
            return 0
        
        # Paris simples (1:1 + mise remboursée)
        winning_conditions = {
            "red": self.winning_number in RouletteConfig.RED_NUMBERS,
            "black": self.winning_number in RouletteConfig.BLACK_NUMBERS,
            "even": self.winning_number != 0 and self.winning_number % 2 == 0,
            "odd": self.winning_number != 0 and self.winning_number % 2 == 1,
            "low": 1 <= self.winning_number <= 18,
            "high": 19 <= self.winning_number <= 36
        }
        
        if winning_conditions.get(self.bet_type, False):
            gross_winnings = self.bet_amount * 2
            tax = int(gross_winnings * RouletteConfig.TAX_RATE)
            return max(0, gross_winnings - tax)
        
        return 0
    
    def get_number_info(self) -> Tuple[str, str, str]:
        """Retourne les informations du numéro gagnant"""
        if self.winning_number in RouletteConfig.RED_NUMBERS:
            return "Rouge", "🔴", "#ff0000"
        elif self.winning_number in RouletteConfig.BLACK_NUMBERS:
            return "Noir", "⚫", "#000000"
        else:  # 0
            return "Vert", "💚", "#00ff00"

class RouletteAnimator:
    """Gère les animations de la roulette de manière optimisée"""
    
    @staticmethod
    async def animate_spin(edit_func, winning_number: int) -> None:
        """Animation optimisée du spin"""
        try:
            # Phase 1: Préparation (1s)
            embed = discord.Embed(
                title="🎰 LA ROULETTE TOURNE...",
                description="```\n🎯 La bille est lancée !\n```",
                color=Colors.WARNING
            )
            await edit_func(embed=embed)
            await asyncio.sleep(1.0)

            # Phase 2: Animation rapide (2s)
            for i, phase_msg in enumerate(RouletteConfig.ANIMATION_PHASES[1:3]):
                fake_numbers = [random.randint(0, 36) for _ in range(3)]
                animation_text = " → ".join([f"**{n}**" for n in fake_numbers])
                
                embed = discord.Embed(
                    title=phase_msg,
                    description=f"```\n{animation_text} → ...\n```",
                    color=Colors.INFO
                )
                await edit_func(embed=embed)
                await asyncio.sleep(0.7)

            # Phase 3: Résultat final (1s)
            embed = discord.Embed(
                title="🎯 RÉSULTAT !",
                description=f"```\nNuméro gagnant : {winning_number}\n```",
                color=Colors.SUCCESS
            )
            await edit_func(embed=embed)
            await asyncio.sleep(0.8)
            
        except Exception as e:
            logger.error(f"Erreur animation roulette: {e}")
            # Animation simplifiée en cas d'erreur
            embed = discord.Embed(
                title="🎰 Roulette",
                description=f"**Numéro tiré :** {winning_number}",
                color=Colors.INFO
            )
            await edit_func(embed=embed)

class RouletteEmbeds:
    """Générateur d'embeds pour la roulette"""
    
    @staticmethod
    def create_result_embed(game: RouletteGame, user: discord.Member, 
                          balance_before: int, balance_after: int) -> discord.Embed:
        """Crée l'embed de résultat optimisé avec version allégée pour les pertes"""
        color_name, color_emoji, hex_color = game.get_number_info()
        
        # Couleur de l'embed selon le résultat
        if game.winnings > 0:
            # ========== VERSION COMPLÈTE POUR LES VICTOIRES ==========
            title = "🎉 VICTOIRE !"
            color = Colors.SUCCESS
            profit = game.winnings - game.bet_amount
            
            embed = discord.Embed(
                title=title,
                description=f"## 🎲 **Numéro tiré : {game.winning_number}** {color_emoji}",
                color=color
            )
            
            # Informations du pari
            bet_description = RouletteEmbeds._get_bet_description(game.bet_type)
            embed.add_field(
                name="🎯 Ton pari",
                value=f"{bet_description}\n💸 **{game.bet_amount:,}** PrissBucks",
                inline=True
            )
            
            # Résultat financier
            embed.add_field(
                name="💰 Gains",
                value=f"🎊 **+{game.winnings:,}** PrissBucks\n💎 Profit : **+{profit:,}** PB",
                inline=True
            )
            
            # Solde actuel
            embed.add_field(
                name="💳 Nouveau solde",
                value=f"{'💎' if balance_after > 1000 else '💰'} **{balance_after:,}** PrissBucks",
                inline=True
            )
            
            # Message selon le résultat
            embed.add_field(
                name="🎊 Félicitations !",
                value="La chance était de ton côté ! 🍀",
                inline=False
            )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="🎰 Rejoue dans 4 secondes ! Historique: /transactions")
            
        else:
            # ========== VERSION ALLÉGÉE POUR LES DÉFAITES ==========
            title = "💔 Pas cette fois..."
            color = Colors.ERROR
            profit = -game.bet_amount
            
            embed = discord.Embed(
                title=title,
                description=f"🎲 **{game.winning_number}** {color_emoji} • Perte: **{profit:,}** PB",
                color=color
            )
            
            # Version compacte - un seul field
            embed.add_field(
                name="🏛️ Impact Social",
                value=f"**{game.bet_amount:,} PB** → Banque publique\n✨ `/publicbank` pour récupérer !",
                inline=False
            )
            
            # Footer minimaliste
            embed.set_footer(text="🎰 Retry dans 4s • Plus de chance la prochaine fois !")
        
        return embed
    
    @staticmethod
    def _get_bet_description(bet_type: str) -> str:
        """Retourne la description du type de pari"""
        if bet_type.startswith("number_"):
            number = bet_type.split("_")[1]
            return f"🎯 **Numéro {number}**"
        
        descriptions = {
            "red": "🔴 **Rouge**",
            "black": "⚫ **Noir**", 
            "even": "⚪ **Pair**",
            "odd": "🔘 **Impair**",
            "low": "📉 **1-18**",
            "high": "📈 **19-36**"
        }
        return descriptions.get(bet_type, bet_type)

class Roulette(commands.Cog):
    """Mini-jeu de roulette sécurisé et optimisé"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Système de cooldown optimisé
        self._cooldowns: Dict[int, float] = {}
        
        # Statistiques et monitoring
        self._games_played = 0
        self._total_wagered = 0
        
    async def cog_load(self):
        """Initialisation du cog"""
        self.db = self.bot.database
        logger.info("✅ Cog Roulette initialisé (sécurisé et optimisé) - Cooldown: 4s")
    
    def _check_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown de manière optimisée"""
        now = time.time()
        
        if user_id in self._cooldowns:
            elapsed = now - self._cooldowns[user_id]
            if elapsed < RouletteConfig.COOLDOWN_SECONDS:
                return RouletteConfig.COOLDOWN_SECONDS - elapsed
        
        self._cooldowns[user_id] = now
        return 0
    
    async def _cleanup_old_cooldowns(self):
        """Nettoie les anciens cooldowns pour optimiser la mémoire"""
        if not self._cooldowns:
            return
            
        now = time.time()
        expired_users = [
            user_id for user_id, timestamp in self._cooldowns.items()
            if now - timestamp > 300  # 5 minutes
        ]
        
        for user_id in expired_users:
            del self._cooldowns[user_id]
        
        if expired_users:
            logger.debug(f"Roulette: {len(expired_users)} cooldowns expirés nettoyés")
    
    async def _send_to_public_bank(self, amount: int) -> bool:
        """Envoie l'argent vers la banque publique de manière sécurisée"""
        try:
            public_bank_cog = self.bot.get_cog('PublicBank')
            
            if public_bank_cog:
                success = await public_bank_cog.add_casino_loss(amount, "roulette_loss")
                if success:
                    logger.info(f"🏛️ Roulette: {amount} PB envoyés vers la banque publique")
                    return True
            
            # Fallback vers l'owner
            if OWNER_ID:
                await self.db.update_balance(OWNER_ID, amount)
                logger.warning(f"Roulette: Fallback owner, {amount} PB envoyés")
                return True
                
            logger.error(f"Roulette: Impossible d'envoyer {amount} PB (ni banque ni owner)")
            return False
            
        except Exception as e:
            logger.error(f"Erreur envoi banque publique: {e}")
            return False
    
    async def _process_game_transaction(self, game: RouletteGame, balance_before: int) -> Tuple[int, bool]:
        """Traite la transaction de jeu de manière atomique"""
        try:
            # Débiter la mise du joueur
            await self.db.update_balance(game.user_id, -game.bet_amount)
            
            if game.winnings > 0:
                # VICTOIRE : Créditer les gains
                await self.db.update_balance(game.user_id, game.winnings)
                
                # Taxe optionnelle vers banque publique
                if game.bet_type.startswith("number_"):
                    gross_profit = game.bet_amount * 35
                else:
                    gross_profit = game.bet_amount
                
                tax = int(gross_profit * RouletteConfig.TAX_RATE)
                if tax > 0:
                    await self._send_to_public_bank(tax)
                
                balance_after = balance_before - game.bet_amount + game.winnings
            else:
                # DÉFAITE : Envoyer la mise vers la banque publique
                success = await self._send_to_public_bank(game.bet_amount)
                if not success:
                    logger.error(f"Échec envoi {game.bet_amount} PB vers banque publique")
                
                balance_after = balance_before - game.bet_amount
            
            return balance_after, True
            
        except Exception as e:
            logger.error(f"Erreur transaction roulette {game.user_id}: {e}")
            # En cas d'erreur, essayer de restaurer le solde
            try:
                await self.db.update_balance(game.user_id, game.bet_amount)
            except:
                pass
            return balance_before, False
    
    async def _log_game_result(self, game: RouletteGame, balance_before: int, balance_after: int):
        """Enregistre le résultat dans les logs"""
        try:
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_roulette_result(
                    user_id=game.user_id,
                    bet=game.bet_amount,
                    winnings=game.winnings,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    number=game.winning_number
                )
        except Exception as e:
            logger.error(f"Erreur log roulette: {e}")
    
    @app_commands.command(name="roulette", description="🎰 Roulette européenne sécurisée avec animations !")
    @app_commands.describe(
        pari="Type de pari: red, black, even, odd, low (1-18), high (19-36), ou un numéro (0-36)",
        mise="Montant à miser en PrissBucks"
    )
    async def roulette_slash(self, interaction: discord.Interaction, pari: str, mise: int):
        """Slash command pour la roulette"""
        await interaction.response.defer()
        await self._execute_roulette(interaction, pari, mise, is_slash=True)
    
    @commands.command(name='roulette', aliases=['roul', 'casino'])
    async def roulette_cmd(self, ctx, bet_type: str, bet_amount: int):
        """Commande prefix pour la roulette"""
        await self._execute_roulette(ctx, bet_type, bet_amount)
    
    async def _execute_roulette(self, ctx_or_interaction, bet_type: str, bet_amount: int, is_slash=False):
        """Logique principale de la roulette"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
            edit_func = ctx_or_interaction.edit_original_response
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
            # Pour les messages normaux, on créera le message puis on l'éditera
            edit_func = None
        
        # Vérification du cooldown (4 secondes maintenant)
        cooldown_remaining = self._check_cooldown(user.id)
        if cooldown_remaining > 0:
            embed = discord.Embed(
                title="⏰ Cooldown actif",
                description=f"Attends **{cooldown_remaining:.1f}s** avant le prochain spin !",
                color=Colors.WARNING
            )
            await send_func(embed=embed)
            return
        
        # Création et validation du jeu
        game = RouletteGame(user.id, bet_type, bet_amount)
        
        if not game.is_valid:
            embed = create_error_embed("Paramètres invalides", game.error_message)
            await send_func(embed=embed)
            return
        
        try:
            # Vérification du solde
            balance_before = await self.db.get_balance(user.id)
            
            if balance_before < game.bet_amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Tu as {balance_before:,} PrissBucks mais tu veux miser {game.bet_amount:,} PrissBucks."
                )
                await send_func(embed=embed)
                return
            
            # Effectuer le spin
            winning_number = game.spin()
            
            # Animation du spin
            if not edit_func:
                # Pour les commandes prefix, créer le message d'abord
                msg = await send_func("🎰 **La roulette tourne...**")
                edit_func = msg.edit
            
            await RouletteAnimator.animate_spin(edit_func, winning_number)
            
            # Traitement de la transaction
            balance_after, transaction_success = await self._process_game_transaction(game, balance_before)
            
            if not transaction_success:
                embed = create_error_embed("Erreur technique", "Une erreur s'est produite lors du jeu.")
                await edit_func(embed=embed)
                return
            
            # Enregistrement dans les logs
            await self._log_game_result(game, balance_before, balance_after)
            
            # Affichage du résultat (embed allégé pour les défaites)
            result_embed = RouletteEmbeds.create_result_embed(game, user, balance_before, balance_after)
            await edit_func(embed=result_embed)
            
            # Mise à jour des statistiques
            self._games_played += 1
            self._total_wagered += game.bet_amount
            
            # Nettoyage périodique
            if self._games_played % 50 == 0:
                await self._cleanup_old_cooldowns()
            
            # Log de l'action
            result_text = "GAGNE" if game.winnings > 0 else "PERD"
            logger.info(f"Roulette: {user} {result_text} {game.bet_amount} PB sur #{winning_number}")
            
        except Exception as e:
            logger.error(f"Erreur critique roulette {user.id}: {e}")
            embed = create_error_embed(
                "Erreur inattendue", 
                "Une erreur technique s'est produite. Réessaie dans quelques instants."
            )
            
            if edit_func:
                await edit_func(embed=embed)
            else:
                await send_func(embed=embed)
    
    @commands.command(name='roulette_stats')
    @commands.is_owner()
    async def roulette_stats_cmd(self, ctx):
        """[OWNER] Statistiques de la roulette"""
        embed = discord.Embed(
            title="🎰 Statistiques Roulette",
            color=Colors.INFO
        )
        
        embed.add_field(
            name="📊 Session actuelle",
            value=f"**{self._games_played}** parties jouées\n"
                  f"**{self._total_wagered:,}** PrissBucks misés\n"
                  f"**{len(self._cooldowns)}** cooldowns actifs",
            inline=True
        )
        
        embed.add_field(
            name="⚙️ Configuration",
            value=f"**Mise min/max :** {RouletteConfig.MIN_BET:,}/{RouletteConfig.MAX_BET:,} PB\n"
                  f"**Cooldown :** {RouletteConfig.COOLDOWN_SECONDS}s\n"
                  f"**Taxe :** {RouletteConfig.TAX_RATE*100}%",
            inline=True
        )
        
        await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Roulette(bot))