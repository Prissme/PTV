from utils.formatting import format_currency
from utils.pet_formatting import PetDisplay


def test_collection_line_omits_identifier_for_single_pet() -> None:
    display = PetDisplay(
        name="Griff",
        rarity="Rare",
        income_per_hour=1_000,
        identifier=42,
    )

    line = display.collection_line(quantity=1, identifiers=[42])

    assert "#" not in line
    assert display.name not in line


def test_collection_line_displays_quantity_suffix() -> None:
    display = PetDisplay(
        name="Dragon",
        rarity="Mythique",
        income_per_hour=5_000,
    )

    line = display.collection_line(quantity=3, identifiers=[10, 11, 12])

    assert "x3" in line
    assert display.name not in line


def test_collection_line_uses_formatted_income_suffix() -> None:
    display = PetDisplay(
        name="Phoenix",
        rarity="Légendaire",
        income_per_hour=9_000,
    )

    line = display.collection_line(quantity=1)

    formatted = f"{format_currency(display.income_per_hour)}/h"
    assert formatted in line
