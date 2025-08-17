"""
Système de braquage de banque pour le bot économie
Version refactorisée avec persistance DB et architecture robuste
"""

import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple
import time

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_warning_embed

logger = logging.getLogger(__name__)


class HeistConfig:
    """Configuration centralisée du système de braquage"""
    
    # Paramètres de base
    MIN_HEIST_AMOUNT = 100
    MAX_HEIST_AMOUNT = 50000
    
    # Probabilités (en %)
    SUCCESS_BASE_RATE = 40
    CRITICAL_SUCCESS_RATE = 5
    CRITICAL_FAILURE_RATE = 10
    
    # Multiplicateurs de gain/perte
    SUCCESS_MULTIPLIER = 1.5
    CRITICAL_SUCCESS_MULTIPLIER = 3.0
    FAILURE_PENALTY_RATE = 0.7
    CRITICAL_FAILURE_PENALTY_RATE = 1.2
    
    # Cooldowns
    COOLDOWN_HOURS = 2
    COOLDOWN_SECONDS = COOLDOWN_HOURS * 3600
    
    # Limites de sécurité
    MIN_BALANCE_REQUIRED = 500
    MAX_DAILY_HEISTS = 5


class HeistResult:
    """Résultat d'un braquage avec toutes les informations"""
    
    def __init__(self, success: bool, critical: bool, amount_won: int, 
                 amount_lost: int, description: str, multiplier: float = 1.0):
        self.success = success
        self.critical = critical
        self.amount_won = amount_won
        self.amount_lost = amount_lost
        self.net_result = amount_won - amount_lost
        self.description = description
        self.multiplier = multiplier
        self.timestamp = datetime.now(timezone.utc)
    
    @property
    def is_profit(self) -> bool:
        return self.net_result > 0
    
    @property
    def result_type(self) -> str:
        if self.critical:
            return "CRITIQUE_SUCCESS" if self.success else "CRITIQUE_ECHEC"
        return "SUCCESS" if self.success else "ECHEC"


class HeistManager:
    """Gestionnaire principal des braquages"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Cache pour les cooldowns (fallback)
        self._memory_cooldowns: Dict[int, datetime] = {}
        self._daily_attempts: Dict[int, Dict] = {}
    
    async def initialize(self):
        """Initialise le système avec la DB"""
        self.db = self.bot.database
        await self._create_cooldown_tables()
    
    async def _create_cooldown_tables(self):
        """Crée les tables pour la persistance des cooldowns"""
        if not self.db or not self.db.pool:
            return
            
        try:
            async with self.db.pool.acquire() as conn:
                # Table des cooldowns persistants
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS cooldowns (
                        user_id BIGINT NOT NULL,
                        cooldown_type VARCHAR(50) NOT NULL,
                        last_used TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        data JSONB DEFAULT '{}',
                        PRIMARY KEY (user_id, cooldown_type)
                    )
                ''')
                
                # Index pour les performances
                await conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_cooldowns_user_type 
                    ON cooldowns(user_id, cooldown_type)
                ''')
                
                logger.info("Tables cooldowns initialisées pour bank_heist")
        except Exception as e:
            logger.error(f"Erreur création tables heist: {e}")
    
    async def check_heist_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown avec persistance DB"""
        # Essayer la DB d'abord
        if self.db and self.db.pool:
            try:
                async with self.db.pool.acquire() as conn:
                    row = await conn.fetchrow("""
                        SELECT last_used FROM cooldowns 
                        WHERE user_id = $1 AND cooldown_type = 'bank_heist'
                    """, user_id)
                    
                    if row and row['last_used']:
                        elapsed = (datetime.now(timezone.utc) - row['last_used']).total_seconds()
                        remaining = HeistConfig.COOLDOWN_SECONDS - elapsed
                        return max(0, remaining)
                    return 0
            except Exception as e:
                logger.error(f"Erreur vérification cooldown DB: {e}")
        
        # Fallback mémoire
        return self._check_memory_cooldown(user_id)
    
    def _check_memory_cooldown(self, user_id: int) -> float:
        """Fallback cooldown en mémoire"""
        if user_id not in self._memory_cooldowns:
            return 0
        
        elapsed = (datetime.now(timezone.utc) - self._memory_cooldowns[user_id]).total_seconds()
        remaining = HeistConfig.COOLDOWN_SECONDS - elapsed
        return max(0, remaining)
    
    async def set_heist_cooldown(self, user_id: int):
        """Met en cooldown avec persistance DB"""
        now = datetime.now(timezone.utc)
        
        # Essayer la DB d'abord
        if self.db and self.db.pool:
            try:
                async with self.db.pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO cooldowns (user_id, cooldown_type, last_used)
                        VALUES ($1, 'bank_heist', $2)
                        ON CONFLICT (user_id, cooldown_type) 
                        DO UPDATE SET last_used = $2
                    """, user_id, now)
                    
                logger.debug(f"Cooldown heist persisté pour user {user_id}")
            except Exception as e:
                logger.error(f"Erreur sauvegarde cooldown: {e}")
        
        # Toujours garder en mémoire aussi
        self._memory_cooldowns[user_id] = now
    
    def check_daily_attempts(self, user_id: int) -> Tuple[int, bool]:
        """Vérifie les tentatives quotidiennes"""
        today = datetime.now(timezone.utc).date()
        
        if user_id not in self._daily_attempts:
            self._daily_attempts[user_id] = {'date': today, 'count': 0}
            return 0, True
        
        user_data = self._daily_attempts[user_id]
        if user_data['date'] != today:
            # Nouveau jour
            user_data['date'] = today
            user_data['count'] = 0
        
        attempts_today = user_data['count']
        can_attempt = attempts_today < HeistConfig.MAX_DAILY_HEISTS
        
        return attempts_today, can_attempt
    
    def increment_daily_attempts(self, user_id: int):
        """Incrémente le compteur quotidien"""
        today = datetime.now(timezone.utc).date()
        
        if user_id not in self._daily_attempts:
            self._daily_attempts[user_id] = {'date': today, 'count': 0}
        
        if self._daily_attempts[user_id]['date'] == today:
            self._daily_attempts[user_id]['count'] += 1
        else:
            self._daily_attempts[user_id] = {'date': today, 'count': 1}
    
    def calculate_heist_result(self, target_amount: int, user_balance: int) -> HeistResult:
        """Calcule le résultat du braquage avec logique équilibrée"""
        # Facteur de difficulté basé sur le montant
        difficulty_factor = min(target_amount / 10000, 1.0)  # Plus c'est gros, plus c'est dur
        
        # Ajustement probabilité selon balance utilisateur
        balance_factor = min(user_balance / 50000, 1.2)  # Bonus léger si riche
        
        # Probabilité finale
        success_rate = HeistConfig.SUCCESS_BASE_RATE * balance_factor * (1 - difficulty_factor * 0.3)
        
        # Tirage au sort
        roll = random.randint(1, 100)
        
        if roll <= HeistConfig.CRITICAL_FAILURE_RATE:
            # Échec critique
            loss = int(target_amount * HeistConfig.CRITICAL_FAILURE_PENALTY_RATE)
            return HeistResult(
                success=False,
                critical=True,
                amount_won=0,
                amount_lost=loss,
                description="Échec critique ! Tu as été arrêté et as dû payer une forte amende !",
                multiplier=HeistConfig.CRITICAL_FAILURE_PENALTY_RATE
            )
        
        elif roll <= success_rate:
            # Succès normal ou critique
            if roll <= HeistConfig.CRITICAL_SUCCESS_RATE:
                # Succès critique
                win = int(target_amount * HeistConfig.CRITICAL_SUCCESS_MULTIPLIER)
                return HeistResult(
                    success=True,
                    critical=True,
                    amount_won=win,
                    amount_lost=0,
                    description="BRAQUAGE PARFAIT ! Tu as trouvé un coffre-fort secret !",
                    multiplier=HeistConfig.CRITICAL_SUCCESS_MULTIPLIER
                )
            else:
                # Succès normal
                win = int(target_amount * HeistConfig.SUCCESS_MULTIPLIER)
                return HeistResult(
                    success=True,
                    critical=False,
                    amount_won=win,
                    amount_lost=0,
                    description="Braquage réussi ! Tu t'es échappé avec le butin !",
                    multiplier=HeistConfig.SUCCESS_MULTIPLIER
                )
        
        else:
            # Échec normal
            loss = int(target_amount * HeistConfig.FAILURE_PENALTY_RATE)
            return HeistResult(
                success=False,
                critical=False,
                amount_won=0,
                amount_lost=loss,
                description="Braquage échoué ! Tu as été repéré et as perdu de l'argent en fuyant.",
                multiplier=HeistConfig.FAILURE_PENALTY_RATE
            )
    
    async def execute_heist_transaction(self, user_id: int, result: HeistResult, 
                                      balance_before: int) -> Tuple[bool, int]:
        """Exécute la transaction du braquage de manière atomique"""
        if not self.db:
            return False, balance_before
        
        try:
            net_change = result.net_result
            new_balance = balance_before + net_change
            
            if new_balance < 0:
                # Ajuster pour éviter balance négative
                net_change = -balance_before
                new_balance = 0
                result.amount_lost = balance_before
                result.net_result = net_change
            
            # Mettre à jour le solde
            await self.db.update_balance(user_id, net_change)
            
            # Logger la transaction si disponible
            if hasattr(self.bot, 'transaction_logs'):
                description = f"Braquage {result.result_type} - {result.description[:50]}..."
                await self.bot.transaction_logs.log_transaction(
                    user_id=user_id,
                    transaction_type='bank_heist',
                    amount=net_change,
                    balance_before=balance_before,
                    balance_after=new_balance,
                    description=description
                )
            
            return True, new_balance
        except Exception as e:
            logger.error(f"Erreur transaction heist: {e}")
            return False, balance_before


class BankHeist(commands.Cog):
    """Système de braquage de banque avec cooldowns persistants"""
    
    def __init__(self, bot):
        self.bot = bot
        self.heist_manager = HeistManager(bot)
        
        # Stats de session
        self.stats = {
            'total_heists': 0,
            'successful_heists': 0,
            'total_won': 0,
            'total_lost': 0
        }
    
    async def cog_load(self):
        """Initialisation du cog"""
        await self.heist_manager.initialize()
        logger.info("Cog BankHeist initialisé avec persistance DB")
    
    def _format_cooldown_time(self, seconds: float) -> str:
        """Formate le temps de cooldown"""
        if seconds <= 0:
            return "Disponible"
        
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes}min"
        elif minutes > 0:
            return f"{minutes}min {secs}s"
        else:
            return f"{secs}s"
    
    def _create_heist_embed(self, user: discord.Member, result: HeistResult, 
                          balance_before: int, balance_after: int) -> discord.Embed:
        """Crée l'embed de résultat du braquage"""
        if result.success:
            if result.critical:
                title = "💎 BRAQUAGE LÉGENDAIRE !"
                color = Colors.GOLD
            else:
                title = "✅ Braquage réussi !"
                color = Colors.SUCCESS
        else:
            if result.critical:
                title = "💥 ÉCHEC CATASTROPHIQUE !"
                color = 0x8B0000  # Rouge foncé
            else:
                title = "❌ Braquage échoué"
                color = Colors.ERROR
        
        embed = discord.Embed(
            title=title,
            description=result.description,
            color=color
        )
        
        # Résultat financier
        if result.success:
            embed.add_field(
                name="💰 Butin",
                value=f"+{result.amount_won:,} PrissBucks",
                inline=True
            )
        else:
            embed.add_field(
                name="💸 Perte",
                value=f"-{result.amount_lost:,} PrissBucks",
                inline=True
            )
        
        embed.add_field(
            name="📊 Résultat net",
            value=f"{'+'if result.net_result >= 0 else ''}{result.net_result:,} PrissBucks",
            inline=True
        )
        
        embed.add_field(
            name="💳 Nouveau solde",
            value=f"{balance_after:,} PrissBucks",
            inline=True
        )
        
        # Multiplicateur si significatif
        if result.multiplier != 1.0:
            embed.add_field(
                name="🎲 Multiplicateur",
                value=f"x{result.multiplier}",
                inline=True
            )
        
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"Prochain braquage dans {HeistConfig.COOLDOWN_HOURS}h")
        
        return embed
    
    @commands.command(name='braquage', aliases=['heist', 'rob_bank'])
    async def heist_cmd(self, ctx, amount: int):
        """Tente de braquer une banque avec un montant spécifique"""
        await self._execute_heist(ctx, amount)
    
    @app_commands.command(name="braquage", description="Tente de braquer une banque (risque élevé, gains potentiels importants)")
    @app_commands.describe(amount="Montant visé pour le braquage (entre 100 et 50,000 PB)")
    async def heist_slash(self, interaction: discord.Interaction, amount: int):
        """/braquage <amount> - Braque une banque"""
        await interaction.response.defer()
        await self._execute_heist(interaction, amount, is_slash=True)
    
    async def _execute_heist(self, ctx_or_interaction, amount: int, is_slash=False):
        """Logique principale du braquage"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        
        # Validations de base
        if amount < HeistConfig.MIN_HEIST_AMOUNT or amount > HeistConfig.MAX_HEIST_AMOUNT:
            embed = create_error_embed(
                "Montant invalide",
                f"Le montant doit être entre {HeistConfig.MIN_HEIST_AMOUNT:,} et {HeistConfig.MAX_HEIST_AMOUNT:,} PrissBucks"
            )
            await send_func(embed=embed)
            return
        
        # Vérifier le cooldown
        cooldown_remaining = await self.heist_manager.check_heist_cooldown(user_id)
        if cooldown_remaining > 0:
            time_str = self._format_cooldown_time(cooldown_remaining)
            embed = discord.Embed(
                title="⏰ Cooldown actif",
                description=f"Tu dois attendre **{time_str}** avant ton prochain braquage",
                color=Colors.WARNING
            )
            embed.add_field(
                name="💡 Pourquoi ce cooldown ?",
                value="Les braquages sont des opérations complexes qui nécessitent de la préparation !",
                inline=False
            )
            await send_func(embed=embed)
            return
        
        # Vérifier les tentatives quotidiennes
        attempts_today, can_attempt = self.heist_manager.check_daily_attempts(user_id)
        if not can_attempt:
            embed = create_warning_embed(
                "Limite quotidienne atteinte",
                f"Tu as déjà effectué {attempts_today}/{HeistConfig.MAX_DAILY_HEISTS} braquages aujourd'hui.\n"
                "Reviens demain pour de nouvelles tentatives !"
            )
            await send_func(embed=embed)
            return
        
        # Vérifier le solde minimum
        user_balance = await self.bot.database.get_balance(user_id)
        if user_balance < HeistConfig.MIN_BALANCE_REQUIRED:
            embed = create_error_embed(
                "Solde insuffisant",
                f"Tu as besoin d'au moins {HeistConfig.MIN_BALANCE_REQUIRED:,} PrissBucks pour tenter un braquage.\n"
                f"Solde actuel: {user_balance:,} PrissBucks"
            )
            await send_func(embed=embed)
            return
        
        try:
            # Calculer le résultat du braquage
            result = self.heist_manager.calculate_heist_result(amount, user_balance)
            
            # Vérifier si l'utilisateur peut payer les pertes
            if not result.success and result.amount_lost > user_balance:
                result.amount_lost = user_balance
                result.net_result = -result.amount_lost
            
            # Exécuter la transaction
            success, new_balance = await self.heist_manager.execute_heist_transaction(
                user_id, result, user_balance
            )
            
            if not success:
                embed = create_error_embed("Erreur", "Erreur lors de l'exécution du braquage")
                await send_func(embed=embed)
                return
            
            # Mettre à jour les compteurs
            await self.heist_manager.set_heist_cooldown(user_id)
            self.heist_manager.increment_daily_attempts(user_id)
            
            # Statistiques
            self.stats['total_heists'] += 1
            if result.success:
                self.stats['successful_heists'] += 1
                self.stats['total_won'] += result.amount_won
            else:
                self.stats['total_lost'] += result.amount_lost
            
            # Afficher le résultat
            embed = self._create_heist_embed(user, result, user_balance, new_balance)
            await send_func(embed=embed)
            
            # Log pour debug
            logger.info(f"Braquage: {user} - {result.result_type} - Net: {result.net_result:,} PB")
            
        except Exception as e:
            logger.error(f"Erreur critique braquage {user_id}: {e}")
            embed = create_error_embed("Erreur", "Une erreur inattendue s'est produite")
            await send_func(embed=embed)
    
    @commands.command(name='heist_info', aliases=['braquage_info'])
    async def heist_info_cmd(self, ctx):
        """Affiche les informations sur le système de braquage"""
        user_id = ctx.author.id
        
        # Informations de cooldown
        cooldown_remaining = await self.heist_manager.check_heist_cooldown(user_id)
        attempts_today, can_attempt = self.heist_manager.check_daily_attempts(user_id)
        
        cooldown_status = self._format_cooldown_time(cooldown_remaining) if cooldown_remaining > 0 else "✅ Disponible"
        
        embed = discord.Embed(
            title="🏦 Système de Braquage de Banque",
            description="Tente de braquer des banques pour des gains importants... mais attention aux risques !",
            color=Colors.INFO
        )
        
        # Règles de base
        embed.add_field(
            name="💰 Montants",
            value=f"**Min:** {HeistConfig.MIN_HEIST_AMOUNT:,} PB\n"
                  f"**Max:** {HeistConfig.MAX_HEIST_AMOUNT:,} PB\n"
                  f"**Solde requis:** {HeistConfig.MIN_BALANCE_REQUIRED:,} PB",
            inline=True
        )
        
        # Probabilités
        embed.add_field(
            name="🎲 Probabilités",
            value=f"**Succès:** ~{HeistConfig.SUCCESS_BASE_RATE}%\n"
                  f"**Critique:** {HeistConfig.CRITICAL_SUCCESS_RATE}% / {HeistConfig.CRITICAL_FAILURE_RATE}%\n"
                  f"**Multiplicateur:** x{HeistConfig.SUCCESS_MULTIPLIER} à x{HeistConfig.CRITICAL_SUCCESS_MULTIPLIER}",
            inline=True
        )
        
        # Limites
        embed.add_field(
            name="⏱️ Limites",
            value=f"**Cooldown:** {HeistConfig.COOLDOWN_HOURS}h\n"
                  f"**Par jour:** {HeistConfig.MAX_DAILY_HEISTS} tentatives\n"
                  f"**Aujourd'hui:** {attempts_today}/{HeistConfig.MAX_DAILY_HEISTS}",
            inline=True
        )
        
        # Ton statut
        embed.add_field(
            name="📊 Ton statut",
            value=f"**Cooldown:** {cooldown_status}\n"
                  f"**Tentatives:** {attempts_today}/{HeistConfig.MAX_DAILY_HEISTS}\n"
                  f"**Peut braquer:** {'✅ Oui' if can_attempt and cooldown_remaining <= 0 else '❌ Non'}",
            inline=True
        )
        
        # Conseils stratégiques
        embed.add_field(
            name="💡 Stratégie",
            value="• Plus le montant visé est élevé, plus le risque augmente\n"
                  "• Garde toujours une réserve pour absorber les pertes\n"
                  "• Les succès critiques peuvent tripler tes gains\n"
                  "• Évite de braquer si tu n'as que le minimum requis",
            inline=False
        )
        
        # Statistiques si disponibles
        if self.stats['total_heists'] > 0:
            success_rate = (self.stats['successful_heists'] / self.stats['total_heists']) * 100
            embed.add_field(
                name="📈 Statistiques serveur",
                value=f"**Braquages:** {self.stats['total_heists']}\n"
                      f"**Taux succès:** {success_rate:.1f}%\n"
                      f"**Gains/Pertes:** +{self.stats['total_won']:,} / -{self.stats['total_lost']:,} PB",
                inline=False
            )
        
        embed.set_footer(text=f"Utilise '{PREFIX}braquage <montant>' ou '/braquage <montant>' pour tenter ta chance !")
        await ctx.send(embed=embed)
    
    @commands.command(name='heist_stats')
    @commands.has_permissions(administrator=True)
    async def heist_stats_admin(self, ctx):
        """[ADMIN] Statistiques détaillées du système de braquage"""
        embed = discord.Embed(
            title="📊 Statistiques Admin - Braquages",
            color=Colors.INFO
        )
        
        # Stats globales
        embed.add_field(
            name="🎯 Statistiques globales",
            value=f"**Total braquages:** {self.stats['total_heists']}\n"
                  f"**Succès:** {self.stats['successful_heists']}\n"
                  f"**Échecs:** {self.stats['total_heists'] - self.stats['successful_heists']}",
            inline=True
        )
        
        if self.stats['total_heists'] > 0:
            success_rate = (self.stats['successful_heists'] / self.stats['total_heists']) * 100
            embed.add_field(
                name="📈 Taux de succès",
                value=f"**{success_rate:.1f}%** de réussite\n"
                      f"*(Théorique: ~{HeistConfig.SUCCESS_BASE_RATE}%)*",
                inline=True
            )
        
        # Impact financier
        net_impact = self.stats['total_won'] - self.stats['total_lost']
        embed.add_field(
            name="💰 Impact économique",
            value=f"**Gains totaux:** +{self.stats['total_won']:,} PB\n"
                  f"**Pertes totales:** -{self.stats['total_lost']:,} PB\n"
                  f"**Impact net:** {'+' if net_impact >= 0 else ''}{net_impact:,} PB",
            inline=True
        )
        
        # Configuration actuelle
        embed.add_field(
            name="⚙️ Configuration",
            value=f"**Cooldown:** {HeistConfig.COOLDOWN_HOURS}h\n"
                  f"**Limite jour:** {HeistConfig.MAX_DAILY_HEISTS}\n"
                  f"**Montant:** {HeistConfig.MIN_HEIST_AMOUNT:,}-{HeistConfig.MAX_HEIST_AMOUNT:,} PB\n"
                  f"**Solde min:** {HeistConfig.MIN_BALANCE_REQUIRED:,} PB",
            inline=True
        )
        
        # Cooldowns actifs
        active_cooldowns = len([
            user_id for user_id, timestamp in self.heist_manager._memory_cooldowns.items()
            if (datetime.now(timezone.utc) - timestamp).total_seconds() < HeistConfig.COOLDOWN_SECONDS
        ])
        
        embed.add_field(
            name="⏰ État système",
            value=f"**Cooldowns actifs:** {active_cooldowns}\n"
                  f"**Cache mémoire:** {len(self.heist_manager._memory_cooldowns)} entrées\n"
                  f"**Tentatives jour:** {len(self.heist_manager._daily_attempts)} utilisateurs",
            inline=True
        )
        
        embed.set_footer(text="Système de braquage - Configuration équilibrée pour éviter l'inflation")
        await ctx.send(embed=embed)
    
    @commands.command(name='reset_heist_cooldown')
    @commands.is_owner()
    async def reset_heist_cooldown_cmd(self, ctx, user: discord.Member):
        """[OWNER] Remet à zéro le cooldown de braquage d'un utilisateur"""
        try:
            user_id = user.id
            
            # Supprimer de la DB si possible
            if self.heist_manager.db and self.heist_manager.db.pool:
                async with self.heist_manager.db.pool.acquire() as conn:
                    await conn.execute("""
                        DELETE FROM cooldowns 
                        WHERE user_id = $1 AND cooldown_type = 'bank_heist'
                    """, user_id)
            
            # Supprimer du cache mémoire
            if user_id in self.heist_manager._memory_cooldowns:
                del self.heist_manager._memory_cooldowns[user_id]
            
            # Supprimer les tentatives quotidiennes
            if user_id in self.heist_manager._daily_attempts:
                del self.heist_manager._daily_attempts[user_id]
            
            embed = create_success_embed(
                "Cooldown réinitialisé",
                f"Le cooldown de braquage de {user.display_name} a été remis à zéro."
            )
            await ctx.send(embed=embed)
            
            logger.info(f"OWNER {ctx.author} a réinitialisé le cooldown heist de {user}")
            
        except Exception as e:
            logger.error(f"Erreur reset cooldown heist: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la réinitialisation du cooldown.")
            await ctx.send(embed=embed)
    
    @commands.command(name='heist_help')
    async def heist_help_cmd(self, ctx):
        """Guide complet du système de braquage"""
        embed = discord.Embed(
            title="🏦 Guide Complet - Braquage de Banque",
            description="**Système à haut risque et haute récompense**",
            color=Colors.WARNING
        )
        
        embed.add_field(
            name="🎯 Principe de base",
            value="Le braquage est un mini-jeu où tu mise un montant pour tenter de le multiplier.\n"
                  "Plus tu vises haut, plus les gains potentiels sont importants, mais les risques aussi !",
            inline=False
        )
        
        embed.add_field(
            name="💰 Comment ça marche",
            value=f"1. Choisis un montant entre {HeistConfig.MIN_HEIST_AMOUNT:,} et {HeistConfig.MAX_HEIST_AMOUNT:,} PB\n"
                  f"2. Le système calcule tes chances selon le montant et ton solde\n"
                  f"3. Tu peux gagner x{HeistConfig.SUCCESS_MULTIPLIER} à x{HeistConfig.CRITICAL_SUCCESS_MULTIPLIER} ton mise, ou perdre une partie de tes PB\n"
                  f"4. Cooldown de {HeistConfig.COOLDOWN_HOURS}h entre chaque tentative",
            inline=False
        )
        
        embed.add_field(
            name="🎲 Résultats possibles",
            value=f"**🟢 Succès normal:** +{int(HeistConfig.SUCCESS_MULTIPLIER*100):,}% de gains\n"
                  f"**💎 Succès critique:** +{int(HeistConfig.CRITICAL_SUCCESS_MULTIPLIER*100):,}% de gains ({HeistConfig.CRITICAL_SUCCESS_RATE}% chance)\n"
                  f"**🟡 Échec normal:** -{int(HeistConfig.FAILURE_PENALTY_RATE*100):,}% de perte\n"
                  f"**🔴 Échec critique:** -{int(HeistConfig.CRITICAL_FAILURE_PENALTY_RATE*100):,}% de perte ({HeistConfig.CRITICAL_FAILURE_RATE}% chance)",
            inline=False
        )
        
        embed.add_field(
            name="⚖️ Facteurs d'influence",
            value="• **Montant visé:** Plus c'est gros, plus c'est risqué\n"
                  "• **Ton solde:** Un solde élevé améliore légèrement tes chances\n"
                  "• **Chance pure:** Une part de hasard reste toujours présente",
            inline=False
        )
        
        embed.add_field(
            name="🛡️ Sécurités",
            value=f"• **Solde minimum:** {HeistConfig.MIN_BALANCE_REQUIRED:,} PB requis pour jouer\n"
                  f"• **Limite quotidienne:** {HeistConfig.MAX_DAILY_HEISTS} tentatives par jour\n"
                  f"• **Protection:** Impossible de perdre plus que ton solde\n"
                  f"• **Cooldown persistant:** Même si le bot redémarre",
            inline=False
        )
        
        embed.add_field(
            name="💡 Stratégies recommandées",
            value="🔸 **Débutant:** Commence par des petits montants (500-1000 PB)\n"
                  f"🔸 **Intermédiaire:** Vise 10-20% de ton solde total\n"
                  f"🔸 **Expert:** Garde toujours 3x le montant visé en réserve\n"
                  f"🔸 **Prudent:** Arrête-toi après 2-3 échecs consécutifs",
            inline=False
        )
        
        embed.add_field(
            name="🔧 Commandes disponibles",
            value=f"`{PREFIX}braquage <montant>` ou `/braquage <montant>` - Tenter un braquage\n"
                  f"`{PREFIX}heist_info` - Voir tes statistiques et limites\n"
                  f"`{PREFIX}heist_help` - Ce guide complet",
            inline=False
        )
        
        embed.set_footer(text="⚠️ Attention: Le braquage est un jeu de hasard. Ne mise que ce que tu peux te permettre de perdre !")
        await ctx.send(embed=embed)
    
    async def cog_unload(self):
        """Nettoyage lors du déchargement"""
        # Pas de tâches à arrêter pour l'instant
        logger.info("BankHeist cog déchargé")


async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(BankHeist(bot))
