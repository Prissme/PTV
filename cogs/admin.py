import discord
from discord.ext import commands
import logging

from config import Colors, Emojis, OWNER_ID
from utils.embeds import (
    create_success_embed, create_error_embed, create_shop_stats_embed,
    create_warning_embed, create_info_embed
)

logger = logging.getLogger(__name__)

class Admin(commands.Cog):
    """Commandes administrateur : gestion du shop, modération économique"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Admin initialisé")

    # ==================== GESTION SHOP ====================
    
    @commands.command(name='additem')
    @commands.has_permissions(administrator=True)
    async def additem_cmd(self, ctx, price: int, role_input: str, *, name: str):
        """[ADMIN] Ajoute un rôle au shop"""
        if price <= 0:
            embed = create_error_embed("Prix invalide", "Le prix doit être positif !")
            await ctx.send(embed=embed)
            return
        
        try:
            # Essayer de récupérer le rôle par ID ou mention
            role = None
            
            # Si c'est un ID numérique
            if role_input.isdigit():
                role = ctx.guild.get_role(int(role_input))
            # Si c'est une mention <@&ID>
            elif role_input.startswith('<@&') and role_input.endswith('>'):
                role_id = int(role_input[3:-1])
                role = ctx.guild.get_role(role_id)
            # Sinon essayer de trouver par nom
            else:
                role = discord.utils.get(ctx.guild.roles, name=role_input)
            
            if not role:
                embed = create_error_embed(
                    "Rôle introuvable",
                    f"**Rôle introuvable !**\n"
                    f"Utilisez l'une de ces méthodes :\n"
                    f"• `!additem {price} @RôleNom {name}`\n"
                    f"• `!additem {price} {role_input} {name}` (avec l'ID du rôle)\n"
                    f"• `!additem {price} \"Nom exact du rôle\" {name}`"
                )
                await ctx.send(embed=embed)
                return
            
            # Vérifier que le bot peut gérer ce rôle
            if role >= ctx.guild.me.top_role:
                embed = create_error_embed(
                    "Hiérarchie insuffisante",
                    f"Je ne peux pas gérer ce rôle !\n"
                    f"Le rôle {role.mention} est plus haut que mon rôle dans la hiérarchie."
                )
                await ctx.send(embed=embed)
                return
            
            # Vérifier si ce rôle existe déjà dans le shop
            existing_items = await self.db.get_shop_items(active_only=False)
            for item in existing_items:
                if item.get('data', {}).get('role_id') == role.id and item.get('is_active'):
                    embed = create_warning_embed(
                        "Rôle déjà présent",
                        f"Ce rôle est déjà dans la boutique !\n"
                        f"**Item existant :** {item['name']} (ID: {item['id']}) - {item['price']:,} PrissBucks"
                    )
                    await ctx.send(embed=embed)
                    return
            
            # Créer une description personnalisée
            description = f"🎭 Obtenez le rôle {role.mention} avec tous ses privilèges !"
            if "PREMIUM" in name.upper() or "VIP" in name.upper():
                description += "\n🌟 Statut premium sur le serveur !"
            if "PERM" in name.upper() and "VOC" in name.upper():
                description += "\n🎤 Inclut les permissions vocales spéciales !"
            if "BOURGEOIS" in name.upper():
                description += "\n💎 Statut de prestige sur le serveur !"
            
            # Ajouter à la base
            item_id = await self.db.add_shop_item(
                name=name,
                description=description,
                price=price,
                item_type="role",
                data={"role_id": role.id}
            )
            
            # Embed de confirmation
            embed = create_success_embed("Item ajouté au shop", f"L'item **{name}** a été ajouté avec succès !")
            embed.add_field(name="📛 Nom", value=name, inline=True)
            embed.add_field(name="💰 Prix", value=f"{price:,} PrissBucks", inline=True)
            embed.add_field(name="🎭 Rôle", value=f"{role.mention} (`{role.id}`)", inline=True)
            embed.add_field(name="🆔 Item ID", value=f"`{item_id}`", inline=True)
            embed.add_field(name="📝 Description", value=description, inline=False)
            embed.set_footer(text=f"Les utilisateurs peuvent maintenant acheter cet item avec !buy {item_id}")
            
            await ctx.send(embed=embed)
            logger.info(f"Item ajouté au shop: {name} (rôle {role.name}, prix {price}) par {ctx.author}")
            
        except ValueError:
            embed = create_error_embed("ID invalide", "ID de rôle invalide ! Utilisez un nombre valide.")
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Erreur additem: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'ajout de l'item.")
            await ctx.send(embed=embed)

    @commands.command(name='removeitem')
    @commands.has_permissions(administrator=True)
    async def removeitem_cmd(self, ctx, item_id: int):
        """[ADMIN] Retire un item du shop"""
        try:
            # Vérifier que l'item existe
            item = await self.db.get_shop_item(item_id)
            if not item:
                embed = create_error_embed("Item introuvable", "Cet item n'existe pas.")
                await ctx.send(embed=embed)
                return
            
            # Désactiver l'item
            success = await self.db.deactivate_shop_item(item_id)
            
            if success:
                embed = create_success_embed(
                    "Item retiré du shop",
                    f"**{item['name']}** n'est plus disponible à l'achat."
                )
                await ctx.send(embed=embed)
                logger.info(f"Item {item['name']} (ID: {item_id}) désactivé par {ctx.author}")
            else:
                embed = create_error_embed("Erreur", "Erreur lors de la suppression.")
                await ctx.send(embed=embed)
        
        except Exception as e:
            logger.error(f"Erreur removeitem: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la suppression de l'item.")
            await ctx.send(embed=embed)

    @commands.command(name='shopstats')
    @commands.has_permissions(administrator=True)
    async def shopstats_cmd(self, ctx):
        """[ADMIN] Affiche les statistiques du shop"""
        try:
            stats = await self.db.get_shop_stats()
            embed = create_shop_stats_embed(stats)
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur shopstats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage des statistiques.")
            await ctx.send(embed=embed)

    @commands.command(name='listshop')
    @commands.has_permissions(administrator=True)
    async def listshop_cmd(self, ctx):
        """[ADMIN] Liste tous les items du shop (actifs et inactifs)"""
        try:
            items = await self.db.get_shop_items(active_only=False)
            
            if not items:
                embed = create_warning_embed("Shop vide", "Aucun item dans la base de données.")
                await ctx.send(embed=embed)
                return
            
            embed = discord.Embed(
                title="📋 Liste complète des items",
                color=Colors.INFO
            )
            
            active_items = [item for item in items if item['is_active']]
            inactive_items = [item for item in items if not item['is_active']]
            
            # Items actifs
            if active_items:
                active_text = ""
                for item in active_items[:10]:  # Limite à 10 pour éviter les embeds trop longs
                    icon = Emojis.ROLE if item["type"] == "role" else Emojis.SHOP
                    active_text += f"{icon} `{item['id']}` **{item['name']}** - {item['price']:,} 💰\n"
                
                if len(active_items) > 10:
                    active_text += f"... et {len(active_items) - 10} autre(s)"
                
                embed.add_field(
                    name=f"✅ Items actifs ({len(active_items)})",
                    value=active_text,
                    inline=False
                )
            
            # Items inactifs
            if inactive_items:
                inactive_text = ""
                for item in inactive_items[:5]:  # Moins d'items inactifs affichés
                    icon = Emojis.ROLE if item["type"] == "role" else Emojis.SHOP
                    inactive_text += f"{icon} `{item['id']}` ~~{item['name']}~~ - {item['price']:,} 💰\n"
                
                if len(inactive_items) > 5:
                    inactive_text += f"... et {len(inactive_items) - 5} autre(s)"
                
                embed.add_field(
                    name=f"❌ Items inactifs ({len(inactive_items)})",
                    value=inactive_text,
                    inline=False
                )
            
            embed.set_footer(text="Utilisez !removeitem <id> pour désactiver un item")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur listshop: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage de la liste.")
            await ctx.send(embed=embed)

    # ==================== GESTION ÉCONOMIQUE ====================
    
    @commands.command(name='addmoney', aliases=['addbal'])
    @commands.is_owner()
    async def addmoney_cmd(self, ctx, member: discord.Member, amount: int):
        """[OWNER] Ajoute des pièces à un utilisateur"""
        try:
            await self.db.update_balance(member.id, amount)
            embed = create_success_embed(
                "Argent ajouté",
                f"**{amount:,}** PrissBucks ajoutées à **{member.display_name}**"
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await ctx.send(embed=embed)
            logger.info(f"{amount} PrissBucks ajoutées à {member} par {ctx.author}")
            
        except Exception as e:
            logger.error(f"Erreur addmoney: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'ajout d'argent.")
            await ctx.send(embed=embed)

    @commands.command(name='setmoney', aliases=['setbal'])
    @commands.is_owner()
    async def setmoney_cmd(self, ctx, member: discord.Member, amount: int):
        """[OWNER] Définit le solde exact d'un utilisateur"""
        try:
            old_balance = await self.db.get_balance(member.id)
            await self.db.set_balance(member.id, amount)
            
            embed = create_success_embed(
                "Solde défini",
                f"Solde de **{member.display_name}** défini à **{amount:,}** PrissBucks"
            )
            embed.add_field(
                name="📊 Changement",
                value=f"Ancien solde : {old_balance:,}\nNouveau solde : {amount:,}",
                inline=False
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await ctx.send(embed=embed)
            logger.info(f"Solde de {member} défini à {amount} par {ctx.author} (ancien: {old_balance})")
            
        except Exception as e:
            logger.error(f"Erreur setmoney: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la définition du solde.")
            await ctx.send(embed=embed)

    @commands.command(name='removemoney', aliases=['takebal'])
    @commands.is_owner()
    async def removemoney_cmd(self, ctx, member: discord.Member, amount: int):
        """[OWNER] Retire des pièces à un utilisateur"""
        if amount <= 0:
            embed = create_error_embed("Montant invalide", "Le montant doit être positif !")
            await ctx.send(embed=embed)
            return
            
        try:
            old_balance = await self.db.get_balance(member.id)
            if old_balance < amount:
                embed = create_warning_embed(
                    "Solde insuffisant",
                    f"**{member.display_name}** n'a que {old_balance:,} PrissBucks.\n"
                    f"Le solde sera mis à 0."
                )
                amount = old_balance
                
            await self.db.update_balance(member.id, -amount)
            new_balance = old_balance - amount
            
            embed = create_success_embed(
                "Argent retiré",
                f"**{amount:,}** PrissBucks retirées à **{member.display_name}**"
            )
            embed.add_field(
                name="📊 Nouveau solde",
                value=f"{new_balance:,} PrissBucks",
                inline=True
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await ctx.send(embed=embed)
            logger.info(f"{amount} PrissBucks retirées à {member} par {ctx.author}")
            
        except Exception as e:
            logger.error(f"Erreur removemoney: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du retrait d'argent.")
            await ctx.send(embed=embed)

    @commands.command(name='resetuser', aliases=['resetbal'])
    @commands.is_owner()
    async def resetuser_cmd(self, ctx, member: discord.Member):
        """[OWNER] Remet à zéro complètement un utilisateur"""
        try:
            old_balance = await self.db.get_balance(member.id)
            
            # Confirmation
            embed = create_warning_embed(
                "Confirmation requise",
                f"Êtes-vous sûr de vouloir remettre à zéro **{member.display_name}** ?\n\n"
                f"**Solde actuel :** {old_balance:,} PrissBucks\n"
                f"**Daily :** Sera réinitialisé\n\n"
                f"Réagissez avec ✅ pour confirmer ou ❌ pour annuler."
            )
            msg = await ctx.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            
            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == msg.id
            
            try:
                reaction, _ = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
                
                if str(reaction.emoji) == "✅":
                    # Effectuer le reset
                    await self.db.set_balance(member.id, 0)
                    await self.db.set_last_daily(member.id, None)
                    
                    embed = create_success_embed(
                        "Utilisateur réinitialisé",
                        f"**{member.display_name}** a été complètement remis à zéro."
                    )
                    embed.add_field(
                        name="📊 Ancien solde",
                        value=f"{old_balance:,} PrissBucks",
                        inline=True
                    )
                    await msg.edit(embed=embed)
                    logger.info(f"Utilisateur {member} réinitialisé par {ctx.author}")
                else:
                    embed = create_info_embed("Annulé", "Réinitialisation annulée.")
                    await msg.edit(embed=embed)
                    
            except Exception:
                embed = create_warning_embed("Timeout", "Temps écoulé, réinitialisation annulée.")
                await msg.edit(embed=embed)
                
        except Exception as e:
            logger.error(f"Erreur resetuser: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la réinitialisation.")
            await ctx.send(embed=embed)

    # ==================== STATISTIQUES AVANCÉES ====================
    
    @commands.command(name='economystats', aliases=['ecostats'])
    @commands.has_permissions(administrator=True)
    async def economystats_cmd(self, ctx):
        """[ADMIN] Statistiques complètes de l'économie"""
        try:
            # Récupérer les statistiques
            async with self.db.pool.acquire() as conn:
                # Stats générales
                total_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE balance > 0")
                total_money = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
                avg_balance = await conn.fetchval("SELECT COALESCE(AVG(balance), 0) FROM users WHERE balance > 0")
                
                # Daily stats
                daily_users = await conn.fetchval("""
                    SELECT COUNT(*) FROM users 
                    WHERE last_daily > NOW() - INTERVAL '7 days'
                """)
                
                # Top richest
                richest = await conn.fetchrow("""
                    SELECT user_id, balance FROM users 
                    ORDER BY balance DESC LIMIT 1
                """)
            
            embed = discord.Embed(
                title="📈 Statistiques Économiques",
                color=Colors.INFO
            )
            
            embed.add_field(
                name="👥 Utilisateurs actifs",
                value=f"**{total_users:,}** utilisateurs",
                inline=True
            )
            
            embed.add_field(
                name="💰 Argent total",
                value=f"**{total_money:,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="📊 Solde moyen",
                value=f"**{int(avg_balance):,}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="🎰 Daily actifs (7j)",
                value=f"**{daily_users:,}** utilisateurs",
                inline=True
            )
            
            if richest:
                try:
                    richest_user = self.bot.get_user(richest['user_id'])
                    richest_name = richest_user.display_name if richest_user else f"User {richest['user_id']}"
                    embed.add_field(
                        name="👑 Plus riche",
                        value=f"**{richest_name}**\n{richest['balance']:,} PrissBucks",
                        inline=True
                    )
                except:
                    embed.add_field(
                        name="👑 Plus riche",
                        value=f"**User {richest['user_id']}**\n{richest['balance']:,} PrissBucks",
                        inline=True
                    )
            
            # Stats du shop
            shop_stats = await self.db.get_shop_stats()
            embed.add_field(
                name="🛍️ Revenus boutique",
                value=f"**{shop_stats['total_revenue']:,}** PrissBucks",
                inline=True
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur economystats: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage des statistiques.")
            await ctx.send(embed=embed)

    @commands.command(name='userinfo')
    @commands.has_permissions(administrator=True)
    async def userinfo_cmd(self, ctx, member: discord.Member):
        """[ADMIN] Informations détaillées sur un utilisateur"""
        try:
            # Récupérer toutes les infos
            balance = await self.db.get_balance(member.id)
            last_daily = await self.db.get_last_daily(member.id)
            purchases = await self.db.get_user_purchases(member.id)
            
            embed = discord.Embed(
                title=f"🔍 Informations détaillées",
                description=f"**Utilisateur :** {member.mention}",
                color=Colors.INFO
            )
            
            embed.add_field(
                name="💰 Solde",
                value=f"{balance:,} PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="🛒 Achats",
                value=f"{len(purchases)} item(s)",
                inline=True
            )
            
            total_spent = sum(purchase['price_paid'] for purchase in purchases)
            embed.add_field(
                name="💸 Total dépensé",
                value=f"{total_spent:,} PrissBucks",
                inline=True
            )
            
            if last_daily:
                daily_timestamp = int(last_daily.timestamp())
                embed.add_field(
                    name="🎰 Dernier daily",
                    value=f"<t:{daily_timestamp}:R>",
                    inline=True
                )
            else:
                embed.add_field(
                    name="🎰 Dernier daily",
                    value="Jamais utilisé",
                    inline=True
                )
            
            embed.add_field(
                name="📅 Compte créé",
                value=f"<t:{int(member.created_at.timestamp())}:d>",
                inline=True
            )
            
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"ID: {member.id}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur userinfo: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage des informations.")
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Admin(bot))
