"""Fonctions utilitaires pour générer des embeds homogènes."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Sequence

import discord
from discord.ext import commands

from config import Colors, Emojis, PREFIX

__all__ = [
    "format_currency",
    "cooldown_embed",
    "error_embed",
    "warning_embed",
    "success_embed",
    "info_embed",
    "balance_embed",
    "daily_embed",
    "transfer_embed",
    "leaderboard_embed",
    "inventory_embed",
    "public_bank_embed",
    "transaction_log_embed",
]


def format_currency(amount: int) -> str:
    """Formate un montant en mettant des séparateurs de milliers."""

    return f"{amount:,} PB".replace(",", " ")


def base_embed(title: str, description: str, *, color: int) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = datetime.utcnow()
    return embed


def cooldown_embed(command: str, remaining: float) -> discord.Embed:
    minutes, seconds = divmod(int(remaining), 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return base_embed(
        f"{Emojis.COOLDOWN} Cooldown actif",
        f"Tu pourras réutiliser `{command}` dans **{' '.join(parts)}**.",
        color=Colors.WARNING,
    )


def error_embed(message: str, *, title: str = "Erreur") -> discord.Embed:
    return base_embed(f"{Emojis.ERROR} {title}", message, color=Colors.ERROR)


def warning_embed(message: str, *, title: str = "Attention") -> discord.Embed:
    return base_embed(f"{Emojis.WARNING} {title}", message, color=Colors.WARNING)


def success_embed(message: str, *, title: str = "Succès") -> discord.Embed:
    return base_embed(f"{Emojis.SUCCESS} {title}", message, color=Colors.SUCCESS)


def info_embed(message: str, *, title: str = "Information") -> discord.Embed:
    return base_embed(f"ℹ️ {title}", message, color=Colors.INFO)


def balance_embed(member: discord.Member, *, balance: int, bank_balance: int = 0) -> discord.Embed:
    description = f"{Emojis.MONEY} **Solde portefeuille :** {format_currency(balance)}"
    if bank_balance:
        description += f"\n{Emojis.BANK} **Banque privée :** {format_currency(bank_balance)}"
    embed = base_embed("Solde", description, color=Colors.SUCCESS if balance else Colors.NEUTRAL)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.set_footer(text=f"Utilise {PREFIX}daily et {PREFIX}work pour progresser !")
    return embed


def daily_embed(member: discord.Member, *, amount: int, bonus: int = 0) -> discord.Embed:
    description = f"Tu as reçu {format_currency(amount)} aujourd'hui."
    if bonus:
        description += f"\n🎉 Bonus chanceux : {format_currency(bonus)}"
    embed = base_embed(f"{Emojis.DAILY} Récompense quotidienne", description, color=Colors.SUCCESS)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Reviens dans 24h pour ta prochaine récompense")
    return embed


def transfer_embed(*, sender: discord.Member, receiver: discord.Member, net_amount: int, tax_amount: int, tax_rate: float, new_balance: int) -> discord.Embed:
    description = (
        f"{sender.mention} a envoyé {format_currency(net_amount + tax_amount)} à {receiver.mention}.\n"
        f"{Emojis.MONEY} Reçu par {receiver.display_name}: {format_currency(net_amount)}"
    )
    if tax_amount:
        description += f"\n{Emojis.TAX} Taxe ({tax_rate:.0f}%): {format_currency(tax_amount)}"
    description += f"\n{Emojis.MONEY} Nouveau solde : {format_currency(new_balance)}"
    return base_embed(f"{Emojis.TRANSFER} Transfert réussi", description, color=Colors.SUCCESS)


def leaderboard_embed(title: str, entries: Sequence[tuple[int, int]], bot: commands.Bot, *, symbol: str) -> discord.Embed:
    embed = base_embed(title, "", color=Colors.GOLD)
    lines: List[str] = []
    for index, (user_id, value) in enumerate(entries, start=1):
        user = bot.get_user(user_id)
        name = user.display_name if user else f"Utilisateur {user_id}"
        lines.append(f"**{index}.** {name} — {symbol} {value:,}")
    embed.description = "\n".join(lines) if lines else "Aucune donnée disponible."
    return embed


def inventory_embed(member: discord.Member, items: Iterable[dict]) -> discord.Embed:
    embed = base_embed(f"{Emojis.INVENTORY} Inventaire", "", color=Colors.INFO)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    for entry in items:
        description = entry.get("description", "Aucune description")
        purchase_date = entry.get("purchase_date")
        if purchase_date:
            description += f"\nAcheté le {discord.utils.format_dt(purchase_date, 'R')}"
        embed.add_field(name=entry.get("name", "Item"), value=description, inline=False)
    if not embed.fields:
        embed.description = "Tu n'as pas encore d'items. Rendez-vous dans la boutique !"
    return embed


def public_bank_embed(stats: dict) -> discord.Embed:
    description = (
        f"Solde actuel : {format_currency(stats['balance'])}\n"
        f"Total collecté : {format_currency(stats['total_deposited'])}\n"
        f"Total redistribué : {format_currency(stats['total_withdrawn'])}"
    )
    embed = base_embed(f"{Emojis.PUBLIC_BANK} Banque publique", description, color=Colors.INFO)
    if stats.get("last_activity"):
        embed.set_footer(text=f"Dernière activité : {discord.utils.format_dt(stats['last_activity'], 'R')}")
    return embed


def transaction_log_embed(member: discord.Member, entries: Sequence) -> discord.Embed:
    embed = base_embed(f"{Emojis.MONEY} Historique de {member.display_name}", "", color=Colors.INFO)
    for entry in entries:
        timestamp = discord.utils.format_dt(entry.timestamp, "R")
        delta = format_currency(entry.amount)
        balance = format_currency(entry.balance_after)
        description = f"{timestamp} — {entry.transaction_type}\nVariation : {delta}\nSolde : {balance}"
        if entry.description:
            description += f"\n{entry.description}"
        embed.add_field(name="Transaction", value=description, inline=False)
    if not embed.fields:
        embed.description = "Aucune transaction enregistrée."
    return embed

