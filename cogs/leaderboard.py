"""Commandes de classement économique."""
from __future__ import annotations

from discord.ext import commands

from config import LEADERBOARD_LIMIT
from database.db import DatabaseError
from utils.mastery import EGG_MASTERY, MASTERMIND_MASTERY, PET_MASTERY, MasteryDefinition
from utils import embeds


class Leaderboard(commands.Cog):
    """Expose le classement économique minimal."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database

    @commands.command(name="leaderboard", aliases=("lb",))
    async def leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.database.get_balance_leaderboard(LEADERBOARD_LIMIT)
        embed = embeds.leaderboard_embed(
            title="Classement des plus riches",
            entries=[(row["user_id"], row["balance"]) for row in rows],
            bot=self.bot,
            symbol="PB",
        )
        await ctx.send(embed=embed)

    @commands.command(name="rapleaderboard", aliases=("raplb", "rap"))
    async def rap_leaderboard(self, ctx: commands.Context) -> None:
        try:
            rows = await self.database.get_pet_rap_leaderboard(LEADERBOARD_LIMIT)
        except Exception:
            await ctx.send(embed=embeds.error_embed("Impossible de récupérer le classement RAP."))
            return

        embed = embeds.leaderboard_embed(
            title="Classement RAP des collectionneurs",
            entries=[(user_id, rap) for user_id, rap in rows],
            bot=self.bot,
            symbol="RAP",
        )
        await ctx.send(embed=embed)

    @commands.command(name="revenusleaderboard", aliases=("revenuslb", "incomelb", "hourlylb"))
    async def income_leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.database.get_hourly_income_leaderboard(LEADERBOARD_LIMIT)
        embed = embeds.leaderboard_embed(
            title="Classement des revenus horaires",
            entries=[(user_id, income) for user_id, income in rows],
            bot=self.bot,
            symbol="PB/h",
        )
        await ctx.send(embed=embed)

    async def _send_mastery_leaderboard(
        self, ctx: commands.Context, mastery: MasteryDefinition
    ) -> None:
        try:
            rows = await self.database.get_mastery_leaderboard(mastery.slug, LEADERBOARD_LIMIT)
        except DatabaseError as exc:  # type: ignore[name-defined]
            await ctx.send(embed=embeds.error_embed(str(exc)))
            return
        except Exception:
            await ctx.send(
                embed=embeds.error_embed("Impossible de récupérer le classement demandé.")
            )
            return

        title = f"Classement {mastery.display_name}"
        if not rows:
            await ctx.send(
                embed=embeds.info_embed(
                    "Aucune donnée disponible pour cette maîtrise pour le moment.",
                    title=title,
                )
            )
            return

        lines = []
        for rank, row in enumerate(rows, start=1):
            user_id = int(row["user_id"])
            level = int(row.get("level") or 0)
            xp = int(row.get("experience") or 0)
            required = mastery.required_xp(level)
            if required <= 0:
                progress = f"{xp} XP (niveau max)"
            else:
                progress = f"{xp}/{required} XP"

            user = self.bot.get_user(user_id)
            name = user.display_name if user else f"Utilisateur {user_id}"
            lines.append(f"**{rank}.** {name} — Niveau {level} ({progress})")

        embed = embeds.info_embed("\n".join(lines), title=title)
        await ctx.send(embed=embed)

    @commands.command(name="eggmasteryleb", aliases=("eggmasterylb",))
    async def egg_mastery_leaderboard(self, ctx: commands.Context) -> None:
        await self._send_mastery_leaderboard(ctx, EGG_MASTERY)

    @commands.command(name="petmasteryleb", aliases=("petmasterylb",))
    async def pet_mastery_leaderboard(self, ctx: commands.Context) -> None:
        await self._send_mastery_leaderboard(ctx, PET_MASTERY)

    @commands.command(
        name="mastermindmasterylb",
        aliases=("mastermindmasteryleb", "mmmasterylb"),
    )
    async def mastermind_mastery_leaderboard(self, ctx: commands.Context) -> None:
        await self._send_mastery_leaderboard(ctx, MASTERMIND_MASTERY)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leaderboard(bot))
