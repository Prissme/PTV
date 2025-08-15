import discord
from discord.ext import commands
from discord import app_commands
import math
import logging
import json

from config import ITEMS_PER_PAGE, SHOP_TAX_RATE, OWNER_ID, Colors, Emojis, PREFIX
from utils.embeds import (
    create_shop_embed_with_tax, create_purchase_embed_with_tax, create_inventory_embed,
    create_error_embed, create_warning_embed, create_success_embed
)

logger = logging.getLogger(__name__)

class Shop(commands.Cog):
    """Système boutique complet : shop, buy, inventory avec taxes et items spéciaux"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        # Dictionnaire pour gérer les cooldowns manuellement des slash commands
        self.buy_cooldowns = {}
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Shop initialisé avec système de taxes et items spéciaux")
    
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

    def _calculate_price_with_tax(self, base_price: int) -> tuple:
        """Calcule le prix avec taxe et retourne (prix_total, taxe)"""
        tax_amount = int(base_price * SHOP_TAX_RATE)
        total_price = base_price + tax_amount
        return total_price, tax_amount

    async def _handle_special_item_effects(self, user, guild, item: dict, channel) -> tuple:
        """Gère les effets spéciaux des items après achat"""
        special_effect = None
        
        # Gestion de l'XP Boost - Envoie la commande à Arcane
        if item["type"] == "xp_boost":
            try:
                xp_amount = item["data"].get("xp_amount", 1000)
                
                # Envoyer la commande /xp add directement dans le canal
                if channel and channel.permissions_for(guild.me).send_messages:
                    try:
                        # Envoyer la commande pour Arcane Premium
                        xp_command = f"/xp add {user.mention} {xp_amount}"
                        await channel.send(xp_command)
                        
                        special_effect = f"⚡ **Commande XP envoyée !**\n🤖 `/xp add {user.mention} {xp_amount}` envoyé à Arcane Premium\n💫 Tes {xp_amount} XP arrivent !"
                        logger.info(f"XP Boost command sent: {xp_command} pour {user} dans {channel}")
                        
                    except discord.Forbidden:
                        special_effect = f"⚡ **XP Boost acheté !**\n⚠️ Pas de permission pour envoyer la commande XP dans ce canal"
                        logger.warning(f"Pas de permission pour envoyer commande XP dans {channel}")
                        
                    except Exception as e:
                        logger.error(f"Erreur envoi commande XP: {e}")
                        special_effect = f"⚡ **XP Boost acheté !**\n❌ Erreur lors de l'envoi de la commande XP"
                else:
                    special_effect = f"⚡ **XP Boost acheté !**\n⚠️ Impossible d'envoyer la commande XP (canal indisponible)"
                    
            except Exception as e:
                logger.error(f"Erreur traitement XP Boost {item['id']}: {e}")
                special_effect = f"⚡ **XP Boost acheté !**\n❌ Erreur lors du traitement"
        
        # Gestion du Reset Cooldowns
        elif item["type"] == "cooldown_reset":
            try:
                # Reset des cooldowns dans tous les cogs
                cooldowns_reset = []
                
                for cog_name, cog in self.bot.cogs.items():
                    try:
                        # Reset des cooldowns personnalisés
                        if hasattr(cog, 'cooldowns') and user.id in cog.cooldowns:
                            del cog.cooldowns[user.id]
                            cooldowns_reset.append(cog_name)
                        
                        # Reset des cooldowns Discord.py
                        for command in cog.get_commands():
                            if hasattr(command, '_buckets') and command._buckets:
                                bucket = command._buckets.get_bucket(type('MockMessage', (), {'author': user, 'guild': guild, 'channel': None})())
                                if bucket:
                                    bucket.reset()
                                    
                    except Exception as e:
                        logger.error(f"Erreur reset cooldown {cog_name}: {e}")
                        continue
                
                special_effect = f"⏰ **Tous tes cooldowns ont été supprimés !**\n✅ Tu peux maintenant utiliser toutes tes commandes !\n🔄 Cooldowns reset: {len(cooldowns_reset)} modules"
                logger.info(f"Reset cooldowns: {user} a reset tous ses cooldowns (item {item['id']})")
                
            except Exception as e:
                logger.error(f"Erreur reset cooldowns {item['id']}: {e}")
                special_effect = f"⏰ **Reset Cooldowns acheté !**\n❌ Erreur lors du reset"
        
        return special_effect

    # ==================== SHOP COMMANDS ====================

    @commands.command(name='shop', aliases=['boutique', 'store'])
    async def shop_cmd(self, ctx, page: int = 1):
        """Affiche la boutique avec pagination et prix avec taxes"""
        await self._execute_shop(ctx, page)

    @app_commands.command(name="shop", description="Affiche la boutique avec tous les items disponibles (prix avec taxes)")
    @app_commands.describe(page="Numéro de la page à afficher (optionnel)")
    async def shop_slash(self, interaction: discord.Interaction, page: int = 1):
        """Slash command pour afficher la boutique"""
        await interaction.response.defer()
        await self._execute_shop(interaction, page, is_slash=True)

    async def _execute_shop(self, ctx_or_interaction, page=1, is_slash=False):
        """Logique commune pour shop (prefix et slash)"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
            user = ctx_or_interaction.user
        else:
            send_func = ctx_or_interaction.send
            user = ctx_or_interaction.author

        try:
            items = await self.db.get_shop_items(active_only=True)
            
            if not items:
                embed = create_warning_embed(
                    "Boutique vide",
                    "La boutique est vide pour le moment. Revenez plus tard !"
                )
                await send_func(embed=embed)
                return
            
            # Ajouter le calcul des prix avec taxe pour chaque item
            for item in items:
                total_price, tax = self._calculate_price_with_tax(item['price'])
                item['total_price'] = total_price
                item['tax_amount'] = tax
            
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
            
            # Créer l'embed avec les prix taxés
            embed = create_shop_embed_with_tax(page_items, page, total_pages, SHOP_TAX_RATE)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shop: {e}")
            embed = create_error_embed("Erreur", f"Erreur lors de l'affichage de la boutique.")
            await send_func(embed=embed)

    # ==================== BUY COMMANDS AVEC TAXES ET EFFETS SPÉCIAUX ====================

    @commands.command(name='buy', aliases=['acheter', 'purchase'])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def buy_cmd(self, ctx, item_id: int):
        """Achète un item du shop (avec taxe de 5% et effets spéciaux)"""
        await self._execute_buy(ctx, item_id)

    @app_commands.command(name="buy", description="Achète un item de la boutique (avec taxe de 5% et effets spéciaux)")
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
        """Logique commune pour buy avec taxes et effets spéciaux (prefix et slash)"""
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
            
            # Effectuer l'achat avec taxe (transaction atomique)
            success, message, tax_info = await self.db.purchase_item_with_tax(
                user_id, item_id, SHOP_TAX_RATE, OWNER_ID
            )
            
            if not success:
                embed = create_error_embed("Achat échoué", message)
                await send_func(embed=embed)
                return
            
            # Variables pour les différents types d'items
            role_granted = False
            role_name = None
            special_effect = None
            
            # ==================== GESTION DES RÔLES ====================
            if item["type"] == "role":
                try:
                    role_id = item["data"].get("role_id")
                    if role_id:
                        role = guild.get_role(int(role_id))
                        if role:
                            # Vérifications de permissions
                            bot_member = guild.get_member(self.bot.user.id)
                            if not bot_member.guild_permissions.manage_roles:
                                embed = create_warning_embed(
                                    "Achat réussi mais...",
                                    f"L'item a été acheté mais le bot n'a pas la permission `Gérer les rôles`. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {tax_info['total_price']:,} PrissBucks"
                                )
                                await send_func(embed=embed)
                                return
                            
                            if role >= bot_member.top_role:
                                embed = create_warning_embed(
                                    "Achat réussi mais...",
                                    f"L'item a été acheté mais le rôle `{role.name}` est trop haut dans la hiérarchie. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {tax_info['total_price']:,} PrissBucks"
                                )
                                await send_func(embed=embed)
                                return
                            
                            await author.add_roles(role, reason=f"Achat boutique: {item['name']}")
                            role_granted = True
                            role_name = role.name
                            logger.info(f"Rôle {role.name} attribué à {author} (achat item {item_id})")
                        else:
                            embed = create_warning_embed(
                                "Achat réussi mais...",
                                f"L'item a été acheté mais le rôle est introuvable. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {tax_info['total_price']:,} PrissBucks"
                            )
                            await send_func(embed=embed)
                            return
                    else:
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
                        f"L'item a été acheté mais il y a eu une erreur lors de l'attribution du rôle. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {tax_info['total_price']:,} PrissBucks"
                    )
                    await send_func(embed=embed)
                    return
                except Exception as e:
                    logger.error(f"Erreur attribution rôle {item_id}: {e}")
                    embed = create_warning_embed(
                        "Achat réussi mais...",
                        f"L'item a été acheté mais il y a eu une erreur lors de l'attribution du rôle. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {tax_info['total_price']:,} PrissBucks"
                    )
                    await send_func(embed=embed)
                    return
            
            # ==================== GESTION DES ITEMS SPÉCIAUX ====================
            elif item["type"] in ["xp_boost", "cooldown_reset"]:
                # Pour XP Boost, on a besoin du canal pour envoyer la commande
                channel = None
                if item["type"] == "xp_boost":
                    if is_slash:
                        channel = ctx_or_interaction.channel
                    else:
                        channel = ctx_or_interaction.channel
                
                special_effect = await self._handle_special_item_effects(author, guild, item, channel)
            
            # Récupérer le nouveau solde
            new_balance = await self.db.get_balance(user_id)
            
            # Message de confirmation avec tous les effets
            embed = create_purchase_embed_with_tax(
                author, item, tax_info, new_balance, role_granted, role_name, special_effect
            )
            
            await send_func(embed=embed)
            
            # Log de l'action avec détails
            effect_log = ""
            if role_granted:
                effect_log += f" | Rôle: {role_name}"
            if special_effect:
                effect_log += f" | Effet: {item['type']}"
                
            logger.info(f"Achat avec effets: {author} a acheté {item['name']} (ID: {item_id}) | Total: {tax_info['total_price']} | Taxe: {tax_info['tax_amount']}{effect_log}")
            
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
        price="Prix de l'item en PrissBucks (sans taxe)",
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
            
            # Calculer le prix avec taxe pour l'affichage
            total_price, tax = self._calculate_price_with_tax(price)
            
            # Ajouter l'item à la base de données
            item_id = await self.db.add_shop_item(
                name=name,
                description=description,
                price=price,  # Prix de base (sans taxe)
                item_type="role",
                data=item_data
            )
            
            # Confirmation
            embed = create_success_embed(
                "Item ajouté !",
                f"**{name}** a été ajouté à la boutique avec succès !"
            )
            
            embed.add_field(name="💰 Prix de base", value=f"{price:,} PrissBucks", inline=True)
            embed.add_field(name="🏛️ Prix avec taxe", value=f"{total_price:,} PrissBucks", inline=True)
            embed.add_field(name="🎭 Rôle", value=role.mention, inline=True)
            embed.add_field(name="🆔 ID", value=f"`{item_id}`", inline=True)
            embed.add_field(name="📈 Taxe", value=f"{SHOP_TAX_RATE*100}% ({tax:,} PB)", inline=True)
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
            
            embed = discord.Embed(
                title="📊 Statistiques de la boutique",
                color=Colors.INFO
            )
            
            # Statistiques générales avec taxes
            embed.add_field(
                name="👥 Acheteurs uniques", 
                value=f"**{stats['unique_buyers']}** utilisateurs", 
                inline=True
            )
            embed.add_field(
                name="🛒 Total des achats", 
                value=f"**{stats['total_purchases']}** achats", 
                inline=True
            )
            embed.add_field(
                name="💰 Revenus totaux", 
                value=f"**{stats['total_revenue']:,}** PrissBucks", 
                inline=True
            )
            
            # Nouvelles statistiques sur les taxes
            embed.add_field(
                name="🏛️ Taxes collectées", 
                value=f"**{stats['total_taxes']:,}** PrissBucks", 
                inline=True
            )
            
            tax_percentage = (stats['total_taxes'] / stats['total_revenue'] * 100) if stats['total_revenue'] > 0 else 0
            embed.add_field(
                name="📈 Pourcentage taxes", 
                value=f"**{tax_percentage:.1f}%** du CA", 
                inline=True
            )
            
            # Top des items avec revenus et taxes
            if stats['top_items']:
                top_text = ""
                for i, item in enumerate(stats['top_items'][:5], 1):
                    emoji = ["🥇", "🥈", "🥉", "🏅", "🏅"][i-1]
                    top_text += f"{emoji} **{item['name']}** - {item['purchases']} vente(s) ({item['revenue']:,} PB)\n"
                
                embed.add_field(
                    name="🏆 Top des ventes",
                    value=top_text,
                    inline=False
                )
            
            embed.set_footer(text=f"Taux de taxe actuel: {SHOP_TAX_RATE*100}%")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shopstats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)

    # ==================== COMMANDES XP BOOST ADMIN ====================

    @commands.command(name='addxpitem')
    @commands.has_permissions(administrator=True)
    async def add_xp_item_cmd(self, ctx, price: int, xp_amount: int, *, name: str = None):
        """[ADMIN] Ajoute un item XP Boost à la boutique"""
        await self._execute_add_xp_item(ctx, price, xp_amount, name)

    @app_commands.command(name="addxpitem", description="[ADMIN] Ajoute un item XP Boost à la boutique")
    @app_commands.describe(
        price="Prix de l'item en PrissBucks (sans taxe)",
        xp_amount="Quantité d'XP à donner",
        name="Nom de l'item (optionnel, par défaut basé sur l'XP)"
    )
    @app_commands.default_permissions(administrator=True)
    async def add_xp_item_slash(self, interaction: discord.Interaction, price: int, xp_amount: int, name: str = None):
        """Slash command pour ajouter un XP Boost (admin seulement)"""
        if not interaction.user.guild_permissions.administrator:
            embed = create_error_embed(
                "Permission refusée", 
                "Seuls les administrateurs peuvent utiliser cette commande."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()
        await self._execute_add_xp_item(interaction, price, xp_amount, name, is_slash=True)

    async def _execute_add_xp_item(self, ctx_or_interaction, price, xp_amount, name=None, is_slash=False):
        """Logique commune pour add_xp_item (prefix et slash)"""
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

        if xp_amount <= 0:
            embed = create_error_embed("XP invalide", "La quantité d'XP doit être positive !")
            await send_func(embed=embed)
            return

        if price > 10000000:
            embed = create_error_embed("Prix trop élevé", "Le prix maximum est de 10,000,000 PrissBucks.")
            await send_func(embed=embed)
            return

        if xp_amount > 100000:
            embed = create_error_embed("XP trop élevé", "La quantité d'XP maximum est de 100,000.")
            await send_func(embed=embed)
            return

        try:
            # Nom par défaut si pas fourni
            if not name:
                name = f"⚡ XP Boost {xp_amount}"
            
            # Description automatique
            description = f"Gagne instantanément {xp_amount:,} XP via Arcane Premium ! Le bot enverra automatiquement la commande `/xp add` dans le canal. - Usage immédiat à l'achat"
            
            # Données de l'XP Boost
            item_data = {
                "instant_use": True,
                "effect": "send_xp_command",
                "xp_amount": xp_amount
            }
            
            # Calculer le prix avec taxe
            total_price, tax = self._calculate_price_with_tax(price)
            
            # Ajouter l'item à la base de données
            item_id = await self.db.add_shop_item(
                name=name,
                description=description,
                price=price,
                item_type="xp_boost",
                data=item_data
            )
            
            # Confirmation
            embed = create_success_embed(
                "XP Boost ajouté !",
                f"**{name}** a été ajouté à la boutique avec succès !"
            )
            
            embed.add_field(name="💰 Prix de base", value=f"{price:,} PrissBucks", inline=True)
            embed.add_field(name="🏛️ Prix avec taxe", value=f"{total_price:,} PrissBucks", inline=True)
            embed.add_field(name="⚡ XP donné", value=f"{xp_amount:,} XP", inline=True)
            embed.add_field(name="🆔 ID", value=f"`{item_id}`", inline=True)
            embed.add_field(name="📈 Taxe", value=f"{SHOP_TAX_RATE*100}% ({tax:,} PB)", inline=True)
            embed.add_field(name="🎯 Type", value="XP Boost instantané", inline=True)
            embed.add_field(name="📝 Description", value=description, inline=False)
            
            embed.set_footer(text=f"Ajouté par {admin.display_name}")
            await send_func(embed=embed)
            
            logger.info(f"ADMIN: {admin} a ajouté l'XP Boost '{name}' (ID: {item_id}, Prix: {price}, XP: {xp_amount})")
            
        except Exception as e:
            logger.error(f"Erreur add_xp_item: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'ajout de l'XP Boost.")
            await send_func(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Shop(bot))