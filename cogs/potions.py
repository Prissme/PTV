from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import discord
from discord.ext import commands

from config import POTION_DEFINITION_MAP, POTION_DEFINITIONS, PotionDefinition
from utils import embeds


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    minutes, sec = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if sec or not parts:
        parts.append(f"{sec}s")
    return " ".join(parts)


class Potions(commands.Cog):
    """Gestion des potions et de leurs effets."""

    DURATION_SECONDS = 3600

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._alias_map: Dict[str, str] = {}
        self._build_aliases()

    def _build_aliases(self) -> None:
        for definition in POTION_DEFINITIONS:
            slug = definition.slug
            self._alias_map[slug] = slug
            self._alias_map[slug.replace("_", "")] = slug
            name_key = definition.name.lower()
            self._alias_map[name_key] = slug
            self._alias_map[name_key.replace(" ", "_")] = slug

    def _resolve_potion(self, raw: str | None) -> Optional[PotionDefinition]:
        if not raw:
            return None
        key = raw.strip().lower()
        normalized = key.replace("-", "_")
        slug = self._alias_map.get(key)
        if slug is None:
            slug = self._alias_map.get(normalized)
        if slug is None:
            slug = normalized
        return POTION_DEFINITION_MAP.get(slug)

    def _effect_description(self, definition: PotionDefinition) -> str:
        percentage = int(definition.effect_value * 100)
        if definition.effect_type == "pb_boost":
            return (
                f"Tes gains de PB sont augmentés de {percentage}% pendant"
                f" {_format_duration(self.DURATION_SECONDS)}."
            )
        if definition.effect_type == "egg_luck":
            return (
                f"Ta chance d'obtenir les meilleurs pets est augmentée de {percentage}%"
                f" pendant {_format_duration(self.DURATION_SECONDS)}."
            )
        return "Effet appliqué."  # Sécurité pour d'éventuels effets futurs

    async def _activate_user_potion(
        self,
        user_id: int,
        definition: PotionDefinition,
        expires_at: datetime,
    ) -> bool:
        async with self.database.transaction() as connection:
            consumed = await self.database.consume_user_potion(
                user_id,
                definition.slug,
                connection=connection,
            )
            if not consumed:
                return False
            await self.database.set_active_potion(
                user_id,
                definition.slug,
                expires_at,
                connection=connection,
            )
        return True

    @commands.command(name="potions")
    async def list_potions(self, ctx: commands.Context) -> None:
        """Affiche l'inventaire et la potion active."""

        rows = await self.database.get_user_potions(ctx.author.id)
        inventory_lines: list[str] = []
        for row in rows:
            slug = str(row.get("potion_slug"))
            quantity = int(row.get("quantity") or 0)
            definition = POTION_DEFINITION_MAP.get(slug)
            if definition is None:
                display = slug
            else:
                display = definition.name
            inventory_lines.append(f"• {display} (x{quantity})")

        active = await self.database.get_active_potion(ctx.author.id)
        lines: list[str] = []
        if inventory_lines:
            lines.append("🧪 Tes potions :")
            lines.extend(inventory_lines)
        else:
            lines.append("🧪 Tu n'as aucune potion en stock pour le moment.")

        if active:
            definition, expires_at = active
            remaining = expires_at - datetime.now(timezone.utc)
            lines.append(
                f"⏰ Potion active : **{definition.name}** — reste {_format_duration(remaining.total_seconds())}."
            )
        else:
            lines.append("⏰ Aucune potion active.")

        embed = embeds.info_embed("\n".join(lines), title="Inventaire des potions")
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="usepotion")
    async def use_potion(self, ctx: commands.Context, slug: str | None = None) -> None:
        """Active une potion à partir de l'inventaire."""

        if not slug:
            await ctx.send(
                embed=embeds.warning_embed(
                    "Indique la potion à utiliser, par exemple `e!usepotion fortune_i`.",
                    title="Potion",
                )
            )
            return

        definition = self._resolve_potion(slug)
        if definition is None:
            await ctx.send(
                embed=embeds.error_embed("Potion inconnue. Utilise son slug (ex: `fortune_i`).")
            )
            return

        previous_active = await self.database.get_active_potion(ctx.author.id)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.DURATION_SECONDS)

        success = await self._activate_user_potion(ctx.author.id, definition, expires_at)
        if not success:
            await ctx.send(
                embed=embeds.error_embed("Tu ne possèdes pas cette potion dans ton inventaire."),
            )
            return

        lines = [f"✅ {definition.name} activée !", self._effect_description(definition)]
        if previous_active and previous_active[0].slug != definition.slug:
            lines.append(f"L'ancienne potion **{previous_active[0].name}** a été remplacée.")
        elif previous_active:
            lines.append("La durée de ta potion a été réinitialisée.")

        embed = embeds.success_embed("\n".join(lines), title="Potion activée")
        await ctx.send(embed=embed)

    @commands.command(name="potiontime")
    async def potion_time(self, ctx: commands.Context) -> None:
        """Affiche la potion active et son temps restant."""

        active = await self.database.get_active_potion(ctx.author.id)
        if not active:
            await ctx.send(
                embed=embeds.info_embed("Aucune potion active pour le moment.", title="Potions"),
            )
            return

        definition, expires_at = active
        remaining_seconds = (expires_at - datetime.now(timezone.utc)).total_seconds()
        lines = [
            f"Potion active : **{definition.name}**",
            f"Temps restant : {_format_duration(remaining_seconds)}",
        ]
        embed = embeds.success_embed("\n".join(lines), title="Statut de potion")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Potions(bot))
