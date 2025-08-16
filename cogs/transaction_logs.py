import discord
from discord.ext import commands
from discord import app_commands
import logging
from datetime import datetime, timezone
import math

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_info_embed

logger = logging.getLogger(__name__)

class TransactionLogs(commands.Cog):
    """Système d'historique des transactions pour les utilisateurs"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        
        # Créer la table des logs de transactions si elle n'existe pas
        await self.create_transaction_logs_table()
        logger.info("✅ Cog TransactionLogs initialisé avec table de logs")
    
    async def create_transaction_logs_table(self):
        """Crée la table pour stocker les logs de transactions"""
        if not self.db.pool:
            return
            
        async with self.db.pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS transaction_logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    transaction_type VARCHAR(50) NOT NULL,
                    amount BIGINT NOT NULL,
                    balance_before BIGINT NOT NULL,
                    balance_after BIGINT NOT NULL,
                    description TEXT,
                    related_user_id BIGINT,
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            
            # Index pour optimiser les requêtes
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_transaction_logs_user_id ON transaction_logs(user_id)
            ''')
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_transaction_logs_timestamp ON transaction_logs(timestamp DESC)
            ''')
            
            logger.info("✅ Table transaction_logs créée/vérifiée")

    async def log_transaction(self, user_id: int, transaction_type: str, amount: int, 
                             balance_before: int, balance_after: int, description: str = None, 
                             related_user_id: int = None):
        """Enregistre une transaction dans les logs"""
        if not self.db.pool:
            return
            
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO transaction_logs 
                    (user_id, transaction_type, amount, balance_before, balance_after, description, related_user_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                ''', user_id, transaction_type, amount, balance_before, balance_after, description, related_user_id)
        except Exception as e:
            logger.error(f"Erreur log transaction {user_id}: {e}")

    async def get_user_transactions(self, user_id: int, limit: int = 20, offset: int = 0):
        """Récupère l'historique des transactions d'un utilisateur"""
        if not self.db.pool:
            return []
            
        try:
            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT transaction_type, amount, balance_before, balance_after, 
                           description, related_user_id, timestamp
                    FROM transaction_logs 
                    WHERE user_id = $1 
                    ORDER BY timestamp DESC 
                    LIMIT $2 OFFSET $3
                ''', user_id, limit, offset)
                
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Erreur récupération transactions {user_id}: {e}")
            return []

    async def get_transaction_count(self, user_id: int) -> int:
        """Récupère le nombre total de transactions d'un utilisateur"""
        if not self.db.pool:
            return 0
            
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    SELECT COUNT(*) as count FROM transaction_logs WHERE user_id = $1
                ''', user_id)
                return row['count'] if row else 0
        except Exception as e:
            logger.error(f"Erreur count transactions {user_id}: {e}")
            return 0

    def format_transaction(self, transaction: dict) -> str:
        """Formate une transaction pour l'affichage"""
        trans_type = transaction['transaction_type']
        amount = transaction['amount']
        description = transaction.get('description', '')
        timestamp = transaction['timestamp']
        balance_after = transaction['balance_after']
        
        # Emojis selon le type de transaction
        type_emojis = {
            'daily': f'{Emojis.DAILY}',
            'give_sent': '💸',
            'give_received': '💰',
            'buy': f'{Emojis.SHOP}',
            'roulette_win': '🎰💚',
            'roulette_loss': '🎰💔',
            'ppc_win': '🎮💚',
            'ppc_loss': '🎮💔',
            'steal_success': '🎯💚',
            'steal_fail': '🎯💔',
            'admin_add': '👑💰',
            'message_reward': '💬',
            'tax_collected': '🏛️',
            'casino_profit': '🏦💰',
            'bank_deposit': '🏦📤',
            'bank_withdraw': '🏦📥',
            'public_bank_withdraw': '🏛️💰'
        }
        
        emoji = type_emojis.get(trans_type, '💰')
        
        # Format du montant (+ ou -)
        if amount > 0:
            amount_str = f"+{amount:,}"
            color = "🟢"
        else:
            amount_str = f"{amount:,}"
            color = "🔴"
        
        # Format de la date
        date_str = timestamp.strftime("%d/%m %H:%M")
        
        # Description courte
        desc_short = description[:30] + "..." if description and len(description) > 30 else description or ""
        
        return f"{emoji} {color} **{amount_str}** PB • {date_str}\n└ *{desc_short}* → Solde: {balance_after:,}"

    @commands.command(name='transactions', aliases=['logs', 'historique', 'history'])
    async def transactions_cmd(self, ctx, page: int = 1):
        """Affiche ton historique de transactions"""
        await self._execute_transactions(ctx, ctx.author, page)

    @app_commands.command(name="transactions", description="Affiche ton historique de transactions")
    @app_commands.describe(page="Numéro de la page à afficher (optionnel)")
    async def transactions_slash(self, interaction: discord.Interaction, page: int = 1):
        """Slash command pour voir les transactions"""
        await interaction.response.defer()
        await self._execute_transactions(interaction, interaction.user, page, is_slash=True)

    async def _execute_transactions(self, ctx_or_interaction, user, page=1, is_slash=False):
        """Logique commune pour afficher les transactions"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
        else:
            send_func = ctx_or_interaction.send
        
        # Pagination
        per_page = 10
        offset = (page - 1) * per_page
        
        try:
            # Récupérer les transactions et le count total
            transactions = await self.get_user_transactions(user.id, per_page, offset)
            total_count = await self.get_transaction_count(user.id)
            total_pages = math.ceil(total_count / per_page) if total_count > 0 else 1
            
            if not transactions:
                if page == 1:
                    embed = discord.Embed(
                        title="📊 Historique des transactions",
                        description=f"**{user.display_name}** n'a encore aucune transaction enregistrée.",
                        color=Colors.WARNING
                    )
                    embed.add_field(
                        name="💡 Comment avoir des transactions ?",
                        value=f"• Utilise `{PREFIX}daily` pour tes pièces quotidiennes\n"
                              f"• Fais des transferts avec `{PREFIX}give`\n"
                              f"• Joue au casino avec `/roulette` ou `/ppc`\n"
                              f"• Achète des items avec `{PREFIX}shop`",
                        inline=False
                    )
                else:
                    embed = create_error_embed("Page vide", f"La page {page} est vide. Utilise une page entre 1 et {total_pages}.")
                    
                await send_func(embed=embed)
                return
            
            # Créer l'embed avec les transactions
            embed = discord.Embed(
                title="📊 Historique des transactions",
                description=f"**{user.display_name}** • {total_count} transaction(s) au total",
                color=Colors.INFO
            )
            
            # Ajouter chaque transaction
            transaction_list = []
            for transaction in transactions:
                formatted = self.format_transaction(transaction)
                transaction_list.append(formatted)
            
            embed.add_field(
                name=f"📋 Dernières transactions (Page {page}/{total_pages})",
                value="\n\n".join(transaction_list),
                inline=False
            )
            
            # Résumé financier
            current_balance = await self.db.get_balance(user.id)
            embed.add_field(
                name="💳 Résumé",
                value=f"**Solde actuel :** {current_balance:,} PrissBucks\n"
                      f"**Transactions :** {total_count} enregistrée(s)",
                inline=True
            )
            
            # Navigation si nécessaire
            if total_pages > 1:
                nav_text = ""
                if page > 1:
                    nav_text += f"`{PREFIX}transactions {page-1}` ← "
                nav_text += f"Page {page}/{total_pages}"
                if page < total_pages:
                    nav_text += f" → `{PREFIX}transactions {page+1}`"
                
                embed.add_field(
                    name="📄 Navigation",
                    value=nav_text,
                    inline=True
                )
            
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text=f"Utilise '{PREFIX}transactions [page]' pour naviguer")
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur transactions {user.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération de l'historique.")
            await send_func(embed=embed)

    # ==================== MÉTHODES D'INTÉGRATION ====================
    
    async def log_daily_reward(self, user_id: int, amount: int, balance_before: int, balance_after: int):
        """Log une récompense daily"""
        await self.log_transaction(
            user_id=user_id,
            transaction_type='daily',
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            description=f"Daily reward +{amount} PrissBucks"
        )

    async def log_give_transaction(self, giver_id: int, receiver_id: int, amount: int, 
                                  giver_balance_before: int, giver_balance_after: int,
                                  receiver_balance_before: int, receiver_balance_after: int, tax: int = 0):
        """Log une transaction give avec taxe"""
        try:
            # Log pour le donneur
            giver = self.bot.get_user(giver_id)
            receiver = self.bot.get_user(receiver_id)
            giver_name = giver.display_name if giver else f"User#{giver_id}"
            receiver_name = receiver.display_name if receiver else f"User#{receiver_id}"
            
            await self.log_transaction(
                user_id=giver_id,
                transaction_type='give_sent',
                amount=-amount,
                balance_before=giver_balance_before,
                balance_after=giver_balance_after,
                description=f"Transfert vers {receiver_name}" + (f" (taxe: {tax})" if tax > 0 else ""),
                related_user_id=receiver_id
            )
            
            # Log pour le receveur
            net_received = amount - tax
            await self.log_transaction(
                user_id=receiver_id,
                transaction_type='give_received',
                amount=net_received,
                balance_before=receiver_balance_before,
                balance_after=receiver_balance_after,
                description=f"Reçu de {giver_name}" + (f" (après taxe)" if tax > 0 else ""),
                related_user_id=giver_id
            )
        except Exception as e:
            logger.error(f"Erreur log give transaction: {e}")

    # ==================== MÉTHODES BANCAIRES ====================
    
    async def log_bank_deposit(self, user_id: int, amount: int, 
                              main_balance_before: int, main_balance_after: int,
                              bank_balance_before: int, bank_balance_after: int):
        """Log un dépôt bancaire"""
        await self.log_transaction(
            user_id=user_id,
            transaction_type='bank_deposit',
            amount=-amount,  # Négatif car c'est retiré du solde principal
            balance_before=main_balance_before,
            balance_after=main_balance_after,
            description=f"Dépôt en banque +{amount} PB (banque: {bank_balance_before:,} → {bank_balance_after:,})"
        )

    async def log_bank_withdraw(self, user_id: int, amount: int,
                               main_balance_before: int, main_balance_after: int,
                               bank_balance_before: int, bank_balance_after: int):
        """Log un retrait bancaire"""
        await self.log_transaction(
            user_id=user_id,
            transaction_type='bank_withdraw',
            amount=amount,  # Positif car c'est ajouté au solde principal
            balance_before=main_balance_before,
            balance_after=main_balance_after,
            description=f"Retrait de banque +{amount} PB (banque: {bank_balance_before:,} → {bank_balance_after:,})"
        )

    async def log_purchase(self, user_id: int, item_name: str, price: int, tax: int, 
                          balance_before: int, balance_after: int):
        """Log un achat avec taxe"""
        await self.log_transaction(
            user_id=user_id,
            transaction_type='buy',
            amount=-(price + tax),
            balance_before=balance_before,
            balance_after=balance_after,
            description=f"Achat: {item_name} (base: {price}, taxe: {tax})"
        )

    async def log_roulette_result(self, user_id: int, bet: int, winnings: int, 
                                 balance_before: int, balance_after: int, number: int):
        """Log un résultat de roulette"""
        if winnings > 0:
            net_profit = winnings - bet
            await self.log_transaction(
                user_id=user_id,
                transaction_type='roulette_win',
                amount=net_profit,
                balance_before=balance_before,
                balance_after=balance_after,
                description=f"Roulette WIN (#{number}) - Mise: {bet}, Gains: {winnings}"
            )
        else:
            await self.log_transaction(
                user_id=user_id,
                transaction_type='roulette_loss',
                amount=-bet,
                balance_before=balance_before,
                balance_after=balance_after,
                description=f"Roulette LOSS (#{number}) - Mise perdue: {bet}"
            )

    async def log_ppc_result(self, user_id: int, bet: int, result: str, winnings: int,
                            balance_before: int, balance_after: int, opponent_name: str = None):
        """Log un résultat de PPC"""
        if result == 'win':
            await self.log_transaction(
                user_id=user_id,
                transaction_type='ppc_win',
                amount=winnings,
                balance_before=balance_before,
                balance_after=balance_after,
                description=f"PPC WIN vs {opponent_name or 'joueur'} - Pot: {winnings}"
            )
        elif result == 'loss':
            await self.log_transaction(
                user_id=user_id,
                transaction_type='ppc_loss',
                amount=-bet,
                balance_before=balance_before,
                balance_after=balance_after,
                description=f"PPC LOSS vs {opponent_name or 'joueur'} - Mise: {bet}"
            )
        else:  # tie
            await self.log_transaction(
                user_id=user_id,
                transaction_type='ppc_loss',
                amount=-bet,
                balance_before=balance_before,
                balance_after=balance_after,
                description=f"PPC TIE vs {opponent_name or 'joueur'} - Mise vers banque publique: {bet}"
            )

    async def log_admin_add(self, user_id: int, amount: int, balance_before: int, balance_after: int, admin_name: str):
        """Log un ajout admin"""
        await self.log_transaction(
            user_id=user_id,
            transaction_type='admin_add',
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            description=f"Ajout administrateur par {admin_name}"
        )

    async def log_message_reward(self, user_id: int, amount: int, balance_before: int, balance_after: int):
        """Log une récompense de message"""
        await self.log_transaction(
            user_id=user_id,
            transaction_type='message_reward',
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            description=f"Récompense message +{amount} PrissBuck"
        )

    # Commande pour l'owner pour voir ses profits
    @commands.command(name='casino_profits')
    @commands.is_owner()
    async def casino_profits_cmd(self, ctx, days: int = 7):
        """[OWNER] Affiche tes profits du casino des X derniers jours"""
        try:
            from config import OWNER_ID
            if not OWNER_ID or ctx.author.id != OWNER_ID:
                return
                
            # Récupérer les transactions de type casino/tax des derniers jours
            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT transaction_type, amount, description, timestamp
                    FROM transaction_logs 
                    WHERE user_id = $1 
                    AND transaction_type IN ('casino_profit', 'tax_collected', 'roulette_loss', 'ppc_loss')
                    AND timestamp > NOW() - INTERVAL '%s days'
                    ORDER BY timestamp DESC
                    LIMIT 50
                ''', OWNER_ID, days)
            
            if not rows:
                embed = create_info_embed("Pas de profits", f"Aucun profit casino dans les {days} derniers jours.")
                await ctx.send(embed=embed)
                return
            
            total_profit = sum(row['amount'] for row in rows)
            
            embed = discord.Embed(
                title="🏦 Profits Casino",
                description=f"**Profits des {days} derniers jours**\n"
                           f"💰 **Total:** {total_profit:,} PrissBucks",
                color=Colors.GOLD
            )
            
            # Détail par type
            types_profit = {}
            for row in rows:
                trans_type = row['transaction_type']
                if trans_type not in types_profit:
                    types_profit[trans_type] = 0
                types_profit[trans_type] += row['amount']
            
            type_names = {
                'casino_profit': '🎰 Casino (général)',
                'tax_collected': '🏛️ Taxes collectées',
                'roulette_loss': '🎰 Roulette (pertes)',
                'ppc_loss': '🎮 PPC (égalités/abandons)'
            }
            
            for trans_type, amount in types_profit.items():
                name = type_names.get(trans_type, trans_type)
                embed.add_field(name=name, value=f"{amount:,} PB", inline=True)
            
            embed.set_footer(text=f"Dernière mise à jour • {len(rows)} transactions")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur casino profits: {e}")
            await ctx.send("Erreur lors de la récupération des profits.")

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(TransactionLogs(bot))