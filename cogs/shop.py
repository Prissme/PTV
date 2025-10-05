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
    """Système boutique complet avec rôles XP, défense et timeout"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        self.buy_cooldowns = {}
    
    async def cog_load(self):
        self.db = self.bot.database
        await self._create_timeout_tokens_table()
        await self._create_defense_table()
        logger.info("✅ Cog Shop initialisé avec XP roles, défense et timeout")
    
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
    
    async def _create_defense_table(self):
        """Crée la table pour les défenses anti-vol"""
        if not self.db or not self.db.pool:
            return
            
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_defenses (
                        user_id BIGINT PRIMARY KEY,
                        active BOOLEAN DEFAULT TRUE,
                        purchased_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                ''')
                logger.info("✅ Table user_defenses créée/vérifiée")
        except Exception as e:
            logger.error(f"Erreur création table défenses: {e}")
    
    def _check_buy_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown pour buy"""
        import time
        now = time.time()
        cooldown_duration = 3
        if user_id in self.buy_cooldowns:
            elapsed = now - self.buy_cooldowns[user_id]
            if elapsed < cooldown_duration:
                return cooldown_duration - elapsed
        self.buy_cooldowns[user_id] = now
        return 0

    def _calculate_price_with_tax(self, base_price: int) -> tuple:
        """Calcule le prix avec taxe"""
        tax_amount = int(base_price * SHOP_TAX_RATE)
        total_price = base_price + tax_amount
        return total_price, tax_amount

    async def _handle_special_item_effects(self, user, guild, item: dict) -> tuple:
        """Gère les effets spéciaux des items"""
        special_effect = None
        
        if item.get('type') == 'timeout_token':
            try:
                special_effect = await self._create_timeout_token(user.id, item)
            except Exception as e:
                logger.error(f"Erreur création timeout token: {e}")
                special_effect = "Erreur lors de la création du token"
        
        return special_effect

    async def _create_timeout_token(self, user_id: int, item: dict) -> str:
        """Crée un token de timeout"""
        if not self.db.pool:
            return "Erreur base de données"
        
        try:
            async with self.db.pool.acquire() as conn:
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
        """Affiche la boutique"""
        await self._execute_shop(ctx, page)

    @app_commands.command(name="shop", description="Affiche la boutique complète")
    @app_commands.describe(page="Numéro de la page à afficher")
    async def shop_slash(self, interaction: discord.Interaction, page: int = 1):
        await interaction.response.defer()
        await self._execute_shop(interaction, page, is_slash=True)

    async def _execute_shop(self, ctx_or_interaction, page=1, is_slash=False):
        """Logique commune pour shop"""
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
                    "La boutique est vide pour le moment."
                )
                await send_func(embed=embed)
                return
            
            for item in items:
                total_price, tax = self._calculate_price_with_tax(item['price'])
                item['total_price'] = total_price
                item['tax_amount'] = tax
            
            total_pages = math.ceil(len(items) / ITEMS_PER_PAGE)
            
            if page < 1 or page > total_pages:
                embed = create_error_embed(
                    "Page invalide",
                    f"Utilise une page entre 1 et {total_pages}."
                )
                await send_func(embed=embed)
                return
            
            start_idx = (page - 1) * ITEMS_PER_PAGE
            end_idx = start_idx + ITEMS_PER_PAGE
            page_items = items[start_idx:end_idx]
            
            embed = create_shop_embed_with_tax(page_items, page, total_pages, SHOP_TAX_RATE)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shop: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage de la boutique.")
            await send_func(embed=embed)

    # ==================== BUY XP ROLE ====================

    @commands.command(name='buy_xp_role')
    async def buy_xp_role_cmd(self, ctx, rank: str):
        """Achète un rôle XP boost"""
        await self._execute_buy_xp_role(ctx, rank)

    @app_commands.command(name="buy_xp_role", description="Achète un rôle de boost XP (argent vers banque publique)")
    @app_commands.describe(rank="Le rang à acheter (E, D, C, B, A, S, SS, SSS)")
    async def buy_xp_role_slash(self, interaction: discord.Interaction, rank: str):
        cooldown_remaining = self._check_buy_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            embed = discord.Embed(
                title=f"{Emojis.COOLDOWN} Cooldown actif !",
                description=f"Attends **{cooldown_remaining:.1f}s** avant un autre achat.",
                color=Colors.WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        await self._execute_buy_xp_role(interaction, rank, is_slash=True)

    async def _execute_buy_xp_role(self, ctx_or_interaction, rank, is_slash=False):
        """Achète un rôle XP avec redirection vers banque publique"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        rank = rank.upper()
        
        try:
            xp_cog = self.bot.get_cog('XPSystem')
            if not xp_cog:
                embed = create_error_embed("Système indisponible", "Le système XP n'est pas disponible.")
                await send_func(embed=embed)
                return
            
            if rank not in xp_cog.XP_ROLES:
                valid_ranks = ', '.join(xp_cog.XP_ROLES.keys())
                embed = create_error_embed("Rang invalide", f"Rangs disponibles: {valid_ranks}")
                await send_func(embed=embed)
                return
            
            role_data = xp_cog.XP_ROLES[rank]
            price = role_data['price']
            boost = role_data['boost']
            emoji = role_data['emoji']
            
            # Vérifier solde
            balance_before = await self.db.get_balance(user_id)
            if balance_before < price:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"Prix: {price:,} PB\nTon solde: {balance_before:,} PB"
                )
                await send_func(embed=embed)
                return
            
            # Transaction atomique
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Débiter utilisateur
                    await conn.execute(
                        "UPDATE users SET balance = balance - $1 WHERE user_id = $2",
                        price, user_id
                    )
                    
                    # Enregistrer rôle XP
                    await conn.execute("""
                        INSERT INTO user_xp (user_id, xp_boost_role)
                        VALUES ($1, $2)
                        ON CONFLICT (user_id) DO UPDATE SET xp_boost_role = $2
                    """, user_id, rank)
                    
                    # Envoyer vers banque publique
                    public_bank = self.bot.get_cog('PublicBank')
                    if public_bank:
                        await public_bank.add_casino_loss(price, "xp_role_purchase")
            
            # Calculer nouveau solde
            balance_after = balance_before - price
            
            # Logger la transaction
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_transaction(
                    user_id=user_id,
                    transaction_type='xp_role_purchase',
                    amount=-price,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    description=f"Achat rôle XP {rank} (+{boost*100:.0f}% XP)"
                )
            
            # Message de confirmation
            boost_percent = boost * 100
            embed = discord.Embed(
                title=f"{emoji} Rôle XP acheté !",
                description=f"**Rang {rank}** activé avec succès !",
                color=Colors.SUCCESS
            )
            
            embed.add_field(
                name="⚡ Boost XP",
                value=f"**+{boost_percent:.0f}%** sur tous les gains d'XP",
                inline=True
            )
            
            embed.add_field(
                name="💰 Prix payé",
                value=f"**{price:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="💳 Nouveau solde",
                value=f"**{balance_after:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="🏛️ Impact social",
                value=f"**{price:,} PB** versés à la banque publique !\nUtilise `/publicbank` pour voir les fonds disponibles.",
                inline=False
            )
            
            embed.set_footer(text="Les achats de rôles XP financent la banque publique !")
            await send_func(embed=embed)
            
            logger.info(f"XP Role purchase: {user} bought rank {rank} for {price} PB → Public bank")
            
        except Exception as e:
            logger.error(f"Erreur buy_xp_role {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'achat du rôle XP.")
            await send_func(embed=embed)

    # ==================== BUY DEFENSE ====================

    @commands.command(name='buy_defense')
    async def buy_defense_cmd(self, ctx):
        """Achète la défense anti-vol"""
        await self._execute_buy_defense(ctx)

    @app_commands.command(name="buy_defense", description="Achète une défense permanente contre les vols (2000 PB)")
    async def buy_defense_slash(self, interaction: discord.Interaction):
        cooldown_remaining = self._check_buy_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            embed = discord.Embed(
                title=f"{Emojis.COOLDOWN} Cooldown actif !",
                description=f"Attends **{cooldown_remaining:.1f}s**.",
                color=Colors.WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        await self._execute_buy_defense(interaction, is_slash=True)

    async def _execute_buy_defense(self, ctx_or_interaction, is_slash=False):
        """Achète une défense permanente anti-vol"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        defense_price = 2000
        
        try:
            # Vérifier si déjà possédé
            async with self.db.pool.acquire() as conn:
                has_defense = await conn.fetchval(
                    "SELECT 1 FROM user_defenses WHERE user_id = $1 AND active = TRUE",
                    user_id
                )
                
                if has_defense:
                    embed = create_error_embed(
                        "Déjà possédé",
                        "Tu possèdes déjà une défense anti-vol active !"
                    )
                    await send_func(embed=embed)
                    return
                
                # Vérifier solde
                balance_before = await self.db.get_balance(user_id)
                if balance_before < defense_price:
                    embed = create_error_embed(
                        "Solde insuffisant",
                        f"Prix: {defense_price:,} PB\nTon solde: {balance_before:,} PB"
                    )
                    await send_func(embed=embed)
                    return
                
                # Transaction atomique
                async with conn.transaction():
                    # Débiter
                    await conn.execute(
                        "UPDATE users SET balance = balance - $1 WHERE user_id = $2",
                        defense_price, user_id
                    )
                    
                    # Activer défense
                    await conn.execute("""
                        INSERT INTO user_defenses (user_id, active)
                        VALUES ($1, TRUE)
                        ON CONFLICT (user_id) DO UPDATE SET active = TRUE, purchased_at = NOW()
                    """, user_id)
            
            balance_after = balance_before - defense_price
            
            # Logger
            if hasattr(self.bot, 'transaction_logs'):
                await self.bot.transaction_logs.log_transaction(
                    user_id=user_id,
                    transaction_type='defense_purchase',
                    amount=-defense_price,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    description="Achat défense anti-vol permanente"
                )
            
            # Confirmation
            embed = discord.Embed(
                title="🛡️ Défense activée !",
                description="Protection anti-vol permanente acquise !",
                color=Colors.SUCCESS
            )
            
            embed.add_field(
                name="🎯 Protection",
                value="**100%** des tentatives de vol échoueront",
                inline=True
            )
            
            embed.add_field(
                name="💰 Prix payé",
                value=f"**{defense_price:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="💳 Nouveau solde",
                value=f"**{balance_after:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="✨ Effet permanent",
                value="Cette défense est active **à vie** !\nPlus aucune crainte des vols.",
                inline=False
            )
            
            await send_func(embed=embed)
            logger.info(f"Defense purchased: {user} bought anti-steal defense for {defense_price} PB")
            
        except Exception as e:
            logger.error(f"Erreur buy_defense {user_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'achat de la défense.")
            await send_func(embed=embed)

    # ==================== BUY STANDARD ====================

    @commands.command(name='buy', aliases=['acheter', 'purchase'])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def buy_cmd(self, ctx, item_id: int):
        """Achète un item du shop"""
        await self._execute_buy(ctx, item_id)

    @app_commands.command(name="buy", description="Achète un item de la boutique")
    @app_commands.describe(item_id="L'ID de l'item à acheter")
    async def buy_slash(self, interaction: discord.Interaction, item_id: int):
        cooldown_remaining = self._check_buy_cooldown(interaction.user.id)
        if cooldown_remaining > 0:
            embed = discord.Embed(
                title=f"{Emojis.COOLDOWN} Cooldown actif !",
                description=f"Attends **{cooldown_remaining:.1f}s**.",
                color=Colors.WARNING
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await interaction.response.defer()
        await self._execute_buy(interaction, item_id, is_slash=True)

    async def _execute_buy(self, ctx_or_interaction, item_id, is_slash=False):
        """Logique d'achat standard"""
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
            item = await self.db.get_shop_item(item_id)
            if not item or not item["is_active"]:
                embed = create_error_embed(
                    "Item introuvable",
                    "Cet item n'existe pas ou n'est plus disponible."
                )
                await send_func(embed=embed)
                return
            
            balance_before = await self.db.get_balance(user_id)
            
            success, message, tax_info = await self.db.purchase_item_with_tax(
                user_id, item_id, SHOP_TAX_RATE, OWNER_ID
            )
            
            if not success:
                embed = create_error_embed("Achat échoué", message)
                await send_func(embed=embed)
                return
            
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
            
            role_granted = False
            role_name = None
            special_effect = None
            
            if item["type"] == "role":
                try:
                    role_id = item["data"].get("role_id")
                    if role_id:
                        role = guild.get_role(int(role_id))
                        if role:
                            bot_member = guild.get_member(self.bot.user.id)
                            if not bot_member.guild_permissions.manage_roles:
                                embed = create_warning_embed(
                                    "Achat réussi mais...",
                                    f"Le bot n'a pas la permission de gérer les rôles."
                                )
                                await send_func(embed=embed)
                                return
                            
                            if role >= bot_member.top_role:
                                embed = create_warning_embed(
                                    "Achat réussi mais...",
                                    f"Le rôle est trop haut dans la hiérarchie."
                                )
                                await send_func(embed=embed)
                                return
                            
                            await author.add_roles(role, reason=f"Achat boutique: {item['name']}")
                            role_granted = True
                            role_name = role.name
                            
                except Exception as e:
                    logger.error(f"Erreur attribution rôle: {e}")
            
            if item["type"] == "timeout_token":
                special_effect = await self._handle_special_item_effects(author, guild, item)
            
            new_balance = await self.db.get_balance(user_id)
            
            embed = create_purchase_embed_with_tax(
                author, item, tax_info, new_balance, role_granted, role_name, special_effect
            )
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur buy: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'achat.")
            await send_func(embed=embed)

    # ==================== TIMEOUT COMMAND ====================

    @commands.command(name='timeout', aliases=['timeoutuser'])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def timeout_cmd(self, ctx, target: discord.Member, *, reason: str = "Temps mort rigolo !"):
        """Utilise un token pour timeout"""
        await self._execute_timeout(ctx, target, reason)

    @app_commands.command(name="timeout", description="Utilise un token pour timeout (5 min max)")
    @app_commands.describe(
        target="L'utilisateur à timeout",
        reason="Raison du timeout"
    )
    async def timeout_slash(self, interaction: discord.Interaction, target: discord.Member, reason: str = "Temps mort rigolo !"):
        await interaction.response.defer()
        await self._execute_timeout(interaction, target, reason, is_slash=True)

    async def _execute_timeout(self, ctx_or_interaction, target: discord.Member, reason: str, is_slash=False):
        """Utilise un token timeout"""
        if is_slash:
            user = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            user = ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        user_id = user.id
        
        # Protections
        if target.guild_permissions.administrator or target.guild_permissions.moderate_members:
            embed = create_error_embed(
                "Cible protégée",
                "Tu ne peux pas timeout un modérateur ou administrateur !"
            )
            await send_func(embed=embed)
            return
        
        if target.id == user_id:
            embed = create_error_embed(
                "Auto-timeout interdit",
                "Tu ne peux pas te timeout toi-même !"
            )
            await send_func(embed=embed)
            return
        
        if target.bot:
            embed = create_error_embed(
                "Bots protégés",
                "Tu ne peux pas timeout un bot !"
            )
            await send_func(embed=embed)
            return
        
        if not ctx_or_interaction.guild.me.guild_permissions.moderate_members:
            embed = create_error_embed(
                "Permissions insuffisantes",
                "Le bot n'a pas les permissions pour timeout !"
            )
            await send_func(embed=embed)
            return
        
        try:
            async with self.db.pool.acquire() as conn:
                tokens = await conn.fetchval("""
                    SELECT timeout_tokens FROM user_timeout_tokens 
                    WHERE user_id = $1
                """, user_id)
                
                if not tokens or tokens <= 0:
                    embed = create_error_embed(
                        "Pas de token",
                        f"Tu n'as pas de token de timeout ! Achète-en dans le shop."
                    )
                    await send_func(embed=embed)
                    return
                
                await conn.execute("""
                    UPDATE user_timeout_tokens 
                    SET timeout_tokens = timeout_tokens - 1,
                        last_used = NOW()
                    WHERE user_id = $1
                """, user_id)
            
            timeout_duration = datetime.timedelta(minutes=5)
            await target.timeout(timeout_duration, reason=f"Timeout premium par {user.display_name}: {reason}")
            
            embed = discord.Embed(
                title="⏰ Timeout Premium utilisé !",
                description=f"{target.mention} a reçu un timeout de 5 minutes !",
                color=Colors.WARNING
            )
            embed.add_field(name="Raison", value=reason, inline=False)
            embed.add_field(name="Durée", value="5 minutes", inline=True)
            embed.add_field(name="Tokens restants", value=f"{tokens-1}", inline=True)
            embed.add_field(name="Utilisé par", value=user.mention, inline=True)
            
            await send_func(embed=embed)
            logger.info(f"Premium timeout: {user} timeout {target} for 5min - Tokens left: {tokens-1}")
            
        except discord.Forbidden:
            embed = create_error_embed(
                "Hiérarchie des rôles",
                "Je ne peux pas timeout cet utilisateur"
            )
            await send_func(embed=embed)
        except Exception as e:
            logger.error(f"Erreur timeout premium: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du timeout")
            await send_func(embed=embed)

    # ==================== INVENTORY ====================

    @commands.command(name='inventory', aliases=['inv', 'inventaire'])
    async def inventory_cmd(self, ctx, member: discord.Member = None):
        """Affiche l'inventaire"""
        await self._execute_inventory(ctx, member)

    @app_commands.command(name="inventory", description="Affiche l'inventaire")
    @app_commands.describe(utilisateur="L'utilisateur dont voir l'inventaire")
    async def inventory_slash(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        await interaction.response.defer()
        await self._execute_inventory(interaction, utilisateur, is_slash=True)

    async def _execute_inventory(self, ctx_or_interaction, member=None, is_slash=False):
        """Logique commune pour inventory"""
        if is_slash:
            target = member or ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            target = member or ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        try:
            purchases = await self.db.get_user_purchases(target.id)
            
            # Ajouter tokens timeout
            tokens_count = 0
            if self.db.pool:
                async with self.db.pool.acquire() as conn:
                    tokens_result = await conn.fetchval("""
                        SELECT timeout_tokens FROM user_timeout_tokens 
                        WHERE user_id = $1
                    """, target.id)
                    tokens_count = tokens_result or 0
            
            if tokens_count > 0:
                token_entry = {
                    'name': '⏰ Tokens Timeout',
                    'description': f'Permet de timeout des utilisateurs (5 min max)',
                    'type': 'timeout_token',
                    'price_paid': 0,
                    'tax_paid': 0,
                    'purchase_date': datetime.datetime.now(),
                    'data': {'quantity': tokens_count}
                }
                purchases.insert(0, token_entry)
            
            embed = create_inventory_embed(target, purchases)
            
            if tokens_count > 0:
                embed.add_field(
                    name="🎯 Tokens actifs",
                    value=f"**{tokens_count}** token(s) timeout disponible(s)\nUtilise `{PREFIX}timeout @user` pour les utiliser",
                    inline=False
                )
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur inventory: {e}")
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
        description="Description de l'item"
    )
    @app_commands.default_permissions(administrator=True)
    async def add_item_slash(self, interaction: discord.Interaction, price: int, role: discord.Role, name: str, description: str = None):
        if not interaction.user.guild_permissions.administrator:
            embed = create_error_embed("Permission refusée", "Admins seulement.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await interaction.response.defer()
        await self._execute_add_item(interaction, price, role, name, description, is_slash=True)

    async def _execute_add_item(self, ctx_or_interaction, price, role, name, description=None, is_slash=False):
        """Ajoute un item au shop"""
        if is_slash:
            admin = ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            admin = ctx_or_interaction.author
            send_func = ctx_or_interaction.send

        if price <= 0 or price > 10000000:
            embed = create_error_embed("Prix invalide", "Le prix doit être entre 1 et 10,000,000 PB.")
            await send_func(embed=embed)
            return

        try:
            if not description:
                description = f"Rôle {role.name} - Débloquer des avantages exclusifs !"
            
            item_data = {"role_id": role.id}
            total_price, tax = self._calculate_price_with_tax(price)
            
            item_id = await self.db.add_shop_item(
                name=name,
                description=description,
                price=price,
                item_type="role",
                data=item_data
            )
            
            embed = create_success_embed("Item ajouté !", f"**{name}** ajouté à la boutique !")
            embed.add_field(name="💰 Prix de base", value=f"{price:,} PB", inline=True)
            embed.add_field(name="🛒 Prix avec taxe", value=f"{total_price:,} PB", inline=True)
            embed.add_field(name="🎭 Rôle", value=role.mention, inline=True)
            embed.add_field(name="🆔 ID", value=f"`{item_id}`", inline=True)
            embed.add_field(name="📈 Taxe", value=f"{SHOP_TAX_RATE*100}% ({tax:,} PB)", inline=True)
            
            await send_func(embed=embed)
            logger.info(f"ADMIN: {admin} added item '{name}' (ID: {item_id}, Price: {price})")
            
        except Exception as e:
            logger.error(f"Erreur add_item: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'ajout.")
            await send_func(embed=embed)

async def setup(bot):
    await bot.add_cog(Shop(bot))
