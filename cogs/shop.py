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
        # Dictionnaire pour gérer les cooldowns manuellement des slash commands
        self.buy_cooldowns = {}
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Shop initialisé (simplifié) avec slash commands")
    
    def _check_buy_cooldown(self, user_id: int) -> float:
        """Vérifie et retourne le cooldown restant pour buy"""
        import time
        now = time.time()
        cooldown_duration = 3  # 3 secondes de cooldown
        if user_id in self.buy_cooldowns:
            elapsed = now - self.buy_cooldowns[user_id]
            if elapsed < cooldown_duration:
                return cooldown_duration - elapsed
        self.buy_cooldowns[user_id] = now
        return 0

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
        cooldown_remaining = self._check_buy_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            embed = discord.Embed(
                title=f"{Emojis.COOLDOWN} Cooldown actif !",
                description=f"Tu pourras acheter un autre item dans **{cooldown_remaining:.1f}** secondes.",
                color=Colors.WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
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
            
            # Si c'est un rôle, l'attribuer
            role_granted = False
            role_name = None
            
            if item["type"] == "role":
                try:
                    role_id = item["data"].get("role_id")
                    if role_id:
                        role = guild.get_role(int(role_id))
                        if role:
                            await author.add_roles(role, reason=f"Achat boutique: {item['name']}")
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
                        embed = create_warning_embed(
                            "Configuration invalide",
                            f"L'item {item['name']} n'a pas de rôle configuré correctement. Contacte un administrateur."
                        )
                        await send_func(embed=embed)
                        return
                        
                except discord.HTTPException as e:
                    logger.error(f"Erreur Discord lors de l'attribution du rôle {item_id}: {e}")
                    embed = create_warning_embed(
                        "Achat réussi mais...",
                        f"L'item a été acheté mais il y a eu une erreur lors de l'attribution du rôle (permissions insuffisantes ?). Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {item['price']:,} PrissBucks"
                    )
                    await send_func(embed=embed)
                    return
                except Exception as e:
                    logger.error(f"Erreur attribution rôle {item_id}: {e}")
                    embed = create_warning_embed(
                        "Achat réussi mais...",
                        f"L'item a été acheté mais il y a eu une erreur lors de l'attribution du rôle. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {item['price']:,} PrissBucks"
                    )
                    await send_func(embed=embed)
                    return
            
            # Récupérer le nouveau solde
            new_balance = await self.db.get_balance(user_id)
            
            # Message de confirmation
            embed = create_purchase_embed(author, item, new_balance, role_granted, role_name)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur buy {user_id} -> {item_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'achat.")
            await send_func(embed=embed)

    @commands.command(name='inventory', aliases=['inv', 'inventaire'])
    async def inventory_cmd(self, ctx, member: discord.Member = None):
        """Affiche l'inventaire d'un utilisateur"""
        await self._execute_inventory(ctx, member)

    @app_commands.command(name="inventory", description="Affiche l'inventaire d'un utilisateur")
    @app_commands.describe(utilisateur="L'utilisateur dont voir l'inventaire (optionnel)")
    async def inventory_slash(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        """Slash command pour voir l'inventaire"""
        await interaction.response.defer()
        await self._execute_inventory(interaction, utilisateur, is_slash=True)

    async def _execute_inventory(self, ctx_or_interaction, member=None, is_slash=False):
        """Logique commune pour inventory (prefix et slash)"""
        if is_slash:
            target = member or ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            target = member or ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        try:
            purchases = await self.db.get_user_purchases(target.id)
            
            from utils.embeds import create_inventory_embed
            embed = create_inventory_embed(target, purchases)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur inventory pour {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération de l'inventaire.")
            await send_func(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Shop(bot))