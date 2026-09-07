"""Point d'entrée du bot Discord (EcoBot).

Ce fichier construit le bot, se connecte à la base de données, charge les
cogs, démarre un petit serveur HTTP de "health check" pour Koyeb, puis
lance le bot.

NOTE IMPORTANTE :
------------------
Ce fichier avait disparu (remplacé par une copie de ``config.py``, qui ne
contient que des constantes et aucun code de démarrage). Résultat : le
process se terminait normalement dès la fin du script (``exit 0``), sans
jamais se connecter à Discord, et la plateforme le relançait en boucle.

``cogs/stats.py`` est volontairement exclu de la liste ci-dessous : ce
fichier est une quasi-copie de ``cogs/admin.py`` (même classe ``Admin``,
mêmes commandes). Les charger tous les deux ferait planter le bot au
démarrage avec une erreur de commandes en double
(``CommandRegistrationError``). Vérifie ce fichier, renomme/adapte-le si tu
veux vraiment un cog "stats" distinct, puis ajoute-le à la liste.
"""
from __future__ import annotations

import asyncio
import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

from config import DATABASE_URL, LOG_LEVEL, OWNER_ID, PREFIX, TOKEN
from database.db import Database

logging.basicConfig(
    level=getattr(logging, str(LOG_LEVEL).upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ecobot")

# Cogs à charger au démarrage. `cogs.stats` est exclu volontairement, voir
# la note en haut du fichier.
INITIAL_EXTENSIONS = (
    "cogs.admin",
    "cogs.clans",
    "cogs.drops",
    "cogs.economy",
    "cogs.event_anniversaire",
    "cogs.event_pinata",
    "cogs.grades",
    "cogs.help",
    "cogs.language",
    "cogs.leaderboard",
    "cogs.pets",
    "cogs.plaza",
    "cogs.potions",
)


class EcoBot(commands.Bot):
    """Bot Discord principal, avec accès à la base de données via `self.database`."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        # Nécessaire pour lire le contenu des messages (commandes préfixées
        # `e!...`) et pour les vérifications basées sur les rôles.
        # Ces deux intents sont "privilégiés" : ils doivent aussi être
        # activés dans le Discord Developer Portal, onglet "Bot".
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=commands.when_mentioned_or(PREFIX),
            intents=intents,
            owner_id=OWNER_ID or None,
            help_command=None,  # cogs/help.py fournit sa propre commande d'aide
        )

        self.database: Database | None = None
        self._health_runner: web.AppRunner | None = None

    async def setup_hook(self) -> None:
        logger.info("Connexion à la base de données...")
        self.database = Database(DATABASE_URL)
        await self.database.connect()
        logger.info("Base de données connectée.")

        for extension in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info("Cog chargé : %s", extension)
            except Exception:
                logger.exception("Échec du chargement du cog %s", extension)

        self._health_runner = await _start_health_server()

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info(
            "Connecté en tant que %s (%s) — %d serveur(s)",
            self.user,
            self.user.id,
            len(self.guilds),
        )

    async def close(self) -> None:
        if self.database is not None:
            await self.database.close()
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await super().close()


async def _start_health_server() -> web.AppRunner:
    """Démarre un serveur HTTP minimal pour le health check Koyeb (`/`)."""

    port = int(os.getenv("PORT", "8000"))

    async def handle_health(_request: web.Request) -> web.Response:
        return web.Response(text="OK")

    app = web.Application()
    app.router.add_get("/", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Serveur de health check démarré sur le port %d", port)
    return runner


async def main() -> None:
    bot = EcoBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arrêt du bot demandé.")
