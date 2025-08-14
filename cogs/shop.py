import discord
from discord.ext import commands
from discord import app_commands
import math
import logging
import json

from config import ITEMS_PER_PAGE, SHOP_TAX_RATE, OWNER_ID, Colors, Emojis, PREFIX
from utils.embeds import (
    create_shop_embed_with_tax, create_purchase_embed_with_tax, create_inventory_embed,
    create_error_embed, create_warning_embed, create_success_embed,
    create_special_item_effect_embed
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

    # ==================== NOUVELLES COMMANDES DE DIAGNOSTIC ====================

    @commands.command(name='shopdiag')
    @commands.is_owner()
    async def shop_diagnostic(self, ctx):
        """[OWNER] Diagnostic complet du shop"""
        try:
            # Test de connexion DB
            if not self.db or not self.db.pool:
                await ctx.send("❌ **Base de données non connectée !**")
                return
            
            # Récupérer TOUS les items (actifs et inactifs)
            async with self.db.pool.acquire() as conn:
                all_items = await conn.fetch("""
                    SELECT id, name, description, price, type, data, is_active, created_at 
                    FROM shop_items 
                    ORDER BY created_at DESC
                """)
            
            embed = discord.Embed(
                title="🔍 Diagnostic Shop",
                color=Colors.INFO
            )
            
            if not all_items:
                embed.description = "❌ **Aucun item trouvé dans la base de données !**"
                embed.add_field(
                    name="🔧 Solution",
                    value="Utilisez la commande `setupcooldownreset` pour créer l'item.",
                    inline=False
                )
            else:
                active_items = [item for item in all_items if item['is_active']]
                inactive_items = [item for item in all_items if not item['is_active']]
                cooldown_items = [item for item in all_items if item['type'] == 'cooldown_reset']
                
                embed.description = f"📊 **{len(all_items)} item(s) total dans la DB**"
                
                embed.add_field(
                    name="✅ Items actifs",
                    value=f"{len(active_items)} item(s)",
                    inline=True
                )
                
                embed.add_field(
                    name="❌ Items inactifs",
                    value=f"{len(inactive_items)} item(s)",
                    inline=True
                )
                
                embed.add_field(
                    name="⏰ Items Reset Cooldowns",
                    value=f"{len(cooldown_items)} item(s)",
                    inline=True
                )
                
                # Détails des items actifs
                if active_items:
                    items_list = ""
                    for item in active_items[:5]:  # Limiter à 5
                        items_list += f"• **{item['name']}** (ID: {item['id']}, Type: {item['type']})\n"
                    if len(active_items) > 5:
                        items_list += f"• ... et {len(active_items) - 5} autre(s)\n"
                    
                    embed.add_field(
                        name="📋 Items actifs détaillés",
                        value=items_list or "Aucun",
                        inline=False
                    )
                
                # Détails des items cooldown_reset
                if cooldown_items:
                    cooldown_list = ""
                    for item in cooldown_items:
                        status = "✅" if item['is_active'] else "❌"
                        cooldown_list += f"{status} **{item['name']}** (ID: {item['id']}, Prix: {item['price']} PB)\n"
                    
                    embed.add_field(
                        name="⏰ Items Reset Cooldowns détaillés",
                        value=cooldown_list,
                        inline=False
                    )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur diagnostic shop: {e}")
            embed = create_error_embed("Erreur Diagnostic", f"```{str(e)}```")
            await ctx.send(embed=embed)

    @commands.command(name='setupcooldownreset')
    @commands.is_owner()
    async def setup_cooldown_reset(self, ctx):
        """[OWNER] Ajoute l'item Reset Cooldowns au shop"""
        try:
            # Vérifier si un item cooldown_reset existe déjà
            async with self.db.pool.acquire() as conn:
                existing = await conn.fetchrow("""
                    SELECT id, name, is_active FROM shop_items 
                    WHERE type = 'cooldown_reset'
                    ORDER BY created_at DESC
                    LIMIT 1
                """)
            
            if existing and existing['is_active']:
                embed = create_warning_embed(
                    "Item déjà présent",
                    f"Un item Reset Cooldowns actif existe déjà (ID: {existing['id']})\n"
                    f"Nom: **{existing['name']}**"
                )
                await ctx.send(embed=embed)
                return
            
            # Données de l'item
            item_data = {
                "instant_use": True,
                "effect": "reset_all_cooldowns",
                "description": "Remet à zéro tous les cooldowns du joueur immédiatement après l'achat"
            }
            
            # Ajouter l'item
            item_id = await self.db.add_shop_item(
                name="⏰ Reset Cooldowns",
                description="Désactive instantanément TOUS tes cooldowns en cours ! (Daily, Vol, Give, etc.) - Usage immédiat à l'achat",
                price=200,
                item_type="cooldown_reset",
                data=item_data
            )
            
            embed = create_success_embed(
                "✅ Item Reset Cooldowns créé !",
                f"L'item a été ajouté avec succès dans la boutique."
            )
            
            embed.add_field(
                name="📋 Détails",
                value=f"**ID:** {item_id}\n"
                      f"**Nom:** ⏰ Reset Cooldowns\n"
                      f"**Prix:** 200 PrissBucks (base)\n"
                      f"**Prix avec taxe:** {200 + int(200 * SHOP_TAX_RATE)} PrissBucks\n"
                      f"**Type:** cooldown_reset",
                inline=False
            )
            
            embed.add_field(
                name="🔧 Test",
                value=f"Utilisez `{PREFIX}shop` pour vérifier que l'item apparaît !",
                inline=False
            )
            
            await ctx.send(embed=embed)
            logger.info(f"Item Reset Cooldowns créé par {ctx.author} (ID: {item_id})")
            
        except Exception as e:
            logger.error(f"Erreur setup cooldown reset: {e}")
            embed = create_error_embed("Erreur", f"Impossible de créer l'item:\n```{str(e)}```")
            await ctx.send(embed=embed)

    @commands.command(name='fixcooldownreset')
    @commands.is_owner()
    async def fix_cooldown_reset(self, ctx):
        """[OWNER] Active/réactive l'item Reset Cooldowns"""
        try:
            async with self.db.pool.acquire() as conn:
                # Chercher tous les items cooldown_reset
                items = await conn.fetch("""
                    SELECT id, name, is_active FROM shop_items 
                    WHERE type = 'cooldown_reset'
                    ORDER BY created_at DESC
                """)
                
                if not items:
                    await ctx.send("❌ **Aucun item Reset Cooldowns trouvé !**\nUtilisez `setupcooldownreset` pour le créer.")
                    return
                
                # Activer le plus récent et désactiver les autres
                latest_item = items[0]
                
                # Désactiver tous les anciens
                await conn.execute("""
                    UPDATE shop_items SET is_active = FALSE 
                    WHERE type = 'cooldown_reset'
                """)
                
                # Activer le plus récent
                await conn.execute("""
                    UPDATE shop_items SET is_active = TRUE 
                    WHERE id = $1
                """, latest_item['id'])
                
                embed = create_success_embed(
                    "✅ Item Reset Cooldowns activé !",
                    f"L'item **{latest_item['name']}** (ID: {latest_item['id']}) est maintenant actif."
                )
                
                if len(items) > 1:
                    embed.add_field(
                        name="🧹 Nettoyage",
                        value=f"{len(items) - 1} ancien(s) item(s) désactivé(s)",
                        inline=False
                    )
                
                await ctx.send(embed=embed)
                logger.info(f"Item cooldown_reset activé: ID {latest_item['id']}")
                
        except Exception as e:
            logger.error(f"Erreur fix cooldown reset: {e}")
            embed = create_error_embed("Erreur", f"```{str(e)}```")
            await ctx.send(embed=embed)

    @commands.command(name='debugshop')
    @commands.is_owner()
    async def debug_shop(self, ctx):
        """[OWNER] Debug rapide du shop"""
        try:
            items = await self.db.get_shop_items(active_only=True)
            await ctx.send(f"🔍 **Items actifs trouvés:** {len(items)}")
            
            for item in items[:5]:  # Premiers 5 items
                await ctx.send(f"• ID: {item['id']}, Nom: **{item['name']}**, Type: {item['type']}, Prix: {item['price']} PB")
            
            if len(items) > 5:
                await ctx.send(f"... et {len(items) - 5} autre(s) item(s)")
                
        except Exception as e:
            await ctx.send(f"❌ **Erreur:** ```{str(e)}```")

    # ==================== SHOP COMMANDS AVEC DEBUG ====================

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
        """Logique commune pour shop (prefix et slash) - avec debug logs"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
            user = ctx_or_interaction.user
        else:
            send_func = ctx_or_interaction.send
            user = ctx_or_interaction.author

        try:
            # LOG DEBUG
            logger.info(f"🔍 SHOP DEBUG: Utilisateur {user} demande le shop (page {page})")
            logger.info(f"🔍 SHOP DEBUG: DB connectée: {self.db and self.db.pool is not None}")
            
            items = await self.db.get_shop_items(active_only=True)
            logger.info(f"🔍 SHOP DEBUG: {len(items)} items récupérés")
            
            # Log chaque item
            for i, item in enumerate(items):
                logger.info(f"🔍 SHOP DEBUG: Item {i+1} - ID:{item['id']}, nom:'{item['name']}', type:'{item['type']}', prix:{item['price']}")
            
            if not items:
                logger.warning(f"🔍 SHOP DEBUG: Aucun item trouvé ! Envoi du message d'erreur...")
                embed = create_warning_embed(
                    "Boutique vide",
                    "La boutique est vide pour le moment. Revenez plus tard !\n\n**🔧 Admin:** Utilisez `e!shopdiag` pour diagnostiquer"
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
            
            logger.info(f"🔍 SHOP DEBUG: Affichage de {len(page_items)} items (page {page}/{total_pages})")
            
            # Créer l'embed avec les prix taxés
            embed = create_shop_embed_with_tax(page_items, page, total_pages, SHOP_TAX_RATE)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"❌ SHOP DEBUG: Erreur shop: {e}")
            import traceback
            traceback.print_exc()
            embed = create_error_embed("Erreur", f"Erreur lors de l'affichage de la boutique.")
            await send_func(embed=embed)

    # ==================== BUY COMMANDS AVEC TAXES ====================

    @commands.command(name='buy', aliases=['acheter', 'purchase'])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def buy_cmd(self, ctx, item_id: int):
        """Achète un item du shop (avec taxe de 5%)"""
        await self._execute_buy(ctx, item_id)

    @app_commands.command(name="buy", description="Achète un item de la boutique (avec taxe de 5%)")
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
        """Logique commune pour buy avec taxes et items spéciaux (prefix et slash)"""
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
            
            # === TRAITEMENT DES EFFETS SPÉCIAUX ===
            special_effect_message = None
            cooldowns_cleared = 0
            
            # 1. ITEM COOLDOWN RESET
            if item["type"] == "cooldown_reset":
                # Déclencher l'effet de reset des cooldowns
                special_items_cog = self.bot.get_cog('SpecialItems')
                if special_items_cog:
                    cooldowns_cleared = await special_items_cog.reset_user_cooldowns(user_id)
                    special_effect_message = f"🔄 **{cooldowns_cleared}** cooldown(s) supprimé(s) !"
                    logger.info(f"Cooldown reset: {author} - {cooldowns_cleared} cooldowns supprimés")
                else:
                    logger.error(f"SpecialItems cog non trouvé pour l'effet cooldown_reset")
            
            # Si c'est un rôle, l'attribuer
            role_granted = False
            role_name = None
            
            if item["type"] == "role":
                try:
                    role_id = item["data"].get("role_id")
                    if role_id:
                        role = guild.get_role(int(role_id))
                        if role:
                            # Vérifier que le bot a les permissions
                            bot_member = guild.get_member(self.bot.user.id)
                            if not bot_member.guild_permissions.manage_roles:
                                embed = create_warning_embed(
                                    "Achat réussi mais...",
                                    f"L'item a été acheté mais le bot n'a pas la permission `Gérer les rôles`. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {tax_info['total_price']:,} PrissBucks"
                                )
                                await send_func(embed=embed)
                                return
                            
                            # Vérifier que le rôle du bot est plus haut
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
                        f"L'item a été acheté mais il y a eu une erreur lors de l'attribution du rôle (permissions insuffisantes ?). Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {tax_info['total_price']:,} PrissBucks"
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
            
            # Récupérer le nouveau solde
            new_balance = await self.db.get_balance(user_id)
            
            # Message de confirmation avec taxes et effets spéciaux
            if item["type"] == "cooldown_reset" and cooldowns_cleared > 0:
                # Embed spécial pour les effets des items
                embed = create_special_item_effect_embed(
                    author, item['name'], 
                    "Tous tes cooldowns ont été immédiatement supprimés !",
                    cooldowns_cleared
                )
            else:
                # Embed normal avec taxes
                embed = create_purchase_embed_with_tax(
                    author, item, tax_info, new_balance, 
                    role_granted, role_name, special_effect_message
                )
            
            await send_func(embed=embed)
            
            # Log de l'action avec taxes
            logger.info(f"Achat avec taxe: {author} a acheté {item['name']} (ID: {item_id}) | Total: {tax_info['total_price']} | Taxe: {tax_info['tax_amount']}")
            
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

    # ==================== COMMANDES UTILITAIRES SUPPLÉMENTAIRES ====================

    @commands.command(name='listallitems')
    @commands.is_owner()
    async def list_all_items(self, ctx):
        """[OWNER] Liste TOUS les items (actifs et inactifs)"""
        try:
            async with self.db.pool.acquire() as conn:
                all_items = await conn.fetch("""
                    SELECT id, name, price, type, is_active, created_at 
                    FROM shop_items 
                    ORDER BY created_at DESC
                """)
            
            if not all_items:
                await ctx.send("❌ **Aucun item trouvé dans la base de données.**")
                return
            
            embed = discord.Embed(
                title="📋 Tous les items du shop",
                description=f"**{len(all_items)}** item(s) total dans la base de données",
                color=Colors.INFO
            )
            
            active_count = sum(1 for item in all_items if item['is_active'])
            inactive_count = len(all_items) - active_count
            
            embed.add_field(
                name="📊 Résumé",
                value=f"✅ **{active_count}** actif(s)\n❌ **{inactive_count}** inactif(s)",
                inline=False
            )
            
            # Lister tous les items
            items_text = ""
            for item in all_items:
                status = "✅" if item['is_active'] else "❌"
                date = item['created_at'].strftime('%d/%m')
                items_text += f"{status} `{item['id']:2d}` **{item['name']}** ({item['type']}) - {item['price']} PB - {date}\n"
                
                # Limite pour éviter de dépasser les 1024 caractères
                if len(items_text) > 900:
                    items_text += f"... et {len(all_items) - all_items.index(item) - 1} autre(s)\n"
                    break
            
            embed.add_field(
                name="🗂️ Liste complète",
                value=items_text,
                inline=False
            )
            
            embed.add_field(
                name="🔧 Commandes utiles",
                value=f"`{PREFIX}fixcooldownreset` - Activer l'item Reset Cooldowns\n"
                      f"`{PREFIX}removeitem <id>` - Désactiver un item\n"
                      f"`{PREFIX}setupcooldownreset` - Créer l'item Reset si inexistant",
                inline=False
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur list_all_items: {e}")
            await ctx.send(f"❌ **Erreur:** ```{str(e)}```")

    @commands.command(name='forceactivateitem')
    @commands.is_owner()
    async def force_activate_item(self, ctx, item_id: int):
        """[OWNER] Force l'activation d'un item"""
        try:
            async with self.db.pool.acquire() as conn:
                # Vérifier que l'item existe
                item = await conn.fetchrow("""
                    SELECT id, name, is_active FROM shop_items WHERE id = $1
                """, item_id)
                
                if not item:
                    await ctx.send(f"❌ **Item avec l'ID {item_id} introuvable.**")
                    return
                
                if item['is_active']:
                    await ctx.send(f"✅ **L'item '{item['name']}' est déjà actif.**")
                    return
                
                # Activer l'item
                await conn.execute("""
                    UPDATE shop_items SET is_active = TRUE WHERE id = $1
                """, item_id)
                
                embed = create_success_embed(
                    "Item activé !",
                    f"L'item **{item['name']}** (ID: {item_id}) est maintenant actif."
                )
                await ctx.send(embed=embed)
                
                logger.info(f"OWNER: {ctx.author} a forcé l'activation de l'item {item_id}")
                
        except Exception as e:
            logger.error(f"Erreur force_activate_item: {e}")
            await ctx.send(f"❌ **Erreur:** ```{str(e)}```")

    @commands.command(name='testshopembed')
    @commands.is_owner()
    async def test_shop_embed(self, ctx):
        """[OWNER] Test l'affichage d'un embed shop avec des données de test"""
        try:
            # Créer des données de test
            test_items = [
                {
                    'id': 1,
                    'name': '⏰ Reset Cooldowns',
                    'description': 'Désactive instantanément TOUS tes cooldowns en cours ! Usage immédiat à l'achat',
                    'price': 200,
                    'type': 'cooldown_reset',
                    'total_price': 210,
                    'tax_amount': 10
                },
                {
                    'id': 2,
                    'name': '🎭 Rôle VIP',
                    'description': 'Accès aux salons VIP et avantages exclusifs',
                    'price': 500,
                    'type': 'role',
                    'total_price': 525,
                    'tax_amount': 25
                }
            ]
            
            embed = create_shop_embed_with_tax(test_items, 1, 1, SHOP_TAX_RATE)
            embed.title = "🧪 TEST - " + embed.title
            embed.add_field(
                name="🔍 Test Info",
                value="Ceci est un test de l'affichage du shop avec des données fictives.",
                inline=False
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur test_shop_embed: {e}")
            await ctx.send(f"❌ **Erreur test embed:** ```{str(e)}```")

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Shop(bot))
