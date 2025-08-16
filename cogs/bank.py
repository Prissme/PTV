import discord
from discord.ext import commands
from discord import app_commands
import logging
import math

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_info_embed

logger = logging.getLogger(__name__)

class Bank(commands.Cog):
    """Système de banque privée - stockage sécurisé invisible des classements avec logs intégrés et sécurité renforcée"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Configuration de la banque avec sécurité renforcée
        self.MIN_DEPOSIT = 1
        self.MIN_WITHDRAW = 1
        self.MAX_TRANSACTION = 1000000  # 1M maximum par transaction
        self.MAX_TOTAL_BANK_BALANCE = 100000000  # 100M limite totale en banque par user
        
        # Dictionnaire pour gérer les cooldowns
        self.bank_cooldowns = {}
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        await self.create_bank_table()
        logger.info("✅ Cog Bank initialisé - Banque privée sécurisée avec logs intégrés et protection renforcée")
    
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
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    last_activity TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            
            # Index pour optimiser les requêtes
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_user_bank_user_id ON user_bank(user_id)
            ''')
            
            # Contrainte pour éviter les balances négatives (double sécurité)
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
            
            logger.info("✅ Table user_bank créée/vérifiée avec sécurités renforcées")

    def _check_bank_cooldown(self, user_id: int) -> float:
        """Vérifie et retourne le cooldown restant pour les opérations bancaires"""
        import time
        now = time.time()
        cooldown_duration = 2  # 2 secondes de cooldown
        if user_id in self.bank_cooldowns:
            elapsed = now - self.bank_cooldowns[user_id]
            if elapsed < cooldown_duration:
                return cooldown_duration - elapsed
        self.bank_cooldowns[user_id] = now
        return 0

    def _validate_amount(self, amount: int, operation: str) -> tuple:
        """Valide un montant pour une opération bancaire"""
        # Vérification que le montant est un entier positif
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
        if amount > 2**53:  # Limite JavaScript safe integer
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
                
                # Vérification de sécurité
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
            return {"balance": 0, "total_deposited": 0, "total_withdrawn": 0, "created_at": None, "last_activity": None}
        
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT balance, total_deposited, total_withdrawn, created_at, last_activity
                    FROM user_bank WHERE user_id = $1
                """, user_id)
                
                if row:
                    stats = dict(row)
                    # Vérifications de sécurité
                    if stats["balance"] < 0:
                        logger.error(f"SÉCURITÉ: Balance négative pour {user_id}")
                        stats["balance"] = 0
                    if stats["total_deposited"] < 0:
                        logger.error(f"SÉCURITÉ: Total déposé négatif pour {user_id}")
                        stats["total_deposited"] = 0
                    if stats["total_withdrawn"] < 0:
                        logger.error(f"SÉCURITÉ: Total retiré négatif pour {user_id}")
                        stats["total_withdrawn"] = 0
                    return stats
                else:
                    return {"balance": 0, "total_deposited": 0, "total_withdrawn": 0, "created_at": None, "last_activity": None}
        except Exception as e:
            logger.error(f"Erreur get_bank_stats {user_id}: {e}")
            return {"balance": 0, "total_deposited": 0, "total_withdrawn": 0, "created_at": None, "last_activity": None}

    async def update_bank_balance(self, user_id: int, amount: int, operation_type: str) -> bool:
        """Met à jour le solde bancaire et les statistiques avec vérifications de sécurité"""
        if not self.db.pool:
            return False
            
        # Validation des paramètres
        if not isinstance(amount, int) or amount <= 0:
            logger.error(f"SÉCURITÉ: Montant invalide pour {user_id}: {amount}")
            return False
            
        if operation_type not in ["deposit", "withdraw"]:
            logger.error(f"SÉCURITÉ: Type d'opération invalide: {operation_type}")
            return False
            
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        
        try:
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Récupérer ou créer le compte avec verrous
                    account = await conn.fetchrow(
                        "SELECT * FROM user_bank WHERE user_id = $1 FOR UPDATE", 
                        user_id
                    )
                    
                    if not account:
                        # Créer un nouveau compte
                        if operation_type == "deposit" and amount > 0:
                            # Vérifier les limites
                            if amount > self.MAX_TOTAL_BANK_BALANCE:
                                logger.warning(f"SÉCURITÉ: Tentative de dépôt dépassant la limite pour {user_id}: {amount}")
                                return False
                                
                            await conn.execute("""
                                INSERT INTO user_bank (user_id, balance, total_deposited, last_activity)
                                VALUES ($1, $2, $3, $4)
                            """, user_id, amount, amount, now)
                            logger.info(f"Bank: Nouveau compte créé pour {user_id} avec dépôt initial {amount}")
                            return True
                        elif operation_type == "withdraw":
                            return False  # Pas de compte = pas de retrait possible
                    else:
                        # Mettre à jour le compte existant
                        current_balance = account["balance"]
                        current_deposited = account["total_deposited"]
                        current_withdrawn = account["total_withdrawn"]
                        
                        # Vérifications de sécurité sur les valeurs existantes
                        if current_balance < 0:
                            logger.error(f"SÉCURITÉ: Balance corrompue pour {user_id}: {current_balance}")
                            current_balance = 0
                        
                        if operation_type == "deposit" and amount > 0:
                            new_balance = current_balance + amount
                            new_deposited = current_deposited + amount
                            
                            # Vérifier les limites
                            if new_balance > self.MAX_TOTAL_BANK_BALANCE:
                                logger.warning(f"SÉCURITÉ: Dépôt dépasserait la limite pour {user_id}: {new_balance}")
                                return False
                            
                            # Protection contre les débordements
                            if new_balance < current_balance:  # Détection d'overflow
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
                                
                                # Vérifications de sécurité
                                if new_balance < 0:  # Double vérification
                                    logger.error(f"SÉCURITÉ: Retrait créerait une balance négative pour {user_id}")
                                    return False
                                    
                                await conn.execute("""
                                    UPDATE user_bank 
                                    SET balance = $1, total_withdrawn = $2, last_activity = $3
                                    WHERE user_id = $4
                                """, new_balance, new_withdrawn, now, user_id)
                                return True
                            else:
                                return False  # Solde insuffisant
                    
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
        await interaction.response.defer(ephemeral=True)  # Réponse privée par défaut
        await self._execute_bank_info(interaction, interaction.user, is_slash=True)

    async def _execute_bank_info(self, ctx_or_interaction, user, is_slash=False):
        """Logique commune pour afficher les infos bancaires avec sécurité"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
        else:
            # Pour les commandes normales, envoyer en DM si possible
            try:
                send_func = user.send
            except:
                send_func = ctx_or_interaction.send
        
        try:
            # Récupérer les stats bancaires et le solde principal
            bank_stats = await self.get_bank_stats(user.id)
            main_balance = await self.db.get_balance(user.id)
            
            embed = discord.Embed(
                title="🏦 Ta Banque Privée",
                description=f"**{user.display_name}** - Compte personnel sécurisé",
                color=Colors.PREMIUM
            )
            
            # Soldes avec indicateurs de sécurité
            embed.add_field(
                name="💰 Solde bancaire",
                value=f"**{bank_stats['balance']:,}** PrissBucks",
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
            
            # Statistiques historiques avec vérification de cohérence
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
            
            # Calcul du ratio sécurité
            if total_wealth > 0:
                security_ratio = (bank_stats['balance'] / total_wealth) * 100
                embed.add_field(
                    name="🔒 Sécurisation",
                    value=f"**{security_ratio:.1f}%** en banque",
                    inline=True
                )
            
            # Limites de sécurité
            remaining_capacity = self.MAX_TOTAL_BANK_BALANCE - bank_stats['balance']
            if remaining_capacity < self.MAX_TOTAL_BANK_BALANCE * 0.1:  # Moins de 10% de capacité restante
                embed.add_field(
                    name="⚠️ Limite de capacité",
                    value=f"**{remaining_capacity:,}** PB restants\n(Max: {self.MAX_TOTAL_BANK_BALANCE:,})",
                    inline=True
                )
            
            # Avantages de la banque avec sécurité
            embed.add_field(
                name="✅ Avantages de la banque",
                value="• **Invisible** dans les classements\n"
                      "• **Protection** contre le vol\n"
                      "• **Privé** - seul toi peux voir\n"
                      "• **Sécurisé** - protection anti-corruption\n"
                      "• **Limité** - protection contre débordements",
                inline=False
            )
            
            # Instructions d'utilisation
            embed.add_field(
                name="🔧 Comment utiliser",
                value=f"• `{PREFIX}deposit <montant>` - Déposer en banque\n"
                      f"• `{PREFIX}withdraw <montant>` - Retirer de la banque\n"
                      f"• `{PREFIX}bank` - Voir tes infos (privées)\n"
                      f"• `/deposit` et `/withdraw` aussi disponibles",
                inline=False
            )
            
            # Date de création du compte avec vérification d'intégrité
            if bank_stats['created_at']:
                embed.set_footer(text=f"Compte créé le {bank_stats['created_at'].strftime('%d/%m/%Y')} • Banque 100% privée • Sécurité renforcée")
            else:
                embed.set_footer(text="Utilise 'deposit' pour créer ton compte bancaire • Banque 100% privée • Sécurité renforcée")
            
            embed.set_thumbnail(url=user.display_avatar.url)
            
            # Envoyer la réponse
            if is_slash:
                await send_func(embed=embed, ephemeral=True)
            else:
                try:
                    await user.send(embed=embed)
                    if ctx_or_interaction.guild:  # Si c'est dans un serveur
                        await ctx_or_interaction.send("🏦 Tes informations bancaires t'ont été envoyées en privé ! 📨")
                except:
                    await ctx_or_interaction.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur bank info {user.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des informations bancaires.")
            await send_func(embed=embed, ephemeral=True if is_slash else False)

    # ==================== DÉPÔT SÉCURISÉ ====================

    @commands.command(name='deposit', aliases=['depot', 'depo'])
    async def deposit_cmd(self, ctx, amount: int):
        """e!deposit <montant> - Dépose des PrissBucks en banque"""
        await self._execute_deposit(ctx, amount)

    @app_commands.command(name="deposit", description="Dépose des PrissBucks dans ta banque privée sécurisée")
    @app_commands.describe(amount="Montant à déposer en PrissBucks")
    async def deposit_slash(self, interaction: discord.Interaction, amount: int):
        """/deposit <amount> - Dépose en banque"""
        # Vérifier le cooldown
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
        """Logique commune pour les dépôts avec sécurité renforcée et logs"""
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

        try:
            # Récupérer les soldes AVANT l'opération pour les logs et vérifications
            main_balance_before = await self.db.get_balance(user_id)
            bank_balance_before = await self.get_bank_balance(user_id)
            
            # Vérification de sécurité des soldes
            if main_balance_before < 0:
                logger.error(f"SÉCURITÉ: Solde principal négatif détecté pour {user_id}: {main_balance_before}")
                embed = create_error_embed("Erreur de sécurité", "Solde principal corrompu détecté. Contactez un admin.")
                await send_func(embed=embed)
                return
            
            # Vérifier le solde principal
            if main_balance_before < amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Tu as **{main_balance_before:,}** PrissBucks mais tu essaies de déposer **{amount:,}** PrissBucks."
                )
                await send_func(embed=embed)
                return
            
            # Vérifier la limite de capacité bancaire
            if bank_balance_before + amount > self.MAX_TOTAL_BANK_BALANCE:
                remaining = self.MAX_TOTAL_BANK_BALANCE - bank_balance_before
                embed = create_error_embed(
                    "Limite de capacité atteinte",
                    f"Tu ne peux déposer que **{remaining:,}** PrissBucks de plus.\n"
                    f"Limite maximale: **{self.MAX_TOTAL_BANK_BALANCE:,}** PrissBucks par compte."
                )
                await send_func(embed=embed)
                return

            # Effectuer les transferts (atomique avec sécurités)
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Débiter le compte principal avec vérification
                    result = await conn.execute(
                        "UPDATE users SET balance = balance - $1 WHERE user_id = $2 AND balance >= $1", 
                        amount, user_id
                    )
                    
                    # Vérifier que le débit a bien eu lieu
                    if result == "UPDATE 0":
                        embed = create_error_embed("Erreur", "Solde insuffisant ou compte verrouillé.")
                        await send_func(embed=embed)
                        return
                    
                    # Créditer le compte bancaire avec vérifications de sécurité
                    success = await self.update_bank_balance(user_id, amount, "deposit")
                    
                    if not success:
                        # La transaction sera automatiquement annulée
                        embed = create_error_embed("Erreur", "Erreur lors du dépôt bancaire.")
                        await send_func(embed=embed)
                        return

            # Calculer les nouveaux soldes et logger les transactions
            main_balance_after = main_balance_before - amount
            bank_balance_after = bank_balance_before + amount

            # Vérifications de cohérence post-transaction
            if main_balance_after < 0 or bank_balance_after < 0:
                logger.error(f"SÉCURITÉ CRITIQUE: Soldes négatifs après dépôt pour {user_id}")
                embed = create_error_embed("Erreur critique", "Transaction annulée pour raisons de sécurité.")
                await send_func(embed=embed)
                return

            # Logger les deux opérations
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_bank_deposit(
                    user_id=user_id,
                    amount=amount,
                    main_balance_before=main_balance_before,
                    main_balance_after=main_balance_after,
                    bank_balance_before=bank_balance_before,
                    bank_balance_after=bank_balance_after
                )

            # Confirmation du dépôt
            embed = discord.Embed(
                title="🏦 Dépôt réussi !",
                description=f"**{amount:,}** PrissBucks ont été déposés dans ta banque privée sécurisée.",
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
            security_ratio = (bank_balance_after / total_wealth) * 100 if total_wealth > 0 else 0
            
            embed.add_field(
                name="💎 Fortune totale",
                value=f"**{total_wealth:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="🔒 Sécurisation",
                value=f"**{security_ratio:.1f}%** de ta fortune est maintenant protégée !",
                inline=False
            )
            
            # Avertissement si proche de la limite
            remaining_capacity = self.MAX_TOTAL_BANK_BALANCE - bank_balance_after
            if remaining_capacity < 100000:  # Moins de 100k de capacité restante
                embed.add_field(
                    name="⚠️ Capacité bancaire",
                    value=f"Plus que **{remaining_capacity:,}** PB de capacité restante.",
                    inline=False
                )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="Argent sécurisé • Invisible des classements • Protection vol • Sécurité renforcée")
            
            await send_func(embed=embed)
            
            # Log de l'action
            logger.info(f"Bank deposit: {user} a déposé {amount} PB (banque: {bank_balance_after}, principal: {main_balance_after}) [LOGGED]")
            
        except Exception as e:
            logger.error(f"Erreur critique deposit {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du dépôt.")
            await send_func(embed=embed)

    # ==================== RETRAIT SÉCURISÉ ====================

    @commands.command(name='withdraw', aliases=['retirer', 'retrait'])
    async def withdraw_cmd(self, ctx, amount: int):
        """e!withdraw <montant> - Retire des PrissBucks de la banque"""
        await self._execute_withdraw(ctx, amount)

    @app_commands.command(name="withdraw", description="Retire des PrissBucks de ta banque privée sécurisée")
    @app_commands.describe(amount="Montant à retirer en PrissBucks")
    async def withdraw_slash(self, interaction: discord.Interaction, amount: int):
        """/withdraw <amount> - Retire de la banque"""
        # Vérifier le cooldown
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
        """Logique commune pour les retraits avec sécurité renforcée et logs"""
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
            # Récupérer les soldes AVANT l'opération pour les logs et vérifications
            main_balance_before = await self.db.get_balance(user_id)
            bank_balance_before = await self.get_bank_balance(user_id)
            
            # Vérifications de sécurité des soldes
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
            if main_balance_before + amount < main_balance_before:  # Détection d'overflow
                embed = create_error_embed(
                    "Montant trop élevé",
                    "Ce retrait créerait un débordement de ton solde principal."
                )
                await send_func(embed=embed)
                return

            # Effectuer les transferts (atomique avec sécurités)
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Débiter le compte bancaire avec vérifications de sécurité
                    success = await self.update_bank_balance(user_id, amount, "withdraw")
                    
                    if not success:
                        embed = create_error_embed("Erreur", "Erreur lors du retrait bancaire.")
                        await send_func(embed=embed)
                        return
                    
                    # Créditer le compte principal avec vérification
                    await conn.execute("""
                        INSERT INTO users (user_id, balance)
                        VALUES ($1, $2)
                        ON CONFLICT (user_id) DO UPDATE SET 
                        balance = CASE 
                            WHEN users.balance + EXCLUDED.balance >= 0 THEN users.balance + EXCLUDED.balance 
                            ELSE users.balance 
                        END
                    """, user_id, amount)

            # Calculer les nouveaux soldes et logger les transactions
            main_balance_after = main_balance_before + amount
            bank_balance_after = bank_balance_before - amount

            # Vérifications de cohérence post-transaction
            if main_balance_after < 0 or bank_balance_after < 0:
                logger.error(f"SÉCURITÉ CRITIQUE: Soldes négatifs après retrait pour {user_id}")
                embed = create_error_embed("Erreur critique", "Transaction annulée pour raisons de sécurité.")
                await send_func(embed=embed)
                return

            # Logger les deux opérations
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_bank_withdraw(
                    user_id=user_id,
                    amount=amount,
                    main_balance_before=main_balance_before,
                    main_balance_after=main_balance_after,
                    bank_balance_before=bank_balance_before,
                    bank_balance_after=bank_balance_after
                )

            # Confirmation du retrait
            embed = discord.Embed(
                title="🏦 Retrait réussi !",
                description=f"**{amount:,}** PrissBucks ont été retirés de ta banque privée sécurisée.",
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
            security_ratio = (bank_balance_after / total_wealth) * 100 if total_wealth > 0 else 0
            
            embed.add_field(
                name="💎 Fortune totale",
                value=f"**{total_wealth:,}** PrissBucks",
                inline=True
            )
            
            if bank_balance_after > 0:
                embed.add_field(
                    name="🔒 Sécurisation",
                    value=f"**{security_ratio:.1f}%** de ta fortune reste protégée en banque.",
                    inline=False
                )
            else:
                embed.add_field(
                    name="⚠️ Attention",
                    value="Ta banque est maintenant vide. Pense à y redéposer pour protéger tes PrissBucks !",
                    inline=False
                )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="Argent transféré vers ton solde principal • Disponible pour dépenser • Sécurité renforcée")
            
            await send_func(embed=embed)
            
            # Log de l'action
            logger.info(f"Bank withdraw: {user} a retiré {amount} PB (banque: {bank_balance_after}, principal: {main_balance_after}) [LOGGED]")
            
        except Exception as e:
            logger.error(f"Erreur critique withdraw {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du retrait.")
            await send_func(embed=embed)

    # ==================== COMMANDES D'INFORMATION ====================

    @commands.command(name='bankhelp', aliases=['banqueaide'])
    async def bank_help_cmd(self, ctx):
        """Affiche l'aide sur le système bancaire sécurisé"""
        embed = discord.Embed(
            title="🏦 Guide de la Banque Privée Sécurisée",
            description="Système de stockage ultra-sécurisé pour tes PrissBucks",
            color=Colors.INFO
        )
        
        embed.add_field(
            name="💡 Pourquoi utiliser la banque ?",
            value="• **Invisible** dans les classements\n"
                  "• **Protection totale** contre le vol\n"
                  "• **100% privé** - personne ne peut voir\n"
                  "• **Sécurité renforcée** - protection anti-corruption\n"
                  "• **Limites intelligentes** - protection débordements",
            inline=False
        )
        
        embed.add_field(
            name="🔧 Commandes disponibles",
            value=f"• `{PREFIX}bank` ou `/bank` - Voir tes infos (privées)\n"
                  f"• `{PREFIX}deposit <montant>` ou `/deposit` - Déposer\n"
                  f"• `{PREFIX}withdraw <montant>` ou `/withdraw` - Retirer\n"
                  f"• `{PREFIX}bankhelp` - Cette aide",
            inline=False
        )
        
        embed.add_field(
            name="📊 Limites et règles sécurisées",
            value=f"• **Dépôt minimum :** {self.MIN_DEPOSIT} PrissBuck\n"
                  f"• **Retrait minimum :** {self.MIN_WITHDRAW} PrissBuck\n"
                  f"• **Maximum par transaction :** {self.MAX_TRANSACTION:,} PrissBucks\n"
                  f"• **Capacité maximale :** {self.MAX_TOTAL_BANK_BALANCE:,} PrissBucks\n"
                  f"• **Cooldown :** 2 secondes entre opérations",
            inline=False
        )
        
        embed.add_field(
            name="🔒 Sécurités intégrées",
            value="• **Protection anti-corruption** des soldes\n"
                  "• **Vérifications de cohérence** automatiques\n"
                  "• **Transactions atomiques** sécurisées\n"
                  "• **Limites intelligentes** contre débordements\n"
                  "• **Logs complets** de toutes les opérations",
            inline=False
        )
        
        embed.add_field(
            name="✅ Cas d'usage recommandés",
            value="• **Stocker de gros montants** à l'abri du vol\n"
                  "• **Cacher ta vraie fortune** des autres joueurs\n"
                  "• **Économiser pour de gros achats** en sécurité\n"
                  "• **Protéger tes gains** de casino/paris\n"
                  "• **Stockage à long terme** sécurisé",
            inline=False
        )
        
        embed.set_footer(text="La banque ne compte PAS dans les classements • 100% privée et ultra-sécurisée")
        await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Bank(bot))