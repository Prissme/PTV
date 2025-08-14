import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone
import random
import logging

from config import (
    DAILY_MIN, DAILY_MAX, DAILY_BONUS_CHANCE, DAILY_BONUS_MIN, DAILY_BONUS_MAX,
    DAILY_COOLDOWN, TRANSFER_COOLDOWN, TRANSFER_TAX_RATE, OWNER_ID, Colors, Emojis
)
from utils.embeds import (
    create_balance_embed, create_daily_embed, create_transfer_embed,
    create_error_embed, create_cooldown_embed
)

logger = logging.getLogger(__name__)

class Economy(commands.Cog):
    """Commandes économie essentielles avec taxes 5% : balance, daily, give"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info(f"✅ Cog Economy initialisé avec taxes transfert {TRANSFER_TAX_RATE*100}% et slash commands")

    def create_transfer_embed_with_tax(self, giver: discord.Member, receiver: discord.Member, tax_info: dict, new_balance: int) -> discord.Embed:
        """Créer un embed pour les transferts avec détails de la taxe"""
        embed = discord.Embed(
            title=f"{Emojis.TRANSFER} Transfert réussi !",
            color=Colors.SUCCESS
        )
        
        gross_amount = tax_info['gross_amount']
        net_amount = tax_info['net_amount'] 
        tax_amount = tax_info['tax_amount']
        tax_rate = tax_info['tax_rate']
        
        embed.add_field(
            name="👤 De → Vers",
            value=f"**{giver.display_name}** → **{receiver.display_name}**",
            inline=False
        )
        
        embed.add_field(
            name="💰 Détail du transfert",
            value=f"**Montant demandé:** {gross_amount:,} {Emojis.MONEY}\n"
                  f"**Taxe ({tax_rate}%):** -{tax_amount:,} {Emojis.MONEY}\n"
                  f"**Reçu par {receiver.display_name}:** {net_amount:,} {Emojis.MONEY}",
            inline=True
        )
        
        embed.add_field(
            name="💳 Nouveau solde",
            value=f"**{giver.display_name}:** {new_balance:,} {Emojis.MONEY}",
            inline=True
        )
        
        if tax_amount > 0:
            embed.set_footer(text=f"Taxe de {tax_amount:,} {Emojis.MONEY} collectée pour le serveur")
        
        return embed

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

    @commands.command(name='addpb', aliases=['addprissbucks', 'give_admin'])
    @commands.has_permissions(administrator=True)
    async def addpb_cmd(self, ctx, member: discord.Member, amount: int):
        """[ADMIN] Ajoute des PrissBucks à un utilisateur"""
        # Validation du montant
        if amount <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif !")
            await ctx.send(embed=embed)
            return

        if amount > 1000000:  # Limite de sécurité
            embed = create_error_embed(
                "Montant trop élevé", 
                "Le montant maximum est de 1,000,000 PrissBucks par ajout."
            )
            await ctx.send(embed=embed)
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
                value=ctx.author.display_name,
                inline=False
            )
            
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="Action administrative - PrissBucks ajoutés")
            
            await ctx.send(embed=embed)
            
            # Log de l'action
            logger.info(f"ADMIN: {ctx.author} a ajouté {amount} PrissBucks à {member} (nouveau solde: {new_balance})")
            
        except Exception as e:
            logger.error(f"Erreur addpb {ctx.author.id} -> {member.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'ajout des PrissBucks.")
            await ctx.send(embed=embed)

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
        """Donne des pièces à un autre utilisateur (taxe 5%)"""
        await self._execute_give(ctx, member, amount)

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

        # Validation du montant
        if montant <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif !")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if montant > 1000000:  # Limite de sécurité
            embed = create_error_embed(
                "Montant trop élevé", 
                "Le montant maximum est de 1,000,000 PrissBucks par ajout."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # Récupérer le solde actuel
            old_balance = await self.db.get_balance(utilisateur.id)
            
            # Ajouter les PrissBucks
            await self.db.update_balance(utilisateur.id, montant)
            
            # Récupérer le nouveau solde
            new_balance = await self.db.get_balance(utilisateur.id)
            
            # Créer l'embed de confirmation
            embed = discord.Embed(
                title="💰 PrissBucks ajoutés !",
                description=f"**{montant:,}** PrissBucks ont été ajoutés à {utilisateur.display_name}",
                color=Colors.SUCCESS
            )
            
            embed.add_field(
                name="👤 Utilisateur",
                value=utilisateur.display_name,
                inline=True
            )
            
            embed.add_field(
                name="💵 Montant ajouté",
                value=f"+{montant:,} PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📊 Soldes",
                value=f"**Avant:** {old_balance:,}\n**Après:** {new_balance:,}",
                inline=True
            )
            
            embed.add_field(
                name="👮‍♂️ Administrateur",
                value=interaction.user.display_name,
                inline=False
            )
            
            embed.set_thumbnail(url=utilisateur.display_avatar.url)
            embed.set_footer(text="Action administrative - PrissBucks ajoutés")
            
            await interaction.followup.send(embed=embed)
            
            # Log de l'action
            logger.info(f"ADMIN: {interaction.user} a ajouté {montant} PrissBucks à {utilisateur} (nouveau solde: {new_balance})")
            
        except Exception as e:
            logger.error(f"Erreur addpb {interaction.user.id} -> {utilisateur.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'ajout des PrissBucks.")
            await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="give", description="Donne des PrissBucks à un autre utilisateur (taxe 5%)")
    @app_commands.describe(
        utilisateur="L'utilisateur à qui donner des PrissBucks",
        montant="Le montant de PrissBucks à donner (avant taxe)"
    )
    async def give_slash(self, interaction: discord.Interaction, utilisateur: discord.Member, montant: int):
        """Slash command pour donner des PrissBucks"""
        # Créer un contexte fictif pour réutiliser la logique
        ctx = await self.bot.get_context(interaction)
        ctx.author = interaction.user
        
        # Vérifier le cooldown manuellement pour les slash commands
        bucket = self.give_cmd._buckets.get_bucket(interaction.user.id)
        if bucket and bucket.tokens == 0:
            retry_after = bucket.get_retry_after()
            embed = create_cooldown_embed("give", retry_after)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Appliquer le cooldown
        if bucket:
            bucket.update_rate_limit()
            
        await self._execute_give(interaction, utilisateur, montant, is_slash=True)

    async def _execute_give(self, ctx_or_interaction, member, amount, is_slash=False):
        """Logique commune pour give (prefix et slash) avec taxes"""
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

        # Calculer le montant avec taxe pour la vérification
        tax_amount = int(amount * TRANSFER_TAX_RATE)
        total_cost = amount  # Le montant demandé reste le coût total

        try:
            # Vérifier le solde du donneur (il doit avoir le montant total demandé)
            giver_balance = await self.db.get_balance(giver.id)
            if giver_balance < amount:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"**Montant à donner:** {amount:,} {Emojis.MONEY}\n"
                    f"**Ton solde:** {giver_balance:,} {Emojis.MONEY}\n"
                    f"**Manque:** {amount - giver_balance:,} {Emojis.MONEY}\n\n"
                    f"ℹ️ *Taxe de {TRANSFER_TAX_RATE*100}% incluse dans le calcul*"
                )
                await send_func(embed=embed)
                return

            # Effectuer le transfert avec taxe
            success, tax_info = await self.db.transfer_with_tax(
                giver.id, receiver.id, amount, TRANSFER_TAX_RATE, OWNER_ID
            )
            
            if success:
                new_balance = giver_balance - amount
                embed = self.create_transfer_embed_with_tax(giver, receiver, tax_info, new_balance)
                await send_func(embed=embed)
                
                # Log du transfert
                logger.info(f"Transfert avec taxe: {giver} → {receiver}, montant: {amount}, taxe: {tax_info['tax_amount']}")
            else:
                error_msg = tax_info.get("error", "Erreur inconnue lors du transfert")
                embed = create_error_embed("Échec du transfert", error_msg)
                await send_func(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur give {giver.id} -> {receiver.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du transfert.")
            await send_func(embed=embed)

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