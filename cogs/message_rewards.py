import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import asyncio
import logging

from config import Colors, Emojis

logger = logging.getLogger(__name__)

class MessageRewards(commands.Cog):
    """Système de récompenses automatiques pour les messages avec logs intégrés"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Dictionnaire pour stocker les cooldowns en mémoire
        # Format: {user_id: datetime_last_reward}
        self.cooldowns = {}
        
        # Configuration
        self.REWARD_AMOUNT = 1  # 1 PrissBuck par message
        self.COOLDOWN_SECONDS = 20  # Cooldown de 20 secondes
        
        # Statistiques en mémoire (optionnel)
        self.stats = {
            'total_messages_rewarded': 0,
            'total_rewards_given': 0
        }
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info(f"✅ Cog MessageRewards initialisé (1 msg = {self.REWARD_AMOUNT} PrissBuck, CD: {self.COOLDOWN_SECONDS}s) avec logs intégrés")

    def is_on_cooldown(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur est en cooldown"""
        if user_id not in self.cooldowns:
            return False
            
        now = datetime.now(timezone.utc)
        last_reward = self.cooldowns[user_id]
        cooldown_end = last_reward + timedelta(seconds=self.COOLDOWN_SECONDS)
        
        return now < cooldown_end

    def get_cooldown_remaining(self, user_id: int) -> int:
        """Retourne le temps de cooldown restant en secondes"""
        if user_id not in self.cooldowns:
            return 0
            
        now = datetime.now(timezone.utc)
        last_reward = self.cooldowns[user_id]
        cooldown_end = last_reward + timedelta(seconds=self.COOLDOWN_SECONDS)
        
        if now >= cooldown_end:
            return 0
            
        return int((cooldown_end - now).total_seconds())

    def set_cooldown(self, user_id: int):
        """Met l'utilisateur en cooldown"""
        self.cooldowns[user_id] = datetime.now(timezone.utc)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Événement déclenché à chaque message avec logs intégrés"""
        # Ignorer les bots
        if message.author.bot:
            return
            
        # Ignorer les commandes (messages qui commencent par le préfixe)
        if message.content.startswith(self.bot.command_prefix):
            return
            
        # Ignorer les messages vides ou trop courts (anti-spam)
        if not message.content or len(message.content.strip()) < 3:
            return
            
        # Ignorer les messages en DM
        if not message.guild:
            return
            
        user_id = message.author.id
        
        # Vérifier le cooldown
        if self.is_on_cooldown(user_id):
            return  # Pas de message d'erreur pour ne pas spammer
            
        try:
            # Récupérer le solde AVANT la récompense pour les logs
            balance_before = await self.db.get_balance(user_id)
            
            # Donner la récompense
            await self.db.update_balance(user_id, self.REWARD_AMOUNT)
            
            # Calculer le nouveau solde et logger la transaction
            balance_after = balance_before + self.REWARD_AMOUNT
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_message_reward(
                    user_id=user_id,
                    amount=self.REWARD_AMOUNT,
                    balance_before=balance_before,
                    balance_after=balance_after
                )
            
            # Mettre en cooldown
            self.set_cooldown(user_id)
            
            # Mettre à jour les statistiques
            self.stats['total_messages_rewarded'] += 1
            self.stats['total_rewards_given'] += self.REWARD_AMOUNT
            
            # Log pour debug (optionnel, peut être supprimé en production)
            logger.debug(f"💰 {message.author} a reçu {self.REWARD_AMOUNT} PrissBuck pour un message [LOGGED]")
            
        except Exception as e:
            logger.error(f"Erreur récompense message {user_id}: {e}")

    # Nettoyage automatique des anciens cooldowns (optionnel, pour optimiser la mémoire)
    async def cleanup_old_cooldowns(self):
        """Supprime les anciens cooldowns pour économiser la mémoire"""
        if not self.cooldowns:
            return
            
        now = datetime.now(timezone.utc)
        expired_users = []
        
        for user_id, last_reward in self.cooldowns.items():
            if now - last_reward > timedelta(minutes=5):  # Supprimer après 5 minutes d'inactivité
                expired_users.append(user_id)
        
        for user_id in expired_users:
            del self.cooldowns[user_id]
            
        if expired_users:
            logger.debug(f"Nettoyage: {len(expired_users)} cooldowns expirés supprimés")

    @commands.Cog.listener()
    async def on_ready(self):
        """Démarre le nettoyage automatique des cooldowns"""
        await self.start_cleanup_task()

    async def start_cleanup_task(self):
        """Tâche de nettoyage automatique des cooldowns (tous les 10 minutes)"""
        while not self.bot.is_closed():
            try:
                await asyncio.sleep(600)  # 10 minutes
                await self.cleanup_old_cooldowns()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur nettoyage cooldowns: {e}")

    # ==================== COMMANDES DE STATISTIQUES ====================

    @commands.command(name='msgstats', aliases=['message_stats', 'rewardstats'])
    async def message_stats_cmd(self, ctx):
        """Affiche les statistiques des récompenses de messages"""
        try:
            embed = discord.Embed(
                title="📊 Statistiques des récompenses de messages",
                color=Colors.INFO
            )
            
            embed.add_field(
                name="💬 Messages récompensés",
                value=f"**{self.stats['total_messages_rewarded']:,}** messages",
                inline=True
            )
            
            embed.add_field(
                name="💰 Total distribué",
                value=f"**{self.stats['total_rewards_given']:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="⏱️ Cooldown actuel",
                value=f"**{self.COOLDOWN_SECONDS}** secondes",
                inline=True
            )
            
            embed.add_field(
                name="💎 Récompense par message",
                value=f"**{self.REWARD_AMOUNT}** PrissBuck",
                inline=True
            )
            
            # Statut personnel
            user_remaining = self.get_cooldown_remaining(ctx.author.id)
            if user_remaining > 0:
                status = f"**{user_remaining}s** restantes"
                color_status = "🔴"
            else:
                status = "**Disponible**"
                color_status = "🟢"
                
            embed.add_field(
                name=f"{color_status} Ton statut",
                value=status,
                inline=True
            )
            
            embed.add_field(
                name="📈 Comment ça marche",
                value=f"• Écris un message de 3+ caractères\n"
                      f"• Attends {self.COOLDOWN_SECONDS}s entre chaque récompense\n"
                      f"• Pas de récompense pour les commandes\n"
                      f"• Fonctionne uniquement dans les serveurs",
                inline=False
            )
            
            embed.set_footer(text="Toutes les récompenses sont automatiquement enregistrées !")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur msgstats: {e}")
            embed = discord.Embed(
                title="❌ Erreur",
                description="Impossible d'afficher les statistiques des messages.",
                color=Colors.ERROR
            )
            await ctx.send(embed=embed)

    @commands.command(name='msgcd', aliases=['message_cooldown', 'rewardcd'])
    async def message_cooldown_cmd(self, ctx):
        """Vérifie le cooldown personnel pour les récompenses de messages"""
        user_id = ctx.author.id
        remaining = self.get_cooldown_remaining(user_id)
        
        if remaining <= 0:
            embed = discord.Embed(
                title="✅ Récompenses de messages disponibles !",
                description=f"Tu peux gagner **{self.REWARD_AMOUNT} PrissBuck** en écrivant un message !",
                color=Colors.SUCCESS
            )
            embed.add_field(
                name="💡 Astuce",
                value="Écris un message de 3+ caractères pour recevoir ta récompense !",
                inline=False
            )
        else:
            embed = discord.Embed(
                title="⏰ Cooldown des récompenses de messages",
                description=f"Tu pourras gagner des PrissBucks dans **{remaining}** secondes.",
                color=Colors.WARNING
            )
            embed.add_field(
                name="📊 Récompense suivante",
                value=f"**{self.REWARD_AMOUNT} PrissBuck** pour ton prochain message",
                inline=True
            )
        
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"Cooldown: {self.COOLDOWN_SECONDS}s • Récompense: {self.REWARD_AMOUNT} PB • Historique dans /transactions")
        await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(MessageRewards(bot))