import discord
from discord.ext import commands
import logging

from config import PREFIX, Colors, Emojis
from utils.embeds import create_help_embed, create_info_embed

logger = logging.getLogger(__name__)

class Help(commands.Cog):
    """Système d'aide et informations sur le bot"""
    
    def __init__(self, bot):
        self.bot = bot
    
    async def cog_load(self):
        """Appelé quand le cog est chargé"""
        logger.info("✅ Cog Help initialisé")

    @commands.command(name='help', aliases=['h', 'aide', 'commands'])
    async def help_cmd(self, ctx, *, command_name: str = None):
        """Affiche l'aide du bot ou d'une commande spécifique"""
        
        if command_name:
            # Aide pour une commande spécifique
            await self.show_command_help(ctx, command_name)
        else:
            # Aide générale
            await self.show_general_help(ctx)
    
    async def show_general_help(self, ctx):
        """Affiche l'aide générale"""
        try:
            # Vérifier les permissions de l'utilisateur
            user_permissions = {
                'administrator': ctx.author.guild_permissions.administrator,
                'is_owner': await self.bot.is_owner(ctx.author)
            }
            
            embed = create_help_embed(user_permissions)
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            # Ajouter des infos sur le bot
            embed.add_field(
                name="🤖 Informations Bot",
                value=f"**Serveurs :** {len(self.bot.guilds)}\n"
                      f"**Uptime :** <t:{int(self.bot.start_time.timestamp())}:R>\n"
                      f"**Latence :** {round(self.bot.latency * 1000)}ms",
                inline=False
            )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur help général: {e}")
            embed = create_info_embed(
                "Aide du Bot",
                f"**Commandes principales :**\n"
                f"`{PREFIX}balance` - Voir ton solde\n"
                f"`{PREFIX}daily` - Pièces quotidiennes\n"
                f"`{PREFIX}shop` - Voir la boutique\n"
                f"`{PREFIX}buy <id>` - Acheter un item"
            )
            await ctx.send(embed=embed)
    
    async def show_command_help(self, ctx, command_name: str):
        """Affiche l'aide pour une commande spécifique"""
        # Chercher la commande
        command = self.bot.get_command(command_name.lower())
        
        if not command:
            embed = discord.Embed(
                title=f"{Emojis.ERROR} Commande introuvable",
                description=f"La commande `{command_name}` n'existe pas.\nUtilise `{PREFIX}help` pour voir toutes les commandes.",
                color=Colors.ERROR
            )
            await ctx.send(embed=embed)
            return
        
        try:
            embed = discord.Embed(
                title=f"ℹ️ Aide - {command.name}",
                color=Colors.INFO
            )
            
            # Description
            if command.help:
                embed.add_field(name="📝 Description", value=command.help, inline=False)
            
            # Usage
            usage = f"`{PREFIX}{command.name}"
            if command.signature:
                usage += f" {command.signature}"
            usage += "`"
            embed.add_field(name="💻 Usage", value=usage, inline=False)
            
            # Aliases
            if command.aliases:
                aliases_text = ", ".join([f"`{alias}`" for alias in command.aliases])
                embed.add_field(name="🔄 Aliases", value=aliases_text, inline=True)
            
            # Cooldown
            if hasattr(command, '_buckets') and command._buckets:
                cooldown = command._buckets._cooldown
                if cooldown:
                    embed.add_field(
                        name="⏰ Cooldown", 
                        value=f"{cooldown.rate} fois toutes les {cooldown.per}s",
                        inline=True
                    )
            
            # Permissions requises
            permissions = []
            for check in command.checks:
                if hasattr(check, '__name__'):
                    if 'administrator' in check.__name__:
                        permissions.append("Administrateur")
                    elif 'owner' in check.__name__:
                        permissions.append("Propriétaire du bot")
            
            if permissions:
                embed.add_field(
                    name="🔐 Permissions", 
                    value=" • ".join(permissions),
                    inline=False
                )
            
            # Exemples d'utilisation
            examples = self.get_command_examples(command.name)
            if examples:
                embed.add_field(
                    name="💡 Exemples",
                    value=examples,
                    inline=False
                )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur help commande {command_name}: {e}")
            embed = create_info_embed(
                f"Aide - {command.name}",
                command.help or "Aucune description disponible."
            )
            await ctx.send(embed=embed)
    
    def get_command_examples(self, command_name: str) -> str:
        """Retourne des exemples d'utilisation pour une commande"""
        examples = {
            'balance': f"`{PREFIX}balance` - Ton solde\n`{PREFIX}balance @user` - Solde d'un utilisateur",
            'give': f"`{PREFIX}give @user 100` - Donner 100 PrissBucks\n`{PREFIX}pay @user 500` - Donner 500 PrissBucks",
            'shop': f"`{PREFIX}shop` - Page 1 de la boutique\n`{PREFIX}shop 2` - Page 2 de la boutique",
            'buy': f"`{PREFIX}buy 1` - Acheter l'item avec l'ID 1",
            'additem': f"`{PREFIX}additem 1000 @Premium Rôle Premium` - Ajouter un rôle au shop",
            'leaderboard': f"`{PREFIX}leaderboard` - Top 10\n`{PREFIX}top 20` - Top 20",
            'inventory': f"`{PREFIX}inventory` - Ton inventaire\n`{PREFIX}inv @user` - Inventaire d'un utilisateur"
        }
        return examples.get(command_name, "")

    @commands.command(name='about', aliases=['info', 'botinfo'])
    async def about_cmd(self, ctx):
        """Informations sur le bot"""
        try:
            embed = discord.Embed(
                title="🤖 À propos du bot",
                description="Bot économie développé pour les serveurs Discord",
                color=Colors.INFO
            )
            
            # Stats du bot
            total_users = sum(len(guild.members) for guild in self.bot.guilds)
            embed.add_field(
                name="📊 Statistiques",
                value=f"**Serveurs :** {len(self.bot.guilds):,}\n"
                      f"**Utilisateurs :** {total_users:,}\n"
                      f"**Commandes chargées :** {len(self.bot.commands):,}",
                inline=True
            )
            
            # Infos techniques
            embed.add_field(
                name="⚙️ Technique",
                value=f"**Langage :** Python {discord.__version__}\n"
                      f"**Discord.py :** v{discord.__version__}\n"
                      f"**Latence :** {round(self.bot.latency * 1000)}ms",
                inline=True
            )
            
            # Cogs chargés
            loaded_cogs = [name.split('.')[-1] for name in self.bot.extensions.keys()]
            embed.add_field(
                name="🔧 Modules chargés",
                value=" • ".join(loaded_cogs) if loaded_cogs else "Aucun",
                inline=False
            )
            
            # Liens utiles
            embed.add_field(
                name="🔗 Liens",
                value=f"[Support Discord](https://discord.gg/example)\n"
                      f"[Inviter le bot](https://discord.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot&permissions=268815424)",
                inline=False
            )
            
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            embed.set_footer(text=f"Développé avec ❤️ • Uptime: depuis le {self.bot.start_time.strftime('%d/%m/%Y')}")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur about: {e}")
            embed = create_info_embed(
                "À propos",
                "Bot économie pour Discord\nDéveloppé avec discord.py"
            )
            await ctx.send(embed=embed)

    @commands.command(name='ping')
    async def ping_cmd(self, ctx):
        """Affiche la latence du bot"""
        try:
            # Latence WebSocket
            ws_latency = round(self.bot.latency * 1000)
            
            # Latence API (temps de réponse)
            import time
            start = time.perf_counter()
            message = await ctx.send("🏓 Pong !")
            end = time.perf_counter()
            api_latency = round((end - start) * 1000)
            
            # Déterminer la couleur selon la latence
            if ws_latency < 100:
                color = Colors.SUCCESS
                status = "Excellent"
            elif ws_latency < 200:
                color = Colors.WARNING
                status = "Bon"
            else:
                color = Colors.ERROR
                status = "Élevé"
            
            embed = discord.Embed(
                title="🏓 Pong !",
                color=color
            )
            
            embed.add_field(
                name="📡 Latence WebSocket",
                value=f"**{ws_latency}ms** ({status})",
                inline=True
            )
            
            embed.add_field(
                name="⚡ Latence API",
                value=f"**{api_latency}ms**",
                inline=True
            )
            
            # Uptime
            uptime_seconds = (ctx.message.created_at - self.bot.start_time).total_seconds()
            uptime_text = self.format_uptime(uptime_seconds)
            embed.add_field(
                name="⏰ Uptime",
                value=uptime_text,
                inline=True
            )
            
            await message.edit(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur ping: {e}")
            await ctx.send(f"🏓 **Pong !** Latence : {round(self.bot.latency * 1000)}ms")
    
    def format_uptime(self, seconds: float) -> str:
        """Formate l'uptime en format lisible"""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days}j")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}min")
        
        return " ".join(parts) or "< 1min"

    @commands.command(name='invite')
    async def invite_cmd(self, ctx):
        """Lien d'invitation du bot"""
        try:
            # Générer le lien d'invitation avec les permissions nécessaires
            permissions = discord.Permissions(
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
                manage_roles=True,
                use_external_emojis=True
            )
            
            invite_link = discord.utils.oauth_url(
                self.bot.user.id,
                permissions=permissions,
                scopes=('bot',)
            )
            
            embed = discord.Embed(
                title="📩 Inviter le bot",
                description=f"Clique sur le lien ci-dessous pour ajouter le bot à ton serveur !",
                color=Colors.INFO
            )
            
            embed.add_field(
                name="🔗 Lien d'invitation",
                value=f"[Cliquez ici pour inviter le bot]({invite_link})",
                inline=False
            )
            
            embed.add_field(
                name="⚠️ Permissions nécessaires",
                value="• Envoyer des messages\n• Intégrer des liens\n• Gérer les rôles\n• Lire l'historique des messages",
                inline=False
            )
            
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            logger.error(f"Erreur invite: {e}")
            embed = create_info_embed(
                "Invitation",
                "Contacte un administrateur pour obtenir le lien d'invitation du bot."
            )
            await ctx.send(embed=embed)

async def setup(bot):
    """Fonction appelée pour charger le cog"""
    # Ajouter l'heure de démarrage du bot
    if not hasattr(bot, 'start_time'):
        from datetime import datetime
        bot.start_time = datetime.utcnow()
    
    await bot.add_cog(Help(bot))
