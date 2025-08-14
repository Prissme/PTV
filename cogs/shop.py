import discord
from discord.ext import commands
from discord import app_commands
import math
import logging

from config import ITEMS_PER_PAGE, Colors, Emojis
from utils.embeds import (
    create_shop_embed, create_purchase_embed,
    create_error_embed, create_warning_embed
)

logger = logging.getLogger(__name__)

class Shop(commands.Cog):
    """Système boutique essentiel : shop, buy"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Shop initialisé (simplifié) avec slash commands")

    @commands.command(name='shop', aliases=['boutique', 'store'])
    async def shop_cmd(self, ctx, page: int = 1):
        """Affiche la boutique avec pagination"""
        await self._execute_shop(ctx, page)

    @app_commands.command(name="shop", description="Affiche la boutique avec tous les items disponibles")
    @app_commands.describe(page="Numéro de la page à afficher (optionnel)")
    async def shop_slash(self, interaction: discord.Interaction, page: int = 1):
        """Slash command pour afficher la boutique"""
        await interaction.response.defer()
        await self._execute_shop(interaction, page, is_slash=True)

    async def _execute_shop(self, ctx_or_interaction, page=1, is_slash=False):
        """Logique commune pour shop (prefix et slash)"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
        else:
            send_func = ctx_or_interaction.send

        try:
            items = await self.db.get_shop_items(active_only=True)
            
            if not items:
                embed = create_warning_embed(
                    "Boutique vide",
                    "La boutique est vide pour le moment. Revenez plus tard !"
                )
                await send_func(embed=embed)
                return
            
            # Pagination
            total_pages = math.ceil(len(items) / ITEMS_PER_PAGE)
            
            if page < 1 or page > total_pages:
                embed = create_error_embed(
                    "Page invalide",
                    f"Utilise une page entre 1 et {total_pages}."
                )
                await send_func(embed=embed)
                return
            
            # Récupérer les items de la page
            start_idx = (page - 1) * ITEMS_PER_PAGE
            end_idx = start_idx + ITEMS_PER_PAGE
            page_items = items[start_idx:end_idx]
            
            # Créer l'embed
            embed = create_shop_embed(page_items, page, total_pages)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shop: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage de la boutique.")
            await send_func(embed=embed)

    @commands.command(name='buy', aliases=['acheter', 'purchase'])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def buy_cmd(self, ctx, item_id: int):
        """Achète un item du shop"""
        await self._execute_buy(ctx, item_id)

    @app_commands.command(name="buy", description="Achète un item de la boutique")
    @app_commands.describe(item_id="L'ID de l'item à acheter (visible dans /shop)")
    async def buy_slash(self, interaction: discord.Interaction, item_id: int):
        """Slash command pour acheter un item"""
        # Vérifier le cooldown manuellement pour les slash commands
        bucket = self.buy_cmd._buckets.get_bucket(interaction.user.id)
        if bucket and bucket.tokens == 0:
            retry_after = bucket.get_retry_after()
            embed = discord.Embed(
                title=f"{Emojis.COOLDOWN} Cooldown actif !",
                description=f"Tu pourras acheter un autre item dans **{retry_after:.1f}** secondes.",
                color=Colors.WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        
        # Appliquer le cooldown
        if bucket:
            bucket.update_rate_limit()
            
        await self._execute_buy(interaction, item_id, is_slash=True)

    async def _execute_buy(self, ctx_or_interaction, item_id, is_slash=False):
        """Logique commune pour buy (prefix et slash)"""
        if is_slash:
            user_id = ctx_or_interaction.user.id
            author = ctx_or_interaction.user
            guild = ctx_or_interaction.guild
            send_func = ctx_or_interaction.followup.send
        else:
            user_id = ctx_or_interaction.author.id
            author = ctx_or_interaction.author
            guild = ctx_or_interaction.guild
            send_func = ctx_or_interaction.send
        
        try:
            # Récupérer les infos de l'item
            item = await self.db.get_shop_item(item_id)
            if not item or not item["is_active"]:
                embed = create_error_embed(
                    "Item introuvable",
                    "Cet item n'existe pas ou n'est plus disponible."
                )
                await send_func(embed=embed)
                return
            
            # Effectuer l'achat (transaction atomique)
            success, message = await self.db.purchase_item(user_id, item_id)
            
            if not success:
                embed = create_error_embed("Achat échoué", message)
                await send_func(embed=embed)
                return
            
            # Variables pour le résultat
            role_granted = False
            role_name = None
            special_effect = None
            
            # Si c'est un rôle, l'attribuer
            if item["type"] == "role":
                try:
                    role_id = item["data"].get("role_id")
                    if role_id:
                        role = guild.get_role(int(role_id))
                        if role:
                            await author.add_roles(role)
                            role_granted = True
                            role_name = role.name
                            logger.info(f"Rôle {role.name} attribué à {author} (achat item {item_id})")
                        else:
                            embed = create_warning_embed(
                                "Achat réussi mais...",
                                f"L'item a été acheté mais le rôle est introuvable. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {item['price']:,} PrissBucks"
                            )
                            await send_func(embed=embed)
                            logger.error(f"Rôle {role_id} introuvable pour l'item {item_id}")
                            return
                    else:
                        logger.error(f"Pas de role_id dans les données de l'item {item_id}")
                        
                except Exception as e:
                    logger.error(f"Erreur attribution rôle {item_id}: {e}")
                    embed = create_warning_embed(
                        "Achat réussi mais...",
                        f"L'item a été acheté mais il y a eu une erreur lors de l'attribution du rôle. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {item['price']:,} PrissBucks"
                    )
                    await send_func(embed=embed)
                    return
            
            # Si c'est un reset de cooldowns, l'appliquer immédiatement
            elif item["type"] == "cooldown_reset":
                try:
                    # Utiliser le cog SpecialItems s'il existe
                    special_items_cog = self.bot.get_cog('SpecialItems')
                    if special_items_cog:
                        cooldowns_cleared = await special_items_cog.reset_user_cooldowns(user_id)
                    else:
                        cooldowns_cleared = await self._reset_user_cooldowns(user_id)
                    
                    special_effect = f"🔄 **{cooldowns_cleared}** cooldown(s) désactivé(s) !"
                    logger.info(f"Reset cooldowns pour {author}: {cooldowns_cleared} cooldown(s) supprimés")
                except Exception as e:
                    logger.error(f"Erreur reset cooldowns {user_id}: {e}")
                    special_effect = "⚠️ Erreur lors du reset des cooldowns"
            
            # Récupérer le nouveau solde
            new_balance = await self.db.get_balance(user_id)
            
            # Message de confirmation
            embed = create_purchase_embed(author, item, new_balance, role_granted, role_name, special_effect)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur buy {user_id} -> {item_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'achat.")
            await send_func(embed=embed)

    async def _reset_user_cooldowns(self, user_id: int) -> int:
        """Reset tous les cooldowns d'un utilisateur (méthode de fallback)"""
        cooldowns_cleared = 0
        
        # Parcourir tous les cogs chargés et chercher des cooldowns
        for cog_name, cog in self.bot.cogs.items():
            try:
                # Reset cooldowns dans MessageRewards, Steal, etc.
                if hasattr(cog, 'cooldowns') and hasattr(cog, 'COOLDOWN_SECONDS'):
                    if user_id in cog.cooldowns:
                        del cog.cooldowns[user_id]
                        cooldowns_cleared += 1
                        logger.debug(f"Cooldown {cog_name} supprimé pour {user_id}")
                
                # Reset cooldowns Discord.py (daily, give, etc.)
                for command in cog.get_commands():
                    if hasattr(command, '_buckets') and command._buckets:
                        bucket = command._buckets.get_bucket(user_id)
                        if bucket and bucket.tokens == 0:
                            bucket._tokens = bucket._per
                            bucket._window = 0.0
                            cooldowns_cleared += 1
                            logger.debug(f"Cooldown Discord.py {command.name} supprimé pour {user_id}")
                            
            except Exception as e:
                logger.error(f"Erreur reset cooldown cog {cog_name}: {e}")
                continue
        
        return cooldowns_cleared

    @commands.command(name='inventory', aliases=['inv'])
    async def inventory_cmd(self, ctx, user: discord.Member = None):
        """Affiche l'inventaire d'un utilisateur"""
        target = user or ctx.author
        
        try:
            purchases = await self.db.get_user_purchases(target.id)
            
            from utils.embeds import create_inventory_embed
            embed = create_inventory_embed(target, purchases)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur inventory {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage de l'inventaire.")
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Shop(bot))
