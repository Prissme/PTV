import discord
from discord.ext import commands
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

def create_taxed_transfer_embed(giver: discord.Member, receiver: discord.Member, transfer_data: dict, new_balance: int) -> discord.Embed:
    """Créer un embed pour les transferts avec taxe"""
    embed = discord.Embed(
        title=f"{Emojis.TRANSFER} Transfert réussi !",
        description=f"**{giver.display_name}** a donné **{transfer_data['gross_amount']:,}** PrissBucks à **{receiver.display_name}**",
        color=Colors.SUCCESS
    )
    
    embed.add_field(
        name="💰 Détail du transfert",
        value=f"**Montant demandé :** {transfer_data['gross_amount']:,} PrissBucks\n"
              f"**Reçu par {receiver.display_name} :** {transfer_data['net_amount']:,} PrissBucks\n"
              f"**{Emojis.TAX} Taxe ({transfer_data['tax_rate']:.0f}%) :** {transfer_data['tax_amount']:,} PrissBucks",
        inline=False
    )
    
    embed.add_field(
        name="📊 Nouveau solde",
        value=f"**{giver.display_name} :** {new_balance:,} PrissBucks",
        inline=True
    )
    
    if transfer_data['tax_amount'] > 0:
        embed.set_footer(text=f"La taxe de {transfer_data['tax_amount']:,} PrissBucks est collectée par le bot")
    else:
        embed.set_footer(text="Aucune taxe appliquée sur ce montant")
    
    return embed

class Economy(commands.Cog):
    """Commandes économie essentielles : balance, daily, give (avec taxe)"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info(f"✅ Cog Economy initialisé avec taxe {TRANSFER_TAX_RATE*100:.0f}% sur les transferts")
    
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
        """Donne des pièces à un autre utilisateur (avec taxe de 2%)"""
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

            # Effectuer le transfert avec taxe
            success, result = await self.db.transfer_with_tax(
                giver.id, receiver.id, amount, TRANSFER_TAX_RATE, OWNER_ID
            )
            
            if success:
                new_balance = giver_balance - amount
                embed = create_taxed_transfer_embed(giver, receiver, result, new_balance)
                await ctx.send(embed=embed)
                
                # Log pour l'owner si une taxe a été collectée
                if result['tax_amount'] > 0:
                    logger.info(f"💰 Taxe collectée: {result['tax_amount']} PrissBucks de {giver} -> Owner ({result['gross_amount']} transfert)")
            else:
                embed = create_error_embed("Échec du transfert", result.get('error', 'Erreur inconnue'))
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

    @commands.command(name='taxinfo', aliases=['infoaxe', 'transfertax'])
    async def tax_info_cmd(self, ctx):
        """Affiche les informations sur la taxe de transfert"""
        tax_percentage = TRANSFER_TAX_RATE * 100
        
        embed = discord.Embed(
            title=f"{Emojis.TAX} Système de Taxe",
            description=f"Une taxe de **{tax_percentage:.0f}%** est appliquée sur tous les transferts.",
            color=Colors.INFO
        )
        
        embed.add_field(
            name="💡 Comment ça marche ?",
            value=f"• Tu donnes **1000** PrissBucks à quelqu'un\n"
                  f"• Il reçoit **{int(1000 * (1 - TRANSFER_TAX_RATE)):,}** PrissBucks\n"
                  f"• Le bot collecte **{int(1000 * TRANSFER_TAX_RATE):,}** PrissBucks de taxe",
            inline=False
        )
        
        embed.add_field(
            name="📊 Exemples",
            value=f"**100** PrissBucks → {int(100 * (1 - TRANSFER_TAX_RATE))} reçus, {int(100 * TRANSFER_TAX_RATE)} de taxe\n"
                  f"**500** PrissBucks → {int(500 * (1 - TRANSFER_TAX_RATE))} reçus, {int(500 * TRANSFER_TAX_RATE)} de taxe\n"
                  f"**1000** PrissBucks → {int(1000 * (1 - TRANSFER_TAX_RATE))} reçus, {int(1000 * TRANSFER_TAX_RATE)} de taxe",
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Important",
            value="• La taxe est automatiquement déduite du montant\n"
                  f"• Les taxes financent le fonctionnement du bot\n"
                  f"• Aucune taxe sur les autres commandes (daily, shop, etc.)",
            inline=False
        )
        
        embed.set_footer(text=f"Taux de taxe actuel: {tax_percentage:.0f}%")
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