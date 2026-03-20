from random import Random

import pytest

from utils.brawl_maps import (
    MAP_ROTATION,
    MapDefinition,
    format_rotation_message,
    pick_unique_mode_maps,
)


def test_pick_unique_mode_maps_returns_three_distinct_modes() -> None:
    rng = Random(42)

    selection = pick_unique_mode_maps(rng=rng)

    assert len(selection) == 3
    modes = {item.mode for item in selection}
    assert len(modes) == 3
    # Ensure the selection uses entries from the configured rotation.
    for item in selection:
        payload = MAP_ROTATION[item.mode]
        assert item.map_name in payload["maps"]
        assert item.emoji == payload["emoji"]


def test_pick_unique_mode_maps_respects_deterministic_seed() -> None:
    rng = Random(0)

    selection = pick_unique_mode_maps(rng=rng)

    assert selection == (
        MapDefinition(
            mode="Braquage",
            map_name="Arrêt au stand",
            emoji="<:Heist:1436473730812481546>",
        ),
        MapDefinition(
            mode="Prime",
            map_name="Mille-feuille",
            emoji="<:Bounty:1436473727519948962>",
        ),
        MapDefinition(
            mode="Razzia de gemmes",
            map_name="Tunnel de mine",
            emoji="<:GemGrab:1436473738765008976>",
        ),
    )


def test_pick_unique_mode_maps_rejects_invalid_count() -> None:
    with pytest.raises(ValueError):
        pick_unique_mode_maps(count=0)

    with pytest.raises(ValueError):
        pick_unique_mode_maps(count=len(MAP_ROTATION) + 1)


def test_format_rotation_message_includes_all_entries() -> None:
    selection = (
        MapDefinition("Mode A", "Carte 1", "<:A:1>"),
        MapDefinition("Mode B", "Carte 2", "<:B:2>"),
    )

    message = format_rotation_message(selection)

    assert message == "<:A:1> **Mode A** — Carte 1\n<:B:2> **Mode B** — Carte 2"
