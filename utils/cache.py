"""Cache TTL léger en mémoire pour les commandes intensives."""
from __future__ import annotations

import time
from typing import Generic, MutableMapping, Optional, Tuple, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Cache en mémoire avec expiration simple."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = max(0, int(ttl_seconds))
        self._data: MutableMapping[object, Tuple[float, T]] = {}

    def get(self, key: object) -> Optional[T]:
        if self._ttl <= 0:
            return None
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: object, value: T, *, ttl_seconds: int | None = None) -> None:
        ttl = self._ttl if ttl_seconds is None else max(0, int(ttl_seconds))
        if ttl <= 0:
            self._data.pop(key, None)
            return
        self._data[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        self._data.clear()
