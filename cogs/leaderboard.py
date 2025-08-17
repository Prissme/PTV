import discord
from discord.ext import commands
from discord import app_commands
import logging

from config import Colors, MAX_LEADERBOARD_LIMIT, DEFAULT_LEADERBOARD_LIMIT
from utils.embeds import create_error_embed, create_success_embed

logger = logging.getLogger(__name__)

class Leaderboard(commands.Cog):
    """Système de classement des PrissBucks complet"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        self.db = self.bot.database
        logger.info("✅ Cog Leaderboard initialisé avec slash commands")

    # ==================== LEADERBOARD COMMANDS ====================

    @commands.command(name='leaderboard', aliases=['top', 'lb', 'rich', 'classement'])
    async def leaderboard_cmd(self, ctx, limit: int = DEFAULT_LEADERBOARD_LIMIT):
        """e!leaderboard [limite] - Affiche le classement des plus riches"""
        await self._execute_leaderboard(ctx, limit)

    @app_commands.command(name="leaderboard", description="Affiche le classement des utilisateurs les plus riches")
    @app_commands.describe(limit="Nombre d'utilisateurs à afficher (max 20)")
    async def leaderboard_slash(self, interaction: discord.Interaction, limit: int = DEFAULT_LEADERBOARD_LIMIT):
        """/leaderboard [limit] - Affiche le classement"""
        await interaction.response.defer()
        await self._execute_leaderboard(interaction, limit, is_slash=True)

    async def _execute_leaderboard(self, ctx_or_interaction, limit=DEFAULT_LEADERBOARD_LIMIT, is_slash=False):
        """Logique commune pour leaderboard (prefix et slash)"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
        else:
            send_func = ctx_or_interaction.send
        
        # Validation de la limite
        if limit < 1:
            limit = DEFAULT_LEADERBOARD_LIMIT
        elif limit > MAX_LEADERBOARD_LIMIT:
            limit = MAX_LEADERBOARD_LIMIT
        
        try:
            # Récupérer les top utilisateurs
            top_users = await self.db.get_top_users(limit)
            
            if not top_users:
                embed = create_error_embed(
                    "Classement vide", 
                    "Aucun utilisateur n'a encore de PrissBucks."
                )
                await send_func(embed=embed)
                return
            
            # Créer l'embed ultra clean
            embed = discord.Embed(
                title="🏆 Classement PrissBucks",
                color=Colors.GOLD
            )
            
            # Construire la description avec le classement
            description = ""
            for i, (user_id, balance) in enumerate(top_users, 1):
                try:
                    user = self.bot.get_user(user_id)
                    if user:
                        username = user.display_name
                    else:
                        # Fallback si l'utilisateur n'est pas en cache
                        try:
                            user = await self.bot.fetch_user(user_id)
                            username = user.display_name
                        except:
                            username = f"Utilisateur {user_id}"
                except:
                    username = f"Utilisateur {user_id}"
                
                # Emoji selon la position
                if i == 1:
                    emoji = "🥇"
                elif i == 2:
                    emoji = "🥈"
                elif i == 3:
                    emoji = "🥉"
                else:
                    emoji = f"`{i:2d}.`"
                
                # Format simple et clean
                description += f"{emoji} **{username}** — {balance:,} PrissBucks\n"
            
            embed.description = description
            
            # Footer minimaliste
            embed.set_footer(text=f"Top {len(top_users)} utilisateurs")
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur leaderboard: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage du classement.")
            await send_func(embed=embed)

    # ==================== RANK COMMANDS ====================

    @commands.command(name='rank', aliases=['rang', 'position'])
    async def rank_cmd(self, ctx, user: discord.Member = None):
        """e!rank [@utilisateur] - Affiche le rang d'un utilisateur dans le classement"""
        await self._execute_rank(ctx, user)

    @app_commands.command(name="rank", description="Affiche le rang d'un utilisateur dans le classement")
    @app_commands.describe(utilisateur="L'utilisateur dont voir le rang (optionnel)")
    async def rank_slash(self, interaction: discord.Interaction, utilisateur: discord.Member = None):
        """/rank [utilisateur] - Affiche le rang"""
        await interaction.response.defer()
        await self._execute_rank(interaction, utilisateur, is_slash=True)

    async def _execute_rank(self, ctx_or_interaction, user=None, is_slash=False):
        """Logique commune pour rank (prefix et slash)"""
        if is_slash:
            target = user or ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            target = user or ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        try:
            # Récupérer tous les utilisateurs pour calculer le rang
            all_users = await self.db.get_top_users(1000)  # Limite élevée pour avoir tout le monde
            
            if not all_users:
                embed = create_error_embed(
                    "Aucun classement",
                    "Aucun utilisateur n'a encore de PrissBucks."
                )
                await send_func(embed=embed)
                return
            
            # Trouver la position de l'utilisateur
            user_rank = None
            user_balance = 0
            
            for i, (user_id, balance) in enumerate(all_users, 1):
                if user_id == target.id:
                    user_rank = i
                    user_balance = balance
                    break
            
            # Si l'utilisateur n'est pas dans le classement
            if user_rank is None:
                user_balance = await self.db.get_balance(target.id)
                if user_balance == 0:
                    embed = discord.Embed(
                        title=f"📊 Rang de {target.display_name}",
                        description=f"**Position :** Non classé\n**Solde :** 0 PrissBucks",
                        color=Colors.WARNING
                    )
                else:
                    # Cas rare où l'utilisateur a des PrissBucks mais n'apparaît pas dans le top
                    embed = discord.Embed(
                        title=f"📊 Rang de {target.display_name}",
                        description=f"**Position :** 1000+\n**Solde :** {user_balance:,} PrissBucks",
                        color=Colors.INFO
                    )
            else:
                # Afficher le rang avec emoji selon la position
                if user_rank == 1:
                    rank_emoji = "🥇"
                    color = Colors.GOLD
                elif user_rank == 2:
                    rank_emoji = "🥈"
                    color = Colors.INFO
                elif user_rank == 3:
                    rank_emoji = "🥉"
                    color = Colors.WARNING
                elif user_rank <= 10:
                    rank_emoji = "🏆"
                    color = Colors.SUCCESS
                else:
                    rank_emoji = "📊"
                    color = Colors.INFO
                    
                embed = discord.Embed(
                    title=f"{rank_emoji} Rang de {target.display_name}",
                    description=f"**Position :** #{user_rank}\n**Solde :** {user_balance:,} PrissBucks",
                    color=color
                )
            
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.set_footer(text="Utilise '/leaderboard' ou 'e!leaderboard' pour voir le classement complet")
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur rank pour {target.id}: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération du rang.")
            await send_func(embed=embed)

    # ==================== UTILITY COMMANDS ====================

    @commands.command(name='richest')
    async def richest_cmd(self, ctx):
        """e!richest - Affiche l'utilisateur le plus riche"""
        try:
            top_users = await self.db.get_top_users(1)
            
            if not top_users:
                embed = create_error_embed("Aucun utilisateur", "Personne n'a encore de PrissBucks.")
                await ctx.send(embed=embed)
                return
                
            user_id, balance = top_users[0]
            
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                username = user.display_name
                avatar_url = user.display_avatar.url
            except:
                username = f"Utilisateur {user_id}"
                avatar_url = None
            
            embed = discord.Embed(
                title="🥇 Utilisateur le plus riche",
                description=f"**{username}** détient **{balance:,}** PrissBucks !",
                color=Colors.GOLD
            )
            
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
                
            embed.set_footer(text="Félicitations pour cette fortune !")
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur richest: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la recherche de l'utilisateur le plus riche.")
            await ctx.send(embed=embed)

    @app_commands.command(name="richest", description="Affiche l'utilisateur le plus riche")
    async def richest_slash(self, interaction: discord.Interaction):
        """/richest - Affiche l'utilisateur le plus riche"""
        await interaction.response.defer()
        
        try:
            top_users = await self.db.get_top_users(1)
            
            if not top_users:
                embed = create_error_embed("Aucun utilisateur", "Personne n'a encore de PrissBucks.")
                await interaction.followup.send(embed=embed)
                return
                
            user_id, balance = top_users[0]
            
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                username = user.display_name
                avatar_url = user.display_avatar.url
            except:
                username = f"Utilisateur {user_id}"
                avatar_url = None
            
            embed = discord.Embed(
                title="🥇 Utilisateur le plus riche",
                description=f"**{username}** détient **{balance:,}** PrissBucks !",
                color=Colors.GOLD
            )
            
            if avatar_url:
                embed.set_thumbnail(url=avatar_url)
                
            embed.set_footer(text="Félicitations pour cette fortune !")
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur richest: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la recherche de l'utilisateur le plus riche.")
            await interaction.followup.send(embed=embed)

    @commands.command(name='poorest')
    async def poorest_cmd(self, ctx):
        """e!poorest - Affiche les utilisateurs avec le moins de PrissBucks"""
        try:
            # Récupérer tous les utilisateurs et les trier par balance croissante
            all_users = await self.db.get_top_users(1000)
            
            if not all_users:
                embed = create_error_embed("Aucun utilisateur", "Personne n'a encore de PrissBucks.")
                await ctx.send(embed=embed)
                return
            
            # Prendre les 5 plus pauvres (en ordre inverse)
            poorest_users = sorted(all_users, key=lambda x: x[1])[:5]
            
            embed = discord.Embed(
                title="💸 Utilisateurs les moins riches",
                color=Colors.WARNING
            )
            
            description = ""
            for i, (user_id, balance) in enumerate(poorest_users, 1):
                try:
                    user = self.bot.get_user(user_id)
                    if user:
                        username = user.display_name
                    else:
                        user = await self.bot.fetch_user(user_id)
                        username = user.display_name
                except:
                    username = f"Utilisateur {user_id}"
                
                description += f"`{i:2d}.` **{username}** — {balance:,} PrissBucks\n"
            
            embed.description = description
            embed.set_footer(text="Aidez-les à s'enrichir ! 💰")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur poorest: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la recherche des utilisateurs les moins riches.")
            await ctx.send(embed=embed)

    @app_commands.command(name="poorest", description="Affiche les utilisateurs avec le moins de PrissBucks")
    async def poorest_slash(self, interaction: discord.Interaction):
        """/poorest - Affiche les utilisateurs les moins riches"""
        await interaction.response.defer()
        
        try:
            # Récupérer tous les utilisateurs et les trier par balance croissante
            all_users = await self.db.get_top_users(1000)
            
            if not all_users:
                embed = create_error_embed("Aucun utilisateur", "Personne n'a encore de PrissBucks.")
                await interaction.followup.send(embed=embed)
                return
            
            # Prendre les 5 plus pauvres (en ordre inverse)
            poorest_users = sorted(all_users, key=lambda x: x[1])[:5]
            
            embed = discord.Embed(
                title="💸 Utilisateurs les moins riches",
                color=Colors.WARNING
            )
            
            description = ""
            for i, (user_id, balance) in enumerate(poorest_users, 1):
                try:
                    user = self.bot.get_user(user_id)
                    if user:
                        username = user.display_name
                    else:
                        user = await self.bot.fetch_user(user_id)
                        username = user.display_name
                except:
                    username = f"Utilisateur {user_id}"
                
                description += f"`{i:2d}.` **{username}** — {balance:,} PrissBucks\n"
            
            embed.description = description
            embed.set_footer(text="Aidez-les à s'enrichir ! 💰")
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur poorest: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la recherche des utilisateurs les moins riches.")
            await interaction.followup.send(embed=embed)

    # ==================== COMMANDE TOTAL PRISSBUCKS ====================

    @commands.command(name='total', aliases=['totalpb', 'totalserver'])
    async def total_prissbucks_cmd(self, ctx):
        """e!total - Affiche le nombre total de PrissBucks sur le serveur"""
        await self._execute_total_prissbucks(ctx)

    @app_commands.command(name="total", description="Affiche le nombre total de PrissBucks circulant sur le serveur")
    async def total_prissbucks_slash(self, interaction: discord.Interaction):
        """/total - Affiche le total des PrissBucks du serveur"""
        await interaction.response.defer()
        await self._execute_total_prissbucks(interaction, is_slash=True)

    async def _execute_total_prissbucks(self, ctx_or_interaction, is_slash=False):
        """Logique commune pour afficher le total des PrissBucks"""
        if is_slash:
            send_func = ctx_or_interaction.followup.send
        else:
            send_func = ctx_or_interaction.send
        
        try:
            # Récupérer tous les utilisateurs (soldes principaux)
            all_users = await self.db.get_top_users(10000)  # Grande limite pour récupérer tout le monde
            
            # Récupérer les soldes bancaires si disponibles
            total_bank_balance = 0
            bank_users_count = 0
            try:
                if hasattr(self.bot, 'database') and self.bot.database.pool:
                    async with self.bot.database.pool.acquire() as conn:
                        bank_result = await conn.fetchrow("""
                            SELECT COUNT(*) as users, COALESCE(SUM(balance), 0) as total_balance 
                            FROM user_bank WHERE balance > 0
                        """)
                        if bank_result:
                            total_bank_balance = bank_result['total_balance']
                            bank_users_count = bank_result['users']
            except Exception as e:
                logger.debug(f"Impossible de récupérer les soldes bancaires: {e}")
            
            # Récupérer la banque publique si disponible
            public_bank_balance = 0
            try:
                public_bank_cog = self.bot.get_cog('PublicBank')
                if public_bank_cog:
                    bank_info = await public_bank_cog.get_public_bank_balance()
                    public_bank_balance = bank_info.get('balance', 0)
            except Exception as e:
                logger.debug(f"Impossible de récupérer la banque publique: {e}")
            
            # Calculer les totaux
            main_balance_total = sum(balance for _, balance in all_users) if all_users else 0
            total_prissbucks = main_balance_total + total_bank_balance + public_bank_balance
            
            if total_prissbucks == 0:
                embed = discord.Embed(
                    title="💰 Total PrissBucks du Serveur",
                    description="**0** PrissBucks en circulation\n\nAucun utilisateur n'a encore de PrissBucks !",
                    color=Colors.WARNING
                )
                embed.add_field(
                    name="💡 Comment obtenir des PrissBucks ?",
                    value="• Utilise `e!daily` ou `/daily` pour commencer\n"
                          "• Écris des messages (récompenses automatiques)\n"
                          "• Joue aux mini-jeux pour gagner plus !",
                    inline=False
                )
                await send_func(embed=embed)
                return
            
            # Statistiques utilisateurs
            user_count = len(all_users) if all_users else 0
            average_per_user = main_balance_total / user_count if user_count > 0 else 0
            
            # Trouver l'utilisateur le plus riche (total main + banque)
            richest_user_id, richest_balance = all_users[0] if all_users else (None, 0)
            try:
                if richest_user_id:
                    richest_user = self.bot.get_user(richest_user_id)
                    richest_name = richest_user.display_name if richest_user else f"Utilisateur {richest_user_id}"
                else:
                    richest_name = "Aucun"
                    richest_balance = 0
            except:
                richest_name = f"Utilisateur {richest_user_id}" if richest_user_id else "Aucun"
            
            # Créer l'embed avec les statistiques complètes
            embed = discord.Embed(
                title="💰 Total PrissBucks du Serveur",
                description=f"**{total_prissbucks:,}** PrissBucks en circulation !",
                color=Colors.GOLD
            )
            
            # Répartition par types de comptes
            embed.add_field(
                name="💳 Comptes principaux",
                value=f"**{main_balance_total:,}** PB\n({user_count} utilisateur{'s' if user_count > 1 else ''})",
                inline=True
            )
            
            embed.add_field(
                name="🏦 Banques privées",
                value=f"**{total_bank_balance:,}** PB\n({bank_users_count} compte{'s' if bank_users_count > 1 else ''})",
                inline=True
            )
            
            embed.add_field(
                name="🏛️ Banque publique",
                value=f"**{public_bank_balance:,}** PB\n(communautaire)",
                inline=True
            )
            
            embed.add_field(
                name="📊 Moyenne (comptes principaux)",
                value=f"**{average_per_user:,.0f}** PrissBucks",
                inline=True
            )
            
            embed.add_field(
                name="👑 Plus riche (principal)",
                value=f"**{richest_name}**\n{richest_balance:,} PrissBucks",
                inline=True
            )
            
            # Répartition économique
            main_pct = (main_balance_total / total_prissbucks * 100) if total_prissbucks > 0 else 0
            bank_pct = (total_bank_balance / total_prissbucks * 100) if total_prissbucks > 0 else 0
            public_pct = (public_bank_balance / total_prissbucks * 100) if total_prissbucks > 0 else 0
            
            embed.add_field(
                name="📊 Répartition économique",
                value=f"💳 **{main_pct:.1f}%** en circulation libre\n"
                      f"🏦 **{bank_pct:.1f}%** épargné (banques privées)\n"
                      f"🏛️ **{public_pct:.1f}%** solidaire (banque publique)",
                inline=False
            )
            
            # Répartition par tranches (basé sur les comptes principaux)
            if all_users:
                ranges = [
                    (0, 100, "🔴"),
                    (100, 1000, "🟠"),
                    (1000, 10000, "🟡"),
                    (10000, 100000, "🟢"),
                    (100000, float('inf'), "🔵")
                ]
                
                range_counts = {emoji: 0 for _, _, emoji in ranges}
                for _, balance in all_users:
                    for min_val, max_val, emoji in ranges:
                        if min_val <= balance < max_val:
                            range_counts[emoji] += 1
                            break
                
                range_text = ""
                for min_val, max_val, emoji in ranges:
                    count = range_counts[emoji]
                    if count > 0:
                        if max_val == float('inf'):
                            range_text += f"{emoji} **{count}** users ≥ {min_val:,} PB\n"
                        else:
                            range_text += f"{emoji} **{count}** users ({min_val:,}-{max_val-1:,} PB)\n"
                
                if range_text:
                    embed.add_field(
                        name="📈 Répartition des richesses (comptes principaux)",
                        value=range_text,
                        inline=True
                    )
                
                # Pourcentage de concentration
                top_10_total = sum(balance for _, balance in all_users[:10])
                concentration_pct = (top_10_total / main_balance_total * 100) if main_balance_total > 0 else 0
                
                embed.add_field(
                    name="🎯 Concentration (comptes principaux)",
                    value=f"Le **top 10** possède **{concentration_pct:.1f}%**\ndes comptes principaux",
                    inline=True
                )
            
            # Info sur l'économie globale
            embed.add_field(
                name="🌍 Économie globale du serveur",
                value=f"• **Total en circulation:** {total_prissbucks:,} PB\n"
                      f"• **Utilisateurs actifs:** {user_count} (comptes principaux)\n"
                      f"• **Comptes bancaires:** {bank_users_count} (épargne privée)\n"
                      f"• **Système solidaire:** {public_bank_balance:,} PB disponibles à tous\n"
                      f"• **Liquidités:** {main_pct:.1f}% immédiatement disponibles",
                inline=False
            )
            
            embed.set_footer(text="Données complètes en temps réel • Comptes principaux + banques privées + banque publique")
            
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur total prissbucks: {e}")
            embed = create_error_embed("Erreur", "Erreur lors du calcul du total des PrissBucks.")
            await send_func(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    await bot.add_cog(Leaderboard(bot))