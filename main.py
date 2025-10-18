"""Point d'entrée du bot d'économie.

Le fichier configure le logger, initialise la base de données puis lance le bot
Discord.  Toute la configuration dynamique (token, préfixe, etc.) est stockée
dans :mod:`config`.  Les cogs chargés reposent sur ``bot.database`` pour
interagir avec PostgreSQL.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from contextlib import suppress
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    DATABASE_URL,
    LOG_LEVEL,
    OWNER_ID,
    PREFIX,
    TOKEN,
)
from database.db import Database, DatabaseError

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Configure un logger formaté pour Replit."""

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


class EcoBot(commands.Bot):
    """Instance principale du bot.

    L'objet étend :class:`commands.Bot` afin d'ajouter une référence directe à
    la base de données et de centraliser le chargement des extensions.
    """

    def __init__(self, database: Database, *, prefix: str, intents: discord.Intents) -> None:
        super().__init__(command_prefix=commands.when_mentioned_or(prefix), intents=intents, help_command=None)
        self.database = database
        self.initial_extensions: tuple[str, ...] = (
            "transaction_logs",
            "economy",
            "shop",
            "xp_system",
            "bank",
            "public_bank",
            "roulette",
            "games",
            "steal",
            "leaderboard",
            "help",
        )
        self.launch_time: datetime = datetime.utcnow()

    async def setup_hook(self) -> None:  # pragma: no cover - discord.py lifecycle
        """Chargement automatique des extensions et synchronisation des slash commands."""

        for extension in self.initial_extensions:
            try:
                await self.load_extension(f"cogs.{extension}")
                logger.info("Extension chargée: %s", extension)
            except Exception:  # pragma: no cover - logging only
                logger.exception("Échec de chargement de l'extension %s", extension)

        with suppress(discord.HTTPException):
            synced = await self.tree.sync()
            logger.info("%d commandes slash synchronisées", len(synced))

    async def on_ready(self) -> None:  # pragma: no cover - discord.py lifecycle
        logger.info("Connecté en tant que %s (%s)", self.user, self.user and self.user.id)
        await self.change_presence(activity=discord.Game(name="EcoBot | /help"))

    async def on_command_error(self, context: commands.Context, exception: Exception) -> None:
        await ErrorHandler.handle_command_error(context, exception)

    async def on_application_command_error(self, interaction: discord.Interaction, exception: Exception) -> None:
        await ErrorHandler.handle_slash_error(interaction, exception)


class ErrorHandler:
    """Gestion centralisée des erreurs pour les commandes."""

    @staticmethod
    async def handle_command_error(ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏱️ Patiente encore {error.retry_after:.1f}s", delete_after=5)
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ Tu n'as pas la permission d'utiliser cette commande", delete_after=5)
            return
        if isinstance(error, commands.UserInputError):
            await ctx.send(f"⚠️ Entrée invalide: {error}", delete_after=10)
            return

        logger.exception("Erreur de commande", exc_info=error)
        await ctx.send("❌ Une erreur inattendue est survenue", delete_after=10)

    @staticmethod
    async def handle_slash_error(interaction: discord.Interaction, error: Exception) -> None:
        if interaction.response.is_done():
            sender = interaction.followup
        else:
            sender = interaction.response

        if isinstance(error, app_commands.CheckFailure):
            await sender.send("❌ Tu n'as pas la permission d'utiliser cette commande", ephemeral=True)
            return
        if isinstance(error, app_commands.CommandOnCooldown):
            await sender.send(f"⏱️ Patiente encore {error.retry_after:.1f}s", ephemeral=True)
            return

        logger.exception("Erreur de slash commande", exc_info=error)
        await sender.send("❌ Une erreur inattendue est survenue", ephemeral=True)


async def start_bot() -> None:
    """Initialise la base de données et lance le bot."""

    configure_logging()
    database = Database(DATABASE_URL)
    await database.connect()

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True

    bot = EcoBot(database, prefix=PREFIX, intents=intents)

    if OWNER_ID:
        bot.owner_ids = {OWNER_ID}

    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signal_name, lambda: asyncio.create_task(bot.close()))

    try:
        await bot.start(TOKEN)
    finally:
        await database.close()


async def main() -> None:
    try:
        await start_bot()
    except DatabaseError:
        logger.critical("Impossible de démarrer le bot: base de données inaccessible")
    except KeyboardInterrupt:  # pragma: no cover - manual stop
        logger.info("Arrêt manuel reçu")


if __name__ == "__main__":
    asyncio.run(main())
