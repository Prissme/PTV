"""Utility helpers for formatting user-facing values."""
from __future__ import annotations

__all__ = ["format_currency", "format_gems"]


def format_currency(amount: int) -> str:
    """Return a formatted currency string using non-breaking thousands separators."""

    return f"{amount:,} PB".replace(",", " ")


def format_gems(amount: int) -> str:
    """Return a formatted gem string using non-breaking thousands separators."""

    return f"{amount:,} Gemmes".replace(",", " ")
