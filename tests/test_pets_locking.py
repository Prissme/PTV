import asyncio
from types import SimpleNamespace

from cogs.pets import Pets


def test_get_open_lock_is_reused_for_user() -> None:
    bot = SimpleNamespace(database=SimpleNamespace())
    cog = Pets(bot)

    lock_a = cog._get_open_lock(42)
    lock_b = cog._get_open_lock(42)

    assert isinstance(lock_a, asyncio.Lock)
    assert lock_a is lock_b
