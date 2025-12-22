"""Définitions et helpers pour les nouveaux enchantements."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Final, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class EnchantmentDefinition:
    slug: str
    name: str
    description: str
    effect_hint: str


ENCHANTMENT_DEFINITIONS: Final[tuple[EnchantmentDefinition, ...]] = (
    EnchantmentDefinition(
        "egg_luck",
        "Enchantement Chance d'œuf",
        "Augmente la probabilité de drop rare lors de l'ouverture d'œufs.",
        "Augmente directement le bonus de luck des œufs.",
    ),
    EnchantmentDefinition(
        "prissbucks",
        "Enchantement Générateur de PB",
        "Boost permanent sur les gains passifs des pets.",
        "Ajoute un multiplicateur aux revenus PrissBucks.",
    ),
    EnchantmentDefinition(
        "slots_luck",
        "Enchantement Chance casino",
        "Altère les tirages de la machine à sous en ta faveur.",
        "Augmente les multiplicateurs et peut sauver un tirage perdant.",
    ),
    EnchantmentDefinition(
        "koth_luck",
        "Enchantement Roi de la Colline",
        "Renforce tes chances de gagner les drops exclusifs du KOTH.",
        "Réduit le dénominateur de chance pour les récompenses KOTH.",
    ),
)

ENCHANTMENT_DEFINITION_MAP: Final[dict[str, EnchantmentDefinition]] = {
    enchantment.slug: enchantment for enchantment in ENCHANTMENT_DEFINITIONS
}

ENCHANTMENT_EMOJIS: Final[dict[str, str]] = {
    "egg_luck": "<:EggLuckEnchant:1438599159706812568>",
    "koth_luck": "<:KothLuckEnchant:1438600557052039269>",
    "prissbucks": "<:PrissBucksEnchant:1438599806892245165>",
    "slots_luck": "<:SlotsLuckEnchant:1438600197780537424>",
}

ENCHANTMENT_SELL_PRICES: Final[dict[int, int]] = {
    1: 100,
    2: 200,
    3: 400,
    4: 800,
    5: 1_600,
    6: 3_200,
    7: 6_400,
    8: 12_800,
    9: 25_160,
    10: 50_320,
}

# Drop rates pour chaque activité. Ces valeurs restent faibles pour préserver la rareté.
ENCHANTMENT_SOURCE_DROP: Final[dict[str, float]] = {
    "distributor": 0.22,
    "mastermind": 0.18,
    "race": 0.35,
}

ENCHANTMENT_SOURCE_LABELS: Final[dict[str, str]] = {
    "distributor": "Distributeur de Mexico",
    "mastermind": "Mastermind",
    "race": "Millionaire Race",
}


def iter_enchantments() -> Iterable[EnchantmentDefinition]:
    return ENCHANTMENT_DEFINITIONS


def get_enchantment_emoji(slug: str) -> str:
    return ENCHANTMENT_EMOJIS.get(slug, "✨")


def format_enchantment(definition: EnchantmentDefinition, power: int) -> str:
    return f"{definition.name} (puissance {power})"


def roll_enchantment_power() -> int:
    """Retourne un niveau entre 1 et 10 en pondérant exponentiellement la rareté."""

    levels = list(range(1, 11))
    # Poids exponentiels : les niveaux 1 pèsent 2**9 tandis que les niveaux 10 n'ont qu'un poids de 1.
    weights = [2 ** (10 - level) for level in levels]
    return random.choices(levels, weights=weights, k=1)[0]


def pick_random_enchantment() -> EnchantmentDefinition:
    return random.choice(ENCHANTMENT_DEFINITIONS)


def should_drop_enchantment(source: str) -> bool:
    rate = ENCHANTMENT_SOURCE_DROP.get(source, 0.0)
    return rate > 0 and random.random() <= rate


def get_enchantment_sell_price(power: int) -> int | None:
    return ENCHANTMENT_SELL_PRICES.get(power)


def get_source_label(source: str) -> str:
    return ENCHANTMENT_SOURCE_LABELS.get(source, source.title())


def compute_egg_luck_bonus(power: int) -> float:
    """Bonus appliqué au luck des œufs (5 niveaux ~ +10%)."""

    return max(0.0, min(0.5, 0.02 * max(0, power)))


def compute_prissbucks_multiplier(power: int) -> float:
    """Multiplicateur appliqué au revenu des pets."""

    return 1.0 + min(0.60, 0.03 * max(0, power))


def compute_slots_multiplier(power: int) -> float:
    """Bonus appliqué aux gains slots quand il y a une combinaison gagnante."""

    return 1.0 + min(0.75, 0.05 * max(0, power))


def compute_koth_bonus_factor(power: int) -> float:
    """Retourne un multiplicateur appliqué sur la chance KOTH (>=1)."""

    return 1.0 + min(1.5, 0.08 * max(0, power))


def summarize_enchantments(rows: Sequence[Mapping[str, object]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        slug = str(row.get("slug") or "")
        power = int(row.get("power") or 0)
        quantity = int(row.get("quantity") or 0)
        if not slug or power <= 0 or quantity <= 0:
            continue
        current = summary.get(slug, 0)
        summary[slug] = max(current, power)
    return summary
