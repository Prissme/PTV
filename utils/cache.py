"""Cache TTL léger en mémoire pour les commandes intensives."""
from __future__ import annotations

import time
from collections import OrderedDict
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


class LruTTLCache(Generic[T]):
    """Cache TTL avec éviction LRU et taille maximale."""

    def __init__(self, ttl_seconds: int, max_entries: int) -> None:
        self._ttl = max(0, int(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._data: "OrderedDict[object, Tuple[float, T]]" = OrderedDict()

    def _evict_expired(self) -> None:
        if self._ttl <= 0:
            self._data.clear()
            return
        now = time.monotonic()
        expired_keys = [key for key, (expires_at, _value) in self._data.items() if expires_at < now]
        for key in expired_keys:
            self._data.pop(key, None)

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
        self._data.move_to_end(key)
        return value

    def set(self, key: object, value: T, *, ttl_seconds: int | None = None) -> None:
        ttl = self._ttl if ttl_seconds is None else max(0, int(ttl_seconds))
        if ttl <= 0:
            self._data.pop(key, None)
            return
        self._data[key] = (time.monotonic() + ttl, value)
        self._data.move_to_end(key)
        self._evict_expired()
        while len(self._data) > self._max_entries:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
