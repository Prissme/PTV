"""Gestion des drops aléatoires dans un salon dédié."""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import Sequence

import discord
from discord.ext import commands, tasks

from config import Emojis, POTION_DEFINITIONS, PET_DEFINITIONS
from database.db import Database, DatabaseError
from utils import embeds
from utils.enchantments import (
    get_enchantment_emoji,
    pick_random_enchantment,
    roll_enchantment_power,
)
from utils.formatting import format_compact, format_currency
from utils.pet_formatting import pet_emoji

logger = logging.getLogger(__name__)

DROP_CHANNEL_ID = 1464700985506271365
DROP_CHANCE = 1 / 3600
GOOD_PET_RARITIES = {"Légendaire", "Mythique", "Secret"}


@dataclass(frozen=True)
class DropReward:
    kind: str
    label: str
    data: dict[str, object]


def _pick_good_pet() -> DropReward:
    candidates = [
        pet
        for pet in PET_DEFINITIONS
        if not pet.is_huge and pet.rarity in GOOD_PET_RARITIES
    ]
    if not candidates:
        candidates = list(PET_DEFINITIONS)
    pet = random.choice(candidates)
    label = f"{pet_emoji(pet.name)} **{pet.name}** ({pet.rarity})"
    return DropReward(kind="pet", label=label, data={"pet": pet})


def _pick_good_potion() -> DropReward:
    candidates = [
        potion
        for potion in POTION_DEFINITIONS
        if potion.effect_value >= 0.75 or potion.slug == "mastery_xp"
    ]
    if not candidates:
        candidates = list(POTION_DEFINITIONS)
    potion = random.choice(candidates)
    label = f"🧪 **{potion.name}**"
    return DropReward(kind="potion", label=label, data={"potion": potion})


def _pick_good_enchantment() -> DropReward:
    enchantment = pick_random_enchantment()
    power = max(5, roll_enchantment_power())
    emoji = get_enchantment_emoji(enchantment.slug)
    label = f"{emoji} **{enchantment.name}** (puissance {power})"
    return DropReward(
        kind="enchant",
        label=label,
        data={"enchantment": enchantment, "power": power},
    )


def _pick_pb_reward() -> DropReward:
    amount = random.randint(5_000, 50_000)
    label = f"💰 **{format_currency(amount)}**"
    return DropReward(kind="pb", label=label, data={"amount": amount})


def _pick_gem_reward() -> DropReward:
    amount = random.randint(100, 1_000)
    label = f"{format_compact(amount)} {Emojis.GEM}"
    return DropReward(kind="gems", label=label, data={"amount": amount})


class DropClaimView(discord.ui.View):
    def __init__(self, reward: DropReward, database: Database) -> None:
        super().__init__(timeout=300)
        self.reward = reward
        self.database = database
        self.claimed_by: discord.User | None = None
        self.message: discord.Message | None = None

    def _build_embed(self, *, claimer: discord.abc.User | None = None) -> discord.Embed:
        embed = embeds.info_embed(
            f"Un drop vient d'apparaître : {self.reward.label}",
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

    async def _apply_reward(self, user: discord.abc.User) -> None:
        if self.reward.kind == "pet":
            pet = self.reward.data["pet"]
            pet_id = await self.database.get_pet_id_by_name(pet.name)
            if pet_id is None:
                raise DatabaseError("Pet introuvable pour le drop")
            await self.database.add_user_pet(
                user.id,
                pet_id,
                is_huge=bool(getattr(pet, "is_huge", False)),
            )
            return

        if self.reward.kind == "potion":
            potion = self.reward.data["potion"]
            await self.database.add_user_potion(user.id, potion.slug)
            return

        if self.reward.kind == "enchant":
            enchantment = self.reward.data["enchantment"]
            power = int(self.reward.data["power"])
            await self.database.add_user_enchantment(
                user.id, enchantment.slug, power=power
            )
            return

        if self.reward.kind == "pb":
            amount = int(self.reward.data["amount"])
            await self.database.increment_balance(
                user.id,
                amount,
                transaction_type="drop",
                description="Drop sauvage",
            )
            return

        if self.reward.kind == "gems":
            amount = int(self.reward.data["amount"])
            await self.database.increment_gems(
                user.id,
                amount,
                transaction_type="drop",
                description="Drop sauvage",
            )
            return

        raise DatabaseError("Type de drop inconnu")

    @discord.ui.button(label="Claim le drop", style=discord.ButtonStyle.success, emoji="🎁")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.claimed_by is not None:
            await interaction.response.send_message(
                f"Ce drop a déjà été réclamé par {self.claimed_by.mention}.",
                ephemeral=True,
            )
            return

        try:
            await self._apply_reward(interaction.user)
        except DatabaseError:
            logger.exception(
                "Impossible d'attribuer le drop",
                extra={"user_id": interaction.user.id, "drop": self.reward.kind},
            )
            await interaction.response.send_message(
                "Impossible d'ajouter le drop à ton inventaire pour le moment.",
                ephemeral=True,
            )
            return

        self.claimed_by = interaction.user
        button.disabled = True
        embed = self._build_embed(claimer=interaction.user)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


def _roll_drop() -> DropReward:
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
        reward = _roll_drop()
        database = getattr(self.bot, "database", None)
        if not isinstance(database, Database):
            logger.error("Base de données indisponible pour attribuer le drop.")
            return
        view = DropClaimView(reward, database)
        embed = view._build_embed()
        message = await channel.send(embed=embed, view=view)
        view.message = message

    @_drop_loop.before_loop
    async def _before_drop_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Drops(bot))
