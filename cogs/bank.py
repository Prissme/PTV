import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import math
import asyncio  # AJOUT MANQUANT
from datetime import datetime, timezone

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_info_embed

logger = logging.getLogger(__name__)

class Bank(commands.Cog):
    """Système de banque privée simplifié"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        self._initialized = False
        self._initialization_lock = asyncio.Lock()
        
        # Configuration simplifiée
        self.MIN_DEPOSIT = 1
        self.MIN_WITHDRAW = 1
        self.MAX_TRANSACTION = 50000
        self.MAX_TOTAL_BANK_BALANCE = 100000
        
        # Système de frais bancaires simplifié
        self.DAILY_BANK_FEE_RATE = 0.02  # 2% de frais par jour
        self.MIN_BALANCE_FOR_FEES = 500
        self.DEPOSIT_TAX_RATE = 0.02  # 2% de taxe sur les dépôts
        
        # Limites quotidiennes simplifiées
        self.MAX_DAILY_DEPOSITS = 15000
        
        # Dictionnaires pour gérer les cooldowns et limites
        self.bank_cooldowns = {}
        self.daily_deposit_limits = {}
        
        # Tâche de frais - sera initialisée dans cog_load
        self._fees_task = None
        
    async def cog_load(self):
        """Appelé quand le cog est chargé - Protection contre les loops"""
        async with self._initialization_lock:
            if self._initialized:
                logger.warning("Cog Bank déjà initialisé, skip")
                return
            
            try:
                self.db = self.bot.database
                if not self.db:
                    raise RuntimeError("Database non disponible")
                
                await self.create_bank_table()
                
                # Démarrer la tâche de frais seulement après initialisation complète
                if not self._fees_task or self._fees_task.cancelled():
                    self._fees_task = self.daily_bank_fees.start()
                
                self._initialized = True
                logger.info("✅ Cog Bank simplifié initialisé")
                
            except Exception as e:
                logger.error(f"Erreur initialisation Bank cog: {e}")
                raise
    
    async def cog_unload(self):
        """Arrêter les tâches lors du déchargement"""
        try:
            if self._fees_task and not self._fees_task.cancelled():
                self._fees_task.cancel()
                logger.info("Tâche frais bancaires arrêtée")
        except Exception as e:
            logger.error(f"Erreur arrêt tâche frais: {e}")
        
        self._initialized = False

    async def create_bank_table(self):
        """Crée la table pour stocker les comptes bancaires - Protection timeout"""
        if not self.db or not self.db.pool:
            raise RuntimeError("Base de données non disponible")
        
        try:
            # Timeout pour éviter les blocages
            async with asyncio.timeout(30):
                async with self.db.pool.acquire() as conn:
                    # Création table avec protection existante
                    await conn.execute('''
                        CREATE TABLE IF NOT EXISTS user_bank (
                            user_id BIGINT PRIMARY KEY,
                            balance BIGINT DEFAULT 0 CHECK (balance >= 0),
                            total_deposited BIGINT DEFAULT 0,
                            total_withdrawn BIGINT DEFAULT 0,
                            total_fees_paid BIGINT DEFAULT 0,
                            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                            last_activity TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                            last_fee_payment TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                        )
                    ''')
                    
                    # Index avec IF NOT EXISTS
                    await conn.execute('''
                        CREATE INDEX IF NOT EXISTS idx_user_bank_user_id ON user_bank(user_id)
                    ''')
                    
                    logger.info("✅ Table user_bank créée/vérifiée avec succès")
                    
        except asyncio.TimeoutError:
            logger.error("Timeout lors de la création de la table user_bank")
            raise
        except Exception as e:
            logger.error(f"Erreur création table user_bank: {e}")
            raise

    @tasks.loop(hours=24)
    async def daily_bank_fees(self):
        """Applique les frais de maintenance bancaire quotidiens - Protection contre loops"""
        if not self._initialized or not self.db or not self.db.pool:
            logger.warning("Tâche frais: Bot non initialisé, skip")
            return
            
        try:
            async with asyncio.timeout(120):  # Timeout de 2 minutes
                async with self.db.pool.acquire() as conn:
                    accounts = await conn.fetch("""
                        SELECT user_id, balance 
                        FROM user_bank 
                        WHERE balance > $1
                    """, self.MIN_BALANCE_FOR_FEES)
                    
                    if not accounts:
                        logger.info("🏦 Aucun compte avec frais bancaires")
                        return
                    
                    total_fees_collected = 0
                    processed_accounts = 0
                    
                    for account in accounts:
                        try:
                            user_id = account['user_id']
                            current_balance = account['balance']
                            
                            fee = int(current_balance * self.DAILY_BANK_FEE_RATE)
                            if fee <= 0:
                                continue
                                
                            new_balance = max(0, current_balance - fee)
                            
                            await conn.execute("""
                                UPDATE user_bank 
                                SET balance = $1, 
                                    total_fees_paid = total_fees_paid + $2,
                                    last_activity = NOW(),
                                    last_fee_payment = NOW()
                                WHERE user_id = $3
                            """, new_balance, fee, user_id)
                            
                            # Envoyer les frais vers la banque publique
                            try:
                                public_bank_cog = self.bot.get_cog('PublicBank')
                                if public_bank_cog:
                                    await public_bank_cog.add_casino_loss(fee, "bank_maintenance_fees")
                            except Exception as e:
                                logger.error(f"Erreur envoi frais vers banque publique: {e}")
                            
                            total_fees_collected += fee
                            processed_accounts += 1
                            
                        except Exception as e:
                            logger.error(f"Erreur traitement frais pour user {account['user_id']}: {e}")
                            continue
                    
                    logger.info(f"🏦 Frais bancaires: {total_fees_collected:,} PB collectés sur {processed_accounts} comptes → Banque publique")
                    
        except asyncio.TimeoutError:
            logger.error("Timeout lors du traitement des frais bancaires")
        except Exception as e:
            logger.error(f"Erreur tâche frais bancaires: {e}")

    @daily_bank_fees.before_loop
    async def before_daily_bank_fees(self):
        """Attendre que le bot soit prêt avant de démarrer la tâche"""
        if not self.bot.is_ready():
            await self.bot.wait_until_ready()
        
        # Attendre que l'initialisation soit complète
        max_wait = 60  # 60 secondes max
        waited = 0
        while not self._initialized and waited < max_wait:
            await asyncio.sleep(1)
            waited += 1
            
        if not self._initialized:
            logger.warning("Tâche frais: Timeout attente initialisation")
            
    @daily_bank_fees.after_loop
    async def after_daily_bank_fees(self):
        """Nettoyage après arrêt de la tâche"""
        if self.daily_bank_fees.is_being_cancelled():
            logger.info("Tâche frais bancaires arrêtée proprement")
        else:
            logger.warning("Tâche frais bancaires arrêtée de manière inattendue")

    def _check_bank_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown pour les opérations bancaires"""
        import time
        now = time.time()
        cooldown_duration = 3
        if user_id in self.bank_cooldowns:
            elapsed = now - self.bank_cooldowns[user_id]
            if elapsed < cooldown_duration:
                return cooldown_duration - elapsed
        self.bank_cooldowns[user_id] = now
        return 0

    def _check_daily_deposit_limit(self, user_id: int, amount: int) -> tuple:
        """Vérifie la limite quotidienne de dépôts"""
        today = datetime.now(timezone.utc).date()
        
        if user_id not in self.daily_deposit_limits:
            self.daily_deposit_limits[user_id] = {'date': today, 'deposited': 0}
        
        user_data = self.daily_deposit_limits[user_id]
        if user_data['date'] != today:
            user_data['date'] = today
            user_data['deposited'] = 0
        
        deposited_today = user_data['deposited']
        remaining = max(0, self.MAX_DAILY_DEPOSITS - deposited_today)
        
        return (amount <= remaining), remaining - amount if amount <= remaining else remaining

    def _add_daily_deposit(self, user_id: int, amount: int):
        """Ajoute un montant aux dépôts quotidiens"""
        today = datetime.now(timezone.utc).date()
        if user_id not in self.daily_deposit_limits:
            self.daily_deposit_limits[user_id] = {'date': today, 'deposited': 0}
        
        if self.daily_deposit_limits[user_id]['date'] == today:
            self.daily_deposit_limits[user_id]['deposited'] += amount
        else:
            self.daily_deposit_limits[user_id] = {'date': today, 'deposited': amount}

    async def get_bank_balance(self, user_id: int) -> int:
        """Récupère le solde bancaire - Avec protection timeout"""
        if not self._initialized or not self.db or not self.db.pool:
            logger.warning(f"get_bank_balance: DB non initialisée pour user {user_id}")
            return 0
        
        try:
            async with asyncio.timeout(10):
                async with self.db.pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT balance FROM user_bank WHERE user_id = $1", user_id)
                    return row["balance"] if row else 0
        except asyncio.TimeoutError:
            logger.error(f"Timeout get_bank_balance pour user {user_id}")
            return 0
        except Exception as e:
            logger.error(f"Erreur get_bank_balance {user_id}: {e}")
            return 0

    async def get_bank_stats(self, user_id: int) -> dict:
        """Récupère les statistiques bancaires - Avec protection timeout"""
        default_stats = {"balance": 0, "total_deposited": 0, "total_withdrawn": 0, "total_fees_paid": 0}
        
        if not self._initialized or not self.db or not self.db.pool:
            logger.warning(f"get_bank_stats: DB non initialisée pour user {user_id}")
            return default_stats
        
        try:
            async with asyncio.timeout(10):
                async with self.db.pool.acquire() as conn:
                    row = await conn.fetchrow("""
                        SELECT balance, total_deposited, total_withdrawn, total_fees_paid, created_at, last_activity
                        FROM user_bank WHERE user_id = $1
                    """, user_id)
                    
                    return dict(row) if row else default_stats
        except asyncio.TimeoutError:
            logger.error(f"Timeout get_bank_stats pour user {user_id}")
            return default_stats
        except Exception as e:
            logger.error(f"Erreur get_bank_stats {user_id}: {e}")
            return default_stats

    async def update_bank_balance(self, user_id: int, amount: int, operation_type: str) -> bool:
        """Met à jour le solde bancaire - VERSION CORRIGÉE avec protection timeout"""
        if not self._initialized or not self.db or not self.db.pool:
            logger.error(f"update_bank_balance: DB non initialisée pour user {user_id}")
            return False
            
        if amount == 0:
            logger.warning(f"update_bank_balance: montant 0 pour user {user_id}")
            return True  # Considéré comme succès
        
        now = datetime.now(timezone.utc)
        
        try:
            async with asyncio.timeout(15):
                async with self.db.pool.acquire() as conn:
                    async with conn.transaction():
                        if operation_type == "deposit" and amount > 0:
                            # CORRECTION: 3 paramètres seulement
                            await conn.execute("""
                                INSERT INTO user_bank (user_id, balance, total_deposited, last_activity)
                                VALUES ($1, $2, $2, $3)
                                ON CONFLICT (user_id) DO UPDATE SET
                                balance = user_bank.balance + $2,
                                total_deposited = user_bank.total_deposited + $2,
                                last_activity = $3
                            """, user_id, amount, now)
                            return True
                            
                        elif operation_type == "withdraw" and amount > 0:
                            # CORRECTION: 3 paramètres seulement
                            result = await conn.execute("""
                                UPDATE user_bank 
                                SET balance = balance - $1, 
                                    total_withdrawn = total_withdrawn + $1,
                                    last_activity = $2
                                WHERE user_id = $3 AND balance >= $1
                            """, amount, now, user_id)
                            return "UPDATE 0" not in result
                        else:
                            logger.warning(f"update_bank_balance: opération invalide '{operation_type}' ou montant négatif")
                            return False
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout update_bank_balance pour user {user_id}")
            return False
        except Exception as e:
            logger.error(f"Erreur update_bank_balance {user_id}: {e}")
            return False

    # ==================== MÉTHODES UTILITAIRES ====================
    
    def _is_ready(self) -> bool:
        """Vérifie si le cog est prêt à fonctionner"""
        return (self._initialized and 
                self.db is not None and 
                self.db.pool is not None and 
                self.bot.is_ready())

    # ==================== COMMANDES BANQUE ====================

    @commands.command(name='bank', aliases=['banque'])
    async def bank_cmd(self, ctx):
        """Affiche tes informations bancaires"""
        await self._execute_bank_info(ctx, ctx.author)

    @app_commands.command(name="bank", description="Affiche tes informations bancaires privées")
    async def bank_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self._execute_bank_info(interaction, interaction.user, is_slash=True)

    async def _execute_bank_info(self, ctx_or_interaction, user, is_slash=False):
        """Affiche les infos bancaires"""
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
            
            # Vérifier limite quotidienne
            today_allowed, remaining_today = self._check_daily_deposit_limit(user.id, 0)
            deposited_today = self.MAX_DAILY_DEPOSITS - remaining_today if user.id in self.daily_deposit_limits else 0
            
            embed = discord.Embed(
                title="🏦 Ta Banque Privée",
                description=f"**{user.display_name}** - Compte avec frais de maintenance",
                color=Colors.WARNING if bank_stats['balance'] > self.MIN_BALANCE_FOR_FEES else Colors.PREMIUM
            )
            
            # Soldes
            embed.add_field(
                name="💰 Solde bancaire",
                value=f"**{bank_stats['balance']:,}** PrissBucks" + 
                      (f"\n⚠️ Frais quotidiens actifs !" if bank_stats['balance'] > self.MIN_BALANCE_FOR_FEES else ""),
                inline=True
            )
            
            embed.add_field(
                name="💳 Solde principal",
                value=f"**{main_balance:,}** PrissBucks",
                inline=True
            )
            
            # Statistiques
            embed.add_field(
                name="📈 Total déposé",
                value=f"**{bank_stats['total_deposited']:,}** PB",
                inline=True
            )
            
            embed.add_field(
                name="📉 Total retiré", 
                value=f"**{bank_stats['total_withdrawn']:,}** PB",
                inline=True
            )
            
            embed.add_field(
                name="💸 Frais payés",
                value=f"**{bank_stats.get('total_fees_paid', 0):,}** PB",
                inline=True
            )
            
            # Limites
            remaining_capacity = self.MAX_TOTAL_BANK_BALANCE - bank_stats['balance']
            embed.add_field(
                name="⚖️ Limites bancaires",
                value=f"📊 Capacité restante: {remaining_capacity:,} PB\n"
                      f"📅 Dépôts restants aujourd'hui: {remaining_today:,} PB",
                inline=False
            )
            
            # Système de frais
            if bank_stats['balance'] > self.MIN_BALANCE_FOR_FEES:
                daily_fee = int(bank_stats['balance'] * self.DAILY_BANK_FEE_RATE)
                embed.add_field(
                    name="⚠️ Frais de maintenance",
                    value=f"💸 {self.DAILY_BANK_FEE_RATE*100:.0f}% par jour\n"
                          f"💰 Frais quotidiens: {daily_fee:,} PB\n"
                          f"🏛️ Vers banque publique",
                    inline=False
                )
            
            # Instructions
            embed.add_field(
                name="🔧 Commandes",
                value=f"• `{PREFIX}deposit <montant>` - Déposer (taxe {self.DEPOSIT_TAX_RATE*100:.0f}%)\n"
                      f"• `{PREFIX}withdraw <montant>` - Retirer (gratuit)",
                inline=False
            )
            
            embed.set_footer(text=f"Max: {self.MAX_TOTAL_BANK_BALANCE:,} PB • Frais: {self.DAILY_BANK_FEE_RATE*100:.0f}%/jour")
            
            if is_slash:
                await send_func(embed=embed, ephemeral=True)
            else:
                try:
                    await user.send(embed=embed)
                    if ctx_or_interaction.guild:
                        await ctx_or_interaction.send("🏦 Infos bancaires envoyées en privé ! 📨")
                except:
                    await ctx_or_interaction.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur bank info {user.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des informations.")
            await send_func(embed=embed)

    @commands.command(name='deposit', aliases=['depot'])
    async def deposit_cmd(self, ctx, amount: int):
        """Dépose des PrissBucks en banque"""
        await self._execute_deposit(ctx, amount)

    @app_commands.command(name="deposit", description="Dépose des PrissBucks dans ta banque (limites et taxe)")
    async def deposit_slash(self, interaction: discord.Interaction, amount: int):
        cooldown_remaining = self._check_bank_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            embed = create_error_embed("Cooldown", f"Patiente {cooldown_remaining:.1f}s")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        await self._execute_deposit(interaction, amount, is_slash=True)

    async def _execute_deposit(self, ctx_or_interaction, amount, is_slash=False):
        """Logique de dépôt simplifiée - VERSION CORRIGÉE"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        user_id = user.id

        # Validations
        if amount < self.MIN_DEPOSIT or amount > self.MAX_TRANSACTION:
            embed = create_error_embed("Montant invalide", 
                f"Montant entre {self.MIN_DEPOSIT} et {self.MAX_TRANSACTION:,} PB requis.")
            await send_func(embed=embed)
            return

        # Vérifier limite quotidienne
        allowed, remaining_after = self._check_daily_deposit_limit(user_id, amount)
        if not allowed:
            embed = create_error_embed("Limite quotidienne", 
                f"Limite: {self.MAX_DAILY_DEPOSITS:,} PB/jour\nRestant: {self._check_daily_deposit_limit(user_id, 0)[1]:,} PB")
            await send_func(embed=embed)
            return

        try:
            # Soldes avant
            main_balance_before = await self.db.get_balance(user_id)
            bank_balance_before = await self.get_bank_balance(user_id)
            
            # Calculer taxe
            deposit_tax = int(amount * self.DEPOSIT_TAX_RATE)
            total_cost = amount + deposit_tax
            
            logger.info(f"Dépôt debug - User: {user_id}, Amount: {amount}, MainBalance: {main_balance_before}, Tax: {deposit_tax}, TotalCost: {total_cost}")
            
            if main_balance_before < total_cost:
                embed = create_error_embed("Solde insuffisant",
                    f"Coût total: {total_cost:,} PB (dépôt: {amount:,} + taxe: {deposit_tax:,})\nTu as: {main_balance_before:,} PB")
                await send_func(embed=embed)
                return

            # Vérifier limite bancaire
            if bank_balance_before + amount > self.MAX_TOTAL_BANK_BALANCE:
                remaining = self.MAX_TOTAL_BANK_BALANCE - bank_balance_before
                embed = create_error_embed("Limite bancaire",
                    f"Maximum: {self.MAX_TOTAL_BANK_BALANCE:,} PB\nTu peux déposer: {remaining:,} PB")
                await send_func(embed=embed)
                return

            # Transaction atomique CORRIGÉE
            logger.info("Début transaction atomique dépôt")
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Débiter compte principal
                    logger.info(f"Débitage {total_cost} du compte principal")
                    await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", 
                                     total_cost, user_id)
                    
                    # Créditer banque (sans taxe) - UTILISE LA VERSION CORRIGÉE
                    logger.info(f"Crédit banque privée: {amount}")
                    success = await self.update_bank_balance(user_id, amount, "deposit")
                    if not success:
                        logger.error("Échec update_bank_balance")
                        embed = create_error_embed("Erreur", "Erreur lors du dépôt.")
                        await send_func(embed=embed)
                        return
                    
                    # Envoyer taxe vers banque publique
                    if deposit_tax > 0:
                        logger.info(f"Envoi taxe {deposit_tax} vers banque publique")
                        public_bank_cog = self.bot.get_cog('PublicBank')
                        if public_bank_cog:
                            await public_bank_cog.add_casino_loss(deposit_tax, "bank_deposit_tax")
                        else:
                            logger.warning("PublicBank cog non trouvé")

            # Mettre à jour limites
            self._add_daily_deposit(user_id, amount)

            # Confirmation
            embed = create_success_embed("Dépôt réussi", 
                f"{amount:,} PB déposés (coût total: {total_cost:,} PB)")
            
            if bank_balance_before + amount > self.MIN_BALANCE_FOR_FEES:
                daily_fee = int((bank_balance_before + amount) * self.DAILY_BANK_FEE_RATE)
                embed.add_field(name="⚠️ Frais activés", 
                    value=f"{daily_fee:,} PB/jour de frais", inline=False)
            
            await send_func(embed=embed)
            logger.info(f"Dépôt réussi - User: {user_id}, Amount: {amount}, Tax: {deposit_tax}")
            
        except Exception as e:
            logger.error(f"Erreur deposit {user_id}: {type(e).__name__}: {str(e)}", exc_info=True)
            embed = create_error_embed("Erreur", f"Erreur technique lors du dépôt.")
            await send_func(embed=embed)

    @commands.command(name='withdraw', aliases=['retirer'])
    async def withdraw_cmd(self, ctx, amount: int):
        """Retire des PrissBucks de la banque"""
        await self._execute_withdraw(ctx, amount)

    @app_commands.command(name="withdraw", description="Retire des PrissBucks de ta banque (gratuit)")
    async def withdraw_slash(self, interaction: discord.Interaction, amount: int):
        cooldown_remaining = self._check_bank_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            embed = create_error_embed("Cooldown", f"Patiente {cooldown_remaining:.1f}s")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        await self._execute_withdraw(interaction, amount, is_slash=True)

    async def _execute_withdraw(self, ctx_or_interaction, amount, is_slash=False):
        """Logique de retrait simplifiée - VERSION CORRIGÉE"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        user_id = user.id

        # Validations
        if amount < self.MIN_WITHDRAW or amount > self.MAX_TRANSACTION:
            embed = create_error_embed("Montant invalide",
                f"Montant entre {self.MIN_WITHDRAW} et {self.MAX_TRANSACTION:,} PB requis.")
            await send_func(embed=embed)
            return

        try:
            bank_balance_before = await self.get_bank_balance(user_id)
            main_balance_before = await self.db.get_balance(user_id)
            
            logger.info(f"Retrait debug - User: {user_id}, Amount: {amount}, BankBalance: {bank_balance_before}")
            
            if bank_balance_before < amount:
                embed = create_error_embed("Solde bancaire insuffisant",
                    f"Banque: {bank_balance_before:,} PB\nDemandé: {amount:,} PB")
                await send_func(embed=embed)
                return

            # Transaction atomique CORRIGÉE
            logger.info("Début transaction atomique retrait")
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Débiter banque - UTILISE LA VERSION CORRIGÉE
                    logger.info(f"Débit banque: {amount}")
                    success = await self.update_bank_balance(user_id, amount, "withdraw")
                    if not success:
                        logger.error("Échec update_bank_balance pour retrait")
                        embed = create_error_embed("Erreur", "Erreur lors du retrait.")
                        await send_func(embed=embed)
                        return
                    
                    # Créditer compte principal
                    logger.info(f"Crédit compte principal: {amount}")
                    await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", 
                                     amount, user_id)

            # Calculs pour affichage
            bank_balance_after = bank_balance_before - amount
            main_balance_after = main_balance_before + amount
            
            # Confirmation
            embed = create_success_embed("Retrait réussi", f"{amount:,} PB retirés (GRATUIT)")
            embed.add_field(name="💰 Nouveau solde principal", value=f"{main_balance_after:,} PB", inline=True)
            embed.add_field(name="🏦 Nouveau solde bancaire", value=f"{bank_balance_after:,} PB", inline=True)
            
            # Encouragement si plus de frais
            if bank_balance_after <= self.MIN_BALANCE_FOR_FEES:
                embed.add_field(name="✅ Plus de frais", value="Aucun frais quotidien appliqué !", inline=False)
            
            await send_func(embed=embed)
            logger.info(f"Retrait réussi - User: {user_id}, Amount: {amount}")
            
        except Exception as e:
            logger.error(f"Erreur withdraw {user_id}: {type(e).__name__}: {str(e)}", exc_info=True)
            embed = create_error_embed("Erreur", f"Erreur technique lors du retrait.")
            await send_func(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Bank(bot))
