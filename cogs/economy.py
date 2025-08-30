import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import random
import logging
import time
import asyncio
from typing import Optional, Tuple, Dict, Any
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum

from config import (
    DAILY_MIN, DAILY_MAX, DAILY_BONUS_CHANCE, DAILY_BONUS_MIN, DAILY_BONUS_MAX,
    DAILY_COOLDOWN, TRANSFER_COOLDOWN, TRANSFER_MAX, TRANSFER_MIN, 
    TRANSFER_TAX_RATE, OWNER_ID, Colors, Emojis
)

logger = logging.getLogger(__name__)

class TransactionType(Enum):
    """Types de transactions pour la traçabilité"""
    DAILY = "daily"
    TRANSFER_SENT = "transfer_sent"
    TRANSFER_RECEIVED = "transfer_received"
    ADMIN_ADD = "admin_add"

@dataclass
class TransactionResult:
    """Résultat d'une transaction atomique"""
    success: bool
    balance_before: int
    balance_after: int
    amount: int
    tax: int = 0
    error_message: str = ""

class ValidationError(Exception):
    """Exception pour les erreurs de validation"""
    pass

class TransactionError(Exception):
    """Exception pour les erreurs de transaction"""
    pass

class CooldownError(Exception):
    """Exception pour les erreurs de cooldown"""
    pass

class AtomicTransactionManager:
    """Gestionnaire de transactions atomiques avec rollback automatique"""
    
    def __init__(self, db):
        self.db = db
        self._locks: Dict[int, asyncio.Lock] = {}
    
    @asynccontextmanager
    async def transaction(self, user_ids: list):
        """Context manager pour transactions atomiques multi-utilisateurs"""
        if not self.db.pool:
            raise TransactionError("Database not available")
        
        # Trier les IDs pour éviter les deadlocks
        sorted_ids = sorted(set(user_ids))
        locks = [self._get_user_lock(uid) for uid in sorted_ids]
        
        # Acquérir tous les locks dans l'ordre
        acquired_locks = []
        try:
            for lock in locks:
                await lock.acquire()
                acquired_locks.append(lock)
            
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    yield conn
                    
        except Exception as e:
            logger.error(f"Transaction failed: {e}")
            raise TransactionError(f"Transaction failed: {str(e)}")
        finally:
            # Libérer tous les locks
            for lock in reversed(acquired_locks):
                lock.release()
    
    def _get_user_lock(self, user_id: int) -> asyncio.Lock:
        """Récupère ou crée un lock pour un utilisateur"""
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]
    
    async def cleanup_locks(self):
        """Nettoie les locks inutilisés"""
        current_time = time.time()
        if not hasattr(self, '_last_cleanup'):
            self._last_cleanup = current_time
            return
        
        if current_time - self._last_cleanup > 3600:  # Cleanup toutes les heures
            locked_users = [uid for uid, lock in self._locks.items() if lock.locked()]
            self._locks = {uid: lock for uid, lock in self._locks.items() if uid in locked_users}
            self._last_cleanup = current_time

class CooldownManager:
    """Gestionnaire de cooldowns persistant et optimisé"""
    
    def __init__(self, db):
        self.db = db
        self._cache: Dict[Tuple[int, str], float] = {}
        self._cache_ttl = 60  # TTL du cache en secondes
    
    async def check_cooldown(self, user_id: int, command_type: str, duration: int) -> float:
        """Vérifie le cooldown d'un utilisateur pour une commande"""
        cache_key = (user_id, command_type)
        now = time.time()
        
        # Vérifier le cache d'abord
        if cache_key in self._cache:
            last_use = self._cache[cache_key]
            elapsed = now - last_use
            if elapsed < duration:
                return duration - elapsed
            elif elapsed < self._cache_ttl:
                return 0
        
        # Vérifier en base de données
        try:
            if self.db.pool:
                async with self.db.pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT last_used FROM cooldowns WHERE user_id = $1 AND command_type = $2",
                        user_id, command_type
                    )
                    
                    if row:
                        last_used = row['last_used'].timestamp()
                        elapsed = now - last_used
                        self._cache[cache_key] = last_used
                        
                        if elapsed < duration:
                            return duration - elapsed
        except Exception as e:
            logger.error(f"Cooldown check error for {user_id}/{command_type}: {e}")
        
        return 0
    
    async def set_cooldown(self, user_id: int, command_type: str):
        """Active un cooldown pour un utilisateur"""
        now = time.time()
        cache_key = (user_id, command_type)
        self._cache[cache_key] = now
        
        if not self.db.pool:
            return
        
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO cooldowns (user_id, command_type, last_used)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id, command_type)
                    DO UPDATE SET last_used = NOW()
                """, user_id, command_type)
        except Exception as e:
            logger.error(f"Cooldown set error for {user_id}/{command_type}: {e}")

class InputValidator:
    """Validateur centralisé pour toutes les entrées utilisateur"""
    
    @staticmethod
    def validate_amount(amount: int, min_val: int = 1, max_val: int = 10**9) -> int:
        """Valide un montant"""
        if not isinstance(amount, int):
            raise ValidationError("Amount must be an integer")
        if amount < min_val:
            raise ValidationError(f"Amount must be at least {min_val}")
        if amount > max_val:
            raise ValidationError(f"Amount cannot exceed {max_val:,}")
        return amount
    
    @staticmethod
    def validate_user(user: discord.Member, exclude_self: Optional[discord.Member] = None) -> discord.Member:
        """Valide un utilisateur"""
        if user.bot:
            raise ValidationError("Cannot interact with bots")
        if exclude_self and user.id == exclude_self.id:
            raise ValidationError("Cannot interact with yourself")
        return user

class EconomyService:
    """Service métier pour l'économie avec logique centralisée"""
    
    def __init__(self, db, transaction_manager: AtomicTransactionManager):
        self.db = db
        self.tx_manager = transaction_manager
    
    async def get_balance_safe(self, user_id: int) -> int:
        """Récupère le solde de manière sécurisée"""
        try:
            balance = await self.db.get_balance(user_id)
            return max(0, balance)  # Garantit un solde positif
        except Exception as e:
            logger.error(f"Failed to get balance for {user_id}: {e}")
            return 0
    
    async def transfer_with_validation(self, from_user: int, to_user: int, amount: int) -> TransactionResult:
        """Effectue un transfert avec validation complète et taxes"""
        try:
            # Calculs préalables
            tax_amount = int(amount * TRANSFER_TAX_RATE)
            total_cost = amount + tax_amount
            net_received = amount - tax_amount
            
            async with self.tx_manager.transaction([from_user, to_user, OWNER_ID]):
                # Récupérer les soldes actuels
                sender_balance = await self.get_balance_safe(from_user)
                receiver_balance = await self.get_balance_safe(to_user)
                
                # Vérifier la solvabilité
                if sender_balance < total_cost:
                    return TransactionResult(
                        success=False,
                        balance_before=sender_balance,
                        balance_after=sender_balance,
                        amount=0,
                        error_message=f"Insufficient funds. Need {total_cost:,}, have {sender_balance:,}"
                    )
                
                # Effectuer les transferts atomiques
                await self.db.update_balance(from_user, -total_cost)
                await self.db.update_balance(to_user, net_received)
                
                if tax_amount > 0 and OWNER_ID:
                    await self.db.update_balance(OWNER_ID, tax_amount)
                
                return TransactionResult(
                    success=True,
                    balance_before=sender_balance,
                    balance_after=sender_balance - total_cost,
                    amount=amount,
                    tax=tax_amount
                )
                
        except Exception as e:
            logger.error(f"Transfer failed {from_user} -> {to_user}: {e}")
            return TransactionResult(
                success=False,
                balance_before=0,
                balance_after=0,
                amount=0,
                error_message="Transaction failed due to technical error"
            )
    
    async def process_daily_reward(self, user_id: int) -> TransactionResult:
        """Traite une récompense daily avec anti-triche"""
        try:
            # Calcul sécurisé des récompenses
            base_reward = random.randint(DAILY_MIN, DAILY_MAX)
            bonus = 0
            
            if random.randint(1, 100) <= DAILY_BONUS_CHANCE:
                bonus = random.randint(DAILY_BONUS_MIN, DAILY_BONUS_MAX)
            
            total_reward = base_reward + bonus
            
            async with self.tx_manager.transaction([user_id]):
                balance_before = await self.get_balance_safe(user_id)
                await self.db.update_balance(user_id, total_reward)
                
                # Enregistrer la dernière récompense daily
                if hasattr(self.db, 'set_last_daily'):
                    await self.db.set_last_daily(user_id, datetime.now(timezone.utc))
                
                return TransactionResult(
                    success=True,
                    balance_before=balance_before,
                    balance_after=balance_before + total_reward,
                    amount=total_reward
                )
                
        except Exception as e:
            logger.error(f"Daily reward failed for {user_id}: {e}")
            return TransactionResult(
                success=False,
                balance_before=0,
                balance_after=0,
                amount=0,
                error_message="Failed to process daily reward"
            )

class MinimalEmbedBuilder:
    """Constructeur d'embeds minimalistes et informatifs"""
    
    @staticmethod
    def balance(user: discord.Member, balance: int) -> discord.Embed:
        """Embed balance ultra simple"""
        color = Colors.SUCCESS if balance > 0 else Colors.WARNING
        return discord.Embed(
            description=f"**{user.display_name}**: {balance:,} PrissBucks",
            color=color
        )
    
    @staticmethod
    def daily_success(user: discord.Member, amount: int) -> discord.Embed:
        """Embed daily réussi"""
        return discord.Embed(
            description=f"**Daily**: +{amount:,} PB",
            color=Colors.SUCCESS
        )
    
    @staticmethod
    def transfer_success(amount: int, tax: int, new_balance: int) -> discord.Embed:
        """Embed transfert réussi"""
        if tax > 0:
            desc = f"**Sent**: {amount:,} PB (tax: {tax:,})\n**Balance**: {new_balance:,} PB"
        else:
            desc = f"**Sent**: {amount:,} PB\n**Balance**: {new_balance:,} PB"
        return discord.Embed(description=desc, color=Colors.SUCCESS)
    
    @staticmethod
    def error(message: str) -> discord.Embed:
        """Embed erreur simple"""
        return discord.Embed(description=f"❌ {message}", color=Colors.ERROR)
    
    @staticmethod
    def cooldown(command: str, seconds: float) -> discord.Embed:
        """Embed cooldown"""
        if seconds >= 3600:
            time_str = f"{seconds/3600:.1f}h"
        elif seconds >= 60:
            time_str = f"{seconds/60:.1f}m"
        else:
            time_str = f"{seconds:.1f}s"
        return discord.Embed(
            description=f"⏰ **{command}** ready in {time_str}",
            color=Colors.WARNING
        )

class Economy(commands.Cog):
    """Système économique optimisé et sécurisé"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        self.tx_manager = None
        self.cooldown_manager = None
        self.economy_service = None
        self.validator = InputValidator()
        self.embed_builder = MinimalEmbedBuilder()
        
        # Métriques de performance
        self._command_metrics = {
            'balance': 0, 'daily': 0, 'give': 0, 'addpb': 0,
            'errors': 0, 'avg_response_time': 0
        }
    
    async def cog_load(self):
        """Initialisation sécurisée du cog"""
        self.db = self.bot.database
        self.tx_manager = AtomicTransactionManager(self.db)
        self.cooldown_manager = CooldownManager(self.db)
        self.economy_service = EconomyService(self.db, self.tx_manager)
        
        # Vérification de l'intégrité au démarrage
        await self._verify_database_integrity()
        
        logger.info("✅ Economy cog loaded - Secure & Optimized")
    
    async def _verify_database_integrity(self):
        """Vérifie l'intégrité de la base de données au démarrage"""
        try:
            if self.db.pool:
                async with self.db.pool.acquire() as conn:
                    # Vérifier les soldes négatifs
                    negative_balances = await conn.fetchval(
                        "SELECT COUNT(*) FROM users WHERE balance < 0"
                    )
                    if negative_balances > 0:
                        logger.warning(f"Found {negative_balances} users with negative balance")
                        
                    # Créer la table cooldowns si nécessaire
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS cooldowns (
                            user_id BIGINT NOT NULL,
                            command_type VARCHAR(50) NOT NULL,
                            last_used TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                            PRIMARY KEY (user_id, command_type)
                        )
                    """)
                    
        except Exception as e:
            logger.error(f"Database integrity check failed: {e}")
    
    async def _log_transaction_safe(self, user_id: int, tx_type: TransactionType, result: TransactionResult):
        """Log les transactions de manière sécurisée"""
        try:
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_transaction(
                    user_id=user_id,
                    transaction_type=tx_type.value,
                    amount=result.amount if result.success else 0,
                    balance_before=result.balance_before,
                    balance_after=result.balance_after,
                    description=f"Economy - {tx_type.value}"
                )
        except Exception as e:
            logger.error(f"Transaction logging failed: {e}")
    
    async def _execute_with_metrics(self, command_name: str, func, *args, **kwargs):
        """Exécute une commande avec métriques de performance"""
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            self._command_metrics[command_name] = self._command_metrics.get(command_name, 0) + 1
            return result
        except Exception as e:
            self._command_metrics['errors'] = self._command_metrics.get('errors', 0) + 1
            raise e
        finally:
            execution_time = time.time() - start_time
            self._command_metrics['avg_response_time'] = (
                self._command_metrics.get('avg_response_time', 0) * 0.9 + execution_time * 0.1
            )
            
            # Cleanup périodique
            if sum(self._command_metrics.values()) % 100 == 0:
                await self.tx_manager.cleanup_locks()

    # ==================== COMMANDES PRINCIPALES ====================

    @commands.command(name='balance', aliases=['bal'])
    async def balance_cmd(self, ctx, member: discord.Member = None):
        """Affiche le solde"""
        await self._execute_with_metrics('balance', self._balance_logic, ctx, member)
    
    @app_commands.command(name="balance", description="Affiche le solde")
    async def balance_slash(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        await self._execute_with_metrics('balance', self._balance_logic, interaction, user, True)
    
    async def _balance_logic(self, ctx_or_interaction, member=None, is_slash=False):
        """Logique centralisée pour balance"""
        target = member or (ctx_or_interaction.user if is_slash else ctx_or_interaction.author)
        send_func = (ctx_or_interaction.followup.send if is_slash else ctx_or_interaction.send)
        
        try:
            balance = await self.economy_service.get_balance_safe(target.id)
            embed = self.embed_builder.balance(target, balance)
            await send_func(embed=embed)
        except Exception as e:
            logger.error(f"Balance command error: {e}")
            await send_func(embed=self.embed_builder.error("Failed to retrieve balance"))

    @commands.command(name='daily')
    async def daily_cmd(self, ctx):
        """Récupère la récompense quotidienne"""
        await self._execute_with_metrics('daily', self._daily_logic, ctx)
    
    @app_commands.command(name="daily", description="Récupère ta récompense quotidienne")
    async def daily_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._execute_with_metrics('daily', self._daily_logic, interaction, True)
    
    async def _daily_logic(self, ctx_or_interaction, is_slash=False):
        """Logique centralisée pour daily"""
        user = ctx_or_interaction.user if is_slash else ctx_or_interaction.author
        send_func = (ctx_or_interaction.followup.send if is_slash else ctx_or_interaction.send)
        
        try:
            # Vérifier le cooldown
            cooldown_remaining = await self.cooldown_manager.check_cooldown(
                user.id, "daily", DAILY_COOLDOWN
            )
            
            if cooldown_remaining > 0:
                embed = self.embed_builder.cooldown("daily", cooldown_remaining)
                await send_func(embed=embed)
                return
            
            # Traiter la récompense
            result = await self.economy_service.process_daily_reward(user.id)
            
            if result.success:
                await self.cooldown_manager.set_cooldown(user.id, "daily")
                await self._log_transaction_safe(user.id, TransactionType.DAILY, result)
                embed = self.embed_builder.daily_success(user, result.amount)
            else:
                embed = self.embed_builder.error(result.error_message)
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Daily command error: {e}")
            await send_func(embed=self.embed_builder.error("Daily reward failed"))

    @commands.command(name='give', aliases=['transfer'])
    async def give_cmd(self, ctx, member: discord.Member, amount: int):
        """Transfère des PrissBucks"""
        await self._execute_with_metrics('give', self._give_logic, ctx, member, amount)
    
    @app_commands.command(name="give", description="Transfère des PrissBucks à un utilisateur")
    async def give_slash(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        await interaction.response.defer()
        await self._execute_with_metrics('give', self._give_logic, interaction, user, amount, True)
    
    async def _give_logic(self, ctx_or_interaction, member, amount, is_slash=False):
        """Logique centralisée pour give avec toutes les sécurités"""
        giver = ctx_or_interaction.user if is_slash else ctx_or_interaction.author
        send_func = (ctx_or_interaction.followup.send if is_slash else ctx_or_interaction.send)
        
        try:
            # Validations d'entrée
            self.validator.validate_amount(amount, TRANSFER_MIN, TRANSFER_MAX)
            self.validator.validate_user(member, exclude_self=giver)
            
            # Vérifier le cooldown
            cooldown_remaining = await self.cooldown_manager.check_cooldown(
                giver.id, "give", TRANSFER_COOLDOWN
            )
            
            if cooldown_remaining > 0:
                embed = self.embed_builder.cooldown("give", cooldown_remaining)
                await send_func(embed=embed)
                return
            
            # Effectuer le transfert atomique
            result = await self.economy_service.transfer_with_validation(
                giver.id, member.id, amount
            )
            
            if result.success:
                await self.cooldown_manager.set_cooldown(giver.id, "give")
                await self._log_transaction_safe(giver.id, TransactionType.TRANSFER_SENT, result)
                embed = self.embed_builder.transfer_success(amount, result.tax, result.balance_after)
            else:
                embed = self.embed_builder.error(result.error_message)
            
            await send_func(embed=embed)
            
        except ValidationError as e:
            await send_func(embed=self.embed_builder.error(str(e)))
        except Exception as e:
            logger.error(f"Give command error: {e}")
            await send_func(embed=self.embed_builder.error("Transfer failed"))

    @commands.command(name='addpb')
    @commands.has_permissions(administrator=True)
    async def addpb_cmd(self, ctx, member: discord.Member, amount: int):
        """[ADMIN] Ajoute des PrissBucks"""
        await self._execute_with_metrics('addpb', self._addpb_logic, ctx, member, amount)
    
    @app_commands.command(name="addpb", description="[ADMIN] Ajoute des PrissBucks")
    @app_commands.default_permissions(administrator=True)
    async def addpb_slash(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                embed=self.embed_builder.error("Admin only"), ephemeral=True
            )
            return
        await interaction.response.defer()
        await self._execute_with_metrics('addpb', self._addpb_logic, interaction, user, amount, True)
    
    async def _addpb_logic(self, ctx_or_interaction, member, amount, is_slash=False):
        """Logique centralisée pour addpb"""
        admin = ctx_or_interaction.user if is_slash else ctx_or_interaction.author
        send_func = (ctx_or_interaction.followup.send if is_slash else ctx_or_interaction.send)
        
        try:
            # Validation stricte pour les admins
            self.validator.validate_amount(amount, 1, 1000000)
            
            async with self.tx_manager.transaction([member.id]):
                balance_before = await self.economy_service.get_balance_safe(member.id)
                await self.db.update_balance(member.id, amount)
                balance_after = balance_before + amount
                
                result = TransactionResult(
                    success=True,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    amount=amount
                )
                
                await self._log_transaction_safe(member.id, TransactionType.ADMIN_ADD, result)
                
                embed = discord.Embed(
                    description=f"**Added**: {amount:,} PB to {member.display_name}",
                    color=Colors.SUCCESS
                )
                await send_func(embed=embed)
                
        except ValidationError as e:
            await send_func(embed=self.embed_builder.error(str(e)))
        except Exception as e:
            logger.error(f"AddPB command error: {e}")
            await send_func(embed=self.embed_builder.error("Admin operation failed"))

    # ==================== COMMANDES DE MONITORING ====================

    @commands.command(name='economy_stats')
    @commands.is_owner()
    async def economy_stats_cmd(self, ctx):
        """[OWNER] Statistiques du système économique"""
        try:
            embed = discord.Embed(title="Economy Stats", color=Colors.INFO)
            
            # Métriques de performance
            total_commands = sum(self._command_metrics.values()) - self._command_metrics.get('errors', 0)
            embed.add_field(
                name="Performance",
                value=f"Commands: {total_commands}\nErrors: {self._command_metrics.get('errors', 0)}\n"
                      f"Avg time: {self._command_metrics.get('avg_response_time', 0):.3f}s",
                inline=True
            )
            
            # État des locks
            active_locks = sum(1 for lock in self.tx_manager._locks.values() if lock.locked())
            embed.add_field(
                name="System",
                value=f"Active locks: {active_locks}\nTotal locks: {len(self.tx_manager._locks)}",
                inline=True
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Stats command error: {e}")
            await ctx.send(embed=self.embed_builder.error("Stats unavailable"))

    async def cog_unload(self):
        """Nettoyage lors du déchargement"""
        try:
            if hasattr(self, 'tx_manager'):
                await self.tx_manager.cleanup_locks()
            logger.info("Economy cog unloaded cleanly")
        except Exception as e:
            logger.error(f"Error during cog unload: {e}")

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Economy(bot))