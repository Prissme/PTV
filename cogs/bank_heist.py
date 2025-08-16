import discord
from discord.ext import commands
from discord import app_commands
import logging
import random
from datetime import datetime, timezone, timedelta

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_info_embed

logger = logging.getLogger(__name__)

class BankHeist(commands.Cog):
    """Système de braquage de banque privée - Simple et équilibré"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Configuration du braquage
        self.SUCCESS_RATE = 25  # 25% de chances de réussite (plus difficile que le vol normal)
        self.STEAL_PERCENTAGE = 15  # Vol 15% de la banque de la cible
        self.FAIL_PENALTY_MAIN = 30  # Perd 30% du solde principal si échec
        self.COOLDOWN_HOURS = 6  # 6 heures de cooldown (plus long que vol normal)
        self.COOLDOWN_SECONDS = self.COOLDOWN_HOURS * 3600
        
        # Dictionnaire pour les cooldowns
        self.heist_cooldowns = {}
        
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info(f"✅ Cog BankHeist initialisé ({self.STEAL_PERCENTAGE}% vol banque, {self.FAIL_PENALTY_MAIN}% perte principale, {self.SUCCESS_RATE}% réussite, CD: {self.COOLDOWN_HOURS}h)")

    def _check_heist_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown restant"""
        import time
        now = time.time()
        if user_id in self.heist_cooldowns:
            elapsed = now - self.heist_cooldowns[user_id]
            if elapsed < self.COOLDOWN_SECONDS:
                return self.COOLDOWN_SECONDS - elapsed
        return 0

    def _set_heist_cooldown(self, user_id: int):
        """Met en cooldown"""
        import time
        self.heist_cooldowns[user_id] = time.time()

    def _format_cooldown_time(self, seconds: int) -> str:
        """Formate le temps de cooldown"""
        if seconds <= 0:
            return "Disponible"
        
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes}min {secs}s"
        elif minutes > 0:
            return f"{minutes}min {secs}s"
        else:
            return f"{secs}s"

    async def get_bank_cog(self):
        """Récupère le cog Bank"""
        return self.bot.get_cog('Bank')

    @commands.command(name='braquer', aliases=['heist', 'bankheist', 'braquage'])
    async def bank_heist_cmd(self, ctx, target: discord.Member):
        """Tente de braquer la banque privée d'un autre utilisateur (très risqué !)"""
        thief = ctx.author
        victim = target
        
        # Validations de base
        if thief.id == victim.id:
            embed = create_error_embed("Braquage impossible", "Tu ne peux pas braquer ta propre banque !")
            await ctx.send(embed=embed)
            return
            
        if victim.bot:
            embed = create_error_embed("Braquage impossible", "Tu ne peux pas braquer la banque d'un bot !")
            await ctx.send(embed=embed)
            return

        # Vérifier le cooldown
        cooldown_remaining = self._check_heist_cooldown(thief.id)
        if cooldown_remaining > 0:
            time_str = self._format_cooldown_time(cooldown_remaining)
            embed = discord.Embed(
                title=f"🏦 Braquage en cooldown !",
                description=f"Tu pourras braquer une banque dans **{time_str}**\n\n"
                           f"💡 **Le braquage de banque est très risqué !**\n"
                           f"🎯 Seulement {self.SUCCESS_RATE}% de chances de réussite\n"
                           f"💸 En cas d'échec, tu perds {self.FAIL_PENALTY_MAIN}% de ton solde principal !",
                color=Colors.WARNING
            )
            embed.add_field(
                name="⚡ Conseils pendant l'attente",
                value="• 🏛️ Utilise `/publicbank` pour récupérer des fonds sûrs\n"
                      "• 💰 Accumule plus de PrissBucks pour minimiser les pertes\n"
                      "• 🎯 Le braquage est plus risqué mais plus rentable que le vol normal",
                inline=False
            )
            await ctx.send(embed=embed)
            return

        try:
            # Récupérer le cog Bank pour accéder aux fonctions bancaires
            bank_cog = await self.get_bank_cog()
            if not bank_cog:
                embed = create_error_embed("Erreur", "Système bancaire indisponible.")
                await ctx.send(embed=embed)
                return

            # Récupérer les soldes
            thief_main_balance = await self.db.get_balance(thief.id)
            victim_bank_balance = await bank_cog.get_bank_balance(victim.id)
            
            # Vérifications des soldes minimums
            if thief_main_balance < 100:
                embed = create_error_embed(
                    "Solde insuffisant",
                    "Tu dois avoir au moins **100 PrissBucks** sur ton compte principal pour braquer une banque !\n\n"
                    "💡 **Pourquoi ?** En cas d'échec, tu risques de perdre une partie de ton solde."
                )
                await ctx.send(embed=embed)
                return
                
            if victim_bank_balance < 50:
                embed = create_error_embed(
                    "Cible invalide", 
                    f"**{victim.display_name}** n'a que **{victim_bank_balance:,}** PrissBucks en banque.\n"
                    f"Minimum requis : **50 PrissBucks** pour un braquage."
                )
                embed.add_field(
                    name="💡 Alternative",
                    value=f"Utilise `{PREFIX}voler @{victim.display_name}` pour voler son solde principal à la place !",
                    inline=False
                )
                await ctx.send(embed=embed)
                return

            # Calculer les montants
            steal_amount = max(1, int(victim_bank_balance * (self.STEAL_PERCENTAGE / 100)))
            penalty_amount = max(1, int(thief_main_balance * (self.FAIL_PENALTY_MAIN / 100)))
            
            # Déterminer si le braquage réussit
            success = random.randint(1, 100) <= self.SUCCESS_RATE
            
            if success:
                # BRAQUAGE RÉUSSI - Transférer de la banque de la victime vers le compte principal du voleur
                logger.info(f"🏦 Braquage: {thief} tente de voler {steal_amount} PB de la banque de {victim}")
                
                # Débiter la banque de la victime
                success_debit = await bank_cog.update_bank_balance(victim.id, -steal_amount, "withdraw")
                if not success_debit:
                    embed = create_error_embed("Erreur technique", "Impossible de débiter la banque de la cible.")
                    await ctx.send(embed=embed)
                    return
                
                # Créditer le compte principal du voleur
                await self.db.update_balance(thief.id, steal_amount)
                
                # Calculer les nouveaux soldes
                new_thief_balance = thief_main_balance + steal_amount
                new_victim_bank_balance = victim_bank_balance - steal_amount
                
                # Logger les transactions
                if hasattr(self.bot, 'transaction_logs'):
                    # Log pour le voleur (gain)
                    await self.bot.transaction_logs.log_transaction(
                        user_id=thief.id,
                        transaction_type='bank_heist_success',
                        amount=steal_amount,
                        balance_before=thief_main_balance,
                        balance_after=new_thief_balance,
                        description=f"Braquage RÉUSSI banque de {victim.display_name}",
                        related_user_id=victim.id
                    )
                    
                    # Log pour la victime (perte bancaire)
                    victim_main_balance = await self.db.get_balance(victim.id)
                    await self.bot.transaction_logs.log_transaction(
                        user_id=victim.id,
                        transaction_type='bank_heist_victim',
                        amount=-steal_amount,
                        balance_before=victim_main_balance,  # Son solde principal ne change pas
                        balance_after=victim_main_balance,
                        description=f"Banque braquée par {thief.display_name} (-{steal_amount} de banque)",
                        related_user_id=thief.id
                    )

                embed = discord.Embed(
                    title="🏆 Braquage réussi !",
                    description=f"**{thief.display_name}** a braqué avec succès la banque de **{victim.display_name}** !",
                    color=Colors.SUCCESS
                )
                embed.add_field(
                    name="💰 Butin récupéré",
                    value=f"**+{steal_amount:,}** PrissBucks volés de la banque !",
                    inline=True
                )
                embed.add_field(
                    name="💳 Ton nouveau solde",
                    value=f"**{new_thief_balance:,}** PrissBucks",
                    inline=True
                )
                embed.add_field(
                    name="🏦 Dégâts causés",
                    value=f"Banque de {victim.display_name}: {victim_bank_balance:,} → {new_victim_bank_balance:,} PB",
                    inline=False
                )
                embed.add_field(
                    name="🎯 Exploit rare !",
                    value=f"Tu as réussi un braquage avec seulement {self.SUCCESS_RATE}% de chances ! 🍀",
                    inline=False
                )
                embed.set_footer(text=f"Prochain braquage dans {self.COOLDOWN_HOURS}h • Crime parfait !")
                
                logger.info(f"🏦 Braquage RÉUSSI: {thief} a volé {steal_amount} PB de la banque de {victim}")
            else:
                # BRAQUAGE ÉCHOUÉ - Perdre une partie du solde principal vers la banque publique
                logger.info(f"🏦 Braquage ÉCHOUÉ: {thief} perd {penalty_amount} PB (échec contre {victim})")
                
                # Débiter le compte principal du voleur
                await self.db.update_balance(thief.id, -penalty_amount)
                
                # Envoyer la pénalité vers la banque publique
                public_bank_cog = self.bot.get_cog('PublicBank')
                if public_bank_cog and hasattr(public_bank_cog, 'add_casino_loss'):
                    await public_bank_cog.add_casino_loss(penalty_amount, "bank_heist_fail")
                    bank_message = "🏛️ Ta pénalité a été ajoutée à la **banque publique** !"
                else:
                    bank_message = "💸 Ta pénalité a été perdue."
                
                new_thief_balance = thief_main_balance - penalty_amount
                
                # Logger la transaction
                if hasattr(self.bot, 'transaction_logs'):
                    await self.bot.transaction_logs.log_transaction(
                        user_id=thief.id,
                        transaction_type='bank_heist_fail',
                        amount=-penalty_amount,
                        balance_before=thief_main_balance,
                        balance_after=new_thief_balance,
                        description=f"Braquage ÉCHOUÉ banque de {victim.display_name}",
                        related_user_id=victim.id
                    )

                embed = discord.Embed(
                    title="🚨 Braquage échoué !",
                    description=f"**{thief.display_name}** s'est fait prendre en tentant de braquer la banque de **{victim.display_name}** !",
                    color=Colors.ERROR
                )
                embed.add_field(
                    name="💸 Pénalité lourde",
                    value=f"**-{penalty_amount:,}** PrissBucks perdus de ton solde principal",
                    inline=True
                )
                embed.add_field(
                    name="💳 Ton nouveau solde",
                    value=f"**{new_thief_balance:,}** PrissBucks",
                    inline=True
                )
                embed.add_field(
                    name="🏛️ Impact social",
                    value=bank_message,
                    inline=False
                )
                embed.add_field(
                    name="⚖️ Justice rendue",
                    value=f"Les systèmes de sécurité de **{victim.display_name}** t'ont stoppé !\n"
                          f"Tu avais seulement {self.SUCCESS_RATE}% de chances de réussir.",
                    inline=False
                )
                embed.set_footer(text=f"Prochain braquage dans {self.COOLDOWN_HOURS}h • Sois plus prudent !")
                
                logger.info(f"🏦 Braquage ÉCHOUÉ: {thief} a perdu {penalty_amount} PB (échec contre {victim})")

            # Mettre en cooldown dans tous les cas
            self._set_heist_cooldown(thief.id)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur bank heist {thief.id} -> {victim.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la tentative de braquage.")
            await ctx.send(embed=embed)

    @commands.command(name='heistcd', aliases=['braquagecd', 'cooldownbraquer'])
    async def heist_cooldown_cmd(self, ctx):
        """Vérifie le cooldown du braquage"""
        user_id = ctx.author.id
        remaining = self._check_heist_cooldown(user_id)
        
        if remaining <= 0:
            embed = create_success_embed(
                "Braquage disponible !",
                f"✅ Tu peux tenter de braquer la banque de quelqu'un !\n\n"
                f"⚠️ **Rappel des risques:**\n"
                f"• {self.SUCCESS_RATE}% de chances de réussite seulement\n"
                f"• {self.STEAL_PERCENTAGE}% de la banque cible si réussite\n"
                f"• {self.FAIL_PENALTY_MAIN}% de ton solde principal si échec"
            )
            embed.add_field(
                name="🎯 Comment faire ?",
                value=f"Utilise `{PREFIX}braquer @utilisateur` pour tenter ta chance !",
                inline=False
            )
        else:
            time_str = self._format_cooldown_time(remaining)
            embed = discord.Embed(
                title="⏰ Cooldown Braquage",
                description=f"Tu pourras braquer une banque dans **{time_str}**.",
                color=Colors.WARNING
            )
            embed.add_field(
                name="💡 En attendant",
                value="• 💰 Accumule des PrissBucks sur ton compte principal\n"
                      "• 🏛️ Récupère des fonds de la banque publique\n"
                      "• 🎯 Repère les cibles avec beaucoup en banque !",
                inline=False
            )
        
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name='heistinfo', aliases=['braquageinfo', 'infobraquer'])
    async def heist_info_cmd(self, ctx):
        """Affiche toutes les informations sur le système de braquage"""
        user_id = ctx.author.id
        remaining = self._check_heist_cooldown(user_id)
        cooldown_status = f"⏰ **{self._format_cooldown_time(remaining)}**" if remaining > 0 else "✅ **Disponible**"
        
        embed = discord.Embed(
            title="🏦 Système de Braquage de Banque",
            description="**TRÈS RISQUÉ** - Tente de voler directement dans la banque privée d'autres utilisateurs !",
            color=Colors.WARNING
        )
        
        embed.add_field(
            name="📊 Probabilités",
            value=f"**{self.SUCCESS_RATE}%** de chances de réussite ⚡\n"
                  f"**{100-self.SUCCESS_RATE}%** de chances d'échec 💥",
            inline=True
        )
        
        embed.add_field(
            name="💰 Si tu réussis",
            value=f"Tu voles **{self.STEAL_PERCENTAGE}%** de la banque de ta cible\n"
                  f"(Argent ajouté à ton solde principal)",
            inline=True
        )
        
        embed.add_field(
            name="💸 Si tu échoues",
            value=f"Tu perds **{self.FAIL_PENALTY_MAIN}%** de ton solde principal\n"
                  f"(Pénalité va à la banque publique)",
            inline=True
        )
        
        embed.add_field(
            name="⏱️ Cooldown",
            value=f"**{self.COOLDOWN_HOURS} heures** entre chaque tentative\n"
                  f"(Plus long que le vol normal)",
            inline=True
        )
        
        embed.add_field(
            name="🎯 Ton Status",
            value=cooldown_status,
            inline=True
        )
        
        embed.add_field(
            name="🆚 Comparaison Vol vs Braquage",
            value=f"**Vol normal:** 70% réussite, 10% vol, 40% perte, 30min CD\n"
                  f"**Braquage:** {self.SUCCESS_RATE}% réussite, {self.STEAL_PERCENTAGE}% vol banque, {self.FAIL_PENALTY_MAIN}% perte, {self.COOLDOWN_HOURS}h CD",
            inline=False
        )
        
        embed.add_field(
            name="📋 Règles strictes",
            value="• **Minimum 100 PB** sur ton compte principal pour braquer\n"
                  "• La cible doit avoir **minimum 50 PB en banque**\n"
                  "• Impossible de braquer sa propre banque\n"
                  "• Impossible de braquer les bots\n"
                  "• **Tu voles la BANQUE, pas le solde principal**",
            inline=False
        )
        
        embed.add_field(
            name="🏛️ Impact Social",
            value="En cas d'échec, ta pénalité va à la **banque publique**\n"
                  f"Utilise `/publicbank` pour récupérer des fonds communautaires !",
            inline=False
        )
        
        embed.set_footer(text=f"Utilise `{PREFIX}braquer @utilisateur` pour tenter ta chance ! TRÈS RISQUÉ !")
        await ctx.send(embed=embed)

    @app_commands.command(name="heist", description="🏦 Braque la banque privée d'un utilisateur (TRÈS RISQUÉ !)")
    @app_commands.describe(cible="L'utilisateur dont tu veux braquer la banque privée")
    async def heist_slash(self, interaction: discord.Interaction, cible: discord.Member):
        """Slash command pour le braquage"""
        # Vérifier le cooldown immédiatement
        cooldown_remaining = self._check_heist_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            time_str = self._format_cooldown_time(cooldown_remaining)
            embed = discord.Embed(
                title="🏦 Braquage en cooldown !",
                description=f"Tu pourras braquer une banque dans **{time_str}**",
                color=Colors.WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Créer un contexte factice pour réutiliser la logique existante
        class FakeCtx:
            def __init__(self, interaction):
                self.author = interaction.user
                self.send = interaction.followup.send
        
        fake_ctx = FakeCtx(interaction)
        await self.bank_heist_cmd(fake_ctx, cible)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(BankHeist(bot))