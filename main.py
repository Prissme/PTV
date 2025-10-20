"""Point d'entrée du bot Discord EcoBot."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from contextlib import suppress

import discord
from discord.ext import commands

from config import DATABASE_URL, LOG_LEVEL, OWNER_ID, PREFIX, TOKEN
from database.db import Database, DatabaseError

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Configure un logger standardisé pour l'application."""

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


class EcoBot(commands.Bot):
    """Bot Discord spécialisé pour l'économie et l'XP."""

    def __init__(self, database: Database, *, prefix: str, intents: discord.Intents) -> None:
        super().__init__(command_prefix=commands.when_mentioned_or(prefix), intents=intents, help_command=None)
        self.database = database
        self.initial_extensions: tuple[str, ...] = (
            "economy",
            "xp_system",
            "leaderboard",
            "help",
        )

    async def setup_hook(self) -> None:  # pragma: no cover - cycle de vie discord.py
        for extension in self.initial_extensions:
            try:
                await self.load_extension(f"cogs.{extension}")
                logger.info("Extension chargée: %s", extension)
            except Exception:  # pragma: no cover - log uniquement
                logger.exception("Impossible de charger l'extension %s", extension)

    async def on_ready(self) -> None:  # pragma: no cover - callback Discord
        assert self.user is not None
        logger.info("Connecté en tant que %s (%s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name=f"EcoBot | {PREFIX}help"))

    async def on_command_error(self, context: commands.Context, exception: Exception) -> None:
        await ErrorHandler.handle_command_error(context, exception)


class ErrorHandler:
    """Gestionnaire centralisé des erreurs de commandes."""

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


async def start_bot() -> None:
    """Initialise la base de données et démarre le bot."""

    configure_logging()
    database = Database(DATABASE_URL)
    await database.connect()

    intents = discord.Intents.default()
    intents.message_content = True
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
    except KeyboardInterrupt:  # pragma: no cover - arrêt manuel
        logger.info("Arrêt manuel reçu")


if __name__ == "__main__":
    asyncio.run(main())
