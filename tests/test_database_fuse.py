import asyncio
from contextlib import asynccontextmanager

import pytest

from config import TITANIC_GRIFF_NAME
from database.db import Database, DatabaseError


class _FakeConnection:
    def __init__(self) -> None:
        self.fetch_results: list[list[dict[str, object]]] = []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_results: list[dict[str, object]] = []
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls.append((query, args))
        return self.fetch_results.pop(0)

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        self.fetchrow_calls.append((query, args))
        if not self.fetchrow_results:
            return None
        return self.fetchrow_results.pop(0)

    async def execute(self, query: str, *args: object) -> None:  # pragma: no cover - simple stub
        self.execute_calls.append((query, args))


class _FakeDatabase(Database):
    def __init__(self) -> None:
        super().__init__("postgres://fake")
        self._connection = _FakeConnection()

    async def ensure_user(self, user_id: int) -> None:  # pragma: no cover - stub
        return None

    @asynccontextmanager
    async def transaction(self):  # pragma: no cover - deterministic stub
        yield self._connection


def test_forge_result_is_marked_huge() -> None:
    database = _FakeDatabase()
    connection = database._connection
    connection.fetchrow_results = [
        {"name": TITANIC_GRIFF_NAME},
        {"id": 999},
        {
            "id": 999,
            "is_active": False,
            "is_huge": True,
            "is_gold": False,
            "is_rainbow": False,
            "is_shiny": False,
            "huge_level": 1,
            "huge_xp": 0,
            "acquired_at": None,
            "pet_id": 42,
            "name": TITANIC_GRIFF_NAME,
            "rarity": "Secret",
            "image_url": "https://example.com",
            "base_income_per_hour": 1000,
        },
    ]
    connection.fetch_results = [
        [
            {"id": idx, "is_active": False, "is_huge": False, "on_market": False}
            for idx in range(1, 11)
        ]
    ]

    record = asyncio.run(
        database.fuse_user_pets(
            1,
            list(range(1, 11)),
            result_pet_id=42,
            make_shiny=False,
            allow_huge=True,
            result_is_huge=None,
        )
    )

    assert bool(record["is_huge"]) is True
    # La deuxième requête INSERT INTO user_pets doit marquer le pet comme Huge.
    insert_call = connection.fetchrow_calls[1]
    assert insert_call[1][2] is True


def test_forge_prevented_without_permission() -> None:
    database = _FakeDatabase()
    connection = database._connection
    connection.fetchrow_results = [
        {"name": TITANIC_GRIFF_NAME},
    ]

    with pytest.raises(DatabaseError):
        asyncio.run(
            database.fuse_user_pets(
                1,
                list(range(1, 11)),
                result_pet_id=42,
                make_shiny=False,
                allow_huge=False,
                result_is_huge=None,
            )
        )

