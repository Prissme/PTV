"""Definitions et outils pour la gestion des maîtrises du bot."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


@dataclass(frozen=True)
class MasteryDefinition:
    """Représente une maîtrise disponible pour les utilisateurs."""

    slug: str
    display_name: str
    max_level: int
    base_xp: int
    growth_factor: float
    broadcast_levels: Tuple[int, ...] = ()

    def required_xp(self, level: int) -> int:
        """Retourne l'expérience nécessaire pour atteindre le niveau suivant."""

        if level >= self.max_level:
            return 0

        exponent = max(0, level - 1)
        required = int(round(self.base_xp * (self.growth_factor ** exponent) / 3))
        return max(1, required)


EGG_MASTERY_SLUG = "egg"
EGG_MASTERY = MasteryDefinition(
    slug=EGG_MASTERY_SLUG,
    display_name="Maîtrise des œufs",
    max_level=99,
    base_xp=18,
    growth_factor=1.3,
    broadcast_levels=(10, 30, 50, 75, 90, 99),
)

PET_MASTERY_SLUG = "pet"
PET_MASTERY = MasteryDefinition(
    slug=PET_MASTERY_SLUG,
    display_name="Maîtrise des pets",
    max_level=99,
    base_xp=22,
    growth_factor=1.33,
    broadcast_levels=(10, 30, 50, 75, 90, 99),
)

MASTERMIND_MASTERY_SLUG = "mastermind"
MASTERMIND_MASTERY = MasteryDefinition(
    slug=MASTERMIND_MASTERY_SLUG,
    display_name="Maîtrise du Mastermind",
    max_level=99,
    base_xp=10,
    growth_factor=1.34,
    broadcast_levels=(10, 30, 50, 75, 90, 99),
)

MASTERIES: Dict[str, MasteryDefinition] = {
    EGG_MASTERY.slug: EGG_MASTERY,
    PET_MASTERY.slug: PET_MASTERY,
    MASTERMIND_MASTERY.slug: MASTERMIND_MASTERY,
}


def get_mastery_definition(slug: str) -> MasteryDefinition:
    """Récupère la définition d'une maîtrise ou lève une erreur."""

    try:
        return MASTERIES[slug]
    except KeyError as exc:  # pragma: no cover - entrée développeur invalide
        raise KeyError(f"Mastery '{slug}' is not defined") from exc


def iter_masteries() -> Iterable[MasteryDefinition]:
    """Itère sur toutes les maîtrises déclarées."""

    return MASTERIES.values()
