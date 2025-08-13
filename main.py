import discord
from discord.ext import commands
import asyncio
import logging
import os
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

@bot.event
async def on_ready():
    """Événement déclenché quand le bot est prêt"""
    logger.info(f"✅ {bot.user} est connecté et prêt !")
    logger.info(f"📊 Connecté à {len(bot.guilds)} serveur(s)")

@bot.event
async def on_command_error(ctx, error):
    """Gestion globale des erreurs de commandes"""
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
    else:
        logger.error(f"Erreur non gérée dans {ctx.command}: {error}")
        await ctx.send("❌ **Une erreur inattendue s'est produite.**")

@bot.event
async def on_guild_join(guild):
    """Événement quand le bot rejoint un serveur"""
    logger.info(f"✅ Bot ajouté au serveur: {guild.name} ({guild.id})")

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

async def load_cogs():
    """Charge automatiquement tous les cogs"""
    cogs_dir = Path("cogs")
    cogs_loaded = 0
    cogs_failed = 0
    
    if not cogs_dir.exists():
        logger.warning("📁 Dossier 'cogs' introuvable, création...")
        cogs_dir.mkdir()
        return
    
    # Lister tous les fichiers .py dans le dossier cogs
    cog_files = [f.stem for f in cogs_dir.glob("*.py") if f.stem != "__init__"]
    
    if not cog_files:
        logger.warning("⚠️ Aucun cog trouvé dans le dossier 'cogs'")
        return
    
    logger.info(f"🔄 Chargement de {len(cog_files)} cog(s)...")
    
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
        await ctx.send(f"✅ **Cog '{cog_name}' rechargé avec succès !**")
        logger.info(f"🔄 Cog '{cog_name}' rechargé par {ctx.author}")
    except Exception as e:
        await ctx.send(f"❌ **Erreur lors du rechargement de '{cog_name}': {e}**")
        logger.error(f"Erreur reload {cog_name}: {e}")

@bot.command(name='load')
@commands.is_owner()
async def load_cog(ctx, cog_name: str):
    """[OWNER] Charge un cog"""
    try:
        await bot.load_extension(f'cogs.{cog_name}')
        await ctx.send(f"✅ **Cog '{cog_name}' chargé avec succès !**")
        logger.info(f"➕ Cog '{cog_name}' chargé par {ctx.author}")
    except Exception as e:
        await ctx.send(f"❌ **Erreur lors du chargement de '{cog_name}': {e}**")
        logger.error(f"Erreur load {cog_name}: {e}")

@bot.command(name='unload')
@commands.is_owner()
async def unload_cog(ctx, cog_name: str):
    """[OWNER] Décharge un cog"""
    try:
        await bot.unload_extension(f'cogs.{cog_name}')
        await ctx.send(f"✅ **Cog '{cog_name}' déchargé avec succès !**")
        logger.info(f"➖ Cog '{cog_name}' déchargé par {ctx.author}")
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
        cogs_list = "\n".join([f"✅ `{cog}`" for cog in loaded_cogs])
        embed.add_field(name="Cogs Actifs", value=cogs_list, inline=False)
    else:
        embed.add_field(name="Aucun Cog", value="Aucun cog chargé", inline=False)
    
    embed.set_footer(text=f"Utilisez {PREFIX}reload <cog> pour recharger")
    await ctx.send(embed=embed)

async def main():
    """Fonction principale pour démarrer le bot"""
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
        await bot.start(TOKEN)
        
    except KeyboardInterrupt:
        logger.info("👋 Arrêt du bot demandé par l'utilisateur")
    except Exception as e:
        logger.error(f"💥 Erreur fatale: {e}")
        raise
    finally:
        # Nettoyer la base de données
        if database and database.pool:
            try:
                await database.close()
                logger.info("🔌 Connexion à la base fermée")
            except Exception as e:
                logger.error(f"Erreur fermeture DB: {e}")

async def run_with_health_server():
    """Lance le bot avec le serveur de santé"""
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
        # Attendre que l'une des tâches se termine
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
        # Annuler les tâches restantes
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
                
    except KeyboardInterrupt:
        logger.info("👋 Arrêt en cours...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    try:
        asyncio.run(run_with_health_server())
    except KeyboardInterrupt:
        print("\n👋 Au revoir !")
    except Exception as e:
        print(f"💥 Erreur lors du démarrage: {e}")
        raise