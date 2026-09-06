"""Erreichbarkeitspruefung und Circuit Breaker fuer NAS-Pfade.

Spezifikation 08 fordert, dass die Anwendung bei nicht erreichbarem NAS
weiterarbeitet. Das Problem ist dabei nicht die Fehlerbehandlung, sondern die
Blockade: ein os.stat() auf eine tote SMB-Freigabe haengt je nach Konfiguration
zwanzig Sekunden und laenger. Laufen mehrere Jobs, stauen sie sich auf, und der
JobManager ist trotz korrekter Fehlerbehandlung tot.

Der Breaker kennt drei Zustaende:

    closed     alles normal, Zugriffe laufen durch
    open       Ziel gilt als tot, Zugriffe scheitern SOFORT ohne Systemaufruf
    half_open  nach Ablauf der Sperrzeit darf ein einzelner Versuch testen

Jede Dateisystemoperation fragt zuerst den Breaker. Suche, Vergleich, Analyse
und Evolution laufen unabhaengig davon weiter, weil sie ohnehin nur lokale
Daten brauchen.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from enum import Enum

from app.core.errors import ErrorReporter
from app.core.paths import long_path, normalize_path


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class TargetUnavailableError(OSError):
    """Zugriff abgelehnt, weil der Breaker offen ist. Kein Systemaufruf erfolgt."""


@dataclass
class _Target:
    root: str
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    last_ok: float = 0.0
    last_error: str = ""


class NasHealthMonitor:
    """Verwaltet je Wurzelverzeichnis einen Circuit Breaker."""

    def __init__(
        self,
        reporter: ErrorReporter,
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        probe_timeout_s: float = 3.0,
    ) -> None:
        self._reporter = reporter
        self._threshold = failure_threshold
        self._cooldown = cooldown_s
        self._probe_timeout = probe_timeout_s
        self._targets: dict[str, _Target] = {}
        self._lock = threading.RLock()

    # Zuordnung ----------------------------------------------------------------

    def register_root(self, path: str) -> None:
        key = normalize_path(path)
        with self._lock:
            self._targets.setdefault(key, _Target(root=path))

    def _target_for(self, path: str) -> _Target | None:
        key = normalize_path(path)
        with self._lock:
            best: _Target | None = None
            for root_key, target in self._targets.items():
                if key == root_key or key.startswith(root_key + "\\") or key.startswith(root_key + "/"):
                    if best is None or len(root_key) > len(normalize_path(best.root)):
                        best = target
            return best

    # Zustand ------------------------------------------------------------------

    def state_of(self, path: str) -> BreakerState:
        target = self._target_for(path)
        if target is None:
            return BreakerState.CLOSED
        with self._lock:
            if target.state is BreakerState.OPEN:
                if time.monotonic() - target.opened_at >= self._cooldown:
                    target.state = BreakerState.HALF_OPEN
            return target.state

    def is_available(self, path: str) -> bool:
        return self.state_of(path) is not BreakerState.OPEN

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return {t.root: t.state.value for t in self._targets.values()}

    # Rueckmeldung aus den Jobs ------------------------------------------------

    def record_success(self, path: str) -> None:
        target = self._target_for(path)
        if target is None:
            return
        with self._lock:
            was_down = target.state is not BreakerState.CLOSED
            target.state = BreakerState.CLOSED
            target.failures = 0
            target.last_ok = time.time()
            target.last_error = ""
        if was_down:
            self._reporter.info(
                "filesystem.nas",
                "Ziel wieder erreichbar: " + target.root,
                message_key="nas:recovered:" + normalize_path(target.root),
            )

    def record_failure(self, path: str, error: BaseException) -> None:
        target = self._target_for(path)
        if target is None:
            return
        with self._lock:
            target.failures += 1
            target.last_error = str(error)
            should_open = (
                target.state is not BreakerState.OPEN and target.failures >= self._threshold
            )
            if should_open:
                target.state = BreakerState.OPEN
                target.opened_at = time.monotonic()
        if should_open:
            self._reporter.warning(
                "filesystem.nas",
                "Ziel nicht erreichbar, Zugriffe werden ausgesetzt: " + target.root,
                message_key="nas:open:" + normalize_path(target.root),
                details=str(error),
            )

    # Gefuehrter Zugriff -------------------------------------------------------

    def guard(self, path: str) -> None:
        """Wirft TargetUnavailableError, wenn der Breaker offen ist."""
        if self.state_of(path) is BreakerState.OPEN:
            raise TargetUnavailableError("Ziel derzeit nicht erreichbar: " + str(path))

    def probe(self, path: str) -> bool:
        """Einzelner Erreichbarkeitstest. Aktualisiert den Breaker."""
        try:
            os.stat(long_path(path))
        except OSError as exc:
            self.record_failure(path, exc)
            return False
        self.record_success(path)
        return True
