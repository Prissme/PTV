import discord
from discord.ext import commands
from datetime import datetime, timezone
import random
import logging

from config import (
    DAILY_MIN, DAILY_MAX, DAILY_BONUS_CHANCE, DAILY_BONUS_MIN, DAILY_BONUS_MAX,
    DAILY_COOLDOWN, TRANSFER_COOLDOWN, DEFAULT_LEADERBOARD_LIMIT, MAX_LEADERBOARD_LIMIT,
    Colors, Emojis
)
from utils.embeds import (
    create_balance_embed, create_daily_embed, create_transfer_embed,
    create_leaderboard_embed, create_error_embed, create_cooldown_embed
)

logger = logging.getLogger(__name__)

class Economy(commands.Cog):
    """Commandes économie de base : balance, daily, give, leaderboard"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None  # Sera initialisé dans cog_load
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Economy initialisé")
    
    @commands.command(name='balance', aliases=['bal', 'money'])
    async def balance_cmd(self, ctx, member: discord.Member = None):
        """Affiche le solde d'un utilisateur"""
        target = member or ctx.author
        
        try:
            balance = await self.db.get_balance(target.id)
            embed = create_balance_embed(target, balance)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur balance pour {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération du solde.")
            await ctx.send(embed=embed)

    @commands.command(name='give', aliases=['pay', 'transfer'])
    @commands.cooldown(1, TRANSFER_COOLDOWN, commands.BucketType.user)
    async def give_cmd(self, ctx, member: discord.Member, amount: int):
        """Donne des pièces à un autre utilisateur"""
        giver = ctx.author
        receiver = member
        
        # Validations
        if amount <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif !")
            await ctx.send(embed=embed)
            return
            
        if giver.id == receiver.id:
            embed = create_error_embed("Transfert impossible", "Tu ne peux pas te donner des pièces à toi-même !")
            await ctx.send(embed=embed)
            return
            
        if receiver.bot:
            embed = create_error_embed("Transfert impossible", "Tu ne peux pas donner des pièces à un bot !")
            await ctx.send(embed=embed)
            return

        try:
            # Vérifier le solde du donneur
            giver_balance = await self.db.get_balance(giver.id)
            if giver_balance < amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Tu as {giver_balance:,} PrissBucks mais tu essaies de donner {amount:,} PrissBucks."
                )
                await ctx.send(embed=embed)
                return

            # Effectuer le transfert
            success = await self.db.transfer(giver.id, receiver.id, amount)
            
            if success:
                new_balance = giver_balance - amount
                embed = create_transfer_embed(giver, receiver, amount, new_balance)
                await ctx.send(embed=embed)
            else:
                embed = create_error_embed("Échec du transfert", "Solde insuffisant.")
                await ctx.send(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur give {giver.id} -> {receiver.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du transfert.")
            await ctx.send(embed=embed)

    @commands.command(name='daily', aliases=['dailyspin', 'spin'])
    @commands.cooldown(1, DAILY_COOLDOWN, commands.BucketType.user)
    async def daily_cmd(self, ctx):
        """Récupère tes pièces quotidiennes"""
        user_id = ctx.author.id
        now = datetime.now(timezone.utc)

        try:
            # Vérifier le dernier daily
            last_daily = await self.db.get_last_daily(user_id)
            
            if last_daily:
                delta = now - last_daily
                if delta.total_seconds() < DAILY_COOLDOWN:
                    remaining = DAILY_COOLDOWN - delta.total_seconds()
                    embed = create_cooldown_embed("daily", remaining)
                    await ctx.send(embed=embed)
                    return

            # Calculer la récompense
            base_reward = random.randint(DAILY_MIN, DAILY_MAX)
            bonus = 0
            
            # Chance de bonus
            if random.randint(1, 100) <= DAILY_BONUS_CHANCE:
                bonus = random.randint(DAILY_BONUS_MIN, DAILY_BONUS_MAX)
            
            total_reward = base_reward + bonus

            # Mettre à jour la base de données
            await self.db.update_balance(user_id, total_reward)
            await self.db.set_last_daily(user_id, now)

            # Envoyer l'embed
            embed = create_daily_embed(ctx.author, total_reward, bonus)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur daily pour {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du daily spin.")
            await ctx.send(embed=embed)

    @commands.command(name='leaderboard', aliases=['top', 'rich', 'lb'])
    async def leaderboard_cmd(self, ctx, limit: int = DEFAULT_LEADERBOARD_LIMIT):
        """Affiche le classement des plus riches"""
        # Limiter la valeur
        if limit > MAX_LEADERBOARD_LIMIT:
            limit = MAX_LEADERBOARD_LIMIT
        elif limit < 1:
            limit = DEFAULT_LEADERBOARD_LIMIT

        try:
            top_users = await self.db.get_top_users(limit)
            
            if not top_users:
                embed = create_error_embed("Classement vide", "Aucun utilisateur trouvé dans le classement.")
                await ctx.send(embed=embed)
                return

            embed = create_leaderboard_embed(top_users, self.bot, limit)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur leaderboard: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage du classement.")
            await ctx.send(embed=embed)

    @commands.command(name='profile', aliases=['profil', 'me'])
    async def profile_cmd(self, ctx, member: discord.Member = None):
        """Affiche le profil économique d'un utilisateur"""
        target = member or ctx.author
        
        try:
            # Récupérer les données
            balance = await self.db.get_balance(target.id)
            last_daily = await self.db.get_last_daily(target.id)
            purchases = await self.db.get_user_purchases(target.id)
            
            # Calculer le total dépensé
            total_spent = sum(purchase['price_paid'] for purchase in purchases)
            
            # Créer l'embed
            embed = discord.Embed(
                title=f"👤 Profil de {target.display_name}",
                color=Colors.INFO
            )
            
            embed.add_field(
                name=f"{Emojis.MONEY} Solde actuel",
                value=f"**{balance:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="🛒 Items possédés",
                value=f"**{len(purchases)}** item(s)",
                inline=True
            )
            
            embed.add_field(
                name="💸 Total dépensé",
                value=f"**{total_spent:,}** PrissBucks",
                inline=True
            )
            
            # Dernier daily
            if last_daily:
                daily_text = f"<t:{int(last_daily.timestamp())}:R>"
            else:
                daily_text = "Jamais utilisé"
                
            embed.add_field(
                name=f"{Emojis.DAILY} Dernier daily",
                value=daily_text,
                inline=True
            )
            
            # Richesse relative
            try:
                top_users = await self.db.get_top_users(1000)  # Top 1000 pour calculer le rang
                user_rank = None
                for i, (user_id, _) in enumerate(top_users, 1):
                    if user_id == target.id:
                        user_rank = i
                        break
                
                if user_rank:
                    embed.add_field(
                        name=f"{Emojis.LEADERBOARD} Classement",
                        value=f"**#{user_rank}** sur le serveur",
                        inline=True
                    )
            except:
                pass  # Ignorer les erreurs de classement
            
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.set_footer(text="Utilise !daily pour gagner des pièces quotidiennes !")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur profile pour {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage du profil.")
            await ctx.send(embed=embed)

    # Gestion d'erreur spécifique pour ce cog
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """Gestion d'erreurs spécifique au cog Economy"""
        if isinstance(error, commands.CommandOnCooldown):
            if ctx.command.name in ['daily', 'dailyspin', 'spin']:
                embed = create_cooldown_embed("daily", error.retry_after)
                await ctx.send(embed=embed)
            elif ctx.command.name in ['give', 'pay', 'transfer']:
                embed = create_cooldown_embed("give", error.retry_after)
                await ctx.send(embed=embed)
            else:
                # Laisser la gestion globale s'en occuper
                raise error

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Economy(bot))
