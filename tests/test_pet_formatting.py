from utils.pet_formatting import PetDisplay


def test_collection_line_displays_identifier_for_single_pet() -> None:
    display = PetDisplay(
        name="Griff",
        rarity="Rare",
        income_per_hour=1_000,
        identifier=42,
    )

    line = display.collection_line(quantity=1, identifiers=[42])

    assert "#42" in line


def test_collection_line_lists_identifiers_when_grouped() -> None:
    display = PetDisplay(
        name="Dragon",
        rarity="Mythique",
        income_per_hour=5_000,
    )

    line = display.collection_line(quantity=3, identifiers=[10, 11, 12])

    assert "IDs : #10, #11, #12" in line
    assert "x3" in line


def test_collection_line_collapses_identifier_overflow() -> None:
    display = PetDisplay(
        name="Phoenix",
        rarity="Légendaire",
        income_per_hour=9_000,
    )

    identifiers = [1, 2, 3, 4, 5, 6, 7]
    line = display.collection_line(quantity=len(identifiers), identifiers=identifiers)

    assert "IDs : #1, #2, #3, #4, #5, +2" in line
