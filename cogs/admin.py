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

    @commands.command(name="addpb")
    @commands.is_owner()
    async def add_pb(
        self,
        ctx: commands.Context,
        user_id: int,
        amount: int,
        *,
        reason: str | None = None,
    ) -> None:
        """Admin: Ajouter des PB via l'identifiant utilisateur."""

        if amount <= 0:
            await ctx.send(embed=embeds.warning_embed("Le montant doit être supérieur à 0."))
            return

        description = reason or f"Ajout manuel par {ctx.author.id}"

        try:
            before, after = await self.database.increment_balance(
                user_id,
                amount,
                transaction_type="admin_grant",
                description=description,
            )
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        member = None
        if ctx.guild is not None:
            member = ctx.guild.get_member(user_id)
        user_obj = member or self.bot.get_user(user_id)
        if user_obj is None:
            target_display = f"Utilisateur {user_id}"
        else:
            target_display = user_obj.mention

        lines = [
            f"Cible : {target_display}",
            f"Ajout : {embeds.format_currency(amount)}",
            f"Solde avant : {embeds.format_currency(before)}",
            f"Solde après : {embeds.format_currency(after)}",
        ]
        if reason:
            lines.append(f"Raison : {reason}")

        await ctx.send(embed=embeds.success_embed("\n".join(lines), title="PB ajoutés"))

        logger.info(
            "Admin add_pb",
            extra={
                "admin_id": ctx.author.id,
                "target_id": user_id,
                "amount": amount,
                "before": before,
                "after": after,
                "reason": reason,
            },
        )

    @commands.command(name="givepet", aliases=("transferpet",))
    @commands.is_owner()
    async def give_pet(
        self,
        ctx: commands.Context,
        source: discord.User,
        target: discord.User,
        *,
        pet: str,
    ) -> None:
        """Admin: Transférer un pet d'un utilisateur à un autre."""

        if source.id == target.id:
            await ctx.send(
                embed=embeds.warning_embed("L'utilisateur source et la cible doivent être différents."),
            )
            return

        pet_identifier = pet.strip()
        if not pet_identifier:
            await ctx.send(
                embed=embeds.error_embed(
                    "Précise l'identifiant ou le nom du pet à transférer.",
                    title="Pet introuvable",
                )
            )
            return

        await self.database.ensure_user(source.id)
        await self.database.ensure_user(target.id)

        transfer_result = None
        last_error: str | None = None

        if pet_identifier.isdigit():
            try:
                transfer_result = await self.database.admin_transfer_user_pet(
                    source.id,
                    target.id,
                    int(pet_identifier),
                )
            except DatabaseError as exc:
                last_error = str(exc)
        else:
            candidates = await self.database.get_user_pet_by_name(source.id, pet_identifier)
            if not candidates:
                await ctx.send(
                    embed=embeds.error_embed(
                        f"{source.mention} n'a aucun pet nommé '{pet_identifier}'.",
                        title="Pet introuvable",
                    )
                )
                return

            ordered_candidates = sorted(
                candidates,
                key=lambda record: (bool(record["is_active"]), record["acquired_at"]),
            )

            for candidate in ordered_candidates:
                try:
                    transfer_result = await self.database.admin_transfer_user_pet(
                        source.id,
                        target.id,
                        int(candidate["id"]),
                    )
                except DatabaseError as exc:
                    last_error = str(exc)
                    continue
                else:
                    last_error = None
                    break

        if transfer_result is None:
            message = last_error or "Impossible de transférer ce pet pour le moment."
            await ctx.send(embed=embeds.error_embed(message))
            return

        flags: list[str] = []
        if transfer_result["is_huge"]:
            flags.append("Huge")
        if transfer_result["is_gold"]:
            flags.append("Or")
        if transfer_result["is_rainbow"]:
            flags.append("Rainbow")

        pet_description = f"**{transfer_result['name']}** ({transfer_result['rarity']})"
        if flags:
            pet_description += " – " + ", ".join(flags)

        lines = [
            f"Source : {source.mention} (`{source.id}`)",
            f"Cible : {target.mention} (`{target.id}`)",
            f"Pet transféré : {pet_description}",
            f"ID inventaire : #{transfer_result['id']}",
            f"Revenu de base : {embeds.format_currency(int(transfer_result['base_income_per_hour']))} PB/h",
        ]

        await ctx.send(embed=embeds.success_embed("\n".join(lines), title="Pet transféré"))

        logger.info(
            "Admin give_pet",
            extra={
                "admin_id": ctx.author.id,
                "source_id": source.id,
                "target_id": target.id,
                "user_pet_id": int(transfer_result["id"]),
                "pet_name": str(transfer_result["name"]),
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

        await self.database.reset_user_grade(user.id)

        grade_cog = self.bot.get_cog("GradeSystem")
        if grade_cog and hasattr(grade_cog, "_assign_grade_role"):
            try:
                await grade_cog._assign_grade_role(user, 0)  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Impossible de retirer les rôles de grade pour %s", user.id)

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
            f"Annonces actives : **{int(stats['listings_active'])}/{int(stats['listings_count'])}**\n"
            f"Richesse totale : **{embeds.format_currency(int(stats['total_balance']))}**"
        )
        embed = embeds.info_embed(description, title="Statistiques base de données")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
