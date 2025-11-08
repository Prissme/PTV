"""Utility helpers for formatting user-facing values."""
from __future__ import annotations

__all__ = ["format_currency"]


def format_currency(amount: int) -> str:
    """Return a formatted currency string using non-breaking thousands separators."""

    return f"{amount:,} PB".replace(",", " ")
