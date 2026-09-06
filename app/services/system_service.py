"""SystemService - Systemzustand fuer Statusleiste und Problemliste."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.core.errors import AppError, ErrorReporter, Severity
from app.database.connection import Database
from app.database.writer import DbWriter
from app.filesystem.nas_health import NasHealthMonitor
from app.jobs.job_manager import JobManager
from app.dto.models import ErrorEntry
from app.repositories.error_repo import ErrorRepository
from app.repositories.stats_repo import StatsRepository


@dataclass(frozen=True)
class SystemStatus:
    nas: dict[str, str]
    schema_version: int
    pending_writes: int
    active_jobs: int
    warnings: int
    errors: int
    image_count: int
    prompt_count: int


class SystemService(QObject):
    """status_changed(SystemStatus), problem_raised(AppError)."""

    status_changed = pyqtSignal(object)
    problem_raised = pyqtSignal(object)

    def __init__(
        self,
        database: Database,
        writer: DbWriter,
        errors: ErrorRepository,
        stats: StatsRepository,
        health: NasHealthMonitor,
        jobs: JobManager,
        reporter: ErrorReporter,
        *,
        poll_interval_ms: int = 2000,
    ) -> None:
        super().__init__()
        self._db = database
        self._writer = writer
        self._errors = errors
        self._stats = stats
        self._health = health
        self._jobs = jobs
        self._reporter = reporter

        # Die Bruecke vom Qt-freien ErrorReporter zu Qt-Signalen. Sie existiert
        # genau hier und nirgends sonst, damit core und analysis ohne
        # QApplication testbar bleiben.
        reporter.add_sink(self)

        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._publish)

    def start(self) -> None:
        self._publish()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._reporter.remove_sink(self)

    def refresh(self) -> None:
        """Sofortige Aktualisierung ausserhalb des Abfragetakts.

        Nach einem Abgleich sollen Bild- und Promptzahl unmittelbar stimmen und
        nicht bis zum naechsten Takt veraltet bleiben.
        """
        self._publish()

    # ErrorSink ----------------------------------------------------------------

    def handle(self, error: AppError) -> None:
        """Wird aus beliebigen Threads aufgerufen.

        pyqtSignal.emit ist threadsicher; die Auslieferung an im GUI-Thread
        verbundene Empfaenger erfolgt ueber die Ereignisschleife.
        """
        if error.severity >= Severity.WARNING:
            self.problem_raised.emit(error)

    # Zustand ------------------------------------------------------------------

    def status(self) -> SystemStatus:
        counts = self._errors.counts_by_severity()
        counters = self._stats.all_counters()
        return SystemStatus(
            nas=self._health.snapshot(),
            schema_version=self._db.user_version,
            pending_writes=self._writer.pending,
            active_jobs=self._jobs.active_count,
            warnings=counts.get(Severity.WARNING, 0),
            errors=counts.get(Severity.ERROR, 0) + counts.get(Severity.CRITICAL, 0),
            image_count=counters.get("image_count", 0),
            prompt_count=counters.get("prompt_count", 0),
        )

    def problems(self, limit: int = 200) -> list[ErrorEntry]:
        return self._errors.open_entries(limit=limit)

    def resolve_problem(self, entry_id: int, *, wait: bool = True) -> None:
        """Hakt einen Eintrag ab.

        `wait=False` ist der Weg fuer die Oberflaeche. Das Warten laeuft ueber
        `DbWriter.await_result`, und das gibt erst auf, wenn der Schreiber
        STILLSTEHT - solange er vorankommt, wartet es bis zu einer
        Viertelstunde. Aus dem GUI-Faden heraus bedeutet das ein eingefrorenes
        Fenster ohne jede Rueckmeldung; genau so wurde es gemeldet.
        """
        self._errors.resolve(entry_id, wait=wait)

    def _publish(self) -> None:
        self.status_changed.emit(self.status())
