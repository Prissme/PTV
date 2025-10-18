"""Banque privée: dépôts, retraits et maintenance."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    PRIVATE_BANK_DAILY_LIMIT,
    PRIVATE_BANK_DEPOSIT_TAX,
    PRIVATE_BANK_FEE_THRESHOLD,
    PRIVATE_BANK_MAINTENANCE_FEE,
    PRIVATE_BANK_MAX_BALANCE,
)
from utils import embeds

logger = logging.getLogger(__name__)


def bank_embed(member: discord.Member, account: dict) -> discord.Embed:
    description = (
        f"Solde banque : {embeds.format_currency(account['balance'])}\n"
        f"Total déposé : {embeds.format_currency(account['total_deposited'])}\n"
        f"Total retiré : {embeds.format_currency(account['total_withdrawn'])}\n"
        f"Frais payés : {embeds.format_currency(account['total_fees_paid'])}"
    )
    embed = embeds.info_embed(description, title=f"Banque privée de {member.display_name}")
    return embed


class Bank(commands.Cog):
    """Gestion des opérations de banque privée."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database

    async def _apply_maintenance(self, user_id: int) -> dict:
        account = await self.database.get_private_bank_account(user_id)
        balance = account["balance"]
        last_fee_payment = account.get("last_fee_payment")
        now = datetime.now(timezone.utc)
        if balance <= PRIVATE_BANK_FEE_THRESHOLD:
            return account
        if last_fee_payment and (now - last_fee_payment).total_seconds() < 86400:
            return account

        fee = max(int(balance * PRIVATE_BANK_MAINTENANCE_FEE), 1)
        new_balance = max(balance - fee, 0)
        async with self.database.transaction() as conn:
            await conn.execute(
                """
                UPDATE user_bank
                SET balance = $2,
                    total_fees_paid = total_fees_paid + $3,
                    last_fee_payment = NOW()
                WHERE user_id = $1
                """,
                user_id,
                new_balance,
                fee,
            )
        await self.database.add_public_bank_funds(fee)
        await self.bot.transaction_logs.log(
            user_id,
            "bank_fee",
            -fee,
            balance,
            new_balance,
            description="Frais de maintenance bancaire",
        )
        account = await self.database.get_private_bank_account(user_id)
        return account

    async def show_bank(self, ctx_or_inter, member: discord.Member) -> None:
        account = await self._apply_maintenance(member.id)
        embed = bank_embed(member, account)
        await ctx_or_inter.send(embed=embed)

    @commands.command(name="bank")
    async def bank_prefix(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        await self.show_bank(ctx, member or ctx.author)

    @app_commands.command(name="bank", description="Afficher ton compte bancaire")
    async def bank_slash(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.show_bank(interaction.followup, membre or interaction.user)

    # ------------------------------------------------------------------
    # Dépôt / retrait
    # ------------------------------------------------------------------
    async def deposit(self, member: discord.Member, amount: int) -> discord.Embed:
        if amount <= 0:
            return embeds.error_embed("Le montant doit être positif.")

        account = await self._apply_maintenance(member.id)
        tax = max(int(amount * PRIVATE_BANK_DEPOSIT_TAX), 1)
        net = amount - tax
        if net <= 0:
            return embeds.error_embed("Le montant est trop faible après taxe.")
        if account["balance"] + net > PRIVATE_BANK_MAX_BALANCE:
            return embeds.error_embed("Tu dépasses la capacité maximale de la banque.")

        async with self.database.transaction() as conn:
            wallet = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1 FOR UPDATE", member.id)
            if wallet is None or wallet < amount:
                return embeds.error_embed("Solde insuffisant dans ton portefeuille.")

            bank_row = await conn.fetchrow(
                "SELECT balance, daily_deposit, last_deposit_reset FROM user_bank WHERE user_id = $1 FOR UPDATE",
                member.id,
            )
            if bank_row is None:
                await conn.execute("INSERT INTO user_bank (user_id) VALUES ($1)", member.id)
                bank_balance, daily_deposit, last_reset = 0, 0, None
            else:
                bank_balance, daily_deposit, last_reset = bank_row

            today = datetime.utcnow().date()
            if last_reset != today:
                daily_deposit = 0
                await conn.execute(
                    "UPDATE user_bank SET daily_deposit = 0, last_deposit_reset = CURRENT_DATE WHERE user_id = $1",
                    member.id,
                )

            if daily_deposit + amount > PRIVATE_BANK_DAILY_LIMIT:
                return embeds.error_embed("Tu as atteint ta limite de dépôt quotidienne (15k PB).")

            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", amount, member.id)
            await conn.execute(
                """
                UPDATE user_bank
                SET balance = balance + $2,
                    total_deposited = total_deposited + $2,
                    daily_deposit = daily_deposit + $3
                WHERE user_id = $1
                """,
                member.id,
                net,
                amount,
            )

        await self.database.add_public_bank_funds(tax)
        await self.bot.transaction_logs.log(
            member.id,
            "bank_deposit",
            amount,
            wallet,
            wallet - amount,
            description="Dépôt vers la banque privée",
        )
        return embeds.success_embed(f"Dépôt de {embeds.format_currency(net)} effectué.")

    async def withdraw(self, member: discord.Member, amount: int) -> discord.Embed:
        if amount <= 0:
            return embeds.error_embed("Le montant doit être positif.")

        async with self.database.transaction() as conn:
            bank_row = await conn.fetchrow(
                "SELECT balance FROM user_bank WHERE user_id = $1 FOR UPDATE",
                member.id,
            )
            if bank_row is None or bank_row[0] < amount:
                return embeds.error_embed("Solde bancaire insuffisant.")
            wallet = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1 FOR UPDATE", member.id)
            wallet = wallet or 0
            await conn.execute(
                "UPDATE user_bank SET balance = balance - $2, total_withdrawn = total_withdrawn + $2 WHERE user_id = $1",
                member.id,
                amount,
            )
            await conn.execute(
                "UPDATE users SET balance = balance + $2 WHERE user_id = $1",
                member.id,
                amount,
            )

        await self.bot.transaction_logs.log(
            member.id,
            "bank_withdraw",
            amount,
            wallet,
            wallet + amount,
            description="Retrait de la banque privée",
        )
        return embeds.success_embed(f"Retrait de {embeds.format_currency(amount)} effectué.")

    @commands.command(name="deposit")
    async def deposit_prefix(self, ctx: commands.Context, amount: int) -> None:
        embed = await self.deposit(ctx.author, amount)
        await ctx.send(embed=embed)

    @commands.command(name="withdraw")
    async def withdraw_prefix(self, ctx: commands.Context, amount: int) -> None:
        embed = await self.withdraw(ctx.author, amount)
        await ctx.send(embed=embed)

    @app_commands.command(name="deposit", description="Déposer de l'argent")
    async def deposit_slash(self, interaction: discord.Interaction, montant: int) -> None:
        embed = await self.deposit(interaction.user, montant)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="withdraw", description="Retirer de l'argent")
    async def withdraw_slash(self, interaction: discord.Interaction, montant: int) -> None:
        embed = await self.withdraw(interaction.user, montant)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Bank(bot))
