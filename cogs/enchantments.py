from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple, cast

import discord
from discord.ext import commands

from config import PREFIX
from utils import embeds
from utils.enchantments import (
    ENCHANTMENT_DEFINITION_MAP,
    ENCHANTMENT_DEFINITIONS,
    ENCHANTMENT_SELL_PRICES,
    format_enchantment,
    get_enchantment_emoji,
    get_enchantment_sell_price,
)


@dataclass(frozen=True)
class _QuickEquipEntry:
    slug: str
    label: str
    power: int


class EnchantmentEquipButton(discord.ui.Button):
    def __init__(self, entry: _QuickEquipEntry) -> None:
        label = entry.label[:80]
        super().__init__(
            label=label,
            style=discord.ButtonStyle.primary,
            custom_id=f"enchants:equip:{entry.slug}:{entry.power}",
        )
        self.entry = entry

    async def callback(self, interaction: discord.Interaction) -> None:  # pragma: no cover - UI
        view = cast("EnchantmentInventoryView", self.view)
        if view is None:
            return
        await view.handle_quick_equip(interaction, self.entry.slug)


class EnchantmentInventoryView(discord.ui.View):
    def __init__(
        self,
        cog: "Enchantments",
        ctx: commands.Context,
        entries: Sequence[_QuickEquipEntry],
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        for entry in entries[:25]:
            self.add_item(EnchantmentEquipButton(entry))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.ctx.author.id:
            return True

        await interaction.response.send_message(
            "Seul le propriétaire de ces enchantements peut utiliser ces boutons.",
            ephemeral=True,
        )
        return False

    async def handle_quick_equip(
        self, interaction: discord.Interaction, slug: str
    ) -> None:
        embed, _ = await self.cog._equip_enchantment_for_user(
            user_id=interaction.user.id,
            slug=slug,
            requested_power=None,
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


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

        quick_entries = self._build_quick_entries(rows)
        view = EnchantmentInventoryView(self, ctx, quick_entries) if quick_entries else None

        embed = embeds.info_embed("\n".join(description_lines), title="Enchantements")
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed, view=view)

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

    def _build_quick_entries(
        self, rows: Sequence[Mapping[str, object]]
    ) -> List[_QuickEquipEntry]:
        best: Dict[str, Tuple[int, str]] = {}
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
            entry_label = f"{get_enchantment_emoji(slug)} {label}"
            current = best.get(slug)
            if current is None or power > current[0]:
                best[slug] = (power, entry_label)

        entries = [
            _QuickEquipEntry(slug=slug, power=power, label=label)
            for slug, (power, label) in best.items()
        ]
        entries.sort(key=lambda item: item.label.lower())
        return entries

    async def _equip_enchantment_for_user(
        self,
        *,
        user_id: int,
        slug: str,
        requested_power: Optional[int],
    ) -> tuple[discord.Embed, bool]:
        definition = ENCHANTMENT_DEFINITION_MAP.get(slug)
        if definition is None:
            return embeds.error_embed("Cet enchantement est inconnu."), False

        rows = await self.database.get_user_enchantments(user_id)
        owned_powers = self._find_owned_powers(rows, slug)
        if not owned_powers:
            return embeds.error_embed("Tu ne possèdes pas cet enchantement."), False

        selected_power = requested_power if requested_power is not None else max(owned_powers)
        if selected_power not in owned_powers:
            return (
                embeds.error_embed(
                    "Tu ne possèdes pas cet enchantement à ce niveau. Utilise un niveau existant."
                ),
                False,
            )

        slot_limit = await self._slot_limit(user_id)
        result = await self.database.equip_user_enchantment(
            user_id,
            slug,
            power=selected_power,
            slot_limit=slot_limit,
        )

        label = format_enchantment(definition, selected_power)
        if result == "missing":
            return embeds.error_embed("Tu ne possèdes plus cet enchantement."), False
        if result == "limit":
            plural = "s" if slot_limit > 1 else ""
            return (
                embeds.error_embed(
                    f"Tu as déjà {slot_limit} enchantement{plural} équipé{plural}. Libère un slot avant d'en ajouter un nouveau."
                ),
                False,
            )
        if result == "unchanged":
            return (
                embeds.info_embed(f"{label} est déjà équipé.", title="Enchantements"),
                False,
            )

        message = (
            f"{label} équipé !"
            if result == "equipped"
            else f"{label} est maintenant équipé à ce niveau."
        )
        return embeds.success_embed(message, title="Enchantements"), True

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

        embed, _ = await self._equip_enchantment_for_user(
            user_id=ctx.author.id,
            slug=resolved_slug,
            requested_power=power,
        )
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

    @commands.command(name="adminshop")
    async def adminshop(
        self,
        ctx: commands.Context,
        slug: Optional[str] = None,
        power: Optional[int] = None,
        quantity: int = 1,
    ) -> None:
        if not slug or power is None:
            price_lines = [
                f"• Niveau {level} : {embeds.format_gems(price)}"
                for level, price in sorted(ENCHANTMENT_SELL_PRICES.items())
            ]
            description = [
                "Revends tes enchantements contre des gemmes (achat par le shop admin, pas entre joueurs).",
                "Utilisation :",
                f"`{PREFIX}adminshop <enchantement> <niveau> [quantité]`",
                "",
                "**Barème des ventes :**",
                *price_lines,
            ]
            embed = embeds.info_embed(
                "\n".join(description), title="Admin shop — Enchantements"
            )
            await ctx.send(embed=embed)
            return

        resolved_slug = self._resolve_slug(slug)
        definition = ENCHANTMENT_DEFINITION_MAP.get(resolved_slug or "")
        if resolved_slug is None or definition is None:
            await ctx.send(embed=embeds.error_embed("Cet enchantement est inconnu."))
            return

        if power < 1 or power > 10:
            await ctx.send(
                embed=embeds.error_embed("Le niveau doit être compris entre 1 et 10."),
            )
            return
        if quantity <= 0:
            await ctx.send(
                embed=embeds.error_embed("La quantité doit être positive."),
            )
            return

        unit_price = get_enchantment_sell_price(power)
        if unit_price is None:
            await ctx.send(
                embed=embeds.error_embed(
                    "Ce niveau n'est pas vendable actuellement dans l'admin shop."
                )
            )
            return

        status, payout, gems_before, gems_after, remaining_qty = (
            await self.database.sell_enchantment_for_gems(
                ctx.author.id,
                resolved_slug,
                power=power,
                quantity=quantity,
                unit_price=unit_price,
            )
        )

        label = format_enchantment(definition, power)
        if status == "missing":
            await ctx.send(
                embed=embeds.error_embed(
                    "Tu ne possèdes pas cet enchantement à ce niveau."
                )
            )
            return
        if status == "insufficient":
            await ctx.send(
                embed=embeds.error_embed(
                    f"Quantité insuffisante. Tu en possèdes {remaining_qty} à ce niveau."
                )
            )
            return
        if status != "sold":
            await ctx.send(
                embed=embeds.error_embed(
                    "Impossible de finaliser la vente pour le moment. Réessaie plus tard."
                )
            )
            return

        lines = [
            f"{label} ×{quantity} vendu pour {embeds.format_gems(payout)}.",
            f"Gemmes avant : {embeds.format_gems(gems_before)}",
            f"Gemmes après : {embeds.format_gems(gems_after)}",
        ]
        if remaining_qty <= 0:
            lines.append("Cet enchantement n'est plus présent à ce niveau dans ton inventaire.")

        await ctx.send(embed=embeds.success_embed("\n".join(lines), title="Admin shop"))


async def setup(bot: commands.Bot) -> None:  # pragma: no cover - hook discord.py
    await bot.add_cog(Enchantments(bot))
