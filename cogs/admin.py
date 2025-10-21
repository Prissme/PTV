"""Commandes administrateur pour la gestion d'EcoBot."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from database.db import DatabaseError
from utils import embeds

logger = logging.getLogger(__name__)


class Admin(commands.Cog):
    """Commandes réservées au propriétaire du bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database

    @commands.command(name="addbalance")
    @commands.is_owner()
    async def add_balance(self, ctx: commands.Context, user: discord.Member, amount: int) -> None:
        """Admin: Ajouter du solde à un utilisateur."""

        if amount == 0:
            await ctx.send(embed=embeds.warning_embed("Le montant doit être différent de zéro."))
            return

        try:
            before, after = await self.database.increment_balance(
                user.id,
                amount,
                transaction_type="admin_adjustment",
                description=f"Ajustement manuel par {ctx.author.id}",
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        await ctx.send(
            embed=embeds.success_embed(
                f"Solde de {user.mention} mis à jour : {embeds.format_currency(before)} → {embeds.format_currency(after)}",
                title="Solde modifié",
            )
        )
        logger.info(
            "Admin add_balance",
            extra={
                "admin_id": ctx.author.id,
                "target_id": user.id,
                "amount": amount,
                "before": before,
                "after": after,
            },
        )

    @commands.command(name="resetuser")
    @commands.is_owner()
    async def reset_user(self, ctx: commands.Context, user: discord.Member) -> None:
        """Admin: Réinitialiser un utilisateur."""

        await self.database.ensure_user(user.id)
        balance = await self.database.fetch_balance(user.id)
        if balance > 0:
            await self.database.increment_balance(
                user.id,
                -balance,
                transaction_type="admin_reset",
                description=f"Réinitialisation par {ctx.author.id}",
            )

        async with self.database.transaction() as connection:
            await connection.execute("DELETE FROM user_pets WHERE user_id = $1", user.id)
            await connection.execute("DELETE FROM pet_openings WHERE user_id = $1", user.id)
            await connection.execute("UPDATE users SET last_daily = NULL, pet_last_claim = NULL WHERE user_id = $1", user.id)
            await connection.execute("UPDATE user_xp SET total_xp = 0, level = 1 WHERE user_id = $1", user.id)

        await ctx.send(
            embed=embeds.success_embed(
                f"Données d'{user.mention} réinitialisées.",
                title="Utilisateur réinitialisé",
            )
        )
        logger.info("Admin reset_user", extra={"admin_id": ctx.author.id, "target_id": user.id})

    @commands.command(name="dbstats")
    @commands.is_owner()
    async def db_stats(self, ctx: commands.Context) -> None:
        """Admin: Statistiques de la base de données."""

        try:
            stats = await self.database.get_database_stats()
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        description = (
            f"Utilisateurs : **{int(stats['users_count'])}**\n"
            f"Transactions : **{int(stats['transactions_count'])}**\n"
            f"Pets détenus : **{int(stats['pets_count'])}**\n"
            f"Trades terminés : **{int(stats['trades_completed'])}/{int(stats['trades_count'])}**\n"
            f"Richesse totale : **{embeds.format_currency(int(stats['total_balance']))}**"
        )
        embed = embeds.info_embed(description, title="Statistiques base de données")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
