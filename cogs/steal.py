import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import random
import logging

from config import (
    STEAL_SUCCESS_RATE, STEAL_PERCENTAGE, STEAL_FAIL_PENALTY_PERCENTAGE,
    STEAL_COOLDOWN_HOURS, STEAL_COOLDOWN_SECONDS, Colors, Emojis
)
from utils.embeds import create_error_embed, create_success_embed

logger = logging.getLogger(__name__)

class Steal(commands.Cog):
    """Système de vol avec risque et récompense"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Dictionnaire pour stocker les cooldowns en mémoire
        # Format: {user_id: datetime_last_steal}
        self.cooldowns = {}
        
        # Configuration depuis config.py
        self.SUCCESS_RATE = STEAL_SUCCESS_RATE
        self.STEAL_PERCENTAGE = STEAL_PERCENTAGE
        self.FAIL_PENALTY_PERCENTAGE = STEAL_FAIL_PENALTY_PERCENTAGE
        self.COOLDOWN_HOURS = STEAL_COOLDOWN_HOURS
        self.COOLDOWN_SECONDS = STEAL_COOLDOWN_SECONDS
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info(f"✅ Cog Steal initialisé ({self.STEAL_PERCENTAGE}% vol, {self.FAIL_PENALTY_PERCENTAGE}% perte, {self.SUCCESS_RATE}% réussite, CD: {self.COOLDOWN_HOURS}h)")

    def is_on_cooldown(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur est en cooldown"""
        if user_id not in self.cooldowns:
            return False
            
        now = datetime.now(timezone.utc)
        last_steal = self.cooldowns[user_id]
        cooldown_end = last_steal + timedelta(seconds=self.COOLDOWN_SECONDS)
        
        return now < cooldown_end

    def get_cooldown_remaining(self, user_id: int) -> int:
        """Retourne le temps de cooldown restant en secondes"""
        if user_id not in self.cooldowns:
            return 0
            
        now = datetime.now(timezone.utc)
        last_steal = self.cooldowns[user_id]
        cooldown_end = last_steal + timedelta(seconds=self.COOLDOWN_SECONDS)
        
        if now >= cooldown_end:
            return 0
            
        return int((cooldown_end - now).total_seconds())

    def set_cooldown(self, user_id: int):
        """Met l'utilisateur en cooldown"""
        self.cooldowns[user_id] = datetime.now(timezone.utc)

    def format_cooldown_time(self, seconds: int) -> str:
        """Formate le temps de cooldown en format lisible"""
        if seconds <= 0:
            return "Disponible"
            
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        
        if hours > 0:
            return f"{hours}h {minutes}min {secs}s"
        elif minutes > 0:
            return f"{minutes}min {secs}s"
        else:
            return f"{secs}s"

    @commands.command(name='voler', aliases=['steal', 'rob'])
    @commands.cooldown(1, STEAL_COOLDOWN_SECONDS, commands.BucketType.user)  # Cooldown Discord en backup
    async def steal_cmd(self, ctx, target: discord.Member):
        """Tente de voler 10% des pièces d'un autre utilisateur"""
        thief = ctx.author
        victim = target
        
        # Validations de base
        if thief.id == victim.id:
            embed = create_error_embed("Vol impossible", "Tu ne peux pas te voler toi-même !")
            await ctx.send(embed=embed)
            return
            
        if victim.bot:
            embed = create_error_embed("Vol impossible", "Tu ne peux pas voler un bot !")
            await ctx.send(embed=embed)
            return

        # Vérifier le cooldown personnalisé
        if self.is_on_cooldown(thief.id):
            remaining = self.get_cooldown_remaining(thief.id)
            time_str = self.format_cooldown_time(remaining)
            
            embed = discord.Embed(
                title=f"{Emojis.COOLDOWN} Cooldown actif !",
                description=f"Tu pourras utiliser `voler` dans **{time_str}**",
                color=Colors.WARNING
            )
            await ctx.send(embed=embed)
            return

        try:
            # Récupérer les soldes
            thief_balance = await self.db.get_balance(thief.id)
            victim_balance = await self.db.get_balance(victim.id)
            
            # Vérifications des soldes minimums
            if thief_balance < 10:
                embed = create_error_embed(
                    "Solde insuffisant",
                    "Tu dois avoir au moins **10 PrissBucks** pour pouvoir voler !"
                )
                await ctx.send(embed=embed)
                return
                
            if victim_balance < 10:
                embed = create_error_embed(
                    "Cible invalide", 
                    f"{victim.display_name} n'a pas assez de PrissBucks à voler (minimum 10)."
                )
                await ctx.send(embed=embed)
                return

            # Calculer les montants
            steal_amount = max(1, int(victim_balance * (self.STEAL_PERCENTAGE / 100)))  # 10% de la victime
            penalty_amount = max(1, int(thief_balance * (self.FAIL_PENALTY_PERCENTAGE / 100)))  # 40% du voleur
            
            # Déterminer si le vol réussit (50% de chances)
            success = random.randint(1, 100) <= self.SUCCESS_RATE
            
            if success:
                # VOL RÉUSSI
                # Transférer l'argent de la victime au voleur
                success_transfer = await self.db.transfer(victim.id, thief.id, steal_amount)
                
                if success_transfer:
                    new_thief_balance = thief_balance + steal_amount
                    new_victim_balance = victim_balance - steal_amount
                    
                    embed = discord.Embed(
                        title="🎯 Vol réussi !",
                        description=f"**{thief.display_name}** a volé **{steal_amount:,} PrissBucks** à **{victim.display_name}** !",
                        color=Colors.SUCCESS
                    )
                    embed.add_field(
                        name="💰 Butin",
                        value=f"**+{steal_amount:,}** PrissBucks volés",
                        inline=True
                    )
                    embed.add_field(
                        name="💳 Nouveau solde",
                        value=f"**{new_thief_balance:,}** PrissBucks",
                        inline=True
                    )
                    embed.set_footer(text=f"Prochaine tentative dans {self.COOLDOWN_HOURS}h")
                    
                    logger.info(f"Vol réussi: {thief} a volé {steal_amount} à {victim}")
                else:
                    # Cas rare où le transfert échoue
                    embed = create_error_embed("Erreur", "Échec du transfert lors du vol.")
                    await ctx.send(embed=embed)
                    return
            else:
                # VOL ÉCHOUÉ
                # Transférer 40% du voleur à la victime
                success_transfer = await self.db.transfer(thief.id, victim.id, penalty_amount)
                
                if success_transfer:
                    new_thief_balance = thief_balance - penalty_amount
                    new_victim_balance = victim_balance + penalty_amount
                    
                    embed = discord.Embed(
                        title="❌ Vol échoué !",
                        description=f"**{thief.display_name}** s'est fait prendre en essayant de voler **{victim.display_name}** !",
                        color=Colors.ERROR
                    )
                    embed.add_field(
                        name="💸 Pénalité",
                        value=f"**-{penalty_amount:,}** PrissBucks perdus",
                        inline=True
                    )
                    embed.add_field(
                        name="💳 Nouveau solde",
                        value=f"**{new_thief_balance:,}** PrissBucks",
                        inline=True
                    )
                    embed.add_field(
                        name="🎁 Bonus victime",
                        value=f"{victim.display_name} gagne **{penalty_amount:,}** PrissBucks !",
                        inline=False
                    )
                    embed.set_footer(text=f"Prochaine tentative dans {self.COOLDOWN_HOURS}h")
                    
                    logger.info(f"Vol échoué: {thief} a perdu {penalty_amount} au profit de {victim}")
                else:
                    # Cas rare où le transfert échoue
                    embed = create_error_embed("Erreur", "Échec du transfert lors de la pénalité.")
                    await ctx.send(embed=embed)
                    return

            # Mettre en cooldown dans tous les cas (réussite ou échec)
            self.set_cooldown(thief.id)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur vol {thief.id} -> {victim.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la tentative de vol.")
            await ctx.send(embed=embed)

    @commands.command(name='stealcd', aliases=['volcd', 'cooldownvol'])
    async def steal_cooldown(self, ctx):
        """Vérifie le cooldown de la commande voler"""
        user_id = ctx.author.id
        remaining = self.get_cooldown_remaining(user_id)
        
        if remaining <= 0:
            embed = create_success_embed(
                "Cooldown Vol",
                "✅ Tu peux tenter de voler quelqu'un !"
            )
        else:
            time_str = self.format_cooldown_time(remaining)
            embed = discord.Embed(
                title="⏰ Cooldown Vol",
                description=f"Tu pourras voler dans **{time_str}**.",
                color=Colors.WARNING
            )
        
        await ctx.send(embed=embed)

    @commands.command(name='stealinfo', aliases=['volinfo', 'volerinfo'])
    async def steal_info(self, ctx):
        """Affiche les informations sur le système de vol"""
        user_id = ctx.author.id
        remaining = self.get_cooldown_remaining(user_id)
        cooldown_status = f"⏰ **{self.format_cooldown_time(remaining)}**" if remaining > 0 else "✅ **Disponible**"
        
        embed = discord.Embed(
            title="🎯 Système de Vol",
            description="Tente de voler d'autres utilisateurs... mais attention aux risques !",
            color=Colors.WARNING
        )
        
        embed.add_field(
            name="📊 Probabilités",
            value=f"**{self.SUCCESS_RATE}%** de chances de réussite\n"
                  f"**{100-self.SUCCESS_RATE}%** de chances d'échec",
            inline=True
        )
        
        embed.add_field(
            name="💰 Si tu réussis",
            value=f"Tu voles **{self.STEAL_PERCENTAGE}%** des PrissBucks de ta cible",
            inline=True
        )
        
        embed.add_field(
            name="💸 Si tu échoues",
            value=f"Tu perds **{self.FAIL_PENALTY_PERCENTAGE}%** de tes PrissBucks\n"
                  f"(qui vont à ta cible)",
            inline=True
        )
        
        embed.add_field(
            name="⏱️ Cooldown",
            value=f"**{self.COOLDOWN_HOURS} heure(s)** entre chaque tentative",
            inline=True
        )
        
        embed.add_field(
            name="🎯 Ton Status",
            value=cooldown_status,
            inline=True
        )
        
        embed.add_field(
            name="📋 Règles",
            value="• Minimum 10 PrissBucks pour voler\n"
                  "• La cible doit avoir minimum 10 PrissBucks\n"
                  "• Impossible de se voler soi-même\n"
                  "• Impossible de voler les bots",
            inline=False
        )
        
        embed.set_footer(text=f"Utilise `voler @utilisateur` pour tenter ta chance !")
        await ctx.send(embed=embed)

    # Nettoyage automatique des anciens cooldowns
    async def cleanup_old_cooldowns(self):
        """Supprime les anciens cooldowns pour économiser la mémoire"""
        if not self.cooldowns:
            return
            
        now = datetime.now(timezone.utc)
        expired_users = []
        
        for user_id, last_steal in self.cooldowns.items():
            # Supprimer après 2 heures d'inactivité (cooldown + marge)
            if now - last_steal > timedelta(hours=2):
                expired_users.append(user_id)
        
        for user_id in expired_users:
            del self.cooldowns[user_id]
            
        if expired_users:
            logger.debug(f"Nettoyage vol: {len(expired_users)} cooldowns expirés supprimés")

    # Gestion d'erreur pour le cooldown
    @steal_cmd.error
    async def steal_error(self, ctx, error):
        """Gestion d'erreurs spécifique au vol"""
        if isinstance(error, commands.CommandOnCooldown):
            # Utiliser notre cooldown personnalisé au lieu du cooldown Discord
            remaining = self.get_cooldown_remaining(ctx.author.id)
            if remaining > 0:
                time_str = self.format_cooldown_time(remaining)
                embed = discord.Embed(
                    title=f"{Emojis.COOLDOWN} Cooldown actif !",
                    description=f"Tu pourras utiliser `voler` dans **{time_str}**",
                    color=Colors.WARNING
                )
                await ctx.send(embed=embed)
            else:
                # Reset le cooldown Discord si notre cooldown est fini
                ctx.command.reset_cooldown(ctx)
        else:
            # Laisser la gestion globale s'occuper des autres erreurs
            raise error

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Steal(bot))