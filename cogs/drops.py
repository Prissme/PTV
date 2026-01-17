"""Gestion des drops aléatoires dans un salon dédié."""
from __future__ import annotations

import logging
import random
from typing import Sequence

import discord
from discord.ext import commands, tasks

from config import Emojis, POTION_DEFINITIONS, PET_DEFINITIONS
from utils import embeds
from utils.enchantments import (
    get_enchantment_emoji,
    pick_random_enchantment,
    roll_enchantment_power,
)
from utils.formatting import format_compact, format_currency
from utils.pet_formatting import pet_emoji

logger = logging.getLogger(__name__)

DROP_CHANNEL_ID = 1236724293631611022
DROP_CHANCE = 1 / 3600
GOOD_PET_RARITIES = {"Légendaire", "Mythique", "Secret"}


def _pick_good_pet() -> str:
    candidates = [
        pet
        for pet in PET_DEFINITIONS
        if not pet.is_huge and pet.rarity in GOOD_PET_RARITIES
    ]
    if not candidates:
        candidates = list(PET_DEFINITIONS)
    pet = random.choice(candidates)
    return f"{pet_emoji(pet.name)} **{pet.name}** ({pet.rarity})"


def _pick_good_potion() -> str:
    candidates = [
        potion
        for potion in POTION_DEFINITIONS
        if potion.effect_value >= 0.75 or potion.slug == "mastery_xp"
    ]
    if not candidates:
        candidates = list(POTION_DEFINITIONS)
    potion = random.choice(candidates)
    return f"🧪 **{potion.name}**"


def _pick_good_enchantment() -> str:
    enchantment = pick_random_enchantment()
    power = max(5, roll_enchantment_power())
    emoji = get_enchantment_emoji(enchantment.slug)
    return f"{emoji} **{enchantment.name}** (puissance {power})"


def _pick_pb_reward() -> str:
    amount = random.randint(5_000, 50_000)
    return f"💰 **{format_currency(amount)}**"


def _pick_gem_reward() -> str:
    amount = random.randint(100, 1_000)
    return f"{format_compact(amount)} {Emojis.GEM}"


class DropClaimView(discord.ui.View):
    def __init__(self, loot: str) -> None:
        super().__init__(timeout=300)
        self.loot = loot
        self.claimed_by: discord.User | None = None
        self.message: discord.Message | None = None

    def _build_embed(self, *, claimer: discord.abc.User | None = None) -> discord.Embed:
        embed = embeds.info_embed(
            f"Un drop vient d'apparaître : {self.loot}",
            title="🎁 Drop sauvage",
        )
        if claimer is not None:
            embed.add_field(name="Réclamé par", value=claimer.mention, inline=False)
        return embed

    def _disable_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        self._disable_buttons()
        await self.message.edit(view=self)

    @discord.ui.button(label="Claim le drop", style=discord.ButtonStyle.success, emoji="🎁")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.claimed_by is not None:
            await interaction.response.send_message(
                f"Ce drop a déjà été réclamé par {self.claimed_by.mention}.",
                ephemeral=True,
            )
            return

        self.claimed_by = interaction.user
        button.disabled = True
        embed = self._build_embed(claimer=interaction.user)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


def _roll_drop() -> str:
    choices: Sequence[tuple[str, int]] = (
        ("pet", 4),
        ("enchant", 3),
        ("potion", 3),
        ("pb", 2),
        ("gems", 2),
    )
    pool = [entry for entry, weight in choices for _ in range(weight)]
    selected = random.choice(pool)
    if selected == "pet":
        return _pick_good_pet()
    if selected == "enchant":
        return _pick_good_enchantment()
    if selected == "potion":
        return _pick_good_potion()
    if selected == "gems":
        return _pick_gem_reward()
    return _pick_pb_reward()


class Drops(commands.Cog):
    """Lance des drops aléatoires dans un salon fixe."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._drop_loop.start()

    def cog_unload(self) -> None:
        self._drop_loop.cancel()

    async def _get_drop_channel(self) -> discord.abc.Messageable | None:
        channel = self.bot.get_channel(DROP_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(DROP_CHANNEL_ID)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                logger.warning("Impossible de récupérer le salon de drop %s", DROP_CHANNEL_ID)
                return None
        if not isinstance(channel, discord.abc.Messageable):
            logger.warning("Salon de drop non compatible pour l'envoi")
            return None
        return channel

    @tasks.loop(seconds=1)
    async def _drop_loop(self) -> None:
        if random.random() > DROP_CHANCE:
            return
        channel = await self._get_drop_channel()
        if channel is None:
            return
        loot = _roll_drop()
        view = DropClaimView(loot)
        embed = view._build_embed()
        message = await channel.send(embed=embed, view=view)
        view.message = message

    @_drop_loop.before_loop
    async def _before_drop_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Drops(bot))
