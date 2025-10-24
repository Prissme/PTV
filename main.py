"""Point d'entrée du bot Discord EcoBot."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from contextlib import suppress
from typing import Optional

import discord
from aiohttp import web
from discord.ext import commands

from config import DATABASE_URL, LOG_LEVEL, OWNER_ID, PREFIX, TOKEN
from database.db import Database, DatabaseError

logger = logging.getLogger(__name__)


class HealthCheckServer:
    """Expose un endpoint HTTP minimal utilisé par Koyeb pour le health check."""

    def __init__(self, *, host: str = "0.0.0.0", port: int | None = None) -> None:
        self._host = host
        self._logger = logging.getLogger(f"{__name__}.HealthCheckServer")
        resolved_port: Optional[int]
        if port is not None:
            resolved_port = port
        else:
            env_port = os.getenv("PORT")
            resolved_port = None
            if env_port:
                try:
                    resolved_port = int(env_port)
                except ValueError:
                    self._logger.warning(
                        "Valeur de PORT invalide (%s), utilisation du port par défaut 8000",
                        env_port,
                    )
            if resolved_port is None:
                resolved_port = 8000
        self._port = resolved_port
        self._app = web.Application()
        self._app.router.add_get("/", self.health_check)
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    async def health_check(self, request: web.Request) -> web.Response:
        return web.Response(text="Bot is alive", status=200)

    async def start(self) -> None:
        if self._runner is not None:
            return

        try:
            runner = web.AppRunner(self._app)
            await runner.setup()
            site = web.TCPSite(runner, self._host, self._port)
            await site.start()
        except Exception:
            self._logger.exception("Échec du démarrage du serveur HTTP de health check")
            await self.stop()
            raise

        self._runner = runner
        self._site = site
        self._logger.info(
            "Serveur de health check opérationnel sur %s:%s", self._host, self._port
        )

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._logger.info("Serveur de health check arrêté")


def configure_logging() -> None:
    """Configure un logger standardisé pour l'application."""

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


class EcoBot(commands.Bot):
    """Bot Discord spécialisé pour l'économie et l'XP."""

    def __init__(
        self,
        database: Database,
        *,
        prefix: str,
        intents: discord.Intents,
        health_server: Optional[HealthCheckServer] = None,
    ) -> None:
        super().__init__(command_prefix=commands.when_mentioned_or(prefix), intents=intents, help_command=None)
        self.database = database
        self.health_server = health_server
        self.initial_extensions: tuple[str, ...] = (
            "economy",
            "grades",
            "leaderboard",
            "pets",
            "clans",
            "help",
            "trade",
            "admin",
            "stats",
        )

    async def setup_hook(self) -> None:  # pragma: no cover - cycle de vie discord.py
        await super().setup_hook()

        for extension in self.initial_extensions:
            try:
                await self.load_extension(f"cogs.{extension}")
                logger.info("Extension chargée: %s", extension)
            except Exception:  # pragma: no cover - log uniquement
                logger.exception("Impossible de charger l'extension %s", extension)

        if self.health_server is not None:
            try:
                await self.health_server.start()
            except Exception:
                logger.exception(
                    "Le serveur de health check n'a pas pu démarrer. Poursuite en mode dégradé."
                )

    async def close(self) -> None:  # pragma: no cover - cycle de vie discord.py
        if self.health_server is not None:
            try:
                await self.health_server.stop()
            except Exception:
                logger.exception("Impossible d'arrêter proprement le serveur de health check")

        await super().close()

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

    health_server = HealthCheckServer()
    bot = EcoBot(database, prefix=PREFIX, intents=intents, health_server=health_server)

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
