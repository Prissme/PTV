import discord
from discord.ext import commands
from discord import app_commands
import math
import logging
import json
import datetime

from config import ITEMS_PER_PAGE, SHOP_TAX_RATE, OWNER_ID, Colors, Emojis, PREFIX
from utils.embeds import (
    create_shop_embed_with_tax, create_purchase_embed_with_tax, create_inventory_embed,
    create_error_embed, create_warning_embed, create_success_embed
)

logger = logging.getLogger(__name__)

class Shop(commands.Cog):
    """Système boutique complet : shop, buy, inventory avec timeout rigolo"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        # Dictionnaire pour gérer les cooldowns manuellement des slash commands
        self.buy_cooldowns = {}
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        await self._create_timeout_tokens_table()
        logger.info("✅ Cog Shop initialisé avec système timeout rigolo")
    
    async def _create_timeout_tokens_table(self):
        """Crée la table pour les tokens timeout"""
        if not self.db or not self.db.pool:
            return
            
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_timeout_tokens (
                        user_id BIGINT PRIMARY KEY,
                        timeout_tokens INTEGER DEFAULT 0,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                        last_used TIMESTAMP WITH TIME ZONE
                    )
                ''')
                logger.info("✅ Table user_timeout_tokens créée/vérifiée")
        except Exception as e:
            logger.error(f"Erreur création table timeout tokens: {e}")
    
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

    async def _handle_special_item_effects(self, user, guild, item: dict) -> tuple:
        """Gère les effets spéciaux des items après achat"""
        special_effect = None
        
        # Item timeout rigolo
        if item.get('type') == 'timeout_token':
            try:
                special_effect = await self._create_timeout_token(user.id, item)
            except Exception as e:
                logger.error(f"Erreur création timeout token: {e}")
                special_effect = "Erreur lors de la création du token"
        
        return special_effect

    async def _create_timeout_token(self, user_id: int, item: dict) -> str:
        """Crée un token de timeout utilisable"""
        if not self.db.pool:
            return "Erreur base de données"
        
        try:
            async with self.db.pool.acquire() as conn:
                # Vérifier s'il a déjà des tokens
                existing = await conn.fetchval("""
                    SELECT timeout_tokens FROM user_timeout_tokens 
                    WHERE user_id = $1
                """, user_id)
                
                if existing is not None:
                    new_count = existing + 1
                    await conn.execute("""
                        UPDATE user_timeout_tokens 
                        SET timeout_tokens = $1 
                        WHERE user_id = $2
                    """, new_count, user_id)
                else:
                    await conn.execute("""
                        INSERT INTO user_timeout_tokens (user_id, timeout_tokens)
                        VALUES ($1, 1)
                    """, user_id)
            
            return f"Token de temps mort ajouté ! Utilise `{PREFIX}timeout @user` pour l'utiliser."
        except Exception as e:
            logger.error(f"Erreur ajout timeout token: {e}")
            return "Erreur lors de l'ajout du token"

    # ==================== SHOP COMMANDS ====================

    @commands.command(name='shop', aliases=['boutique', 'store'])
    async def shop_cmd(self, ctx, page: int = 1):
        """e!shop [page] - Affiche la boutique avec pagination et prix avec taxes"""
        await self._execute_shop(ctx, page)

    @app_commands.command(name="shop", description="Affiche la boutique avec tous les items disponibles (prix avec taxes)")
    @app_commands.describe(page="Numéro de la page à afficher (optionnel)")
    async def shop_slash(self, interaction: discord.Interaction, page: int = 1):
        """/shop [page] - Affiche la boutique"""
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

    # ==================== BUY COMMANDS AVEC TAXES ET LOGS ====================

    @commands.command(name='buy', aliases=['acheter', 'purchase'])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def buy_cmd(self, ctx, item_id: int):
        """e!buy <item_id> - Achète un item du shop (avec taxe de 5%)"""
        await self._execute_buy(ctx, item_id)

    @app_commands.command(name="buy", description="Achète un item de la boutique (avec taxe de 5%)")
    @app_commands.describe(item_id="L'ID de l'item à acheter (visible dans /shop)")
    async def buy_slash(self, interaction: discord.Interaction, item_id: int):
        """/buy <item_id> - Achète un item"""
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
        """Logique commune pour buy avec taxes et logs intégrés (prefix et slash)"""
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
            # Récupérer les infos de l'item AVANT l'achat
            item = await self.db.get_shop_item(item_id)
            if not item or not item["is_active"]:
                embed = create_error_embed(
                    "Item introuvable",
                    "Cet item n'existe pas ou n'est plus disponible."
                )
                await send_func(embed=embed)
                return
            
            # Récupérer le solde AVANT l'achat pour les logs
            balance_before = await self.db.get_balance(user_id)
            
            # Effectuer l'achat avec taxe (transaction atomique)
            success, message, tax_info = await self.db.purchase_item_with_tax(
                user_id, item_id, SHOP_TAX_RATE, OWNER_ID
            )
            
            if not success:
                embed = create_error_embed("Achat échoué", message)
                await send_func(embed=embed)
                return
            
            # Calculer le nouveau solde APRÈS l'achat et logger la transaction
            balance_after = balance_before - tax_info['total_price']
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_purchase(
                    user_id=user_id,
                    item_name=item['name'],
                    price=tax_info['base_price'],
                    tax=tax_info['tax_amount'],
                    balance_before=balance_before,
                    balance_after=balance_after
                )
            
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
                            logger.info(f"Rôle {role.name} attribué à {author} (achat item {item_id}) [LOGGED]")
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
            
            # ==================== GESTION DES ITEMS TIMEOUT ====================
            if item["type"] == "timeout_token":
                special_effect = await self._handle_special_item_effects(author, guild, item)
            
            # Récupérer le nouveau solde final (pour être sûr)
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
                
            logger.info(f"Achat avec effets: {author} a acheté {item['name']} (ID: {item_id}) | Total: {tax_info['total_price']} | Taxe: {tax_info['tax_amount']}{effect_log} [LOGGED]")
            
        except Exception as e:
            logger.error(f"Erreur buy {user_id} -> {item_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'achat.")
            await send_func(embed=embed)

    # ==================== TIMEOUT COMMAND ====================

    @commands.command(name='timeout', aliases=['timeoutuser'])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def timeout_cmd(self, ctx, target: discord.Member, *, reason: str = "Temps mort rigolo !"):
        """Utilise un token pour donner un timeout rigolo de 5 minutes"""
        await self._execute_timeout(ctx, target, reason)

    @app_commands.command(name="timeout", description="[PREMIUM] Utilise un token pour donner un timeout rigolo de 5 minutes")
    @app_commands.describe(
        target="L'utilisateur à timeout",
        reason="Raison du timeout (optionnel)"
    )
    async def timeout_slash(self, interaction: discord.Interaction, target: discord.Member, reason: str = "Temps mort rigolo !"):
        """Slash command pour timeout premium"""
        await interaction.response.defer()
        await self._execute_timeout(interaction, target, reason, is_slash=True)

    async def _execute_timeout(self, ctx_or_interaction, target: discord.Member, reason: str, is_slash=False):
        """Utilise un token de timeout acheté dans le shop"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        
        # ==================== PROTECTIONS IMPORTANTES ====================
        # 1. Vérifier que ce n'est pas un admin/modérateur
        if target.guild_permissions.administrator or target.guild_permissions.moderate_members:
            embed = create_error_embed(
                "Cible protégée",
                "Tu ne peux pas timeout un modérateur ou administrateur !"
            )
            await send_func(embed=embed)
            return
        
        # 2. Vérifier que ce n'est pas soi-même
        if target.id == user_id:
            embed = create_error_embed(
                "Auto-timeout interdit",
                "Tu ne peux pas te timeout toi-même !"
            )
            await send_func(embed=embed)
            return
        
        # 3. Vérifier que ce n'est pas un bot
        if target.bot:
            embed = create_error_embed(
                "Bots protégés",
                "Tu ne peux pas timeout un bot !"
            )
            await send_func(embed=embed)
            return
        
        # 4. Vérifier les permissions du bot
        if not ctx_or_interaction.guild.me.guild_permissions.moderate_members:
            embed = create_error_embed(
                "Permissions insuffisantes",
                "Le bot n'a pas les permissions pour timeout !"
            )
            await send_func(embed=embed)
            return
        
        try:
            # Vérifier si l'utilisateur a des tokens
            async with self.db.pool.acquire() as conn:
                tokens = await conn.fetchval("""
                    SELECT timeout_tokens FROM user_timeout_tokens 
                    WHERE user_id = $1
                """, user_id)
                
                if not tokens or tokens <= 0:
                    embed = create_error_embed(
                        "Pas de token",
                        f"Tu n'as pas de token de timeout ! Achète-en un dans le shop avec `{PREFIX}buy <id>`."
                    )
                    await send_func(embed=embed)
                    return
                
                # Consommer un token
                await conn.execute("""
                    UPDATE user_timeout_tokens 
                    SET timeout_tokens = timeout_tokens - 1,
                        last_used = NOW()
                    WHERE user_id = $1
                """, user_id)
            
            # Appliquer le timeout (5 minutes pour rester rigolo)
            timeout_duration = datetime.timedelta(minutes=5)
            await target.timeout(timeout_duration, reason=f"Timeout premium par {user.display_name}: {reason}")
            
            # Message de confirmation
            embed = discord.Embed(
                title="⏰ Timeout Premium utilisé !",
                description=f"{target.mention} a reçu un timeout de 5 minutes !",
                color=Colors.WARNING
            )
            embed.add_field(name="Raison", value=reason, inline=False)
            embed.add_field(name="Durée", value="5 minutes", inline=True)
            embed.add_field(name="Tokens restants", value=f"{tokens-1}", inline=True)
            embed.add_field(name="Utilisé par", value=user.mention, inline=True)
            embed.set_footer(text="Timeout rigolo via le système premium !")
            
            await send_func(embed=embed)
            
            # Log l'action
            logger.info(f"Premium timeout: {user} a timeout {target} pour 5min - Tokens restants: {tokens-1}")
            
        except discord.Forbidden:
            embed = create_error_embed(
                "Hiérarchie des rôles",
                "Je ne peux pas timeout cet utilisateur (hiérarchie des rôles)"
            )
            await send_func(embed=embed)
        except Exception as e:
            logger.error(f"Erreur timeout premium: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du timeout")
            await send_func(embed=embed)

    # ==================== INVENTORY COMMANDS ====================

    @commands.command(name='inventory', aliases=['inv', 'inventaire'])
    async def inventory_cmd(self, ctx, member: discord.Member = None):
        """e!inventory [@utilisateur] - Affiche l'inventaire d'un utilisateur"""
        await self._execute_inventory(ctx, member)

    @app_commands.command(name="inventory", description="Affiche l'inventaire d'un utilisateur")
    @app_commands.describe(utilisateur="L'utilisateur dont voir l'inventaire (optionnel)")
    async def inventory_slash(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        """/inventory [utilisateur] - Affiche l'inventaire"""
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
            
            # Ajouter les tokens timeout à l'inventaire
            tokens_count = 0
            if self.db.pool:
                async with self.db.pool.acquire() as conn:
                    tokens_result = await conn.fetchval("""
                        SELECT timeout_tokens FROM user_timeout_tokens 
                        WHERE user_id = $1
                    """, target.id)
                    tokens_count = tokens_result or 0
            
            # Créer un pseudo-achat pour les tokens
            if tokens_count > 0:
                token_entry = {
                    'name': '⏰ Tokens Timeout',
                    'description': f'Permet de timeout des utilisateurs (5 min max)',
                    'type': 'timeout_token',
                    'price_paid': 0,  # Prix non affiché pour les tokens
                    'tax_paid': 0,
                    'purchase_date': datetime.datetime.now(),
                    'data': {'quantity': tokens_count}
                }
                purchases.insert(0, token_entry)  # Ajouter en première position
            
            embed = create_inventory_embed(target, purchases)
            
            # Ajouter info spéciale pour les tokens
            if tokens_count > 0:
                embed.add_field(
                    name="🎯 Tokens actifs",
                    value=f"**{tokens_count}** token(s) timeout disponible(s)\nUtilise `{PREFIX}timeout @user` pour les utiliser",
                    inline=False
                )
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur inventory pour {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération de l'inventaire.")
            await send_func(embed=embed)

    # ==================== COMMANDES D'INFO TOKENS ====================

    @commands.command(name='tokens', aliases=['mytokens'])
    async def tokens_cmd(self, ctx, user: discord.Member = None):
        """Affiche tes tokens timeout disponibles"""
        await self._execute_tokens_info(ctx, user)

    @app_commands.command(name="tokens", description="Affiche tes tokens timeout disponibles")
    @app_commands.describe(user="Utilisateur dont voir les tokens (optionnel)")
    async def tokens_slash(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        await self._execute_tokens_info(interaction, user, is_slash=True)

    async def _execute_tokens_info(self, ctx_or_interaction, user=None, is_slash=False):
        """Affiche les tokens timeout d'un utilisateur"""
        if is_slash:
            target = user or ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            target = user or ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        try:
            tokens_count = 0
            last_used = None
            
            if self.db.pool:
                async with self.db.pool.acquire() as conn:
                    result = await conn.fetchrow("""
                        SELECT timeout_tokens, last_used FROM user_timeout_tokens 
                        WHERE user_id = $1
                    """, target.id)
                    
                    if result:
                        tokens_count = result['timeout_tokens'] or 0
                        last_used = result['last_used']

            embed = discord.Embed(
                title="⏰ Tokens Timeout",
                description=f"Tokens de **{target.display_name}**",
                color=Colors.PREMIUM if tokens_count > 0 else Colors.WARNING
            )

            embed.add_field(
                name="🎯 Tokens disponibles",
                value=f"**{tokens_count}** token(s)",
                inline=True
            )

            if last_used:
                embed.add_field(
                    name="📅 Dernière utilisation",
                    value=f"<t:{int(last_used.timestamp())}:R>",
                    inline=True
                )

            if tokens_count > 0:
                embed.add_field(
                    name="🚀 Utilisation",
                    value=f"`{PREFIX}timeout @user [raison]`\n5 minutes maximum par timeout",
                    inline=False
                )
                embed.add_field(
                    name="🛡️ Protections",
                    value="• Admins/modérateurs protégés\n• Bots protégés\n• Auto-timeout interdit",
                    inline=True
                )
            else:
                embed.add_field(
                    name="🛒 Comment obtenir ?",
                    value=f"Achète des tokens dans `{PREFIX}shop` !\nCherche l'item **Token Temps Mort**",
                    inline=False
                )

            embed.set_thumbnail(url=target.display_avatar.url)
            embed.set_footer(text="Système timeout rigolo • Protections incluses")
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur tokens info {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des tokens.")
            await send_func(embed=embed)

    # ==================== ADMIN COMMANDS ====================

    @commands.command(name='addtimeoutitem')
    @commands.has_permissions(administrator=True)
    async def add_timeout_item_cmd(self, ctx, price: int = 1000):
        """[ADMIN] Ajoute l'item timeout token au shop"""
        await self._execute_add_timeout_item(ctx, price)

    @app_commands.command(name="addtimeoutitem", description="[ADMIN] Ajoute l'item timeout token au shop")
    @app_commands.describe(price="Prix du token en PrissBucks (sans taxe)")
    @app_commands.default_permissions(administrator=True)
    async def add_timeout_item_slash(self, interaction: discord.Interaction, price: int = 1000):
        """Ajoute l'item timeout au shop"""
        if not interaction.user.guild_permissions.administrator:
            embed = create_error_embed(
                "Permission refusée", 
                "Seuls les administrateurs peuvent utiliser cette commande."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()
        await self._execute_add_timeout_item(interaction, price, is_slash=True)

    async def _execute_add_timeout_item(self, ctx_or_interaction, price, is_slash=False):
        """Ajoute l'item timeout token au shop"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
            admin = ctx_or_interaction.user
        else:
            send_func = ctx_or_interaction.send
            admin = ctx_or_interaction.author

        try:
            if price <= 0 or price > 10000000:
                embed = create_error_embed("Prix invalide", "Le prix doit être entre 1 et 10,000,000 PrissBucks.")
                await send_func(embed=embed)
                return

            item_data = {
                "duration_minutes": 5,
                "restrictions": ["no_admin", "no_mod", "no_bot", "no_self"],
                "type": "timeout_rigolo"
            }
            
            item_id = await self.db.add_shop_item(
                name="⏰ Token Temps Mort",
                description="Permet de donner un timeout rigolo de 5 minutes à un utilisateur (protections incluses)",
                price=price,
                item_type="timeout_token",
                data=item_data
            )
            
            # Calculer le prix avec taxe
            total_price, tax = self._calculate_price_with_tax(price)
            
            embed = create_success_embed(
                "Item timeout ajouté !",
                f"**Token Temps Mort** ajouté au shop avec succès !"
            )
            embed.add_field(name="💰 Prix de base", value=f"{price:,} PrissBucks", inline=True)
            embed.add_field(name="🛒 Prix avec taxe", value=f"{total_price:,} PrissBucks", inline=True)
            embed.add_field(name="🆔 ID", value=f"`{item_id}`", inline=True)
            embed.add_field(name="⏱️ Durée", value="5 minutes maximum", inline=True)
            embed.add_field(name="🛡️ Protections", value="Admins/mods protégés", inline=True)
            embed.add_field(name="📈 Taxe", value=f"{SHOP_TAX_RATE*100}% ({tax:,} PB)", inline=True)
            embed.add_field(
                name="ℹ️ Utilisation", 
                value=f"Après achat : `{PREFIX}timeout @user [raison]`", 
                inline=False
            )
            
            embed.set_footer(text=f"Ajouté par {admin.display_name} • Item rigolo et sécurisé")
            await send_func(embed=embed)
            
            logger.info(f"ADMIN: {admin} a ajouté l'item timeout token (ID: {item_id}, Prix: {price})")
            
        except Exception as e:
            logger.error(f"Erreur add timeout item: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'ajout de l'item timeout.")
            await send_func(embed=embed)

    @commands.command(name='additem')
    @commands.has_permissions(administrator=True)
    async def add_item_cmd(self, ctx, price: int, role: discord.Role, *, name: str):
        """e!additem <prix> <@role> <nom> - [ADMIN] Ajoute un rôle à la boutique"""
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
        """/additem <price> <role> <name> [description] - [ADMIN] Ajoute un item"""
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
            embed.add_field(name="🛒 Prix avec taxe", value=f"{total_price:,} PrissBucks", inline=True)
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
        """e!removeitem <item_id> - [ADMIN] Désactive un item de la boutique"""
        await self._execute_remove_item(ctx, item_id)

    @app_commands.command(name="removeitem", description="[ADMIN] Désactive un item de la boutique")
    @app_commands.describe(item_id="L'ID de l'item à désactiver")
    @app_commands.default_permissions(administrator=True)
    async def remove_item_slash(self, interaction: discord.Interaction, item_id: int):
        """/removeitem <item_id> - [ADMIN] Désactive un item"""
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
        """e!shopstats - [ADMIN] Affiche les statistiques de la boutique"""
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
                name="🛒 Taxes collectées", 
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
            
            embed.set_footer(text=f"Taux de taxe actuel: {SHOP_TAX_RATE*100}% • Tous les achats sont enregistrés")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shopstats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)

    @app_commands.command(name="shopstats", description="[ADMIN] Affiche les statistiques de la boutique")
    @app_commands.default_permissions(administrator=True)
    async def shop_stats_slash(self, interaction: discord.Interaction):
        """/shopstats - [ADMIN] Statistiques de la boutique"""
        if not interaction.user.guild_permissions.administrator:
            embed = create_error_embed(
                "Permission refusée", 
                "Seuls les administrateurs peuvent utiliser cette commande."
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer()
        
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
                name="🛒 Taxes collectées", 
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
            
            embed.set_footer(text=f"Taux de taxe actuel: {SHOP_TAX_RATE*100}% • Tous les achats sont enregistrés")
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shopstats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await interaction.followup.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Shop(bot))
