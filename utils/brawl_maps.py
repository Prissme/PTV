"""Utility helpers to handle Brawl Stars map rotations for lobbies.

This module centralises the map pool configuration shared by the bot and
provides a deterministic helper used when a lobby reaches its maximum
capacity.  The selector always returns maps coming from distinct game modes,
avoiding repetitions within the same rotation.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True)
class MapDefinition:
    """Describe a single playable map in the rotation."""

    mode: str
    map_name: str
    emoji: str


#: Canonical configuration for the available game modes and their maps.
MAP_ROTATION: Mapping[str, Mapping[str, Sequence[str]]] = {
    "Razzia de gemmes": {
        "emoji": "<:GemGrab:1436473738765008976>",
        "maps": ("Mine hard-rock", "Tunnel de mine", "Bruissements"),
    },
    "Brawlball": {
        "emoji": "<:Brawlball:1436473735573143562>",
        "maps": ("Tir au buts", "Super plage", "Triple Dribble"),
    },
    "Hors-jeu": {
        "emoji": "<:KnockOut:1436473703083937914>",
        "maps": ("Rocher de la belle", "Ravin du bras d'or", "À découvert"),
    },
    "Braquage": {
        "emoji": "<:Heist:1436473730812481546>",
        "maps": ("C'est chaud patate", "Arrêt au stand", "Zone sécurisée"),
    },
    "Zone réservée": {
        "emoji": "<:HotZone:1436473698491175137>",
        "maps": ("Duel de scarabées", "Cercle de feu", "Stratégies parallèles"),
    },
    "Prime": {
        "emoji": "<:Bounty:1436473727519948962>",
        "maps": ("Cachette secrète", "Étoile filante", "Mille-feuille"),
    },
}


def _normalise_count(count: int, available: int) -> int:
    """Bound the requested map count to the available number of modes."""

    if count <= 0:
        raise ValueError("count must be strictly positive")
    if available <= 0:
        raise ValueError("no map modes configured")
    if count > available:
        raise ValueError("requested map count exceeds the number of modes")
    return count


def pick_unique_mode_maps(
    *, count: int = 3, rng: random.Random | None = None
) -> tuple[MapDefinition, ...]:
    """Return ``count`` maps with distinct game modes.

    Parameters
    ----------
    count:
        Number of maps to select. Must be less than or equal to the number of
        configured modes. Defaults to ``3`` as required by the lobby rules.
    rng:
        Optional :class:`random.Random` instance to allow deterministic tests
        or reproducible seeds.

    Raises
    ------
    ValueError
        If ``count`` is not positive or larger than the number of modes, or if
        the configuration does not define any mode.
    """

    available_modes = list(MAP_ROTATION.items())
    normalised = _normalise_count(count, len(available_modes))
    random_source = rng or random

    # Pick distinct modes first and then select a map within each mode.
    selected_modes = random_source.sample(available_modes, k=normalised)
    selection: list[MapDefinition] = []
    for mode, payload in selected_modes:
        maps = tuple(payload.get("maps", ()))
        emoji = str(payload.get("emoji", ""))
        if not maps:
            raise ValueError(f"mode '{mode}' has no maps configured")
        chosen = random_source.choice(maps)
        selection.append(MapDefinition(mode=mode, map_name=chosen, emoji=emoji))

    return tuple(selection)


def format_rotation_message(selection: Iterable[MapDefinition]) -> str:
    """Format a human-readable sentence announcing the selected maps."""

    formatted_chunks: list[str] = []
    for item in selection:
        chunk = f"{item.emoji} **{item.mode}** — {item.map_name}"
        formatted_chunks.append(chunk)
    return "\n".join(formatted_chunks)
