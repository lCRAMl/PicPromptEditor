"""Threadsichere LRU-Caches.

Zwei Varianten, weil die beiden Anwendungsfaelle unterschiedlich begrenzt werden:

* LruCache begrenzt nach Anzahl (Prompts, Ergebnismengen).
* SizedLruCache begrenzt nach Speicherverbrauch (Vorschaubilder). Bei Bildern
  ist die Anzahl kein sinnvolles Mass - hundert 512er-Thumbnails belegen ein
  Vielfaches von hundert 128ern.

Beide sind threadsicher, weil aus GUI-Thread und Worker-Pools gleichzeitig
zugegriffen wird.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, Generic, Iterator, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class LruCache(Generic[K, V]):
    def __init__(self, capacity: int) -> None:
        self._capacity = max(0, capacity)
        self._data: OrderedDict[K, V] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: K) -> bool:
        with self._lock:
            return key in self._data

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            if key not in self._data:
                self.misses += 1
                return default
            self._data.move_to_end(key)
            self.hits += 1
            return self._data[key]

    def put(self, key: K, value: V) -> None:
        with self._lock:
            if self._capacity == 0:
                return
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def pop(self, key: K) -> V | None:
        with self._lock:
            return self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = 0
            self.misses = 0

    def set_capacity(self, capacity: int) -> None:
        with self._lock:
            self._capacity = max(0, capacity)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def keys(self) -> Iterator[K]:
        with self._lock:
            return iter(list(self._data.keys()))


class SizedLruCache(Generic[K, V]):
    """LRU mit Begrenzung nach Gesamtgroesse statt nach Anzahl."""

    def __init__(self, max_bytes: int, sizer: Callable[[V], int]) -> None:
        self._max_bytes = max(0, max_bytes)
        self._sizer = sizer
        self._data: OrderedDict[K, tuple[V, int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: K) -> bool:
        with self._lock:
            return key in self._data

    @property
    def current_bytes(self) -> int:
        with self._lock:
            return self._bytes

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return default
            self._data.move_to_end(key)
            self.hits += 1
            return entry[0]

    def put(self, key: K, value: V) -> None:
        with self._lock:
            if self._max_bytes == 0:
                return
            size = max(1, self._sizer(value))
            if size > self._max_bytes:
                # Ein einzelner Eintrag, der den gesamten Cache sprengt, wird
                # nicht aufgenommen - sonst raeumt er alles andere weg.
                return
            existing = self._data.pop(key, None)
            if existing is not None:
                self._bytes -= existing[1]
            self._data[key] = (value, size)
            self._bytes += size
            while self._bytes > self._max_bytes and self._data:
                _, evicted = self._data.popitem(last=False)
                self._bytes -= evicted[1]
                self.evictions += 1

    def pop(self, key: K) -> V | None:
        with self._lock:
            entry = self._data.pop(key, None)
            if entry is None:
                return None
            self._bytes -= entry[1]
            return entry[0]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._bytes = 0
            self.hits = 0
            self.misses = 0
            self.evictions = 0

    def set_max_bytes(self, max_bytes: int) -> None:
        with self._lock:
            self._max_bytes = max(0, max_bytes)
            while self._bytes > self._max_bytes and self._data:
                _, evicted = self._data.popitem(last=False)
                self._bytes -= evicted[1]
                self.evictions += 1

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def stats(self) -> dict[str, float | int]:
        with self._lock:
            return {
                "entries": len(self._data),
                "bytes": self._bytes,
                "max_bytes": self._max_bytes,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "hit_rate": round(self.hit_rate, 3),
            }
