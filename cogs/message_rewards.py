import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import asyncio
import logging

from config import Colors, Emojis

logger = logging.getLogger(__name__)

class MessageRewards(commands.Cog):
    """Système de récompenses automatiques pour les messages avec logs intégrés, optimisations et multiplicateur TOP 1"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Dictionnaire pour stocker les cooldowns en mémoire
        # Format: {user_id: datetime_last_reward}
        self.cooldowns = {}
        
        # Configuration
        self.REWARD_AMOUNT = 1  # 1 PrissBuck par message
        self.COOLDOWN_SECONDS = 20  # Cooldown de 20 secondes
        self.TOP1_MULTIPLIER = 10  # Multiplicateur x10 pour le top 1
        
        # Cache pour le top 1 (mise à jour périodique)
        self.current_top1_user_id = None
        self.last_top1_check = None
        self.TOP1_CHECK_INTERVAL = 300  # 5 minutes
        
        # Statistiques en mémoire (optionnel)
        self.stats = {
            'total_messages_rewarded': 0,
            'total_rewards_given': 0,
            'top1_bonuses_given': 0,
            'session_start': datetime.now(timezone.utc)
        }
        
        # Compteurs pour optimisation
        self.cleanup_counter = 0
        self.CLEANUP_INTERVAL = 1000  # Nettoyer tous les 1000 messages traités
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        # Démarrer les tâches automatiques
        self.cleanup_task.start()
        self.update_top1_cache.start()
        logger.info(f"✅ Cog MessageRewards initialisé (1 msg = {self.REWARD_AMOUNT} PB, TOP 1 x{self.TOP1_MULTIPLIER}, CD: {self.COOLDOWN_SECONDS}s) avec logs intégrés et optimisé")

    async def cog_unload(self):
        """Appelé quand le cog est déchargé"""
        if self.cleanup_task.is_running():
            self.cleanup_task.cancel()
        if self.update_top1_cache.is_running():
            self.update_top1_cache.cancel()
        logger.info("MessageRewards: Tâches arrêtées")

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

    async def get_current_top1_user(self) -> int:
        """Récupère l'utilisateur #1 du classement avec cache"""
        now = datetime.now(timezone.utc)
        
        # Vérifier si le cache est valide
        if (self.last_top1_check and 
            self.current_top1_user_id and 
            (now - self.last_top1_check).total_seconds() < self.TOP1_CHECK_INTERVAL):
            return self.current_top1_user_id
        
        # Mettre à jour le cache
        try:
            top_users = await self.db.get_top_users(1)
            if top_users:
                self.current_top1_user_id = top_users[0][0]  # Premier utilisateur (user_id, balance)
                logger.debug(f"TOP 1 updated: User {self.current_top1_user_id}")
            else:
                self.current_top1_user_id = None
            
            self.last_top1_check = now
            return self.current_top1_user_id
        except Exception as e:
            logger.error(f"Erreur récupération TOP 1: {e}")
            return self.current_top1_user_id  # Retourner l'ancien cache

    @tasks.loop(minutes=5)  # Mise à jour du cache TOP 1 toutes les 5 minutes
    async def update_top1_cache(self):
        """Met à jour le cache du TOP 1 périodiquement"""
        try:
            await self.get_current_top1_user()
        except Exception as e:
            logger.error(f"Erreur mise à jour cache TOP 1: {e}")

    @update_top1_cache.before_loop
    async def before_update_top1_cache(self):
        """Attendre que le bot soit prêt"""
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message):
        """Événement déclenché à chaque message avec logs intégrés, optimisations et bonus TOP 1"""
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
            
            # Calculer la récompense (avec bonus TOP 1)
            base_reward = self.REWARD_AMOUNT
            is_top1 = False
            
            # Vérifier si l'utilisateur est TOP 1
            top1_user_id = await self.get_current_top1_user()
            if top1_user_id and user_id == top1_user_id:
                base_reward *= self.TOP1_MULTIPLIER
                is_top1 = True
                self.stats['top1_bonuses_given'] += 1
            
            # Donner la récompense
            await self.db.update_balance(user_id, base_reward)
            
            # Calculer le nouveau solde et logger la transaction
            balance_after = balance_before + base_reward
            if hasattr(self.bot, 'transaction_logs'):
                description = f"Récompense message +{base_reward} PrissBuck"
                if is_top1:
                    description += f" (TOP 1 x{self.TOP1_MULTIPLIER})"
                
                await self.bot.transaction_logs.log_transaction(
                    user_id=user_id,
                    transaction_type='message_reward',
                    amount=base_reward,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    description=description
                )
            
            # Mettre en cooldown
            self.set_cooldown(user_id)
            
            # Mettre à jour les statistiques
            self.stats['total_messages_rewarded'] += 1
            self.stats['total_rewards_given'] += base_reward
            
            # Notification spéciale pour TOP 1 (occasionnelle)
            if is_top1 and base_reward > self.REWARD_AMOUNT:
                # Notification 1 fois sur 20 pour éviter le spam
                import random
                if random.randint(1, 20) == 1:
                    try:
                        embed = discord.Embed(
                            title="👑 Bonus TOP 1 !",
                            description=f"🔥 **{message.author.display_name}** reçoit **{base_reward} PB** pour ce message !\n"
                                       f"✨ **Privilège du TOP 1** : x{self.TOP1_MULTIPLIER} multiplicateur !",
                            color=Colors.GOLD
                        )
                        embed.add_field(
                            name="🏆 Statut",
                            value="Tu es actuellement **#1** du classement !",
                            inline=True
                        )
                        embed.add_field(
                            name="💰 Nouveau solde",
                            value=f"**{balance_after:,}** PrissBucks",
                            inline=True
                        )
                        embed.set_thumbnail(url=message.author.display_avatar.url)
                        embed.set_footer(text=f"Reste TOP 1 pour garder ce privilège ! • x{self.TOP1_MULTIPLIER} par message")
                        
                        await message.channel.send(embed=embed, delete_after=10)
                    except:
                        pass  # Ignorer si on ne peut pas envoyer le message
            
            # Nettoyage périodique optimisé
            self.cleanup_counter += 1
            if self.cleanup_counter >= self.CLEANUP_INTERVAL:
                self.cleanup_counter = 0
                # Nettoyer en arrière-plan sans bloquer
                asyncio.create_task(self.cleanup_old_cooldowns())
            
            # Log pour debug (optionnel, peut être supprimé en production)
            if is_top1:
                logger.debug(f"👑 TOP 1: {message.author} a reçu {base_reward} PrissBucks (x{self.TOP1_MULTIPLIER}) [LOGGED]")
            else:
                logger.debug(f"💰 {message.author} a reçu {base_reward} PrissBuck pour un message [LOGGED]")
            
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
            
            # Optionnel: Log des statistiques périodiques avec TOP 1
            uptime = datetime.now(timezone.utc) - self.stats['session_start']
            logger.debug(
                f"MessageRewards Stats: {self.stats['total_messages_rewarded']} messages récompensés, "
                f"{self.stats['total_rewards_given']} PB distribués, "
                f"{self.stats['top1_bonuses_given']} bonus TOP 1, "
                f"uptime: {uptime.total_seconds()/3600:.1f}h"
            )
            
        except Exception as e:
            logger.error(f"Erreur tâche de nettoyage MessageRewards: {e}")

    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        """Attendre que le bot soit prêt avant de démarrer la tâche"""
        await self.bot.wait_until_ready()

    # ==================== COMMANDES DE STATISTIQUES AMÉLIORÉES AVEC TOP 1 ====================

    @commands.command(name='msgstats', aliases=['message_stats', 'rewardstats'])
    async def message_stats_cmd(self, ctx):
        """Affiche les statistiques des récompenses de messages avec détails TOP 1"""
        try:
            uptime = datetime.now(timezone.utc) - self.stats['session_start']
            
            embed = discord.Embed(
                title="📊 Statistiques des récompenses de messages",
                description="💬 **Système avec bonus TOP 1 actif !**",
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
            
            # Statistiques TOP 1
            embed.add_field(
                name="👑 Bonus TOP 1 donnés",
                value=f"**{self.stats['top1_bonuses_given']:,}** fois",
                inline=True
            )
            
            embed.add_field(
                name="🔥 Multiplicateur TOP 1",
                value=f"**x{self.TOP1_MULTIPLIER}** par message",
                inline=True
            )
            
            # Afficher qui est actuellement TOP 1
            current_top1 = await self.get_current_top1_user()
            if current_top1:
                try:
                    top1_user = self.bot.get_user(current_top1)
                    top1_name = top1_user.display_name if top1_user else f"User#{current_top1}"
                    top1_display = f"**{top1_name}** 👑"
                except:
                    top1_display = f"User#{current_top1} 👑"
            else:
                top1_display = "**Aucun**"
            
            embed.add_field(
                name="🏆 TOP 1 actuel",
                value=top1_display,
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
            
            # Statut personnel avec TOP 1
            user_remaining = self.get_cooldown_remaining(ctx.author.id)
            is_user_top1 = current_top1 and ctx.author.id == current_top1
            
            if user_remaining > 0:
                status = f"**{user_remaining}s** restantes"
                color_status = "🔴"
            else:
                status = "**Disponible**"
                color_status = "🟢"
            
            if is_user_top1:
                status += f" 👑 (TOP 1: **{self.TOP1_MULTIPLIER} PB**/msg)"
                color_status = "👑"
                
            embed.add_field(
                name=f"{color_status} Ton statut",
                value=status,
                inline=False
            )
            
            # Informations système avec TOP 1
            embed.add_field(
                name="⚡ Optimisations actives",
                value=f"• Nettoyage auto: 30min\n• Nettoyage smart: {self.CLEANUP_INTERVAL} msgs\n• Cache TOP 1: {self.TOP1_CHECK_INTERVAL//60}min\n• Cooldowns mémoire: efficace",
                inline=False
            )
            
            embed.add_field(
                name="📈 Comment ça marche",
                value=f"• Écris un message de 3+ caractères\n"
                      f"• Attends {self.COOLDOWN_SECONDS}s entre chaque récompense\n"
                      f"• **TOP 1 du classement** = **x{self.TOP1_MULTIPLIER}** récompenses !\n"
                      f"• Pas de récompense pour les commandes\n"
                      f"• Fonctionne uniquement dans les serveurs",
                inline=False
            )
            
            embed.add_field(
                name="👑 PRIVILÈGE TOP 1",
                value=f"🔥 **Le #1 du classement** gagne **{self.TOP1_MULTIPLIER} PB** par message au lieu de 1 !\n"
                      f"⚡ **Deviens TOP 1** pour débloquer ce privilège exclusif !\n"
                      f"📊 Cache mis à jour toutes les {self.TOP1_CHECK_INTERVAL//60} minutes",
                inline=False
            )
            
            embed.set_footer(text="Toutes les récompenses sont automatiquement enregistrées ! Système TOP 1 x10 actif !")
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
        """Vérifie le cooldown personnel pour les récompenses de messages avec info TOP 1"""
        user_id = ctx.author.id
        remaining = self.get_cooldown_remaining(user_id)
        
        # Vérifier si l'utilisateur est TOP 1
        current_top1 = await self.get_current_top1_user()
        is_top1 = current_top1 and user_id == current_top1
        reward_amount = self.REWARD_AMOUNT * self.TOP1_MULTIPLIER if is_top1 else self.REWARD_AMOUNT
        
        if remaining <= 0:
            embed = discord.Embed(
                title="✅ Récompenses de messages disponibles !",
                description=f"Tu peux gagner **{reward_amount} PrissBuck{'s' if reward_amount > 1 else ''}** en écrivant un message !",
                color=Colors.GOLD if is_top1 else Colors.SUCCESS
            )
            
            if is_top1:
                embed.add_field(
                    name="👑 PRIVILÈGE TOP 1",
                    value=f"🔥 **Tu es #1 !** Tu gagnes **x{self.TOP1_MULTIPLIER}** plus que les autres !\n"
                          f"⚡ Reste à la première place pour garder ce bonus !",
                    inline=False
                )
            else:
                embed.add_field(
                    name="🎯 Objectif TOP 1",
                    value=f"💡 Deviens **#1 du classement** pour gagner **x{self.TOP1_MULTIPLIER}** par message !\n"
                          f"🏆 Utilise `/leaderboard` pour voir ta position !",
                    inline=False
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
                color=Colors.GOLD if is_top1 else Colors.WARNING
            )
            
            embed.add_field(
                name="📊 Récompense suivante",
                value=f"**{reward_amount} PrissBuck{'s' if reward_amount > 1 else ''}** pour ton prochain message",
                inline=True
            )
            
            if is_top1:
                embed.add_field(
                    name="👑 Statut TOP 1",
                    value=f"🔥 **Multiplicateur x{self.TOP1_MULTIPLIER}** actif !",
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
        
        footer_text = f"Cooldown: {self.COOLDOWN_SECONDS}s • Récompense: {reward_amount} PB • Historique dans /transactions"
        if is_top1:
            footer_text += " • 👑 TOP 1 PRIVILÈGE ACTIF"
        embed.set_footer(text=footer_text)
        
        await ctx.send(embed=embed)

    @commands.command(name='msgtop', aliases=['message_top'])
    @commands.cooldown(1, 60, commands.BucketType.guild)  # Limite: 1 fois par minute par serveur
    async def msgtop_cmd(self, ctx):
        """Affiche un classement des utilisateurs les plus actifs avec indication TOP 1"""
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
            current_top1 = await self.get_current_top1_user()
            
            for user_id, last_reward in self.cooldowns.items():
                # Ne considérer que l'activité des 24 dernières heures
                if (now - last_reward).total_seconds() <= 86400:  # 24h
                    try:
                        user = self.bot.get_user(user_id)
                        if user and user in ctx.guild.members:  # Seulement les membres du serveur
                            is_top1 = current_top1 and user_id == current_top1
                            recent_activity.append((user, last_reward, is_top1))
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
                description=f"Utilisateurs les plus actifs récemment\n👑 **Le TOP 1 gagne x{self.TOP1_MULTIPLIER} par message !**",
                color=Colors.INFO
            )
            
            top_text = ""
            for i, (user, last_activity, is_top1) in enumerate(recent_activity[:10], 1):
                emoji = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                time_ago = now - last_activity
                if time_ago.total_seconds() < 3600:  # Moins d'1h
                    time_str = f"{int(time_ago.total_seconds()/60)}min"
                else:
                    time_str = f"{int(time_ago.total_seconds()/3600)}h"
                
                user_display = f"**{user.display_name}**"
                if is_top1:
                    user_display += " 👑"
                
                top_text += f"{emoji[i-1]} {user_display} - il y a {time_str}"
                if is_top1:
                    top_text += f" *({self.TOP1_MULTIPLIER} PB/msg)*"
                top_text += "\n"
            
            embed.add_field(
                name="⚡ Activité récente",
                value=top_text,
                inline=False
            )
            
            # Afficher qui est TOP 1
            if current_top1:
                try:
                    top1_user = self.bot.get_user(current_top1)
                    top1_name = top1_user.display_name if top1_user else f"User#{current_top1}"
                    embed.add_field(
                        name="👑 TOP 1 du Classement",
                        value=f"**{top1_name}** reçoit **x{self.TOP1_MULTIPLIER}** récompenses par message !",
                        inline=False
                    )
                except:
                    pass
            
            embed.add_field(
                name="ℹ️ Note",
                value=f"Ce classement se base sur l'activité récente des récompenses de messages.\n"
                      f"👑 Le **TOP 1 global** du serveur gagne **{self.TOP1_MULTIPLIER} PB** par message au lieu de 1 !",
                inline=False
            )
            
            embed.set_footer(text=f"Total: {len(recent_activity)} utilisateurs actifs sur 24h • TOP 1 privilège: x{self.TOP1_MULTIPLIER}")
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

    # ==================== COMMANDE SPÉCIALE TOP 1 ====================
    
    @commands.command(name='top1status', aliases=['top1', 'checktop1'])
    async def top1_status_cmd(self, ctx):
        """Vérifie qui est actuellement TOP 1 et ses privilèges"""
        try:
            current_top1 = await self.get_current_top1_user()
            
            embed = discord.Embed(
                title="👑 Statut TOP 1 - Privilèges Messages",
                color=Colors.GOLD
            )
            
            if current_top1:
                try:
                    top1_user = self.bot.get_user(current_top1)
                    if top1_user:
                        top1_name = top1_user.display_name
                        top1_avatar = top1_user.display_avatar.url
                    else:
                        top1_name = f"User#{current_top1}"
                        top1_avatar = None
                except:
                    top1_name = f"User#{current_top1}"
                    top1_avatar = None
                
                is_me_top1 = current_top1 == ctx.author.id
                
                if is_me_top1:
                    embed.description = f"🔥 **TU ES LE TOP 1 !** 🔥\n\nFélicitations **{ctx.author.display_name}** !"
                    embed.color = Colors.SUCCESS
                else:
                    embed.description = f"👑 **{top1_name}** est actuellement TOP 1"
                
                embed.add_field(
                    name="💰 Privilège TOP 1",
                    value=f"**{self.TOP1_MULTIPLIER} PrissBucks** par message au lieu de 1\n"
                          f"⚡ Multiplicateur **x{self.TOP1_MULTIPLIER}** actif !",
                    inline=True
                )
                
                embed.add_field(
                    name="📊 Statistiques",
                    value=f"**{self.stats['top1_bonuses_given']:,}** bonus TOP 1 donnés\n"
                          f"Cache mis à jour toutes les {self.TOP1_CHECK_INTERVAL//60} min",
                    inline=True
                )
                
                if is_me_top1:
                    embed.add_field(
                        name="🎯 Tes avantages",
                        value="✅ Tu reçois **10x plus** de PB par message !\n"
                              "✅ Notifications spéciales occasionnelles\n"
                              "✅ Statut prestigieux dans les commandes",
                        inline=False
                    )
                else:
                    # Récupérer le solde TOP 1 pour info
                    try:
                        top_users = await self.db.get_top_users(1)
                        if top_users:
                            top1_balance = top_users[0][1]
                            embed.add_field(
                                name="🏆 Solde TOP 1",
                                value=f"**{top1_balance:,}** PrissBucks",
                                inline=True
                            )
                    except:
                        pass
                    
                    embed.add_field(
                        name="🎯 Comment devenir TOP 1",
                        value="💰 Accumule plus de PrissBucks que lui !\n"
                              "🎮 Joue aux mini-jeux pour gagner gros\n"
                              "📈 Utilise `/leaderboard` pour voir ton rang",
                        inline=False
                    )
                
                if top1_avatar:
                    embed.set_thumbnail(url=top1_avatar)
            else:
                embed.description = "❓ **Aucun TOP 1 détecté**\n\nLe classement est vide ou en cours de mise à jour."
                embed.color = Colors.WARNING
                embed.add_field(
                    name="🚀 Opportunité !",
                    value=f"Sois le premier à accumuler des PrissBucks !\n"
                          f"Le premier joueur deviendra TOP 1 et gagnera **{self.TOP1_MULTIPLIER} PB** par message !",
                    inline=False
                )
            
            embed.add_field(
                name="ℹ️ Fonctionnement",
                value=f"• Le TOP 1 est basé sur le **classement général** (`/leaderboard`)\n"
                      f"• Mise à jour automatique toutes les **{self.TOP1_CHECK_INTERVAL//60} minutes**\n"
                      f"• Bonus appliqué instantanément lors des messages\n"
                      f"• Fonctionne dans tous les salons du serveur",
                inline=False
            )
            
            embed.set_footer(text=f"Multiplicateur TOP 1: x{self.TOP1_MULTIPLIER} • Récompense normale: {self.REWARD_AMOUNT} PB")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur top1status: {e}")
            embed = discord.Embed(
                title="❌ Erreur",
                description="Impossible de récupérer le statut TOP 1.",
                color=Colors.ERROR
            )
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(MessageRewards(bot))