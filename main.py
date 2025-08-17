import discord
from discord.ext import commands
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional, List
import traceback

# Imports locaux
from config import TOKEN, PREFIX, DATABASE_URL, LOG_LEVEL, HEALTH_PORT
from database.db import Database

# Import conditionnel du serveur de santé
try:
    from health_server import HealthServer
    HEALTH_SERVER_AVAILABLE = True
except ImportError:
    HEALTH_SERVER_AVAILABLE = False
    logging.warning("⚠️ health_server.py non trouvé, pas de health check")

# Configuration du logging optimisée
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8', mode='a') if os.getenv('LOG_FILE') else logging.NullHandler()
    ]
)
logger = logging.getLogger(__name__)

class EconomyBot:
    """Bot économie avec architecture moderne et gestion d'erreurs robuste"""
    
    def __init__(self):
        # Configuration des intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.guild_messages = True
        
        # Initialisation du bot
        self.bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
        self.database: Optional[Database] = None
        self.health_server: Optional[HealthServer] = None
        
        # État du bot
        self.is_ready = False
        self.shutdown_requested = False
        
        # Services
        self.services: List[asyncio.Task] = []
        
        # Configuration des événements
        self._setup_bot_events()
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self):
        """Configure les gestionnaires de signaux pour arrêt propre"""
        if sys.platform != "win32":
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Gestionnaire de signaux pour arrêt gracieux"""
        logger.info(f"Signal {signum} reçu, arrêt en cours...")
        self.shutdown_requested = True
    
    def _setup_bot_events(self):
        """Configure les événements du bot"""
        
        @self.bot.event
        async def on_ready():
            """Événement déclenché quand le bot est prêt"""
            logger.info(f"✅ {self.bot.user} connecté ! Serveurs: {len(self.bot.guilds)}")
            
            # Synchroniser les slash commands une seule fois
            try:
                synced = await self.bot.tree.sync()
                logger.info(f"🔄 {len(synced)} slash command(s) synchronisée(s)")
            except Exception as e:
                logger.error(f"❌ Erreur sync slash commands: {e}")
            
            self.is_ready = True
        
        @self.bot.event
        async def on_guild_join(guild):
            """Événement quand le bot rejoint un serveur"""
            logger.info(f"✅ Ajouté à: {guild.name} ({guild.id}) - {guild.member_count} membres")
        
        @self.bot.event
        async def on_guild_remove(guild):
            """Événement quand le bot quitte un serveur"""
            logger.info(f"❌ Retiré de: {guild.name} ({guild.id})")
        
        @self.bot.event
        async def on_command_error(ctx, error):
            """Gestion globale des erreurs - version simplifiée"""
            if hasattr(ctx.command, 'has_error_handler') and ctx.command.has_error_handler():
                return
            
            error_handlers = {
                commands.CommandNotFound: None,  # Ignorer
                commands.MissingRequiredArgument: ("❌ **Argument manquant !**", False),
                commands.BadArgument: ("❌ **Argument invalide !**", False),
                commands.CommandOnCooldown: (f"⏰ **Cooldown !** Réessaye dans {error.retry_after:.1f}s.", False),
                commands.MissingPermissions: ("❌ **Permissions insuffisantes !**", False),
                commands.BotMissingPermissions: (f"❌ **Bot sans permissions:** {', '.join(error.missing_permissions)}", False),
                commands.NotOwner: ("❌ **Commande réservée au propriétaire !**", False)
            }
            
            handler = error_handlers.get(type(error))
            if handler is None:
                if isinstance(error, commands.CommandNotFound):
                    return
                # Erreur non gérée
                logger.error(f"Erreur non gérée {ctx.command}: {error}")
                await ctx.send("❌ **Erreur inattendue.**")
            elif handler[0]:  # Si message à envoyer
                await ctx.send(handler[0])
        
        @self.bot.event
        async def on_application_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
            """Gestion des erreurs slash commands - version simplifiée"""
            embed_data = {
                discord.app_commands.CommandOnCooldown: ("⏰ Cooldown actif", f"Réessaye dans **{error.retry_after:.1f}s**", 0xff9900),
                discord.app_commands.MissingPermissions: ("❌ Permissions insuffisantes", "Tu n'as pas les permissions nécessaires", 0xff0000),
            }
            
            embed_info = embed_data.get(type(error), ("❌ Erreur", "Une erreur s'est produite", 0xff0000))
            embed = discord.Embed(title=embed_info[0], description=embed_info[1], color=embed_info[2])
            
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                logger.error(f"Erreur envoi message d'erreur: {error}")
    
    async def setup_database(self) -> bool:
        """Connecte la base de données de manière robuste"""
        try:
            logger.info("🔌 Connexion à la base de données...")
            self.database = Database(dsn=DATABASE_URL)
            await self.database.connect()
            self.bot.database = self.database
            logger.info("✅ Base de données connectée")
            return True
        except Exception as e:
            logger.error(f"❌ Erreur connexion DB: {e}")
            logger.error(traceback.format_exc())
            return False
    
    async def load_cogs(self) -> tuple[int, int]:
        """Charge tous les cogs avec gestion d'erreurs améliorée"""
        cogs_dir = Path("cogs")
        if not cogs_dir.exists():
            logger.warning("📁 Dossier 'cogs' manquant, création...")
            cogs_dir.mkdir()
            return 0, 0
        
        # Ordre de chargement (cogs critiques d'abord)
        priority_cogs = ['transaction_logs', 'economy', 'message_rewards']
        
        loaded, failed = 0, 0
        
        # Charger les cogs prioritaires
        for cog_name in priority_cogs:
            success = await self._load_single_cog(cog_name)
            if success:
                loaded += 1
            else:
                failed += 1
        
        # Configurer les logs de transactions après chargement de transaction_logs
        self._setup_transaction_logs()
        
        # Charger les autres cogs
        other_cogs = [
            f.stem for f in cogs_dir.glob("*.py") 
            if f.stem not in priority_cogs and f.stem != "__init__"
        ]
        
        for cog_name in other_cogs:
            success = await self._load_single_cog(cog_name)
            if success:
                loaded += 1
            else:
                failed += 1
        
        logger.info(f"📊 Cogs: {loaded} chargés, {failed} échecs")
        return loaded, failed
    
    async def _load_single_cog(self, cog_name: str) -> bool:
        """Charge un cog unique avec gestion d'erreurs"""
        try:
            await self.bot.load_extension(f'cogs.{cog_name}')
            logger.info(f"✅ Cog '{cog_name}' chargé")
            return True
        except Exception as e:
            logger.error(f"❌ Échec chargement '{cog_name}': {e}")
            return False
    
    def _setup_transaction_logs(self):
        """Configure le système de logs de transactions"""
        transaction_logs_cog = self.bot.get_cog('TransactionLogs')
        if transaction_logs_cog:
            self.bot.transaction_logs = transaction_logs_cog
            logger.info("✅ Logs de transactions configurés")
        else:
            logger.warning("⚠️ Cog TransactionLogs non trouvé")
    
    async def start_health_server(self) -> Optional[asyncio.Task]:
        """Démarre le serveur de santé si disponible"""
        if not HEALTH_SERVER_AVAILABLE:
            return None
        
        try:
            self.health_server = HealthServer(port=HEALTH_PORT)
            task = asyncio.create_task(self.health_server.run_forever())
            logger.info(f"🏥 Serveur de santé démarré sur port {HEALTH_PORT}")
            return task
        except Exception as e:
            logger.error(f"❌ Erreur serveur de santé: {e}")
            return None
    
    def add_owner_commands(self):
        """Ajoute les commandes owner essentielles"""
        
        @self.bot.command(name='reload')
        @commands.is_owner()
        async def reload_cog(ctx, cog_name: str):
            """[OWNER] Recharge un cog"""
            try:
                await self.bot.reload_extension(f'cogs.{cog_name}')
                if cog_name == 'transaction_logs':
                    self._setup_transaction_logs()
                await ctx.send(f"✅ **'{cog_name}' rechargé !**")
                logger.info(f"🔄 Cog '{cog_name}' rechargé par {ctx.author}")
            except Exception as e:
                await ctx.send(f"❌ **Erreur:** {e}")
                logger.error(f"Erreur reload {cog_name}: {e}")
        
        @self.bot.command(name='sync')
        @commands.is_owner()
        async def sync_commands(ctx):
            """[OWNER] Synchronise les slash commands"""
            try:
                synced = await self.bot.tree.sync()
                await ctx.send(f"✅ **{len(synced)} commande(s) synchronisée(s) !**")
                logger.info(f"🔄 Sync manuelle: {len(synced)} commandes")
            except Exception as e:
                await ctx.send(f"❌ **Erreur sync:** {e}")
                logger.error(f"Erreur sync: {e}")
        
        @self.bot.command(name='status')
        @commands.is_owner()
        async def bot_status(ctx):
            """[OWNER] Statut du bot"""
            embed = discord.Embed(title="🤖 Statut Bot", color=0x0099ff)
            embed.add_field(name="🟢 État", value="En ligne", inline=True)
            embed.add_field(name="📊 Serveurs", value=len(self.bot.guilds), inline=True)
            embed.add_field(name="🔧 Cogs", value=len(self.bot.extensions), inline=True)
            embed.add_field(name="💾 DB", value="🟢 OK" if self.database else "🔴 KO", inline=True)
            embed.add_field(name="🏥 Santé", value="🟢 OK" if self.health_server else "🔴 KO", inline=True)
            await ctx.send(embed=embed)
    
    async def cleanup(self):
        """Nettoyage complet des ressources"""
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
            try:
                await self.database.close()
                logger.info("🔌 Base de données fermée")
            except Exception as e:
                logger.error(f"Erreur fermeture DB: {e}")
        
        # Fermer le bot
        if not self.bot.is_closed():
            try:
                await self.bot.close()
                logger.info("🤖 Bot fermé")
            except Exception as e:
                logger.error(f"Erreur fermeture bot: {e}")
        
        logger.info("✅ Nettoyage terminé")
    
    async def run(self):
        """Méthode principale pour lancer le bot avec gestion complète d'erreurs"""
        try:
            # 1. Vérifications préalables
            if not TOKEN:
                raise ValueError("❌ TOKEN Discord manquant dans la configuration")
            
            if not DATABASE_URL:
                raise ValueError("❌ URL de base de données manquante")
            
            # 2. Connexion base de données
            if not await self.setup_database():
                raise RuntimeError("💥 Impossible de se connecter à la base de données")
            
            # 3. Chargement des cogs
            loaded, failed = await self.load_cogs()
            if loaded == 0:
                logger.warning("⚠️ Aucun cog chargé, le bot pourrait ne pas fonctionner")
            
            # 4. Commandes owner
            self.add_owner_commands()
            
            # 5. Serveur de santé (optionnel)
            health_task = await self.start_health_server()
            if health_task:
                self.services.append(health_task)
            
            # 6. Démarrage du bot Discord
            logger.info("🚀 Démarrage du bot Discord...")
            bot_task = asyncio.create_task(self.bot.start(TOKEN))
            self.services.append(bot_task)
            
            # 7. Boucle principale avec surveillance
            await self._main_loop()
            
        except KeyboardInterrupt:
            logger.info("👋 Arrêt demandé par l'utilisateur")
        except Exception as e:
            logger.error(f"💥 Erreur fatale: {e}")
            logger.error(traceback.format_exc())
            raise
        finally:
            await self.cleanup()
    
    async def _main_loop(self):
        """Boucle principale avec surveillance des services"""
        while not self.shutdown_requested:
            try:
                # Vérifier l'état des services
                done_services = [s for s in self.services if s.done()]
                
                if done_services:
                    # Un service s'est arrêté
                    for service in done_services:
                        try:
                            await service  # Récupérer l'exception si elle existe
                        except Exception as e:
                            logger.error(f"Service arrêté avec erreur: {e}")
                    break
                
                # Attendre un peu avant la prochaine vérification
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Erreur dans la boucle principale: {e}")
                break

# Point d'entrée principal
async def main():
    """Point d'entrée principal avec configuration d'event loop optimisée"""
    
    # Configuration Windows si nécessaire
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    # Création et lancement du bot
    bot = EconomyBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Au revoir !")
    except Exception as e:
        print(f"💥 Erreur critique: {e}")
        sys.exit(1)