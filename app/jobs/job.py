"""Basisklasse aller Hintergrundaufgaben.

Ein Job ist ein QRunnable mit einem eigenen Signalobjekt. Qt-Signale koennen
nur von einem QObject ausgehen, QRunnable ist aber keines - deshalb die
Trennung in Job (Ausfuehrung) und JobSignals (Benachrichtigung).

Die Signalverbindungen werden im GUI-Thread hergestellt und laufen als
QueuedConnection: die Signalauslieferung passiert damit in der Ereignisschleife
des Empfaengers, nicht im Worker-Thread. Ohne das wuerde Widget-Code im
Worker-Thread laufen, was Qt nicht erlaubt.

Jeder Job ist isoliert: eine Exception wird gefangen, gemeldet und als
failed-Signal ausgeliefert. Spezifikation 08 verlangt, dass ein Fehler in einem
Job niemals andere Jobs oder den ThreadPool stoppt.
"""

from __future__ import annotations

import time
import traceback
from enum import Enum
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from app.core.cancellation import CancellationToken, CancelledError
from app.core.errors import AppError, ErrorReporter, Severity


class JobState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobSignals(QObject):
    """Signale eines einzelnen Jobs.

    started(job_id)
    progress(job_id, current, total, text)
    result(job_id, payload)
    failed(job_id, message)
    finished(job_id, state)
    """

    started = pyqtSignal(int)
    progress = pyqtSignal(int, int, int, str)
    result = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)
    finished = pyqtSignal(int, str)


class Job(QRunnable):
    """Basisklasse. Unterklassen implementieren run_job()."""

    #: Pool, in dem der Job laufen soll: "interactive", "io" oder "cpu"
    pool = "cpu"
    #: Anzeigename fuer Statusleiste und Protokoll
    title = "Aufgabe"

    _next_id = 0

    def __init__(
        self,
        reporter: ErrorReporter,
        token: CancellationToken | None = None,
    ) -> None:
        super().__init__()
        Job._next_id += 1
        self.job_id = Job._next_id
        self.signals = JobSignals()
        self.token = token or CancellationToken()
        self.state = JobState.PENDING
        self.started_at = 0.0
        self.duration = 0.0
        self._reporter = reporter
        self.setAutoDelete(True)

    # Von Unterklassen zu implementieren --------------------------------------

    def run_job(self) -> Any:
        raise NotImplementedError

    # Ausfuehrung --------------------------------------------------------------

    def run(self) -> None:  # QRunnable
        if self.token.cancelled:
            self.state = JobState.CANCELLED
            self.signals.finished.emit(self.job_id, self.state.value)
            return

        self.state = JobState.RUNNING
        self.started_at = time.perf_counter()
        self.signals.started.emit(self.job_id)

        try:
            payload = self.run_job()
            if self.token.cancelled:
                self.state = JobState.CANCELLED
            else:
                self.state = JobState.DONE
                self.signals.result.emit(self.job_id, payload)
        except CancelledError:
            self.state = JobState.CANCELLED
        except Exception as exc:
            self.state = JobState.FAILED
            self._reporter.report(
                AppError.from_exception(
                    exc,
                    source="jobs." + type(self).__name__,
                    severity=Severity.ERROR,
                    message_key="jobs:" + type(self).__name__,
                )
            )
            self.signals.failed.emit(self.job_id, str(exc))
        except BaseException as exc:
            # Auch SystemExit oder KeyboardInterrupt aus einem Worker duerfen
            # den Pool nicht mitreissen.
            self.state = JobState.FAILED
            self._reporter.critical(
                "jobs." + type(self).__name__,
                "Unerwarteter Abbruch: " + type(exc).__name__,
                message_key="jobs:fatal",
                details=traceback.format_exc(),
            )
            self.signals.failed.emit(self.job_id, str(exc))
        finally:
            self.duration = time.perf_counter() - self.started_at
            self.signals.finished.emit(self.job_id, self.state.value)

    # Hilfen fuer Unterklassen -------------------------------------------------

    def report_progress(self, current: int, total: int, text: str = "") -> None:
        self.signals.progress.emit(self.job_id, current, total, text)

    def check_cancelled(self) -> None:
        self.token.raise_if_cancelled()

    def cancel(self) -> None:
        self.token.cancel()
