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
    """Système de niveaux de dette progressif et équilibré"""
    
    GREEN = 0    # 0-5000 PB - Accès normal
    YELLOW = 1   # 5001-15000 PB - Limites légères
    ORANGE = 2   # 15001-35000 PB - Limites modérées  
    RED = 3      # 35001-75000 PB - Limites strictes
    CRITICAL = 4 # 75000+ PB - Accès très restreint
    
    CONFIGS = {
        GREEN: {
            "name": "🟢 EXCELLENT",
            "color": Colors.SUCCESS,
            "max_withdraw": 2000,      # Augmenté de 1000 à 2000
            "cooldown_multiplier": 1.0,
            "description": "Accès privilégié à la banque publique",
            "interest_rate": 0.0       # Pas d'intérêts
        },
        YELLOW: {
            "name": "🟡 BON", 
            "color": Colors.WARNING,
            "max_withdraw": 1200,      # Augmenté de 500 à 1200
            "cooldown_multiplier": 1.2, # Réduit de 1.5 à 1.2
            "description": "Quelques limites - Gestion recommandée",
            "interest_rate": 0.002     # 0.2% par jour
        },
        ORANGE: {
            "name": "🟠 MOYEN",
            "color": 0xff6600,
            "max_withdraw": 800,       # Augmenté de 200 à 800
            "cooldown_multiplier": 1.5, # Réduit de 2.0 à 1.5
            "description": "Limites importantes - Remboursement conseillé",
            "interest_rate": 0.003     # 0.3% par jour
        },
        RED: {
            "name": "🔴 CRITIQUE",
            "color": Colors.ERROR,
            "max_withdraw": 400,       # Au lieu de 0, permet encore des retraits
            "cooldown_multiplier": 2.0, # Réduit de inf à 2.0
            "description": "Situation critique - Action requise",
            "interest_rate": 0.005     # 0.5% par jour
        },
        CRITICAL: {
            "name": "💀 DÉFAILLANT",
            "color": 0x800000,         # Rouge très sombre
            "max_withdraw": 100,       # Retrait minimal seulement
            "cooldown_multiplier": 3.0,
            "description": "⚠️ DÉFAUT DE PAIEMENT - Remboursement immédiat requis",
            "interest_rate": 0.01      # 1% par jour
        }
    }
    
    # Seuils augmentés pour permettre plus d'endettement
    THRESHOLDS = [5000, 15000, 35000, 75000]
    
    @classmethod
    def get_level_from_debt(cls, debt_amount: int) -> int:
        """Détermine le niveau de dette selon le montant"""
        for i, threshold in enumerate(cls.THRESHOLDS):
            if debt_amount <= threshold:
                return i
        return cls.CRITICAL

class DebtSettings:
    """Configuration optimisée du système de dette"""
    
    # Remboursement automatique amélioré
    AUTO_REPAY_MESSAGE = 2           # Augmenté de 1 à 2 PB par message
    AUTO_REPAY_CASINO_RATE = 0.15    # Réduit de 20% à 15%
    MANUAL_REPAY_BONUS = 0.15        # Augmenté de 10% à 15%
    
    # Système de streaks amélioré
    MIN_PAYMENT_STREAK = 25          # Réduit de 50 à 25 PB
    STREAK_BONUS_RATE = 0.03         # Réduit de 5% à 3% par jour (plus équilibré)
    MAX_STREAK_BONUS = 0.50          # Bonus maximum 50%
    
    # Période de grâce étendue
    GRACE_PERIOD_HOURS = 336         # 14 jours au lieu de 7 jours
    
    # Nouveau: Système de crédit personnel
    CREDIT_SCORE_ENABLED = True
    CREDIT_MULTIPLIERS = {
        "excellent": 1.5,    # Utilisateurs très actifs
        "good": 1.2,         # Utilisateurs moyennement actifs  
        "fair": 1.0,         # Utilisateurs normaux
        "poor": 0.8          # Utilisateurs peu fiables
    }
    
    # Nouveau: Intérêts composés (appliqués quotidiennement)
    INTEREST_COMPOUND_DAILY = True
    
    # Nouveau: Remboursement minimum requis
    MIN_MONTHLY_REPAYMENT_RATE = 0.05  # 5% du total par mois minimum

class CreditScore:
    """Système de score de crédit pour déterminer les limites personnalisées"""
    
    @staticmethod
    def calculate_credit_score(user_stats: Dict) -> Tuple[str, float]:
        """
        Calcule le score de crédit basé sur l'historique de l'utilisateur
        Retourne (niveau, multiplicateur)
        """
        # Récupérer les statistiques
        total_repaid = user_stats.get('total_repaid', 0)
        total_withdrawn = user_stats.get('total_withdrawn', 0)
        payment_streak = user_stats.get('payment_streak', 0)
        days_since_creation = user_stats.get('days_since_creation', 0)
        message_activity = user_stats.get('message_activity', 0)
        
        score = 100  # Score de base
        
        # Facteur de remboursement (40% du score)
        if total_withdrawn > 0:
            repayment_ratio = total_repaid / total_withdrawn
            if repayment_ratio >= 0.9:
                score += 40
            elif repayment_ratio >= 0.7:
                score += 30
            elif repayment_ratio >= 0.5:
                score += 15
            elif repayment_ratio >= 0.3:
                score += 5
            else:
                score -= 20
        
        # Facteur de régularité (30% du score)
        if payment_streak >= 14:
            score += 30
        elif payment_streak >= 7:
            score += 20
        elif payment_streak >= 3:
            score += 10
        
        # Facteur d'ancienneté (20% du score) 
        if days_since_creation >= 90:
            score += 20
        elif days_since_creation >= 30:
            score += 15
        elif days_since_creation >= 7:
            score += 10
        
        # Facteur d'activité (10% du score)
        if message_activity >= 1000:
            score += 10
        elif message_activity >= 500:
            score += 8
        elif message_activity >= 100:
            score += 5
        
        # Déterminer le niveau
        if score >= 150:
            return "excellent", DebtSettings.CREDIT_MULTIPLIERS["excellent"]
        elif score >= 120:
            return "good", DebtSettings.CREDIT_MULTIPLIERS["good"]
        elif score >= 80:
            return "fair", DebtSettings.CREDIT_MULTIPLIERS["fair"]
        else:
            return "poor", DebtSettings.CREDIT_MULTIPLIERS["poor"]

class PublicBankDebt:
    """Gestionnaire optimisé du système de dette"""
    
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db
        
        # Cache optimisé
        self._debt_cache: Dict[int, Dict] = {}
        self._cache_expiry: Dict[int, datetime] = {}
        self.CACHE_DURATION = 180  # Réduit à 3 minutes pour plus de réactivité
        
        # Nouveau: Statistiques de crédit
        self._credit_cache: Dict[int, Tuple[str, float]] = {}
    
    async def create_debt_table(self):
        """Crée la table des dettes avec nouvelles colonnes"""
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
                    last_interest_applied TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    grace_period_end TIMESTAMP WITH TIME ZONE DEFAULT NOW() + INTERVAL '14 days',
                    credit_score VARCHAR(20) DEFAULT 'fair',
                    credit_multiplier DECIMAL(3,2) DEFAULT 1.0,
                    monthly_repaid BIGINT DEFAULT 0,
                    last_monthly_reset TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    CHECK (total_debt >= 0),
                    CHECK (debt_level >= 0 AND debt_level <= 4),
                    CHECK (credit_multiplier > 0)
                )
            ''')
            
            # Ajouter les nouvelles colonnes si elles n'existent pas
            try:
                await conn.execute('ALTER TABLE public_bank_debts ADD COLUMN IF NOT EXISTS last_interest_applied TIMESTAMP WITH TIME ZONE DEFAULT NOW()')
                await conn.execute('ALTER TABLE public_bank_debts ADD COLUMN IF NOT EXISTS credit_score VARCHAR(20) DEFAULT \'fair\'')
                await conn.execute('ALTER TABLE public_bank_debts ADD COLUMN IF NOT EXISTS credit_multiplier DECIMAL(3,2) DEFAULT 1.0')
                await conn.execute('ALTER TABLE public_bank_debts ADD COLUMN IF NOT EXISTS monthly_repaid BIGINT DEFAULT 0')
                await conn.execute('ALTER TABLE public_bank_debts ADD COLUMN IF NOT EXISTS last_monthly_reset TIMESTAMP WITH TIME ZONE DEFAULT NOW()')
            except Exception as e:
                logger.debug(f"Colonnes déjà existantes ou erreur migration: {e}")
            
            # Index optimisés
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_debt_user_id ON public_bank_debts(user_id)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_debt_level ON public_bank_debts(debt_level)')
            await conn.execute('CREATE INDEX IF NOT EXISTS idx_credit_score ON public_bank_debts(credit_score)')
            
            logger.info("✅ Table public_bank_debts optimisée créée/mise à jour")
    
    async def _update_credit_score(self, user_id: int) -> Tuple[str, float]:
        """Met à jour le score de crédit d'un utilisateur"""
        try:
            # Récupérer les statistiques pour le calcul du crédit
            debt_info = await self.get_debt_info(user_id, use_cache=False)
            
            # Calculer l'activité récente (simulation)
            message_activity = await self._get_user_message_activity(user_id)
            days_since_creation = (datetime.now(timezone.utc) - 
                                 debt_info.get('created_at', datetime.now(timezone.utc))).days
            
            user_stats = {
                'total_repaid': debt_info.get('total_repaid', 0),
                'total_withdrawn': debt_info.get('total_withdrawn', 0),
                'payment_streak': debt_info.get('payment_streak', 0),
                'days_since_creation': days_since_creation,
                'message_activity': message_activity
            }
            
            credit_level, multiplier = CreditScore.calculate_credit_score(user_stats)
            
            # Mettre à jour en base
            async with self.db.pool.acquire() as conn:
                await conn.execute('''
                    UPDATE public_bank_debts 
                    SET credit_score = $2, credit_multiplier = $3
                    WHERE user_id = $1
                ''', user_id, credit_level, multiplier)
            
            # Mettre à jour le cache
            self._credit_cache[user_id] = (credit_level, multiplier)
            
            return credit_level, multiplier
            
        except Exception as e:
            logger.error(f"Erreur mise à jour crédit {user_id}: {e}")
            return "fair", 1.0
    
    async def _get_user_message_activity(self, user_id: int) -> int:
        """Récupère l'activité de message de l'utilisateur (simulation)"""
        # Dans un vrai bot, ceci serait calculé à partir des logs de messages
        # Pour l'instant, on simule avec une valeur basée sur l'ID
        return (user_id % 1000) + 100
    
    async def _apply_daily_interest(self, user_id: int) -> int:
        """Applique les intérêts quotidiens si nécessaire"""
        try:
            debt_info = await self.get_debt_info(user_id, use_cache=False)
            current_debt = debt_info.get('total_debt', 0)
            debt_level = debt_info.get('debt_level', 0)
            last_interest = debt_info.get('last_interest_applied')
            
            if current_debt <= 0 or debt_level == DebtLevel.GREEN:
                return 0  # Pas d'intérêts pour niveau vert ou sans dette
            
            now = datetime.now(timezone.utc)
            
            # Vérifier si les intérêts doivent être appliqués
            if not last_interest or (now - last_interest).days >= 1:
                interest_rate = DebtLevel.CONFIGS[debt_level]["interest_rate"]
                interest_amount = int(current_debt * interest_rate)
                
                if interest_amount > 0:
                    async with self.db.pool.acquire() as conn:
                        await conn.execute('''
                            UPDATE public_bank_debts 
                            SET total_debt = total_debt + $2,
                                last_interest_applied = $3
                            WHERE user_id = $1
                        ''', user_id, interest_amount, now)
                    
                    # Invalider le cache
                    if user_id in self._debt_cache:
                        del self._debt_cache[user_id]
                        del self._cache_expiry[user_id]
                    
                    logger.info(f"Intérêts appliqués: User {user_id} +{interest_amount} PB ({interest_rate*100:.1f}%)")
                    return interest_amount
            
            return 0
            
        except Exception as e:
            logger.error(f"Erreur application intérêts {user_id}: {e}")
            return 0
    
    async def get_debt_info(self, user_id: int, use_cache: bool = True) -> Dict:
        """Récupère les informations de dette avec application d'intérêts"""
        # Vérifier le cache
        if use_cache and self._is_cache_valid(user_id):
            return self._debt_cache[user_id].copy()
        
        if not self.db.pool:
            return self._default_debt_info()
            
        try:
            # Appliquer les intérêts d'abord
            await self._apply_daily_interest(user_id)
            
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT total_debt, debt_level, payment_streak, total_withdrawn, 
                           total_repaid, last_payment, grace_period_end, created_at,
                           credit_score, credit_multiplier, monthly_repaid, last_monthly_reset
                    FROM public_bank_debts 
                    WHERE user_id = $1
                """, user_id)
                
                if row:
                    debt_info = dict(row)
                    # Vérifier si le score de crédit doit être recalculé
                    if not debt_info.get('credit_score') or debt_info.get('credit_score') == 'fair':
                        await self._update_credit_score(user_id)
                        # Re-récupérer avec le nouveau score
                        row = await conn.fetchrow("""
                            SELECT total_debt, debt_level, payment_streak, total_withdrawn, 
                                   total_repaid, last_payment, grace_period_end, created_at,
                                   credit_score, credit_multiplier, monthly_repaid, last_monthly_reset
                            FROM public_bank_debts 
                            WHERE user_id = $1
                        """, user_id)
                        debt_info = dict(row)
                else:
                    debt_info = self._default_debt_info()
                
                # Mettre à jour le cache
                self._update_cache(user_id, debt_info)
                return debt_info.copy()
                
        except Exception as e:
            logger.error(f"Erreur get_debt_info {user_id}: {e}")
            return self._default_debt_info()
    
    def _default_debt_info(self) -> Dict:
        """Retourne les informations par défaut"""
        return {
            "total_debt": 0, "debt_level": 0, "payment_streak": 0,
            "total_withdrawn": 0, "total_repaid": 0, "last_payment": None,
            "grace_period_end": None, "created_at": datetime.now(timezone.utc),
            "credit_score": "fair", "credit_multiplier": 1.0,
            "monthly_repaid": 0, "last_monthly_reset": datetime.now(timezone.utc)
        }
    
    def _is_cache_valid(self, user_id: int) -> bool:
        """Vérifie si le cache est encore valide"""
        if user_id not in self._cache_expiry:
            return False
        return datetime.now(timezone.utc) < self._cache_expiry[user_id]
    
    def _update_cache(self, user_id: int, debt_info: Dict):
        """Met à jour le cache pour un utilisateur"""
        self._debt_cache[user_id] = debt_info
        self._cache_expiry[user_id] = datetime.now(timezone.utc) + timedelta(seconds=self.CACHE_DURATION)
    
    async def can_withdraw(self, user_id: int, amount: int) -> Tuple[bool, str, float]:
        """Vérifie si l'utilisateur peut retirer selon son niveau et crédit"""
        try:
            debt_info = await self.get_debt_info(user_id)
            debt_level = debt_info.get("debt_level", 0)
            grace_end = debt_info.get("grace_period_end")
            credit_multiplier = debt_info.get("credit_multiplier", 1.0)
            credit_score = debt_info.get("credit_score", "fair")
            
            # Vérifier la période de grâce
            is_in_grace = False
            if grace_end and datetime.now(timezone.utc) < grace_end:
                is_in_grace = True
                debt_level = 0  # Traiter comme niveau vert pendant la grâce
            
            level_config = DebtLevel.CONFIGS[debt_level]
            
            # Appliquer le multiplicateur de crédit à la limite de retrait
            base_max_withdraw = level_config["max_withdraw"]
            max_withdraw = int(base_max_withdraw * credit_multiplier)
            cooldown_multiplier = level_config["cooldown_multiplier"]
            
            if amount > max_withdraw:
                if debt_level == DebtLevel.CRITICAL:
                    return False, f"💀 **DÉFAUT DE PAIEMENT**\n\n" + \
                                f"Tu as {debt_info['total_debt']:,} PB de dette.\n" + \
                                f"**Remboursement urgent requis pour débloquer les retraits**", cooldown_multiplier
                else:
                    grace_msg = "\n🎁 *Période de grâce active*" if is_in_grace else ""
                    credit_msg = f"\n📊 *Score crédit: {credit_score.upper()} (x{credit_multiplier})*" if not is_in_grace else ""
                    
                    return False, f"{level_config['name']} **Limite de retrait dépassée**\n\n" + \
                                f"• **Limite de base:** {base_max_withdraw:,} PB\n" + \
                                f"• **Ta limite:** {max_withdraw:,} PB\n" + \
                                f"• **Montant demandé:** {amount:,} PB\n" + \
                                f"• **Ta dette:** {debt_info['total_debt']:,} PB{grace_msg}{credit_msg}", cooldown_multiplier
            
            return True, "Retrait autorisé", cooldown_multiplier
            
        except Exception as e:
            logger.error(f"Erreur can_withdraw {user_id}: {e}")
            return False, "Erreur lors de la vérification", 1.0
    
    async def create_debt(self, user_id: int, amount: int) -> bool:
        """Crée une dette lors d'un retrait avec mise à jour du crédit"""
        if not self.db.pool or amount <= 0:
            return False
            
        try:
            async with self.db.pool.acquire() as conn:
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
                
                # Recalculer le crédit après changement significatif
                if amount > 1000:
                    await self._update_credit_score(user_id)
                
                logger.info(f"Dette créée: User {user_id} +{amount} PB (total: {new_total_debt}, niveau: {new_debt_level})")
                return True
                
        except Exception as e:
            logger.error(f"Erreur create_debt {user_id}: {e}")
            return False
    
    async def repay_debt(self, user_id: int, amount: int, payment_type: str = "manual", 
                        apply_bonus: bool = False) -> Tuple[bool, int, Dict]:
        """Rembourse une partie de la dette avec bonus de streak amélioré"""
        if not self.db.pool or amount <= 0:
            return False, 0, {}
            
        try:
            current_info = await self.get_debt_info(user_id, use_cache=False)
            current_debt = current_info["total_debt"]
            
            if current_debt <= 0:
                return True, 0, {"message": "Aucune dette à rembourser"}
            
            # Calculer le bonus de streak (amélioré)
            streak_bonus = 0
            current_streak = current_info.get("payment_streak", 0)
            if current_streak > 0 and amount >= DebtSettings.MIN_PAYMENT_STREAK:
                max_bonus = min(current_streak * DebtSettings.STREAK_BONUS_RATE, 
                               DebtSettings.MAX_STREAK_BONUS)
                streak_bonus = int(amount * max_bonus)
            
            # Calculer le remboursement effectif
            actual_repayment = amount
            if apply_bonus:
                manual_bonus = int(amount * DebtSettings.MANUAL_REPAY_BONUS)
                actual_repayment += manual_bonus + streak_bonus
            elif streak_bonus > 0:
                actual_repayment += streak_bonus
            
            # Ne pas rembourser plus que la dette
            actual_repayment = min(actual_repayment, current_debt)
            new_debt = max(0, current_debt - actual_repayment)
            new_debt_level = DebtLevel.get_level_from_debt(new_debt)
            
            # Calculer le nouveau streak
            now = datetime.now(timezone.utc)
            last_payment = current_info.get("last_payment")
            new_streak = current_info.get("payment_streak", 0)
            
            if (last_payment and 
                now - last_payment <= timedelta(days=1.5) and  # Un peu plus de flexibilité
                amount >= DebtSettings.MIN_PAYMENT_STREAK):
                new_streak += 1
            elif amount >= DebtSettings.MIN_PAYMENT_STREAK:
                new_streak = 1
            else:
                new_streak = max(0, new_streak - 1)  # Décrémenter si paiement trop petit
            
            async with self.db.pool.acquire() as conn:
                await conn.execute("""
                    UPDATE public_bank_debts SET
                    total_debt = $2,
                    total_repaid = total_repaid + $3,
                    debt_level = $4,
                    payment_streak = $5,
                    last_payment = $6,
                    monthly_repaid = monthly_repaid + $3
                    WHERE user_id = $1
                """, user_id, new_debt, actual_repayment, new_debt_level, new_streak, now)
                
                # Invalider le cache
                if user_id in self._debt_cache:
                    del self._debt_cache[user_id]
                    del self._cache_expiry[user_id]
                
                # Mettre à jour le score de crédit si remboursement important
                if actual_repayment > 500:
                    await self._update_credit_score(user_id)
                
                repay_info = {
                    "amount_paid": amount,
                    "actual_repayment": actual_repayment,
                    "manual_bonus": int(amount * DebtSettings.MANUAL_REPAY_BONUS) if apply_bonus else 0,
                    "streak_bonus": streak_bonus,
                    "total_bonus": actual_repayment - amount,
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
    
    async def get_debt_statistics(self) -> Dict:
        """Récupère les statistiques globales avec nouvelles métriques"""
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
                        COUNT(CASE WHEN debt_level = 3 THEN 1 END) as red_users,
                        COUNT(CASE WHEN debt_level = 4 THEN 1 END) as critical_users,
                        AVG(credit_multiplier) as avg_credit_multiplier
                    FROM public_bank_debts
                    WHERE total_debt > 0 OR total_withdrawn > 0
                """)
                
                # Statistiques de crédit
                credit_stats = await conn.fetch("""
                    SELECT credit_score, COUNT(*) as count, AVG(total_debt) as avg_debt
                    FROM public_bank_debts 
                    WHERE total_debt > 0 OR total_withdrawn > 0
                    GROUP BY credit_score
                """)
                
                result = dict(stats) if stats else {}
                result['credit_distribution'] = {row['credit_score']: dict(row) for row in credit_stats}
                
                return result
                
        except Exception as e:
            logger.error(f"Erreur debt statistics: {e}")
            return {}

class DebtSystem(commands.Cog):
    """Cog du système de dette optimisé"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        self.debt_manager = None
        
        # Statistiques de session étendues
        self._session_stats = {
            "auto_repayments": 0,
            "manual_repayments": 0, 
            "debts_created": 0,
            "total_repaid": 0,
            "interest_applied": 0,
            "credit_updates": 0
        }
    
    async def cog_load(self):
        """Initialisation du cog optimisé"""
        self.db = self.bot.database
        self.debt_manager = PublicBankDebt(self.bot, self.db)
        await self.debt_manager.create_debt_table()
        logger.info("✅ Système de Dette optimisé initialisé")
    
    # ==================== COMMANDES UTILISATEUR AMÉLIORÉES ====================
    
    @commands.command(name='debt', aliases=['dette', 'mydebts'])
    async def debt_cmd(self, ctx):
        """Affiche tes dettes avec informations de crédit"""
        await self._execute_debt_info(ctx)
    
    @app_commands.command(name="debt", description="Affiche tes dettes et ton score de crédit")
    async def debt_slash(self, interaction: discord.Interaction):
        """/debt - Voir mes dettes et crédit"""
        await interaction.response.defer()
        await self._execute_debt_info(interaction, is_slash=True)
    
    async def _execute_debt_info(self, ctx_or_interaction, is_slash=False):
        """Logique améliorée pour afficher les informations de dette"""
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
            credit_score = debt_info.get("credit_score", "fair")
            credit_multiplier = debt_info.get("credit_multiplier", 1.0)
            
            level_config = DebtLevel.CONFIGS[debt_level]
            
            # Vérifier période de grâce
            grace_end = debt_info.get("grace_period_end")
            is_in_grace = False
            if grace_end and datetime.now(timezone.utc) < grace_end:
                is_in_grace = True
            
            # Couleur du embed basée sur le score de crédit
            embed_colors = {
                "excellent": 0x00ff80,
                "good": 0x40ff00,
                "fair": level_config['color'],
                "poor": 0xff4000
            }
            
            embed = discord.Embed(
                title="🏦 Profil de Dette & Crédit",
                description=f"**Niveau de Dette:** {level_config['name']}\n"
                           f"**Score de Crédit:** {credit_score.upper()} (×{credit_multiplier})",
                color=embed_colors.get(credit_score, level_config['color'])
            )
            
            # Section financière
            repayment_rate = (debt_info.get('total_repaid', 0) / debt_info.get('total_withdrawn', 1)) * 100
            embed.add_field(
                name="💰 Situation Financière",
                value=f"**Dette actuelle:** {total_debt:,} PB\n"
                      f"**Total emprunté:** {debt_info.get('total_withdrawn', 0):,} PB\n"
                      f"**Total remboursé:** {debt_info.get('total_repaid', 0):,} PB\n"
                      f"**Taux remboursement:** {repayment_rate:.1f}%",
                inline=False
            )
            
            # Limites personnalisées
            base_max = level_config['max_withdraw']
            personal_max = int(base_max * credit_multiplier)
            cooldown_mult = level_config['cooldown_multiplier']
            
            if is_in_grace:
                embed.add_field(
                    name="🎁 Période de Grâce Active",
                    value=f"Accès privilégié jusqu'au <t:{int(grace_end.timestamp())}:f>\n"
                          f"⚡ Limite temporaire: **2,000 PB** par retrait",
                    inline=False
                )
                personal_max = 2000
                cooldown_mult = 1.0
            
            embed.add_field(
                name="⚖️ Tes Limites Personnalisées",
                value=f"🔸 **Limite de base:** {base_max:,} PB\n"
                      f"🔸 **Ta limite:** {personal_max:,} PB\n"
                      f"🔸 **Bonus crédit:** +{((credit_multiplier - 1) * 100):+.0f}%\n"
                      f"🔸 **Cooldown:** ×{cooldown_mult} (base: 30min)",
                inline=False
            )
            
            # Système de remboursement amélioré
            embed.add_field(
                name="💡 Options de Remboursement",
                value=f"🔸 **Messages actifs:** {DebtSettings.AUTO_REPAY_MESSAGE} PB/message\n"
                      f"🔸 **Gains casino:** {DebtSettings.AUTO_REPAY_CASINO_RATE*100:.0f}% prélevés\n"
                      f"🔸 **Manuel:** `{PREFIX}paydebt <montant>` (+{DebtSettings.MANUAL_REPAY_BONUS*100:.0f}% bonus)\n"
                      f"🔸 **Intérêts:** {level_config.get('interest_rate', 0)*100:.1f}% par jour",
                inline=False
            )
            
            # Streak et bonus
            streak = debt_info.get("payment_streak", 0)
            if streak > 0:
                streak_bonus = min(streak * DebtSettings.STREAK_BONUS_RATE * 100, 
                                 DebtSettings.MAX_STREAK_BONUS * 100)
                embed.add_field(
                    name="🔥 Série de Remboursements",
                    value=f"**{streak} jour(s)** consécutifs\n"
                          f"*Bonus actuel: +{streak_bonus:.0f}% sur remboursements*\n"
                          f"*Maximum possible: +{DebtSettings.MAX_STREAK_BONUS*100:.0f}%*",
                    inline=True
                )
            
            # Progression et objectifs
            if debt_level < DebtLevel.CRITICAL:
                next_threshold = DebtLevel.THRESHOLDS[debt_level] if debt_level < len(DebtLevel.THRESHOLDS) else None
                if next_threshold and total_debt < next_threshold:
                    remaining = next_threshold - total_debt
                    embed.add_field(
                        name="📈 Marge de Crédit Restante",
                        value=f"**{remaining:,} PB** avant dégradation\n"
                              f"*Tu peux encore emprunter*",
                        inline=True
                    )
            
            if total_debt > 0:
                if debt_level > DebtLevel.GREEN:
                    target_threshold = DebtLevel.THRESHOLDS[debt_level - 1] if debt_level <= len(DebtLevel.THRESHOLDS) else 0
                    needed_repayment = max(0, total_debt - target_threshold)
                    next_level_config = DebtLevel.CONFIGS[debt_level - 1]
                    embed.add_field(
                        name="⬆️ Amélioration Possible",
                        value=f"Rembourse **{needed_repayment:,} PB** pour passer à:\n"
                              f"**{next_level_config['name']}** (limite: {int(next_level_config['max_withdraw'] * credit_multiplier):,} PB)",
                        inline=True
                    )
            
            # Informations crédit détaillées
            credit_info = {
                "excellent": "🌟 Crédit exceptionnel - Privilèges étendus",
                "good": "✨ Bon crédit - Avantages modérés", 
                "fair": "📊 Crédit standard - Conditions normales",
                "poor": "⚠️ Crédit faible - Améliorations nécessaires"
            }
            
            embed.add_field(
                name="📊 Ton Score de Crédit",
                value=f"{credit_info.get(credit_score, 'Crédit inconnu')}\n"
                      f"*Basé sur ton historique et activité*",
                inline=False
            )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            
            if total_debt == 0:
                embed.set_footer(text="✨ Aucune dette ! Profite de ta capacité d'emprunt.")
            else:
                embed.set_footer(text="💡 Rembourse régulièrement pour améliorer ton crédit !")
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur debt info {user.id}: {e}")
            embed = create_error_embed("Erreur", "Impossible de récupérer tes informations de dette.")
            await send_func(embed=embed)
    
    @commands.command(name='paydebt', aliases=['rembourser', 'payback'])
    async def pay_debt_cmd(self, ctx, amount: int):
        """Rembourse ta dette avec bonus améliorés"""
        await self._execute_pay_debt(ctx, amount)
    
    @app_commands.command(name="paydebt", description="Rembourse ta dette avec bonus de streak et crédit")
    @app_commands.describe(amount="Montant à rembourser en PrissBucks")
    async def pay_debt_slash(self, interaction: discord.Interaction, amount: int):
        """/paydebt <amount> - Rembourser avec bonus"""
        await interaction.response.defer()
        await self._execute_pay_debt(interaction, amount, is_slash=True)
    
    async def _execute_pay_debt(self, ctx_or_interaction, amount, is_slash=False):
        """Logique améliorée pour le remboursement manuel"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        
        # Validations améliorées
        if amount <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif !")
            await send_func(embed=embed)
            return
        
        if amount > 250000:  # Limite augmentée
            embed = create_error_embed("Montant trop élevé", "Maximum 250,000 PB par remboursement.")
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
                    f"Tu n'as aucune dette ! 🎉\n"
                    f"**Ton score de crédit:** {debt_info.get('credit_score', 'fair').upper()}\n"
                    f"**Capacité d'emprunt disponible !**"
                )
                await send_func(embed=embed)
                return
            
            # Effectuer le remboursement avec tous les bonus
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
                    description=f"Remboursement manuel +{actual_repayment} PB (total bonus: {repay_info['total_bonus']})"
                )
            
            # Message de confirmation amélioré
            old_level = repay_info.get("old_level", 0)
            new_level = repay_info.get("new_level", 0)
            level_changed = old_level != new_level
            
            embed = discord.Embed(
                title="✅ Remboursement Réussi !",
                description=f"**{amount:,}** PrissBucks prélevés de ton compte",
                color=Colors.SUCCESS
            )
            
            # Détail des bonus
            manual_bonus = repay_info.get('manual_bonus', 0)
            streak_bonus = repay_info.get('streak_bonus', 0)
            total_bonus = repay_info.get('total_bonus', 0)
            
            embed.add_field(
                name="💰 Remboursement Effectif",
                value=f"**{actual_repayment:,}** PrissBucks appliqués\n"
                      f"*Base: {amount:,} PB*\n"
                      f"*Bonus manuel: +{manual_bonus:,} PB*\n"
                      f"*Bonus streak: +{streak_bonus:,} PB*",
                inline=True
            )
            
            embed.add_field(
                name="📊 Nouvelle Situation", 
                value=f"**Dette restante:** {repay_info['remaining_debt']:,} PB\n"
                      f"**Nouveau solde:** {balance_after:,} PB\n"
                      f"**Total bonus:** +{total_bonus:,} PB",
                inline=True
            )
            
            if level_changed:
                old_config = DebtLevel.CONFIGS[old_level]
                new_config = DebtLevel.CONFIGS[new_level]
                
                # Calculer les nouvelles limites avec le crédit
                credit_multiplier = debt_info.get('credit_multiplier', 1.0)
                new_limit = int(new_config['max_withdraw'] * credit_multiplier)
                
                embed.add_field(
                    name="🎉 Niveau Amélioré !",
                    value=f"{old_config['name']} → **{new_config['name']}**\n"
                          f"**Nouvelle limite:** {new_limit:,} PB\n"
                          f"**Cooldown:** ×{new_config['cooldown_multiplier']}",
                    inline=False
                )
            
            # Streak amélioré
            new_streak = repay_info.get("new_streak", 0)
            if new_streak > 0:
                next_bonus = min((new_streak + 1) * DebtSettings.STREAK_BONUS_RATE * 100,
                               DebtSettings.MAX_STREAK_BONUS * 100)
                embed.add_field(
                    name="🔥 Série de Remboursements",
                    value=f"**{new_streak} jour(s)** consécutifs\n"
                          f"Bonus actuel: +{streak_bonus:,} PB\n"
                          f"Prochain bonus: +{next_bonus:.0f}%",
                    inline=True
                )
            
            # Encouragements basés sur la performance
            remaining_debt = repay_info['remaining_debt']
            if remaining_debt == 0:
                embed.add_field(
                    name="🎊 Félicitations !",
                    value="**Tu es libre de dettes !**\n"
                          "Ton score de crédit va s'améliorer.\n"
                          "Tu peux maintenant emprunter à nouveau !",
                    inline=False
                )
            elif level_changed and new_level < old_level:
                embed.add_field(
                    name="📈 Excellent Progrès !",
                    value="Tu améliores ton profil de crédit.\n"
                          "Continue comme ça pour débloquer\n"
                          "des limites d'emprunt plus élevées !",
                    inline=False
                )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="💡 Les remboursements réguliers améliorent ton score de crédit !")
            
            await send_func(embed=embed)
            
            # Mettre à jour les stats
            self._session_stats["manual_repayments"] += 1
            self._session_stats["total_repaid"] += actual_repayment
            
            logger.info(f"Remboursement manuel réussi: {user} -{amount} PB → -{actual_repayment} PB (bonus: {total_bonus})")
            
        except Exception as e:
            logger.error(f"Erreur paydebt {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du remboursement de la dette.")
            await send_func(embed=embed)
    
    @commands.command(name='creditinfo', aliases=['credit', 'credscore'])
    async def credit_info_cmd(self, ctx, user: discord.Member = None):
        """Affiche les informations de crédit détaillées"""
        target = user or ctx.author
        
        try:
            debt_info = await self.debt_manager.get_debt_info(target.id)
            credit_score = debt_info.get("credit_score", "fair")
            credit_multiplier = debt_info.get("credit_multiplier", 1.0)
            
            embed = discord.Embed(
                title=f"📊 Score de Crédit - {target.display_name}",
                color=Colors.INFO
            )
            
            # Informations de base
            embed.add_field(
                name="💳 Score Actuel",
                value=f"**{credit_score.upper()}** (×{credit_multiplier})",
                inline=True
            )
            
            # Impact sur les limites
            base_limits = [config["max_withdraw"] for config in DebtLevel.CONFIGS.values()]
            personal_limits = [int(limit * credit_multiplier) for limit in base_limits]
            
            embed.add_field(
                name="🎯 Impact sur tes Limites",
                value=f"🟢 Excellent: {personal_limits[0]:,} PB\n"
                      f"🟡 Bon: {personal_limits[1]:,} PB\n"
                      f"🟠 Moyen: {personal_limits[2]:,} PB\n"
                      f"🔴 Critique: {personal_limits[3]:,} PB\n"
                      f"💀 Défaillant: {personal_limits[4]:,} PB",
                inline=True
            )
            
            # Facteurs d'amélioration
            embed.add_field(
                name="📈 Comment Améliorer ton Score",
                value="🔸 Rembourse régulièrement tes dettes\n"
                      "🔸 Maintiens un bon ratio remboursement/emprunt\n"
                      "🔸 Conserve une série de paiements\n"
                      "🔸 Reste actif sur le serveur\n"
                      "🔸 Évite les défauts de paiement",
                inline=False
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur creditinfo {target.id}: {e}")
            embed = create_error_embed("Erreur", "Impossible de récupérer les informations de crédit.")
            await ctx.send(embed=embed)
    
    # ==================== COMMANDES ADMIN AMÉLIORÉES ====================
    
    @commands.command(name='debtstats', aliases=['debt_stats'])
    @commands.has_permissions(administrator=True)
    async def debt_stats_cmd(self, ctx):
        """[ADMIN] Statistiques complètes du système"""
        try:
            stats = await self.debt_manager.get_debt_statistics()
            
            if not stats.get("total_users", 0):
                embed = create_warning_embed(
                    "Aucune donnée",
                    "Aucun utilisateur n'a encore utilisé le système de dette optimisé."
                )
                await ctx.send(embed=embed)
                return
            
            embed = discord.Embed(
                title="📊 Statistiques Système de Dette Optimisé",
                color=Colors.INFO
            )
            
            # Statistiques financières
            total_debt = stats['total_debt']
            total_withdrawn = stats['total_withdrawn']
            total_repaid = stats['total_repaid']
            repayment_rate = (total_repaid / total_withdrawn * 100) if total_withdrawn > 0 else 0
            
            embed.add_field(
                name="💰 Vue Financière Globale",
                value=f"**Total en circulation:** {total_debt:,} PB\n"
                      f"**Total emprunté:** {total_withdrawn:,} PB\n"
                      f"**Total remboursé:** {total_repaid:,} PB\n"
                      f"**Taux de remboursement:** {repayment_rate:.1f}%",
                inline=False
            )
            
            # Répartition par niveaux (incluant CRITICAL)
            total_users = stats['total_users']
            level_names = ["🟢 EXCELLENT", "🟡 BON", "🟠 MOYEN", "🔴 CRITIQUE", "💀 DÉFAILLANT"]
            level_counts = [
                stats.get('green_users', 0),
                stats.get('yellow_users', 0), 
                stats.get('orange_users', 0),
                stats.get('red_users', 0),
                stats.get('critical_users', 0)
            ]
            
            distribution = ""
            for i, (name, count) in enumerate(zip(level_names, level_counts)):
                percentage = (count / total_users * 100) if total_users > 0 else 0
                distribution += f"{name}: **{count}** ({percentage:.1f}%)\n"
            
            embed.add_field(
                name="📈 Répartition par Niveaux",
                value=distribution,
                inline=True
            )
            
            # Statistiques de crédit
            credit_dist = stats.get('credit_distribution', {})
            if credit_dist:
                credit_info = ""
                for score, data in credit_dist.items():
                    count = data.get('count', 0)
                    avg_debt = data.get('avg_debt', 0)
                    credit_info += f"**{score.upper()}**: {count} users (dette moy: {avg_debt:,.0f} PB)\n"
                
                embed.add_field(
                    name="💳 Répartition Crédit",
                    value=credit_info or "Aucune donnée",
                    inline=True
                )
            
            # Métriques avancées
            avg_debt = total_debt / total_users if total_users > 0 else 0
            avg_credit = stats.get('avg_credit_multiplier', 1.0)
            
            embed.add_field(
                name="🧮 Métriques Avancées",
                value=f"**Dette moyenne:** {avg_debt:,.0f} PB/user\n"
                      f"**Multiplicateur crédit moyen:** ×{avg_credit:.2f}\n"
                      f"**Utilisateurs actifs:** {total_users}\n"
                      f"**Santé du système:** {'🟢 Excellent' if repayment_rate > 70 else '🟡 Bon' if repayment_rate > 50 else '🔴 Critique'}",
                inline=False
            )
            
            # Stats de session
            embed.add_field(
                name="⚡ Statistiques de Session",
                value=f"• **Remboursements auto:** {self._session_stats['auto_repayments']}\n"
                      f"• **Remboursements manuels:** {self._session_stats['manual_repayments']}\n"
                      f"• **Nouvelles dettes:** {self._session_stats['debts_created']}\n"
                      f"• **PB remboursés:** {self._session_stats['total_repaid']:,}\n"
                      f"• **Intérêts appliqués:** {self._session_stats['interest_applied']}\n"
                      f"• **Scores de crédit mis à jour:** {self._session_stats['credit_updates']}",
                inline=False
            )
            
            embed.set_footer(text="Système de Dette Optimisé v2.0 - Équilibre inflation/accessibilité")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur debtstats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)
    
    @commands.command(name='updatecredit')
    @commands.has_permissions(administrator=True)
    async def update_credit_cmd(self, ctx, user: discord.Member):
        """[ADMIN] Force la mise à jour du score de crédit"""
        try:
            old_score, old_mult = await self.debt_manager._update_credit_score(user.id)
            debt_info = await self.debt_manager.get_debt_info(user.id, use_cache=False)
            new_score = debt_info.get('credit_score', 'fair')
            new_mult = debt_info.get('credit_multiplier', 1.0)
            
            embed = create_success_embed(
                "Score de Crédit Mis à Jour",
                f"**{user.display_name}**\n"
                f"Score: `{new_score.upper()}` (×{new_mult})\n"
                f"*Mise à jour effectuée*"
            )
            
            await ctx.send(embed=embed)
            self._session_stats["credit_updates"] += 1
            
        except Exception as e:
            logger.error(f"Erreur updatecredit: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la mise à jour du crédit.")
            await ctx.send(embed=embed)
    
    # ==================== INTÉGRATIONS AUTOMATIQUES OPTIMISÉES ====================
    
    async def auto_repay_from_message(self, user_id: int) -> bool:
        """Remboursement automatique amélioré via messages"""
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
                
                # Mise à jour crédit si remboursement significatif cumulé
                if self._session_stats["auto_repayments"] % 50 == 0:  # Tous les 50 messages
                    await self.debt_manager._update_credit_score(user_id)
                    self._session_stats["credit_updates"] += 1
                
                return True
                
        except Exception as e:
            logger.error(f"Erreur auto_repay_from_message {user_id}: {e}")
        
        return False
    
    async def auto_repay_from_casino(self, user_id: int, winnings: int) -> int:
        """Prélèvement automatique optimisé sur les gains casino"""
        try:
            debt_info = await self.debt_manager.get_debt_info(user_id)
            current_debt = debt_info.get("total_debt", 0)
            
            if current_debt <= 0:
                return winnings
            
            # Prélèvement réduit pour plus de flexibilité
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
                
                # Mise à jour crédit pour gros remboursements
                if actual_repayment > 1000:
                    await self.debt_manager._update_credit_score(user_id)
                    self._session_stats["credit_updates"] += 1
                
                logger.debug(f"Auto-remboursement casino optimisé: User {user_id} {repayment_amount} PB prélevés sur gains {winnings}")
                return max(0, net_winnings)
            
        except Exception as e:
            logger.error(f"Erreur auto_repay_from_casino {user_id}: {e}")
        
        return winnings
    
    async def create_debt_for_withdrawal(self, user_id: int, amount: int) -> bool:
        """Création de dette avec mise à jour de crédit"""
        try:
            success = await self.debt_manager.create_debt(user_id, amount)
            if success:
                self._session_stats["debts_created"] += 1
                
                # Mise à jour crédit pour gros emprunts
                if amount > 5000:
                    await self.debt_manager._update_credit_score(user_id)
                    self._session_stats["credit_updates"] += 1
            
            return success
        except Exception as e:
            logger.error(f"Erreur create_debt_for_withdrawal {user_id}: {e}")
            return False
    
    async def check_withdrawal_authorization(self, user_id: int, amount: int) -> Tuple[bool, str, float]:
        """Vérification d'autorisation avec limites personnalisées"""
        return await self.debt_manager.can_withdraw(user_id, amount)
    
    # ==================== NOUVELLES COMMANDES UTILITAIRES ====================
    
    @commands.command(name='debthelp')
    async def debt_help_cmd(self, ctx):
        """Guide complet du système de dette optimisé"""
        embed = discord.Embed(
            title="🏦 Guide du Système de Dette Optimisé",
            description="**Système intelligent d'emprunt avec score de crédit**",
            color=Colors.INFO
        )
        
        # Principe de base amélioré
        embed.add_field(
            name="💡 Fonctionnement",
            value="• Chaque retrait crée une **dette équivalente**\n"
                  "• Ton **score de crédit** détermine tes limites personnalisées\n"
                  "• Plus tu rembourses régulièrement, plus tu peux emprunter\n"
                  "• **Objectif:** Équilibre entre accessibilité et stabilité économique",
            inline=False
        )
        
        # Niveaux de dette optimisés
        levels_desc = ""
        for level, config in DebtLevel.CONFIGS.items():
            if level < len(DebtLevel.THRESHOLDS):
                threshold_text = f"0-{DebtLevel.THRESHOLDS[0]:,}" if level == 0 else \
                               f"{DebtLevel.THRESHOLDS[level-1]+1:,}-{DebtLevel.THRESHOLDS[level]:,}"
            else:
                threshold_text = f"{DebtLevel.THRESHOLDS[-1]+1:,}+"
            
            levels_desc += f"{config['name']} **({threshold_text} PB)**\n"
            levels_desc += f"├ Retrait max: {config['max_withdraw']:,} PB (base)\n"
            levels_desc += f"├ Cooldown: ×{config['cooldown_multiplier']}\n"
            levels_desc += f"└ Intérêts: {config['interest_rate']*100:.1f}%/jour\n\n"
        
        embed.add_field(
            name="📊 Niveaux de Dette",
            value=levels_desc,
            inline=False
        )
        
        # Système de crédit
        embed.add_field(
            name="💳 Score de Crédit",
            value=f"🌟 **EXCELLENT** (×{DebtSettings.CREDIT_MULTIPLIERS['excellent']}): Privilèges maximum\n"
                  f"✨ **BON** (×{DebtSettings.CREDIT_MULTIPLIERS['good']}): Avantages modérés\n"
                  f"📊 **MOYEN** (×{DebtSettings.CREDIT_MULTIPLIERS['fair']}): Conditions standard\n"
                  f"⚠️ **FAIBLE** (×{DebtSettings.CREDIT_MULTIPLIERS['poor']}): Limites réduites\n\n"
                  f"*Basé sur: historique de remboursement, régularité, ancienneté, activité*",
            inline=False
        )
        
        # Options de remboursement
        embed.add_field(
            name="💰 Remboursements Améliorés",
            value=f"🔸 **Automatique (Messages):** {DebtSettings.AUTO_REPAY_MESSAGE} PB par message actif\n"
                  f"🔸 **Automatique (Casino):** {DebtSettings.AUTO_REPAY_CASINO_RATE*100:.0f}% des gains prélevés\n"
                  f"🔸 **Manuel:** `{PREFIX}paydebt <montant>` (+{DebtSettings.MANUAL_REPAY_BONUS*100:.0f}% bonus)\n"
                  f"🔸 **Streak:** Jusqu'à +{DebtSettings.MAX_STREAK_BONUS*100:.0f}% bonus (série de {7} jours)\n"
                  f"🔸 **Intérêts:** Appliqués quotidiennement selon ton niveau",
            inline=False
        )
        
        # Période de grâce étendue
        embed.add_field(
            name="🎁 Période de Grâce",
            value=f"Les nouveaux utilisateurs bénéficient de **{DebtSettings.GRACE_PERIOD_HOURS//24} jours** d'accès privilégié\n"
                  f"pour s'adapter au système sans pénalités.\n"
                  f"*Limite temporaire: 2,000 PB par retrait*",
            inline=False
        )
        
        # Commandes disponibles
        embed.add_field(
            name="🔧 Commandes Disponibles",
            value=f"• `{PREFIX}debt` ou `/debt` - Ton profil complet\n"
                  f"• `{PREFIX}paydebt <montant>` - Remboursement avec bonus\n"
                  f"• `{PREFIX}creditinfo` - Détails de ton score de crédit\n"
                  f"• `{PREFIX}debthelp` - Ce guide complet",
            inline=False
        )
        
        # Exemple concret optimisé
        embed.add_field(
            name="📝 Exemple Pratique",
            value="**Marie (crédit BON, ×1.2) retire 8,000 PB**\n"
                  "→ Dette 8,000 PB (🟡 Niveau BON)\n"
                  "→ Nouvelle limite: 1,440 PB par retrait (1,200 × 1.2)\n"
                  "→ **Activité:** 100 messages → -200 PB dette\n"
                  "→ **Casino:** Gagne 2,000 PB → -300 PB dette auto\n"
                  "→ **Manuel:** Paie 1,000 PB → -1,150 PB dette (bonus 15%)\n"
                  "→ **Résultat:** Dette 6,350 PB, amélioration de crédit possible",
            inline=False
        )
        
        embed.set_footer(text="💡 Système équilibré • Plus de responsabilité = Plus d'opportunités")
        await ctx.send(embed=embed)
    
    @commands.command(name='debtcalc', aliases=['calc_debt'])
    async def debt_calculator_cmd(self, ctx, amount: int):
        """Calculateur de dette - montre l'impact d'un emprunt"""
        if amount <= 0 or amount > 100000:
            embed = create_error_embed("Montant invalide", "Le montant doit être entre 1 et 100,000 PB.")
            await ctx.send(embed=embed)
            return
        
        try:
            user_id = ctx.author.id
            current_debt_info = await self.debt_manager.get_debt_info(user_id)
            current_debt = current_debt_info.get("total_debt", 0)
            credit_multiplier = current_debt_info.get("credit_multiplier", 1.0)
            credit_score = current_debt_info.get("credit_score", "fair")
            
            # Calculer la nouvelle dette et niveau
            new_total_debt = current_debt + amount
            new_level = DebtLevel.get_level_from_debt(new_total_debt)
            new_config = DebtLevel.CONFIGS[new_level]
            
            embed = discord.Embed(
                title="🧮 Calculateur de Dette",
                description=f"Impact d'un emprunt de **{amount:,} PB**",
                color=Colors.INFO
            )
            
            embed.add_field(
                name="📊 Situation Actuelle",
                value=f"**Dette actuelle:** {current_debt:,} PB\n"
                      f"**Score crédit:** {credit_score.upper()} (×{credit_multiplier})",
                inline=True
            )
            
            embed.add_field(
                name="📈 Après Emprunt",
                value=f"**Nouvelle dette:** {new_total_debt:,} PB\n"
                      f"**Nouveau niveau:** {new_config['name']}",
                inline=True
            )
            
            # Nouvelles limites
            base_limit = new_config['max_withdraw']
            personal_limit = int(base_limit * credit_multiplier)
            daily_interest = int(new_total_debt * new_config['interest_rate'])
            
            embed.add_field(
                name="⚖️ Nouvelles Conditions",
                value=f"**Limite de retrait:** {personal_limit:,} PB\n"
                      f"**Cooldown:** ×{new_config['cooldown_multiplier']}\n"
                      f"**Intérêts quotidiens:** {daily_interest:,} PB ({new_config['interest_rate']*100:.1f}%)",
                inline=False
            )
            
            # Estimation remboursement
            monthly_messages = 30 * 10  # 10 messages par jour estimé
            monthly_auto_repay = monthly_messages * DebtSettings.AUTO_REPAY_MESSAGE
            
            embed.add_field(
                name="💡 Estimation Remboursement",
                value=f"**Via messages** (10/jour): {monthly_auto_repay:,} PB/mois\n"
                      f"**Manuel recommandé:** {int(new_total_debt * 0.1):,} PB/mois\n"
                      f"**Durée estimée:** {int(new_total_debt / (monthly_auto_repay + int(new_total_debt * 0.05))):.0f} mois",
                inline=False
            )
            
            # Avertissement si niveau critique
            if new_level >= DebtLevel.RED:
                embed.add_field(
                    name="⚠️ Attention",
                    value=f"Cet emprunt te fera passer en **{new_config['name']}**\n"
                          f"Assure-toi de pouvoir rembourser régulièrement !",
                    inline=False
                )
            
            embed.set_footer(text="💡 Simulation basée sur ton profil actuel")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur debtcalc {ctx.author.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du calcul.")
            await ctx.send(embed=embed)
    
    # ==================== COMMANDES DE DEBUG/MAINTENANCE ====================
    
    @commands.command(name='testdebt')
    @commands.is_owner()
    async def test_debt_cmd(self, ctx, user: discord.Member = None, amount: int = 5000):
        """[OWNER] Test du système de dette optimisé"""
        target = user or ctx.author
        
        try:
            # Créer une dette de test
            await self.debt_manager.create_debt(target.id, amount)
            
            # Mettre à jour le crédit
            await self.debt_manager._update_credit_score(target.id)
            
            # Simuler un remboursement avec bonus
            test_repay = min(500, amount // 2)
            await self.debt_manager.repay_debt(target.id, test_repay, "test", apply_bonus=True)
            
            # Récupérer les infos finales
            final_info = await self.debt_manager.get_debt_info(target.id, use_cache=False)
            
            embed = create_success_embed(
                "Test Dette Optimisée",
                f"**{target.display_name}**\n"
                f"Dette créée: {amount:,} PB\n"
                f"Remboursement test: {test_repay:,} PB\n"
                f"Dette finale: {final_info.get('total_debt', 0):,} PB\n"
                f"Score crédit: {final_info.get('credit_score', 'fair').upper()}\n"
                f"Multiplicateur: ×{final_info.get('credit_multiplier', 1.0)}"
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            await ctx.send(f"❌ Erreur test: {e}")
    
    @commands.command(name='resetdebt')
    @commands.is_owner()
    async def reset_debt_cmd(self, ctx, user: discord.Member):
        """[OWNER] Remet à zéro la dette et recalcule le crédit"""
        try:
            if not self.db.pool:
                await ctx.send("❌ Base de données non disponible")
                return
            
            async with self.db.pool.acquire() as conn:
                # Reset complet avec nouvelles colonnes
                await conn.execute("""
                    UPDATE public_bank_debts SET
                    total_debt = 0,
                    debt_level = 0,
                    payment_streak = 0,
                    credit_score = 'fair',
                    credit_multiplier = 1.0,
                    monthly_repaid = 0,
                    last_monthly_reset = NOW()
                    WHERE user_id = $1
                """, user.id)
            
            # Invalider tous les caches
            if user.id in self.debt_manager._debt_cache:
                del self.debt_manager._debt_cache[user.id]
                del self.debt_manager._cache_expiry[user.id]
            if user.id in self.debt_manager._credit_cache:
                del self.debt_manager._credit_cache[user.id]
            
            # Recalculer le crédit
            await self.debt_manager._update_credit_score(user.id)
            
            embed = create_success_embed(
                "Dette Réinitialisée",
                f"**{user.display_name}**\n"
                f"✅ Dette remise à zéro\n"
                f"✅ Score de crédit recalculé\n"
                f"✅ Cache invalidé"
            )
            await ctx.send(embed=embed)
            
            logger.info(f"OWNER {ctx.author} a réinitialisé la dette de {user}")
            
        except Exception as e:
            logger.error(f"Erreur resetdebt: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la réinitialisation.")
            await ctx.send(embed=embed)
    
    @commands.command(name='migratedebt')
    @commands.is_owner()
    async def migrate_debt_cmd(self, ctx):
        """[OWNER] Migration vers le nouveau système optimisé"""
        try:
            if not self.db.pool:
                await ctx.send("❌ Base de données non disponible")
                return
            
            async with self.db.pool.acquire() as conn:
                # Compter les utilisateurs à migrer
                count = await conn.fetchval("""
                    SELECT COUNT(*) FROM public_bank_debts 
                    WHERE credit_score IS NULL OR credit_multiplier IS NULL
                """)
                
                if count == 0:
                    await ctx.send("✅ Aucune migration nécessaire")
                    return
                
                # Migrer les utilisateurs
                users = await conn.fetch("""
                    SELECT user_id FROM public_bank_debts 
                    WHERE total_withdrawn > 0 OR total_debt > 0
                """)
                
                migrated = 0
                for user in users:
                    try:
                        await self.debt_manager._update_credit_score(user['user_id'])
                        migrated += 1
                    except Exception as e:
                        logger.error(f"Erreur migration user {user['user_id']}: {e}")
                
                embed = create_success_embed(
                    "Migration Terminée",
                    f"**{migrated}/{len(users)}** utilisateurs migrés vers le système optimisé"
                )
                await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur migration: {e}")
            await ctx.send(f"❌ Erreur migration: {e}")
    
    # ==================== TÂCHES AUTOMATIQUES ====================
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Initialisation des tâches automatiques"""
        if not hasattr(self, '_tasks_started'):
            self._tasks_started = True
            # Démarrer les tâches de maintenance quotidiennes
            # (Dans un vrai système, utiliser discord.ext.tasks)
            logger.info("🔄 Système de Dette Optimisé prêt")


async def setup(bot):
    """Fonction appelée pour charger le cog optimisé"""
    await bot.add_cog(DebtSystem(bot))
