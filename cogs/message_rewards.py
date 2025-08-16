import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import asyncio
import logging

from config import Colors, Emojis

logger = logging.getLogger(__name__)

class MessageRewards(commands.Cog):
    """Système de récompenses automatiques pour les messages avec logs intégrés et optimisations"""
    
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
            'total_rewards_given': 0,
            'session_start': datetime.now(timezone.utc)
        }
        
        # Compteurs pour optimisation
        self.cleanup_counter = 0
        self.CLEANUP_INTERVAL = 1000  # Nettoyer tous les 1000 messages traités
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        # Démarrer la tâche de nettoyage périodique
        self.cleanup_task.start()
        logger.info(f"✅ Cog MessageRewards initialisé (1 msg = {self.REWARD_AMOUNT} PrissBuck, CD: {self.COOLDOWN_SECONDS}s) avec logs intégrés et optimisé")

    async def cog_unload(self):
        """Appelé quand le cog est déchargé"""
        if self.cleanup_task.is_running():
            self.cleanup_task.cancel()
        logger.info("MessageRewards: Tâche de nettoyage arrêtée")

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
        """Événement déclenché à chaque message avec logs intégrés et optimisations"""
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
            
            # Nettoyage périodique optimisé
            self.cleanup_counter += 1
            if self.cleanup_counter >= self.CLEANUP_INTERVAL:
                self.cleanup_counter = 0
                # Nettoyer en arrière-plan sans bloquer
                asyncio.create_task(self.cleanup_old_cooldowns())
            
            # Log pour debug (optionnel, peut être supprimé en production)
            logger.debug(f"💰 {message.author} a reçu {self.REWARD_AMOUNT} PrissBuck pour un message [LOGGED]")
            
        except Exception as e:
            logger.error(f"Erreur récompense message {user_id}: {e}")

    async def cleanup_old_cooldowns(self):
        """Supprime les anciens cooldowns pour économiser la mémoire (optimisé)"""
        if not self.cooldowns:
            return
            
        now = datetime.now(timezone.utc)
        expired_users = []
        cutoff_time = now - timedelta(minutes=10)  # Supprimer après 10 minutes d'inactivité
        
        # Identifier les cooldowns expirés
        for user_id, last_reward in self.cooldowns.items():
            if last_reward < cutoff_time:
                expired_users.append(user_id)
        
        # Supprimer les cooldowns expirés
        for user_id in expired_users:
            del self.cooldowns[user_id]
            
        if expired_users:
            logger.debug(f"Nettoyage: {len(expired_users)} cooldowns expirés supprimés")
            
        # Statistiques de mémoire
        logger.debug(f"MessageRewards: {len(self.cooldowns)} cooldowns actifs en mémoire")

    @tasks.loop(minutes=30)  # Nettoyage automatique toutes les 30 minutes
    async def cleanup_task(self):
        """Tâche de nettoyage automatique périodique"""
        try:
            await self.cleanup_old_cooldowns()
            
            # Optionnel: Log des statistiques périodiques
            uptime = datetime.now(timezone.utc) - self.stats['session_start']
            logger.debug(
                f"MessageRewards Stats: {self.stats['total_messages_rewarded']} messages récompensés, "
                f"{self.stats['total_rewards_given']} PB distribués, "
                f"uptime: {uptime.total_seconds()/3600:.1f}h"
            )
            
        except Exception as e:
            logger.error(f"Erreur tâche de nettoyage MessageRewards: {e}")

    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        """Attendre que le bot soit prêt avant de démarrer la tâche"""
        await self.bot.wait_until_ready()

    # ==================== COMMANDES DE STATISTIQUES AMÉLIORÉES ====================

    @commands.command(name='msgstats', aliases=['message_stats', 'rewardstats'])
    async def message_stats_cmd(self, ctx):
        """Affiche les statistiques des récompenses de messages avec détails optimisés"""
        try:
            uptime = datetime.now(timezone.utc) - self.stats['session_start']
            
            embed = discord.Embed(
                title="📊 Statistiques des récompenses de messages",
                color=Colors.INFO
            )
            
            # Statistiques de session
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
            
            # Statistiques de performance
            embed.add_field(
                name="📈 Taux de récompense",
                value=f"**{self.stats['total_rewards_given'] / max(1, uptime.total_seconds()) * 3600:.1f}** PB/heure",
                inline=True
            )
            
            embed.add_field(
                name="🔄 Cooldowns actifs",
                value=f"**{len(self.cooldowns)}** utilisateurs",
                inline=True
            )
            
            embed.add_field(
                name="⏰ Uptime session",
                value=f"**{uptime.total_seconds()/3600:.1f}** heures",
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
            
            # Informations système
            embed.add_field(
                name="⚡ Optimisations actives",
                value=f"• Nettoyage auto: 30min\n• Nettoyage smart: {self.CLEANUP_INTERVAL} msgs\n• Cooldowns mémoire: efficace",
                inline=False
            )
            
            embed.add_field(
                name="📈 Comment ça marche",
                value=f"• Écris un message de 3+ caractères\n"
                      f"• Attends {self.COOLDOWN_SECONDS}s entre chaque récompense\n"
                      f"• Pas de récompense pour les commandes\n"
                      f"• Fonctionne uniquement dans les serveurs",
                inline=False
            )
            
            embed.set_footer(text="Toutes les récompenses sont automatiquement enregistrées ! Système optimisé actif.")
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
        
        # Statistiques personnelles
        if user_id in self.cooldowns:
            last_reward = self.cooldowns[user_id]
            embed.add_field(
                name="🕐 Dernière récompense",
                value=f"<t:{int(last_reward.timestamp())}:R>",
                inline=True
            )
        
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"Cooldown: {self.COOLDOWN_SECONDS}s • Récompense: {self.REWARD_AMOUNT} PB • Historique dans /transactions")
        await ctx.send(embed=embed)

    @commands.command(name='msgtop', aliases=['message_top'])
    @commands.cooldown(1, 60, commands.BucketType.guild)  # Limite: 1 fois par minute par serveur
    async def message_top_cmd(self, ctx):
        """Affiche un classement des utilisateurs les plus actifs (basé sur les cooldowns récents)"""
        try:
            if not self.cooldowns:
                embed = discord.Embed(
                    title="📊 Top Activité Messages",
                    description="Aucune activité récente détectée.",
                    color=Colors.WARNING
                )
                await ctx.send(embed=embed)
                return
            
            # Trier par activité récente (cooldowns les plus récents)
            now = datetime.now(timezone.utc)
            recent_activity = []
            
            for user_id, last_reward in self.cooldowns.items():
                # Ne considérer que l'activité des 24 dernières heures
                if (now - last_reward).total_seconds() <= 86400:  # 24h
                    try:
                        user = self.bot.get_user(user_id)
                        if user and user in ctx.guild.members:  # Seulement les membres du serveur
                            recent_activity.append((user, last_reward))
                    except:
                        continue
            
            if not recent_activity:
                embed = discord.Embed(
                    title="📊 Top Activité Messages",
                    description="Aucune activité récente dans ce serveur.",
                    color=Colors.WARNING
                )
                await ctx.send(embed=embed)
                return
            
            # Trier par activité la plus récente
            recent_activity.sort(key=lambda x: x[1], reverse=True)
            
            embed = discord.Embed(
                title="📊 Top Activité Messages (24h)",
                description="Utilisateurs les plus actifs récemment",
                color=Colors.INFO
            )
            
            top_text = ""
            for i, (user, last_activity) in enumerate(recent_activity[:10], 1):
                emoji = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                time_ago = now - last_activity
                if time_ago.total_seconds() < 3600:  # Moins d'1h
                    time_str = f"{int(time_ago.total_seconds()/60)}min"
                else:
                    time_str = f"{int(time_ago.total_seconds()/3600)}h"
                
                top_text += f"{emoji[i-1]} **{user.display_name}** - il y a {time_str}\n"
            
            embed.add_field(
                name="⚡ Activité récente",
                value=top_text,
                inline=False
            )
            
            embed.add_field(
                name="ℹ️ Note",
                value="Ce classement se base sur l'activité récente des récompenses de messages.",
                inline=False
            )
            
            embed.set_footer(text=f"Total: {len(recent_activity)} utilisateurs actifs sur 24h")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur msgtop: {e}")
            embed = discord.Embed(
                title="❌ Erreur",
                description="Impossible d'afficher le classement d'activité.",
                color=Colors.ERROR
            )
            await ctx.send(embed=embed)

    @msgtop_cmd.error
    async def msgtop_error(self, ctx, error):
        """Gestion d'erreur pour msgtop"""
        if isinstance(error, commands.CommandOnCooldown):
            embed = discord.Embed(
                title="⏰ Cooldown",
                description=f"Cette commande peut être utilisée dans **{error.retry_after:.0f}** secondes.",
                color=Colors.WARNING
            )
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(MessageRewards(bot))