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
    """Système de vol avec défense intégrée"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        self.cooldowns = {}
        
        self.SUCCESS_RATE = STEAL_SUCCESS_RATE
        self.STEAL_PERCENTAGE = STEAL_PERCENTAGE
        self.FAIL_PENALTY_PERCENTAGE = FAIL_PENALTY_PERCENTAGE
        self.COOLDOWN_HOURS = STEAL_COOLDOWN_HOURS
        self.COOLDOWN_SECONDS = STEAL_COOLDOWN_SECONDS
        
    async def cog_load(self):
        self.db = self.bot.database
        logger.info(f"✅ Cog Steal initialisé avec système de défense")

    async def _check_defense(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur a une défense active"""
        if not self.db or not self.db.pool:
            return False
        
        try:
            async with self.db.pool.acquire() as conn:
                has_defense = await conn.fetchval("""
                    SELECT 1 FROM user_defenses 
                    WHERE user_id = $1 AND active = TRUE
                """, user_id)
                return bool(has_defense)
        except Exception as e:
            logger.error(f"Erreur check defense: {e}")
            return False

    def is_on_cooldown(self, user_id: int) -> bool:
        if user_id not in self.cooldowns:
            return False
        now = datetime.now(timezone.utc)
        last_steal = self.cooldowns[user_id]
        cooldown_end = last_steal + timedelta(seconds=self.COOLDOWN_SECONDS)
        return now < cooldown_end

    def get_cooldown_remaining(self, user_id: int) -> int:
        if user_id not in self.cooldowns:
            return 0
        now = datetime.now(timezone.utc)
        last_steal = self.cooldowns[user_id]
        cooldown_end = last_steal + timedelta(seconds=self.COOLDOWN_SECONDS)
        if now >= cooldown_end:
            return 0
        return int((cooldown_end - now).total_seconds())

    def set_cooldown(self, user_id: int):
        self.cooldowns[user_id] = datetime.now(timezone.utc)

    @commands.command(name='voler', aliases=['steal', 'rob'])
    @commands.cooldown(1, STEAL_COOLDOWN_SECONDS, commands.BucketType.user)
    async def steal_cmd(self, ctx, target: discord.Member):
        """Tente de voler des PrissBucks"""
        thief = ctx.author
        victim = target
        
        if thief.id == victim.id:
            embed = create_error_embed("Vol impossible", "Tu ne peux pas te voler toi-même !")
            await ctx.send(embed=embed)
            return
            
        if victim.bot:
            embed = create_error_embed("Vol impossible", "Tu ne peux pas voler un bot !")
            await ctx.send(embed=embed)
            return

        # VÉRIFIER DÉFENSE
        if await self._check_defense(victim.id):
            embed = discord.Embed(
                title="🛡️ Défense active !",
                description=f"**{victim.display_name}** est protégé par une défense anti-vol !\n\nTon attaque est bloquée.",
                color=Colors.ERROR
            )
            embed.add_field(
                name="💡 Comment obtenir une défense ?",
                value="Utilise `/buy_defense` pour acheter ta propre protection (2000 PB)",
                inline=False
            )
            await ctx.send(embed=embed)
            return

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
            thief_balance = await self.db.get_balance(thief.id)
            victim_balance = await self.db.get_balance(victim.id)
            
            if thief_balance < 10:
                embed = create_error_embed(
                    "Solde insuffisant",
                    "Tu dois avoir au moins **10 PrissBucks** pour voler !"
                )
                await ctx.send(embed=embed)
                return
                
            if victim_balance < 10:
                embed = create_error_embed(
                    "Cible invalide", 
                    f"{victim.display_name} n'a pas assez de PrissBucks (minimum 10)."
                )
                await ctx.send(embed=embed)
                return

            steal_amount = max(1, int(victim_balance * (self.STEAL_PERCENTAGE / 100)))
            penalty_amount = max(1, int(thief_balance * (self.FAIL_PENALTY_PERCENTAGE / 100)))
            
            success = random.randint(1, 100) <= self.SUCCESS_RATE
            
            if success:
                # VOL RÉUSSI
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
                    embed = create_error_embed("Erreur", "Échec du transfert lors du vol.")
                    await ctx.send(embed=embed)
                    return
            else:
                # VOL ÉCHOUÉ
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
                    embed = create_error_embed("Erreur", "Échec du transfert lors de la pénalité.")
                    await ctx.send(embed=embed)
                    return

            self.set_cooldown(thief.id)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur vol {thief.id} -> {victim.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la tentative de vol.")
            await ctx.send(embed=embed)

    def format_cooldown_time(self, seconds: int) -> str:
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

async def setup(bot):
    await bot.add_cog(Steal(bot))
