"""Utility helpers for managing EcoBot user languages."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "LANGUAGE_NAMES",
    "normalize_language",
    "is_supported_language",
    "HelpLocaleStrings",
]

DEFAULT_LANGUAGE = "fr"
SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"fr", "en"})
LANGUAGE_NAMES: dict[str, str] = {
    "fr": "Français",
    "en": "English",
}


def normalize_language(value: str | None) -> str:
    """Normalize the provided value into a supported language code."""

    if not value:
        return DEFAULT_LANGUAGE

    lowered = value.lower()
    if lowered in SUPPORTED_LANGUAGES:
        return lowered

    # Accept common language prefixes ("fr-FR", "en_GB", etc.).
    for language in SUPPORTED_LANGUAGES:
        if lowered.startswith(f"{language}-") or lowered.startswith(f"{language}_"):
            return language

    if lowered.startswith("fr"):
        return "fr"
    if lowered.startswith("en"):
        return "en"

    return DEFAULT_LANGUAGE


def is_supported_language(value: str) -> bool:
    """Return ``True`` if the provided value is a supported language code."""

    return normalize_language(value) == value


@dataclass(frozen=True)
class HelpLocaleStrings:
    """Localized copy used by the help command embeds and menus."""

    menu_placeholder: str
    all_option_label: str
    all_option_description: str
    interaction_denied: str
    all_embed_title: str
    all_embed_description: str
    footer_text: str
    section_embed_title_format: str
    command_not_found_title: str
    command_not_found_body: str
    suggestions_heading: str
    command_detail_title_format: str
    category_field_name: str
    usage_field_name: str
    aliases_field_name: str


def ensure_languages_defined(languages: Iterable[str]) -> None:
    """Validation helper ensuring language constants remain in sync.

    This function is currently unused at runtime but offers a convenient
    assertion point for tests or future sanity checks.
    """

    missing = [language for language in languages if language not in SUPPORTED_LANGUAGES]
    if missing:
        raise ValueError(f"Unsupported languages declared: {', '.join(sorted(missing))}")
