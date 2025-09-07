"""
Bot Économie - Point d'entrée principal simplifié
Version allégée sans les systèmes complexes
"""

import discord
from discord.ext import commands
import asyncio
import logging
import sys
import signal
from pathlib import Path
from typing import Optional, List

# Imports locaux
from config import TOKEN, PREFIX, DATABASE_URL, LOG_LEVEL, HEALTH_PORT
from database.db import Database

# Configuration simple du logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class BotManager:
    """Gestionnaire principal du bot - version simplifiée"""
    
    def __init__(self):
        self.bot: Optional[commands.Bot] = None
        self.database: Optional[Database] = None
        self.services: List[asyncio.Task] = []
        self.shutdown_requested = False
        
        # Configurer les gestionnaires d'arrêt
        self._setup_shutdown_handlers()
    
    def _setup_shutdown_handlers(self):
        """Configure les gestionnaires d'arrêt propre"""
        if sys.platform != "win32":
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Gestionnaire simple pour arrêt gracieux"""
        logger.info(f"Signal {signum} reçu, arrêt demandé")
        self.shutdown_requested = True
    
    def create_bot(self) -> commands.Bot:
        """Crée et configure le bot Discord"""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.guild_messages = True
        
        bot = commands.Bot(
            command_prefix=PREFIX,
            intents=intents,
            help_command=None
        )
        
        # Événements essentiels seulement
        self._setup_bot_events(bot)
        
        return bot
    
    def _setup_bot_events(self, bot: commands.Bot):
        """Configure les événements essentiels du bot"""
        
        @bot.event
        async def on_ready():
            logger.info(f"✅ {bot.user} connecté sur {len(bot.guilds)} serveur(s)")
            await self._sync_slash_commands(bot)
        
        @bot.event
        async def on_command_error(ctx, error):
            await ErrorHandler.handle_command_error(ctx, error)
        
        @bot.event
        async def on_application_command_error(interaction, error):
            await ErrorHandler.handle_slash_error(interaction, error)
    
    async def _sync_slash_commands(self, bot: commands.Bot):
        """Synchronise les slash commands"""
        try:
            synced = await bot.tree.sync()
            logger.info(f"🔄 {len(synced)} slash command(s) synchronisée(s)")
        except Exception as e:
            logger.error(f"❌ Erreur sync slash commands: {e}")
    
    async def setup_database(self) -> bool:
        """Configure la base de données"""
        try:
            logger.info("🔌 Connexion à la base de données...")
            self.database = Database(dsn=DATABASE_URL)
            await self.database.connect()
            logger.info("✅ Base de données connectée")
            return True
        except Exception as e:
            logger.error(f"❌ Erreur connexion DB: {e}")
            return False
    
    async def load_extensions(self, bot: commands.Bot) -> tuple[int, int]:
        """Charge les extensions simplifiées"""
        cogs_dir = Path("cogs")
        if not cogs_dir.exists():
            logger.warning("📁 Dossier 'cogs' manquant")
            return 0, 0
        
        # Extensions essentielles seulement
        essential_extensions = [
            'transaction_logs',  # Doit être chargé en premier
            'economy',
            'shop', 
            'leaderboard',
            'games',
            'roulette',
            'public_bank',
            'bank',  # Banque privée conservée
            'steal',
            'help'
        ]
        
        loaded = failed = 0
        
        for ext_name in essential_extensions:
            if await self._load_extension(bot, ext_name):
                loaded += 1
            else:
                failed += 1
        
        # Configurer les logs après chargement de transaction_logs
        self._setup_transaction_logging(bot)
        
        logger.info(f"📊 Extensions simplifiées: {loaded} chargées, {failed} échecs")
        return loaded, failed
    
    async def _load_extension(self, bot: commands.Bot, ext_name: str) -> bool:
        """Charge une extension avec gestion d'erreurs"""
        try:
            await bot.load_extension(f'cogs.{ext_name}')
            logger.debug(f"✅ Extension '{ext_name}' chargée")
            return True
        except Exception as e:
            logger.error(f"❌ Échec chargement '{ext_name}': {e}")
            return False
    
    def _setup_transaction_logging(self, bot: commands.Bot):
        """Configure le système de logs de transactions"""
        transaction_logs_cog = bot.get_cog('TransactionLogs')
        if transaction_logs_cog:
            bot.transaction_logs = transaction_logs_cog
            logger.info("✅ Logs de transactions configurés")
    
    def add_owner_commands(self, bot: commands.Bot):
        """Ajoute les commandes owner essentielles"""
        
        @bot.command(name='reload')
        @commands.is_owner()
        async def reload_cog(ctx, cog_name: str):
            try:
                await bot.reload_extension(f'cogs.{cog_name}')
                if cog_name == 'transaction_logs':
                    self._setup_transaction_logging(bot)
                await ctx.send(f"✅ **'{cog_name}' rechargé**")
            except Exception as e:
                await ctx.send(f"❌ **Erreur:** {e}")
        
        @bot.command(name='sync')
        @commands.is_owner()
        async def sync_commands(ctx):
            try:
                synced = await bot.tree.sync()
                await ctx.send(f"✅ **{len(synced)} commande(s) synchronisée(s)**")
            except Exception as e:
                await ctx.send(f"❌ **Erreur sync:** {e}")
        
        @bot.command(name='status')
        @commands.is_owner()
        async def bot_status(ctx):
            embed = discord.Embed(title="🤖 Statut Bot", color=0x0099ff)
            embed.add_field(name="🟢 État", value="En ligne", inline=True)
            embed.add_field(name="📊 Serveurs", value=len(bot.guilds), inline=True)
            embed.add_field(name="🔧 Extensions", value=len(bot.extensions), inline=True)
            embed.add_field(name="💾 DB", value="🟢 OK" if self.database else "🔴 KO", inline=True)
            await ctx.send(embed=embed)
    
    async def run(self) -> None:
        """Point d'entrée principal - logique simplifiée"""
        try:
            # Validations préalables
            self._validate_configuration()
            
            # Configuration de la base de données
            if not await self.setup_database():
                raise RuntimeError("Impossible de connecter la base de données")
            
            # Création et configuration du bot
            self.bot = self.create_bot()
            self.bot.database = self.database
            
            # Chargement des extensions essentielles
            loaded, failed = await self.load_extensions(self.bot)
            if loaded == 0:
                logger.warning("⚠️ Aucune extension chargée")
            
            # Commandes owner
            self.add_owner_commands(self.bot)
            
            # Démarrage du bot
            logger.info("🚀 Démarrage du bot simplifié...")
            bot_task = asyncio.create_task(self.bot.start(TOKEN))
            self.services.append(bot_task)
            
            # Boucle principale simplifiée
            await self._main_loop()
            
        except KeyboardInterrupt:
            logger.info("👋 Arrêt demandé par l'utilisateur")
        except Exception as e:
            logger.error(f"💥 Erreur critique: {e}")
            raise
        finally:
            await self._cleanup()
    
    def _validate_configuration(self):
        """Valide la configuration avant démarrage"""
        if not TOKEN:
            raise ValueError("TOKEN Discord manquant")
        if not DATABASE_URL:
            raise ValueError("URL de base de données manquante")
    
    async def _main_loop(self):
        """Boucle principale simplifiée"""
        while not self.shutdown_requested:
            try:
                # Vérifier les services
                done_services = [s for s in self.services if s.done()]
                if done_services:
                    break
                
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur boucle principale: {e}")
                break
    
    async def _cleanup(self):
        """Nettoyage des ressources"""
        logger.info("🧹 Nettoyage en cours...")
        
        # Arrêter les services
        for service in self.services:
            if not service.done():
                service.cancel()
                try:
                    await service
                except asyncio.CancelledError:
                    pass
        
        # Fermer la base de données
        if self.database:
            await self.database.close()
        
        # Fermer le bot
        if self.bot and not self.bot.is_closed():
            await self.bot.close()
        
        logger.info("✅ Nettoyage terminé")


class ErrorHandler:
    """Gestionnaire d'erreurs centralisé et simplifié"""
    
    ERROR_MESSAGES = {
        commands.CommandNotFound: None,  # Ignorer
        commands.MissingRequiredArgument: "❌ **Argument manquant**",
        commands.BadArgument: "❌ **Argument invalide**",
        commands.MissingPermissions: "❌ **Permissions insuffisantes**",
        commands.NotOwner: "❌ **Commande réservée au propriétaire**",
    }
    
    @classmethod
    async def handle_command_error(cls, ctx, error):
        """Gestion simplifiée des erreurs de commandes"""
        # Vérifier si la commande a son propre gestionnaire
        if hasattr(ctx.command, 'has_error_handler') and ctx.command.has_error_handler():
            return
        
        error_type = type(error)
        
        # Cooldown avec temps formaté
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏰ **Cooldown** - Réessaye dans {error.retry_after:.1f}s")
            return
        
        # Permissions bot
        if isinstance(error, commands.BotMissingPermissions):
            perms = ', '.join(error.missing_permissions)
            await ctx.send(f"❌ **Bot sans permissions:** {perms}")
            return
        
        # Messages d'erreur standard
        message = cls.ERROR_MESSAGES.get(error_type)
        if message:
            await ctx.send(message)
        elif error_type != commands.CommandNotFound:
            logger.error(f"Erreur non gérée {ctx.command}: {error}")
            await ctx.send("❌ **Erreur inattendue**")
    
    @classmethod
    async def handle_slash_error(cls, interaction, error):
        """Gestion simplifiée des erreurs slash"""
        error_messages = {
            discord.app_commands.CommandOnCooldown: 
                f"⏰ **Cooldown** - Réessaye dans {error.retry_after:.1f}s",
            discord.app_commands.MissingPermissions: 
                "❌ **Permissions insuffisantes**",
        }
        
        message = error_messages.get(type(error), "❌ **Erreur inattendue**")
        
        embed = discord.Embed(
            description=message,
            color=0xff0000
        )
        
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            logger.error(f"Erreur envoi message d'erreur: {error}")


async def main():
    """Point d'entrée principal simplifié"""
    # Configuration Windows si nécessaire
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    # Lancement du gestionnaire de bot
    manager = BotManager()
    await manager.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Au revoir !")
    except Exception as e:
        print(f"💥 Erreur critique: {e}")
        sys.exit(1)
