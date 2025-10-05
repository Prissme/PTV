import discord
from discord.ext import commands
from discord import app_commands
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Dict

from config import Colors, Emojis, PREFIX
from utils.embeds import create_error_embed, create_success_embed, create_info_embed

logger = logging.getLogger(__name__)

class XPSystem(commands.Cog):
    """Système d'XP avec rôles de boost progressifs"""
    
    def __init__(self, bot):
        self.bot = bot
        self.db = None
        
        # Configuration des rôles XP et leurs boosts
        self.XP_ROLES = {
            "E": {"price": 5000, "boost": 0.05, "emoji": "🔰", "name": "Rang E"},
            "D": {"price": 10000, "boost": 0.10, "emoji": "⚡", "name": "Rang D"},
            "C": {"price": 20000, "boost": 0.20, "emoji": "💎", "name": "Rang C"},
            "B": {"price": 30000, "boost": 0.30, "emoji": "🌟", "name": "Rang B"},
            "A": {"price": 50000, "boost": 0.50, "emoji": "👑", "name": "Rang A"},
            "S": {"price": 70000, "boost": 0.70, "emoji": "🔥", "name": "Rang S"},
            "SS": {"price": 85000, "boost": 0.85, "emoji": "⚔️", "name": "Rang SS"},
            "SSS": {"price": 100000, "boost": 1.00, "emoji": "🏆", "name": "Rang SSS"}
        }
        
        # XP de base par message
        self.BASE_XP = 10
        self.XP_COOLDOWN = 60  # 1 minute entre gains XP
        
        # Cache des cooldowns XP
        self._xp_cooldowns = {}
        
    async def cog_load(self):
        """Initialisation du cog"""
        self.db = self.bot.database
        await self._create_xp_tables()
        logger.info("✅ Cog XP System initialisé avec rôles boost")
    
    async def _create_xp_tables(self):
        """Crée les tables XP"""
        if not self.db or not self.db.pool:
            return
        
        try:
            async with self.db.pool.acquire() as conn:
                # Table XP utilisateurs
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_xp (
                        user_id BIGINT PRIMARY KEY,
                        xp BIGINT DEFAULT 0,
                        level INTEGER DEFAULT 1,
                        total_xp BIGINT DEFAULT 0,
                        xp_boost_role VARCHAR(10),
                        last_xp_gain TIMESTAMP WITH TIME ZONE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                ''')
                
                # Index pour performances
                await conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_user_xp_level ON user_xp(level DESC)
                ''')
                
                await conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_user_xp_total ON user_xp(total_xp DESC)
                ''')
                
                logger.info("✅ Tables XP créées/vérifiées")
        except Exception as e:
            logger.error(f"Erreur création tables XP: {e}")
    
    def _check_xp_cooldown(self, user_id: int) -> float:
        """Vérifie le cooldown XP"""
        now = time.time()
        if user_id in self._xp_cooldowns:
            elapsed = now - self._xp_cooldowns[user_id]
            if elapsed < self.XP_COOLDOWN:
                return self.XP_COOLDOWN - elapsed
        return 0
    
    def _set_xp_cooldown(self, user_id: int):
        """Définit le cooldown XP"""
        self._xp_cooldowns[user_id] = time.time()
    
    def _calculate_xp_for_level(self, level: int) -> int:
        """Calcule l'XP requise pour un niveau"""
        return int(100 * (level ** 1.5))
    
    async def get_user_xp_boost(self, user_id: int) -> float:
        """Récupère le boost XP d'un utilisateur"""
        if not self.db or not self.db.pool:
            return 0.0
        
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT xp_boost_role FROM user_xp WHERE user_id = $1",
                    user_id
                )
                
                if row and row['xp_boost_role']:
                    role_rank = row['xp_boost_role']
                    return self.XP_ROLES.get(role_rank, {}).get('boost', 0.0)
                
                return 0.0
        except Exception as e:
            logger.error(f"Erreur get_user_xp_boost: {e}")
            return 0.0
    
    async def add_xp(self, user_id: int, base_xp: int) -> Dict:
        """Ajoute de l'XP avec boost"""
        if not self.db or not self.db.pool:
            return {"success": False}
        
        try:
            # Récupérer le boost
            boost = await self.get_user_xp_boost(user_id)
            final_xp = int(base_xp * (1 + boost))
            
            async with self.db.pool.acquire() as conn:
                async with conn.transaction():
                    # Récupérer stats actuelles
                    row = await conn.fetchrow(
                        "SELECT xp, level, total_xp FROM user_xp WHERE user_id = $1",
                        user_id
                    )
                    
                    if row:
                        current_xp = row['xp']
                        current_level = row['level']
                        total_xp = row['total_xp']
                    else:
                        current_xp = 0
                        current_level = 1
                        total_xp = 0
                    
                    # Ajouter l'XP
                    new_xp = current_xp + final_xp
                    new_total_xp = total_xp + final_xp
                    new_level = current_level
                    leveled_up = False
                    levels_gained = 0
                    
                    # Vérifier level up (possiblement plusieurs)
                    while new_xp >= self._calculate_xp_for_level(new_level):
                        xp_required = self._calculate_xp_for_level(new_level)
                        new_xp -= xp_required
                        new_level += 1
                        leveled_up = True
                        levels_gained += 1
                    
                    # Mettre à jour
                    await conn.execute('''
                        INSERT INTO user_xp (user_id, xp, level, total_xp, last_xp_gain)
                        VALUES ($1, $2, $3, $4, NOW())
                        ON CONFLICT (user_id) DO UPDATE SET
                        xp = $2, level = $3, total_xp = $4, last_xp_gain = NOW()
                    ''', user_id, new_xp, new_level, new_total_xp)
                    
                    return {
                        "success": True,
                        "xp_gained": final_xp,
                        "boost": boost,
                        "new_level": new_level,
                        "leveled_up": leveled_up,
                        "levels_gained": levels_gained,
                        "current_xp": new_xp,
                        "xp_for_next": self._calculate_xp_for_level(new_level)
                    }
        except Exception as e:
            logger.error(f"Erreur add_xp: {e}")
            return {"success": False}
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Gain XP sur messages"""
        if message.author.bot or not message.guild:
            return
        
        # Vérifier cooldown
        cooldown = self._check_xp_cooldown(message.author.id)
        if cooldown > 0:
            return
        
        # Ajouter XP
        result = await self.add_xp(message.author.id, self.BASE_XP)
        
        if result.get("success"):
            self._set_xp_cooldown(message.author.id)
            
            # Notifier level up
            if result.get("leveled_up"):
                levels_gained = result.get("levels_gained", 1)
                if levels_gained > 1:
                    description = f"**{message.author.display_name}** a gagné **{levels_gained} niveaux** et atteint le niveau **{result['new_level']}** !"
                else:
                    description = f"**{message.author.display_name}** atteint le niveau **{result['new_level']}** !"
                
                embed = discord.Embed(
                    title="🎉 Level Up!",
                    description=description,
                    color=Colors.GOLD
                )
                
                # Afficher le boost si actif
                if result.get('boost', 0) > 0:
                    boost_percent = result['boost'] * 100
                    embed.add_field(
                        name="⚡ Boost actif",
                        value=f"+{boost_percent:.0f}% XP",
                        inline=True
                    )
                
                try:
                    await message.channel.send(embed=embed, delete_after=10)
                except:
                    pass
    
    @commands.command(name='xp', aliases=['level', 'rank'])
    async def xp_cmd(self, ctx, member: discord.Member = None):
        """Affiche l'XP et le niveau"""
        await self._execute_xp_info(ctx, member)
    
    @app_commands.command(name="xp", description="Affiche ton XP et niveau")
    @app_commands.describe(user="L'utilisateur dont voir l'XP (optionnel)")
    async def xp_slash(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        await self._execute_xp_info(interaction, user, is_slash=True)
    
    async def _execute_xp_info(self, ctx_or_interaction, member=None, is_slash=False):
        """Affiche les infos XP"""
        if is_slash:
            target = member or ctx_or_interaction.user
            send_func = ctx_or_interaction.followup.send
        else:
            target = member or ctx_or_interaction.author
            send_func = ctx_or_interaction.send
        
        try:
            if not self.db or not self.db.pool:
                embed = create_error_embed("Erreur", "Base de données indisponible")
                await send_func(embed=embed)
                return
            
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM user_xp WHERE user_id = $1", target.id
                )
            
            if not row:
                embed = discord.Embed(
                    title=f"📊 {target.display_name}",
                    description="Aucun XP pour le moment.\nÉcris des messages pour gagner de l'XP !",
                    color=Colors.WARNING
                )
                await send_func(embed=embed)
                return
            
            level = row['level']
            current_xp = row['xp']
            total_xp = row['total_xp']
            xp_boost_role = row['xp_boost_role']
            xp_needed = self._calculate_xp_for_level(level)
            
            embed = discord.Embed(
                title=f"📊 Niveau & XP de {target.display_name}",
                color=Colors.PREMIUM
            )
            
            # Niveau et progression
            progress = int((current_xp / xp_needed) * 10)
            progress_bar = "█" * progress + "░" * (10 - progress)
            
            embed.add_field(
                name="🎯 Niveau actuel",
                value=f"**Niveau {level}**",
                inline=True
            )
            
            embed.add_field(
                name="⚡ XP Total",
                value=f"**{total_xp:,}** XP",
                inline=True
            )
            
            embed.add_field(
                name="📈 Progression",
                value=f"`{progress_bar}`\n{current_xp}/{xp_needed} XP ({(current_xp/xp_needed*100):.1f}%)",
                inline=False
            )
            
            # Boost actif
            if xp_boost_role:
                role_data = self.XP_ROLES.get(xp_boost_role, {})
                boost_percent = role_data.get('boost', 0) * 100
                emoji = role_data.get('emoji', '🔰')
                name = role_data.get('name', f'Rang {xp_boost_role}')
                
                embed.add_field(
                    name=f"{emoji} Boost actif",
                    value=f"**{name}** - +{boost_percent:.0f}% XP",
                    inline=False
                )
            else:
                embed.add_field(
                    name="💡 Pas de boost",
                    value=f"Utilise `{PREFIX}xp_shop` pour voir les rôles disponibles !",
                    inline=False
                )
            
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.set_footer(text="Gagne de l'XP en écrivant des messages (cooldown 1 min)")
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur xp info: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de la récupération de l'XP")
            await send_func(embed=embed)
    
    @commands.command(name='xp_shop', aliases=['xpshop', 'rankshop'])
    async def xp_shop_cmd(self, ctx):
        """Affiche la boutique des rôles XP"""
        await self._execute_xp_shop(ctx)
    
    @app_commands.command(name="xp_shop", description="Boutique des rôles de boost XP")
    async def xp_shop_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._execute_xp_shop(interaction, is_slash=True)
    
    async def _execute_xp_shop(self, ctx_or_interaction, is_slash=False):
        """Affiche la boutique XP"""
        send_func = (ctx_or_interaction.followup.send if is_slash 
                    else ctx_or_interaction.send)
        
        embed = discord.Embed(
            title="🏪 Boutique Rôles XP Boost",
            description="Achète des rôles pour booster tes gains d'XP !\n\n**L'argent dépensé va à la banque publique.**",
            color=Colors.GOLD
        )
        
        for rank, data in self.XP_ROLES.items():
            boost_percent = data['boost'] * 100
            embed.add_field(
                name=f"{data['emoji']} {data['name']}",
                value=f"💰 **{data['price']:,}** PrissBucks\n"
                      f"⚡ **+{boost_percent:.0f}%** XP sur tous les gains\n"
                      f"`{PREFIX}buy_xp_role {rank}` ou `/buy_xp_role {rank}`",
                inline=True
            )
        
        embed.add_field(
            name="ℹ️ Informations",
            value=f"• Les boosts sont **cumulatifs** avec l'XP de base\n"
                  f"• Achat **permanent** (pas de durée limitée)\n"
                  f"• Un seul rang actif à la fois\n"
                  f"• Argent dépensé → Banque publique",
            inline=False
        )
        
        embed.set_footer(text="Investis dans ton progression ! 🚀")
        await send_func(embed=embed)
    
    @commands.command(name='xp_leaderboard', aliases=['xplb', 'topxp'])
    async def xp_leaderboard_cmd(self, ctx, limit: int = 10):
        """Affiche le classement XP"""
        await self._execute_xp_leaderboard(ctx, limit)
    
    @app_commands.command(name="xp_leaderboard", description="Affiche le classement des niveaux XP")
    @app_commands.describe(limit="Nombre d'utilisateurs à afficher (max 20)")
    async def xp_leaderboard_slash(self, interaction: discord.Interaction, limit: int = 10):
        await interaction.response.defer()
        await self._execute_xp_leaderboard(interaction, limit, is_slash=True)
    
    async def _execute_xp_leaderboard(self, ctx_or_interaction, limit=10, is_slash=False):
        """Affiche le classement XP"""
        send_func = (ctx_or_interaction.followup.send if is_slash 
                    else ctx_or_interaction.send)
        
        if limit < 1:
            limit = 10
        elif limit > 20:
            limit = 20
        
        try:
            if not self.db or not self.db.pool:
                embed = create_error_embed("Erreur", "Base de données indisponible")
                await send_func(embed=embed)
                return
            
            async with self.db.pool.acquire() as conn:
                top_users = await conn.fetch("""
                    SELECT user_id, level, total_xp, xp_boost_role
                    FROM user_xp
                    ORDER BY total_xp DESC
                    LIMIT $1
                """, limit)
            
            if not top_users:
                embed = create_info_embed("Classement vide", "Aucun utilisateur n'a encore d'XP.")
                await send_func(embed=embed)
                return
            
            embed = discord.Embed(
                title="🏆 Classement XP",
                description="Top des utilisateurs par XP total",
                color=Colors.GOLD
            )
            
            description = ""
            for i, row in enumerate(top_users, 1):
                user_id = row['user_id']
                level = row['level']
                total_xp = row['total_xp']
                boost_role = row['xp_boost_role']
                
                try:
                    user = self.bot.get_user(user_id)
                    if user:
                        username = user.display_name
                    else:
                        user = await self.bot.fetch_user(user_id)
                        username = user.display_name
                except:
                    username = f"Utilisateur {user_id}"
                
                # Emoji position
                if i == 1:
                    emoji = "🥇"
                elif i == 2:
                    emoji = "🥈"
                elif i == 3:
                    emoji = "🥉"
                else:
                    emoji = f"`{i:2d}.`"
                
                # Afficher boost si actif
                boost_info = ""
                if boost_role:
                    role_data = self.XP_ROLES.get(boost_role, {})
                    boost_emoji = role_data.get('emoji', '')
                    boost_info = f" {boost_emoji}"
                
                description += f"{emoji} **{username}**{boost_info}\n└ Niveau {level} • {total_xp:,} XP\n"
            
            embed.description = description
            embed.set_footer(text=f"Top {len(top_users)} utilisateurs")
            await send_func(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur xp leaderboard: {e}")
            embed = create_error_embed("Erreur", "Erreur lors de l'affichage du classement")
            await send_func(embed=embed)

async def setup(bot):
    await bot.add_cog(XPSystem(bot))
