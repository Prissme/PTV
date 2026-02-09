from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from typing import Dict, Mapping, Optional, Sequence

import discord
from discord.ext import commands

from config import (
    POTION_DEFINITION_MAP,
    POTION_DEFINITIONS,
    POTION_SELL_VALUES,
    PotionDefinition,
)
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
        duration_seconds = getattr(definition, "duration_seconds", self.DURATION_SECONDS)
        percentage = int(definition.effect_value * 100)
        if definition.effect_type == "pb_boost":
            return (
                f"Tes gains de PB sont augmentés de {percentage}% pendant"
                f" {_format_duration(duration_seconds)}."
            )
        if definition.effect_type == "egg_luck":
            return (
                f"Ta chance d'obtenir les meilleurs pets est augmentée de {percentage}%"
                f" pendant {_format_duration(duration_seconds)}."
            )
        if definition.effect_type == "mastery_xp":
            return (
                f"Ton XP de maîtrise est multiplié par {1 + definition.effect_value:g}"
                f" pendant {_format_duration(duration_seconds)}."
            )
        if definition.effect_type == "slots_luck":
            return (
                "Tes chances de gains au casino sont améliorées pendant "
                f"{_format_duration(duration_seconds)}."
            )
        return "Effet appliqué."  # Sécurité pour d'éventuels effets futurs

    async def _fetch_potion_state(
        self, user_id: int
    ) -> tuple[Sequence[Mapping[str, object]], Optional[tuple[PotionDefinition, datetime]]]:
        return await asyncio.gather(
            self.database.get_user_potions(user_id),
            self.database.get_active_potion(user_id),
        )

    def _build_potion_embed(
        self,
        user: discord.abc.User,
        rows: Sequence[Mapping[str, object]],
        active: Optional[tuple[PotionDefinition, datetime]],
    ) -> discord.Embed:
        inventory_lines: list[str] = []
        for row in rows:
            slug = str(row.get("potion_slug"))
            quantity = int(row.get("quantity") or 0)
            definition = POTION_DEFINITION_MAP.get(slug)
            if definition is None:
                display = slug
                description = "Potion mystérieuse."
            else:
                display = definition.name
                description = definition.description
            sell_value = POTION_SELL_VALUES.get(slug)
            price_hint = (
                f" — revendable {embeds.format_currency(sell_value)}" if sell_value else ""
            )
            inventory_lines.append(
                f"• {display} (x{quantity}){price_hint}\n  {description}"
            )

        lines: list[str] = []
        if inventory_lines:
            lines.append("🧪 Tes potions :")
            lines.extend(inventory_lines)
        else:
            lines.append(
                "Tu n'as aucune potion en stock. Récupère-les dans les événements pour booster tes gains !"
            )

        if active:
            definition, expires_at = active
            remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
            lines.extend(
                (
                    "",
                    "✨ Potion active :",
                    f"• {definition.name} — {_format_duration(remaining)} restants",
                )
            )
        else:
            lines.extend(("", "Aucune potion active actuellement."))

        embed = embeds.info_embed("\n".join(lines), title="Inventaire des potions")
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        return embed

    async def _consume_and_schedule_potion(
        self, user_id: int, definition: PotionDefinition
    ) -> tuple[bool, PotionDefinition | None, float, datetime]:
        duration_seconds = getattr(definition, "duration_seconds", self.DURATION_SECONDS)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=duration_seconds)
        replaced_definition: PotionDefinition | None = None
        stacked_seconds = 0.0

        async with self.database.transaction() as connection:
            consumed = await self.database.consume_user_potion(
                user_id, definition.slug, connection=connection
            )
            if not consumed:
                return False, None, 0.0, expires_at

            row = await connection.fetchrow(
                """
                SELECT active_potion_slug, active_potion_expires_at
                FROM users
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )

            active_slug = str(row.get("active_potion_slug") or "") if row else ""
            active_expires_at = row.get("active_potion_expires_at") if row else None

            if active_slug and isinstance(active_expires_at, datetime):
                if active_expires_at > now and active_slug == definition.slug:
                    stacked_seconds = (active_expires_at - now).total_seconds()
                    expires_at = active_expires_at + timedelta(seconds=duration_seconds)
                elif active_expires_at > now:
                    replaced_definition = POTION_DEFINITION_MAP.get(active_slug)
                else:
                    await self.database.clear_active_potion(
                        user_id, connection=connection
                    )
            elif active_slug:
                await self.database.clear_active_potion(user_id, connection=connection)

            await self.database.set_active_potion(
                user_id,
                definition.slug,
                expires_at,
                connection=connection,
            )

        return True, replaced_definition, stacked_seconds, expires_at

    def _activation_embed(
        self,
        definition: PotionDefinition,
        replaced_definition: PotionDefinition | None,
        stacked_seconds: float,
        expires_at: datetime,
    ) -> discord.Embed:
        remaining_seconds = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
        duration_seconds = getattr(definition, "duration_seconds", self.DURATION_SECONDS)

        lines = [f"✅ {definition.name} activée !", self._effect_description(definition)]
        if stacked_seconds > 0:
            lines.append(
                "La durée de ta potion a été prolongée "
                f"de {_format_duration(duration_seconds)} (total {_format_duration(remaining_seconds)})."
            )
        elif replaced_definition and replaced_definition.slug != definition.slug:
            lines.append(f"L'ancienne potion **{replaced_definition.name}** a été remplacée.")
        elif replaced_definition:
            lines.append("La durée de ta potion a été réinitialisée.")

        return embeds.success_embed("\n".join(lines), title="Potion activée")

    @commands.command(name="potions")
    async def list_potions(self, ctx: commands.Context) -> None:
        """Affiche l'inventaire et la potion active."""

        rows, active = await self._fetch_potion_state(ctx.author.id)
        embed = self._build_potion_embed(ctx.author, rows, active)
        view = PotionInventoryView(self, ctx.author, rows, active)
        message = await ctx.send(embed=embed, view=view if view.has_controls else None)
        if view.has_controls:
            view.message = message

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

        success, replaced_definition, stacked_seconds, expires_at = (
            await self._consume_and_schedule_potion(ctx.author.id, definition)
        )
        if not success:
            await ctx.send(
                embed=embeds.error_embed("Tu ne possèdes pas cette potion dans ton inventaire."),
            )
            return

        embed = self._activation_embed(
            definition, replaced_definition, stacked_seconds, expires_at
        )
        await ctx.send(embed=embed)
        self.bot.dispatch("grade_quest_progress", ctx.author, "potion", 1, ctx.channel)

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

    @commands.command(name="sellpotion", aliases=("sellpotions", "vendrepotion", "vendrepotions"))
    async def sell_potion(
        self,
        ctx: commands.Context,
        slug: str | None = None,
        quantity: int = 1,
    ) -> None:
        """Permet de revendre une potion contre des PB."""

        if not slug:
            lines = [
                "Utilise `e!sellpotion <slug> [quantité]` pour revendre tes potions.",
                "Exemple : `e!sellpotion fortune_i 3`.",
                "Potions disponibles :",
            ]
            for definition in POTION_DEFINITIONS:
                value = POTION_SELL_VALUES.get(definition.slug)
                if value:
                    lines.append(
                        f"• `{definition.slug}` — {definition.name} → {embeds.format_currency(value)}"
                    )
            await ctx.send(
                embed=embeds.info_embed("\n".join(lines), title="Vente de potions")
            )
            return

        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 0

        if quantity <= 0:
            await ctx.send(
                embed=embeds.error_embed(
                    "La quantité à vendre doit être un nombre positif.",
                )
            )
            return

        definition = self._resolve_potion(slug)
        if definition is None:
            await ctx.send(
                embed=embeds.error_embed(
                    "Potion inconnue. Vérifie le slug avec `e!potions`.",
                )
            )
            return

        sell_value = POTION_SELL_VALUES.get(definition.slug)
        if not sell_value:
            await ctx.send(
                embed=embeds.error_embed(
                    "Cette potion ne peut pas être revendue pour le moment.",
                )
            )
            return

        total_value = sell_value * quantity

        await self.database.ensure_user(ctx.author.id)
        async with self.database.transaction() as connection:
            removed = await self.database.consume_user_potion(
                ctx.author.id,
                definition.slug,
                quantity=quantity,
                connection=connection,
            )
            if not removed:
                await ctx.send(
                    embed=embeds.error_embed(
                        "Tu n'as pas assez d'exemplaires de cette potion.",
                    )
                )
                return

            row = await connection.fetchrow(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                ctx.author.id,
            )
            if row is None:
                await ctx.send(
                    embed=embeds.error_embed(
                        "Impossible d'accéder à ton solde. Réessaie plus tard.",
                    )
                )
                return

            before_balance = int(row.get("balance") or 0)
            after_balance = before_balance + total_value
            await connection.execute(
                "UPDATE users SET balance = $2 WHERE user_id = $1",
                ctx.author.id,
                after_balance,
            )
            await self.database.record_transaction(
                user_id=ctx.author.id,
                transaction_type="potion_sale",
                amount=total_value,
                balance_before=before_balance,
                balance_after=after_balance,
                description=f"Vente {definition.name} x{quantity}",
                connection=connection,
            )

        embed = embeds.success_embed(
            (
                f"Tu as vendu **{quantity}** potion{'s' if quantity > 1 else ''}"
                f" {definition.name} pour {embeds.format_currency(total_value)}.\n"
                f"Nouveau solde : {embeds.format_currency(after_balance)}"
            ),
            title="Potion revendue",
        )
        await ctx.send(embed=embed)


class PotionSelect(discord.ui.Select):
    def __init__(self, view: "PotionInventoryView") -> None:
        self.inventory_view = view
        options: list[discord.SelectOption] = []
        for row in view.rows:
            slug = str(row.get("potion_slug") or "")
            quantity = int(row.get("quantity") or 0)
            if not slug or quantity <= 0:
                continue
            definition = POTION_DEFINITION_MAP.get(slug)
            name = definition.name if definition else slug
            description = f"En stock : {quantity}"
            options.append(
                discord.SelectOption(label=name[:95], value=slug, description=description)
            )

        super().__init__(
            placeholder="Choisis une potion à boire",
            min_values=1,
            max_values=1,
            options=options,
        )
        if options:
            options[0].default = True
            view.selected_slug = options[0].value

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is None:  # pragma: no cover - defensive
            return
        self.inventory_view.selected_slug = self.values[0]
        await interaction.response.defer()


class PotionInventoryView(discord.ui.View):
    def __init__(
        self,
        cog: Potions,
        author: discord.abc.User,
        rows: Sequence[Mapping[str, object]],
        active: Optional[tuple[PotionDefinition, datetime]],
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.author = author
        self.rows = tuple(rows)
        self.active = active
        self.selected_slug: str | None = None
        self.message: discord.Message | None = None

        self.select: PotionSelect | None = None
        self.drink_button: discord.ui.Button | None = None
        self._rebuild_components()

    @property
    def has_controls(self) -> bool:
        return bool(self.select and self.select.options)

    def _rebuild_components(self) -> None:
        self.clear_items()
        self.selected_slug = None

        self.select = PotionSelect(self)
        if self.select.options:
            self.add_item(self.select)

        self.drink_button = discord.ui.Button(
            label="Boire la potion",
            style=discord.ButtonStyle.success,
            disabled=not bool(self.select.options),
        )
        self.drink_button.callback = self._handle_drink  # type: ignore[assignment]
        self.add_item(self.drink_button)

    async def _handle_drink(self, interaction: discord.Interaction) -> None:
        if not self.selected_slug:
            await interaction.response.send_message(
                embed=embeds.error_embed("Choisis d'abord une potion dans le menu."),
                ephemeral=True,
            )
            return

        definition = POTION_DEFINITION_MAP.get(self.selected_slug)
        if definition is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Cette potion est inconnue ou n'est plus disponible."),
                ephemeral=True,
            )
            return

        success, replaced_definition, stacked_seconds, expires_at = (
            await self.cog._consume_and_schedule_potion(self.author.id, definition)
        )
        if not interaction.response.is_done():
            if not success:
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        "Tu n'as plus d'exemplaires de cette potion en stock."
                    ),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=self.cog._activation_embed(
                        definition, replaced_definition, stacked_seconds, expires_at
                    ),
                    ephemeral=True,
                )

        if not success:
            return

        channel = getattr(self.message, "channel", None)
        self.cog.bot.dispatch("grade_quest_progress", self.author, "potion", 1, channel)

        self.rows, self.active = await self.cog._fetch_potion_state(self.author.id)
        self._rebuild_components()
        await self._refresh_message()

    async def _refresh_message(self) -> None:
        if self.message is None:
            return
        embed = self.cog._build_potion_embed(self.author, self.rows, self.active)
        await self.message.edit(embed=embed, view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author.id:
            return True
        await interaction.response.send_message(
            "Seul le propriétaire de cet inventaire peut interagir avec ces boutons.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        with contextlib.suppress(discord.HTTPException):
            await self.message.edit(view=self)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Potions(bot))
