import asyncio
import asyncio
import os
import sys
from contextlib import asynccontextmanager
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

    async def get_enchantment_powers(
        self, user_id: int
    ) -> dict[str, int]:  # pragma: no cover - stub
        return {}

    @asynccontextmanager
    async def transaction(self):  # pragma: no cover - stub
        yield _DummyConnection()


def test_claim_income_returns_consistent_tuple_length() -> None:
    database = _DummyDatabase()
    result = asyncio.run(database.claim_active_pet_income(42))
    assert result == (
        0,
        [],
        0.0,
        {},
        {},
        {},
        {},
        {},
        {},
        {"count": 0, "bonus": 0, "multiplier": 1.0},
    )
