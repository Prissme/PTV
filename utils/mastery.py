"""Definitions et outils pour la gestion des maîtrises du bot."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Sequence, Tuple


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
        required = int(round(self.base_xp * (self.growth_factor ** exponent)))
        return max(1, required)


EGG_MASTERY_SLUG = "egg"
EGG_MASTERY = MasteryDefinition(
    slug=EGG_MASTERY_SLUG,
    display_name="Maîtrise des œufs",
    max_level=64,
    base_xp=15,
    growth_factor=1.22,
    broadcast_levels=(10, 30, 50, 64),
)

PET_MASTERY_SLUG = "pet"
PET_MASTERY = MasteryDefinition(
    slug=PET_MASTERY_SLUG,
    display_name="Maîtrise des pets",
    max_level=64,
    base_xp=20,
    growth_factor=1.24,
    broadcast_levels=(10, 30, 50, 64),
)

MASTERMIND_MASTERY_SLUG = "mastermind"
MASTERMIND_MASTERY = MasteryDefinition(
    slug=MASTERMIND_MASTERY_SLUG,
    display_name="Maîtrise Mastermind",
    max_level=64,
    base_xp=12,
    growth_factor=1.28,
    broadcast_levels=(10, 30, 50, 64),
)

@dataclass(frozen=True)
class MasteryPerk:
    """Description textuelle d'un bonus débloqué à un niveau donné."""

    level: int
    description: str


MASTERIES: Dict[str, MasteryDefinition] = {
    EGG_MASTERY.slug: EGG_MASTERY,
    PET_MASTERY.slug: PET_MASTERY,
    MASTERMIND_MASTERY.slug: MASTERMIND_MASTERY,
}


MASTERIES_PERKS: Dict[str, Tuple[MasteryPerk, ...]] = {
    EGG_MASTERY.slug: (
        MasteryPerk(5, "5% de chance d'obtenir un deuxième œuf gratuitement."),
        MasteryPerk(10, "3% de chance de pack un pet **Gold** directement."),
        MasteryPerk(
            20,
            "1% de chance de pet **Rainbow** et animations d'ouverture 2× plus rapides.",
        ),
        MasteryPerk(
            30,
            "15% de double œuf et 1% de triple ouverture sur chaque session.",
        ),
        MasteryPerk(
            40,
            "5% Gold, 2% Rainbow, 20% double œuf et 3% triple ouverture.",
        ),
        MasteryPerk(
            50,
            "10% Gold, 4% Rainbow, 35% double œuf et 10% triple ouverture.",
        ),
        MasteryPerk(64, "Rôle ultime : chance x2 permanente sur les ouvertures."),
    ),
    PET_MASTERY.slug: (
        MasteryPerk(
            5,
            "Auto goldify, 1% de shiny dans les œufs et accès à la fuse machine.",
        ),
        MasteryPerk(10, "10% de chance d'obtenir un pet bonus dans la fuse machine."),
        MasteryPerk(
            20,
            "3% de shiny avec goldify et 1% supplémentaire avec rainbowify.",
        ),
        MasteryPerk(30, "Auto rainbowify et 3% de shiny dans les œufs."),
        MasteryPerk(40, "35% double et 10% triple dans la fuse machine."),
        MasteryPerk(
            50,
            "50% double, 5% de shiny dans les œufs et boosts shiny sur goldify/rainbowify.",
        ),
        MasteryPerk(
            64,
            "Rôle ultime : chance Gold x1,5, Shiny x1,2 et Rainbow x1,3 permanente.",
        ),
    ),
    MASTERMIND_MASTERY.slug: (
        MasteryPerk(5, "Récompenses en PB doublées (x2)."),
        MasteryPerk(10, "Récompenses x8 au total et loot luck x2."),
        MasteryPerk(20, "Récompenses x16 et suppression d'une couleur dans le code."),
        MasteryPerk(30, "Chance doublée d'obtenir Kenji Oni."),
        MasteryPerk(40, "Récompenses x64 au total."),
        MasteryPerk(50, "Récompenses x256 au total."),
        MasteryPerk(64, "Rôle ultime : deux couleurs en moins en permanence."),
    ),
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


def get_mastery_perks(slug: str) -> Sequence[MasteryPerk]:
    """Retourne la liste des perks définis pour une maîtrise donnée."""

    return MASTERIES_PERKS.get(slug, ())

