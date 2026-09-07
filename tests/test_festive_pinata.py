import os
import sys
import asyncio
from pathlib import Path

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "postgres://user:pass@localhost/db")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.event_anniversaire import PINATA_FESTIVE_EGG_LUCK_PER_UPGRADE
from cogs.event_pinata import PinataShopView, _upgrade_cost
from config import FESTIVE_EGG_DEFINITION, TITANIC_SMOOTH_LOU_NAME, get_huge_multiplier


def test_festive_egg_includes_titanic_smooth_lou_at_requested_base_rate() -> None:
    smooth_lou = next(
        pet for pet in FESTIVE_EGG_DEFINITION.pets if pet.name == TITANIC_SMOOTH_LOU_NAME
    )

    assert smooth_lou.drop_rate == 1 / 175_000_000
    assert smooth_lou.is_huge
    assert get_huge_multiplier(smooth_lou.name) == 150


def test_pinata_upgrades_have_a_small_festive_egg_luck_bonus() -> None:
    assert PINATA_FESTIVE_EGG_LUCK_PER_UPGRADE == 0.001


async def _shop_button_labels() -> list[str | None]:
    view = PinataShopView(cog=None, ctx=None)  # type: ignore[arg-type]
    return [button.label for button in view.children]


def test_pinata_shop_exposes_one_button_per_upgrade() -> None:
    labels = asyncio.run(_shop_button_labels())
    assert labels == [
        "Cooldown",
        "Chance",
        "Production",
    ]


def test_pinata_upgrade_costs_follow_a_steep_curve() -> None:
    assert _upgrade_cost("cooldown", 1) > _upgrade_cost("cooldown", 0) * 1.7
    assert _upgrade_cost("cash", 10) > _upgrade_cost("cash", 0) * 9
