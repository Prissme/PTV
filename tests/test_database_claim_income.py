import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgres://user:pass@localhost/db")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.db import Database  # noqa: E402


class _DummyConnection:
    async def fetch(self, *args, **kwargs):  # pragma: no cover - simple stub
        return []

    async def execute(self, *args, **kwargs):  # pragma: no cover - simple stub
        return None


class _DummyDatabase(Database):
    def __init__(self) -> None:
        self._dsn = "postgres://dummy"
        self._pool = None

    async def ensure_user(self, user_id: int) -> None:  # pragma: no cover - stub
        return None

    async def get_best_non_huge_income(
        self, user_id: int, *, connection=None
    ) -> int:  # pragma: no cover - stub
        return 0

    @asynccontextmanager
    async def transaction(self):  # pragma: no cover - stub
        yield _DummyConnection()


def test_claim_income_returns_consistent_tuple_length() -> None:
    database = _DummyDatabase()
    result = asyncio.run(database.claim_active_pet_income(42))
    assert result == (0, [], 0.0, {}, {}, {}, {})


class _ScriptedConnection(_DummyConnection):
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *args, **kwargs):  # pragma: no cover - simple stub
        return self._rows


class _ScenarioDatabase(_DummyDatabase):
    def __init__(self, rows):
        super().__init__()
        self._rows = rows
        self._potion_cleared = False

    async def get_best_non_huge_income(self, user_id: int, *, connection=None) -> int:  # pragma: no cover - stub
        return 100

    @asynccontextmanager
    async def transaction(self):  # pragma: no cover - stub
        yield _ScriptedConnection(self._rows)

    async def clear_active_potion(self, user_id: int, *, connection=None) -> None:  # pragma: no cover - stub
        self._potion_cleared = True

    @property
    def potion_cleared(self) -> bool:
        return self._potion_cleared


def test_claim_income_clears_expired_potion_on_early_return() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": 1,
            "nickname": "Buddy",
            "is_active": True,
            "is_huge": False,
            "is_gold": False,
            "is_rainbow": False,
            "is_shiny": False,
            "huge_level": None,
            "huge_xp": None,
            "acquired_at": now,
            "pet_id": 1,
            "name": "Test",
            "rarity": "Common",
            "image_url": "http://example.invalid/pet.png",
            "base_income_per_hour": 100,
            "pet_last_claim": now + timedelta(seconds=60),
            "balance": 1_000,
            "pet_booster_multiplier": 2.0,
            "pet_booster_expires_at": now + timedelta(hours=1),
            "pet_booster_activated_at": now - timedelta(hours=1),
            "active_potion_slug": "fortune_i",
            "active_potion_expires_at": now - timedelta(seconds=30),
            "member_clan_id": 5,
            "clan_name": "Test Clan",
            "pb_boost_multiplier": 1.5,
            "clan_boost_level": 2,
            "clan_banner": "🏳️",
            "shiny_luck_multiplier": 1.1,
        }
    ]

    database = _ScenarioDatabase(rows)
    (
        amount,
        returned_rows,
        elapsed,
        booster,
        clan,
        progress,
        potion,
    ) = asyncio.run(database.claim_active_pet_income(42))

    assert amount == 0
    assert returned_rows == rows
    assert elapsed == 0.0
    assert booster["multiplier"] == 2.0
    assert booster["remaining_seconds"] > 0
    assert clan == {}
    assert progress == {}
    assert potion == {}
    assert database.potion_cleared is True
