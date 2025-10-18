"""Cog responsable de l'historique des transactions."""
from __future__ import annotations

import logging
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import embeds

logger = logging.getLogger(__name__)


class TransactionLogs(commands.Cog):
    """Expose les commandes permettant de consulter l'historique."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database

    async def cog_load(self) -> None:
        self.bot.transaction_logs = self  # type: ignore[attr-defined]
        logger.info("TransactionLogs prêt")

    async def log(
        self,
        user_id: int,
        transaction_type: str,
        amount: int,
        balance_before: int,
        balance_after: int,
        *,
        description: str = "",
        related_user_id: Optional[int] = None,
    ) -> None:
        await self.database.log_transaction(
            user_id,
            transaction_type,
            amount,
            balance_before,
            balance_after,
            description=description,
            related_user_id=related_user_id,
        )

    async def _send_logs(self, ctx_or_inter: Any, member: discord.Member, page: int) -> None:
        limit = 10
        offset = (page - 1) * limit
        entries, total = await self.database.fetch_transactions(member.id, limit=limit, offset=offset)
        embed = embeds.transaction_log_embed(member, entries)
        embed.set_footer(text=f"Page {page} • {total} transactions au total")
        await ctx_or_inter.send(embed=embed)

    @commands.command(name="transactions")
    async def transactions_prefix(self, ctx: commands.Context, member: Optional[discord.Member] = None, page: int = 1) -> None:
        """Commande prefix pour afficher les transactions."""

        member = member or ctx.author
        page = max(page, 1)
        await self._send_logs(ctx, member, page)

    @app_commands.command(name="transactions", description="Consulter l'historique des transactions")
    @app_commands.describe(page="Numéro de page", membre="Utilisateur ciblé")
    async def transactions_slash(self, interaction: discord.Interaction, page: int = 1, membre: Optional[discord.Member] = None) -> None:
        member = membre or interaction.user
        page = max(page, 1)
        await interaction.response.defer(ephemeral=True)
        await self._send_logs(interaction.followup, member, page)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TransactionLogs(bot))
