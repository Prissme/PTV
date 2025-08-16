import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional
import math

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_warning_embed

logger = logging.getLogger(__name__)

class DebtLevel:
    """Énumération des niveaux de dette avec configuration"""
    
    GREEN = 0    # 0-2000 PB
    YELLOW = 1   # 2001-5000 PB  
    ORANGE = 2   # 5001-10000 PB
    RED = 3      # 10000+ PB
    
    CONFIGS = {
        GREEN: {
            "name": "🟢 VERT",
            "color": Colors.SUCCESS,
            "max_withdraw": 1000,
            "cooldown_multiplier": 1.0,
            "description": "Accès normal à la banque publique"
        },
        YELLOW: {
            "name": "🟡 JAUNE", 
            "color": Colors.WARNING,
            "max_withdraw": 500,
            "cooldown_multiplier": 1.5,
            "description": "Limites légères - Remboursement recommandé"
        },
        ORANGE: {
            "name": "🟠 ORANGE",
            "color": 0xff6600,
            "max_withdraw": 200,
            "cooldown_multiplier": 2.0,
            "description": "Limites strictes - Remboursement urgent"
        },
        RED: {
            "name": "🔴 ROUGE",
            "color": Colors.ERROR,
            "max_withdraw": 0,
            "cooldown_multiplier": float('inf'),
            "description": "BLOQUÉ - Remboursement obligatoire"
        }
    }
    
    THRESHOLDS = [2000, 5000, 10000]  # Seuils pour chaque niveau
    
    @classmethod
    def get_level_from_debt(cls, debt_amount: int) -> int:
        """Détermine le niveau de dette selon le montant"""
        if debt_amount <= cls.THRESHOLDS[0]:
            return cls.GREEN
        elif debt_amount <= cls.THRESHOLDS[1]:
            return cls.YELLOW
        elif debt_amount <= cls.THRESHOLDS[2]:
            return cls.ORANGE
        else:
            return cls.RED

class DebtSettings:
    """Configuration du système de dette"""
    
    # Remboursement automatique
    AUTO_REPAY_MESSAGE = 1           # 1 PB par message
    AUTO_REPAY_CASINO_RATE = 0.20    # 20% des gains casino
    MANUAL_REPAY_BONUS = 0.10        # +10% bonus remboursement manuel
    
    # Système de streaks
    MIN_PAYMENT_STREAK = 50          # 50 PB minimum pour compter dans le streak
    STREAK_BONUS_RATE = 0.05         # +5% bonus par streak de 7 jours
    
    # Période de grâce pour nouveaux utilisateurs
    GRACE_PERIOD_HOURS = 168         # 7 jours en heures

class PublicBankDebt:
    """Gestionnaire principal du système de dette"""
    
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        
        # Cache pour éviter trop de requêtes DB
        self._debt_cache: Dict[int, Dict] = {}
        self._cache_expiry: Dict[int, datetime] = {}
        self.CACHE_DURATION = 300  # 5 minutes
    
    async def create_debt_table(self):
        """Crée la table des dettes si elle n'existe pas"""
        if not self.db.pool:
            return
            
        async with self.db.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS public_bank_debts (
                    user_id BIGINT PRIMARY KEY,
                    total_debt BIGINT DEFAULT 0,
                    total_withdrawn BIGINT DEFAULT 0,
                    total_repaid BIGINT DEFAULT 0,
                    debt_level INTEGER DEFAULT 0,
                    payment_streak INTEGER DEFAULT 0,
                    last_payment TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    grace_period_end TIMESTAMP WITH TIME ZONE DEFAULT NOW() + INTERVAL '7 days',
                    CHECK (total_debt >= 0),
                    CHECK (debt_level >= 0 AND debt_level <= 3)
                )
            ''')
            
            # Index pour optimiser les requêtes
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_debt_user_id ON public_bank_debts(user_id)
            ''')
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_debt_level ON public_bank_debts(debt_level)
            ''')
            
            logger.info("✅ Table public_bank_debts créée/vérifiée")
    
    def _is_cache_valid(self, user_id: int) -> bool:
        """Vérifie si le cache est encore valide"""
        if user_id not in self._cache_expiry:
            return False
        return datetime.now(timezone.utc) < self._cache_expiry[user_id]
    
    def _update_cache(self, user_id: int, debt_info: Dict):
        """Met à jour le cache pour un utilisateur"""
        self._debt_cache[user_id] = debt_info
        self._cache_expiry[user_id] = datetime.now(timezone.utc) + timedelta(seconds=self.CACHE_DURATION)
    
    async def get_debt_info(self, user_id: int, use_cache: bool = True) -> Dict:
        """Récupère les informations de dette d'un utilisateur"""
        # Vérifier le cache d'abord
        if use_cache and self._is_cache_valid(user_id):
            return self._debt_cache[user_id].copy()
        
        if not self.db.pool:
            default_info = {
                "total_debt": 0, "debt_level": 0, "payment_streak": 0,
                "total_withdrawn": 0, "total_repaid": 0, "grace_period_end": None
            }
            return default_info
            
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT total_debt, debt_level, payment_streak, total_withdrawn, 
                           total_repaid, last_payment, grace_period_end
                    FROM public_bank_debts 
                    WHERE user_id = $1
                """, user_id)
                
                if row:
                    debt_info = dict(row)
                else:
                    debt_info = {
                        "total_debt": 0, "debt_level": 0, "payment_streak": 0,
                        "total_withdrawn": 0, "total_repaid": 0, "last_payment": None,
                        "grace_period_end": None
                    }
                
                # Mettre à jour le cache
                self._update_cache(user_id, debt_info)
                return debt_info.copy()
                
        except Exception as e:
            logger.error(f"Erreur get_debt_info {user_id}: {e}")
            return {"total_debt": 0, "debt_level": 0, "payment_streak": 0}
    
    async def create_debt(self, user_id: int, amount: int) -> bool:
        """Crée une dette lors d'un retrait de la banque publique"""
        if not self.db.pool or amount <= 0:
            return False
            
        try:
            async with self.db.pool.acquire() as conn:
                # Calculer le nouveau niveau de dette
                current_info = await self.get_debt_info(user_id, use_cache=False)
                new_total_debt = current_info["total_debt"] + amount
                new_debt_level = DebtLevel.get_level_from_debt(new_total_debt)
                
                await conn.execute("""
                    INSERT INTO public_bank_debts 
                    (user_id, total_debt, total_withdrawn, debt_level)
                    VALUES ($1, $2, $2, $3)
                    ON CONFLICT (user_id) DO UPDATE SET
                    total_debt = public_bank_debts.total_debt + EXCLUDED.total_debt,
                    total_withdrawn = public_bank_debts.total_withdrawn + EXCLUDED.total_withdrawn,
                    debt_level = $3
                """, user_id, amount, amount, new_debt_level)
                
                # Invalider le cache
                if user_id in self._debt_cache:
                    del self._debt_cache[user_id]
                    del self._cache_expiry[user_id]
                
                logger.info(f"Dette créée: User {user_id} +{amount} PB (total: {new_total_debt}, niveau: {new_debt_level})")
                return True
                
        except Exception as e:
            logger.error(f"Erreur create_debt {user_id}: {e}")
            return False
    
    async def repay_debt(self, user_id: int, amount: int, payment_type: str = "manual", 
                        apply_bonus: bool = False) -> Tuple[bool, int, Dict]:
        """Rembourse une partie de la dette"""
        if not self.db.pool or amount <= 0:
            return False, 0, {}
            
        try:
            current_info = await self.get_debt_info(user_id, use_cache=False)
            current_debt = current_info["total_debt"]
            
            if current_debt <= 0:
                return True, 0, {"message": "Aucune dette à rembourser"}
            
            # Calculer le remboursement effectif avec bonus
            actual_repayment = amount
            if apply_bonus:
                bonus_amount = int(amount * DebtSettings.MANUAL_REPAY_BONUS)
                actual_repayment += bonus_amount
            
            # Ne pas rembourser plus que la dette
            actual_repayment = min(actual_repayment, current_debt)
            new_debt = max(0, current_debt - actual_repayment)
            new_debt_level = DebtLevel.get_level_from_debt(new_debt)
            
            # Calculer le streak
            now = datetime.now(timezone.utc)
            last_payment = current_info.get("last_payment")
            new_streak = current_info.get("payment_streak", 0)
            
            if (last_payment and 
                now - last_payment <= timedelta(days=1) and 
                amount >= DebtSettings.MIN_PAYMENT_STREAK):
                new_streak += 1
            elif amount >= DebtSettings.MIN_PAYMENT_STREAK:
                new_streak = 1
            else:
                new_streak = 0
            
            async with self.db.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE public_bank_debts SET
                    total_debt = $2,
                    total_repaid = total_repaid + $3,
                    debt_level = $4,
                    payment_streak = $5,
                    last_payment = $6
                    WHERE user_id = $1
                """, user_id, new_debt, actual_repayment, new_debt_level, new_streak, now)
                
                # Invalider le cache
                if user_id in self._debt_cache:
                    del self._debt_cache[user_id]
                    del self._cache_expiry[user_id]
                
                repay_info = {
                    "amount_paid": amount,
                    "actual_repayment": actual_repayment,
                    "bonus": actual_repayment - amount if apply_bonus else 0,
                    "remaining_debt": new_debt,
                    "old_level": current_info.get("debt_level", 0),
                    "new_level": new_debt_level,
                    "new_streak": new_streak,
                    "payment_type": payment_type
                }
                
                logger.info(f"Dette remboursée: User {user_id} -{actual_repayment} PB ({payment_type}) - Restant: {new_debt}")
                return True, actual_repayment, repay_info
                
        except Exception as e:
            logger.error(f"Erreur repay_debt {user_id}: {e}")
            return False, 0, {"error": str(e)}
    
    async def can_withdraw(self, user_id: int, amount: int) -> Tuple[bool, str, float]:
        """Vérifie si l'utilisateur peut retirer selon son niveau de dette"""
        try:
            debt_info = await self.get_debt_info(user_id)
            debt_level = debt_info.get("debt_level", 0)
            grace_end = debt_info.get("grace_period_end")
            
            # Vérifier la période de grâce
            is_in_grace = False
            if grace_end and datetime.now(timezone.utc) < grace_end:
                is_in_grace = True
                debt_level = 0  # Traiter comme niveau vert pendant la grâce
            
            level_config = DebtLevel.CONFIGS[debt_level]
            max_withdraw = level_config["max_withdraw"]
            cooldown_multiplier = level_config["cooldown_multiplier"]
            
            if amount > max_withdraw:
                if debt_level == DebtLevel.RED:
                    return False, f"🔴 **ACCÈS BLOQUÉ**\n\nTu as {debt_info['total_debt']:,} PB de dette.\n**Rembourse au moins {debt_info['total_debt'] - DebtLevel.THRESHOLDS[2]:,} PB** pour débloquer les retraits.", cooldown_multiplier
                else:
                    grace_msg = "\n🎁 *Période de grâce active*" if is_in_grace else ""
                    return False, f"{level_config['name']} **Limite de retrait dépassée**\n\n" + \
                                f"• **Maximum autorisé:** {max_withdraw:,} PB\n" + \
                                f"• **Montant demandé:** {amount:,} PB\n" + \
                                f"• **Ta dette actuelle:** {debt_info['total_debt']:,} PB{grace_msg}", cooldown_multiplier
            
            return True, "Retrait autorisé", cooldown_multiplier
            
        except Exception as e:
            logger.error(f"Erreur can_withdraw {user_id}: {e}")
            return False, "Erreur lors de la vérification", 1.0
    
    async def get_debt_statistics(self) -> Dict:
        """Récupère les statistiques globales du système de dette"""
        if not self.db.pool:
            return {}
            
        try:
            async with self.db.pool.acquire() as conn:
                stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total_users,
                        COALESCE(SUM(total_debt), 0) as total_debt,
                        COALESCE(SUM(total_withdrawn), 0) as total_withdrawn,
                        COALESCE(SUM(total_repaid), 0) as total_repaid,
                        COUNT(CASE WHEN debt_level = 0 THEN 1 END) as green_users,
                        COUNT(CASE WHEN debt_level = 1 THEN 1 END) as yellow_users,
                        COUNT(CASE WHEN debt_level = 2 THEN 1 END) as orange_users,
                        COUNT(CASE WHEN debt_level = 3 THEN 1 END) as red_users
                    FROM public_bank_debts
                    WHERE total_debt > 0 OR total_withdrawn > 0
                """)
                
                return dict(stats) if stats else {}
                
        except Exception as e:
            logger.error(f"Erreur debt statistics: {e}")
            return {}

class DebtSystem(commands.Cog):
    """Cog principal du système de dette de la banque publique"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        self.debt_manager = None
        
        # Statistiques de session
        self._session_stats = {
            "auto_repayments": 0,
            "manual_repayments": 0, 
            "debts_created": 0,
            "total_repaid": 0
        }
    
    async def cog_load(self):
        """Initialisation du cog"""
        self.db = self.bot.database
        self.debt_manager = PublicBankDebt(self.bot, self.db)
        await self.debt_manager.create_debt_table()
        logger.info("✅ Système de Dette banque publique initialisé")
    
    # ==================== COMMANDES UTILISATEUR ====================
    
    @commands.command(name='debt', aliases=['dette', 'mydebts'])
    async def debt_cmd(self, ctx):
        """Affiche tes dettes envers la banque publique"""
        await self._execute_debt_info(ctx)
    
    @app_commands.command(name="debt", description="Affiche tes dettes envers la banque publique")
    async def debt_slash(self, interaction: discord.Interaction):
        """/debt - Voir mes dettes"""
        await interaction.response.defer()
        await self._execute_debt_info(interaction, is_slash=True)
    
    async def _execute_debt_info(self, ctx_or_interaction, is_slash=False):
        """Logique commune pour afficher les informations de dette"""
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
            grace_end = debt_info.get("grace_period_end")
            is_in_grace = False
            if grace_end and datetime.now(timezone.utc) < grace_end:
                is_in_grace = True
            
            embed = discord.Embed(
                title="🏦 Ton Statut de Dette - Banque Publique",
                description=f"**Niveau:** {level_config['name']}\n*{level_config['description']}*",
                color=level_config['color']
            )
            
            # Informations de dette
            embed.add_field(
                name="💸 Dette actuelle",
                value=f"**{total_debt:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📊 Total retiré",
                value=f"**{debt_info.get('total_withdrawn', 0):,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="✅ Total remboursé",
                value=f"**{debt_info.get('total_repaid', 0):,}** PrissBucks",
                inline=True
            )
            
            # Limites actuelles
            max_withdraw = level_config['max_withdraw']
            cooldown_mult = level_config['cooldown_multiplier']
            
            if is_in_grace:
                embed.add_field(
                    name="🎁 Période de Grâce Active",
                    value=f"Tu bénéficies d'un accès normal jusqu'au <t:{int(grace_end.timestamp())}:f>\n"
                          f"Profite-en pour rembourser tes dettes !",
                    inline=False
                )
                max_withdraw = 1000  # Limite normale pendant la grâce
                cooldown_mult = 1.0
            
            embed.add_field(
                name="⚖️ Tes limites actuelles",
                value=f"🔸 **Retrait max:** {max_withdraw:,} PB par fois\n"
                      f"🔸 **Cooldown:** x{cooldown_mult} (base: 30min)\n"
                      f"🔸 **Statut:** {'🎁 Grâce' if is_in_grace else level_config['name']}",
                inline=False
            )
            
            # Comment rembourser
            embed.add_field(
                name="💡 Comment rembourser ta dette ?",
                value=f"🔸 **Messages:** {DebtSettings.AUTO_REPAY_MESSAGE} PB effacé par message actif\n"
                      f"🔸 **Gains casino:** {DebtSettings.AUTO_REPAY_CASINO_RATE*100:.0f}% prélevés automatiquement\n"
                      f"🔸 **Manuel:** `{PREFIX}paydebt <montant>` (+{DebtSettings.MANUAL_REPAY_BONUS*100:.0f}% bonus !)\n"
                      f"🔸 **Temps:** Remboursement passif par activité",
                inline=False
            )
            
            # Streak de paiement
            streak = debt_info.get("payment_streak", 0)
            if streak > 0:
                embed.add_field(
                    name="🔥 Streak de Remboursement",
                    value=f"**{streak} jour(s)** consécutifs\n"
                          f"*Bonus actuel: +{streak * DebtSettings.STREAK_BONUS_RATE * 100:.0f}% sur remboursements*",
                    inline=True
                )
            
            # Prochaines étapes
            if debt_level < DebtLevel.RED:
                next_threshold = DebtLevel.THRESHOLDS[debt_level] if debt_level < len(DebtLevel.THRESHOLDS) else None
                if next_threshold and total_debt < next_threshold:
                    remaining = next_threshold - total_debt
                    embed.add_field(
                        name="🎯 Prochaine dégradation",
                        value=f"Dans **{remaining:,}** PB de dette supplémentaire",
                        inline=True
                    )
            
            if total_debt > 0:
                # Progression vers niveau inférieur
                if debt_level > DebtLevel.GREEN:
                    target_threshold = DebtLevel.THRESHOLDS[debt_level - 1] if debt_level <= len(DebtLevel.THRESHOLDS) else 0
                    needed_repayment = max(0, total_debt - target_threshold)
                    embed.add_field(
                        name="⬇️ Améliorer ton niveau",
                        value=f"Rembourse **{needed_repayment:,}** PB pour passer au niveau supérieur",
                        inline=True
                    )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            
            if total_debt == 0:
                embed.set_footer(text="✨ Aucune dette ! Tu peux retirer normalement de la banque publique.")
            else:
                embed.set_footer(text="💡 Le remboursement de dette est automatique via ton activité !")
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur debt info {user.id}: {e}")
            embed = create_error_embed("Erreur", "Impossible de récupérer tes informations de dette.")
            await send_func(embed=embed)
    
    @commands.command(name='paydebt', aliases=['rembourser', 'payback'])
    async def pay_debt_cmd(self, ctx, amount: int):
        """Rembourse manuellement ta dette avec bonus de 10%"""
        await self._execute_pay_debt(ctx, amount)
    
    @app_commands.command(name="paydebt", description="Rembourse manuellement ta dette avec bonus de 10%")
    @app_commands.describe(amount="Montant à rembourser en PrissBucks")
    async def pay_debt_slash(self, interaction: discord.Interaction, amount: int):
        """/paydebt <amount> - Rembourser sa dette"""
        await interaction.response.defer()
        await self._execute_pay_debt(interaction, amount, is_slash=True)
    
    async def _execute_pay_debt(self, ctx_or_interaction, amount, is_slash=False):
        """Logique commune pour le remboursement manuel"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        
        # Validations
        if amount <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif !")
            await send_func(embed=embed)
            return
        
        if amount > 100000:
            embed = create_error_embed("Montant trop élevé", "Maximum 100,000 PB par remboursement.")
            await send_func(embed=embed)
            return
        
        try:
            # Vérifier le solde utilisateur
            current_balance = await self.db.get_balance(user_id)
            if current_balance < amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Tu as {current_balance:,} PrissBucks mais tu veux rembourser {amount:,} PrissBucks."
                )
                await send_func(embed=embed)
                return
            
            # Vérifier s'il y a une dette
            debt_info = await self.debt_manager.get_debt_info(user_id)
            current_debt = debt_info.get("total_debt", 0)
            
            if current_debt <= 0:
                embed = create_warning_embed(
                    "Aucune dette",
                    "Tu n'as actuellement aucune dette envers la banque publique !"
                )
                await send_func(embed=embed)
                return
            
            # Effectuer le remboursement
            balance_before = current_balance
            success, actual_repayment, repay_info = await self.debt_manager.repay_debt(
                user_id, amount, "manual", apply_bonus=True
            )
            
            if not success:
                embed = create_error_embed("Erreur", repay_info.get("error", "Erreur lors du remboursement."))
                await send_func(embed=embed)
                return
            
            # Débiter le montant du compte utilisateur
            await self.db.update_balance(user_id, -amount)
            balance_after = balance_before - amount
            
            # Logger la transaction
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_transaction(
                    user_id=user_id,
                    transaction_type='debt_repayment',
                    amount=-amount,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    description=f"Remboursement dette manuel +{actual_repayment} PB (bonus: {repay_info['bonus']})"
                )
            
            # Message de confirmation
            old_level = repay_info.get("old_level", 0)
            new_level = repay_info.get("new_level", 0)
            level_changed = old_level != new_level
            
            embed = discord.Embed(
                title="✅ Remboursement Réussi !",
                description=f"**{amount:,}** PrissBucks prélevés de ton compte",
                color=Colors.SUCCESS
            )
            
            embed.add_field(
                name="💰 Remboursement effectif",
                value=f"**{actual_repayment:,}** PrissBucks\n"
                      f"*(+{repay_info['bonus']:,} PB de bonus !)*",
                inline=True
            )
            
            embed.add_field(
                name="📊 Dette restante", 
                value=f"**{repay_info['remaining_debt']:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="💳 Nouveau solde",
                value=f"**{balance_after:,}** PrissBucks", 
                inline=True
            )
            
            if level_changed:
                old_config = DebtLevel.CONFIGS[old_level]
                new_config = DebtLevel.CONFIGS[new_level]
                embed.add_field(
                    name="🎉 Niveau amélioré !",
                    value=f"{old_config['name']} → **{new_config['name']}**\n"
                          f"Nouvelle limite: **{new_config['max_withdraw']:,}** PB",
                    inline=False
                )
            
            # Streak info
            new_streak = repay_info.get("new_streak", 0)
            if new_streak > 0:
                embed.add_field(
                    name="🔥 Streak de paiement",
                    value=f"**{new_streak} jour(s)** consécutifs\n"
                          f"Bonus futur: +{new_streak * DebtSettings.STREAK_BONUS_RATE * 100:.0f}%",
                    inline=True
                )
            
            embed.add_field(
                name="💡 Bonus remboursement manuel",
                value=f"Tu as reçu **{DebtSettings.MANUAL_REPAY_BONUS*100:.0f}% de bonus** pour ce remboursement volontaire !\n"
                      f"Continue à rembourser pour améliorer ton niveau.",
                inline=False
            )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="Merci pour ton remboursement ! Tes privilèges de retrait sont maintenus.")
            
            await send_func(embed=embed)
            
            # Mettre à jour les stats de session
            self._session_stats["manual_repayments"] += 1
            self._session_stats["total_repaid"] += actual_repayment
            
            logger.info(f"Dette remboursée manuellement: {user} -{amount} PB → -{actual_repayment} PB dette (bonus: {repay_info['bonus']})")
            
        except Exception as e:
            logger.error(f"Erreur paydebt {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du remboursement de la dette.")
            await send_func(embed=embed)
    
    # ==================== COMMANDES ADMIN ====================
    
    @commands.command(name='debtstats', aliases=['debt_stats'])
    @commands.has_permissions(administrator=True)
    async def debt_stats_cmd(self, ctx):
        """[ADMIN] Statistiques du système de dette"""
        try:
            stats = await self.debt_manager.get_debt_statistics()
            
            if not stats.get("total_users", 0):
                embed = create_warning_embed(
                    "Aucune donnée",
                    "Aucun utilisateur n'a encore utilisé la banque publique avec le système de dette."
                )
                await ctx.send(embed=embed)
                return
            
            embed = discord.Embed(
                title="📊 Statistiques Système de Dette",
                color=Colors.INFO
            )
            
            # Statistiques globales
            embed.add_field(
                name="👥 Utilisateurs",
                value=f"**{stats['total_users']}** au total",
                inline=True
            )
            
            embed.add_field(
                name="💸 Dette totale",
                value=f"**{stats['total_debt']:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="✅ Total remboursé", 
                value=f"**{stats['total_repaid']:,}** PrissBucks",
                inline=True
            )
            
            # Répartition par niveaux
            total_users = stats['total_users']
            green_pct = (stats['green_users'] / total_users * 100) if total_users > 0 else 0
            yellow_pct = (stats['yellow_users'] / total_users * 100) if total_users > 0 else 0
            orange_pct = (stats['orange_users'] / total_users * 100) if total_users > 0 else 0
            red_pct = (stats['red_users'] / total_users * 100) if total_users > 0 else 0
            
            embed.add_field(
                name="🟢 Niveau VERT",
                value=f"**{stats['green_users']}** users ({green_pct:.1f}%)",
                inline=True
            )
            
            embed.add_field(
                name="🟡 Niveau JAUNE",
                value=f"**{stats['yellow_users']}** users ({yellow_pct:.1f}%)",
                inline=True
            )
            
            embed.add_field(
                name="🟠 Niveau ORANGE", 
                value=f"**{stats['orange_users']}** users ({orange_pct:.1f}%)",
                inline=True
            )
            
            embed.add_field(
                name="🔴 Niveau ROUGE",
                value=f"**{stats['red_users']}** users ({red_pct:.1f}%)",
                inline=True
            )
            
            # Calculs supplémentaires
            repayment_rate = (stats['total_repaid'] / stats['total_withdrawn'] * 100) if stats['total_withdrawn'] > 0 else 0
            avg_debt = stats['total_debt'] / stats['total_users'] if stats['total_users'] > 0 else 0
            
            embed.add_field(
                name="📈 Taux de remboursement",
                value=f"**{repayment_rate:.1f}%** remboursé",
                inline=True
            )
            
            embed.add_field(
                name="📊 Dette moyenne",
                value=f"**{avg_debt:,.0f}** PB par user",
                inline=True
            )
            
            # Stats de session
            embed.add_field(
                name="⚡ Stats session",
                value=f"• {self._session_stats['auto_repayments']} remboursements auto\n"
                      f"• {self._session_stats['manual_repayments']} remboursements manuels\n"
                      f"• {self._session_stats['debts_created']} nouvelles dettes\n"
                      f"• {self._session_stats['total_repaid']:,} PB remboursés",
                inline=False
            )
            
            embed.set_footer(text="Système de dette - Prévention inflation banque publique")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur debtstats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)
    
    @commands.command(name='forcerepay')
    @commands.has_permissions(administrator=True)
    async def force_repay_cmd(self, ctx, user: discord.Member, amount: int):
        """[ADMIN] Force le remboursement de dette d'un utilisateur"""
        try:
            success, actual_repayment, repay_info = await self.debt_manager.repay_debt(
                user.id, amount, "admin_force", apply_bonus=False
            )
            
            if not success:
                embed = create_error_embed("Erreur", repay_info.get("error", "Impossible de forcer le remboursement."))
                await ctx.send(embed=embed)
                return
            
            embed = create_success_embed(
                "Remboursement forcé",
                f"**{actual_repayment:,}** PrissBucks de dette remboursés pour {user.display_name}"
            )
            
            embed.add_field(
                name="📊 Avant/Après",
                value=f"Niveau: {repay_info['old_level']} → {repay_info['new_level']}\n"
                      f"Dette restante: {repay_info['remaining_debt']:,} PB",
                inline=False
            )
            
            await ctx.send(embed=embed)
            logger.info(f"ADMIN {ctx.author} a forcé le remboursement de {amount} PB pour {user}")
            
        except Exception as e:
            logger.error(f"Erreur forcerepay: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du remboursement forcé.")
            await ctx.send(embed=embed)
    
    @commands.command(name='debthelp')
    async def debt_help_cmd(self, ctx):
        """Guide complet du système de dette"""
        embed = discord.Embed(
            title="🏦 Guide du Système de Dette",
            description="**Comment fonctionne le système anti-inflation de la banque publique**",
            color=Colors.INFO
        )
        
        # Principe de base
        embed.add_field(
            name="💡 Principe",
            value="Chaque retrait de la banque publique crée une **dette équivalente**.\n"
                  "Plus tu dois, plus tes accès sont limités.\n"
                  "**Objectif:** Éviter l'inflation et encourager la circulation d'argent.",
            inline=False
        )
        
        # Niveaux de dette
        levels_desc = ""
        for level, config in DebtLevel.CONFIGS.items():
            threshold_text = f"0-{DebtLevel.THRESHOLDS[0]:,}" if level == 0 else \
                           f"{DebtLevel.THRESHOLDS[level-1]+1:,}-{DebtLevel.THRESHOLDS[level]:,}" if level < len(DebtLevel.THRESHOLDS) else \
                           f"{DebtLevel.THRESHOLDS[-1]+1:,}+"
            
            levels_desc += f"{config['name']} **({threshold_text} PB)**\n"
            levels_desc += f"├ Max retrait: {config['max_withdraw']:,} PB\n"
            levels_desc += f"└ Cooldown: x{config['cooldown_multiplier']}\n\n"
        
        embed.add_field(
            name="📊 Niveaux de Dette",
            value=levels_desc,
            inline=False
        )
        
        # Comment rembourser
        embed.add_field(
            name="💰 Comment rembourser ?",
            value=f"🔸 **Automatique (Messages):** {DebtSettings.AUTO_REPAY_MESSAGE} PB par message actif\n"
                  f"🔸 **Automatique (Casino):** {DebtSettings.AUTO_REPAY_CASINO_RATE*100:.0f}% des gains prélevés\n"
                  f"🔸 **Manuel:** `{PREFIX}paydebt <montant>` (+{DebtSettings.MANUAL_REPAY_BONUS*100:.0f}% bonus)\n"
                  f"🔸 **Streak:** Jusqu'à +{7 * DebtSettings.STREAK_BONUS_RATE * 100:.0f}% bonus après 7 jours",
            inline=False
        )
        
        # Période de grâce
        embed.add_field(
            name="🎁 Période de Grâce",
            value=f"Les nouveaux utilisateurs bénéficient de **{DebtSettings.GRACE_PERIOD_HOURS//24} jours** d'accès normal\n"
                  f"pour s'adapter au système sans pénalités immédiates.",
            inline=False
        )
        
        # Commandes utiles
        embed.add_field(
            name="🔧 Commandes",
            value=f"• `{PREFIX}debt` ou `/debt` - Voir ta dette et tes limites\n"
                  f"• `{PREFIX}paydebt <montant>` - Rembourser manuellement (+bonus)\n"
                  f"• `{PREFIX}debthelp` - Ce guide complet",
            inline=False
        )
        
        # Exemple concret
        embed.add_field(
            name="📝 Exemple",
            value="**Jean retire 3000 PB** → Dette 3000 PB (🟡 Niveau JAUNE)\n"
                  "**Limite:** Max 500 PB par retrait, cooldown x1.5\n"
                  "**Activité:** 100 messages → -100 PB dette\n"
                  "**Casino:** Gagne 1000 PB → -200 PB dette auto\n"
                  "**Manuel:** Paie 500 PB → -550 PB dette (bonus 10%)\n"
                  "**Résultat:** Dette 2150 PB → Toujours niveau 🟡",
            inline=False
        )
        
        embed.set_footer(text="Le système s'équilibre automatiquement • Plus d'activité = moins de dette")
        await ctx.send(embed=embed)
    
    # ==================== INTÉGRATIONS AUTOMATIQUES ====================
    
    async def auto_repay_from_message(self, user_id: int) -> bool:
        """Appelé par MessageRewards pour rembourser automatiquement via messages"""
        try:
            debt_info = await self.debt_manager.get_debt_info(user_id)
            if debt_info.get("total_debt", 0) <= 0:
                return False
            
            success, actual_repayment, _ = await self.debt_manager.repay_debt(
                user_id, DebtSettings.AUTO_REPAY_MESSAGE, "message", apply_bonus=False
            )
            
            if success:
                self._session_stats["auto_repayments"] += 1
                self._session_stats["total_repaid"] += actual_repayment
                return True
                
        except Exception as e:
            logger.error(f"Erreur auto_repay_from_message {user_id}: {e}")
        
        return False
    
    async def auto_repay_from_casino(self, user_id: int, winnings: int) -> int:
        """Appelé par les jeux casino pour prélever automatiquement sur les gains"""
        try:
            debt_info = await self.debt_manager.get_debt_info(user_id)
            current_debt = debt_info.get("total_debt", 0)
            
            if current_debt <= 0:
                return winnings  # Aucune dette, pas de prélèvement
            
            # Calculer le prélèvement
            repayment_amount = int(winnings * DebtSettings.AUTO_REPAY_CASINO_RATE)
            
            if repayment_amount <= 0:
                return winnings
            
            success, actual_repayment, _ = await self.debt_manager.repay_debt(
                user_id, repayment_amount, "casino", apply_bonus=False
            )
            
            if success:
                self._session_stats["auto_repayments"] += 1
                self._session_stats["total_repaid"] += actual_repayment
                net_winnings = winnings - repayment_amount
                
                logger.debug(f"Auto-remboursement casino: User {user_id} {repayment_amount} PB prélevés sur gains {winnings}")
                return max(0, net_winnings)
            
        except Exception as e:
            logger.error(f"Erreur auto_repay_from_casino {user_id}: {e}")
        
        return winnings  # En cas d'erreur, pas de prélèvement
    
    async def create_debt_for_withdrawal(self, user_id: int, amount: int) -> bool:
        """Appelé par PublicBank pour créer une dette lors d'un retrait"""
        try:
            success = await self.debt_manager.create_debt(user_id, amount)
            if success:
                self._session_stats["debts_created"] += 1
            return success
        except Exception as e:
            logger.error(f"Erreur create_debt_for_withdrawal {user_id}: {e}")
            return False
    
    async def check_withdrawal_authorization(self, user_id: int, amount: int) -> Tuple[bool, str, float]:
        """Appelé par PublicBank avant chaque retrait pour vérification"""
        return await self.debt_manager.can_withdraw(user_id, amount)
    
    # ==================== COMMANDES DE DEBUG/TEST ====================
    
    @commands.command(name='testdebt')
    @commands.is_owner()
    async def test_debt_cmd(self, ctx, user: discord.Member = None):
        """[OWNER] Test du système de dette"""
        target = user or ctx.author
        
        try:
            # Créer une dette de test
            await self.debt_manager.create_debt(target.id, 1500)
            
            # Simuler un remboursement
            await self.debt_manager.repay_debt(target.id, 200, "test", apply_bonus=True)
            
            embed = create_success_embed(
                "Test Dette",
                f"Dette de test créée et remboursement simulé pour {target.display_name}"
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"Erreur test: {e}")
    
    @commands.command(name='resetdebt')
    @commands.is_owner()
    async def reset_debt_cmd(self, ctx, user: discord.Member):
        """[OWNER] Remet à zéro la dette d'un utilisateur"""
        try:
            if not self.db.pool:
                await ctx.send("❌ Base de données non disponible")
                return
            
            async with self.db.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE public_bank_debts SET
                    total_debt = 0,
                    debt_level = 0,
                    payment_streak = 0
                    WHERE user_id = $1
                """, user.id)
            
            # Invalider le cache
            if user.id in self.debt_manager._debt_cache:
                del self.debt_manager._debt_cache[user.id]
                del self.debt_manager._cache_expiry[user.id]
            
            embed = create_success_embed(
                "Dette réinitialisée",
                f"La dette de {user.display_name} a été remise à zéro."
            )
            await ctx.send(embed=embed)
            
            logger.info(f"OWNER {ctx.author} a réinitialisé la dette de {user}")
            
        except Exception as e:
            logger.error(f"Erreur resetdebt: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la réinitialisation de la dette.")
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(DebtSystem(bot))
