import discord
from discord.ext import commands, tasks
from discord import app_commands
import logging
import asyncio
from datetime import datetime, timezone, timedelta

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_warning_embed

logger = logging.getLogger(__name__)

class LoanSystem(commands.Cog):
    """Système de prêts bancaires avec intérêts et sanctions"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Configuration prêts
        self.MAX_LOAN = 20000
        self.DAILY_INTEREST_RATE = 0.02  # 2% par jour
        self.MAX_LOAN_DURATION_DAYS = 30  # 30 jours max
        self.PENALTY_RATE = 0.10  # 10% pénalité si non remboursé
        
    async def cog_load(self):
        self.db = self.bot.database
        await self._create_loan_tables()
        self.daily_interest_task.start()
        logger.info("✅ Cog LoanSystem initialisé")
    
    async def cog_unload(self):
        self.daily_interest_task.cancel()
    
    async def _create_loan_tables(self):
        """Crée les tables de prêts"""
        if not self.db or not self.db.pool:
            return
        
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_loans (
                        loan_id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        principal BIGINT NOT NULL,
                        remaining_debt BIGINT NOT NULL,
                        interest_accumulated BIGINT DEFAULT 0,
                        loan_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        due_date TIMESTAMP WITH TIME ZONE NOT NULL,
                        last_interest_calculation TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        is_active BOOLEAN DEFAULT TRUE,
                        repaid_at TIMESTAMP WITH TIME ZONE
                    )
                ''')
                
                await conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_user_loans_user_id 
                    ON user_loans(user_id)
                ''')
                
                await conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_user_loans_active 
                    ON user_loans(user_id, is_active)
                ''')
                
                logger.info("✅ Tables loan créées/vérifiées")
        except Exception as e:
            logger.error(f"Erreur création tables loan: {e}")
    
    async def _has_active_loan(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur a un prêt actif"""
        if not self.db.pool:
            return False
        
        try:
            async with self.db.pool.acquire() as conn:
                has_loan = await conn.fetchval("""
                    SELECT 1 FROM user_loans 
                    WHERE user_id = $1 AND is_active = TRUE
                    LIMIT 1
                """, user_id)
                return bool(has_loan)
        except Exception as e:
            logger.error(f"Erreur check active loan: {e}")
            return False
    
    async def _get_active_loan(self, user_id: int) -> dict:
        """Récupère le prêt actif d'un utilisateur"""
        if not self.db.pool:
            return None
        
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT * FROM user_loans 
                    WHERE user_id = $1 AND is_active = TRUE
                    LIMIT 1
                """, user_id)
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Erreur get active loan: {e}")
            return None
    
    @tasks.loop(hours=24)
    async def daily_interest_task(self):
        """Applique les intérêts quotidiens"""
        if not self.db or not self.db.pool:
            return
        
        try:
            async with self.db.pool.acquire() as conn:
                # Récupérer tous les prêts actifs
                active_loans = await conn.fetch("""
                    SELECT loan_id, user_id, remaining_debt, due_date
                    FROM user_loans 
                    WHERE is_active = TRUE
                """)
                
                now = datetime.now(timezone.utc)
                
                for loan in active_loans:
                    loan_id = loan['loan_id']
                    user_id = loan['user_id']
                    current_debt = loan['remaining_debt']
                    due_date = loan['due_date']
                    
                    # Calculer intérêts
                    interest = int(current_debt * self.DAILY_INTEREST_RATE)
                    new_debt = current_debt + interest
                    
                    # Mettre à jour
                    await conn.execute("""
                        UPDATE user_loans 
                        SET remaining_debt = $1,
                            interest_accumulated = interest_accumulated + $2,
                            last_interest_calculation = NOW()
                        WHERE loan_id = $3
                    """, new_debt, interest, loan_id)
                    
                    # Vérifier échéance dépassée
                    if now > due_date:
                        penalty = int(new_debt * self.PENALTY_RATE)
                        await conn.execute("""
                            UPDATE user_loans 
                            SET remaining_debt = remaining_debt + $1
                            WHERE loan_id = $2
                        """, penalty, loan_id)
                        
                        logger.warning(f"Prêt {loan_id} en retard - Pénalité {penalty} appliquée")
                
                logger.info(f"Intérêts quotidiens appliqués sur {len(active_loans)} prêts")
                
        except Exception as e:
            logger.error(f"Erreur daily interest: {e}")
    
    @daily_interest_task.before_loop
    async def before_daily_interest(self):
        await self.bot.wait_until_ready()
    
    # ==================== COMMANDES ====================
    
    @commands.command(name='loan', aliases=['pret', 'emprunt'])
    async def loan_cmd(self, ctx, amount: int):
        """Demande un prêt"""
        await self._execute_loan(ctx, amount)
    
    @app_commands.command(name="loan", description="Demande un prêt bancaire (max 20k, 2% intérêts/jour)")
    @app_commands.describe(montant="Montant à emprunter (1-20000 PB)")
    async def loan_slash(self, interaction: discord.Interaction, montant: int):
        await interaction.response.defer()
        await self._execute_loan(interaction, montant, is_slash=True)
    
    async def _execute_loan(self, ctx_or_interaction, amount, is_slash=False):
        """Demande un prêt"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        
        # Validations
        if amount < 1 or amount > self.MAX_LOAN:
            embed = create_error_embed(
                "Montant invalide",
                f"Tu peux emprunter entre 1 et {self.MAX_LOAN:,} PrissBucks."
            )
            await send_func(embed=embed)
            return
        
        # Vérifier prêt existant
        if await self._has_active_loan(user_id):
            embed = create_error_embed(
                "Prêt déjà actif",
                "Tu as déjà un prêt en cours ! Rembourse-le avant d'en demander un autre."
            )
            await send_func(embed=embed)
            return
        
        try:
            # Créer le prêt
            now = datetime.now(timezone.utc)
            due_date = now + timedelta(days=self.MAX_LOAN_DURATION_DAYS)
            
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Créer l'entrée prêt
                    loan_id = await conn.fetchval("""
                        INSERT INTO user_loans 
                        (user_id, principal, remaining_debt, due_date)
                        VALUES ($1, $2, $2, $3)
                        RETURNING loan_id
                    """, user_id, amount, due_date)
                    
                    # Créditer l'utilisateur
                    await conn.execute("""
                        UPDATE users SET balance = balance + $1 
                        WHERE user_id = $2
                    """, amount, user_id)
            
            # Logger
            if hasattr(self.bot, 'transaction_logs'):
                balance_before = await self.db.get_balance(user_id) - amount
                balance_after = await self.db.get_balance(user_id)
                await self.bot.transaction_logs.log_transaction(
                    user_id=user_id,
                    transaction_type='loan_received',
                    amount=amount,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    description=f"Prêt bancaire #{loan_id}"
                )
            
            # Confirmation
            embed = discord.Embed(
                title="🏦 Prêt accordé !",
                description=f"Prêt de **{amount:,} PrissBucks** approuvé !",
                color=Colors.SUCCESS
            )
            
            embed.add_field(
                name="💰 Montant reçu",
                value=f"**{amount:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📅 À rembourser avant",
                value=f"<t:{int(due_date.timestamp())}:D>",
                inline=True
            )
            
            embed.add_field(
                name="📈 Intérêts",
                value=f"**{self.DAILY_INTEREST_RATE*100}%** par jour",
                inline=True
            )
            
            daily_interest = int(amount * self.DAILY_INTEREST_RATE)
            embed.add_field(
                name="⚠️ Attention",
                value=f"• Intérêts quotidiens: ~{daily_interest:,} PB/jour\n"
                      f"• Durée max: {self.MAX_LOAN_DURATION_DAYS} jours\n"
                      f"• Pénalité retard: {self.PENALTY_RATE*100}%\n"
                      f"• Remboursement obligatoire !",
                inline=False
            )
            
            embed.add_field(
                name="💳 Remboursement",
                value=f"Utilise `{PREFIX}repay <montant>` pour rembourser",
                inline=False
            )
            
            embed.set_footer(text=f"ID Prêt: #{loan_id}")
            await send_func(embed=embed)
            
            logger.info(f"Loan created: User {user_id} borrowed {amount} PB (ID: {loan_id})")
            
        except Exception as e:
            logger.error(f"Erreur loan {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la création du prêt.")
            await send_func(embed=embed)
    
    @commands.command(name='repay', aliases=['rembourser', 'payback'])
    async def repay_cmd(self, ctx, amount: int):
        """Rembourse un prêt"""
        await self._execute_repay(ctx, amount)
    
    @app_commands.command(name="repay", description="Rembourse ton prêt bancaire")
    @app_commands.describe(montant="Montant à rembourser")
    async def repay_slash(self, interaction: discord.Interaction, montant: int):
        await interaction.response.defer()
        await self._execute_repay(interaction, montant, is_slash=True)
    
    async def _execute_repay(self, ctx_or_interaction, amount, is_slash=False):
        """Rembourse un prêt"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        
        if amount <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif.")
            await send_func(embed=embed)
            return
        
        try:
            # Récupérer prêt actif
            loan = await self._get_active_loan(user_id)
            if not loan:
                embed = create_error_embed(
                    "Aucun prêt actif",
                    "Tu n'as pas de prêt à rembourser."
                )
                await send_func(embed=embed)
                return
            
            # Vérifier solde
            balance = await self.db.get_balance(user_id)
            if balance < amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Ton solde: {balance:,} PB\nMontant: {amount:,} PB"
                )
                await send_func(embed=embed)
                return
            
            remaining_debt = loan['remaining_debt']
            amount_to_repay = min(amount, remaining_debt)
            
            # Transaction
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Débiter utilisateur
                    await conn.execute("""
                        UPDATE users SET balance = balance - $1 
                        WHERE user_id = $2
                    """, amount_to_repay, user_id)
                    
                    # Mettre à jour prêt
                    new_debt = remaining_debt - amount_to_repay
                    
                    if new_debt <= 0:
                        # Prêt remboursé
                        await conn.execute("""
                            UPDATE user_loans 
                            SET remaining_debt = 0, is_active = FALSE, repaid_at = NOW()
                            WHERE loan_id = $1
                        """, loan['loan_id'])
                    else:
                        await conn.execute("""
                            UPDATE user_loans 
                            SET remaining_debt = $1
                            WHERE loan_id = $2
                        """, new_debt, loan['loan_id'])
            
            # Confirmation
            if new_debt <= 0:
                embed = discord.Embed(
                    title="✅ Prêt remboursé !",
                    description=f"Tu as remboursé ton prêt intégralement !",
                    color=Colors.SUCCESS
                )
                embed.add_field(
                    name="💰 Montant remboursé",
                    value=f"**{amount_to_repay:,}** PrissBucks",
                    inline=True
                )
                embed.add_field(
                    name="📊 Total intérêts payés",
                    value=f"**{loan['interest_accumulated']:,}** PrissBucks",
                    inline=True
                )
                embed.add_field(
                    name="🎉 Félicitations",
                    value="Tu peux maintenant demander un nouveau prêt si besoin !",
                    inline=False
                )
            else:
                embed = discord.Embed(
                    title="💳 Remboursement partiel",
                    description=f"Remboursement de **{amount_to_repay:,} PB** effectué !",
                    color=Colors.INFO
                )
                embed.add_field(
                    name="📉 Dette restante",
                    value=f"**{new_debt:,}** PrissBucks",
                    inline=True
                )
                
                days_remaining = (loan['due_date'] - datetime.now(timezone.utc)).days
                embed.add_field(
                    name="⏳ Temps restant",
                    value=f"**{days_remaining}** jour(s)",
                    inline=True
                )
                
                daily_interest = int(new_debt * self.DAILY_INTEREST_RATE)
                embed.add_field(
                    name="📈 Intérêts/jour",
                    value=f"~{daily_interest:,} PB",
                    inline=True
                )
            
            embed.set_footer(text=f"Prêt #{loan['loan_id']}")
            await send_func(embed=embed)
            
            logger.info(f"Loan repayment: User {user_id} paid {amount_to_repay} PB (Loan #{loan['loan_id']})")
            
        except Exception as e:
            logger.error(f"Erreur repay {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du remboursement.")
            await send_func(embed=embed)
    
    @commands.command(name='loan_status', aliases=['loan_info', 'pret_info'])
    async def loan_status_cmd(self, ctx):
        """Affiche le statut du prêt"""
        await self._execute_loan_status(ctx)
    
    @app_commands.command(name="loan_status", description="Affiche les détails de ton prêt actif")
    async def loan_status_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._execute_loan_status(interaction, is_slash=True)
    
    async def _execute_loan_status(self, ctx_or_interaction, is_slash=False):
        """Affiche le statut du prêt"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        
        try:
            loan = await self._get_active_loan(user_id)
            
            if not loan:
                embed = discord.Embed(
                    title="🏦 Aucun prêt actif",
                    description="Tu n'as pas de prêt en cours.",
                    color=Colors.INFO
                )
                embed.add_field(
                    name="💡 Besoin d'argent ?",
                    value=f"Utilise `{PREFIX}loan <montant>` pour emprunter jusqu'à {self.MAX_LOAN:,} PB",
                    inline=False
                )
                await send_func(embed=embed)
                return
            
            # Calculs
            principal = loan['principal']
            remaining_debt = loan['remaining_debt']
            interest_accumulated = loan['interest_accumulated']
            loan_date = loan['loan_date']
            due_date = loan['due_date']
            
            now = datetime.now(timezone.utc)
            days_elapsed = (now - loan_date).days
            days_remaining = (due_date - now).days
            
            daily_interest = int(remaining_debt * self.DAILY_INTEREST_RATE)
            
            # Couleur selon urgence
            if days_remaining <= 3:
                color = Colors.ERROR
                urgency = "🚨 URGENT"
            elif days_remaining <= 7:
                color = Colors.WARNING
                urgency = "⚠️ Attention"
            else:
                color = Colors.INFO
                urgency = "✅ Normal"
            
            embed = discord.Embed(
                title="🏦 Statut de ton prêt",
                description=f"**{urgency}** - Prêt #{loan['loan_id']}",
                color=color
            )
            
            embed.add_field(
                name="💰 Montant initial",
                value=f"**{principal:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📉 Dette actuelle",
                value=f"**{remaining_debt:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📈 Intérêts accumulés",
                value=f"**{interest_accumulated:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📅 Date d'emprunt",
                value=f"<t:{int(loan_date.timestamp())}:D>",
                inline=True
            )
            
            embed.add_field(
                name="⏰ Échéance",
                value=f"<t:{int(due_date.timestamp())}:D>",
                inline=True
            )
            
            embed.add_field(
                name="⏳ Temps restant",
                value=f"**{days_remaining}** jour(s)",
                inline=True
            )
            
            embed.add_field(
                name="📊 Statistiques",
                value=f"• Durée: {days_elapsed} jour(s)\n"
                      f"• Intérêts/jour: ~{daily_interest:,} PB\n"
                      f"• Taux: {self.DAILY_INTEREST_RATE*100}%/jour",
                inline=False
            )
            
            if days_remaining <= 0:
                penalty = int(remaining_debt * self.PENALTY_RATE)
                embed.add_field(
                    name="⚠️ RETARD DE PAIEMENT",
                    value=f"Pénalité quotidienne: **{self.PENALTY_RATE*100}%** ({penalty:,} PB/jour)\n"
                          f"Rembourse rapidement pour éviter plus de frais !",
                    inline=False
                )
            
            embed.add_field(
                name="💳 Remboursement",
                value=f"Utilise `{PREFIX}repay <montant>` pour rembourser",
                inline=False
            )
            
            embed.set_footer(text="Les intérêts sont calculés automatiquement chaque jour")
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur loan_status {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération du prêt.")
            await send_func(embed=embed)

async def setup(bot):
    await bot.add_cog(LoanSystem(bot))
