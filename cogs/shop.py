"""Gestion de la boutique et des inventaires."""
from __future__ import annotations

import logging
from typing import Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import Colors, ITEMS_PER_PAGE, OWNER_ID, SHOP_TAX_RATE, XP_ROLE_BOOSTS
from utils import embeds

logger = logging.getLogger(__name__)


def build_shop_embed(items: list[Dict], page: int, total: int) -> discord.Embed:
    if not items:
        return embeds.info_embed("La boutique est vide pour le moment.")

    embed = discord.Embed(title=f"🛍️ Boutique — Page {page}", color=Colors.INFO)
    for item in items:
        data = item.get("data", {})
        description = item.get("description", "Aucune description")
        if item["type"] == "role":
            tier = str(data.get("tier", "")).upper()
            bonus = XP_ROLE_BOOSTS.get(tier, 0.0)
            description += f"\nBonus XP : +{bonus * 100:.0f}%" if bonus else ""
        if item["type"] == "timeout_token":
            description += f"\nContient {data.get('amount', 1)} jeton(s) timeout"
        if item["type"] == "defense":
            description += "\nProtection permanente contre les vols"
        embed.add_field(
            name=f"#{item['id']} — {item['name']} ({item['price']:,} PB)",
            value=description,
            inline=False,
        )
    embed.set_footer(text=f"{total} item(s) disponibles")
    return embed


class Shop(commands.Cog):
    """Cog de gestion des achats."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.database = bot.database

    async def display_shop(self, ctx_or_inter, page: int) -> None:
        items, total = await self.database.list_shop_items(page=page, per_page=ITEMS_PER_PAGE)
        embed = build_shop_embed(items, page, total)
        await ctx_or_inter.send(embed=embed)

    @commands.command(name="shop")
    async def shop_prefix(self, ctx: commands.Context, page: int = 1) -> None:
        page = max(page, 1)
        await self.display_shop(ctx, page)

    @app_commands.command(name="shop", description="Afficher la boutique")
    async def shop_slash(self, interaction: discord.Interaction, page: int = 1) -> None:
        page = max(page, 1)
        await interaction.response.defer(ephemeral=True)
        await self.display_shop(interaction.followup, page)

    # ------------------------------------------------------------------
    # Achat
    # ------------------------------------------------------------------
    async def _apply_purchase_effect(self, member: discord.Member, item: Dict) -> Optional[str]:
        item_type = item.get("type")
        data = item.get("data", {})
        if item_type == "role":
            role_id = data.get("role_id")
            if not role_id:
                return "Item invalide (role manquant)."
            role = member.guild.get_role(int(role_id)) if member.guild else None
            if role is None:
                return "Rôle introuvable sur ce serveur."
            try:
                await member.add_roles(role, reason="Achat boutique EcoBot")
            except discord.Forbidden:
                return "Je n'ai pas la permission d'attribuer ce rôle."
            tier = str(data.get("tier", role.name)).upper()
            await self.database.set_xp_boost_role(member.id, tier)
            return None
        if item_type == "timeout_token":
            amount = int(data.get("amount", 1))
            await self.database.add_timeout_tokens(member.id, amount)
            return None
        if item_type == "defense":
            await self.database.set_defense_status(member.id, True)
            return None
        return "Type d'item non géré."

    async def purchase(self, member: discord.Member, item_id: int) -> discord.Embed:
        item = await self.database.get_shop_item(item_id)
        if not item or not item.get("is_active", True):
            return embeds.error_embed("Cet item n'existe pas ou n'est pas disponible.")

        base_price = int(item["price"])
        tax_amount = round(base_price * SHOP_TAX_RATE)
        total_price = base_price + tax_amount

        async with self.database.transaction() as conn:
            balance = await conn.fetchval(
                "SELECT balance FROM users WHERE user_id = $1 FOR UPDATE",
                member.id,
            )
            if balance is None or balance < total_price:
                return embeds.error_embed("Solde insuffisant pour cet achat.")

            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", total_price, member.id)
            await conn.execute(
                """
                INSERT INTO user_purchases (user_id, item_id, price_paid, tax_paid)
                VALUES ($1, $2, $3, $4)
                """,
                member.id,
                item["id"],
                total_price,
                tax_amount,
            )

        await self.database.log_transaction(
            member.id,
            "purchase",
            -total_price,
            balance,
            balance - total_price,
            description=f"Achat de {item['name']}",
        )

        if OWNER_ID and tax_amount:
            await self.database.increment_balance(OWNER_ID, tax_amount)
        if item["type"] == "role":
            await self.database.add_public_bank_funds(base_price)
        effect_error = await self._apply_purchase_effect(member, item)
        if effect_error:
            return embeds.warning_embed(effect_error)
        return embeds.success_embed(f"Achat de {item['name']} réussi !")

    @commands.command(name="buy")
    async def buy_prefix(self, ctx: commands.Context, item_id: int) -> None:
        embed = await self.purchase(ctx.author, item_id)
        await ctx.send(embed=embed)

    @app_commands.command(name="buy", description="Acheter un item")
    async def buy_slash(self, interaction: discord.Interaction, item_id: int) -> None:
        embed = await self.purchase(interaction.user, item_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Inventaire
    # ------------------------------------------------------------------
    async def send_inventory(self, ctx_or_inter, member: discord.Member) -> None:
        inventory = await self.database.get_user_inventory(member.id)
        embed = embeds.inventory_embed(member, inventory)
        await ctx_or_inter.send(embed=embed)

    @commands.command(name="inventory", aliases=("inv",))
    async def inventory_prefix(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        member = member or ctx.author
        await self.send_inventory(ctx, member)

    @app_commands.command(name="inventory", description="Voir ton inventaire")
    async def inventory_slash(self, interaction: discord.Interaction, membre: Optional[discord.Member] = None) -> None:
        member = membre or interaction.user
        await interaction.response.defer(ephemeral=True)
        await self.send_inventory(interaction.followup, member)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))
