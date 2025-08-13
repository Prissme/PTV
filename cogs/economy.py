import discord
from discord.ext import commands
from datetime import datetime, timezone
import random
import logging

from config import (
    DAILY_MIN, DAILY_MAX, DAILY_BONUS_CHANCE, DAILY_BONUS_MIN, DAILY_BONUS_MAX,
    DAILY_COOLDOWN, TRANSFER_COOLDOWN, Colors, Emojis
)
from utils.embeds import (
    create_balance_embed, create_daily_embed, create_transfer_embed,
    create_error_embed, create_cooldown_embed
)

logger = logging.getLogger(__name__)

class Economy(commands.Cog):
    """Commandes économie essentielles : balance, daily, give"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Economy initialisé (simplifié)")
    
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