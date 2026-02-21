import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgres://user:pass@localhost/db")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.clans import _is_clan_war_active
from database.db import Database


def test_clan_level_cost_growth_20_percent() -> None:
    assert Database.get_clan_next_level_cost(1) == 100_000
    assert Database.get_clan_next_level_cost(2) == 120_000
    assert Database.get_clan_next_level_cost(3) == 144_000


def test_clan_war_window_only_between_saturday_10h_and_sunday_20h_utc() -> None:
    assert _is_clan_war_active(datetime(2026, 1, 3, 9, 59)) is False
    assert _is_clan_war_active(datetime(2026, 1, 3, 10, 0)) is True
    assert _is_clan_war_active(datetime(2026, 1, 4, 19, 59)) is True
    assert _is_clan_war_active(datetime(2026, 1, 4, 20, 0)) is False
