import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional
import asyncio

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_warning_embed

logger = logging.getLogger(__name__)

class DebtLevel:
    """Niveaux de dette simplifiés"""
    GREEN = 0    # 0-2000 PB
    YELLOW = 1   # 2001-5000 PB  
    RED = 2      # 5000+ PB
    
    CONFIGS = {
        GREEN: {
            "name": "🟢 BON",
            "color": Colors.SUCCESS,
            "max_withdraw": 1000,
            "cooldown_multiplier": 1.0,
            "interest_rate": 0.0
        },
        YELLOW: {
            "name": "🟡 ATTENTION", 
            "color": Colors.WARNING,
            "max_withdraw": 500,
            "cooldown_multiplier": 1.5,
            "interest_rate": 0.002
        },
        RED: {
            "name": "🔴 LIMITE",
            "color": Colors.ERROR,
            "max_withdraw": 200,
            "cooldown_multiplier": 2.0,
            "interest_rate": 0.005
        }
    }
    
    THRESHOLDS = [2000, 5000]
    
    @classmethod
    def get_level_from_debt(cls, debt_amount: int) -> int:
        """Détermine le niveau selon le montant"""
        if debt_amount <= cls.THRESHOLDS[0]:
            return cls.GREEN
        elif debt_amount <= cls.THRESHOLDS[1]:
            return cls.YELLOW
        return cls.RED

class SimpleDebtManager:
    """Gestionnaire de dette ultra simplifié"""
    
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        
        # Cache simple en mémoire
        self._cache = {}
        self._cache_expiry = {}
        self.CACHE_DURATION = 300  # 5 minutes
    
    async def create_debt_table(self):
        """Crée une table simple"""
        if not self.db.pool:
            return
            
        async with self.db.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS simple_debts (
                    user_id BIGINT PRIMARY KEY,
                    total_debt BIGINT DEFAULT 0,
                    debt_level INTEGER DEFAULT 0,
                    last_payment TIMESTAMP WITH TIME ZONE,
                    grace_end TIMESTAMP WITH TIME ZONE DEFAULT NOW() + INTERVAL '7 days',
                    CHECK (total_debt >= 0 AND debt_level >= 0 AND debt_level <= 2)
                )
            ''')
            logger.info("Table simple_debts créée")
    
    async def get_debt_info(self, user_id: int, use_cache: bool = True) -> Dict:
        """Récupère les infos de dette avec cache simple"""
        # Cache check
        cache_key = f"debt_{user_id}"
        now = datetime.now(timezone.utc)
        
        if use_cache and cache_key in self._cache:
            if now < self._cache_expiry[cache_key]:
                return self._cache[cache_key].copy()
        
        if not self.db.pool:
            return self._default_debt_info()
            
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM simple_debts WHERE user_id = $1", user_id
                )
                
                if row:
                    debt_info = dict(row)
                else:
                    debt_info = self._default_debt_info()
                
                # Cache pour 5 minutes
                self._cache[cache_key] = debt_info
                self._cache_expiry[cache_key] = now + timedelta(seconds=self.CACHE_DURATION)
                
                return debt_info.copy()
                
        except Exception as e:
            logger.error(f"Erreur get_debt_info {user_id}: {e}")
            return self._default_debt_info()
    
    def _default_debt_info(self) -> Dict:
        """Infos par défaut"""
        now = datetime.now(timezone.utc)
        return {
            "total_debt": 0,
            "debt_level": 0,
            "last_payment": None,
            "grace_end": now + timedelta(days=7)
        }
    
    async def can_withdraw(self, user_id: int, amount: int) -> Tuple[bool, str, float]:
        """Vérifie si l'utilisateur peut retirer"""
        try:
            debt_info = await self.get_debt_info(user_id)
            debt_level = debt_info.get("debt_level", 0)
            grace_end = debt_info.get("grace_end")
            
            # Période de grâce active
            is_in_grace = False
            if grace_end and datetime.now(timezone.utc) < grace_end:
                is_in_grace = True
                debt_level = 0  # Traiter comme niveau vert
            
            level_config = DebtLevel.CONFIGS[debt_level]
            max_withdraw = level_config["max_withdraw"]
            cooldown_multiplier = level_config["cooldown_multiplier"]
            
            if amount > max_withdraw:
                grace_msg = "\n🎁 Période de grâce active" if is_in_grace else ""
                return False, f"{level_config['name']} - Limite: {max_withdraw:,} PB\n" + \
                             f"Demandé: {amount:,} PB\nDette: {debt_info['total_debt']:,} PB{grace_msg}", cooldown_multiplier
            
            return True, "Autorisé", cooldown_multiplier
            
        except Exception as e:
            logger.error(f"Erreur can_withdraw {user_id}: {e}")
            return False, "Erreur technique", 1.0
    
    async def create_debt(self, user_id: int, amount: int) -> bool:
        """Crée une dette simple"""
        if not self.db.pool or amount <= 0:
            return False
            
        try:
            async with self.db.pool.acquire() as conn:
                current_info = await self.get_debt_info(user_id, use_cache=False)
                new_total_debt = current_info["total_debt"] + amount
                new_debt_level = DebtLevel.get_level_from_debt(new_total_debt)
                
                await conn.execute("""
                    INSERT INTO simple_debts (user_id, total_debt, debt_level)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) DO UPDATE SET
                    total_debt = simple_debts.total_debt + EXCLUDED.total_debt,
                    debt_level = $3
                """, user_id, amount, new_debt_level)
                
                # Invalider le cache
                cache_key = f"debt_{user_id}"
                if cache_key in self._cache:
                    del self._cache[cache_key]
                    del self._cache_expiry[cache_key]
                
                logger.info(f"Dette simple créée: User {user_id} +{amount} PB")
                return True
                
        except Exception as e:
            logger.error(f"Erreur create_debt {user_id}: {e}")
            return False
    
    async def repay_debt(self, user_id: int, amount: int) -> Tuple[bool, int]:
        """Rembourse la dette de manière simple"""
        if not self.db.pool or amount <= 0:
            return False, 0
            
        try:
            current_info = await self.get_debt_info(user_id, use_cache=False)
            current_debt = current_info["total_debt"]
            
            if current_debt <= 0:
                return True, 0
            
            # Simple: montant exact remboursé
            actual_repayment = min(amount, current_debt)
            new_debt = current_debt - actual_repayment
            new_debt_level = DebtLevel.get_level_from_debt(new_debt)
            
            async with self.db.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE simple_debts SET
                    total_debt = $2,
                    debt_level = $3,
                    last_payment = NOW()
                    WHERE user_id = $1
                """, user_id, new_debt, new_debt_level)
                
                # Invalider le cache
                cache_key = f"debt_{user_id}"
                if cache_key in self._cache:
                    del self._cache[cache_key]
                    del self._cache_expiry[cache_key]
                
                logger.info(f"Dette remboursée: User {user_id} -{actual_repayment} PB")
                return True, actual_repayment
                
        except Exception as e:
            logger.error(f"Erreur repay_debt {user_id}: {e}")
            return False, 0
    
    async def get_debt_stats(self) -> Dict:
        """Stats simples"""
        if not self.db.pool:
            return {}
            
        try:
            async with self.db.pool.acquire() as conn:
                stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total_users,
                        COALESCE(SUM(total_debt), 0) as total_debt,
                        COUNT(CASE WHEN debt_level = 0 THEN 1 END) as green_users,
                        COUNT(CASE WHEN debt_level = 1 THEN 1 END) as yellow_users,
                        COUNT(CASE WHEN debt_level = 2 THEN 1 END) as red_users
                    FROM simple_debts
                    WHERE total_debt > 0
                """)
                
                return dict(stats) if stats else {}
                
        except Exception as e:
            logger.error(f"Erreur debt stats: {e}")
            return {}

class SimpleDebtSystem(commands.Cog):
    """Système de dette ultra simplifié"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        self.debt_manager = None
        
        # Auto-remboursement simple
        self.AUTO_REPAY_MESSAGE = 2  # 2 PB par message
    
    async def cog_load(self):
        """Initialisation simple"""
        self.db = self.bot.database
        self.debt_manager = SimpleDebtManager(self.bot, self.db)
        await self.debt_manager.create_debt_table()
        logger.info("Système de Dette Simplifié initialisé")
    
    # ==================== COMMANDES UTILISATEUR ====================
    
    @commands.command(name='debt')
    async def debt_cmd(self, ctx):
        """Affiche tes dettes"""
        await self._execute_debt_info(ctx)
    
    @app_commands.command(name="debt", description="Affiche tes dettes")
    async def debt_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._execute_debt_info(interaction, is_slash=True)
    
    async def _execute_debt_info(self, ctx_or_interaction, is_slash=False):
        """Affiche les infos de dette simplifiées"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        try:
            debt_info = await self.debt_manager.get_debt_info(user.id)
            debt_level = debt_info.get("debt_level", 0)
            total_debt = debt_info.get("total_debt", 0)
            
            level_config = DebtLevel.CONFIGS[debt_level]
            
            # Vérifier période de grâce
            grace_end = debt_info.get("grace_end")
            is_in_grace = False
            if grace_end and datetime.now(timezone.utc) < grace_end:
                is_in_grace = True
            
            embed = discord.Embed(
                title="💳 Profil de Dette",
                description=f"**Niveau:** {level_config['name']}\n**Dette:** {total_debt:,} PB",
                color=level_config['color']
            )
            
            if is_in_grace:
                embed.add_field(
                    name="🎁 Période de Grâce",
                    value=f"Active jusqu'au <t:{int(grace_end.timestamp())}:f>\nLimite temporaire: 1000 PB",
                    inline=False
                )
                personal_max = 1000
            else:
                personal_max = level_config['max_withdraw']
            
            embed.add_field(
                name="⚖️ Tes Limites",
                value=f"Retrait max: {personal_max:,} PB\nCooldown: ×{level_config['cooldown_multiplier']}",
                inline=True
            )
            
            embed.add_field(
                name="💡 Remboursement",
                value=f"Messages: {self.AUTO_REPAY_MESSAGE} PB/message\nManuel: `{PREFIX}paydebt <montant>`",
                inline=True
            )
            
            if total_debt == 0:
                embed.add_field(
                    name="✅ Aucune dette",
                    value="Tu peux emprunter librement !",
                    inline=False
                )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur debt info {user.id}: {e}")
            embed = create_error_embed("Erreur", "Impossible de récupérer tes infos de dette")
            await send_func(embed=embed)
    
    @commands.command(name='paydebt')
    async def pay_debt_cmd(self, ctx, amount: int):
        """Rembourse ta dette"""
        await self._execute_pay_debt(ctx, amount)
    
    @app_commands.command(name="paydebt", description="Rembourse ta dette")
    @app_commands.describe(amount="Montant à rembourser")
    async def pay_debt_slash(self, interaction: discord.Interaction, amount: int):
        await interaction.response.defer()
        await self._execute_pay_debt(interaction, amount, is_slash=True)
    
    async def _execute_pay_debt(self, ctx_or_interaction, amount, is_slash=False):
        """Logique de remboursement simplifiée"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        if amount <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif")
            await send_func(embed=embed)
            return
        
        try:
            # Vérifier le solde
            current_balance = await self.db.get_balance(user.id)
            if current_balance < amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Tu as {current_balance:,} PB mais veux rembourser {amount:,} PB"
                )
                await send_func(embed=embed)
                return
            
            # Vérifier s'il y a une dette
            debt_info = await self.debt_manager.get_debt_info(user.id)
            current_debt = debt_info.get("total_debt", 0)
            
            if current_debt <= 0:
                embed = create_warning_embed("Aucune dette", "Tu n'as aucune dette !")
                await send_func(embed=embed)
                return
            
            # Remboursement simple
            success, actual_repayment = await self.debt_manager.repay_debt(user.id, amount)
            
            if not success:
                embed = create_error_embed("Erreur", "Erreur lors du remboursement")
                await send_func(embed=embed)
                return
            
            # Débiter le compte
            await self.db.update_balance(user.id, -amount)
            new_balance = current_balance - amount
            new_debt = current_debt - actual_repayment
            
            # Log si disponible
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_transaction(
                    user_id=user.id,
                    transaction_type='debt_repayment',
                    amount=-amount,
                    balance_before=current_balance,
                    balance_after=new_balance,
                    description=f"Remboursement dette -{actual_repayment} PB"
                )
            
            # Confirmation simple
            embed = discord.Embed(
                title="✅ Remboursement réussi",
                description=f"Dette remboursée: **{actual_repayment:,} PB**\n"
                           f"Nouveau solde: **{new_balance:,} PB**\n"
                           f"Dette restante: **{new_debt:,} PB**",
                color=Colors.SUCCESS
            )
            
            if new_debt == 0:
                embed.add_field(
                    name="🎉 Plus de dette !",
                    value="Tu peux maintenant emprunter à nouveau !",
                    inline=False
                )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            await send_func(embed=embed)
            
            logger.info(f"Remboursement simple: {user} -{amount} PB → dette -{actual_repayment}")
            
        except Exception as e:
            logger.error(f"Erreur paydebt {user.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du remboursement")
            await send_func(embed=embed)
    
    # ==================== MÉTHODES D'INTÉGRATION ====================
    
    async def auto_repay_from_message(self, user_id: int) -> bool:
        """Remboursement automatique via messages"""
        try:
            debt_info = await self.debt_manager.get_debt_info(user_id)
            if debt_info.get("total_debt", 0) <= 0:
                return False
            
            success, actual_repayment = await self.debt_manager.repay_debt(
                user_id, self.AUTO_REPAY_MESSAGE
            )
            
            if success and actual_repayment > 0:
                logger.debug(f"Auto-remboursement message: User {user_id} -{actual_repayment} PB")
                return True
                
        except Exception as e:
            logger.error(f"Erreur auto_repay_from_message {user_id}: {e}")
        
        return False
    
    async def create_debt_for_withdrawal(self, user_id: int, amount: int) -> bool:
        """Création de dette pour retrait"""
        try:
            success = await self.debt_manager.create_debt(user_id, amount)
            return success
        except Exception as e:
            logger.error(f"Erreur create_debt_for_withdrawal {user_id}: {e}")
            return False
    
    async def check_withdrawal_authorization(self, user_id: int, amount: int) -> Tuple[bool, str, float]:
        """Vérification d'autorisation simplifiée"""
        return await self.debt_manager.can_withdraw(user_id, amount)
    
    # ==================== COMMANDES ADMIN ====================
    
    @commands.command(name='debtstats')
    @commands.has_permissions(administrator=True)
    async def debt_stats_cmd(self, ctx):
        """[ADMIN] Statistiques simples"""
        try:
            stats = await self.debt_manager.get_debt_stats()
            
            if not stats.get("total_users", 0):
                embed = create_warning_embed("Aucune donnée", "Pas d'utilisateurs avec des dettes")
                await ctx.send(embed=embed)
                return
            
            embed = discord.Embed(
                title="📊 Statistiques Dette (Simplifiées)",
                color=Colors.INFO
            )
            
            embed.add_field(
                name="💰 Finances",
                value=f"Total dette: **{stats['total_debt']:,}** PB\n"
                      f"Utilisateurs: **{stats['total_users']}**",
                inline=True
            )
            
            embed.add_field(
                name="📈 Répartition",
                value=f"🟢 Bon: **{stats.get('green_users', 0)}**\n"
                      f"🟡 Attention: **{stats.get('yellow_users', 0)}**\n"
                      f"🔴 Limite: **{stats.get('red_users', 0)}**",
                inline=True
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur debtstats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des stats")
            await ctx.send(embed=embed)
    
    @commands.command(name='resetdebt')
    @commands.is_owner()
    async def reset_debt_cmd(self, ctx, user: discord.Member):
        """[OWNER] Remet à zéro la dette"""
        try:
            if not self.db.pool:
                await ctx.send("Base de données non disponible")
                return
            
            async with self.db.pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM simple_debts WHERE user_id = $1", user.id
                )
            
            # Invalider le cache
            cache_key = f"debt_{user.id}"
            if cache_key in self.debt_manager._cache:
                del self.debt_manager._cache[cache_key]
                del self.debt_manager._cache_expiry[cache_key]
            
            embed = create_success_embed(
                "Dette réinitialisée",
                f"Dette de {user.display_name} remise à zéro"
            )
            await ctx.send(embed=embed)
            
            logger.info(f"OWNER {ctx.author} a réinitialisé la dette de {user}")
            
        except Exception as e:
            logger.error(f"Erreur resetdebt: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la réinitialisation")
            await ctx.send(embed=embed)
    
    @commands.command(name='debthelp')
    async def debt_help_cmd(self, ctx):
        """Guide du système de dette simplifié"""
        embed = discord.Embed(
            title="💳 Guide Dette Simplifié",
            description="**3 niveaux, règles simples**",
            color=Colors.INFO
        )
        
        # Niveaux simplifiés
        embed.add_field(
            name="📊 Niveaux de Dette",
            value=f"🟢 **BON** (0-2,000 PB)\n├ Retrait: 1,000 PB\n└ Cooldown: normal\n\n"
                  f"🟡 **ATTENTION** (2,001-5,000 PB)\n├ Retrait: 500 PB\n└ Cooldown: ×1.5\n\n"
                  f"🔴 **LIMITE** (5,000+ PB)\n├ Retrait: 200 PB\n└ Cooldown: ×2",
            inline=False
        )
        
        # Remboursement
        embed.add_field(
            name="💰 Remboursement",
            value=f"• **Messages:** {self.AUTO_REPAY_MESSAGE} PB par message actif\n"
                  f"• **Manuel:** `{PREFIX}paydebt <montant>`\n"
                  f"• **Période de grâce:** 7 jours pour nouveaux utilisateurs",
            inline=False
        )
        
        # Commandes
        embed.add_field(
            name="🔧 Commandes",
            value=f"• `{PREFIX}debt` - Voir tes dettes\n"
                  f"• `{PREFIX}paydebt <montant>` - Rembourser\n"
                  f"• `{PREFIX}debthelp` - Cette aide",
            inline=False
        )
        
        embed.set_footer(text="Système simplifié • Moins de complexité, même efficacité")
        await ctx.send(embed=embed)

async def setup(bot):
    """Charge le cog simplifié"""
    await bot.add_cog(SimpleDebtSystem(bot))