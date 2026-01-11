import os
import sys
from pathlib import Path

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgres://user:pass@localhost/db")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import MARKET_VALUE_CONFIG, rebase_gems_amount, scale_pet_value
from database.db import Database


def test_scale_pet_value_keeps_income_scale() -> None:
    assert scale_pet_value(1_000) == 1_000


def test_rebase_gems_amount_divides_by_factor() -> None:
    assert rebase_gems_amount(20_000_000_000) == 2_000_000


def test_market_value_respects_rarity_cap() -> None:
    pet = {
        "name": "Test Rare",
        "rarity": "Rare",
        "base_income_per_hour": 10_000_000_000,
        "is_huge": False,
    }
    value = Database.compute_market_value_gems(
        pet,
        config=MARKET_VALUE_CONFIG,
        zone_slug="mexico",
        variant_multiplier=25.0,
    )
    cap = int(MARKET_VALUE_CONFIG["rarity_cap"]["Rare"])
    assert value <= cap
