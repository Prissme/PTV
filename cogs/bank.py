import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import math
from datetime import datetime, timezone

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_info_embed

logger = logging.getLogger(__name__)

class Bank(commands.Cog):
    """Système de banque privée RÉÉQUILIBRÉ - Limites strictes et frais de maintenance pour éviter la thésaurisation"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # NOUVELLE Configuration restrictive anti-thésaurisation
        self.MIN_DEPOSIT = 1
        self.MIN_WITHDRAW = 1
        self.MAX_TRANSACTION = 50000  # Réduit de 1M à 50K
        self.MAX_TOTAL_BANK_BALANCE = 15000  # DRASTIQUEMENT RÉDUIT : 15K max au lieu de 100M !
        
        # NOUVEAU: Système de frais bancaires
        self.DAILY_BANK_FEE_RATE = 0.02  # 2% de frais par jour
        self.MIN_BALANCE_FOR_FEES = 500  # Frais seulement si > 500 PB
        self.DEPOSIT_TAX_RATE = 0.02  # 2% de taxe sur les dépôts (coût de sécurité)
        
        # NOUVEAU: Limites quotidiennes pour éviter l'accumulation
        self.MAX_DAILY_DEPOSITS = 5000  # 5K max de dépôts par jour par utilisateur
        
        # Dictionnaires pour gérer les cooldowns et limites quotidiennes
        self.bank_cooldowns = {}
        self.daily_deposit_limits = {}  # {user_id: {'date': date, 'deposited': amount}}
        
        # Démarrer les tâches automatiques
        self.daily_bank_fees.start()
        self.cleanup_daily_limits.start()
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        await self.create_bank_table()
        logger.info("✅ Cog Bank RÉÉQUILIBRÉ initialisé - Banque restrictive anti-thésaurisation avec frais quotidiens")
    
    async def cog_unload(self):
        """Arrêter les tâches lors du déchargement"""
        self.daily_bank_fees.cancel()
        self.cleanup_daily_limits.cancel()

    async def create_bank_table(self):
        """Crée la table pour stocker les comptes bancaires"""
        if not self.db.pool:
            return
            
        async with self.db.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_bank (
                    user_id BIGINT PRIMARY KEY,
                    balance BIGINT DEFAULT 0 CHECK (balance >= 0),
                    total_deposited BIGINT DEFAULT 0 CHECK (total_deposited >= 0),
                    total_withdrawn BIGINT DEFAULT 0 CHECK (total_withdrawn >= 0),
                    total_fees_paid BIGINT DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    last_activity TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    last_fee_payment TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            
            # Ajouter la colonne des frais si elle n'existe pas (migration)
            try:
                await conn.execute('ALTER TABLE user_bank ADD COLUMN IF NOT EXISTS total_fees_paid BIGINT DEFAULT 0')
                await conn.execute('ALTER TABLE user_bank ADD COLUMN IF NOT EXISTS last_fee_payment TIMESTAMP WITH TIME ZONE DEFAULT NOW()')
            except:
                pass
            
            # Index pour optimiser les requêtes
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_user_bank_user_id ON user_bank(user_id)
            ''')
            
            # Contrainte pour éviter les balances négatives
            await conn.execute('''
                CREATE OR REPLACE FUNCTION prevent_negative_bank_balance() 
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.balance < 0 THEN
                        RAISE EXCEPTION 'Balance bancaire ne peut pas être négative: %', NEW.balance;
                    END IF;
                    IF NEW.total_deposited < 0 THEN
                        RAISE EXCEPTION 'Total déposé ne peut pas être négatif: %', NEW.total_deposited;
                    END IF;
                    IF NEW.total_withdrawn < 0 THEN
                        RAISE EXCEPTION 'Total retiré ne peut pas être négatif: %', NEW.total_withdrawn;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            ''')
            
            await conn.execute('''
                DROP TRIGGER IF EXISTS trg_prevent_negative_bank_balance ON user_bank;
                CREATE TRIGGER trg_prevent_negative_bank_balance
                    BEFORE INSERT OR UPDATE ON user_bank
                    FOR EACH ROW EXECUTE FUNCTION prevent_negative_bank_balance();
            ''')
            
            logger.info("✅ Table user_bank créée/vérifiée avec système de frais intégré")

    # ==================== NOUVELLES TÂCHES AUTOMATIQUES ====================

    @tasks.loop(hours=24)  # Tous les jours à la même heure
    async def daily_bank_fees(self):
        """Applique les frais de maintenance bancaire quotidiens"""
        if not self.db.pool:
            return
            
        try:
            async with self.db.pool.acquire() as conn:
                # Récupérer tous les comptes avec solde > minimum
                accounts = await conn.fetch("""
                    SELECT user_id, balance 
                    FROM user_bank 
                    WHERE balance > $1
                """, self.MIN_BALANCE_FOR_FEES)
                
                total_fees_collected = 0
                accounts_affected = 0
                
                for account in accounts:
                    user_id = account['user_id']
                    current_balance = account['balance']
                    
                    # Calculer les frais (2% par jour)
                    fee = int(current_balance * self.DAILY_BANK_FEE_RATE)
                    new_balance = max(0, current_balance - fee)
                    
                    # Appliquer les frais
                    await conn.execute("""
                        UPDATE user_bank 
                        SET balance = $1, 
                            total_fees_paid = total_fees_paid + $2,
                            last_activity = NOW(),
                            last_fee_payment = NOW()
                        WHERE user_id = $3
                    """, new_balance, fee, user_id)
                    
                    # Envoyer les frais vers la banque publique
                    public_bank_cog = self.bot.get_cog('PublicBank')
                    if public_bank_cog and hasattr(public_bank_cog, 'add_casino_loss'):
                        await public_bank_cog.add_casino_loss(fee, "bank_maintenance_fees")
                    
                    # Logger la transaction
                    if hasattr(self.bot, 'transaction_logs'):
                        await self.bot.transaction_logs.log_transaction(
                            user_id=user_id,
                            transaction_type='bank_fees',
                            amount=-fee,
                            balance_before=current_balance,  # Solde principal (pas affecté)
                            balance_after=current_balance,
                            description=f"Frais maintenance bancaire ({self.DAILY_BANK_FEE_RATE*100:.0f}%/jour)"
                        )
                    
                    total_fees_collected += fee
                    accounts_affected += 1
                
                logger.info(f"🏦 Frais bancaires quotidiens: {total_fees_collected:,} PB collectés sur {accounts_affected} comptes → Banque publique")
                
        except Exception as e:
            logger.error(f"Erreur frais bancaires quotidiens: {e}")

    @daily_bank_fees.before_loop
    async def before_daily_bank_fees(self):
        """Attendre que le bot soit prêt"""
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)  # Toutes les heures
    async def cleanup_daily_limits(self):
        """Nettoie les limites quotidiennes expirées"""
        today = datetime.now(timezone.utc).date()
        expired_users = []
        
        for user_id, data in self.daily_deposit_limits.items():
            if data['date'] != today:
                expired_users.append(user_id)
        
        for user_id in expired_users:
            del self.daily_deposit_limits[user_id]
        
        if expired_users:
            logger.debug(f"Nettoyage limites quotidiennes: {len(expired_users)} entrées supprimées")

    @cleanup_daily_limits.before_loop
    async def before_cleanup_daily_limits(self):
        await self.bot.wait_until_ready()

    # ==================== NOUVELLES MÉTHODES DE GESTION ====================

    def _check_bank_cooldown(self, user_id: int) -> float:
        """Vérifie et retourne le cooldown restant pour les opérations bancaires"""
        import time
        now = time.time()
        cooldown_duration = 3  # 3 secondes de cooldown
        if user_id in self.bank_cooldowns:
            elapsed = now - self.bank_cooldowns[user_id]
            if elapsed < cooldown_duration:
                return cooldown_duration - elapsed
        self.bank_cooldowns[user_id] = now
        return 0

    def _check_daily_deposit_limit(self, user_id: int, amount: int) -> tuple:
        """Vérifie la limite quotidienne de dépôts - retourne (allowed, remaining)"""
        today = datetime.now(timezone.utc).date()
        
        if user_id not in self.daily_deposit_limits:
            self.daily_deposit_limits[user_id] = {'date': today, 'deposited': 0}
        
        user_data = self.daily_deposit_limits[user_id]
        if user_data['date'] != today:
            # Nouveau jour, reset le compteur
            user_data['date'] = today
            user_data['deposited'] = 0
        
        deposited_today = user_data['deposited']
        remaining = max(0, self.MAX_DAILY_DEPOSITS - deposited_today)
        
        if amount <= remaining:
            return True, remaining - amount
        else:
            return False, remaining

    def _add_daily_deposit(self, user_id: int, amount: int):
        """Ajoute un montant aux dépôts quotidiens"""
        today = datetime.now(timezone.utc).date()
        if user_id not in self.daily_deposit_limits:
            self.daily_deposit_limits[user_id] = {'date': today, 'deposited': 0}
        
        if self.daily_deposit_limits[user_id]['date'] == today:
            self.daily_deposit_limits[user_id]['deposited'] += amount
        else:
            self.daily_deposit_limits[user_id] = {'date': today, 'deposited': amount}

    def _validate_amount(self, amount: int, operation: str) -> tuple:
        """Valide un montant pour une opération bancaire avec nouvelles limites"""
        if not isinstance(amount, int):
            return False, "Le montant doit être un nombre entier."
        
        if amount <= 0:
            return False, "Le montant doit être positif."
            
        if operation == "deposit":
            if amount < self.MIN_DEPOSIT:
                return False, f"Le montant minimum de dépôt est {self.MIN_DEPOSIT} PrissBuck."
        elif operation == "withdraw":
            if amount < self.MIN_WITHDRAW:
                return False, f"Le montant minimum de retrait est {self.MIN_WITHDRAW} PrissBuck."
        
        if amount > self.MAX_TRANSACTION:
            return False, f"Le montant maximum par transaction est {self.MAX_TRANSACTION:,} PrissBucks."
            
        # Protection contre les overflow/underflow
        if amount > 2**53:
            return False, "Montant trop élevé pour être traité en sécurité."
            
        return True, ""

    # ==================== MÉTHODES DE BASE SÉCURISÉES ====================

    async def get_bank_balance(self, user_id: int) -> int:
        """Récupère le solde bancaire d'un utilisateur de façon sécurisée"""
        if not self.db.pool:
            return 0
        
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow("SELECT balance FROM user_bank WHERE user_id = $1", user_id)
                balance = row["balance"] if row else 0
                
                if balance < 0:
                    logger.error(f"SÉCURITÉ: Balance bancaire négative détectée pour {user_id}: {balance}")
                    await conn.execute("UPDATE user_bank SET balance = 0 WHERE user_id = $1", user_id)
                    return 0
                    
                return balance
        except Exception as e:
            logger.error(f"Erreur get_bank_balance {user_id}: {e}")
            return 0

    async def get_bank_stats(self, user_id: int) -> dict:
        """Récupère les statistiques bancaires complètes d'un utilisateur"""
        if not self.db.pool:
            return {
                "balance": 0, "total_deposited": 0, "total_withdrawn": 0, 
                "total_fees_paid": 0, "created_at": None, "last_activity": None
            }
        
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT balance, total_deposited, total_withdrawn, total_fees_paid,
                           created_at, last_activity, last_fee_payment
                    FROM user_bank WHERE user_id = $1
                """, user_id)
                
                if row:
                    stats = dict(row)
                    # Vérifications de sécurité
                    for field in ['balance', 'total_deposited', 'total_withdrawn', 'total_fees_paid']:
                        if stats.get(field, 0) < 0:
                            logger.error(f"SÉCURITÉ: {field} négatif pour {user_id}")
                            stats[field] = 0
                    return stats
                else:
                    return {
                        "balance": 0, "total_deposited": 0, "total_withdrawn": 0,
                        "total_fees_paid": 0, "created_at": None, "last_activity": None
                    }
        except Exception as e:
            logger.error(f"Erreur get_bank_stats {user_id}: {e}")
            return {
                "balance": 0, "total_deposited": 0, "total_withdrawn": 0,
                "total_fees_paid": 0, "created_at": None, "last_activity": None
            }

    async def update_bank_balance(self, user_id: int, amount: int, operation_type: str) -> bool:
        """Met à jour le solde bancaire et les statistiques avec vérifications de sécurité"""
        if not self.db.pool:
            return False
            
        if not isinstance(amount, int) or amount == 0:
            logger.error(f"SÉCURITÉ: Montant invalide pour {user_id}: {amount}")
            return False
            
        if operation_type not in ["deposit", "withdraw"]:
            logger.error(f"SÉCURITÉ: Type d'opération invalide: {operation_type}")
            return False
            
        now = datetime.now(timezone.utc)
        
        try:
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    account = await conn.fetchrow(
                        "SELECT * FROM user_bank WHERE user_id = $1 FOR UPDATE", 
                        user_id
                    )
                    
                    if not account:
                        if operation_type == "deposit" and amount > 0:
                            if amount > self.MAX_TOTAL_BANK_BALANCE:
                                logger.warning(f"SÉCURITÉ: Dépôt initial dépassant la limite pour {user_id}: {amount}")
                                return False
                                
                            await conn.execute("""
                                INSERT INTO user_bank (user_id, balance, total_deposited, last_activity)
                                VALUES ($1, $2, $3, $4)
                            """, user_id, amount, amount, now)
                            logger.info(f"Bank: Nouveau compte créé pour {user_id} avec dépôt initial {amount}")
                            return True
                        elif operation_type == "withdraw":
                            return False
                    else:
                        current_balance = account["balance"]
                        current_deposited = account["total_deposited"]
                        current_withdrawn = account["total_withdrawn"]
                        
                        # Vérifications de sécurité
                        if current_balance < 0:
                            logger.error(f"SÉCURITÉ: Balance corrompue pour {user_id}: {current_balance}")
                            current_balance = 0
                        
                        if operation_type == "deposit" and amount > 0:
                            new_balance = current_balance + amount
                            new_deposited = current_deposited + amount
                            
                            # Vérifier les limites strictes
                            if new_balance > self.MAX_TOTAL_BANK_BALANCE:
                                logger.warning(f"SÉCURITÉ: Dépôt dépasserait la limite pour {user_id}: {new_balance}")
                                return False
                            
                            if new_balance < current_balance:
                                logger.error(f"SÉCURITÉ: Débordement détecté pour {user_id}")
                                return False
                                
                            await conn.execute("""
                                UPDATE user_bank 
                                SET balance = $1, total_deposited = $2, last_activity = $3
                                WHERE user_id = $4
                            """, new_balance, new_deposited, now, user_id)
                            return True
                            
                        elif operation_type == "withdraw" and amount > 0:
                            if current_balance >= amount:
                                new_balance = current_balance - amount
                                new_withdrawn = current_withdrawn + amount
                                
                                if new_balance < 0:
                                    logger.error(f"SÉCURITÉ: Retrait créerait une balance négative pour {user_id}")
                                    return False
                                    
                                await conn.execute("""
                                    UPDATE user_bank 
                                    SET balance = $1, total_withdrawn = $2, last_activity = $3
                                    WHERE user_id = $4
                                """, new_balance, new_withdrawn, now, user_id)
                                return True
                            else:
                                return False
                        # Cas spécial pour les frais (montant négatif)
                        elif operation_type == "withdraw" and amount < 0:
                            fee_amount = abs(amount)
                            if current_balance >= fee_amount:
                                new_balance = current_balance - fee_amount
                                await conn.execute("""
                                    UPDATE user_bank 
                                    SET balance = $1, total_fees_paid = total_fees_paid + $2, 
                                        last_activity = $3, last_fee_payment = $3
                                    WHERE user_id = $4
                                """, new_balance, fee_amount, now, user_id)
                                return True
                            else:
                                return False
                    
                    return False
        except Exception as e:
            logger.error(f"Erreur critique update_bank_balance {user_id}: {e}")
            return False

    # ==================== COMMANDES BANQUE ====================

    @commands.command(name='bank', aliases=['banque'])
    async def bank_cmd(self, ctx):
        """e!bank - Affiche tes informations bancaires privées"""
        await self._execute_bank_info(ctx, ctx.author)

    @app_commands.command(name="bank", description="Affiche tes informations bancaires privées")
    async def bank_slash(self, interaction: discord.Interaction):
        """/bank - Affiche tes infos bancaires"""
        await interaction.response.defer(ephemeral=True)
        await self._execute_bank_info(interaction, interaction.user, is_slash=True)

    async def _execute_bank_info(self, ctx_or_interaction, user, is_slash=False):
        """Logique commune pour afficher les infos bancaires avec avertissements sur les frais"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
        else:
            try:
                send_func = user.send
            except:
                send_func = ctx_or_interaction.send
        
        try:
            bank_stats = await self.get_bank_stats(user.id)
            main_balance = await self.db.get_balance(user.id)
            
            # Vérifier la limite quotidienne de dépôts
            today_allowed, remaining_today = self._check_daily_deposit_limit(user.id, 0)
            deposited_today = self.MAX_DAILY_DEPOSITS - remaining_today if user.id in self.daily_deposit_limits else 0
            
            embed = discord.Embed(
                title="🏦 Ta Banque Privée RÉÉQUILIBRÉE",
                description=f"**{user.display_name}** - Compte avec frais de maintenance",
                color=Colors.WARNING if bank_stats['balance'] > self.MIN_BALANCE_FOR_FEES else Colors.PREMIUM
            )
            
            # Soldes avec avertissements
            embed.add_field(
                name="💰 Solde bancaire",
                value=f"**{bank_stats['balance']:,}** PrissBucks" + 
                      (f"\n⚠️ **Frais quotidiens actifs !**" if bank_stats['balance'] > self.MIN_BALANCE_FOR_FEES else ""),
                inline=True
            )
            
            embed.add_field(
                name="💳 Solde principal",
                value=f"**{main_balance:,}** PrissBucks",
                inline=True
            )
            
            total_wealth = bank_stats['balance'] + main_balance
            embed.add_field(
                name="💎 Fortune totale",
                value=f"**{total_wealth:,}** PrissBucks",
                inline=True
            )
            
            # Statistiques historiques avec frais
            embed.add_field(
                name="📈 Total déposé",
                value=f"**{bank_stats['total_deposited']:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📉 Total retiré",
                value=f"**{bank_stats['total_withdrawn']:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="💸 Frais payés",
                value=f"**{bank_stats.get('total_fees_paid', 0):,}** PrissBucks",
                inline=True
            )
            
            # NOUVELLE SECTION: Limites strictes
            remaining_capacity = self.MAX_TOTAL_BANK_BALANCE - bank_stats['balance']
            embed.add_field(
                name="⚖️ Limites bancaires strictes",
                value=f"📊 **Capacité restante:** {remaining_capacity:,} PB\n"
                      f"📅 **Dépôts restants aujourd'hui:** {remaining_today:,} PB\n"
                      f"💰 **Maximum total:** {self.MAX_TOTAL_BANK_BALANCE:,} PB",
                inline=False
            )
            
            # NOUVELLE SECTION: Système de frais
            if bank_stats['balance'] > self.MIN_BALANCE_FOR_FEES:
                daily_fee = int(bank_stats['balance'] * self.DAILY_BANK_FEE_RATE)
                embed.add_field(
                    name="⚠️ Frais de maintenance actifs",
                    value=f"💸 **{self.DAILY_BANK_FEE_RATE*100:.0f}%** par jour (si > {self.MIN_BALANCE_FOR_FEES:,} PB)\n"
                          f"💰 **Frais quotidiens actuels:** {daily_fee:,} PB\n"
                          f"🏛️ **Destination:** Banque publique (récupérable !)\n"
                          f"💡 **Conseil:** Garde moins de {self.MIN_BALANCE_FOR_FEES:,} PB pour éviter les frais",
                    inline=False
                )
            else:
                embed.add_field(
                    name="✅ Pas de frais de maintenance",
                    value=f"Tu es en dessous du seuil de {self.MIN_BALANCE_FOR_FEES:,} PB\n"
                          f"Aucun frais quotidien appliqué ! 🎉",
                    inline=False
                )
            
            # Calcul du ratio sécurité avec avertissement
            if total_wealth > 0:
                security_ratio = (bank_stats['balance'] / total_wealth) * 100
                embed.add_field(
                    name="🔒 Sécurisation",
                    value=f"**{security_ratio:.1f}%** en banque" +
                          (f"\n⚡ **Nouvelle stratégie:** Garde plus sur compte principal !" if security_ratio > 50 else ""),
                    inline=True
                )
            
            # Instructions d'utilisation avec nouvelles limites
            embed.add_field(
                name="🔧 Comment utiliser",
                value=f"• `{PREFIX}deposit <montant>` - Déposer (taxe {self.DEPOSIT_TAX_RATE*100:.0f}%)\n"
                      f"• `{PREFIX}withdraw <montant>` - Retirer sans frais\n"
                      f"• `{PREFIX}bank` - Voir tes infos (privées)\n"
                      f"• **NOUVEAU:** Limites strictes et frais quotidiens !",
                inline=False
            )
            
            # Message d'avertissement sur la stratégie
            embed.add_field(
                name="🎯 NOUVELLE STRATÉGIE ÉCONOMIQUE",
                value="⚠️ **La banque n'est plus un coffre-fort gratuit !**\n"
                      "💡 **Garde de la liquidité** pour de meilleurs daily\n"
                      "🎰 **Joue au casino** avec tes fonds disponibles\n"
                      "🏛️ **Les frais vont en banque publique** (récupérables !)",
                inline=False
            )
            
            if bank_stats['created_at']:
                embed.set_footer(text=f"Compte créé le {bank_stats['created_at'].strftime('%d/%m/%Y')} • Frais: {self.DAILY_BANK_FEE_RATE*100:.0f}%/jour • Max: {self.MAX_TOTAL_BANK_BALANCE:,} PB")
            else:
                embed.set_footer(text=f"Utilise 'deposit' pour créer ton compte • Frais: {self.DAILY_BANK_FEE_RATE*100:.0f}%/jour • Max: {self.MAX_TOTAL_BANK_BALANCE:,} PB")
            
            embed.set_thumbnail(url=user.display_avatar.url)
            
            if is_slash:
                await send_func(embed=embed, ephemeral=True)
            else:
                try:
                    await user.send(embed=embed)
                    if ctx_or_interaction.guild:
                        await ctx_or_interaction.send("🏦 Tes informations bancaires (avec nouvelles limites) t'ont été envoyées en privé ! 📨")
                except:
                    await ctx_or_interaction.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur bank info {user.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des informations bancaires.")
            await send_func(embed=embed, ephemeral=True if is_slash else False)

    # ==================== DÉPÔT AVEC LIMITES ET TAXES ====================

    @commands.command(name='deposit', aliases=['depot', 'depo'])
    async def deposit_cmd(self, ctx, amount: int):
        """e!deposit <montant> - Dépose des PrissBucks en banque (avec limites strictes et taxe)"""
        await self._execute_deposit(ctx, amount)

    @app_commands.command(name="deposit", description="Dépose des PrissBucks dans ta banque (limites: 15K max, 5K/jour, taxe 2%)")
    @app_commands.describe(amount="Montant à déposer en PrissBucks")
    async def deposit_slash(self, interaction: discord.Interaction, amount: int):
        """/deposit <amount> - Dépose en banque"""
        cooldown_remaining = self._check_bank_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            embed = create_error_embed(
                "Cooldown actif", 
                f"Patiente **{cooldown_remaining:.1f}s** avant la prochaine opération bancaire."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        await self._execute_deposit(interaction, amount, is_slash=True)

    async def _execute_deposit(self, ctx_or_interaction, amount, is_slash=False):
        """Logique commune pour les dépôts avec nouvelles limites strictes et taxe"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        user_id = user.id

        # Validations de sécurité
        valid, error_msg = self._validate_amount(amount, "deposit")
        if not valid:
            embed = create_error_embed("Montant invalide", error_msg)
            await send_func(embed=embed)
            return

        # NOUVEAU: Vérifier la limite quotidienne de dépôts
        allowed, remaining_after = self._check_daily_deposit_limit(user_id, amount)
        if not allowed:
            deposited_today = self.MAX_DAILY_DEPOSITS - self._check_daily_deposit_limit(user_id, 0)[1]
            embed = create_error_embed(
                "Limite quotidienne atteinte",
                f"Tu as déjà déposé **{deposited_today:,} PB** aujourd'hui.\n"
                f"Limite quotidienne: **{self.MAX_DAILY_DEPOSITS:,} PB**\n"
                f"Tu peux encore déposer: **{self._check_daily_deposit_limit(user_id, 0)[1]:,} PB** aujourd'hui."
            )
            embed.add_field(
                name="💡 Pourquoi cette limite ?",
                value="Pour éviter l'accumulation massive et encourager la circulation d'argent !\n"
                      f"🔄 Limite remise à zéro chaque jour à minuit UTC.",
                inline=False
            )
            await send_func(embed=embed)
            return

        try:
            # Récupérer les soldes AVANT l'opération
            main_balance_before = await self.db.get_balance(user_id)
            bank_balance_before = await self.get_bank_balance(user_id)
            
            if main_balance_before < 0:
                logger.error(f"SÉCURITÉ: Solde principal négatif détecté pour {user_id}: {main_balance_before}")
                embed = create_error_embed("Erreur de sécurité", "Solde principal corrompu détecté. Contactez un admin.")
                await send_func(embed=embed)
                return
            
            # NOUVEAU: Calculer la taxe de dépôt
            deposit_tax = int(amount * self.DEPOSIT_TAX_RATE)
            total_cost = amount + deposit_tax
            
            # Vérifier le solde principal (coût total avec taxe)
            if main_balance_before < total_cost:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"**Coût total du dépôt:** {total_cost:,} PrissBucks\n"
                    f"• Dépôt: {amount:,} PB\n"
                    f"• Taxe sécurité ({self.DEPOSIT_TAX_RATE*100:.0f}%): {deposit_tax:,} PB\n\n"
                    f"Tu as: **{main_balance_before:,}** PrissBucks"
                )
                embed.add_field(
                    name="💡 Nouvelle réalité",
                    value="Les dépôts coûtent maintenant une taxe de sécurité !\n"
                          "🏦 Les banques ne sont plus gratuites.",
                    inline=False
                )
                await send_func(embed=embed)
                return
            
            # Vérifier la limite de capacité bancaire
            if bank_balance_before + amount > self.MAX_TOTAL_BANK_BALANCE:
                remaining = self.MAX_TOTAL_BANK_BALANCE - bank_balance_before
                embed = create_error_embed(
                    "LIMITE BANCAIRE ATTEINTE",
                    f"**NOUVELLE LIMITE:** {self.MAX_TOTAL_BANK_BALANCE:,} PB maximum par compte !\n\n"
                    f"Tu ne peux déposer que **{remaining:,}** PrissBucks de plus.\n"
                    f"Solde bancaire actuel: **{bank_balance_before:,}** PB"
                )
                embed.add_field(
                    name="🎯 Nouvelle stratégie recommandée",
                    value="• 💰 Garde plus d'argent sur ton compte principal\n"
                          "• 🎰 Joue au casino avec tes fonds disponibles\n"
                          "• 🎮 Participe aux mini-jeux (PPC, roulette)\n"
                          "• 🏛️ Les frais vont vers la banque publique !",
                    inline=False
                )
                await send_func(embed=embed)
                return

            # Effectuer les transferts (atomique avec nouvelles mécaniques)
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Débiter le coût total (dépôt + taxe) du compte principal
                    result = await conn.execute(
                        "UPDATE users SET balance = balance - $1 WHERE user_id = $2 AND balance >= $1", 
                        total_cost, user_id
                    )
                    
                    if result == "UPDATE 0":
                        embed = create_error_embed("Erreur", "Solde insuffisant ou compte verrouillé.")
                        await send_func(embed=embed)
                        return
                    
                    # Créditer seulement le montant de base en banque (sans la taxe)
                    success = await self.update_bank_balance(user_id, amount, "deposit")
                    
                    if not success:
                        embed = create_error_embed("Erreur", "Erreur lors du dépôt bancaire.")
                        await send_func(embed=embed)
                        return
                    
                    # Envoyer la taxe vers la banque publique
                    if deposit_tax > 0:
                        public_bank_cog = self.bot.get_cog('PublicBank')
                        if public_bank_cog and hasattr(public_bank_cog, 'add_casino_loss'):
                            await public_bank_cog.add_casino_loss(deposit_tax, "bank_deposit_tax")

            # Calculer les nouveaux soldes et mettre à jour les limites quotidiennes
            main_balance_after = main_balance_before - total_cost
            bank_balance_after = bank_balance_before + amount
            self._add_daily_deposit(user_id, amount)

            # Vérifications de cohérence post-transaction
            if main_balance_after < 0 or bank_balance_after < 0:
                logger.error(f"SÉCURITÉ CRITIQUE: Soldes négatifs après dépôt pour {user_id}")
                embed = create_error_embed("Erreur critique", "Transaction annulée pour raisons de sécurité.")
                await send_func(embed=embed)
                return

            # Logger les transactions
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_bank_deposit(
                    user_id=user_id,
                    amount=amount,
                    main_balance_before=main_balance_before,
                    main_balance_after=main_balance_after,
                    bank_balance_before=bank_balance_before,
                    bank_balance_after=bank_balance_after
                )
                
                # Logger aussi la taxe séparément
                if deposit_tax > 0:
                    await self.bot.transaction_logs.log_transaction(
                        user_id=user_id,
                        transaction_type='deposit_tax',
                        amount=-deposit_tax,
                        balance_before=main_balance_before,
                        balance_after=main_balance_after,
                        description=f"Taxe dépôt bancaire ({self.DEPOSIT_TAX_RATE*100:.0f}%) → Banque publique"
                    )

            # Confirmation du dépôt avec tous les détails
            embed = discord.Embed(
                title="🏦 Dépôt réussi avec nouvelles conditions !",
                description=f"**{amount:,}** PrissBucks déposés dans ta banque privée.",
                color=Colors.SUCCESS
            )
            
            embed.add_field(
                name="💸 Coût total payé",
                value=f"• Dépôt: **{amount:,}** PB\n"
                      f"• Taxe sécurité: **{deposit_tax:,}** PB\n"
                      f"• **Total: {total_cost:,}** PB",
                inline=True
            )
            
            embed.add_field(
                name="💰 Nouveau solde principal",
                value=f"**{main_balance_after:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="🏦 Nouveau solde bancaire",
                value=f"**{bank_balance_after:,}** PrissBucks",
                inline=True
            )
            
            # Avertissement sur les frais futurs
            if bank_balance_after > self.MIN_BALANCE_FOR_FEES:
                daily_fee = int(bank_balance_after * self.DAILY_BANK_FEE_RATE)
                embed.add_field(
                    name="⚠️ Frais quotidiens activés",
                    value=f"🚨 **{daily_fee:,} PB/jour** de frais de maintenance !\n"
                          f"💡 Garde moins de {self.MIN_BALANCE_FOR_FEES:,} PB pour éviter les frais.",
                    inline=False
                )
            
            # Limites restantes
            remaining_capacity = self.MAX_TOTAL_BANK_BALANCE - bank_balance_after
            remaining_daily = self._check_daily_deposit_limit(user_id, 0)[1]
            embed.add_field(
                name="📊 Tes limites restantes",
                value=f"• **Capacité bancaire:** {remaining_capacity:,} PB\n"
                      f"• **Dépôts aujourd'hui:** {remaining_daily:,} PB\n"
                      f"• **Maximum absolu:** {self.MAX_TOTAL_BANK_BALANCE:,} PB",
                inline=True
            )
            
            embed.add_field(
                name="🏛️ Impact social positif",
                value=f"Ta taxe de **{deposit_tax:,} PB** finance la banque publique !\n"
                      f"💰 Utilise `/publicbank` pour récupérer des fonds communautaires.",
                inline=True
            )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text=f"NOUVELLE ÈRE: Banque payante • Frais: {self.DAILY_BANK_FEE_RATE*100:.0f}%/jour • Limite: {self.MAX_TOTAL_BANK_BALANCE:,} PB")
            
            await send_func(embed=embed)
            
            logger.info(f"Bank deposit TAXÉ: {user} a déposé {amount} PB (coût total: {total_cost}, taxe: {deposit_tax}) [LOGGED]")
            
        except Exception as e:
            logger.error(f"Erreur critique deposit {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du dépôt.")
            await send_func(embed=embed)

    # ==================== RETRAIT SÉCURISÉ (GRATUIT) ====================

    @commands.command(name='withdraw', aliases=['retirer', 'retrait'])
    async def withdraw_cmd(self, ctx, amount: int):
        """e!withdraw <montant> - Retire des PrissBucks de la banque (GRATUIT)"""
        await self._execute_withdraw(ctx, amount)

    @app_commands.command(name="withdraw", description="Retire des PrissBucks de ta banque (gratuit, encouragé !)")
    @app_commands.describe(amount="Montant à retirer en PrissBucks")
    async def withdraw_slash(self, interaction: discord.Interaction, amount: int):
        """/withdraw <amount> - Retire de la banque"""
        cooldown_remaining = self._check_bank_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            embed = create_error_embed(
                "Cooldown actif", 
                f"Patiente **{cooldown_remaining:.1f}s** avant la prochaine opération bancaire."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        await self._execute_withdraw(interaction, amount, is_slash=True)

    async def _execute_withdraw(self, ctx_or_interaction, amount, is_slash=False):
        """Logique commune pour les retraits (GRATUITS pour encourager)"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        user_id = user.id

        # Validations de sécurité
        valid, error_msg = self._validate_amount(amount, "withdraw")
        if not valid:
            embed = create_error_embed("Montant invalide", error_msg)
            await send_func(embed=embed)
            return

        try:
            # Récupérer les soldes AVANT l'opération
            main_balance_before = await self.db.get_balance(user_id)
            bank_balance_before = await self.get_bank_balance(user_id)
            
            if bank_balance_before < 0:
                logger.error(f"SÉCURITÉ: Solde bancaire négatif détecté pour {user_id}: {bank_balance_before}")
                embed = create_error_embed("Erreur de sécurité", "Solde bancaire corrompu détecté. Contactez un admin.")
                await send_func(embed=embed)
                return
            
            # Vérifier le solde bancaire
            if bank_balance_before < amount:
                embed = create_error_embed(
                    "Solde bancaire insuffisant",
                    f"Tu as **{bank_balance_before:,}** PrissBucks en banque mais tu essaies de retirer **{amount:,}** PrissBucks."
                )
                await send_func(embed=embed)
                return

            # Protection contre les débordements du solde principal
            if main_balance_before + amount < main_balance_before:
                embed = create_error_embed(
                    "Montant trop élevé",
                    "Ce retrait créerait un débordement de ton solde principal."
                )
                await send_func(embed=embed)
                return

            # Effectuer les transferts (atomique)
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Débiter le compte bancaire
                    success = await self.update_bank_balance(user_id, amount, "withdraw")
                    
                    if not success:
                        embed = create_error_embed("Erreur", "Erreur lors du retrait bancaire.")
                        await send_func(embed=embed)
                        return
                    
                    # Créditer le compte principal
                    await conn.execute("""
                        INSERT INTO users (user_id, balance)
                        VALUES ($1, $2)
                        ON CONFLICT (user_id) DO UPDATE SET 
                        balance = CASE 
                            WHEN users.balance + EXCLUDED.balance >= 0 THEN users.balance + EXCLUDED.balance 
                            ELSE users.balance 
                        END
                    """, user_id, amount)

            # Calculer les nouveaux soldes
            main_balance_after = main_balance_before + amount
            bank_balance_after = bank_balance_before - amount

            # Vérifications de cohérence post-transaction
            if main_balance_after < 0 or bank_balance_after < 0:
                logger.error(f"SÉCURITÉ CRITIQUE: Soldes négatifs après retrait pour {user_id}")
                embed = create_error_embed("Erreur critique", "Transaction annulée pour raisons de sécurité.")
                await send_func(embed=embed)
                return

            # Logger les transactions
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_bank_withdraw(
                    user_id=user_id,
                    amount=amount,
                    main_balance_before=main_balance_before,
                    main_balance_after=main_balance_after,
                    bank_balance_before=bank_balance_before,
                    bank_balance_after=bank_balance_after
                )

            # Confirmation du retrait avec encouragements
            embed = discord.Embed(
                title="🏆 Retrait intelligent réussi !",
                description=f"**{amount:,}** PrissBucks retirés de ta banque (GRATUIT !)",
                color=Colors.SUCCESS
            )
            
            embed.add_field(
                name="💰 Nouveau solde principal",
                value=f"**{main_balance_after:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="🏦 Nouveau solde bancaire",
                value=f"**{bank_balance_after:,}** PrissBucks",
                inline=True
            )
            
            total_wealth = main_balance_after + bank_balance_after
            liquidity_ratio = (main_balance_after / total_wealth) * 100 if total_wealth > 0 else 0
            
            embed.add_field(
                name="💎 Fortune totale",
                value=f"**{total_wealth:,}** PrissBucks",
                inline=True
            )
            
            # Encouragements basés sur la nouvelle liquidité
            if liquidity_ratio >= 70:
                embed.add_field(
                    name="🎯 Stratégie EXCELLENTE !",
                    value=f"🔥 **{liquidity_ratio:.0f}%** de liquidité !\n"
                          f"✨ Tu auras de meilleurs daily rewards\n"
                          f"🎰 Tu peux jouer au casino efficacement\n"
                          f"🎮 Tu es prêt pour tous les mini-jeux !",
                    inline=False
                )
            elif bank_balance_after <= self.MIN_BALANCE_FOR_FEES:
                embed.add_field(
                    name="💡 Plus de frais bancaires !",
                    value=f"✅ Tu es maintenant sous les {self.MIN_BALANCE_FOR_FEES:,} PB\n"
                          f"🎉 **Aucun frais quotidien** ne sera appliqué !\n"
                          f"💰 Économie pure : {int(bank_balance_before * self.DAILY_BANK_FEE_RATE):,} PB/jour sauvés",
                    inline=False
                )
            
            if bank_balance_after > 0:
                daily_fee = int(bank_balance_after * self.DAILY_BANK_FEE_RATE)
                if daily_fee > 0:
                    embed.add_field(
                        name="⚠️ Frais restants",
                        value=f"Il te reste **{bank_balance_after:,} PB** en banque\n"
                              f"Frais quotidiens: **{daily_fee:,} PB/jour**\n"
                              f"💡 Retire **{bank_balance_after - self.MIN_BALANCE_FOR_FEES:,} PB** de plus pour éliminer tous les frais !",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="🎉 Banque vidée intelligemment !",
                    value="✨ **Aucun frais** ne sera appliqué !\n"
                          "🚀 Tu as maintenant une **liquidité maximale** !\n"
                          "💡 Utilise tes PrissBucks pour jouer et gagner plus !",
                    inline=False
                )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="Retrait GRATUIT • Nouvelle stratégie économique • Liquidité = Rentabilité !")
            
            await send_func(embed=embed)
            
            logger.info(f"Bank withdraw: {user} a retiré {amount} PB intelligemment (liquidité: {liquidity_ratio:.1f}%)")
            
        except Exception as e:
            logger.error(f"Erreur critique withdraw {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du retrait.")
            await send_func(embed=embed)

    # ==================== AIDE MISE À JOUR ====================

    @commands.command(name='bankhelp', aliases=['banqueaide'])
    async def bank_help_cmd(self, ctx):
        """Affiche l'aide sur le nouveau système bancaire rééquilibré"""
        embed = discord.Embed(
            title="🏦 Guide de la Banque Privée RÉÉQUILIBRÉE",
            description="**NOUVELLE ÈRE ÉCONOMIQUE !** Fini les coffres-forts gratuits !",
            color=Colors.WARNING
        )
        
        embed.add_field(
            name="⚖️ NOUVELLES LIMITES STRICTES",
            value=f"📊 **Maximum par compte:** {self.MAX_TOTAL_BANK_BALANCE:,} PB (vs 100M avant !)\n"
                  f"📅 **Maximum par jour:** {self.MAX_DAILY_DEPOSITS:,} PB de dépôts\n"
                  f"💰 **Maximum par transaction:** {self.MAX_TRANSACTION:,} PB",
            inline=False
        )
        
        embed.add_field(
            name="💸 NOUVEAU: Système de frais",
            value=f"🏦 **Frais de maintenance:** {self.DAILY_BANK_FEE_RATE*100:.0f}% par jour (si > {self.MIN_BALANCE_FOR_FEES:,} PB)\n"
                  f"💳 **Taxe de dépôt:** {self.DEPOSIT_TAX_RATE*100:.0f}% à chaque dépôt\n"
                  f"✅ **Retraits:** GRATUITS (encouragés !)\n"
                  f"🏛️ **Destination frais:** Banque publique",
            inline=False
        )
        
        embed.add_field(
            name="🎯 NOUVELLE STRATÉGIE RECOMMANDÉE",
            value="• **Garde MOINS de 500 PB** en banque pour éviter les frais\n"
                  "• **Maximise ta liquidité** pour de meilleurs daily\n"
                  "• **Joue au casino** avec tes fonds disponibles\n"
                  "• **Participe aux mini-jeux** (PPC, roulette)\n"
                  "• **La banque n'est plus un parking gratuit !**",
            inline=False
        )
        
        embed.add_field(
            name="🔧 Commandes disponibles",
            value=f"• `{PREFIX}bank` ou `/bank` - Voir tes infos (privées)\n"
                  f"• `{PREFIX}deposit <montant>` - Déposer (TAXÉ !)\n"
                  f"• `{PREFIX}withdraw <montant>` - Retirer (GRATUIT !)\n"
                  f"• `{PREFIX}bankhelp` - Cette aide",
            inline=False
        )
        
        embed.add_field(
            name="🏛️ Impact sur l'économie",
            value="✨ **Les frais financent la banque publique !**\n"
                  f"• Utilise `/publicbank` pour récupérer des fonds\n"
                  f"• Plus d'argent circule = plus d'opportunités\n"
                  f"• Fin de la thésaurisation massive\n"
                  f"• Économie plus dynamique et équitable",
            inline=False
        )
        
        embed.add_field(
            name="💡 Exemples concrets",
            value=f"**Dépôt 1000 PB:** Coûte 1020 PB (taxe 20 PB)\n"
                  f"**1000 PB en banque:** {int(1000 * self.DAILY_BANK_FEE_RATE)} PB/jour de frais\n"
                  f"**Retrait 1000 PB:** GRATUIT (0 PB de frais)\n"
                  f"**Stratégie optimale:** Max 400 PB en banque !",
            inline=False
        )
        
        embed.set_footer(text="RÉVOLUTION ÉCONOMIQUE • Liquidité = Rentabilité • Les frais financent la communauté !")
        await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Bank(bot))