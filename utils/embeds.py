"""Fonctions utilitaires pour créer des embeds cohérents."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping, Optional, Sequence

import discord
from discord.ext import commands

from config import Colors, Emojis, PREFIX, HUGE_PET_NAME, PET_RARITY_COLORS

__all__ = [
    "format_currency",
    "cooldown_embed",
    "error_embed",
    "warning_embed",
    "success_embed",
    "info_embed",
    "balance_embed",
    "daily_embed",
    "leaderboard_embed",
    "xp_profile_embed",
    "pet_animation_embed",
    "pet_reveal_embed",
    "pet_collection_embed",
    "pet_equip_embed",
    "pet_claim_embed",
    "pet_stats_embed",
    "trade_embed",
    "trade_completed_embed",
    "trade_cancelled_embed",
    "transaction_history_embed",
]


def format_currency(amount: int) -> str:
    """Formate un montant d'argent avec séparateur des milliers."""

    return f"{amount:,} PB".replace(",", " ")


def _base_embed(title: str, description: str, *, color: int) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.timestamp = datetime.utcnow()
    return embed


def cooldown_embed(command: str, remaining: float) -> discord.Embed:
    minutes, seconds = divmod(int(max(0, remaining)), 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return _base_embed(
        f"{Emojis.COOLDOWN} Cooldown actif",
        f"Tu pourras réutiliser `{command}` dans **{' '.join(parts)}**.",
        color=Colors.WARNING,
    )


def error_embed(message: str, *, title: str = "Erreur") -> discord.Embed:
    return _base_embed(f"{Emojis.ERROR} {title}", message, color=Colors.ERROR)


def warning_embed(message: str, *, title: str = "Attention") -> discord.Embed:
    return _base_embed(f"{Emojis.WARNING} {title}", message, color=Colors.WARNING)


def success_embed(message: str, *, title: str = "Succès") -> discord.Embed:
    return _base_embed(f"{Emojis.SUCCESS} {title}", message, color=Colors.SUCCESS)


def info_embed(message: str, *, title: str = "Information") -> discord.Embed:
    return _base_embed(title, message, color=Colors.INFO)


def balance_embed(member: discord.Member, *, balance: int) -> discord.Embed:
    description = f"{Emojis.MONEY} **Solde :** {format_currency(balance)}"
    embed = _base_embed("Solde", description, color=Colors.SUCCESS if balance else Colors.NEUTRAL)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.set_footer(text=f"Utilise {PREFIX}daily pour collecter ta récompense")
    return embed


def daily_embed(member: discord.Member, *, amount: int) -> discord.Embed:
    description = f"Tu as reçu {format_currency(amount)} aujourd'hui."
    embed = _base_embed(f"{Emojis.DAILY} Récompense quotidienne", description, color=Colors.SUCCESS)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Reviens demain pour récupérer ta prochaine récompense !")
    return embed


def leaderboard_embed(
    *,
    title: str,
    entries: Sequence[tuple[int, int]],
    bot: commands.Bot,
    symbol: str,
) -> discord.Embed:
    embed = _base_embed(title, "", color=Colors.GOLD)
    lines = []
    for rank, (user_id, value) in enumerate(entries, start=1):
        user = bot.get_user(user_id)
        name = user.display_name if user else f"Utilisateur {user_id}"
        if symbol.upper() == "PB":
            value_display = format_currency(value)
        else:
            value_display = f"{value:,} {symbol}"
        lines.append(f"**{rank}.** {name} — {value_display}")
    embed.description = "\n".join(lines) if lines else "Aucune donnée disponible."
    return embed


def xp_profile_embed(*, member: discord.Member, level: int, total_xp: int, next_requirement: int) -> discord.Embed:
    description = (
        f"Niveau actuel : **{level}**\n"
        f"XP total : {total_xp:,}\n"
        f"XP pour le prochain niveau : {next_requirement:,}"
    )
    embed = _base_embed(f"{Emojis.XP} Profil XP", description, color=Colors.INFO)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    return embed


def pet_animation_embed(*, title: str, description: str) -> discord.Embed:
    return _base_embed(title, description, color=Colors.INFO)


def _pet_title(name: str, rarity: str, is_huge: bool) -> str:
    prefix = "✨ " if is_huge else ""
    suffix = " ✨" if is_huge else ""
    return f"{prefix}{name} ({rarity}){suffix}"


def pet_reveal_embed(
    *,
    name: str,
    rarity: str,
    image_url: str,
    income_per_hour: int,
    is_huge: bool,
) -> discord.Embed:
    color = PET_RARITY_COLORS.get(rarity, Colors.INFO)
    description = f"Revenus passifs : **{income_per_hour:,} PB/h**".replace(",", " ")
    if is_huge and name == HUGE_PET_NAME:
        description += "\n🎉 ÉNORME ! Tu as obtenu une ÉNORME SHELLY ! 🎉"
    embed = _base_embed(_pet_title(name, rarity, is_huge), description, color=color)
    embed.set_image(url=image_url)
    return embed


def pet_collection_embed(
    *,
    member: discord.Member,
    pets: Sequence[Mapping[str, object]],
    total_count: int,
    total_income_per_hour: int,
) -> discord.Embed:
    embed = _base_embed("Ta collection de pets", "", color=Colors.GOLD if pets else Colors.NEUTRAL)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    if not pets:
        embed.description = "Tu n'as encore aucun pet. Ouvre un œuf avec `e!openbox` !"
    else:
        lines: list[str] = []
        for pet in pets:
            name = str(pet["name"])
            rarity = str(pet["rarity"])
            is_active = bool(pet["is_active"])
            is_huge = bool(pet["is_huge"])
            income = int(pet["income"])
            user_pet_id = int(pet["id"])
            acquired_at = pet.get("acquired_at")
            acquired_text = ""
            if isinstance(acquired_at, datetime):
                acquired_text = acquired_at.strftime("%d/%m/%Y")
            star = "⭐ " if is_active else ""
            huge = " ✨" if is_huge else ""
            line = (
                f"{star}**#{user_pet_id} {name}**{huge}\n"
                f"Rareté : {rarity} — {income:,} PB/h".replace(",", " ")
            )
            if acquired_text:
                line += f"\nObtenu le {acquired_text}"
            lines.append(line)
        embed.description = "\n\n".join(lines)

    footer = (
        f"Total de pets : {total_count} • Revenus par heure (actif) : {total_income_per_hour:,} PB/h"
    ).replace(",", " ")
    footer += " • Utilise e!equip [id] pour équiper un pet"
    embed.set_footer(text=footer)
    return embed


def pet_equip_embed(*, member: discord.Member, pet: Mapping[str, object]) -> discord.Embed:
    name = str(pet["name"])
    rarity = str(pet["rarity"])
    image_url = str(pet["image_url"])
    income = int(pet["base_income_per_hour"])
    is_huge = bool(pet.get("is_huge", False))
    embed = pet_reveal_embed(
        name=name,
        rarity=rarity,
        image_url=image_url,
        income_per_hour=income,
        is_huge=is_huge,
    )
    embed.title = f"Tu as équipé {name} !"
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    return embed


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if sec or not parts:
        parts.append(f"{sec}s")
    return " ".join(parts)


def pet_claim_embed(
    *,
    member: discord.Member,
    pet: Mapping[str, object],
    amount: int,
    elapsed_seconds: float,
) -> discord.Embed:
    name = str(pet["name"])
    rarity = str(pet["rarity"])
    image_url = str(pet["image_url"])
    income = int(pet["base_income_per_hour"])
    is_huge = bool(pet.get("is_huge", False))
    color = PET_RARITY_COLORS.get(rarity, Colors.INFO)
    description = (
        f"{name} a généré **{amount:,} PB** en {_format_duration(elapsed_seconds)}."
        if amount
        else f"{name} vient juste de se mettre au travail. Reviens plus tard !"
    ).replace(",", " ")
    description += f"\nRevenus par heure : **{income:,} PB/h**".replace(",", " ")
    embed = _base_embed(_pet_title(name, rarity, is_huge), description, color=color)
    embed.set_thumbnail(url=image_url)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    return embed


def pet_stats_embed(
    *,
    total_openings: int,
    stats: Iterable[tuple[str, int, float, float]],
    huge_count: int,
) -> discord.Embed:
    description_lines = [f"Œufs ouverts : **{total_openings}**"]
    for name, count, actual_rate, theoretical_rate in stats:
        actual_text = f"{actual_rate:.2f}%" if total_openings else "-"
        description_lines.append(
            f"**{name}** — {count} obtentions • Réel : {actual_text} • Théorique : {theoretical_rate:.2f}%"
        )
    description_lines.append(f"Énormes Shellys en circulation : **{huge_count}**")
    embed = _base_embed("Statistiques des pets", "\n".join(description_lines), color=Colors.INFO)
    return embed


def _trade_offer_lines(offer: Mapping[str, object]) -> str:
    lines: list[str] = []
    amount = int(offer.get("pb", 0))
    if amount:
        lines.append(f"• {format_currency(amount)}")

    pets: Iterable[Mapping[str, object]] = offer.get("pets", [])  # type: ignore[assignment]
    for pet in pets:
        user_pet_id = int(pet.get("user_pet_id", 0))
        name = str(pet.get("name", "Pet"))
        rarity = str(pet.get("rarity", "?"))
        is_huge = bool(pet.get("is_huge", False))
        huge = " ✨" if is_huge else ""
        lines.append(f"• #{user_pet_id} {name}{huge} ({rarity})")

    if not lines:
        lines.append("• Rien pour le moment")
    return "\n".join(lines)


def _trade_status_line(offer: Mapping[str, object]) -> str:
    confirmed = bool(offer.get("confirmed", False))
    accepted = bool(offer.get("accepted", False))
    if confirmed:
        return "🔒 Confirmé"
    if accepted:
        return "✅ Accepté"
    return "⏳ En attente"


def trade_embed(
    *,
    trade_id: int,
    user_a: discord.abc.User,
    user_b: discord.abc.User,
    offer_a: Mapping[str, object],
    offer_b: Mapping[str, object],
    expires_in: Optional[float] = None,
) -> discord.Embed:
    description_lines = [f"Échange #{trade_id}"]
    if expires_in is not None:
        description_lines.append(f"Expire dans {_format_duration(expires_in)}")
    description = "\n".join(description_lines)

    embed = _base_embed("🤝 Trade en cours", description, color=Colors.INFO)
    embed.add_field(
        name=f"{user_a.display_name}",
        value=f"{_trade_status_line(offer_a)}\n{_trade_offer_lines(offer_a)}",
        inline=False,
    )
    embed.add_field(
        name=f"{user_b.display_name}",
        value=f"{_trade_status_line(offer_b)}\n{_trade_offer_lines(offer_b)}",
        inline=False,
    )

    embed.set_footer(text="Utilise les boutons pour modifier ton offre. Les validations sont réinitialisées à chaque changement.")
    return embed


def trade_completed_embed(
    *,
    trade_id: int,
    user_a: discord.abc.User,
    user_b: discord.abc.User,
    sent_a: Mapping[str, object],
    sent_b: Mapping[str, object],
    received_a: Mapping[str, object],
    received_b: Mapping[str, object],
) -> discord.Embed:
    embed = _base_embed("✅ Échange finalisé", f"Trade #{trade_id}", color=Colors.SUCCESS)
    embed.add_field(
        name=f"{user_a.display_name} a donné",
        value=_trade_offer_lines(sent_a),
        inline=False,
    )
    embed.add_field(
        name=f"{user_a.display_name} a reçu",
        value=_trade_offer_lines(received_a),
        inline=False,
    )
    embed.add_field(
        name=f"{user_b.display_name} a donné",
        value=_trade_offer_lines(sent_b),
        inline=False,
    )
    embed.add_field(
        name=f"{user_b.display_name} a reçu",
        value=_trade_offer_lines(received_b),
        inline=False,
    )
    embed.set_footer(text="Merci d'avoir utilisé EcoBot pour vos échanges !")
    return embed


def trade_cancelled_embed(*, trade_id: int, reason: str | None = None) -> discord.Embed:
    message = reason or "L'échange a été annulé."
    return _base_embed(f"❌ Trade #{trade_id} annulé", message, color=Colors.ERROR)


def transaction_history_embed(
    *,
    user: discord.abc.User,
    entries: Sequence[Mapping[str, object]],
) -> discord.Embed:
    embed = _base_embed("Historique des échanges", "", color=Colors.INFO)
    embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)

    if not entries:
        embed.description = "Aucun échange enregistré pour le moment."
        return embed

    lines: list[str] = []
    for entry in entries:
        trade_id = int(entry.get("id", 0))
        status = str(entry.get("status", "pending"))
        partner = str(entry.get("partner_name", entry.get("partner_id", "Inconnu")))
        pb_sent = int(entry.get("pb_sent", 0))
        pb_received = int(entry.get("pb_received", 0))
        pets_sent = int(entry.get("pets_sent", 0))
        pets_received = int(entry.get("pets_received", 0))
        created_at = entry.get("created_at")
        completed_at = entry.get("completed_at")

        status_symbol = {
            "completed": "✅",
            "pending": "⏳",
            "cancelled": "❌",
        }.get(status, "⏺️")

        when = ""
        if isinstance(created_at, datetime):
            when = created_at.strftime("%d/%m/%Y %H:%M")
        if isinstance(completed_at, datetime) and status == "completed":
            when += f" → {completed_at.strftime('%d/%m/%Y %H:%M')}"

        lines.append(
            (
                f"{status_symbol} Trade #{trade_id} avec {partner}\n"
                f"Envoyé : {format_currency(pb_sent)} / {pets_sent} pet(s) — "
                f"Reçu : {format_currency(pb_received)} / {pets_received} pet(s)"
            )
            + (f"\n{when}" if when else "")
        )

    embed.description = "\n\n".join(lines)
    return embed
