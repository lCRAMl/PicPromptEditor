"""Logging-Einrichtung und Fehlerbruecken.

Drei Wege muenden in dieselbe Ablage:
  * das normale logging-Modul,
  * der ErrorReporter dieses Projekts (LoggingSink),
  * unbehandelte Exceptions aus Haupt- und Worker-Threads (install_excepthooks).

Spezifikation 08 verlangt, dass keine Komponente ungefangene Exceptions erzeugt.
Die Hooks sind das letzte Netz darunter, nicht der Ersatz fuer lokale Behandlung.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from pathlib import Path

from app.core.errors import AppError, ErrorReporter, Severity
from app.core.paths import ensure_dir

_LEVELS = {
    Severity.INFO: logging.INFO,
    Severity.WARNING: logging.WARNING,
    Severity.ERROR: logging.ERROR,
    Severity.CRITICAL: logging.CRITICAL,
}

_FORMAT = "%(asctime)s %(levelname)-8s %(name)-28s %(message)s"


def setup_logging(log_dir: str | Path, level: str = "INFO", console: bool = True) -> Path:
    """Richtet rotierendes Dateilogging ein und liefert den Pfad der Logdatei."""
    directory = ensure_dir(log_dir)
    log_file = directory / "picprompteditor.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=4_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(levelname)-8s %(name)-24s %(message)s"))
        stream.setLevel(getattr(logging, str(level).upper(), logging.INFO))
        root.addHandler(stream)

    return log_file


class LoggingSink:
    """ErrorSink, der Fehlerobjekte in das logging-Modul schreibt."""

    def __init__(self, logger_name: str = "app") -> None:
        self._logger = logging.getLogger(logger_name)

    def handle(self, error: AppError) -> None:
        level = _LEVELS.get(error.severity, logging.ERROR)
        self._logger.log(level, "[%s] %s", error.source, error.message)
        if error.details and level >= logging.ERROR:
            self._logger.log(level, "detail: %s", error.details)


def install_excepthooks(reporter: ErrorReporter) -> None:
    """Leitet unbehandelte Exceptions in den ErrorReporter, statt den Prozess zu beenden."""

    previous = sys.excepthook

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        reporter.report(
            AppError.from_exception(
                exc,
                source="unhandled",
                severity=Severity.CRITICAL,
                message_key="unhandled:" + exc_type.__name__,
                recoverable=False,
            )
        )

    sys.excepthook = hook

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None or issubclass(args.exc_type, SystemExit):
            return
        reporter.report(
            AppError.from_exception(
                args.exc_value,
                source="unhandled.thread",
                severity=Severity.ERROR,
                message_key="unhandled.thread:" + args.exc_type.__name__,
                thread=getattr(args.thread, "name", "?"),
            )
        )

    threading.excepthook = thread_hook
