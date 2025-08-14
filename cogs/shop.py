import discord
from discord.ext import commands
from discord import app_commands
import math
import logging

from config import ITEMS_PER_PAGE, Colors, Emojis, SHOP_TAX_RATE, OWNER_ID
from utils.embeds import (
    create_shop_embed, create_purchase_embed,
    create_error_embed, create_warning_embed
)

logger = logging.getLogger(__name__)

class Shop(commands.Cog):
    """Système boutique avec taxes 5% : shop, buy"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info(f"✅ Cog Shop initialisé avec taxes {SHOP_TAX_RATE*100}% et slash commands")

    def create_shop_embed_with_tax(self, items: list, page: int, total_pages: int) -> discord.Embed:
        """Créer un embed pour la boutique avec affichage des taxes"""
        embed = discord.Embed(
            title=f"{Emojis.SHOP} Boutique PrissBucks",
            description=f"Dépense tes PrissBucks pour des récompenses exclusives !\n"
                       f"{Emojis.TAX} **Taxe:** {SHOP_TAX_RATE*100}% appliquée sur tous les achats",
            color=Colors.PREMIUM
        )
        
        for item in items:
            icon = Emojis.ROLE if item["type"] == "role" else Emojis.SHOP
            base_price = item['price']
            tax_amount = int(base_price * SHOP_TAX_RATE)
            total_price = base_price + tax_amount
            
            price_display = f"**{base_price:,}** {Emojis.MONEY}"
            if tax_amount > 0:
                price_display += f" + {tax_amount:,} taxe = **{total_price:,}** {Emojis.MONEY}"
            
            embed.add_field(
                name=f"{icon} **{item['name']}**",
                value=f"{item['description']}\n"
                     f"💰 Prix: {price_display}\n"
                     f"`/buy {item['id']}` ou `{self.bot.command_prefix}buy {item['id']}` pour acheter",
                inline=False
            )
        
        embed.set_footer(text=f"Page {page}/{total_pages} • {len(items)} item(s) • Taxes: {SHOP_TAX_RATE*100}%")
        
        # Ajouter des boutons de navigation si nécessaire
        if total_pages > 1:
            embed.add_field(
                name="📄 Navigation",
                value=f"`{self.bot.command_prefix}shop {page-1 if page > 1 else total_pages}` ← Page précédente\n"
                      f"`{self.bot.command_prefix}shop {page+1 if page < total_pages else 1}` → Page suivante",
                inline=False
            )
        
        return embed

    def create_purchase_embed_with_tax(self, user: discord.Member, item: dict, new_balance: int, tax_info: dict, role_granted: bool = False, role_name: str = None) -> discord.Embed:
        """Créer un embed pour les achats avec détails de la taxe"""
        embed = discord.Embed(
            title=f"{Emojis.SUCCESS} Achat réussi !",
            description=f"**{user.display_name}** a acheté **{item['name']}** !",
            color=Colors.SUCCESS
        )
        
        if tax_info and tax_info.get('tax_amount', 0) > 0:
            embed.add_field(
                name="💰 Détail du prix",
                value=f"**Prix base:** {tax_info['base_price']:,} {Emojis.MONEY}\n"
                      f"**Taxe ({tax_info['tax_rate']}%):** +{tax_info['tax_amount']:,} {Emojis.MONEY}\n"
                      f"**Total payé:** {tax_info['total_price']:,} {Emojis.MONEY}",
                inline=True
            )
        else:
            embed.add_field(
                name="💰 Prix payé",
                value=f"{item['price']:,} {Emojis.MONEY}",
                inline=True
            )
        
        if role_granted and role_name:
            embed.add_field(
                name=f"{Emojis.ROLE} Rôle attribué",
                value=f"**{role_name}**",
                inline=True
            )
        
        embed.add_field(
            name="💳 Nouveau solde",
            value=f"{new_balance:,} {Emojis.MONEY}",
            inline=True
        )
        
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Merci pour ton achat ! Les taxes contribuent au développement du serveur.")
        return embed

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
            
            # Créer l'embed avec affichage des taxes
            embed = self.create_shop_embed_with_tax(page_items, page, total_pages)
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shop: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage de la boutique.")
            await send_func(embed=embed)

    @commands.command(name='buy', aliases=['acheter', 'purchase'])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def buy_cmd(self, ctx, item_id: int):
        """Achète un item du shop avec taxe"""
        await self._execute_buy(ctx, item_id)

    @app_commands.command(name="buy", description="Achète un item de la boutique (taxe 5% incluse)")
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
        """Logique commune pour buy (prefix et slash) avec taxes"""
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
            # Récupérer les infos de l'item pour prévisualiser le prix
            item = await self.db.get_shop_item(item_id)
            if not item or not item["is_active"]:
                embed = create_error_embed(
                    "Item introuvable",
                    "Cet item n'existe pas ou n'est plus disponible."
                )
                await send_func(embed=embed)
                return

            # Calculer le prix avec taxe pour l'affichage
            base_price = item["price"]
            tax_amount = int(base_price * SHOP_TAX_RATE)
            total_price = base_price + tax_amount

            # Vérifier le solde avant l'achat
            current_balance = await self.db.get_balance(user_id)
            if current_balance < total_price:
                embed = create_error_embed(
                    "Solde insuffisant",
                    f"**Prix:** {base_price:,} {Emojis.MONEY} + {tax_amount:,} taxe = **{total_price:,}** {Emojis.MONEY}\n"
                    f"**Ton solde:** {current_balance:,} {Emojis.MONEY}\n"
                    f"**Manque:** {total_price - current_balance:,} {Emojis.MONEY}"
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
            
            # Si c'est un rôle, l'attribuer
            role_granted = False
            role_name = None
            
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
                                f"L'item a été acheté mais le rôle est introuvable. Contacte un administrateur.\n\n"
                                f"**Item acheté:** {item['name']}\n"
                                f"**Prix payé:** {tax_info.get('total_price', item['price']):,} {Emojis.MONEY}"
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
                        f"L'item a été acheté mais il y a eu une erreur lors de l'attribution du rôle. Contacte un administrateur.\n\n"
                        f"**Item acheté:** {item['name']}\n"
                        f"**Prix payé:** {tax_info.get('total_price', item['price']):,} {Emojis.MONEY}"
                    )
                    await send_func(embed=embed)
                    return
            
            # Récupérer le nouveau solde
            new_balance = await self.db.get_balance(user_id)
            
            # Message de confirmation avec détails des taxes
            embed = self.create_purchase_embed_with_tax(author, item, new_balance, tax_info, role_granted, role_name)
            await send_func(embed=embed)
            
            # Log de l'achat pour l'admin
            if tax_info.get('tax_amount', 0) > 0:
                logger.info(f"Achat: {author} a acheté '{item['name']}' pour {tax_info['total_price']} (base: {tax_info['base_price']}, taxe: {tax_info['tax_amount']})")
            
        except Exception as e:
            logger.error(f"Erreur buy {user_id} -> {item_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'achat.")
            await send_func(embed=embed)

    @commands.command(name='shopstats', aliases=['statsshop'])
    @commands.has_permissions(administrator=True)
    async def shop_stats_cmd(self, ctx):
        """[ADMIN] Affiche les statistiques du shop avec les taxes"""
        try:
            stats = await self.db.get_shop_stats()
            
            embed = discord.Embed(
                title="📊 Statistiques de la Boutique",
                color=Colors.INFO
            )
            
            # Statistiques générales
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
                value=f"**{stats['total_revenue']:,}** {Emojis.MONEY}", 
                inline=True
            )
            
            # Nouvelles statistiques de taxes
            embed.add_field(
                name=f"{Emojis.TAX} Taxes collectées", 
                value=f"**{stats['total_taxes']:,}** {Emojis.MONEY}", 
                inline=True
            )
            
            net_revenue = stats['total_revenue'] - stats['total_taxes']
            embed.add_field(
                name="💵 Revenus nets", 
                value=f"**{net_revenue:,}** {Emojis.MONEY}", 
                inline=True
            )
            
            embed.add_field(
                name="📈 Taux de taxe", 
                value=f"**{SHOP_TAX_RATE*100}%** sur tous les achats", 
                inline=True
            )
            
            # Top des items
            if stats['top_items']:
                top_text = ""
                for i, item in enumerate(stats['top_items'][:5], 1):
                    emoji = ["🥇", "🥈", "🥉", "🏅", "🏅"][i-1]
                    revenue = item['revenue'] or 0
                    taxes = item.get('taxes_collected', 0) or 0
                    top_text += f"{emoji} **{item['name']}** - {item['purchases']} vente(s)\n"
                    top_text += f"   💰 {revenue:,} revenus (dont {taxes:,} taxes)\n"
                
                embed.add_field(
                    name="🏆 Top des ventes",
                    value=top_text,
                    inline=False
                )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shopstats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération des statistiques.")
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Shop(bot))