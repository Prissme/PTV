import discord
from discord.ext import commands
from discord import app_commands
import math
import logging

from config import ITEMS_PER_PAGE, Colors, Emojis
from utils.embeds import (
    create_shop_embed, create_purchase_embed, create_inventory_embed,
    create_error_embed, create_warning_embed, create_success_embed
)

logger = logging.getLogger(__name__)

class Shop(commands.Cog):
    """Système boutique complet : shop, buy, inventory"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        # Dictionnaire pour gérer les cooldowns manuellement des slash commands
        self.buy_cooldowns = {}
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Shop initialisé avec slash commands complets")
    
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

    # ==================== SHOP COMMANDS ====================

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

    # ==================== BUY COMMANDS ====================

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

    # ==================== INVENTORY COMMANDS ====================

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
            embed = create_inventory_embed(target, purchases)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur inventory pour {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération de l'inventaire.")
            await send_func(embed=embed)

    # ==================== ADMIN COMMANDS ====================

    @commands.command(name='additem')
    @commands.has_permissions(administrator=True)
    async def add_item_cmd(self, ctx, price: int, role: discord.Role, *, name: str):
        """[ADMIN] Ajoute un rôle à la boutique"""
        await self._execute_add_item(ctx, price, role, name)

    @app_commands.command(name="additem", description="[ADMIN] Ajoute un rôle à la boutique")
    @app_commands.describe(
        price="Prix de l'item en PrissBucks",
        role="Le rôle à attribuer",
        name="Nom de l'item dans la boutique",
        description="Description de l'item (optionnel)"
    )
    @app_commands.default_permissions(administrator=True)
    async def add_item_slash(self, interaction: discord.Interaction, price: int, role: discord.Role, name: str, description: str = None):
        """Slash command pour ajouter un item (admin seulement)"""
        if not interaction.user.guild_permissions.administrator:
            embed = create_error_embed(
                "Permission refusée", 
                "Seuls les administrateurs peuvent utiliser cette commande."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()
        await self._execute_add_item(interaction, price, role, name, description, is_slash=True)

    async def _execute_add_item(self, ctx_or_interaction, price, role, name, description=None, is_slash=False):
        """Logique commune pour add_item (prefix et slash)"""
        if is_slash:
            admin = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            admin = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        # Validations
        if price <= 0:
            embed = create_error_embed("Prix invalide", "Le prix doit être positif !")
            await send_func(embed=embed)
            return

        if price > 10000000:
            embed = create_error_embed("Prix trop élevé", "Le prix maximum est de 10,000,000 PrissBucks.")
            await send_func(embed=embed)
            return

        if len(name) > 100:
            embed = create_error_embed("Nom trop long", "Le nom ne peut pas dépasser 100 caractères.")
            await send_func(embed=embed)
            return

        try:
            # Créer la description par défaut si pas fournie
            if not description:
                description = f"Rôle {role.name} - Débloque des avantages exclusifs !"
            
            # Données du rôle
            item_data = {
                "role_id": role.id
            }
            
            # Ajouter l'item à la base de données
            item_id = await self.db.add_shop_item(
                name=name,
                description=description,
                price=price,
                item_type="role",
                data=item_data
            )
            
            # Confirmation
            embed = create_success_embed(
                "Item ajouté !",
                f"**{name}** a été ajouté à la boutique avec succès !"
            )
            
            embed.add_field(name="💰 Prix", value=f"{price:,} PrissBucks", inline=True)
            embed.add_field(name="🎭 Rôle", value=role.mention, inline=True)
            embed.add_field(name="🆔 ID", value=f"`{item_id}`", inline=True)
            embed.add_field(name="📝 Description", value=description, inline=False)
            
            embed.set_footer(text=f"Ajouté par {admin.display_name}")
            await send_func(embed=embed)
            
            logger.info(f"ADMIN: {admin} a ajouté l'item '{name}' (ID: {item_id}, Prix: {price}, Rôle: {role.name})")
            
        except Exception as e:
            logger.error(f"Erreur add_item: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'ajout de l'item.")
            await send_func(embed=embed)

    @commands.command(name='removeitem')
    @commands.has_permissions(administrator=True)
    async def remove_item_cmd(self, ctx, item_id: int):
        """[ADMIN] Désactive un item de la boutique"""
        await self._execute_remove_item(ctx, item_id)

    @app_commands.command(name="removeitem", description="[ADMIN] Désactive un item de la boutique")
    @app_commands.describe(item_id="L'ID de l'item à désactiver")
    @app_commands.default_permissions(administrator=True)
    async def remove_item_slash(self, interaction: discord.Interaction, item_id: int):
        """Slash command pour désactiver un item (admin seulement)"""
        if not interaction.user.guild_permissions.administrator:
            embed = create_error_embed(
                "Permission refusée", 
                "Seuls les administrateurs peuvent utiliser cette commande."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()
        await self._execute_remove_item(interaction, item_id, is_slash=True)

    async def _execute_remove_item(self, ctx_or_interaction, item_id, is_slash=False):
        """Logique commune pour remove_item (prefix et slash)"""
        if is_slash:
            admin = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            admin = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        try:
            # Vérifier que l'item existe
            item = await self.db.get_shop_item(item_id)
            if not item:
                embed = create_error_embed("Item introuvable", f"Aucun item trouvé avec l'ID `{item_id}`.")
                await send_func(embed=embed)
                return

            # Désactiver l'item
            success = await self.db.deactivate_shop_item(item_id)
            
            if success:
                embed = create_success_embed(
                    "Item désactivé !",
                    f"L'item **{item['name']}** (ID: `{item_id}`) a été désactivé avec succès."
                )
                embed.set_footer(text=f"Désactivé par {admin.display_name}")
                await send_func(embed=embed)
                
                logger.info(f"ADMIN: {admin} a désactivé l'item '{item['name']}' (ID: {item_id})")
            else:
                embed = create_error_embed("Erreur", "Impossible de désactiver cet item.")
                await send_func(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur remove_item: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la désactivation de l'item.")
            await send_func(embed=embed)

    @commands.command(name='shopstats')
    @commands.has_permissions(administrator=True)
    async def shop_stats_cmd(self, ctx):
        """[ADMIN] Affiche les statistiques de la boutique"""
        try:
            stats = await self.db.get_shop_stats()
            
            from utils.embeds import create_shop_stats_embed
            embed = create_shop_stats_embed(stats)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shopstats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Shop(bot))