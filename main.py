"""Point d'entrée du bot Discord EcoBot."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import sys
import time
from contextlib import suppress
from typing import Dict, Optional, Sequence

sys.path.append(os.path.expanduser("~/.local/lib/python3.12/site-packages"))

import discord
from aiohttp import web
from discord.ext import commands

from config import DATABASE_URL, LOG_LEVEL, OWNER_ID, PREFIX, TOKEN, PET_DEFINITIONS
from utils.localization import DEFAULT_LANGUAGE

# FIX: Allow tuning of the health check server operations timeout via environment.
HEALTH_CHECK_TIMEOUT = int(os.getenv("HEALTH_TIMEOUT", "10"))
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
            "potions",
            "clans",
            "help",
            "plaza",
            "admin",
            "stats",
            "language",
        )
        self._market_tip_cooldowns: Dict[int, float] = {}
        self._dm_tip_cooldowns: Dict[int, float] = {}
        self._market_followup_tips: tuple[str, ...] = (
            "Passe en MP pour gérer tes commandes, puis reviens ici pour négocier tes meilleurs deals 🤝",
            "Le MP c’est la base pour gérer tranquille, mais les vraies affaires se concluent ici dans le marché 💼",
            "Optimise tes commandes en privé et garde ce salon pour décrocher tes partenaires de trade 🔄",
        )
        # Autorise certains raccourcis sans préfixe pour les commandes les plus utilisées.
        self._prefixless_aliases: Dict[str, str] = {
            "pets": "pets",
            "eggs": "eggs",
            "claim": "claim",
        }

    async def get_prefix(self, message: discord.Message) -> str | Sequence[str]:
        prefixes = await super().get_prefix(message)
        if isinstance(prefixes, str):
            prefixes = [prefixes]

        content = (message.content or "").strip()
        if content:
            first_token = content.split(maxsplit=1)[0].casefold()
            command_name = self._prefixless_aliases.get(first_token)
            if command_name and self.get_command(command_name) is not None:
                augmented = list(prefixes)
                augmented.append("")
                # Supprime les doublons en préservant l'ordre pour la stabilité.
                seen: set[str] = set()
                unique = [prefix for prefix in augmented if not (prefix in seen or seen.add(prefix))]
                return tuple(unique)

        return prefixes

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
                # FIX: Bound the health server startup to avoid hangs during boot.
                await asyncio.wait_for(
                    self.health_server.start(), timeout=HEALTH_CHECK_TIMEOUT
                )
            except Exception:
                logger.exception(
                    "Le serveur de health check n'a pas pu démarrer. Poursuite en mode dégradé."
                )

    async def close(self) -> None:  # pragma: no cover - cycle de vie discord.py
        if self.health_server is not None:
            try:
                # FIX: Bound the health server shutdown to avoid indefinite waits.
                await asyncio.wait_for(
                    self.health_server.stop(), timeout=HEALTH_CHECK_TIMEOUT
                )
            except Exception:
                logger.exception("Impossible d'arrêter proprement le serveur de health check")

        await super().close()

    async def on_ready(self) -> None:  # pragma: no cover - callback Discord
        assert self.user is not None
        logger.info("Connecté en tant que %s (%s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name=f"EcoBot | {PREFIX}help"))

    async def on_command_error(self, context: commands.Context, exception: Exception) -> None:
        await ErrorHandler.handle_command_error(context, exception)

    async def get_user_language(self, user_id: int) -> str:
        try:
            return await self.database.get_user_language(user_id)
        except DatabaseError:
            logger.exception("Impossible de récupérer la langue de l'utilisateur %s", user_id)
            return DEFAULT_LANGUAGE

    async def set_user_language(self, user_id: int, language: str) -> str:
        try:
            return await self.database.set_user_language(user_id, language)
        except DatabaseError:
            logger.exception(
                "Impossible de mettre à jour la langue de l'utilisateur %s", user_id
            )
            return DEFAULT_LANGUAGE

    async def on_command_completion(self, ctx: commands.Context) -> None:
        await self._maybe_notify_private_usage(ctx)
        await self._maybe_send_first_command_help(ctx)

    async def _maybe_notify_private_usage(self, ctx: commands.Context) -> None:
        author = ctx.author
        channel = ctx.channel

        if author.bot:
            return

        try:
            now = time.monotonic()
        except Exception:
            now = time.time()

        allowed_mentions = discord.AllowedMentions.none()

        if isinstance(channel, discord.DMChannel):
            last_notice = self._dm_tip_cooldowns.get(author.id, 0.0)
            if now - last_notice < 1800:
                return

            message = (
                "✅ Tu utilises le bot en privé, parfait ! Ici tu peux tout gérer sans déranger personne 🔒.\n"
                "Profite-en pour préparer tes ventes et passe ensuite sur le salon du marché pour trouver des partenaires !"
            )
            with suppress(discord.HTTPException, discord.Forbidden):
                await ctx.send(message, allowed_mentions=allowed_mentions)
                self._dm_tip_cooldowns[author.id] = now
            return

        base_channel = None
        if isinstance(channel, discord.TextChannel):
            base_channel = channel
        elif isinstance(channel, discord.Thread):
            base_channel = channel.parent

        if base_channel is None:
            return

        channel_name = base_channel.name.lower()
        if "marche" not in channel_name and "marché" not in channel_name:
            return

        last_tip = self._market_tip_cooldowns.get(author.id, 0.0)
        if now - last_tip < 3600:
            return

        intro_message = (
            "💡 Astuce : tu peux aussi utiliser cette commande **en message privé** avec moi 😉\n"
            "Gère tes inventaires sans bruit en MP, puis viens dans ce salon pour échanger avec les autres joueurs : c’est là que les meilleures affaires se font !"
        )

        with suppress(discord.HTTPException, discord.Forbidden):
            await ctx.send(intro_message, delete_after=20, allowed_mentions=allowed_mentions)
            self._market_tip_cooldowns[author.id] = now

            if random.random() < 0.25:
                followup = random.choice(self._market_followup_tips)
                await ctx.send(followup, delete_after=20, allowed_mentions=allowed_mentions)

    async def _maybe_send_first_command_help(self, ctx: commands.Context) -> None:
        author = ctx.author
        if getattr(author, "bot", False):
            return

        try:
            should_send = await self.database.should_send_help_dm(author.id)
        except DatabaseError:
            logger.exception(
                "Impossible de vérifier l'état d'envoi du help automatique pour %s", author.id
            )
            return

        if not should_send:
            return

        help_cog = self.get_cog("Help")
        embed: discord.Embed | None = None
        build_all_embed = getattr(help_cog, "_build_all_embed", None)
        if callable(build_all_embed):
            try:
                embed = build_all_embed()
            except Exception:
                logger.exception("Échec de la génération du menu d'aide pour l'envoi automatique")

        if embed is None:
            embed = discord.Embed(
                title="EcoBot — Centre d'aide",
                description=f"Utilise {PREFIX}help pour découvrir toutes les commandes du bot.",
                color=discord.Color.blurple(),
            )

        try:
            await author.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.debug(
                "Impossible d'envoyer le help automatique à %s: %s", author.id, exc
            )
        finally:
            try:
                await self.database.mark_help_dm_sent(author.id)
            except DatabaseError:
                logger.exception(
                    "Impossible d'enregistrer l'envoi du help automatique pour %s", author.id
                )


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

    try:
        synced = await database.sync_pets(PET_DEFINITIONS)
    except Exception:
        logger.exception("Synchronisation des pets échouée lors du démarrage")
        raise
    else:
        logger.info("Catalogue des pets synchronisé (%d entrées)", len(synced))

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
