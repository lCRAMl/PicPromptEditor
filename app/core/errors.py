"""Strukturierte Fehlerobjekte und Fehlerverteilung.

Vier Stufen gemaess Spezifikation 08:
    INFO      Statusmeldung, kein Benutzereingriff
    WARNING   eingeschraenkte Funktion, Anwendung weiter nutzbar
    ERROR     fehlgeschlagene Operation, System laeuft weiter
    CRITICAL  Kernkomponente nicht verfuegbar / Datenbank beschaedigt

Bewusst Qt-frei: der ErrorReporter verteilt an registrierte Sinks. Die Bruecke zu
Qt-Signalen liegt in app/gui/status/ und wird erst dort angemeldet. Dadurch bleiben
Analyse-, Repository- und Dateisystemcode ohne QApplication testbar.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Protocol


class Severity(IntEnum):
    INFO = 10
    WARNING = 20
    ERROR = 30
    CRITICAL = 40

    @property
    def label(self) -> str:
        return self.name.capitalize()


@dataclass(frozen=True)
class AppError:
    """Ein Fehlerereignis. Unveraenderlich, damit es threadsicher weitergegeben werden kann.

    message_key ist der Aggregationsschluessel: gleiche Fehlerart, wechselnde Details.
    Ohne ihn wuerde ein NAS-Ausfall waehrend eines Scans das error_log mit
    zehntausenden gleichartigen Zeilen fluten. Spezifikation 08 fordert
    Nachvollziehbarkeit, nicht die Vollprotokollierung jedes einzelnen Vorkommens.
    """

    severity: Severity
    source: str
    message: str
    message_key: str = ""
    details: str | None = None
    exc_type: str | None = None
    timestamp: float = field(default_factory=time.time)
    recoverable: bool = True
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message_key:
            object.__setattr__(self, "message_key", self.source + ":" + self.message[:120])

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        source: str,
        severity: Severity = Severity.ERROR,
        message: str | None = None,
        message_key: str = "",
        recoverable: bool = True,
        **context: Any,
    ) -> AppError:
        return cls(
            severity=severity,
            source=source,
            message=message or (type(exc).__name__ + ": " + str(exc)),
            message_key=message_key or (source + ":" + type(exc).__name__),
            details="".join(traceback.format_exception(exc)).rstrip(),
            exc_type=type(exc).__name__,
            recoverable=recoverable,
            context=context,
        )


class ErrorSink(Protocol):
    """Empfaenger fuer Fehlerereignisse. Implementierungen muessen threadsicher sein."""

    def handle(self, error: AppError) -> None: ...


class ErrorReporter:
    """Zentrale Verteilstelle. Wird aus allen Threads aufgerufen.

    Ein Sink, der selbst wirft, darf die Verteilung an die uebrigen Sinks nicht
    verhindern. Sonst reisst ein defekter Logger die gesamte Fehlerbehandlung mit.
    """

    def __init__(self) -> None:
        self._sinks: list[ErrorSink] = []
        self._lock = threading.RLock()
        self._counts: dict[Severity, int] = {s: 0 for s in Severity}

    def add_sink(self, sink: ErrorSink) -> None:
        with self._lock:
            self._sinks.append(sink)

    def remove_sink(self, sink: ErrorSink) -> None:
        with self._lock:
            if sink in self._sinks:
                self._sinks.remove(sink)

    def counts(self) -> dict[Severity, int]:
        with self._lock:
            return dict(self._counts)

    def report(self, error: AppError) -> None:
        with self._lock:
            self._counts[error.severity] = self._counts.get(error.severity, 0) + 1
            sinks = tuple(self._sinks)
        for sink in sinks:
            try:
                sink.handle(error)
            except Exception:
                traceback.print_exc()

    # Bequemlichkeitsschnittstelle ---------------------------------------------

    def info(self, source: str, message: str, **kw: Any) -> None:
        self.report(AppError(Severity.INFO, source, message, **kw))

    def warning(self, source: str, message: str, **kw: Any) -> None:
        self.report(AppError(Severity.WARNING, source, message, **kw))

    def error(self, source: str, message: str, **kw: Any) -> None:
        self.report(AppError(Severity.ERROR, source, message, **kw))

    def critical(self, source: str, message: str, **kw: Any) -> None:
        self.report(AppError(Severity.CRITICAL, source, message, recoverable=False, **kw))

    def exception(self, exc: BaseException, *, source: str, **kw: Any) -> None:
        self.report(AppError.from_exception(exc, source=source, **kw))


def guarded(
    reporter: ErrorReporter,
    source: str,
    *,
    severity: Severity = Severity.ERROR,
    default: Any = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Faengt jede Exception einer Funktion ab, meldet sie und liefert den Standardwert.

    Wird von den Background-Jobs verwendet: Spezifikation 08 verlangt, dass ein
    Fehler in einem Job niemals andere Jobs oder den ThreadPool stoppt.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                reporter.report(
                    AppError.from_exception(
                        exc,
                        source=source,
                        severity=severity,
                        message_key=source + ":" + getattr(fn, "__name__", "fn"),
                    )
                )
                return default

        wrapper.__name__ = getattr(fn, "__name__", "wrapper")
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator
