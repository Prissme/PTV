"""Fonctions utilitaires pour créer des embeds cohérents."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Collection, Iterable, Mapping, Optional, Sequence

import discord
from discord.ext import commands

from config import (
    Colors,
    Emojis,
    GOLD_PET_MULTIPLIER,
    PET_EMOJIS,
    PET_RARITY_COLORS,
    PREFIX,
    GradeDefinition,
    PetDefinition,
    RAINBOW_PET_MULTIPLIER,
)

_BRANDING_REPLACEMENTS: dict[str, str] = {
    "Freescape": "PrissCup",
    "freescape": "prisscup",
    "FREESCAPE": "PRISSCUP",
}


def _apply_branding(text: str | None) -> str | None:
    """Remplace les anciennes références de marque par les nouvelles."""

    if text is None:
        return None
    replaced = text
    for old, new in _BRANDING_REPLACEMENTS.items():
        replaced = replaced.replace(old, new)
    return replaced


def _finalize_embed(embed: discord.Embed) -> discord.Embed:
    """Applique les règles de branding à l'embed fourni."""

    if embed.title:
        embed.title = _apply_branding(embed.title) or embed.title
    if embed.description:
        embed.description = _apply_branding(embed.description) or embed.description

    footer = embed.footer
    if footer and footer.text:
        embed.set_footer(text=_apply_branding(footer.text) or footer.text, icon_url=footer.icon_url)

    author = embed.author
    if author and author.name:
        embed.set_author(
            name=_apply_branding(author.name) or author.name,
            url=author.url,
            icon_url=author.icon_url,
        )

    for index, field in enumerate(embed.fields):
        new_name = _apply_branding(field.name) or field.name
        new_value = _apply_branding(field.value) or field.value
        if new_name != field.name or new_value != field.value:
            embed.set_field_at(index, name=new_name, value=new_value, inline=field.inline)

    return embed

__all__ = [
    "format_currency",
    "cooldown_embed",
    "error_embed",
    "warning_embed",
    "success_embed",
    "info_embed",
    "balance_embed",
    "daily_embed",
    "slot_machine_embed",
    "mastermind_board_embed",
    "leaderboard_embed",
    "stats_overview_embed",
    "user_activity_embed",
    "grade_profile_embed",
    "grade_completed_embed",
    "pet_animation_embed",
    "pet_reveal_embed",
    "pet_collection_embed",
    "pet_index_embed",
    "pet_equip_embed",
    "pet_claim_embed",
    "clan_overview_embed",
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
    return _finalize_embed(embed)


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
    return _finalize_embed(embed)


def daily_embed(member: discord.Member, *, amount: int) -> discord.Embed:
    description = f"Tu as reçu {format_currency(amount)} aujourd'hui."
    embed = _base_embed(f"{Emojis.DAILY} Récompense quotidienne", description, color=Colors.SUCCESS)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Reviens demain pour récupérer ta prochaine récompense !")
    return _finalize_embed(embed)


def slot_machine_embed(
    *,
    member: discord.Member,
    bet: int,
    reels: Sequence[str],
    payout: int,
    multiplier: int,
    balance_after: int,
    result_text: str,
) -> discord.Embed:
    net = payout - bet
    gain_line = format_currency(payout) if payout else "0 PB"
    multiplier_text = f" (x{multiplier})" if multiplier else ""

    lines = [
        f"**Rouleaux :** {' | '.join(reels)}",
        f"**Mise :** {format_currency(bet)}",
        f"**Gain :** {gain_line}{multiplier_text}",
        f"**Solde actuel :** {format_currency(balance_after)}",
        "",
    ]
    if result_text:
        lines.append(result_text)

    if net > 0:
        lines.append(f"Profit net : **+{format_currency(net)}**")
        color = Colors.GOLD
    elif net == 0:
        lines.append("Équilibre parfait : mise récupérée.")
        color = Colors.INFO
    else:
        lines.append(f"Perte : **-{format_currency(-net)}**")
        color = Colors.ERROR

    embed = _base_embed("🎰 Machine à sous", "\n".join(lines), color=color)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    return _finalize_embed(embed)


def mastermind_board_embed(
    *,
    member: discord.Member,
    palette: Sequence[tuple[str, str]],
    code_length: int,
    max_attempts: int,
    timeout: int,
    attempts: Sequence[tuple[int, str, int, int]],
    attempts_left: int,
    current_selection: str,
    status_lines: Sequence[str] | None = None,
    color: int = Colors.INFO,
) -> discord.Embed:
    palette_line = ", ".join(f"{emoji} {name.capitalize()}" for name, emoji in palette)
    description_lines = [
        f"Devine la combinaison de **{code_length}** couleurs pour décrocher des PB.",
        f"Palette : {palette_line}",
        "Les couleurs peuvent se répéter.",
        f"Tu disposes de **{max_attempts}** tentatives et de {timeout}s par interaction.",
        "Compose ta tentative avec les boutons ci-dessous puis valide-la.",
    ]
    embed = _base_embed("🧠 Mastermind", "\n".join(description_lines), color=color)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    if attempts:
        history_lines = [
            f"**{attempt}.** {guess} • ✅ {well} • ⚪ {misplaced}"
            for attempt, guess, well, misplaced in attempts
        ]
        embed.add_field(
            name="Historique des tentatives",
            value="\n".join(history_lines),
            inline=False,
        )

    progress_value = f"Tentatives utilisées : **{len(attempts)}/{max_attempts}**"
    state_lines = [progress_value]
    state_lines.append(f"Sélection : {current_selection}")
    state_lines.append(f"Tentatives restantes : **{max(0, attempts_left)}**")

    field_name = "Tentative en cours" if attempts_left > 0 else "Partie terminée"
    embed.add_field(name=field_name, value="\n".join(state_lines), inline=False)

    if status_lines:
        embed.add_field(name="Résultat", value="\n".join(status_lines), inline=False)

    return _finalize_embed(embed)


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
        elif symbol.upper() == "RAP":
            value_display = f"{value:,} RAP".replace(",", " ")
        else:
            value_display = f"{value:,} {symbol}".replace(",", " ")
        lines.append(f"**{rank}.** {name} — {value_display}")
    embed.description = "\n".join(lines) if lines else "Aucune donnée disponible."
    return _finalize_embed(embed)


def stats_overview_embed(
    *,
    guild: discord.Guild,
    total_messages: int,
    active_members: int,
    tracked_members: int,
    active_window: timedelta,
) -> discord.Embed:
    window_seconds = max(int(active_window.total_seconds()), 1)
    window_days = window_seconds // 86_400
    if window_days >= 1:
        window_label = f"{window_days} derniers jours"
    else:
        window_hours = max(1, window_seconds // 3_600)
        window_label = f"{window_hours} dernières heures"

    lines = [
        f"Messages suivis : **{total_messages:,}**".replace(",", " "),
        f"Membres suivis : **{tracked_members:,}**".replace(",", " "),
        f"Membres actifs ({window_label}) : **{active_members:,}**".replace(",", " "),
    ]

    embed = _base_embed("📊 Statistiques du serveur", "\n".join(lines), color=Colors.INFO)
    embed.set_author(name=guild.name)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text=f"Fenêtre d'activité : {window_label}")
    return _finalize_embed(embed)


def user_activity_embed(
    *,
    member: discord.Member,
    message_count: int,
    last_message_at: datetime | None,
    rank: int,
    total_tracked: int,
    active_window: timedelta,
) -> discord.Embed:
    lines = [
        f"Messages suivis : **{message_count:,}**".replace(",", " "),
        f"Position : **#{rank}** sur {total_tracked}",
    ]

    if isinstance(last_message_at, datetime):
        timestamp = int(last_message_at.timestamp())
        lines.append(f"Dernier message : <t:{timestamp}:R>")
    else:
        lines.append("Dernier message : aucune donnée")

    window_seconds = max(int(active_window.total_seconds()), 1)
    window_days = window_seconds // 86_400
    if window_days >= 1:
        window_info = f"Classement basé sur {window_days} jours d'activité mesurée."
    else:
        hours = max(1, window_seconds // 3_600)
        window_info = f"Classement basé sur les {hours} dernières heures d'activité."
    lines.append(window_info)

    embed = _base_embed("📊 Statistiques personnelles", "\n".join(lines), color=Colors.INFO)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    return _finalize_embed(embed)


def _quest_progress_line(label: str, current: int, goal: int) -> str:
    if goal <= 0:
        return f"✅ {label} : aucune exigence"
    status = "✅" if current >= goal else "▫️"
    return f"{status} {label} : **{current}/{goal}**"


def grade_profile_embed(
    *,
    member: discord.Member,
    grade_level: int,
    total_grades: int,
    current_grade: GradeDefinition | None,
    next_grade: GradeDefinition | None,
    progress: Mapping[str, int],
    pet_slots: int,
) -> discord.Embed:
    if next_grade is None:
        description = (
            f"Grade actuel : **{current_grade.name if current_grade else 'Aucun'}**"
            f" ({grade_level}/{total_grades})\n"
            f"Slots de pets équipables : **{pet_slots}**\n"
            "🎉 Tu as atteint le grade maximum !"
        )
        quest_lines = ["Toutes les quêtes sont terminées."]
    else:
        description = (
            f"Grade actuel : **{current_grade.name if current_grade else 'Aucun'}**"
            f" ({grade_level}/{total_grades})\n"
            f"Prochain grade : **{next_grade.name}**\n"
            f"Slots de pets équipables : **{pet_slots}**\n"
            f"Prochaine récompense : **{format_currency(next_grade.reward_pb)}** + 1 slot"
        )
        quest_lines = [
            _quest_progress_line("Envoyer des messages", progress.get("messages", 0), next_grade.message_goal),
            _quest_progress_line("Ouvrir des œufs", progress.get("eggs", 0), next_grade.egg_goal),
            _quest_progress_line(
                "Fusionner des pets en or",
                progress.get("gold", 0),
                next_grade.gold_goal,
            ),
        ]

    embed = _base_embed(f"{Emojis.XP} Profil de grade", description, color=Colors.INFO)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.add_field(name="Quêtes", value="\n".join(quest_lines), inline=False)
    return _finalize_embed(embed)


def grade_completed_embed(
    *,
    member: discord.abc.User,
    grade_name: str,
    grade_level: int,
    total_grades: int,
    reward_pb: int,
    balance_after: int,
    pet_slots: int,
) -> discord.Embed:
    lines = [
        f"Nouveau grade : **{grade_name}** ({grade_level}/{total_grades})",
        f"Récompense : **{format_currency(reward_pb)}**",
        f"Slots de pets disponibles : **{pet_slots}**",
        f"Solde actuel : {format_currency(balance_after)}",
    ]
    embed = _base_embed("🎖️ Grade amélioré !", "\n".join(lines), color=Colors.SUCCESS)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    return _finalize_embed(embed)


def pet_animation_embed(*, title: str, description: str) -> discord.Embed:
    return _base_embed(title, description, color=Colors.INFO)


def _pet_emoji(name: str) -> str:
    return PET_EMOJIS.get(name, PET_EMOJIS.get("default", "🐾"))


def _pet_title(
    name: str,
    rarity: str,
    is_huge: bool,
    is_gold: bool,
    *,
    is_rainbow: bool = False,
) -> str:
    rainbow_marker = " 🌈" if is_rainbow else ""
    gold_marker = " 🥇" if is_gold and not is_rainbow else ""
    display_name = f"{_pet_emoji(name)} {name}{rainbow_marker}{gold_marker}".strip()
    if is_rainbow:
        rarity_label = f"{rarity} Rainbow"
    elif is_gold:
        rarity_label = f"{rarity} Or"
    else:
        rarity_label = rarity
    title = f"{display_name} ({rarity_label})"
    if is_huge:
        return f"✨ {title} ✨"
    return title


def pet_reveal_embed(
    *,
    name: str,
    rarity: str,
    image_url: str,
    income_per_hour: int,
    is_huge: bool,
    is_gold: bool,
    is_rainbow: bool = False,
    market_value: int = 0,
) -> discord.Embed:
    base_color = Colors.GOLD if is_gold or is_rainbow else PET_RARITY_COLORS.get(rarity, Colors.INFO)
    description = f"Revenus passifs : **{income_per_hour:,} PB/h**".replace(",", " ")
    if is_huge:
        description += f"\n🎉 Incroyable ! Tu as obtenu **{name}** ! 🎉"
    if is_rainbow:
        description += f"\n🌈 Variante rainbow ! Puissance x{RAINBOW_PET_MULTIPLIER}."
    elif is_gold:
        description += f"\n🥇 Variante or ! Puissance x{GOLD_PET_MULTIPLIER}."
    if market_value > 0 and not is_huge:
        description += f"\nValeur marché : **{format_currency(market_value)}**"
    embed = _base_embed(
        _pet_title(name, rarity, is_huge, is_gold, is_rainbow=is_rainbow),
        description,
        color=base_color,
    )
    embed.set_image(url=image_url)
    return _finalize_embed(embed)


def pet_collection_embed(
    *,
    member: discord.abc.User,
    pets: Sequence[Mapping[str, object]],
    total_count: int,
    total_income_per_hour: int,
    page: int = 1,
    page_count: int = 1,
    huge_descriptions: Mapping[str, str] | None = None,
) -> discord.Embed:
    del huge_descriptions  # Non utilisé dans la version minimaliste

    active_count = sum(1 for pet in pets if bool(pet.get("is_active")))
    header = [
        f"Total : {total_count}",
        f"Actifs : {active_count}",
        f"Revenu actif : {format_currency(total_income_per_hour)}/h",
    ]

    description_lines: list[str] = []
    for pet in pets:
        name = str(pet.get("name", "Pet"))
        rarity = str(pet.get("rarity", "?"))
        is_active = bool(pet.get("is_active"))
        is_huge = bool(pet.get("is_huge"))
        is_gold = bool(pet.get("is_gold"))
        is_rainbow = bool(pet.get("is_rainbow"))
        income = int(pet.get("income", pet.get("base_income_per_hour", 0)))
        tags: list[str] = []
        if is_huge:
            level = int(pet.get("huge_level", 1))
            tags.append(f"Niv. {level}")
        if is_rainbow:
            tags.append("Rainbow")
        elif is_gold:
            tags.append("Gold")
        line_parts = [
            "⭐" if is_active else "",
            _pet_emoji(name),
            name,
            rarity,
            f"{income:,} PB/h".replace(",", " "),
        ]
        if tags:
            line_parts.append(" ".join(tags))
        description_lines.append(
            " ".join(part for part in line_parts if part).replace("  ", " ")
        )

    embed_description = " • ".join(header)
    if description_lines:
        embed_description += "\n\n" + "\n".join(f"• {line}" for line in description_lines)
    else:
        embed_description += "\n\nAucun pet pour le moment. Ouvre un œuf avec e!openbox."

    embed = _base_embed("Inventaire des pets", embed_description, color=Colors.NEUTRAL)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    current_page = max(1, page)
    total_pages = max(1, page_count)
    embed.set_footer(text=f"Page {current_page}/{total_pages}")
    return _finalize_embed(embed)


def _chunk_field_values(lines: Iterable[str], *, limit: int = 1024) -> Iterable[str]:
    chunk: list[str] = []
    length = 0
    for line in lines:
        if not line:
            continue
        line_length = len(line)
        if chunk and length + 1 + line_length > limit:
            yield "\n".join(chunk)
            chunk = [line]
            length = line_length
            continue
        if chunk:
            length += 1
        chunk.append(line)
        length += line_length
    if chunk:
        yield "\n".join(chunk)


def pet_index_embed(
    *,
    member: discord.abc.User,
    pet_definitions: Sequence[PetDefinition],
    owned_names: Collection[str],
    huge_descriptions: Mapping[str, str] | None = None,
) -> discord.Embed:
    embed = _base_embed("Index des pets", "", color=Colors.INFO)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    huge_info = dict(huge_descriptions or {})
    total_pets = len(pet_definitions)
    owned_lookup = {name.casefold() for name in owned_names if name}
    unlocked_count = sum(
        1 for definition in pet_definitions if (definition.name or "").casefold() in owned_lookup
    )
    if total_pets:
        progress_ratio = unlocked_count / total_pets
        embed.description = (
            "Ta progression actuelle dans l'index : **"
            f"{unlocked_count}/{total_pets}** pets découverts ("
            f"{progress_ratio:.0%})."
        )
    else:
        embed.description = "Aucun pet n'est encore enregistré dans l'index."

    lines: list[str] = []
    for definition in pet_definitions:
        name = definition.name
        if not name:
            continue
        rarity = definition.rarity
        is_huge = definition.is_huge
        emoji = _pet_emoji(name)
        status = "✅" if name.casefold() in owned_lookup else "🔒"
        line = f"{status} {emoji} **{name}** — Rareté : {rarity}"
        if is_huge:
            description = huge_info.get(name)
            if description:
                line += f"\n✨ Comment l'obtenir : {description}"
        lines.append(line)

    if lines:
        chunks = list(_chunk_field_values(lines))
        for index, value in enumerate(chunks, start=1):
            field_name = "Catalogue" if len(chunks) == 1 else f"Catalogue ({index})"
            embed.add_field(name=field_name, value=value, inline=False)

    embed.set_footer(
        text=(
            "Les pets dorés comptent également pour l'index. Ouvre un œuf avec e!openbox ou fusionne avec e!goldify !"
        )
    )
    return _finalize_embed(embed)


def pet_equip_embed(
    *,
    member: discord.Member,
    pet: Mapping[str, object],
    activated: bool,
    active_count: int,
    slot_limit: int,
) -> discord.Embed:
    name = str(pet["name"])
    rarity = str(pet["rarity"])
    image_url = str(pet["image_url"])
    income = int(pet["base_income_per_hour"])
    is_huge = bool(pet.get("is_huge", False))
    is_gold = bool(pet.get("is_gold", False))
    is_rainbow = bool(pet.get("is_rainbow", False))
    market_value = int(pet.get("market_value", 0))

    status_symbol = "✅" if activated else "🛌"
    title = f"{status_symbol} {_pet_title(name, rarity, is_huge, is_gold, is_rainbow=is_rainbow)}"
    color = Colors.SUCCESS if activated else Colors.INFO
    lines = [
        "Ce pet génère désormais des revenus passifs !" if activated else "Ce pet se repose pour le moment.",
        f"Rareté : {rarity}",
        f"Revenus : {income:,} PB/h".replace(",", " "),
    ]
    if is_rainbow:
        lines.append(f"Variante rainbow : puissance x{RAINBOW_PET_MULTIPLIER}")
    elif is_gold:
        lines.append(f"Variante or : puissance x{GOLD_PET_MULTIPLIER}")
    if market_value > 0 and not is_huge:
        lines.append(f"Valeur marché : {format_currency(market_value)}")
    lines.append(f"Pets actifs : **{active_count}/{slot_limit}**")

    embed = _base_embed(title, "\n".join(lines), color=color)
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    return _finalize_embed(embed)


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
    pets: Sequence[Mapping[str, object]],
    amount: int,
    elapsed_seconds: float,
    booster: Mapping[str, float] | None = None,
    clan: Mapping[str, object] | None = None,
) -> discord.Embed:
    total_income = sum(int(pet.get("base_income_per_hour", 0)) for pet in pets)
    duration = _format_duration(elapsed_seconds)
    if amount > 0:
        description = f"+{format_currency(amount)} en {duration}"
    else:
        description = "Aucun gain pour l'instant. Reviens plus tard !"

    extra_info: list[str] = []
    if booster:
        multiplier = float(booster.get("multiplier", 1.0))
        if multiplier > 1.0:
            remaining = float(booster.get("remaining_seconds", 0.0))
            info = f"Booster x{multiplier:g}"
            if remaining > 0:
                info += f" ({_format_duration(remaining)} restants)"
            extra_info.append(info)
    if clan:
        clan_name = str(clan.get("name", "Clan"))
        clan_multiplier = float(clan.get("multiplier", 1.0))
        clan_bonus = int(clan.get("bonus", 0))
        clan_line = f"Clan {clan_name}"
        if clan_multiplier > 1.0:
            clan_line += f" x{clan_multiplier:.2f}"
        if clan_bonus > 0:
            clan_line += f" (+{format_currency(clan_bonus)})"
        extra_info.append(clan_line)

    color = Colors.SUCCESS if amount else Colors.INFO
    description_text = description
    if extra_info:
        description_text += "\n" + " • ".join(extra_info)
    embed = _base_embed("Gains des pets", description_text, color=color)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    shares: list[int] = []
    remaining = amount
    for index, pet in enumerate(pets):
        income = int(pet.get("base_income_per_hour", 0))
        if amount > 0 and total_income > 0:
            if index == len(pets) - 1:
                share = remaining
            else:
                share = int(round(amount * (income / total_income)))
                share = max(0, min(remaining, share))
                remaining -= share
        else:
            share = 0
        shares.append(max(0, share))

    if amount > 0 and shares:
        diff = amount - sum(shares)
        if diff > 0:
            shares[-1] += diff

    lines: list[str] = []
    for pet, share in zip(pets, shares):
        name = str(pet.get("name", "Pet"))
        income = int(pet.get("base_income_per_hour", 0))
        is_active = bool(pet.get("is_active", False))
        is_huge = bool(pet.get("is_huge", False))
        is_gold = bool(pet.get("is_gold", False))
        is_rainbow = bool(pet.get("is_rainbow", False))
        level = int(pet.get("huge_level", 1)) if is_huge else 0
        tags: list[str] = []
        if is_active:
            tags.append("Actif")
        if is_huge:
            tags.append(f"Niv. {level}")
        if is_rainbow:
            tags.append("Rainbow")
        elif is_gold:
            tags.append("Gold")
        share_text = f"+{format_currency(share)}" if share > 0 else "0 PB"
        line_parts = [
            _pet_emoji(name),
            name,
            f"{income:,} PB/h".replace(",", " "),
            share_text,
        ]
        if tags:
            line_parts.append(" ".join(tags))
        lines.append(" ".join(part for part in line_parts if part).replace("  ", " "))

    if lines:
        embed.description += "\n\n" + "\n".join(f"• {line}" for line in lines)

    return _finalize_embed(embed)


def clan_overview_embed(
    *,
    clan_name: str,
    banner: str,
    leader_name: str,
    member_count: int,
    capacity: int,
    boost_multiplier: float,
    boost_level: int,
    capacity_level: int,
    members: Sequence[Mapping[str, object]],
    next_capacity_cost: Optional[int] = None,
    next_boost_cost: Optional[int] = None,
) -> discord.Embed:
    header = [
        f"⚔️ Chef : **{leader_name}**",
        f"🧮 Membres : **{member_count}/{capacity}**",
        f"🔥 Turbo PB : **x{boost_multiplier:.2f}** (Niv. boost {boost_level})",
        f"📦 Extension : Niv. {capacity_level}",
    ]
    if next_capacity_cost is not None:
        header.append(f"➕ Slot suivant : {format_currency(next_capacity_cost)}")
    if next_boost_cost is not None:
        header.append(f"🚀 Boost suivant : {format_currency(next_boost_cost)}")

    embed = _base_embed(
        f"{banner} {clan_name} — Salle de guerre",
        "\n".join(header),
        color=Colors.WARNING,
    )

    ranking_lines: list[str] = []
    for index, member in enumerate(members, start=1):
        mention = str(member.get("mention", member.get("display", "?")))
        role = str(member.get("role", "member"))
        contribution = int(member.get("contribution", 0))
        badges: list[str] = []
        if role == "leader":
            badges.append("👑")
        elif role == "officer":
            badges.append("🛡️")
        if contribution > 0:
            badges.append(format_currency(contribution))
        badge_text = f" {' '.join(badges)}" if badges else ""
        ranking_lines.append(f"#{index} {mention}{badge_text}")

    if ranking_lines:
        embed.add_field(name="Tableau de chasse", value="\n".join(ranking_lines[:10]), inline=False)
    else:
        embed.add_field(
            name="Tableau de chasse",
            value="Encore aucun exploit… c'est le moment d'enflammer le chat !",
            inline=False,
        )

    embed.set_footer(
        text="Plus ton clan rugit, plus tes gains explosent. Active-toi et fais trembler le classement !"
    )
    return _finalize_embed(embed)


def pet_stats_embed(
    *,
    total_openings: int,
    stats: Iterable[tuple[str, int, float, float]],
    huge_count: int,
    gold_count: int,
) -> discord.Embed:
    description_lines = [f"Œufs ouverts : **{total_openings}**"]
    for name, count, actual_rate, theoretical_rate in stats:
        actual_text = f"{actual_rate:.2f}%" if total_openings else "-"
        description_lines.append(
            f"**{name}** — {count} obtentions • Réel : {actual_text} • Théorique : {theoretical_rate:.2f}%"
        )
    description_lines.append(f"Énormes Shellys en circulation : **{huge_count}**")
    description_lines.append(f"Pets dorés en circulation : **{gold_count}**")
    embed = _base_embed("Statistiques des pets", "\n".join(description_lines), color=Colors.INFO)
    return _finalize_embed(embed)


def _trade_offer_lines(offer: Mapping[str, object]) -> str:
    lines: list[str] = []
    amount = int(offer.get("pb", 0))
    if amount:
        lines.append(f"• {format_currency(amount)}")

    pets: Iterable[Mapping[str, object]] = offer.get("pets", [])  # type: ignore[assignment]
    for pet in pets:
        name = str(pet.get("name", "Pet"))
        rarity = str(pet.get("rarity", "?"))
        is_huge = bool(pet.get("is_huge", False))
        is_gold = bool(pet.get("is_gold", False))
        is_rainbow = bool(pet.get("is_rainbow", False))
        huge = " ✨" if is_huge else ""
        rainbow = " 🌈" if is_rainbow else ""
        gold = " 🥇" if is_gold and not is_rainbow else ""
        emoji = _pet_emoji(name)
        emoji_prefix = f"{emoji} " if emoji else ""
        rarity_label = (
            f"{rarity} Rainbow" if is_rainbow else f"{rarity} Or" if is_gold else rarity
        )
        lines.append(f"• {emoji_prefix}{name}{huge}{rainbow}{gold} ({rarity_label})")

    if not lines:
        lines.append("• Rien pour le moment")
    return "\n".join(lines)


def _trade_status_line(offer: Mapping[str, object]) -> str:
    accepted = bool(offer.get("accepted", False))
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

    embed.set_footer(
        text=(
            "Utilise les boutons pour modifier ton offre. Les validations sont réinitialisées à chaque changement."
            " Lorsque tout le monde accepte, l'échange se finalise automatiquement."
        )
    )
    return _finalize_embed(embed)


def trade_completed_embed(
    *,
    trade_id: int,
    user_a: discord.abc.User,
    user_b: discord.abc.User,
    sent_a: Mapping[str, object],
    sent_b: Mapping[str, object],
    received_a: Mapping[str, object],
    received_b: Mapping[str, object],
    taxes: Mapping[int, int] | None = None,
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
    tax_lines: list[str] = []
    if taxes:
        tax_a = int(taxes.get(int(user_a.id), 0))
        tax_b = int(taxes.get(int(user_b.id), 0))
        if tax_a:
            tax_lines.append(f"{user_a.display_name} : {format_currency(tax_a)}")
        if tax_b:
            tax_lines.append(f"{user_b.display_name} : {format_currency(tax_b)}")
    if tax_lines:
        embed.add_field(
            name="Taxe de transaction (1%)",
            value="\n".join(tax_lines),
            inline=False,
        )
        footer_text = "Merci d'avoir utilisé EcoBot pour vos échanges !"
        footer_text += " Une taxe de 1% a été retenue sur les montants reçus."
    else:
        footer_text = "Merci d'avoir utilisé EcoBot pour vos échanges !"
    embed.set_footer(text=footer_text)
    return _finalize_embed(embed)


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
        return _finalize_embed(embed)

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
    return _finalize_embed(embed)
