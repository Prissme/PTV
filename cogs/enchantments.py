from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence

from discord.ext import commands

from config import PREFIX
from utils import embeds
from utils.enchantments import (
    ENCHANTMENT_DEFINITION_MAP,
    ENCHANTMENT_DEFINITIONS,
    format_enchantment,
    get_enchantment_emoji,
)


class Enchantments(commands.Cog):
    """Gestion et équipement des enchantements."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database
        self._alias_map: Dict[str, str] = {}
        self._build_aliases()

    def _build_aliases(self) -> None:
        for definition in ENCHANTMENT_DEFINITIONS:
            slug = definition.slug
            normalized = slug.replace("_", "")
            self._alias_map[slug] = slug
            self._alias_map[normalized] = slug
            name_key = definition.name.lower()
            self._alias_map[name_key] = slug
            self._alias_map[name_key.replace(" ", "")] = slug

    def _resolve_slug(self, raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        key = raw.strip().lower()
        if not key:
            return None
        normalized = key.replace("-", "_")
        slug = self._alias_map.get(normalized)
        if slug is None:
            slug = self._alias_map.get(normalized.replace("_", ""))
        return slug

    async def _slot_limit(self, user_id: int) -> int:
        rebirth_count = await self.database.get_rebirth_count(user_id)
        return 2 if rebirth_count > 0 else 1

    async def _send_inventory(self, ctx: commands.Context) -> None:
        rows = await self.database.get_user_enchantments(ctx.author.id)
        equipped_rows = await self.database.get_equipped_enchantments(ctx.author.id)
        slot_limit = await self._slot_limit(ctx.author.id)
        equipped_lookup: Dict[str, int] = {
            str(row.get("slug") or ""): int(row.get("power") or 0)
            for row in equipped_rows
        }
        equipped_count = len(equipped_lookup)

        description_lines: List[str] = [
            f"Slots équipés : **{equipped_count}/{slot_limit}**",
        ]
        if slot_limit == 1:
            description_lines.append("🔒 Débloque un second slot après ton premier rebirth.")

        if equipped_rows:
            description_lines.append("\n✨ Enchantements actifs :")
            for row in equipped_rows:
                slug = str(row.get("slug") or "")
                power = int(row.get("power") or 0)
                if not slug or power <= 0:
                    continue
                definition = ENCHANTMENT_DEFINITION_MAP.get(slug)
                label = (
                    format_enchantment(definition, power)
                    if definition
                    else f"{slug} (puissance {power})"
                )
                description_lines.append(
                    f"{get_enchantment_emoji(slug)} {label}"
                )
        else:
            description_lines.append("✨ Aucun enchantement équipé pour le moment.")

        inventory_lines: List[str] = []
        for row in rows:
            slug = str(row.get("slug") or "")
            power = int(row.get("power") or 0)
            quantity = int(row.get("quantity") or 0)
            if not slug or power <= 0 or quantity <= 0:
                continue
            definition = ENCHANTMENT_DEFINITION_MAP.get(slug)
            label = (
                format_enchantment(definition, power)
                if definition
                else f"{slug} (puissance {power})"
            )
            equipped_power = equipped_lookup.get(slug)
            if equipped_power == power:
                status = " — ✅ Équipé"
            elif equipped_power:
                status = f" — ⚠️ Slot utilisé sur le niveau {equipped_power}"
            else:
                status = ""
            inventory_lines.append(
                f"{get_enchantment_emoji(slug)} {label} ×{quantity}{status}"
            )

        if inventory_lines:
            description_lines.append("\n🎒 Inventaire :")
            description_lines.extend(inventory_lines)
        else:
            description_lines.append(
                "🎒 Tu n'as pas encore obtenu d'enchantement. Continue à jouer aux événements !"
            )

        description_lines.append(
            f"\nUtilise `{PREFIX}enchants equip <nom>` pour activer un enchantement."
        )
        description_lines.append(
            f"Utilise `{PREFIX}enchants unequip <nom>` pour libérer un slot."
        )

        embed = embeds.info_embed("\n".join(description_lines), title="Enchantements")
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    def _find_owned_powers(
        self, rows: Sequence[Mapping[str, object]], slug: str
    ) -> List[int]:
        powers: List[int] = []
        for row in rows:
            if str(row.get("slug") or "") != slug:
                continue
            quantity = int(row.get("quantity") or 0)
            if quantity <= 0:
                continue
            powers.append(int(row.get("power") or 0))
        return powers

    @commands.group(
        name="enchants",
        aliases=("enchantments", "enchant"),
        invoke_without_command=True,
    )
    async def enchants(self, ctx: commands.Context) -> None:
        await self._send_inventory(ctx)

    @enchants.command(name="equip")
    async def equip_enchantment(
        self, ctx: commands.Context, slug: Optional[str] = None, power: Optional[int] = None
    ) -> None:
        if not slug:
            await ctx.send(
                embed=embeds.warning_embed(
                    f"Indique l'enchantement à équiper, par exemple `{PREFIX}enchants equip priss`.",
                    title="Enchantements",
                )
            )
            return

        resolved_slug = self._resolve_slug(slug)
        definition = ENCHANTMENT_DEFINITION_MAP.get(resolved_slug or "")
        if resolved_slug is None or definition is None:
            await ctx.send(embed=embeds.error_embed("Cet enchantement est inconnu."))
            return

        rows = await self.database.get_user_enchantments(ctx.author.id)
        owned_powers = self._find_owned_powers(rows, resolved_slug)
        if not owned_powers:
            await ctx.send(embed=embeds.error_embed("Tu ne possèdes pas cet enchantement."))
            return

        selected_power = power if power is not None else max(owned_powers)
        if selected_power not in owned_powers:
            await ctx.send(
                embed=embeds.error_embed(
                    "Tu ne possèdes pas cet enchantement à ce niveau. Utilise un niveau existant."
                )
            )
            return

        slot_limit = await self._slot_limit(ctx.author.id)
        result = await self.database.equip_user_enchantment(
            ctx.author.id,
            resolved_slug,
            power=selected_power,
            slot_limit=slot_limit,
        )

        label = format_enchantment(definition, selected_power)
        if result == "missing":
            await ctx.send(embed=embeds.error_embed("Tu ne possèdes plus cet enchantement."))
            return
        if result == "limit":
            plural = "s" if slot_limit > 1 else ""
            await ctx.send(
                embed=embeds.error_embed(
                    f"Tu as déjà {slot_limit} enchantement{plural} équipé{plural}. Libère un slot avant d'en ajouter un nouveau."
                )
            )
            return
        if result == "unchanged":
            await ctx.send(
                embed=embeds.info_embed(f"{label} est déjà équipé.", title="Enchantements")
            )
            return

        message = (
            f"{label} équipé !"
            if result == "equipped"
            else f"{label} est maintenant équipé à ce niveau."
        )
        embed = embeds.success_embed(message, title="Enchantements")
        await ctx.send(embed=embed)

    @enchants.command(name="unequip", aliases=("desequip", "remove"))
    async def unequip_enchantment(
        self, ctx: commands.Context, slug: Optional[str] = None
    ) -> None:
        if not slug:
            await ctx.send(
                embed=embeds.warning_embed(
                    f"Indique l'enchantement à retirer, par exemple `{PREFIX}enchants unequip priss`.",
                    title="Enchantements",
                )
            )
            return

        resolved_slug = self._resolve_slug(slug)
        if resolved_slug is None:
            await ctx.send(embed=embeds.error_embed("Cet enchantement est inconnu."))
            return

        removed = await self.database.unequip_user_enchantment(ctx.author.id, resolved_slug)
        if not removed:
            await ctx.send(
                embed=embeds.error_embed("Cet enchantement n'est pas équipé."),
            )
            return

        definition = ENCHANTMENT_DEFINITION_MAP.get(resolved_slug)
        label = definition.name if definition else resolved_slug
        await ctx.send(
            embed=embeds.success_embed(
                f"{label} a été retiré de tes slots.", title="Enchantements"
            )
        )


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - hook discord.py
    await bot.add_cog(Enchantments(bot))
