"""Entscheidet, WANN ein Abgleich faellig ist - ohne Qt und ohne Dateizugriff.

Die Qt-Verdrahtung (QFileSystemWatcher, QTimer) liegt in
app/services/watch_service.py. Hier steht nur die Regel, und die ist testbar,
ohne eine Ereignisschleife laufen zu lassen.

Zwei Ausloeser, mit klarer Rangfolge:

* Der periodische Abgleich ist der PRIMAERMECHANISMUS. SMB-Freigaben melden
  Aenderungen, die andere Rechner vornehmen, grundsaetzlich nicht - ein Watcher
  allein wuerde solche Aenderungen nie sehen.
* Watcher-Ereignisse sind ein BESCHLEUNIGER fuer lokale Aenderungen. Sie werden
  entprellt gesammelt: waehrend ein Generator zweihundert Dateien schreibt,
  sollen nicht zweihundert Scans starten, sondern einer, wenn Ruhe eingekehrt
  ist.

Der Tracker kennt keine Zeitquelle. Jede Methode bekommt die aktuelle Zeit
uebergeben, damit Tests nicht warten muessen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SyncReason(str, Enum):
    WATCHER = "watcher"
    PERIODIC = "periodic"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class SyncRequest:
    reason: SyncReason
    paths: tuple[str, ...] = ()

    @property
    def is_targeted(self) -> bool:
        """Ein Watcher-Ereignis nennt betroffene Pfade, der Takt nicht."""
        return bool(self.paths)


@dataclass
class ChangeTracker:
    """Sammelt Aenderungsmeldungen und beantwortet: ist ein Abgleich faellig?"""

    debounce_seconds: float = 1.5
    periodic_seconds: float = 900.0
    watcher_enabled: bool = True

    _pending: set[str] = field(default_factory=set, init=False)
    _last_change: float | None = field(default=None, init=False)
    _last_sync: float | None = field(default=None, init=False)
    _suspended: bool = field(default=False, init=False)

    # Meldungen ----------------------------------------------------------------

    def note_change(self, path: str, now: float) -> None:
        """Eine Aenderung wurde gemeldet. Setzt das Sammelfenster zurueck.

        Zuruecksetzen statt starten: solange weitere Aenderungen eintreffen,
        wird gewartet. Sonst startet mitten in einem laufenden Schreibvorgang
        ein Scan, der halbe Dateien sieht.
        """
        if not self.watcher_enabled:
            return
        self._pending.add(path)
        self._last_change = now

    def note_sync_started(self, now: float) -> None:
        self._pending.clear()
        self._last_change = None
        self._last_sync = now
        self._suspended = True

    def note_sync_finished(self, now: float) -> None:
        self._last_sync = now
        self._suspended = False

    def suspend(self) -> None:
        """Waehrend ein Scan laeuft, wird nichts Neues faellig."""
        self._suspended = True

    def resume(self, now: float) -> None:
        self._suspended = False
        self._last_sync = now

    def reset(self, now: float) -> None:
        self._pending.clear()
        self._last_change = None
        self._last_sync = now
        self._suspended = False

    # Abfrage ------------------------------------------------------------------

    @property
    def pending_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._pending))

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def due(self, now: float) -> SyncRequest | None:
        """Der faellige Abgleich, oder None.

        Der Takt hat Vorrang vor den gesammelten Ereignissen: er deckt sie
        ohnehin mit ab, und ein Ereignis darf den Takt nicht endlos verschieben.
        """
        if self._suspended:
            return None

        if self._periodic_due(now):
            return SyncRequest(SyncReason.PERIODIC)

        if (
            self.watcher_enabled
            and self._pending
            and self._last_change is not None
            and now - self._last_change >= self.debounce_seconds
        ):
            return SyncRequest(SyncReason.WATCHER, self.pending_paths)

        return None

    def _periodic_due(self, now: float) -> bool:
        if self.periodic_seconds <= 0:
            return False
        if self._last_sync is None:
            return True
        return now - self._last_sync >= self.periodic_seconds

    def seconds_until_periodic(self, now: float) -> float:
        """Fuer die Statusanzeige: wie lange bis zum naechsten Takt."""
        if self.periodic_seconds <= 0:
            return float("inf")
        if self._last_sync is None:
            return 0.0
        return max(0.0, self.periodic_seconds - (now - self._last_sync))
