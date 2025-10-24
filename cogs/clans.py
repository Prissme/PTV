"""Cog dédié à la gestion des clans et de leurs améliorations."""
from __future__ import annotations

from typing import Dict, List

from discord.ext import commands

from config import (
    CLAN_BASE_CAPACITY,
    CLAN_BOOST_COSTS,
    CLAN_CAPACITY_PER_LEVEL,
    CLAN_CAPACITY_UPGRADE_COSTS,
    CLAN_CREATION_COST,
)
from database.db import Database, DatabaseError
from utils import embeds

CLAN_NAME_MIN_LENGTH = 3
CLAN_NAME_MAX_LENGTH = 24


def _compute_capacity(level: int) -> int:
    return CLAN_BASE_CAPACITY + max(0, level) * CLAN_CAPACITY_PER_LEVEL


class Clans(commands.Cog):
    """Regroupe toutes les commandes liées aux clans."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database: Database = bot.database

    @commands.group(name="clan", invoke_without_command=True)
    async def clan(self, ctx: commands.Context) -> None:
        """Affiche les informations du clan ou les actions disponibles."""

        membership = await self.database.get_user_clan(ctx.author.id)
        if membership is None:
            description = (
                "🔥 **Guerre totale imminente !**\n"
                "Fonde ton bastion avec **e!clan create <nom>** (25 000 PB) ou rejoins une faction avec **e!clan join <nom>**.\n"
                "Chaque clan démarre à 3 places, améliore les slots et les boosts PB pour déclencher une tempête de gains."
            )
            embed = embeds.info_embed(description, title="Rejoindre la guerre des clans")
            await ctx.send(embed=embed)
            return

        clan_id = int(membership["clan_id"])
        clan_record, members = await self.database.get_clan_profile(clan_id)

        capacity_level = int(clan_record.get("capacity_level") or 0)
        boost_level = int(clan_record.get("boost_level") or 0)
        capacity = _compute_capacity(capacity_level)
        member_count = len(members)
        banner = str(clan_record.get("banner_emoji") or "⚔️")
        boost_multiplier = float(clan_record.get("pb_boost_multiplier") or 1.0)

        next_capacity_cost = (
            CLAN_CAPACITY_UPGRADE_COSTS[capacity_level]
            if capacity_level < len(CLAN_CAPACITY_UPGRADE_COSTS)
            else None
        )
        next_boost_cost = (
            CLAN_BOOST_COSTS[boost_level]
            if boost_level < len(CLAN_BOOST_COSTS)
            else None
        )

        members_payload: List[Dict[str, object]] = []
        guild = ctx.guild
        for entry in members:
            user_id = int(entry["user_id"])
            contribution = int(entry.get("contribution", 0))
            role = str(entry.get("role", "member"))
            member_obj = guild.get_member(user_id) if guild is not None else None
            mention = member_obj.mention if member_obj else f"<@{user_id}>"
            display = member_obj.display_name if member_obj else mention
            members_payload.append(
                {
                    "mention": mention,
                    "display": display,
                    "role": role,
                    "contribution": contribution,
                }
            )

        owner_id = int(clan_record["owner_id"])
        leader_member = guild.get_member(owner_id) if guild is not None else None
        leader_name = leader_member.display_name if leader_member else f"<@{owner_id}>"

        embed = embeds.clan_overview_embed(
            clan_name=str(clan_record.get("name", "Clan")),
            banner=banner,
            leader_name=leader_name,
            member_count=member_count,
            capacity=capacity,
            boost_multiplier=boost_multiplier,
            boost_level=boost_level,
            capacity_level=capacity_level,
            members=members_payload,
            next_capacity_cost=next_capacity_cost,
            next_boost_cost=next_boost_cost,
        )
        await ctx.send(embed=embed)

    @clan.command(name="create")
    async def clan_create(self, ctx: commands.Context, *, name: str) -> None:
        """Crée un clan flambant neuf."""

        name = name.strip()
        if not (CLAN_NAME_MIN_LENGTH <= len(name) <= CLAN_NAME_MAX_LENGTH):
            await ctx.send(
                embed=embeds.error_embed(
                    f"Le nom doit contenir entre {CLAN_NAME_MIN_LENGTH} et {CLAN_NAME_MAX_LENGTH} caractères."
                )
            )
            return

        membership = await self.database.get_user_clan(ctx.author.id)
        if membership is not None:
            await ctx.send(embed=embeds.error_embed("Tu es déjà enrôlé dans un clan."))
            return

        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < CLAN_CREATION_COST:
            missing = CLAN_CREATION_COST - balance
            await ctx.send(
                embed=embeds.error_embed(
                    f"Il te manque {embeds.format_currency(missing)} pour lancer ton clan. Grind encore un peu !"
                )
            )
            return

        try:
            clan = await self.database.create_clan(ctx.author.id, name)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        await self.database.increment_balance(
            ctx.author.id,
            -CLAN_CREATION_COST,
            transaction_type="clan_create",
            description=f"Création du clan {name}",
        )

        await self.database.record_clan_contribution(int(clan["clan_id"]), ctx.author.id, CLAN_CREATION_COST)

        await ctx.send(
            embed=embeds.success_embed(
                f"{name} ouvre ses portes ! Invite tes alliés et prépare les boosts avec **e!clan slots** et **e!clan boost**."
            )
        )

    @clan.command(name="join")
    async def clan_join(self, ctx: commands.Context, *, name: str) -> None:
        """Rejoint un clan existant."""

        if await self.database.get_user_clan(ctx.author.id):
            await ctx.send(embed=embeds.error_embed("Tu fais déjà vibrer un clan."))
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

        await ctx.send(
            embed=embeds.success_embed(
                f"Tu rejoins **{clan['name']}**. Enflamme le chat et déclenche les boosts collectifs !"
            )
        )

    @clan.command(name="leave")
    async def clan_leave(self, ctx: commands.Context) -> None:
        """Quitte ton clan actuel (ou le dissout si tu es chef)."""

        membership = await self.database.get_user_clan(ctx.author.id)
        if membership is None:
            await ctx.send(embed=embeds.error_embed("Tu n'as encore prêté allégeance à aucun clan."))
            return

        clan_id = int(membership["clan_id"])

        success = await self.database.leave_clan(ctx.author.id)
        if not success:
            await ctx.send(embed=embeds.error_embed("Impossible de quitter le clan pour le moment."))
            return

        remaining = await self.database.get_clan(clan_id)
        if remaining is None:
            await ctx.send(
                embed=embeds.info_embed(
                    "Tu as quitté ton clan. Sans chef, il se dissout : il faudra tout reconstruire ailleurs."
                )
            )
        else:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Tu as quitté ton clan. Les boosts PB ne s'appliquent plus à toi… trouve vite une nouvelle armée !"
                )
            )

    @clan.command(name="slots")
    async def clan_slots(self, ctx: commands.Context) -> None:
        """Augmente le nombre de places disponibles dans ton clan."""

        membership = await self.database.get_user_clan(ctx.author.id)
        if membership is None:
            await ctx.send(embed=embeds.error_embed("Rejoins un clan avant de tenter un upgrade."))
            return

        if str(membership.get("role")) != "leader":
            await ctx.send(embed=embeds.error_embed("Seul le chef de clan peut acheter de nouveaux slots."))
            return

        clan_id = int(membership["clan_id"])
        clan = await self.database.get_clan(clan_id)
        if clan is None:
            await ctx.send(embed=embeds.error_embed("Clan introuvable. Relance la commande."))
            return

        capacity_level = int(clan.get("capacity_level") or 0)
        if capacity_level >= len(CLAN_CAPACITY_UPGRADE_COSTS):
            await ctx.send(embed=embeds.error_embed("Les quartiers sont déjà agrandis au maximum."))
            return

        cost = CLAN_CAPACITY_UPGRADE_COSTS[capacity_level]
        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < cost:
            await ctx.send(
                embed=embeds.error_embed(
                    f"Il manque {embeds.format_currency(cost - balance)} pour signer ces travaux titanesques."
                )
            )
            return

        await self.database.increment_balance(
            ctx.author.id,
            -cost,
            transaction_type="clan_slots",
            description="Extension des quartiers du clan",
        )

        try:
            updated = await self.database.upgrade_clan_capacity(clan_id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        await self.database.record_clan_contribution(clan_id, ctx.author.id, cost)

        new_capacity = _compute_capacity(int(updated.get("capacity_level") or 0))
        await ctx.send(
            embed=embeds.success_embed(
                f"Quartiers agrandis ! {new_capacity} places disponibles, recrute et spammez le chat pour dominer."
            )
        )

    @clan.command(name="boost")
    async def clan_boost(self, ctx: commands.Context) -> None:
        """Achète un boost PB permanent pour ton clan."""

        membership = await self.database.get_user_clan(ctx.author.id)
        if membership is None:
            await ctx.send(embed=embeds.error_embed("Tu dois appartenir à un clan pour acheter un boost."))
            return

        if str(membership.get("role")) != "leader":
            await ctx.send(embed=embeds.error_embed("Seul le chef peut activer les boosts permanents."))
            return

        clan_id = int(membership["clan_id"])
        clan = await self.database.get_clan(clan_id)
        if clan is None:
            await ctx.send(embed=embeds.error_embed("Clan introuvable. Relance la commande."))
            return

        boost_level = int(clan.get("boost_level") or 0)
        if boost_level >= len(CLAN_BOOST_COSTS):
            await ctx.send(embed=embeds.error_embed("Le turbo PB est déjà au maximum.🔥"))
            return

        cost = CLAN_BOOST_COSTS[boost_level]
        balance = await self.database.fetch_balance(ctx.author.id)
        if balance < cost:
            await ctx.send(
                embed=embeds.error_embed(
                    f"Rassemble encore {embeds.format_currency(cost - balance)} pour allumer la forge du boost."
                )
            )
            return

        await self.database.increment_balance(
            ctx.author.id,
            -cost,
            transaction_type="clan_boost",
            description="Activation d'un boost PB permanent",
        )

        try:
            updated = await self.database.upgrade_clan_boost(clan_id)
        except DatabaseError as exc:
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return

        await self.database.record_clan_contribution(clan_id, ctx.author.id, cost)

        new_level = int(updated.get("boost_level") or 0)
        new_multiplier = float(updated.get("pb_boost_multiplier") or 1.0)
        await ctx.send(
            embed=embeds.success_embed(
                f"Boost permanent niveau {new_level} débloqué ! Tous les membres gagnent désormais x{new_multiplier:.2f} PB."
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Clans(bot))
