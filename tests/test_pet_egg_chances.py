import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# Ensure configuration loads without real secrets before importing the bot modules.
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgres://user:pass@localhost/db")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord
from discord.ext import commands

from cogs import pets as pets_module
from cogs.pets import _compute_egg_mastery_perks, _compute_pet_mastery_perks


class DummyDatabase:
    def __init__(self) -> None:
        self.balance = 50_000
        self.last_added: dict[str, object] | None = None

    async def sync_pets(self, definitions):
        return {definition.name: index for index, definition in enumerate(definitions, start=1)}

    async def ensure_user(self, user_id: int):  # pragma: no cover - interface contract
        return None

    async def fetch_balance(self, user_id: int) -> int:
        return self.balance

    async def increment_balance(self, user_id: int, amount: int, *, transaction_type: str, description: str, related_user_id=None):
        self.balance += amount
        return self.balance, self.balance

    async def add_user_pet(self, user_id: int, pet_id: int, *, is_huge: bool = False, is_gold: bool = False, is_rainbow: bool = False, is_shiny: bool = False):
        self.last_added = {
            "user_id": user_id,
            "pet_id": pet_id,
            "is_huge": is_huge,
            "is_gold": is_gold,
            "is_rainbow": is_rainbow,
            "is_shiny": is_shiny,
        }
        return {"id": 1}

    async def record_pet_opening(self, user_id: int, pet_id: int):  # pragma: no cover - interface contract
        return None

    async def get_pet_market_values(self):
        return {}

    async def get_best_non_huge_income(self, user_id: int) -> int:
        return 0

    async def add_mastery_experience(self, user_id: int, slug: str, amount: int):
        return {"levels_gained": 0}

    async def get_user_clan(self, user_id: int):
        return None

    async def get_active_potion(self, user_id: int):
        return None


class DummyMessage:
    async def edit(self, *, content=None, embed=None, view=None):  # pragma: no cover - animation helper
        return None


class DummyContext:
    def __init__(self):
        avatar = SimpleNamespace(url="https://example.com/avatar.png")
        self.author = SimpleNamespace(
            id=42,
            display_name="Tester",
            display_avatar=avatar,
            mention="@Tester",
        )
        self.guild = None
        self.channel = None

    async def send(self, *args, **kwargs):  # pragma: no cover - messages are ignored during tests
        return DummyMessage()


async def _create_cog():
    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)
    database = DummyDatabase()
    bot.database = database
    cog = pets_module.Pets(bot)
    await cog.cog_load()
    return cog, database, bot


async def _no_sleep(_delay):  # pragma: no cover - helper
    return None


async def _exercise_gold_chance(monkeypatch):
    cog, database, bot = await _create_cog()
    try:
        monkeypatch.setattr(pets_module, "GOLD_PET_CHANCE", 0.6)
        monkeypatch.setattr(pets_module, "RAINBOW_PET_CHANCE", 0.0)
        monkeypatch.setattr(pets_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(pets_module.asyncio, "sleep", _no_sleep)

        ctx = DummyContext()
        egg = next(iter(cog._eggs.values()))
        egg_perks = _compute_egg_mastery_perks(1)
        pet_perks = _compute_pet_mastery_perks(1)

        await cog._open_pet_egg(
            ctx, egg, mastery_perks=egg_perks, pet_mastery_perks=pet_perks
        )

        assert database.last_added is not None
        assert database.last_added["is_gold"] is True
        assert database.last_added["is_rainbow"] is False
    finally:
        await bot.close()


async def _exercise_rainbow_chance(monkeypatch):
    cog, database, bot = await _create_cog()
    try:
        monkeypatch.setattr(pets_module, "GOLD_PET_CHANCE", 0.0)
        monkeypatch.setattr(pets_module, "RAINBOW_PET_CHANCE", 0.7)
        monkeypatch.setattr(pets_module.random, "random", lambda: 0.0)
        monkeypatch.setattr(pets_module.asyncio, "sleep", _no_sleep)

        ctx = DummyContext()
        egg = next(iter(cog._eggs.values()))
        egg_perks = _compute_egg_mastery_perks(1)
        pet_perks = _compute_pet_mastery_perks(1)

        await cog._open_pet_egg(
            ctx, egg, mastery_perks=egg_perks, pet_mastery_perks=pet_perks
        )

        assert database.last_added is not None
        assert database.last_added["is_rainbow"] is True
        assert database.last_added["is_gold"] is False
    finally:
        await bot.close()


async def _exercise_egg_lookup_variants():
    cog, _database, bot = await _create_cog()
    try:
        egg = cog._resolve_egg("œuf metallique")
        assert egg is not None
        assert egg.slug == "metallique"
    finally:
        await bot.close()


def test_base_gold_chance_stays_active(monkeypatch):
    asyncio.run(_exercise_gold_chance(monkeypatch))


def test_base_rainbow_chance_stays_active(monkeypatch):
    asyncio.run(_exercise_rainbow_chance(monkeypatch))


def test_egg_lookup_accepts_mixed_diacritics():
    asyncio.run(_exercise_egg_lookup_variants())
