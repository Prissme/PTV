import discord
from discord.ext import commands
from discord import app_commands
import logging
import asyncio
import random
from datetime import datetime, timezone, timedelta
import math

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_info_embed

logger = logging.getLogger(__name__)

class PublicBank(commands.Cog):
    """Système de banque publique alimentée par les pertes casino - accessible à tous les joueurs"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Configuration de la banque publique
        self.MIN_WITHDRAW = 50
        self.MAX_WITHDRAW = 1000  # Limite par retrait
        self.DAILY_WITHDRAW_LIMIT = 2000  # Limite par jour par utilisateur
        
        # Dictionnaire pour les cooldowns de retrait
        self.withdraw_cooldowns = {}
        self.daily_withdrawals = {}  # {user_id: {'date': date, 'amount': total_withdrawn}}
        
        # Dictionnaire pour les cooldowns d'information
        self.info_cooldowns = {}
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        await self.create_public_bank_table()
        logger.info("✅ Cog PublicBank initialisé - Banque publique alimentée par les pertes casino")
    
    async def create_public_bank_table(self):
        """Crée les tables pour la banque publique"""
        if not self.db.pool:
            return
            
        async with self.db.pool.acquire() as conn:
            # Table pour le solde de la banque publique
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS public_bank (
                    id SERIAL PRIMARY KEY,
                    balance BIGINT DEFAULT 0,
                    total_deposited BIGINT DEFAULT 0,
                    total_withdrawn BIGINT DEFAULT 0,
                    last_activity TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            
            # Table pour l'historique des retraits des utilisateurs
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS public_bank_withdrawals (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    amount BIGINT NOT NULL,
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    remaining_balance BIGINT NOT NULL
                )
            ''')
            
            # Créer l'enregistrement initial de la banque publique s'il n'existe pas
            await conn.execute('''
                INSERT INTO public_bank (id, balance, total_deposited, total_withdrawn)
                VALUES (1, 0, 0, 0)
                ON CONFLICT (id) DO NOTHING
            ''')
            
            # Index pour optimiser les requêtes
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_public_bank_withdrawals_user_id 
                ON public_bank_withdrawals(user_id)
            ''')
            
            logger.info("✅ Tables public_bank créées/vérifiées")

    def _check_withdraw_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown de retrait (30 minutes)"""
        import time
        now = time.time()
        cooldown_duration = 1800  # 30 minutes
        if user_id in self.withdraw_cooldowns:
            elapsed = now - self.withdraw_cooldowns[user_id]
            if elapsed < cooldown_duration:
                return cooldown_duration - elapsed
        return 0

    def _set_withdraw_cooldown(self, user_id: int):
        """Définit le cooldown de retrait"""
        import time
        self.withdraw_cooldowns[user_id] = time.time()

    def _check_daily_limit(self, user_id: int) -> tuple:
        """Vérifie la limite quotidienne de retrait - retourne (amount_withdrawn_today, remaining)"""
        today = datetime.now(timezone.utc).date()
        
        if user_id not in self.daily_withdrawals:
            self.daily_withdrawals[user_id] = {'date': today, 'amount': 0}
            return 0, self.DAILY_WITHDRAW_LIMIT
        
        user_data = self.daily_withdrawals[user_id]
        if user_data['date'] != today:
            # Nouveau jour, reset le compteur
            user_data['date'] = today
            user_data['amount'] = 0
            
        withdrawn_today = user_data['amount']
        remaining = max(0, self.DAILY_WITHDRAW_LIMIT - withdrawn_today)
        return withdrawn_today, remaining

    def _add_daily_withdrawal(self, user_id: int, amount: int):
        """Ajoute un montant aux retraits quotidiens"""
        today = datetime.now(timezone.utc).date()
        if user_id not in self.daily_withdrawals:
            self.daily_withdrawals[user_id] = {'date': today, 'amount': 0}
        
        if self.daily_withdrawals[user_id]['date'] == today:
            self.daily_withdrawals[user_id]['amount'] += amount
        else:
            self.daily_withdrawals[user_id] = {'date': today, 'amount': amount}

    async def get_public_bank_balance(self) -> dict:
        """Récupère les informations de la banque publique"""
        if not self.db.pool:
            return {'balance': 0, 'total_deposited': 0, 'total_withdrawn': 0}
            
        async with self.db.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM public_bank WHERE id = 1")
            if row:
                return dict(row)
            else:
                return {'balance': 0, 'total_deposited': 0, 'total_withdrawn': 0}

    async def add_casino_loss(self, amount: int, source: str = "casino") -> bool:
        """Ajoute de l'argent à la banque publique (appelé quand quelqu'un perd au casino)"""
        if not self.db.pool or amount <= 0:
            return False
            
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute('''
                    UPDATE public_bank 
                    SET balance = balance + $1,
                        total_deposited = total_deposited + $1,
                        last_activity = NOW()
                    WHERE id = 1
                ''', amount)
                
                logger.info(f"🏛️ PublicBank: +{amount} PB ajoutés (source: {source})")
                return True
        except Exception as e:
            logger.error(f"Erreur ajout public bank: {e}")
            return False

    async def withdraw_from_public_bank(self, user_id: int, amount: int) -> bool:
        """Retire de l'argent de la banque publique et l'ajoute à l'utilisateur"""
        if not self.db.pool or amount <= 0:
            return False
            
        try:
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Vérifier le solde de la banque publique
                    bank_row = await conn.fetchrow("SELECT balance FROM public_bank WHERE id = 1")
                    if not bank_row or bank_row['balance'] < amount:
                        return False
                    
                    # Débiter la banque publique
                    await conn.execute('''
                        UPDATE public_bank 
                        SET balance = balance - $1,
                            total_withdrawn = total_withdrawn + $1,
                            last_activity = NOW()
                        WHERE id = 1
                    ''', amount)
                    
                    # Créditer l'utilisateur
                    await conn.execute("""
                        INSERT INTO users (user_id, balance)
                        VALUES ($1, $2)
                        ON CONFLICT (user_id) DO UPDATE SET balance = users.balance + EXCLUDED.balance
                    """, user_id, amount)
                    
                    # Enregistrer le retrait
                    new_balance = bank_row['balance'] - amount
                    await conn.execute('''
                        INSERT INTO public_bank_withdrawals (user_id, amount, remaining_balance)
                        VALUES ($1, $2, $3)
                    ''', user_id, amount, new_balance)
                    
                    logger.info(f"🏛️ PublicBank: {amount} PB retirés par user {user_id}")
                    return True
        except Exception as e:
            logger.error(f"Erreur retrait public bank: {e}")
            return False

    # ==================== COMMANDES PUBLIQUES ====================

    @commands.command(name='publicbank', aliases=['banquepublique', 'bp', 'casinobank'])
    async def public_bank_info_cmd(self, ctx):
        """e!publicbank - Affiche les informations de la banque publique"""
        await self._execute_public_bank_info(ctx)

    @app_commands.command(name="publicbank", description="Affiche les informations de la banque publique alimentée par les pertes casino")
    async def public_bank_info_slash(self, interaction: discord.Interaction):
        """/publicbank - Informations sur la banque publique"""
        await interaction.response.defer()
        await self._execute_public_bank_info(interaction, is_slash=True)

    async def _execute_public_bank_info(self, ctx_or_interaction, is_slash=False):
        """Logique commune pour afficher les infos de la banque publique"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        # Cooldown d'info (5 secondes)
        import time
        now = time.time()
        if user.id in self.info_cooldowns:
            elapsed = now - self.info_cooldowns[user.id]
            if elapsed < 5:
                remaining = 5 - elapsed
                embed = create_error_embed(
                    "Cooldown",
                    f"Attends **{remaining:.1f}s** avant de revoir les infos."
                )
                await send_func(embed=embed, ephemeral=True if is_slash else False)
                return
        
        self.info_cooldowns[user.id] = now

        try:
            bank_info = await self.get_public_bank_balance()
            withdrawn_today, remaining_today = self._check_daily_limit(user.id)
            cooldown_remaining = self._check_withdraw_cooldown(user.id)
            
            embed = discord.Embed(
                title="🏛️ Banque Publique Casino",
                description="**Fonds alimentés par les pertes des jeux de casino**\n"
                           "🎰 Roulette • 🎮 Pierre-Papier-Ciseaux • 🎲 Autres jeux",
                color=Colors.GOLD
            )
            
            # Informations principales
            embed.add_field(
                name="💰 Solde disponible",
                value=f"**{bank_info['balance']:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📈 Total collecté",
                value=f"**{bank_info['total_deposited']:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📉 Total retiré",
                value=f"**{bank_info['total_withdrawn']:,}** PrissBucks",
                inline=True
            )
            
            # Tes limites personnelles
            embed.add_field(
                name="📊 Tes limites quotidiennes",
                value=f"🔸 **Retiré aujourd'hui:** {withdrawn_today:,} PB\n"
                      f"🔸 **Restant aujourd'hui:** {remaining_today:,} PB\n"
                      f"🔸 **Limite par retrait:** {self.MIN_WITHDRAW}-{self.MAX_WITHDRAW} PB",
                inline=False
            )
            
            # Statut de cooldown
            if cooldown_remaining > 0:
                minutes = int(cooldown_remaining // 60)
                seconds = int(cooldown_remaining % 60)
                cooldown_text = f"⏰ **{minutes}min {seconds}s** restantes"
            else:
                cooldown_text = "✅ **Disponible**"
                
            embed.add_field(
                name="⏰ Ton cooldown de retrait",
                value=cooldown_text,
                inline=True
            )
            
            # Comment ça marche
            embed.add_field(
                name="💡 Comment ça fonctionne ?",
                value="• **Alimentée automatiquement** par les pertes casino\n"
                      "• **Accessible à tous** les joueurs du serveur\n"
                      "• **Limite quotidienne** pour éviter l'abus\n"
                      "• **Cooldown 30min** entre chaque retrait\n"
                      "• **Redistribution équitable** des fonds perdus",
                inline=False
            )
            
            # Instructions d'utilisation
            embed.add_field(
                name="🚀 Comment retirer ?",
                value=f"• `{PREFIX}withdraw_public <montant>` - Retirer des PrissBucks\n"
                      f"• `/withdraw_public <montant>` - Version slash command\n"
                      f"• Montant minimum: **{self.MIN_WITHDRAW}** PB\n"
                      f"• Montant maximum: **{self.MAX_WITHDRAW}** PB par retrait",
                inline=False
            )
            
            # Statistiques amusantes
            if bank_info['total_deposited'] > 0:
                retention_rate = (bank_info['balance'] / bank_info['total_deposited']) * 100
                embed.add_field(
                    name="📊 Statistiques",
                    value=f"🏦 **Taux de rétention:** {retention_rate:.1f}%\n"
                          f"♻️ **Fonds redistribués:** {bank_info['total_withdrawn']:,} PB\n"
                          f"🎯 **Solidarité casino:** Actif",
                    inline=True
                )
            
            embed.set_footer(text="La banque publique redistribue les pertes casino à tous les joueurs !")
            embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/1234567890.png")  # Emoji banque
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur public bank info {user.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des informations.")
            await send_func(embed=embed)

    @commands.command(name='withdraw_public', aliases=['retirer_public', 'wp'])
    async def withdraw_public_cmd(self, ctx, amount: int):
        """e!withdraw_public <montant> - Retire des PrissBucks de la banque publique"""
        await self._execute_withdraw_public(ctx, amount)

    @app_commands.command(name="withdraw_public", description="Retire des PrissBucks de la banque publique alimentée par les pertes casino")
    @app_commands.describe(amount="Montant à retirer en PrissBucks")
    async def withdraw_public_slash(self, interaction: discord.Interaction, amount: int):
        """/withdraw_public <amount> - Retire de la banque publique"""
        await interaction.response.defer()
        await self._execute_withdraw_public(interaction, amount, is_slash=True)

    async def _execute_withdraw_public(self, ctx_or_interaction, amount, is_slash=False):
        """Logique commune pour les retraits de la banque publique"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        user_id = user.id

        # Validations de base
        if amount < self.MIN_WITHDRAW:
            embed = create_error_embed(
                "Montant trop faible",
                f"Le montant minimum de retrait est **{self.MIN_WITHDRAW}** PrissBucks."
            )
            await send_func(embed=embed)
            return

        if amount > self.MAX_WITHDRAW:
            embed = create_error_embed(
                "Montant trop élevé", 
                f"Le montant maximum par retrait est **{self.MAX_WITHDRAW:,}** PrissBucks."
            )
            await send_func(embed=embed)
            return

        # Vérifier le cooldown
        cooldown_remaining = self._check_withdraw_cooldown(user_id)
        if cooldown_remaining > 0:
            minutes = int(cooldown_remaining // 60)
            seconds = int(cooldown_remaining % 60)
            embed = discord.Embed(
                title="⏰ Cooldown actif !",
                description=f"Tu pourras retirer de la banque publique dans **{minutes}min {seconds}s**.",
                color=Colors.WARNING
            )
            embed.add_field(
                name="💡 Pourquoi ce cooldown ?",
                value="Pour éviter l'abus et garantir un accès équitable à tous les joueurs.",
                inline=False
            )
            await send_func(embed=embed)
            return

        # Vérifier la limite quotidienne
        withdrawn_today, remaining_today = self._check_daily_limit(user_id)
        if amount > remaining_today:
            embed = create_error_embed(
                "Limite quotidienne atteinte",
                f"Tu as déjà retiré **{withdrawn_today:,}** PB aujourd'hui.\n"
                f"Limite restante: **{remaining_today:,}** PB\n"
                f"Réessaie demain ou retire moins !"
            )
            if remaining_today > 0:
                embed.add_field(
                    name="💡 Suggestion",
                    value=f"Tu peux encore retirer **{min(remaining_today, self.MAX_WITHDRAW):,}** PB aujourd'hui.",
                    inline=False
                )
            await send_func(embed=embed)
            return

        try:
            # Vérifier le solde de la banque publique
            bank_info = await self.get_public_bank_balance()
            if bank_info['balance'] < amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"La banque publique n'a que **{bank_info['balance']:,}** PrissBucks disponibles.\n"
                    f"Tu demandes **{amount:,}** PrissBucks."
                )
                
                if bank_info['balance'] >= self.MIN_WITHDRAW:
                    suggested_amount = min(bank_info['balance'], self.MAX_WITHDRAW, remaining_today)
                    embed.add_field(
                        name="💡 Suggestion",
                        value=f"Retire plutôt **{suggested_amount:,}** PrissBucks.",
                        inline=False
                    )
                
                embed.add_field(
                    name="🎰 Comment la banque se remplit ?",
                    value="Elle se remplit automatiquement quand des joueurs perdent au casino !",
                    inline=False
                )
                await send_func(embed=embed)
                return

            # Récupérer le solde AVANT le retrait pour les logs
            user_balance_before = await self.db.get_balance(user_id)

            # Effectuer le retrait
            success = await self.withdraw_from_public_bank(user_id, amount)
            
            if not success:
                embed = create_error_embed("Erreur", "Erreur lors du retrait de la banque publique.")
                await send_func(embed=embed)
                return

            # Marquer le cooldown et la limite quotidienne
            self._set_withdraw_cooldown(user_id)
            self._add_daily_withdrawal(user_id, amount)

            # Calculer les nouveaux soldes pour les logs
            user_balance_after = user_balance_before + amount
            new_bank_info = await self.get_public_bank_balance()

            # Logger la transaction
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_transaction(
                    user_id=user_id,
                    transaction_type='public_bank_withdraw',
                    amount=amount,
                    balance_before=user_balance_before,
                    balance_after=user_balance_after,
                    description=f"Retrait banque publique +{amount} PB"
                )

            # Message de confirmation
            embed = discord.Embed(
                title="🏛️ Retrait réussi !",
                description=f"Tu as retiré **{amount:,}** PrissBucks de la banque publique !",
                color=Colors.SUCCESS
            )
            
            embed.add_field(
                name="💰 Ton nouveau solde",
                value=f"**{user_balance_after:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="🏛️ Banque publique restante",
                value=f"**{new_bank_info['balance']:,}** PrissBucks",
                inline=True
            )
            
            # Nouvelles limites
            new_withdrawn, new_remaining = self._check_daily_limit(user_id)
            embed.add_field(
                name="📊 Tes nouvelles limites",
                value=f"**Retiré aujourd'hui:** {new_withdrawn:,} PB\n"
                      f"**Restant aujourd'hui:** {new_remaining:,} PB",
                inline=True
            )
            
            embed.add_field(
                name="⏰ Prochains retraits",
                value="🔸 **Cooldown:** 30 minutes\n"
                      f"🔸 **Reset quotidien:** Minuit UTC\n"
                      f"🔸 **Limite par retrait:** {self.MAX_WITHDRAW:,} PB",
                inline=False
            )
            
            # Message de solidarité
            motivational_messages = [
                "🤝 **Merci à tous les joueurs casino** qui alimentent cette banque !",
                "♻️ **Redistribution équitable** des pertes casino en action !",
                "🎯 **Solidarité joueur** - Nous perdons ensemble, nous gagnons ensemble !",
                "🏆 **Système communautaire** - Les pertes des uns profitent aux autres !"
            ]
            
            embed.add_field(
                name="💬 Message",
                value=random.choice(motivational_messages),
                inline=False
            )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="Banque publique • Alimentée par les pertes casino • Accessible à tous")
            
            await send_func(embed=embed)
            
            # Log de l'action
            logger.info(f"PublicBank withdraw: {user} a retiré {amount} PB (nouveau solde: {user_balance_after}, banque: {new_bank_info['balance']})")
            
        except Exception as e:
            logger.error(f"Erreur withdraw_public {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du retrait.")
            await send_func(embed=embed)

    # ==================== COMMANDES STATISTIQUES ====================

    @commands.command(name='public_stats', aliases=['stats_publique'])
    async def public_stats_cmd(self, ctx):
        """Affiche les statistiques détaillées de la banque publique"""
        try:
            # Récupérer les statistiques globales
            bank_info = await self.get_public_bank_balance()
            
            # Récupérer les statistiques de retraits récents
            async with self.db.pool.acquire() as conn:
                # Retraits des dernières 24h
                recent_withdrawals = await conn.fetch('''
                    SELECT COUNT(*) as count, COALESCE(SUM(amount), 0) as total
                    FROM public_bank_withdrawals 
                    WHERE timestamp > NOW() - INTERVAL '24 hours'
                ''')
                
                # Top 5 des plus gros retraits récents
                top_withdrawals = await conn.fetch('''
                    SELECT user_id, amount, timestamp
                    FROM public_bank_withdrawals 
                    ORDER BY amount DESC
                    LIMIT 5
                ''')
            
            embed = discord.Embed(
                title="📊 Statistiques Banque Publique",
                description="Données détaillées sur la redistribution des pertes casino",
                color=Colors.INFO
            )
            
            # Statistiques principales
            embed.add_field(
                name="💰 Solde actuel",
                value=f"**{bank_info['balance']:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📈 Total collecté",
                value=f"**{bank_info['total_deposited']:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📉 Total redistribué",
                value=f"**{bank_info['total_withdrawn']:,}** PrissBucks",
                inline=True
            )
            
            # Statistiques récentes
            recent_data = recent_withdrawals[0] if recent_withdrawals else {'count': 0, 'total': 0}
            embed.add_field(
                name="⏰ Dernières 24h",
                value=f"🔸 **{recent_data['count']}** retraits\n"
                      f"🔸 **{recent_data['total']:,}** PB redistribués",
                inline=True
            )
            
            # Calculs avancés
            if bank_info['total_deposited'] > 0:
                redistribution_rate = (bank_info['total_withdrawn'] / bank_info['total_deposited']) * 100
                retention_rate = (bank_info['balance'] / bank_info['total_deposited']) * 100
                
                embed.add_field(
                    name="📊 Taux de redistribution",
                    value=f"**{redistribution_rate:.1f}%** des fonds collectés",
                    inline=True
                )
                
                embed.add_field(
                    name="🏦 Taux de rétention",
                    value=f"**{retention_rate:.1f}%** en réserve",
                    inline=True
                )
            
            # Top des retraits
            if top_withdrawals:
                top_text = ""
                for i, withdrawal in enumerate(top_withdrawals, 1):
                    try:
                        user = self.bot.get_user(withdrawal['user_id'])
                        username = user.display_name if user else f"User#{withdrawal['user_id']}"
                    except:
                        username = f"User#{withdrawal['user_id']}"
                    
                    date_str = withdrawal['timestamp'].strftime("%d/%m")
                    top_text += f"{i}. **{username}** - {withdrawal['amount']:,} PB ({date_str})\n"
                
                embed.add_field(
                    name="🏆 Top des retraits",
                    value=top_text,
                    inline=False
                )
            
            # Impact social
            embed.add_field(
                name="🤝 Impact social",
                value="Cette banque transforme les pertes individuelles en gains collectifs,\n"
                      "créant un filet de sécurité social pour tous les joueurs du serveur.",
                inline=False
            )
            
            embed.set_footer(text="Solidarité casino • Redistribution équitable • Accessible à tous")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur public_stats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)

    # ==================== MÉTHODES D'INTÉGRATION ====================
    
    def get_public_bank_cog(self):
        """Méthode pour que les autres cogs puissent accéder facilement à cette instance"""
        return self

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(PublicBank(bot))
