"""Fonctions utilitaires pour créer des embeds cohérents."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Callable, Collection, Iterable, Mapping, Optional, Sequence, cast

import discord
from discord.ext import commands

from config import (
    Colors,
    Emojis,
    PET_RARITY_COLORS,
    PREFIX,
    GradeDefinition,
    PetDefinition,
    PetEggDefinition,
    POTION_DEFINITION_MAP,
)

from utils.formatting import format_currency, format_gems
from utils.pet_formatting import PetDisplay, pet_emoji
from utils.mastery import MasteryDefinition
from utils.enchantments import ENCHANTMENT_DEFINITION_MAP, format_enchantment

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
    "format_gems",
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
    "pet_multi_reveal_embed",
    "pet_collection_embed",
    "pet_index_embed",
    "pet_equip_embed",
    "pet_claim_embed",
    "egg_index_embed",
    "clan_overview_embed",
    "mastery_overview_embed",
    "mastery_detail_embed",
]


def _format_number(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def _progress_bar(ratio: float, *, length: int = 12) -> str:
    clamped = max(0.0, min(1.0, float(ratio)))
    filled = int(round(clamped * length))
    if filled == 0 and clamped > 0.0:
        filled = 1
    filled = min(length, max(0, filled))
    empty = max(0, length - filled)
    return "▰" * filled + "▱" * empty


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


def balance_embed(
    member: discord.Member, *, balance: int, gems: int | None = None
) -> discord.Embed:
    lines = [f"{Emojis.MONEY} **Solde :** {format_currency(balance)}"]
    if gems is not None:
        lines.append(f"{Emojis.GEM} **Gemmes :** {format_gems(gems)}")
    description = "\n".join(lines)
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
        f"Devine la combinaison de **{code_length}** couleurs pour décrocher des PB bonus et des tickets de tombola.",
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


def raffle_overview_embed(
    *,
    member: discord.abc.User | discord.Member,
    inventory_tickets: int,
    committed_tickets: int,
    total_committed: int,
    next_draw: datetime | None,
    prize_label: str,
    ticket_emoji: str = "🎟️",
) -> discord.Embed:
    description_lines = [
        f"Chaque ticket misé te donne une chance de décrocher **{prize_label}**.",
        "Tous les tickets misés sont remis à zéro après chaque tirage.",
        "Utilise les boutons pour miser ou retirer des tickets depuis ton inventaire.",
    ]
    embed = _base_embed("🎟️ Tombola Mastermind", "\n".join(description_lines), color=Colors.INFO)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    player_lines = [
        f"Inventaire : **{max(0, inventory_tickets)}** {ticket_emoji}",
        f"Tickets misés : **{max(0, committed_tickets)}**",
    ]
    embed.add_field(name="Tes tickets", value="\n".join(player_lines), inline=False)

    pool_lines = [f"Total en lice : **{max(0, total_committed)}** {ticket_emoji}"]
    if isinstance(next_draw, datetime):
        target = next_draw
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        pool_lines.append(f"Prochain tirage : {discord.utils.format_dt(target, style='R')}")
        pool_lines.append(f"{discord.utils.format_dt(target, style='f')}")
    embed.add_field(name="Prochain tirage", value="\n".join(pool_lines), inline=False)

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
    normalized_symbol = symbol.upper()
    for rank, (user_id, value) in enumerate(entries, start=1):
        user = bot.get_user(user_id)
        name = user.display_name if user else f"Utilisateur {user_id}"
        if normalized_symbol == "PB":
            value_display = format_currency(value)
        elif normalized_symbol == "RAP":
            value_display = f"{format_gems(value)} (RAP)"
        elif normalized_symbol in {"GEM", "GEMS"}:
            value_display = format_gems(value)
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


def player_stats_embed(
    *,
    member: discord.Member,
    steal_success: float,
    steal_risk: float,
    steal_protected: bool,
    egg_luck_total: float,
    egg_luck_breakdown: Sequence[str],
    mastermind_wins: int,
    race_best_stage: int,
    race_best_label: str,
    race_total_stages: int,
) -> discord.Embed:
    steal_lines = [
        f"Chance de réussir un vol : **{steal_success * 100:.0f}%**",
    ]
    if steal_protected:
        steal_lines.append(
            f"Protection active : seulement **{steal_risk * 100:.0f}%** de chances pour les voleurs"
        )
    else:
        steal_lines.append("Aucune protection contre le vol.")

    egg_lines = [f"Bonus total : **+{egg_luck_total * 100:.0f}%**"]
    egg_lines.extend(f"• {line}" for line in egg_luck_breakdown)

    race_summary = (
        f"Étape {race_best_stage}/{race_total_stages} — {race_best_label}"
        if race_best_stage > 0
        else "Aucun record enregistré pour l'instant."
    )

    embed = _base_embed("📊 Statistiques du joueur", color=Colors.PRIMARY)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    embed.add_field(name="🔓 Vol", value="\n".join(steal_lines), inline=False)
    embed.add_field(name="🥚 Chance dans les œufs", value="\n".join(egg_lines), inline=False)
    embed.add_field(
        name="🧠 Mastermind",
        value=f"Parties gagnées : **{mastermind_wins}**",
        inline=False,
    )
    embed.add_field(
        name="🏁 Millionaire Race",
        value=f"Record : **{race_summary}**",
        inline=False,
    )
    return _finalize_embed(embed)


def _quest_progress_line(label: str, current: int, goal: int) -> str:
    if goal <= 0:
        return f"✅ {label} : aucune exigence"
    status = "✅" if current >= goal else "▫️"
    return f"{status} {label} : **{current}/{goal}**"


def _quest_currency_progress_line(
    label: str, current: int, goal: int, *, formatter: Callable[[int], str] = format_currency
) -> str:
    if goal <= 0:
        return f"✅ {label} : aucune exigence"
    status = "✅" if current >= goal else "▫️"
    return f"{status} {label} : **{formatter(current)} / {formatter(goal)}**"


def grade_profile_embed(
    *,
    member: discord.Member,
    grade_level: int,
    total_grades: int,
    current_grade: GradeDefinition | None,
    next_grade: GradeDefinition | None,
    progress: Mapping[str, int],
    rap_total: int,
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
            f"Prochaine récompense : **{format_gems(next_grade.reward_gems)}** + 1 slot"
        )
        quest_lines = [
            _quest_progress_line(
                "Gagner des parties de Mastermind",
                progress.get("mastermind", 0),
                next_grade.mastermind_goal,
            ),
            _quest_progress_line(
                "Ouvrir des œufs",
                progress.get("eggs", 0),
                next_grade.egg_goal,
            ),
            _quest_currency_progress_line(
                "Atteindre un RAP total",
                rap_total,
                next_grade.rap_goal,
                formatter=format_gems,
            ),
            _quest_currency_progress_line(
                "Perdre des Prissbucks au casino",
                progress.get("casino_losses", 0),
                next_grade.casino_loss_goal,
            ),
            _quest_progress_line(
                "Boire des potions",
                progress.get("potions", 0),
                next_grade.potion_goal,
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
    reward_gems: int,
    gems_after: int,
    pet_slots: int,
) -> discord.Embed:
    lines = [
        f"Nouveau grade : **{grade_name}** ({grade_level}/{total_grades})",
        f"Récompense : **{format_gems(reward_gems)}**",
        f"Slots de pets disponibles : **{pet_slots}**",
        f"Gemmes actuelles : {format_gems(gems_after)}",
    ]
    embed = _base_embed("🎖️ Grade amélioré !", "\n".join(lines), color=Colors.SUCCESS)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    return _finalize_embed(embed)


def pet_animation_embed(
    *, title: str, description: str, emoji: str | None = None
) -> discord.Embed:
    prefix = f"{emoji} " if emoji else ""
    embed = _base_embed(f"{prefix}{title}".strip(), description, color=Colors.INFO)
    return embed


def _pet_emoji(name: str) -> str:
    """Compatibilité pour les anciens appels internes."""

    return pet_emoji(name)


def pet_reveal_embed(
    *,
    name: str,
    rarity: str,
    image_url: str,
    income_per_hour: int,
    is_huge: bool,
    is_gold: bool,
    is_galaxy: bool = False,
    is_rainbow: bool = False,
    is_shiny: bool = False,
    market_value: int = 0,
) -> discord.Embed:
    pet = PetDisplay(
        name=name,
        rarity=rarity,
        income_per_hour=income_per_hour,
        is_huge=is_huge,
        is_gold=is_gold,
        is_galaxy=is_galaxy,
        is_rainbow=is_rainbow,
        is_shiny=is_shiny,
        market_value=market_value,
        image_url=image_url or None,
    )

    if pet.is_galaxy:
        base_color = Colors.ACCENT
    elif pet.is_gold or pet.is_rainbow:
        base_color = Colors.GOLD
    else:
        base_color = PET_RARITY_COLORS.get(pet.rarity, Colors.INFO)

    embed = _base_embed(pet.title(), "\n".join(pet.reveal_lines()), color=base_color)
    if pet.image_url:
        embed.set_image(url=pet.image_url)
    return _finalize_embed(embed)


def pet_multi_reveal_embed(
    *,
    egg_name: str,
    pets: Sequence[Mapping[str, object]],
) -> discord.Embed:
    displays = [PetDisplay.from_mapping(entry) for entry in pets]
    count = len(displays)
    if count <= 0:
        return pet_reveal_embed(
            name="Mystère",
            rarity="Commun",
            image_url="",
            income_per_hour=0,
            is_huge=False,
            is_gold=False,
        )

    description = f"Tu obtiens **{count}** pets !"
    embed = _base_embed(f"🎁 {egg_name}", description, color=Colors.PRIMARY)

    for display in displays:
        field_name, field_value = display.multi_reveal_field()
        embed.add_field(name=field_name, value=field_value, inline=False)

    first_image = displays[0].image_url
    if first_image:
        embed.set_thumbnail(url=first_image)
    if count >= 2:
        second_image = displays[1].image_url
        if second_image:
            embed.set_image(url=second_image)

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
    group_duplicates: bool = True,
) -> discord.Embed:
    del huge_descriptions  # Non utilisé dans la version minimaliste

    displays = [PetDisplay.from_mapping(pet) for pet in pets]
    active_count = sum(1 for pet in displays if pet.is_active)
    header = [
        f"Total : {total_count}",
        f"Actifs : {active_count}",
        f"Revenu actif : {format_currency(total_income_per_hour)}/h",
    ]

    description_lines = []
    if group_duplicates:
        grouped: OrderedDict[
            tuple[object, ...], dict[str, object]
        ] = OrderedDict()
        for display in displays:
            key = display.collection_key()
            entry = grouped.get(key)
            if entry is None:
                identifiers: list[int] = []
                if display.identifier:
                    identifiers.append(int(display.identifier))
                grouped[key] = {
                    "display": display,
                    "count": 1,
                    "identifiers": identifiers,
                }
                continue

            entry["count"] = int(entry.get("count", 0)) + 1
            if display.identifier:
                identifiers = entry.setdefault("identifiers", [])
                if isinstance(identifiers, list):
                    identifiers.append(int(display.identifier))

        for entry in grouped.values():
            display = cast(PetDisplay | None, entry.get("display"))
            if display is None:
                continue
            count = int(entry.get("count", 0))
            identifiers = entry.get("identifiers")
            if isinstance(identifiers, list):
                line = display.collection_line(quantity=count, identifiers=identifiers)
            else:
                line = display.collection_line(quantity=count)
            description_lines.append(line)
    else:
        for display in displays:
            identifier_value = (
                int(display.identifier)
                if display.identifier is not None
                else 0
            )
            identifiers = [identifier_value] if identifier_value else None
            line = display.collection_line(quantity=1, identifiers=identifiers)
            description_lines.append(line)

    embed_description = " • ".join(header)
    if description_lines:
        embed_description += "\n\n" + "\n".join(f"• {line}" for line in description_lines)
    else:
        embed_description += "\n\nAucun pet pour le moment. Ouvre un œuf avec e!openbox."

    embed = _base_embed("Inventaire des pets", embed_description, color=Colors.INFO)
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
    pet_counts: Mapping[str, int] | None = None,
    market_values: Mapping[str, int] | None = None,
) -> discord.Embed:
    embed = _base_embed("Index des pets", "", color=Colors.INFO)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    huge_info = dict(huge_descriptions or {})
    total_pets = len(pet_definitions)
    owned_lookup = {name.casefold() for name in owned_names if name}
    counts_lookup = {
        key.casefold(): int(value)
        for key, value in (pet_counts or {}).items()
        if key
    }
    market_lookup = {
        key.casefold(): int(value)
        for key, value in (market_values or {}).items()
        if key
    }
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
        count = counts_lookup.get(name.casefold())
        market_value = market_lookup.get(name.casefold(), 0)
        details = [f"Rareté : {rarity}"]
        if count is not None:
            plural = "s" if count != 1 else ""
            details.append(f"{count} existant{plural}")
        if market_value > 0 and not is_huge:
            details.append(f"Valeur marché : {format_gems(market_value)}")
        detail_text = " • ".join(details)
        emoji_part = f"{emoji} " if emoji else ""
        line = f"{status} {emoji_part}**{name}** — {detail_text}"
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


def egg_index_embed(*, eggs: Sequence[PetEggDefinition]) -> discord.Embed:
    embed = _base_embed(
        "Index des œufs",
        "Probabilités de drop connues pour chaque œuf.",
        color=Colors.INFO,
    )

    for egg in eggs:
        if not egg.pets:
            continue
        lines: list[str] = []
        for pet in egg.pets:
            name = pet.name
            if not name:
                continue
            emoji = _pet_emoji(name)
            rarity = pet.rarity
            if pet.is_huge:
                chance_text = "???"
            else:
                rate = max(0.0, float(getattr(pet, "drop_rate", 0.0)))
                if rate <= 0:
                    chance_text = "???"
                else:
                    chance_value = rate * 100
                    formatted = f"{chance_value:.2f}".rstrip("0").rstrip(".")
                    chance_text = f"{formatted}%"
            emoji_prefix = f"{emoji} " if emoji else ""
            marker = " ✨" if pet.is_huge else ""
            lines.append(
                f"{emoji_prefix}**{name}** ({rarity}) — {chance_text}{marker}"
            )

        if not lines:
            continue

        chunks = list(_chunk_field_values(lines, limit=900))
        for index, value in enumerate(chunks, start=1):
            if egg.currency == "gem":
                price_display = format_gems(egg.price)
            else:
                price_display = format_currency(egg.price)
            field_name = f"{egg.name} — {price_display}"
            if len(chunks) > 1:
                field_name = f"{field_name} ({index})"
            embed.add_field(name=field_name, value=value, inline=False)

    embed.set_footer(text="Les chances indiquées sont susceptibles de changer avec les mises à jour.")
    return _finalize_embed(embed)


def pet_equip_embed(
    *,
    member: discord.Member,
    pet: Mapping[str, object],
    activated: bool,
    active_count: int,
    slot_limit: int,
) -> discord.Embed:
    display = PetDisplay.from_mapping(pet)
    status_symbol = "✅" if activated else "🛌"
    title = f"{status_symbol} {display.title()}"
    color = Colors.SUCCESS if activated else Colors.INFO
    lines = display.equipment_lines(activated, active_count, slot_limit)

    embed = _base_embed(title, "\n".join(lines), color=color)
    if display.image_url:
        embed.set_thumbnail(url=display.image_url)
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
    potion: Mapping[str, object] | None = None,
    enchantment: Mapping[str, object] | None = None,
    farm_rewards: Mapping[str, object] | None = None,
) -> discord.Embed:
    displays = [PetDisplay.from_mapping(pet) for pet in pets]
    total_income = sum(display.income_per_hour for display in displays)
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
    if potion:
        potion_multiplier = float(potion.get("multiplier", 1.0))
        if potion_multiplier > 1.0:
            potion_name = str(potion.get("name", "Potion"))
            potion_line = f"{potion_name} x{potion_multiplier:.2f}"
            potion_bonus = int(potion.get("bonus", 0))
            if potion_bonus > 0:
                potion_line += f" (+{format_currency(potion_bonus)})"
            remaining = float(potion.get("remaining_seconds", 0.0))
            if remaining > 0:
                potion_line += f" ({_format_duration(remaining)} restants)"
            extra_info.append(potion_line)
    if enchantment:
        slug = str(enchantment.get("slug") or "")
        power = int(enchantment.get("power") or 0)
        definition = ENCHANTMENT_DEFINITION_MAP.get(slug)
        if definition and power > 0:
            label = format_enchantment(definition, power)
        else:
            label = f"Enchantement puissance {power}" if power else "Enchantement actif"
        multiplier = float(enchantment.get("multiplier", 1.0))
        bonus = int(enchantment.get("bonus", 0))
        line = f"{label} x{multiplier:.2f}"
        if bonus > 0:
            line += f" (+{format_currency(bonus)})"
        extra_info.append(line)
    if clan:
        clan_name = str(clan.get("name", "Clan"))
        clan_multiplier = float(clan.get("multiplier", 1.0))
        clan_bonus = int(clan.get("bonus", 0))
        shiny_mult = float(clan.get("shiny_multiplier", 1.0))
        clan_line = f"Clan {clan_name}"
        if clan_multiplier > 1.0:
            clan_line += f" x{clan_multiplier:.2f}"
        if clan_bonus > 0:
            clan_line += f" (+{format_currency(clan_bonus)})"
        if shiny_mult > 1.0:
            clan_line += f" • Shiny x{shiny_mult:.2f}"
        extra_info.append(clan_line)

    color = Colors.SUCCESS if amount else Colors.INFO
    description_text = description
    if extra_info:
        description_text += "\n" + " • ".join(extra_info)
    embed = _base_embed("Gains des pets", description_text, color=color)
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    reward_lines: list[str] = []
    if farm_rewards:
        gems_reward = int(farm_rewards.get("gems", 0) or 0)
        if gems_reward > 0:
            reward_lines.append(f"💎 {format_gems(gems_reward)}")

        tickets = int(farm_rewards.get("tickets", 0) or 0)
        if tickets:
            reward_lines.append(f"🎟️ Tickets ×{tickets}")

        potion_map = farm_rewards.get("potions")
        if isinstance(potion_map, Mapping):
            for slug, quantity in potion_map.items():
                qty = int(quantity or 0)
                if qty <= 0:
                    continue
                definition = POTION_DEFINITION_MAP.get(str(slug))
                potion_name = definition.name if definition else str(slug)
                reward_lines.append(f"🧪 {potion_name} ×{qty}")

        enchantments = farm_rewards.get("enchantments")
        if isinstance(enchantments, Collection):
            for enchantment_entry in enchantments:
                if not isinstance(enchantment_entry, Mapping):
                    continue
                slug = str(enchantment_entry.get("slug") or "")
                power = int(enchantment_entry.get("power") or 0)
                definition = ENCHANTMENT_DEFINITION_MAP.get(slug)
                label = format_enchantment(definition, power) if definition else slug
                reward_lines.append(f"✨ {label}")

    if reward_lines:
        embed.add_field(
            name="🎁 Butin des pets",
            value="\n".join(reward_lines),
            inline=False,
        )

    summary_lines: list[str] = []
    if displays:
        summary_lines.append(
            f"Revenus horaires totaux : **{format_currency(total_income)}**/h"
        )
        active_count = sum(1 for display in displays if display.is_active)
        if active_count:
            summary_lines.append(f"Pets actifs : **{active_count}**")
    if summary_lines:
        embed.description += "\n\n" + "\n".join(f"• {line}" for line in summary_lines)

    return _finalize_embed(embed)


def mastery_overview_embed(
    *,
    member: discord.abc.User,
    masteries: Sequence[MasteryDefinition],
    progress: Mapping[str, Mapping[str, int]],
) -> discord.Embed:
    embed = _base_embed(
        "Progression des maîtrises",
        "Voici un aperçu de tes progrès sur les différentes maîtrises.",
        color=Colors.INFO,
    )
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    total_levels = 0
    for definition in masteries:
        data = progress.get(definition.slug, {})
        level = int(data.get("level", 1))
        max_level = int(data.get("max_level", definition.max_level))
        experience = int(data.get("experience", 0))
        xp_to_next = int(data.get("xp_to_next_level", definition.required_xp(level)))
        total_levels += level

        if xp_to_next > 0:
            ratio = experience / xp_to_next if xp_to_next else 0.0
        else:
            ratio = 1.0
        ratio = max(0.0, min(1.0, ratio))
        bar = _progress_bar(ratio)

        lines = [f"Niveau **{level}/{max_level}**"]
        lines.append(bar)
        if xp_to_next > 0:
            remaining = max(0, xp_to_next - experience)
            lines.append(
                f"{_format_number(experience)} XP / {_format_number(xp_to_next)}"
                f" ({ratio:.0%})"
            )
            lines.append(f"Reste {_format_number(remaining)} XP pour le prochain niveau")
        else:
            lines.append("Niveau maximal atteint ✅")

        embed.add_field(name=definition.display_name, value="\n".join(lines), inline=False)

    if total_levels:
        embed.description += f"\n\nNiveau cumulé : **{_format_number(total_levels)}**"

    return _finalize_embed(embed)


def mastery_detail_embed(
    *,
    member: discord.abc.User,
    mastery: MasteryDefinition,
    progress: Mapping[str, object],
    tiers: Sequence[Mapping[str, object]],
) -> discord.Embed:
    level = int(progress.get("level", 1) or 1)
    max_level = int(progress.get("max_level", mastery.max_level) or mastery.max_level)
    experience = int(progress.get("experience", 0) or 0)
    xp_to_next = int(
        progress.get("xp_to_next_level", mastery.required_xp(level))
        or mastery.required_xp(level)
    )
    embed = _base_embed(
        f"{mastery.display_name} — Bonus",
        "Survole chaque palier pour préparer ta progression.",
        color=Colors.INFO,
    )
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    lines = [f"Niveau actuel : **{level}/{max_level}**"]
    if xp_to_next > 0:
        ratio = experience / xp_to_next if xp_to_next else 0.0
        remaining = max(0, xp_to_next - experience)
        lines.append(
            f"Progression : {_format_number(experience)} / {_format_number(xp_to_next)} XP ({ratio:.0%})"
        )
        lines.append(f"Il reste {_format_number(remaining)} XP pour le prochain niveau.")
    else:
        lines.append("Niveau maximal atteint ✅")
    embed.description = "\n".join(lines)

    tier_lines: list[str] = []
    for tier in tiers:
        tier_level = int(tier.get("level", 0) or 0)
        title = str(tier.get("title", f"Niveau {tier_level}"))
        description = str(tier.get("description", ""))
        status = "✅" if level >= tier_level else "🔒"
        tier_lines.append(f"{status} **Niveau {tier_level} — {title}**")
        if description:
            tier_lines.append(description)
        tier_lines.append("")

    tier_value = "Aucun palier défini pour cette maîtrise."
    if tier_lines:
        tier_value = "\n".join(tier_lines).strip()

    embed.add_field(name="Bonus par palier", value=tier_value, inline=False)
    embed.set_footer(text="Clique sur une autre maîtrise pour comparer ses paliers.")
    return _finalize_embed(embed)


def clan_overview_embed(
    *,
    clan_name: str,
    banner: str,
    leader_name: str,
    member_count: int,
    capacity: int,
    boost_multiplier: float,
    shiny_multiplier: float,
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
        f"✨ Chance shiny : **x{shiny_multiplier:.2f}**",
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

