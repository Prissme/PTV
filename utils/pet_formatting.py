"""Shared formatting helpers for pet-related displays."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, MutableMapping

from config import (
    GALAXY_PET_MULTIPLIER,
    GOLD_PET_MULTIPLIER,
    PET_EMOJIS,
    RAINBOW_PET_MULTIPLIER,
    SHINY_PET_MULTIPLIER,
)

from .formatting import format_currency

__all__ = ["PetDisplay", "pet_emoji"]


def pet_emoji(name: str) -> str:
    """Return the emoji configured for the provided pet name."""

    return PET_EMOJIS.get(name, PET_EMOJIS.get("default", "🐾"))


def _as_int(value: object | None, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_bool(value: object | None) -> bool:
    return bool(value)


@dataclass(frozen=True)
class PetDisplay:
    """Representation of a pet tailored for embeds and textual output."""

    name: str
    rarity: str
    income_per_hour: int
    is_huge: bool = False
    is_gold: bool = False
    is_galaxy: bool = False
    is_rainbow: bool = False
    is_shiny: bool = False
    market_value: int = 0
    is_active: bool = False
    huge_level: int | None = None
    identifier: int | None = None
    bonus: bool = False
    forced: bool = False
    image_url: str | None = None
    acquired_at: datetime | None = None

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "PetDisplay":
        """Build a :class:`PetDisplay` from a mapping returned by the database."""

        income = _as_int(
            mapping.get("income_per_hour")
            or mapping.get("income")
            or mapping.get("base_income_per_hour")
        )
        identifier: int | None = None
        for key in ("id", "user_pet_id", "pet_id"):
            raw_value = mapping.get(key)
            if raw_value is None:
                continue
            identifier = _as_int(raw_value)
            if identifier:
                break
        huge_level_value = mapping.get("huge_level")
        huge_level = _as_int(huge_level_value) if huge_level_value is not None else None
        raw_image = mapping.get("image_url") or mapping.get("image")
        image_url = str(raw_image) if raw_image else None
        acquired_at = mapping.get("acquired_at")
        if not isinstance(acquired_at, datetime):
            acquired_at = None

        return cls(
            name=str(mapping.get("name", "Pet")),
            rarity=str(mapping.get("rarity", "?")),
            income_per_hour=income,
            is_huge=_as_bool(mapping.get("is_huge")),
            is_gold=_as_bool(mapping.get("is_gold")),
            is_galaxy=_as_bool(mapping.get("is_galaxy")),
            is_rainbow=_as_bool(mapping.get("is_rainbow")),
            is_shiny=_as_bool(mapping.get("is_shiny")),
            market_value=_as_int(mapping.get("market_value")),
            is_active=_as_bool(mapping.get("is_active")),
            huge_level=huge_level,
            identifier=identifier,
            bonus=_as_bool(mapping.get("bonus")),
            forced=_as_bool(mapping.get("forced")),
            image_url=image_url,
            acquired_at=acquired_at,
        )

    @property
    def emoji(self) -> str:
        return pet_emoji(self.name)

    @property
    def income_text(self) -> str:
        return f"{self.income_per_hour:,} PB/h".replace(",", " ")

    def rarity_label(self) -> str:
        if self.is_galaxy:
            return f"{self.rarity} Galaxy"
        if self.is_rainbow:
            return f"{self.rarity} Rainbow"
        if self.is_gold:
            return f"{self.rarity} Or"
        if self.is_shiny:
            return f"{self.rarity} Shiny"
        return self.rarity

    def display_name(self) -> str:
        markers: list[str] = []
        if self.is_galaxy:
            markers.append("🌌")
        elif self.is_rainbow:
            markers.append("🌈")
        elif self.is_gold:
            markers.append("🥇")
        if self.is_shiny:
            markers.append("✨")
        marker_suffix = f" {' '.join(markers)}" if markers else ""
        return f"{self.emoji} {self.name}{marker_suffix}".strip()

    def title(self) -> str:
        base = f"{self.display_name()} ({self.rarity_label()})"
        if self.is_huge:
            return f"✨ {base} ✨"
        return base

    def reveal_lines(self) -> list[str]:
        lines = [f"Revenus passifs : **{self.income_text}**"]
        if self.is_huge:
            lines.append(f"🎉 Incroyable ! Tu as obtenu **{self.name}** ! 🎉")
        if self.is_galaxy:
            lines.append(f"🌌 Variante galaxy ! Puissance x{GALAXY_PET_MULTIPLIER}.")
        elif self.is_rainbow:
            lines.append(f"🌈 Variante rainbow ! Puissance x{RAINBOW_PET_MULTIPLIER}.")
        elif self.is_gold:
            lines.append(f"🥇 Variante or ! Puissance x{GOLD_PET_MULTIPLIER}.")
        if self.is_shiny:
            lines.append(f"✨ Variante shiny ! Puissance x{SHINY_PET_MULTIPLIER}.")
        if self.market_value > 0 and not self.is_huge:
            lines.append(f"Valeur marché : **{format_currency(self.market_value)}**")
        return lines

    def multi_reveal_field(self) -> tuple[str, str]:
        field_name = f"{self.emoji} {self.name}"
        lines = [
            f"Rareté : **{self.rarity}**",
            f"Revenus : **{self.income_text}**",
        ]
        flags: list[str] = []
        if self.is_huge:
            flags.append("Huge")
        if self.is_galaxy:
            flags.append("Galaxy")
        elif self.is_rainbow:
            flags.append("Rainbow")
        elif self.is_gold:
            flags.append("Gold")
        if self.is_shiny:
            flags.append("Shiny")
        if self.bonus:
            flags.append("Bonus")
        if self.forced:
            flags.append("Gold garanti")
        if flags:
            lines.append(" · ".join(flags))
        if self.market_value > 0 and not self.is_huge:
            lines.append(f"Valeur : {format_currency(self.market_value)}")
        return field_name, "\n".join(lines)

    def collection_line(self) -> str:
        parts: list[str] = []
        if self.identifier:
            parts.append(f"#{self.identifier}")
        if self.is_active:
            parts.append("⭐")
        parts.extend([self.emoji, self.name, self.rarity, self.income_text])
        flags: list[str] = []
        if self.is_huge and self.huge_level:
            flags.append(f"Niv. {self.huge_level}")
        elif self.is_huge:
            flags.append("Huge")
        if self.is_galaxy:
            flags.append("Galaxy")
        elif self.is_rainbow:
            flags.append("Rainbow")
        elif self.is_gold:
            flags.append("Gold")
        if self.is_shiny:
            flags.append("Shiny")
        if flags:
            parts.append(" ".join(flags))
        return " ".join(part for part in parts if part).replace("  ", " ")

    def equipment_lines(self, activated: bool, active_count: int, slot_limit: int) -> list[str]:
        lines = [
            "Ce pet génère désormais des revenus passifs !"
            if activated
            else "Ce pet se repose pour le moment.",
            f"Rareté : {self.rarity}",
            f"Revenus : {self.income_text}",
        ]
        if self.is_rainbow:
            lines.append(f"Variante rainbow : puissance x{RAINBOW_PET_MULTIPLIER}")
        elif self.is_gold:
            lines.append(f"Variante or : puissance x{GOLD_PET_MULTIPLIER}")
        if self.market_value > 0 and not self.is_huge:
            lines.append(f"Valeur marché : {format_currency(self.market_value)}")
        lines.append(f"Pets actifs : **{active_count}/{slot_limit}**")
        return lines

    def claim_line(self, share: int) -> str:
        share_text = f"+{format_currency(share)}" if share > 0 else "0 PB"
        parts = [self.emoji, self.name, self.income_text, share_text]
        tags: list[str] = []
        if self.is_active:
            tags.append("Actif")
        if self.is_huge and self.huge_level:
            tags.append(f"Niv. {self.huge_level}")
        elif self.is_huge:
            tags.append("Huge")
        if self.is_rainbow:
            tags.append("Rainbow")
        elif self.is_gold:
            tags.append("Gold")
        if self.is_shiny:
            tags.append("Shiny")
        if tags:
            parts.append(" ".join(tags))
        if self.acquired_at:
            parts.append(f"Obtenu le {self.acquired_at.strftime('%d/%m/%Y')}")
        return " ".join(part for part in parts if part).replace("  ", " ")

    def to_mutable_mapping(self) -> MutableMapping[str, object]:
        """Return a mutable representation useful for serialization if needed."""

        return {
            "name": self.name,
            "rarity": self.rarity,
            "income_per_hour": self.income_per_hour,
            "is_huge": self.is_huge,
            "is_gold": self.is_gold,
            "is_galaxy": self.is_galaxy,
            "is_rainbow": self.is_rainbow,
            "is_shiny": self.is_shiny,
            "market_value": self.market_value,
            "is_active": self.is_active,
            "huge_level": self.huge_level,
            "identifier": self.identifier,
            "bonus": self.bonus,
            "forced": self.forced,
            "image_url": self.image_url,
        }
