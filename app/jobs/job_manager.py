"""Zentrale Jobverwaltung mit getrennten Pools.

Spezifikation 06 sieht einen einzigen QThreadPool mit Prioritaeten vor. Das kann
Interaktivitaet nicht garantieren: QThreadPool kennt zwar einen
Prioritaetsparameter, aber keine Praeemption. Belegt ein langer Analysejob alle
Worker, wartet die Suche, bis er fertig ist.

Deshalb drei getrennte Pools:

    interactive  Suche, Prompt laden, Bild laden        - immer sofort bedient
    io           NAS-Scan, Thumbnail-Erzeugung          - bewusst klein gehalten
    cpu          Parsing, Aehnlichkeit, Cluster         - darf den Rechner fuellen

Der io-Pool ist absichtlich schmal. Auf einer NAS-Freigabe ist der Durchsatz bei
vier parallelen Zugriffen hoeher als bei zweiunddreissig - der Engpass ist das
Netzwerk, nicht die CPU. Auf der Zielmaschine mit 32 Kernen wuerde ein globaler
Pool genau diesen Fehler machen.

Innerhalb eines Pools wirkt die Prioritaet von QThreadPool.start().
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QObject, QThreadPool, Qt, pyqtSignal

from app.core.errors import ErrorReporter
from app.jobs.job import Job, JobState


@dataclass
class PoolConfig:
    interactive: int = 3
    io: int = 4
    cpu: int = 0  # 0 = Kerne minus zwei

    def resolved_cpu(self) -> int:
        if self.cpu > 0:
            return self.cpu
        return max(2, (os.cpu_count() or 4) - 2)


class JobManager(QObject):
    """Startet Jobs, verfolgt ihren Zustand und meldet Sammelfortschritt.

    job_started(job_id, titel)
    job_progress(job_id, current, total, text)
    job_finished(job_id, zustand)
    activity_changed(anzahl_laufend, text)
    """

    job_started = pyqtSignal(int, str)
    job_progress = pyqtSignal(int, int, int, str)
    job_finished = pyqtSignal(int, str)
    activity_changed = pyqtSignal(int, str)

    def __init__(self, reporter: ErrorReporter, config: PoolConfig | None = None) -> None:
        super().__init__()
        self._reporter = reporter
        self._config = config or PoolConfig()
        self._lock = threading.RLock()
        self._active: dict[int, Job] = {}
        self._titles: dict[int, str] = {}
        self._groups: dict[str, set[int]] = {}
        self._completed = 0
        self._failed = 0

        self._pools = {
            "interactive": self._make_pool(self._config.interactive),
            "io": self._make_pool(self._config.io),
            "cpu": self._make_pool(self._config.resolved_cpu()),
            # Eigene Spur fuer die Erreichbarkeitspruefung. Sie lief frueher im
            # io-Pool - gemeinsam mit Abgleich und Vorschaubildern. Sind dessen
            # vier Faeden mit Zugriffen auf ein haengendes NAS belegt, kommt
            # ausgerechnet die Pruefung nicht mehr an die Reihe, die dieses
            # Haengen feststellen soll. Der Breaker waere genau dann blind,
            # wenn er gebraucht wird.
            "probe": self._make_pool(1),
        }

    @staticmethod
    def _make_pool(threads: int) -> QThreadPool:
        pool = QThreadPool()
        pool.setMaxThreadCount(max(1, threads))
        # Verhindert, dass Qt bei voller Warteschlange zusaetzliche Threads
        # ueber das Limit hinaus erzeugt.
        pool.setExpiryTimeout(30000)
        return pool

    # Konfiguration ------------------------------------------------------------

    def configure(self, config: PoolConfig) -> None:
        self._config = config
        self._pools["interactive"].setMaxThreadCount(max(1, config.interactive))
        self._pools["io"].setMaxThreadCount(max(1, config.io))
        self._pools["cpu"].setMaxThreadCount(max(1, config.resolved_cpu()))

    def pool_sizes(self) -> dict[str, int]:
        return {name: pool.maxThreadCount() for name, pool in self._pools.items()}

    # Starten ------------------------------------------------------------------

    def submit(self, job: Job, *, group: str = "", priority: int = 0) -> int:
        """Reiht einen Job in den zu ihm gehoerenden Pool ein.

        group erlaubt es, zusammengehoerende Jobs gemeinsam abzubrechen - etwa
        alle Thumbnail-Jobs eines Ordners, wenn der Benutzer weiterklickt.
        """
        pool = self._pools.get(job.pool)
        if pool is None:
            raise ValueError("Unbekannter Pool: " + str(job.pool))

        with self._lock:
            self._active[job.job_id] = job
            self._titles[job.job_id] = job.title
            if group:
                self._groups.setdefault(group, set()).add(job.job_id)

        job.signals.started.connect(self._on_started, Qt.ConnectionType.QueuedConnection)
        job.signals.progress.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        job.signals.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)

        pool.start(job, priority)
        self._emit_activity()
        return job.job_id

    # Abbrechen ----------------------------------------------------------------

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            job = self._active.get(job_id)
        if job is None:
            return False
        job.cancel()
        return True

    def cancel_group(self, group: str) -> int:
        with self._lock:
            ids = list(self._groups.get(group, ()))
        for job_id in ids:
            self.cancel(job_id)
        return len(ids)

    def cancel_all(self) -> int:
        with self._lock:
            jobs = list(self._active.values())
        for job in jobs:
            job.cancel()
        return len(jobs)

    # Zustand ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "active": len(self._active),
                "completed": self._completed,
                "failed": self._failed,
            }

    def wait_for_all(self, timeout_ms: int = 30000) -> bool:
        """Blockiert, bis alle Pools leer sind. Nur fuer Tests und Programmende."""
        return all(pool.waitForDone(timeout_ms) for pool in self._pools.values())

    def shutdown(self, timeout_ms: int = 5000) -> None:
        self.cancel_all()
        for pool in self._pools.values():
            pool.clear()
            pool.waitForDone(timeout_ms)

    # Signalannahme ------------------------------------------------------------

    def _on_started(self, job_id: int) -> None:
        self.job_started.emit(job_id, self._titles.get(job_id, ""))
        self._emit_activity()

    def _on_progress(self, job_id: int, current: int, total: int, text: str) -> None:
        self.job_progress.emit(job_id, current, total, text)

    def _on_finished(self, job_id: int, state: str) -> None:
        with self._lock:
            self._active.pop(job_id, None)
            title = self._titles.pop(job_id, "")
            for members in self._groups.values():
                members.discard(job_id)
            if state == JobState.FAILED.value:
                self._failed += 1
            elif state == JobState.DONE.value:
                self._completed += 1
        self.job_finished.emit(job_id, state)
        self._emit_activity()

    def _emit_activity(self) -> None:
        with self._lock:
            count = len(self._active)
            text = next(iter(self._titles.values()), "") if self._titles else ""
        self.activity_changed.emit(count, text)


def make_pool_config(get_setting: Callable[[str], int]) -> PoolConfig:
    """Baut die Poolkonfiguration aus den Einstellungen."""
    return PoolConfig(
        interactive=int(get_setting("jobs.interactive_threads")),
        io=int(get_setting("jobs.io_threads")),
        cpu=int(get_setting("jobs.cpu_threads")),
    )
