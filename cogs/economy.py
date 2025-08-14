import discord
from discord.ext import commands
from discord import app_commands
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
        # Dictionnaire pour gérer les cooldowns manuellement des slash commands
        self.give_cooldowns = {}
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Economy initialisé (simplifié) avec slash commands")
    
    def _check_give_cooldown(self, user_id: int) -> float:
        """Vérifie et retourne le cooldown restant pour give"""
        import time
        now = time.time()
        if user_id in self.give_cooldowns:
            elapsed = now - self.give_cooldowns[user_id]
            if elapsed < TRANSFER_COOLDOWN:
                return TRANSFER_COOLDOWN - elapsed
        self.give_cooldowns[user_id] = now
        return 0

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

    @app_commands.command(name="balance", description="Affiche le solde d'un utilisateur")
    @app_commands.describe(utilisateur="L'utilisateur dont voir le solde (optionnel)")
    async def balance_slash(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        """Slash command pour voir le solde"""
        target = utilisateur or interaction.user
        
        try:
            balance = await self.db.get_balance(target.id)
            embed = create_balance_embed(target, balance)
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur balance pour {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération du solde.")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.command(name='addpb', aliases=['addprissbucks', 'give_admin'])
    @commands.has_permissions(administrator=True)
    async def addpb_cmd(self, ctx, member: discord.Member, amount: int):
        """[ADMIN] Ajoute des PrissBucks à un utilisateur"""
        await self._execute_addpb(ctx, member, amount)

    @app_commands.command(name="addpb", description="[ADMIN] Ajoute des PrissBucks à un utilisateur")
    @app_commands.describe(
        utilisateur="L'utilisateur à qui ajouter des PrissBucks",
        montant="Le montant de PrissBucks à ajouter"
    )
    @app_commands.default_permissions(administrator=True)
    async def addpb_slash(self, interaction: discord.Interaction, utilisateur: discord.Member, montant: int):
        """Slash command pour ajouter des PrissBucks (admin seulement)"""
        # Vérifier les permissions
        if not interaction.user.guild_permissions.administrator:
            embed = create_error_embed(
                "Permission refusée", 
                "Seuls les administrateurs peuvent utiliser cette commande."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()
        await self._execute_addpb(interaction, utilisateur, montant, is_slash=True)

    async def _execute_addpb(self, ctx_or_interaction, member, amount, is_slash=False):
        """Logique commune pour addpb (prefix et slash)"""
        if is_slash:
            admin = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            admin = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        # Validation du montant
        if amount <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif !")
            if is_slash:
                await send_func(embed=embed, ephemeral=True)
            else:
                await send_func(embed=embed)
            return

        if amount > 1000000:  # Limite de sécurité
            embed = create_error_embed(
                "Montant trop élevé", 
                "Le montant maximum est de 1,000,000 PrissBucks par ajout."
            )
            if is_slash:
                await send_func(embed=embed, ephemeral=True)
            else:
                await send_func(embed=embed)
            return

        try:
            # Récupérer le solde actuel
            old_balance = await self.db.get_balance(member.id)
            
            # Ajouter les PrissBucks
            await self.db.update_balance(member.id, amount)
            
            # Récupérer le nouveau solde
            new_balance = await self.db.get_balance(member.id)
            
            # Créer l'embed de confirmation
            embed = discord.Embed(
                title="💰 PrissBucks ajoutés !",
                description=f"**{amount:,}** PrissBucks ont été ajoutés à {member.display_name}",
                color=Colors.SUCCESS
            )
            
            embed.add_field(
                name="👤 Utilisateur",
                value=member.display_name,
                inline=True
            )
            
            embed.add_field(
                name="💵 Montant ajouté",
                value=f"+{amount:,} PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📊 Soldes",
                value=f"**Avant:** {old_balance:,}\n**Après:** {new_balance:,}",
                inline=True
            )
            
            embed.add_field(
                name="👮‍♂️ Administrateur",
                value=admin.display_name,
                inline=False
            )
            
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Action administrative - PrissBucks ajoutés")
            
            await send_func(embed=embed)
            
            # Log de l'action
            logger.info(f"ADMIN: {admin} a ajouté {amount} PrissBucks à {member} (nouveau solde: {new_balance})")
            
        except Exception as e:
            logger.error(f"Erreur addpb {admin.id} -> {member.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'ajout des PrissBucks.")
            if is_slash:
                await send_func(embed=embed, ephemeral=True)
            else:
                await send_func(embed=embed)

    @addpb_cmd.error
    async def addpb_error(self, ctx, error):
        """Gestion d'erreurs pour la commande addpb"""
        if isinstance(error, commands.MissingPermissions):
            embed = create_error_embed(
                "Permission refusée",
                "Seuls les administrateurs peuvent utiliser cette commande."
            )
            await ctx.send(embed=embed)
        else:
            # Laisser la gestion globale s'occuper des autres erreurs
            raise error

    @commands.command(name='give', aliases=['pay', 'transfer'])
    @commands.cooldown(1, TRANSFER_COOLDOWN, commands.BucketType.user)
    async def give_cmd(self, ctx, member: discord.Member, amount: int):
        """Donne des pièces à un autre utilisateur"""
        await self._execute_give(ctx, member, amount)

    @app_commands.command(name="give", description="Donne des PrissBucks à un autre utilisateur")
    @app_commands.describe(
        utilisateur="L'utilisateur à qui donner des PrissBucks",
        montant="Le montant de PrissBucks à donner"
    )
    async def give_slash(self, interaction: discord.Interaction, utilisateur: discord.Member, montant: int):
        """Slash command pour donner des PrissBucks"""
        # Vérifier le cooldown manuellement pour les slash commands
        cooldown_remaining = self._check_give_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            embed = create_cooldown_embed("give", cooldown_remaining)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        await self._execute_give(interaction, utilisateur, montant, is_slash=True)

    async def _execute_give(self, ctx_or_interaction, member, amount, is_slash=False):
        """Logique commune pour give (prefix et slash)"""
        if is_slash:
            giver = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            giver = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
            
        receiver = member
        
        # Validations
        if amount <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif !")
            await send_func(embed=embed)
            return
            
        if giver.id == receiver.id:
            embed = create_error_embed("Transfert impossible", "Tu ne peux pas te donner des pièces à toi-même !")
            await send_func(embed=embed)
            return
            
        if receiver.bot:
            embed = create_error_embed("Transfert impossible", "Tu ne peux pas donner des pièces à un bot !")
            await send_func(embed=embed)
            return

        try:
            # Vérifier le solde du donneur
            giver_balance = await self.db.get_balance(giver.id)
            if giver_balance < amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Tu as {giver_balance:,} PrissBucks mais tu essaies de donner {amount:,} PrissBucks."
                )
                await send_func(embed=embed)
                return

            # Effectuer le transfert
            success = await self.db.transfer(giver.id, receiver.id, amount)
            
            if success:
                new_balance = giver_balance - amount
                embed = create_transfer_embed(giver, receiver, amount, new_balance)
                await send_func(embed=embed)
            else:
                embed = create_error_embed("Échec du transfert", "Solde insuffisant.")
                await send_func(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur give {giver.id} -> {receiver.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du transfert.")
            await send_func(embed=embed)

    @commands.command(name='daily', aliases=['dailyspin', 'spin'])
    @commands.cooldown(1, DAILY_COOLDOWN, commands.BucketType.user)
    async def daily_cmd(self, ctx):
        """Récupère tes pièces quotidiennes"""
        await self._execute_daily(ctx)

    @app_commands.command(name="daily", description="Récupère tes pièces quotidiennes")
    async def daily_slash(self, interaction: discord.Interaction):
        """Slash command pour le daily"""
        await interaction.response.defer()
        
        # Vérifier le cooldown manuellement
        user_id = interaction.user.id
        now = datetime.now(timezone.utc)

        try:
            last_daily = await self.db.get_last_daily(user_id)
            
            if last_daily:
                delta = now - last_daily
                if delta.total_seconds() < DAILY_COOLDOWN:
                    remaining = DAILY_COOLDOWN - delta.total_seconds()
                    embed = create_cooldown_embed("daily", remaining)
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return

            await self._execute_daily(interaction, is_slash=True)
            
        except Exception as e:
            logger.error(f"Erreur daily pour {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du daily spin.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def _execute_daily(self, ctx_or_interaction, is_slash=False):
        """Logique commune pour daily (prefix et slash)"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        user_id = user.id
        now = datetime.now(timezone.utc)

        try:
            # Vérifier le dernier daily (pour prefix command seulement, slash l'a déjà vérifié)
            if not is_slash:
                last_daily = await self.db.get_last_daily(user_id)
                
                if last_daily:
                    delta = now - last_daily
                    if delta.total_seconds() < DAILY_COOLDOWN:
                        remaining = DAILY_COOLDOWN - delta.total_seconds()
                        embed = create_cooldown_embed("daily", remaining)
                        await send_func(embed=embed)
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
            embed = create_daily_embed(user, total_reward, bonus)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur daily pour {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du daily spin.")
            await send_func(embed=embed)

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
        else:
            # Laisser la gestion globale s'en occuper
            raise error

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Economy(bot))