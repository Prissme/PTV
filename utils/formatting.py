"""Utility helpers for formatting user-facing values."""
from __future__ import annotations

__all__ = ["format_currency", "format_gems"]

_SUFFIXES: tuple[str, ...] = ("", "K", "M", "B", "T", "q", "Q", "s", "S", "O", "N", "D")


def _format_compact(amount: int) -> str:
    value = float(amount)
    index = 0
    while abs(value) >= 1000 and index < len(_SUFFIXES) - 1:
        value /= 1000.0
        index += 1

    if index == 0:
        return f"{int(value):,}".replace(",", " ")

    abs_value = abs(value)
    if abs_value >= 100:
        decimals = 0
    elif abs_value >= 10:
        decimals = 1
    else:
        decimals = 2
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return f"{text}{_SUFFIXES[index]}"


def format_currency(amount: int) -> str:
    """Return a formatted currency string using US short scale suffixes."""

    return f"{_format_compact(amount)} PB"


def format_gems(amount: int) -> str:
    """Return a formatted gem string using US short scale suffixes."""

    return f"{_format_compact(amount)} Gemmes"
