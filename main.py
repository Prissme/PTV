import discord
from discord.ext import commands
import asyncio
import logging
import os
import signal
import sys
import json
import asyncpg
from pathlib import Path

# Imports locaux
from config import TOKEN, PREFIX, DATABASE_URL, LOG_LEVEL, HEALTH_PORT
from database.db import Database

# Import du serveur de santé
try:
    from health_server import HealthServer
    HEALTH_SERVER_AVAILABLE = True
except ImportError:
    HEALTH_SERVER_AVAILABLE = False
    logging.warning("⚠️ health_server.py non trouvé, pas de health check")

# Configuration du logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration des intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.guild_messages = True

# Initialisation du bot
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# Base de données globale
database = None

# Flag pour arrêt propre
shutdown_flag = False

def signal_handler(signum, frame):
    """Gestionnaire pour arrêt propre"""
    global shutdown_flag
    logger.info(f"Signal {signum} reçu, arrêt en cours...")
    shutdown_flag = True

# Installer les gestionnaires de signaux (sauf sur Windows)
if sys.platform != "win32":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

# ==================== BOT EVENTS ====================

@bot.event
async def on_ready():
    """Événement déclenché quand le bot est prêt"""
    logger.info(f"✅ {bot.user} est connecté et prêt !")
    logger.info(f"📊 Connecté à {len(bot.guilds)} serveur(s)")
    
    # Synchroniser les slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔄 {len(synced)} slash command(s) synchronisée(s)")
    except Exception as e:
        logger.error(f"❌ Erreur sync slash commands: {e}")

@bot.event
async def on_command_error(ctx, error):
    """Gestion globale des erreurs de commandes"""
    # Ignorer les erreurs déjà gérées par les cogs
    if hasattr(ctx.command, 'has_error_handler') and ctx.command.has_error_handler():
        return
    
    if isinstance(error, commands.CommandNotFound):
        return  # Ignorer les commandes inexistantes
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ **Argument manquant !**\nUtilise `{PREFIX}help` pour voir l'aide.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ **Argument invalide !**\nUtilise `{PREFIX}help` pour voir l'aide.")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏰ **Cooldown !** Réessaye dans {error.retry_after:.1f} secondes.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ **Tu n'as pas les permissions nécessaires !**")
    elif isinstance(error, commands.BotMissingPermissions):
        missing_perms = ", ".join(error.missing_permissions)
        await ctx.send(f"❌ **Le bot n'a pas les permissions nécessaires :** {missing_perms}")
    elif isinstance(error, commands.NotOwner):
        await ctx.send("❌ **Seul le propriétaire du bot peut utiliser cette commande !**")
    else:
        logger.error(f"Erreur non gérée dans {ctx.command}: {error}")
        await ctx.send("❌ **Une erreur inattendue s'est produite.**")

@bot.event
async def on_application_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    """Gestion globale des erreurs des slash commands"""
    if isinstance(error, discord.app_commands.CommandOnCooldown):
        embed = discord.Embed(
            title="⏰ Cooldown actif !",
            description=f"Tu pourras utiliser cette commande dans **{error.retry_after:.1f}** secondes.",
            color=0xff9900
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            pass
    elif isinstance(error, discord.app_commands.MissingPermissions):
        embed = discord.Embed(
            title="❌ Permissions insuffisantes",
            description="Tu n'as pas les permissions nécessaires pour utiliser cette commande.",
            color=0xff0000
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            pass
    else:
        logger.error(f"Erreur slash command non gérée: {error}")
        embed = discord.Embed(
            title="❌ Erreur",
            description="Une erreur inattendue s'est produite.",
            color=0xff0000
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            pass

@bot.event
async def on_guild_join(guild):
    """Événement quand le bot rejoint un serveur"""
    logger.info(f"✅ Bot ajouté au serveur: {guild.name} ({guild.id}) - {guild.member_count} membres")

@bot.event
async def on_guild_remove(guild):
    """Événement quand le bot quitte un serveur"""
    logger.info(f"❌ Bot retiré du serveur: {guild.name} ({guild.id})")

async def setup_database():
    """Connecte la base de données et l'attache au bot"""
    global database
    try:
        database = Database(dsn=DATABASE_URL)
        await database.connect()
        bot.database = database  # Rendre la DB accessible aux cogs
        logger.info("✅ Base de données connectée avec succès")
        return True
    except Exception as e:
        logger.error(f"❌ Erreur de connexion à la base de données: {e}")
        return False

async def setup_transaction_logs():
    """Configure le système de logs de transactions"""
    transaction_logs_cog = bot.get_cog('TransactionLogs')
    if transaction_logs_cog:
        # Rendre le système de logs accessible à tous les cogs
        bot.transaction_logs = transaction_logs_cog
        logger.info("✅ Système de logs de transactions configuré")
    else:
        logger.warning("⚠️ Cog TransactionLogs non trouvé")

async def load_cogs():
    """Charge automatiquement tous les cogs dans l'ordre correct"""
    cogs_dir = Path("cogs")
    cogs_loaded = 0
    cogs_failed = 0
    
    if not cogs_dir.exists():
        logger.warning("📁 Dossier 'cogs' introuvable, création...")
        cogs_dir.mkdir()
        return
    
    # Charger d'abord TransactionLogs en priorité
    priority_cogs = ['transaction_logs']
    
    for cog_name in priority_cogs:
        try:
            await bot.load_extension(f'cogs.{cog_name}')
            logger.info(f"✅ Cog prioritaire '{cog_name}' chargé avec succès")
            cogs_loaded += 1
        except Exception as e:
            logger.error(f"❌ Erreur lors du chargement du cog prioritaire '{cog_name}': {e}")
            cogs_failed += 1
    
    # Configurer le système de logs après le chargement de TransactionLogs
    await setup_transaction_logs()
    
    # Lister tous les autres fichiers .py dans le dossier cogs
    cog_files = [f.stem for f in cogs_dir.glob("*.py") if f.stem != "__init__" and f.stem not in priority_cogs]
    
    if not cog_files:
        logger.warning("⚠️ Aucun autre cog trouvé dans le dossier 'cogs'")
        return
    
    logger.info(f"🔄 Chargement de {len(cog_files)} cog(s) supplémentaires...")
    
    for cog_name in cog_files:
        try:
            await bot.load_extension(f'cogs.{cog_name}')
            logger.info(f"✅ Cog '{cog_name}' chargé avec succès")
            cogs_loaded += 1
        except Exception as e:
            logger.error(f"❌ Erreur lors du chargement du cog '{cog_name}': {e}")
            cogs_failed += 1
    
    logger.info(f"📊 Résultat: {cogs_loaded} cog(s) chargé(s), {cogs_failed} échec(s)")

# Commandes de gestion des cogs (pour l'owner)
@bot.command(name='reload')
@commands.is_owner()
async def reload_cog(ctx, cog_name: str):
    """[OWNER] Recharge un cog"""
    try:
        await bot.reload_extension(f'cogs.{cog_name}')
        
        # Reconfigurer les logs si TransactionLogs a été rechargé
        if cog_name == 'transaction_logs':
            await setup_transaction_logs()
        
        await ctx.send(f"✅ **Cog '{cog_name}' rechargé avec succès !**")
        logger.info(f"🔄 Cog '{cog_name}' rechargé par {ctx.author}")
        
        # Re-synchroniser les slash commands après reload
        try:
            synced = await bot.tree.sync()
            logger.info(f"🔄 {len(synced)} slash command(s) re-synchronisée(s)")
        except Exception as e:
            logger.error(f"Erreur re-sync après reload: {e}")
            
    except Exception as e:
        await ctx.send(f"❌ **Erreur lors du rechargement de '{cog_name}': {e}**")
        logger.error(f"Erreur reload {cog_name}: {e}")

@bot.command(name='load')
@commands.is_owner()
async def load_cog(ctx, cog_name: str):
    """[OWNER] Charge un cog"""
    try:
        await bot.load_extension(f'cogs.{cog_name}')
        
        # Reconfigurer les logs si TransactionLogs a été chargé
        if cog_name == 'transaction_logs':
            await setup_transaction_logs()
        
        await ctx.send(f"✅ **Cog '{cog_name}' chargé avec succès !**")
        logger.info(f"➕ Cog '{cog_name}' chargé par {ctx.author}")
        
        # Re-synchroniser les slash commands après load
        try:
            synced = await bot.tree.sync()
            logger.info(f"🔄 {len(synced)} slash command(s) re-synchronisée(s)")
        except Exception as e:
            logger.error(f"Erreur re-sync après load: {e}")
            
    except Exception as e:
        await ctx.send(f"❌ **Erreur lors du chargement de '{cog_name}': {e}**")
        logger.error(f"Erreur load {cog_name}: {e}")

@bot.command(name='unload')
@commands.is_owner()
async def unload_cog(ctx, cog_name: str):
    """[OWNER] Décharge un cog"""
    if cog_name.lower() in ['economy', 'help', 'transaction_logs']:
        await ctx.send(f"❌ **Le cog '{cog_name}' ne peut pas être déchargé (cog critique).**")
        return
    
    try:
        await bot.unload_extension(f'cogs.{cog_name}')
        await ctx.send(f"✅ **Cog '{cog_name}' déchargé avec succès !**")
        logger.info(f"➖ Cog '{cog_name}' déchargé par {ctx.author}")
        
        # Re-synchroniser les slash commands après unload
        try:
            synced = await bot.tree.sync()
            logger.info(f"🔄 {len(synced)} slash command(s) re-synchronisée(s)")
        except Exception as e:
            logger.error(f"Erreur re-sync après unload: {e}")
            
    except Exception as e:
        await ctx.send(f"❌ **Erreur lors du déchargement de '{cog_name}': {e}**")
        logger.error(f"Erreur unload {cog_name}: {e}")

@bot.command(name='cogs')
@commands.is_owner()
async def list_cogs(ctx):
    """[OWNER] Liste les cogs chargés"""
    loaded_cogs = [name.split('.')[-1] for name in bot.extensions.keys()]
    
    embed = discord.Embed(
        title="🔧 Cogs Chargés",
        description=f"**{len(loaded_cogs)}** cog(s) actuellement chargé(s)",
        color=0x0099ff
    )
    
    if loaded_cogs:
        cogs_list = "\n".join([f"✅ `{cog}`" for cog in sorted(loaded_cogs)])
        embed.add_field(name="Cogs Actifs", value=cogs_list, inline=False)
    else:
        embed.add_field(name="Aucun Cog", value="Aucun cog chargé", inline=False)
    
    # Afficher le nombre de slash commands
    slash_count = len(bot.tree.get_commands())
    embed.add_field(name="Slash Commands", value=f"`{slash_count}` commande(s) slash", inline=True)
    
    # Statut de la base de données
    db_status = "🟢 Connectée" if database and database.pool else "🔴 Déconnectée"
    embed.add_field(name="Base de données", value=db_status, inline=True)
    
    # Statut des logs de transactions
    logs_status = "🟢 Actif" if hasattr(bot, 'transaction_logs') else "🔴 Inactif"
    embed.add_field(name="Logs Transactions", value=logs_status, inline=True)
    
    embed.set_footer(text=f"Utilisez {PREFIX}reload <cog> pour recharger")
    await ctx.send(embed=embed)

@bot.command(name='sync')
@commands.is_owner()
async def sync_slash_commands(ctx):
    """[OWNER] Force la synchronisation des slash commands"""
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ **{len(synced)} slash command(s) synchronisée(s) !**")
        logger.info(f"🔄 Sync manuelle: {len(synced)} slash command(s)")
    except Exception as e:
        await ctx.send(f"❌ **Erreur lors de la synchronisation: {e}**")
        logger.error(f"Erreur sync manuelle: {e}")

async def cleanup():
    """Nettoyage propre des ressources"""
    global database
    
    logger.info("🧹 Nettoyage en cours...")
    
    # Fermer la base de données
    if database and database.pool:
        try:
            await database.close()
            logger.info("🔌 Connexion à la base fermée")
        except Exception as e:
            logger.error(f"Erreur fermeture DB: {e}")
    
    # Fermer le bot
    if not bot.is_closed():
        try:
            await bot.close()
            logger.info("🤖 Bot fermé")
        except Exception as e:
            logger.error(f"Erreur fermeture bot: {e}")

async def main():
    """Fonction principale pour démarrer le bot"""
    global shutdown_flag
    
    try:
        # 1. D'ABORD connecter la base de données
        logger.info("🔌 Connexion à la base de données...")
        if not await setup_database():
            logger.error("💥 Impossible de se connecter à la base de données, arrêt.")
            return
        
        # 2. ENSUITE charger les cogs (maintenant que bot.database existe)
        logger.info("📦 Chargement des cogs...")
        await load_cogs()
        
        # 3. ENFIN démarrer le bot
        logger.info("🚀 Démarrage du bot Discord...")
        
        # Démarrer le bot en arrière-plan
        bot_task = asyncio.create_task(bot.start(TOKEN))
        
        # Boucle de vérification du flag d'arrêt
        while not shutdown_flag and not bot.is_closed():
            await asyncio.sleep(1)
        
        # Arrêt demandé
        if not bot.is_closed():
            logger.info("🛑 Arrêt du bot...")
            bot_task.cancel()
            
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        
    except KeyboardInterrupt:
        logger.info("👋 Arrêt du bot demandé par l'utilisateur")
        shutdown_flag = True
    except Exception as e:
        logger.error(f"💥 Erreur fatale: {e}")
        raise
    finally:
        await cleanup()

async def run_with_health_server():
    """Lance le bot avec le serveur de santé"""
    global shutdown_flag
    
    tasks = []
    
    # Tâche principale du bot
    bot_task = asyncio.create_task(main())
    tasks.append(bot_task)
    
    # Serveur de santé si disponible
    if HEALTH_SERVER_AVAILABLE:
        health_server = HealthServer(port=HEALTH_PORT)
        health_task = asyncio.create_task(health_server.run_forever())
        tasks.append(health_task)
        logger.info("🏥 Serveur de santé configuré")
    
    try:
        # Attendre que l'une des tâches se termine ou que l'arrêt soit demandé
        while not shutdown_flag:
            done, pending = await asyncio.wait(tasks, timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
            
            if done:
                # Une tâche s'est terminée
                break
        
        # Annuler toutes les tâches restantes
        for task in tasks:
            if not task.done():
                task.cancel()
        
        # Attendre que toutes les tâches se terminent proprement
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
                
    except KeyboardInterrupt:
        logger.info("👋 Arrêt en cours...")
        shutdown_flag = True
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    try:
        # Gestion propre des signaux sur Windows
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
        asyncio.run(run_with_health_server())
    except KeyboardInterrupt:
        print("\n👋 Au revoir !")
    except Exception as e:
        print(f"💥 Erreur lors du démarrage: {e}")
        raise