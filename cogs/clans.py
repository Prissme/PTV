"""Cog dédié à la gestion des clans et de leurs améliorations."""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

import discord
from discord.ext import commands

from config import (
    CLAN_CREATION_COST,
    CLAN_JOIN_COST,
    CLAN_MAX_MEMBERS,
    CLAN_WAR_MIN_MEMBERS,
)
from database.db import Database, DatabaseError
from utils import embeds

CLAN_NAME_MIN_LENGTH = 3
CLAN_NAME_MAX_LENGTH = 24


def _rank_bonus_percent(rank: int) -> float:
    if rank == 1:
        return 0.10
    if rank == 2:
        return 0.05
    if rank == 4:
        return -0.05
    if rank >= 5:
        return -0.10
    return 0.0


class Clans(commands.Cog):
    """Regroupe toutes les commandes liées aux clans."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database: Database = bot.database

    @commands.group(name="clan", invoke_without_command=True)
    async def clan(self, ctx: commands.Context) -> None:
        membership = await self.database.get_user_clan(ctx.author.id)
        if membership is None:
            description = (
                f"Crée ton clan avec **e!clan create <nom>** ({embeds.format_currency(CLAN_CREATION_COST)}) "
                f"ou rejoins-en un avec **e!clan join <nom>** ({embeds.format_currency(CLAN_JOIN_COST)}).\n"
                f"Un clan peut contenir **1 à {CLAN_MAX_MEMBERS} membres** et la guerre des clans demande **{CLAN_WAR_MIN_MEMBERS} membres minimum**."
            )
            await ctx.send(embed=embeds.info_embed(description, title="Système de clans"))
            return

        clan_id = int(membership["clan_id"])
        clan_record, members = await self.database.get_clan_profile(clan_id)
        clan_level = int(clan_record.get("clan_level") or 1)
        next_cost = self.database.get_clan_next_level_cost(clan_level)
        clan_bonus_percent = (clan_level - 1) * 0.05

        members_payload: List[Dict[str, object]] = []
        guild = ctx.guild
        for idx, entry in enumerate(members, start=1):
            user_id = int(entry["user_id"])
            contribution = int(entry.get("contribution", 0))
            role = str(entry.get("role", "member"))
            member_obj = guild.get_member(user_id) if guild is not None else None
            mention = member_obj.mention if member_obj else f"<@{user_id}>"
            bonus = _rank_bonus_percent(idx)
            members_payload.append(
                {
                    "mention": mention,
                    "role": role,
                    "contribution": contribution,
                    "rank_bonus": bonus,
                }
            )

        lines = [
            f"Niveau: **{clan_level}**",
            f"Investissement total: **{embeds.format_currency(int(clan_record.get('total_investment') or 0))}**",
            f"Prochain niveau: **{embeds.format_currency(next_cost)}**",
            f"Bonus de revenus clan: **+{clan_bonus_percent:.2f}%**",
            f"Membres: **{len(members)}/{CLAN_MAX_MEMBERS}**",
        ]
        embed = embeds.info_embed("\n".join(lines), title=f"⚔️ {clan_record.get('name', 'Clan')}")

        ranking = []
        for index, member in enumerate(members_payload, start=1):
            rank_bonus = float(member["rank_bonus"])
            sign = "+" if rank_bonus >= 0 else ""
            ranking.append(
                f"#{index} {member['mention']} — {embeds.format_currency(int(member['contribution']))} ({sign}{rank_bonus:.2f}%)"
            )
        embed.add_field(name="Classement interne", value="\n".join(ranking) or "Aucun membre", inline=False)
        await ctx.send(embed=embed)

    @clan.command(name="create")
    async def clan_create(self, ctx: commands.Context, *, name: str) -> None:
        name = name.strip()
        if not (CLAN_NAME_MIN_LENGTH <= len(name) <= CLAN_NAME_MAX_LENGTH):
            await ctx.send(embed=embeds.error_embed(f"Le nom doit contenir entre {CLAN_NAME_MIN_LENGTH} et {CLAN_NAME_MAX_LENGTH} caractères."))
            return
        if await self.database.get_user_clan(ctx.author.id):
            await ctx.send(embed=embeds.error_embed("Tu fais déjà partie d'un clan."))
            return
        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < CLAN_CREATION_COST:
            await ctx.send(embed=embeds.error_embed(f"Il te manque {embeds.format_currency(CLAN_CREATION_COST - balance)}."))
            return
        try:
            clan = await self.database.create_clan(ctx.author.id, name)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        await self.database.increment_balance(ctx.author.id, -CLAN_CREATION_COST, transaction_type="clan_create", description=f"Création du clan {name}")
        await self.database.record_clan_contribution(int(clan["clan_id"]), ctx.author.id, CLAN_CREATION_COST)
        await self.database.pool.execute("UPDATE clans SET total_investment = total_investment + $2 WHERE clan_id = $1", int(clan["clan_id"]), CLAN_CREATION_COST)
        await ctx.send(embed=embeds.success_embed(f"Clan **{name}** créé. Tu es le chef du clan."))

    @clan.command(name="join")
    async def clan_join(self, ctx: commands.Context, *, name: str) -> None:
        if await self.database.get_user_clan(ctx.author.id):
            await ctx.send(embed=embeds.error_embed("Tu fais déjà partie d'un clan."))
            return
        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < CLAN_JOIN_COST:
            await ctx.send(embed=embeds.error_embed(f"Il te manque {embeds.format_currency(CLAN_JOIN_COST - balance)} pour rejoindre ce clan."))
            return
        clan = await self.database.get_clan_by_name(name)
        if clan is None:
            await ctx.send(embed=embeds.error_embed("Ce clan est introuvable."))
            return
        try:
            await self.database.add_member_to_clan(int(clan["clan_id"]), ctx.author.id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        await self.database.increment_balance(ctx.author.id, -CLAN_JOIN_COST, transaction_type="clan_join", description=f"Entrée dans le clan {clan['name']}")
        await self.database.record_clan_contribution(int(clan["clan_id"]), ctx.author.id, CLAN_JOIN_COST)
        await self.database.pool.execute("UPDATE clans SET total_investment = total_investment + $2 WHERE clan_id = $1", int(clan["clan_id"]), CLAN_JOIN_COST)
        await ctx.send(embed=embeds.success_embed(f"Tu rejoins **{clan['name']}**."))

    @clan.command(name="buy")
    async def clan_buy(self, ctx: commands.Context) -> None:
        membership = await self.database.get_user_clan(ctx.author.id)
        if membership is None:
            await ctx.send(embed=embeds.error_embed("Tu dois appartenir à un clan."))
            return
        clan_id = int(membership["clan_id"])
        clan = await self.database.get_clan(clan_id)
        if clan is None:
            await ctx.send(embed=embeds.error_embed("Clan introuvable."))
            return
        current_level = int(clan.get("clan_level") or 1)
        cost = self.database.get_clan_next_level_cost(current_level)
        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < cost:
            await ctx.send(embed=embeds.error_embed(f"Il te manque {embeds.format_currency(cost - balance)} pour financer le niveau suivant."))
            return
        await self.database.increment_balance(ctx.author.id, -cost, transaction_type="clan_buy", description="Investissement clan")
        await self.database.record_clan_contribution(clan_id, ctx.author.id, cost)
        updated = await self.database.upgrade_clan_boost(clan_id)
        new_level = int(updated.get("clan_level") or 1)
        await ctx.send(embed=embeds.success_embed(f"Investissement validé: clan niveau **{new_level}**."))

    @clan.command(name="quetes")
    async def clan_quetes(self, ctx: commands.Context) -> None:
        membership = await self.database.get_user_clan(ctx.author.id)
        if membership is None:
            await ctx.send(embed=embeds.error_embed("Tu dois appartenir à un clan."))
            return
        clan_id = int(membership["clan_id"])
        members = await self.database.get_clan_members(clan_id)
        can_war = len(members) >= CLAN_WAR_MIN_MEMBERS
        now = datetime.utcnow()
        weekend_active = now.weekday() in {5, 6}
        lines = [
            "Ouvrir 5 œufs → 1 point",
            "Faire 2 Master Mind → 1 point",
            "Obtenir un Huge → 20 points",
            "Obtenir un Titanic → 1000 points",
            "Chaque validation augmente difficulté/récompense de 20%.",
        ]
        status = "active" if weekend_active else "inactive"
        elig = "oui" if can_war else "non"
        embed = embeds.info_embed("\n".join(lines), title="⚔️ Quêtes de Guerre des Clans")
        embed.add_field(name="Statut week-end", value=status)
        embed.add_field(name="Clan éligible", value=elig)
        await ctx.send(embed=embed)

    @clan.command(name="kick")
    async def clan_kick(self, ctx: commands.Context, member: discord.Member) -> None:
        membership = await self.database.get_user_clan(ctx.author.id)
        if membership is None:
            await ctx.send(embed=embeds.error_embed("Tu n'es dans aucun clan."))
            return
        if str(membership.get("role")) != "leader":
            await ctx.send(embed=embeds.error_embed("Seul le chef peut exclure un membre."))
            return
        if member.id == ctx.author.id:
            await ctx.send(embed=embeds.error_embed("Utilise e!clan leave pour quitter."))
            return
        ok = await self.database.remove_member_from_clan(int(membership["clan_id"]), member.id)
        if not ok:
            await ctx.send(embed=embeds.error_embed("Impossible d'exclure ce membre."))
            return
        await ctx.send(embed=embeds.success_embed(f"{member.mention} a été exclu du clan."))

    @clan.command(name="leave")
    async def clan_leave(self, ctx: commands.Context) -> None:
        if not await self.database.leave_clan(ctx.author.id):
            await ctx.send(embed=embeds.error_embed("Impossible de quitter le clan."))
            return
        await ctx.send(embed=embeds.warning_embed("Tu as quitté ton clan."))

    @commands.command(name="clanlb")
    async def clanlb(self, ctx: commands.Context) -> None:
        rows = await self.database.get_clan_global_leaderboard(limit=10)
        if not rows:
            await ctx.send(embed=embeds.info_embed("Aucun clan classé pour le moment.", title="Classement des clans"))
            return
        lines = []
        for idx, row in enumerate(rows, start=1):
            lines.append(f"#{idx} **{row['name']}** — {embeds.format_currency(int(row.get('total_investment') or 0))} (Niv. {int(row.get('clan_level') or 1)})")
        await ctx.send(embed=embeds.info_embed("\n".join(lines), title="🌍 Classement global des clans"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Clans(bot))
