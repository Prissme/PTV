import discord
from discord.ext import commands
import math
import logging

from config import ITEMS_PER_PAGE, Colors, Emojis
from utils.embeds import (
    create_shop_embed, create_purchase_embed, create_inventory_embed,
    create_error_embed, create_warning_embed
)

logger = logging.getLogger(__name__)

class Shop(commands.Cog):
    """Système boutique : shop, buy, inventory"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Shop initialisé")

    @commands.command(name='shop', aliases=['boutique', 'store'])
    async def shop_cmd(self, ctx, page: int = 1):
        """Affiche la boutique avec pagination"""
        try:
            items = await self.db.get_shop_items(active_only=True)
            
            if not items:
                embed = create_warning_embed(
                    "Boutique vide",
                    "La boutique est vide pour le moment. Revenez plus tard !"
                )
                await ctx.send(embed=embed)
                return
            
            # Pagination
            total_pages = math.ceil(len(items) / ITEMS_PER_PAGE)
            
            if page < 1 or page > total_pages:
                embed = create_error_embed(
                    "Page invalide",
                    f"Utilise une page entre 1 et {total_pages}."
                )
                await ctx.send(embed=embed)
                return
            
            # Récupérer les items de la page
            start_idx = (page - 1) * ITEMS_PER_PAGE
            end_idx = start_idx + ITEMS_PER_PAGE
            page_items = items[start_idx:end_idx]
            
            # Créer l'embed
            embed = create_shop_embed(page_items, page, total_pages)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shop: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage de la boutique.")
            await ctx.send(embed=embed)

    @commands.command(name='buy', aliases=['acheter', 'purchase'])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def buy_cmd(self, ctx, item_id: int):
        """Achète un item du shop"""
        user_id = ctx.author.id
        
        try:
            # Récupérer les infos de l'item
            item = await self.db.get_shop_item(item_id)
            if not item or not item["is_active"]:
                embed = create_error_embed(
                    "Item introuvable",
                    "Cet item n'existe pas ou n'est plus disponible."
                )
                await ctx.send(embed=embed)
                return
            
            # Effectuer l'achat (transaction atomique)
            success, message = await self.db.purchase_item(user_id, item_id)
            
            if not success:
                embed = create_error_embed("Achat échoué", message)
                await ctx.send(embed=embed)
                return
            
            # Si c'est un rôle, l'attribuer
            role_granted = False
            role_name = None
            
            if item["type"] == "role":
                try:
                    role_id = item["data"].get("role_id")
                    if role_id:
                        role = ctx.guild.get_role(int(role_id))
                        if role:
                            await ctx.author.add_roles(role)
                            role_granted = True
                            role_name = role.name
                            logger.info(f"Rôle {role.name} attribué à {ctx.author} (achat item {item_id})")
                        else:
                            embed = create_warning_embed(
                                "Achat réussi mais...",
                                f"L'item a été acheté mais le rôle est introuvable. Contacte un administrateur.\n\n**Item acheté :** {item['name']}\n**Prix payé :** {item['price']:,} PrissBucks"
                            )
                            await ctx.send(embed=embed)
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
                    await ctx.send(embed=embed)
                    return
            
            # Récupérer le nouveau solde
            new_balance = await self.db.get_balance(user_id)
            
            # Message de confirmation
            embed = create_purchase_embed(ctx.author, item, new_balance, role_granted, role_name)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur buy {user_id} -> {item_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'achat.")
            await ctx.send(embed=embed)

    @commands.command(name='inventory', aliases=['inv', 'mes-achats'])
    async def inventory_cmd(self, ctx, member: discord.Member = None):
        """Affiche les achats d'un utilisateur"""
        target = member or ctx.author
        
        try:
            purchases = await self.db.get_user_purchases(target.id)
            embed = create_inventory_embed(target, purchases)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur inventory {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage de l'inventaire.")
            await ctx.send(embed=embed)

    @commands.command(name='iteminfo', aliases=['info-item'])
    async def iteminfo_cmd(self, ctx, item_id: int):
        """Affiche les détails d'un item du shop"""
        try:
            item = await self.db.get_shop_item(item_id)
            
            if not item:
                embed = create_error_embed("Item introuvable", f"Aucun item avec l'ID {item_id}.")
                await ctx.send(embed=embed)
                return
            
            # Créer l'embed d'information
            embed = discord.Embed(
                title=f"ℹ️ Informations sur l'item",
                color=Colors.INFO
            )
            
            # Icône selon le type
            icon = Emojis.ROLE if item["type"] == "role" else Emojis.SHOP
            
            embed.add_field(name="📛 Nom", value=f"{icon} **{item['name']}**", inline=True)
            embed.add_field(name="🆔 ID", value=f"`{item['id']}`", inline=True)
            embed.add_field(name="💰 Prix", value=f"**{item['price']:,}** PrissBucks", inline=True)
            
            embed.add_field(name="📝 Description", value=item['description'], inline=False)
            embed.add_field(name="📊 Statut", value="✅ Disponible" if item['is_active'] else "❌ Indisponible", inline=True)
            embed.add_field(name="🏷️ Type", value=item['type'].capitalize(), inline=True)
            
            # Informations spécifiques au type
            if item["type"] == "role" and item.get("data", {}).get("role_id"):
                role_id = item["data"]["role_id"]
                role = ctx.guild.get_role(int(role_id))
                if role:
                    embed.add_field(name="🎭 Rôle Discord", value=f"{role.mention} (`{role.id}`)", inline=True)
                else:
                    embed.add_field(name="🎭 Rôle Discord", value=f"⚠️ Rôle introuvable (`{role_id}`)", inline=True)
            
            # Date de création
            if item.get('created_at'):
                created_timestamp = int(item['created_at'].timestamp())
                embed.add_field(name="📅 Ajouté le", value=f"<t:{created_timestamp}:d>", inline=True)
            
            # Statistiques d'achat
            try:
                async with self.db.pool.acquire() as conn:
                    purchase_count = await conn.fetchval(
                        "SELECT COUNT(*) FROM user_purchases WHERE item_id = $1", item_id
                    )
                embed.add_field(name="📈 Nombre d'achats", value=f"**{purchase_count}** fois", inline=True)
            except:
                pass  # Ignorer si erreur
            
            embed.set_footer(text=f"Utilisez !buy {item_id} pour acheter cet item")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur iteminfo {item_id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage des informations.")
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Shop(bot))
