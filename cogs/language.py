"""Commands allowing users to switch EcoBot's response language."""
from __future__ import annotations

from typing import Callable, Awaitable, Optional

from discord.ext import commands

from utils import embeds
from utils.localization import DEFAULT_LANGUAGE

BotLanguageSetter = Callable[[int, str], Awaitable[str]]
BotLanguageGetter = Callable[[int], Awaitable[str]]


class Language(commands.Cog):
    """Expose a couple of prefix commands to change the user language."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _get_language_setter(self) -> Optional[BotLanguageSetter]:
        setter = getattr(self.bot, "set_user_language", None)
        if callable(setter):
            return setter

        database = getattr(self.bot, "database", None)
        if database is not None and hasattr(database, "set_user_language"):
            async def _setter(user_id: int, language: str) -> str:
                return await database.set_user_language(user_id, language)

            return _setter
        return None

    def _get_language_getter(self) -> Optional[BotLanguageGetter]:
        getter = getattr(self.bot, "get_user_language", None)
        if callable(getter):
            return getter

        database = getattr(self.bot, "database", None)
        if database is not None and hasattr(database, "get_user_language"):
            async def _getter(user_id: int) -> str:
                return await database.get_user_language(user_id)

            return _getter
        return None

    async def _update_language(
        self,
        ctx: commands.Context,
        target_language: str,
        *,
        success_title: str,
        success_message: str,
        already_title: str,
        already_message: str,
    ) -> None:
        getter = self._get_language_getter()
        setter = self._get_language_setter()

        current_language = DEFAULT_LANGUAGE
        if getter is not None:
            current_language = await getter(ctx.author.id)

        if current_language == target_language:
            await ctx.send(embed=embeds.info_embed(already_message, title=already_title))
            return

        if setter is not None:
            await setter(ctx.author.id, target_language)

        await ctx.send(embed=embeds.success_embed(success_message, title=success_title))

    @commands.command(name="english", aliases=("en",))
    async def set_english(self, ctx: commands.Context) -> None:
        """Switch the caller language preference to English."""

        await self._update_language(
            ctx,
            "en",
            success_title="Language updated",
            success_message=(
                "All of your future commands will now reply in English. "
                "Use `e!french` to switch back to French."
            ),
            already_title="No change",
            already_message="You're already set to English.",
        )

    @commands.command(name="french", aliases=("fr", "français", "francais"))
    async def set_french(self, ctx: commands.Context) -> None:
        """Switch the caller language preference back to French."""

        await self._update_language(
            ctx,
            "fr",
            success_title="Langue mise à jour",
            success_message=(
                "Le bot te répondra désormais en français. "
                "Utilise `e!english` pour repasser en anglais."
            ),
            already_title="Aucun changement",
            already_message="Tu utilises déjà le français.",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Language(bot))
