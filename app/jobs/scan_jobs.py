"""Jobs fuer Indexierung und einfache Datenbankabfragen."""

from __future__ import annotations

from typing import Any, Callable

from app.core.cancellation import CancellationToken
from app.core.errors import ErrorReporter
from app.dto.models import ScanStats
from app.filesystem.scanner import Scanner
from app.jobs.job import Job


class StartupScanJob(Job):
    """Inkrementeller Scan aller konfigurierten Wurzeln.

    Laeuft im io-Pool, weil er ueberwiegend auf das NAS wartet. Ein einzelner
    Job ueber alle Wurzeln statt einer je Wurzel: die Wurzeln liegen in der
    Regel auf derselben Freigabe, und paralleles Scannen derselben Freigabe ist
    langsamer, nicht schneller.
    """

    pool = "io"
    title = "Bibliothek wird abgeglichen"

    def __init__(
        self,
        scanner: Scanner,
        roots: list[str],
        reporter: ErrorReporter,
        finalize: Callable[[], None] | None = None,
        token: CancellationToken | None = None,
    ) -> None:
        super().__init__(reporter, token)
        self._scanner = scanner
        self._roots = list(roots)
        self._finalize = finalize

    def run_job(self) -> ScanStats:
        total = ScanStats()
        for index, root in enumerate(self._roots, start=1):
            self.check_cancelled()

            def progress(directory: str, current: int, count: int) -> None:
                self.report_progress(current, count, directory)

            self.report_progress(0, len(self._roots), root)
            total = total.merged(self._scanner.scan_root(root, self.token, progress))
            self.report_progress(index, len(self._roots), root)

        # Schreibpuffer leeren und Zaehler nachziehen laeuft HIER, im
        # Worker-Thread. Im Signalhandler wuerde derselbe Aufruf den GUI-Thread
        # blockieren, bis alle Schreibvorgaenge des Scans committet sind.
        if self._finalize is not None:
            self._finalize()
        return total


class FullReindexJob(StartupScanJob):
    """Vollstaendiger Neuaufbau.

    Unterscheidet sich vom Startscan nur darin, dass zuvor alle Signaturen
    verworfen werden, sodass jede Datei erneut eingelesen wird.
    """

    title = "Bibliothek wird neu indiziert"

    def __init__(
        self,
        scanner: Scanner,
        roots: list[str],
        reporter: ErrorReporter,
        reset: Callable[[], None],
        finalize: Callable[[], None] | None = None,
        token: CancellationToken | None = None,
    ) -> None:
        super().__init__(scanner, roots, reporter, finalize, token)
        self._reset = reset

    def run_job(self) -> ScanStats:
        self._reset()
        return super().run_job()


class QueryJob(Job):
    """Fuehrt eine Leseabfrage im Hintergrund aus.

    Traegt eine request_id mit sich: die Oberflaeche verwirft Ergebnisse einer
    aelteren Anfrage, wenn inzwischen weitergeklickt wurde. Ohne das landet das
    Ergebnis eines alten Ordnerklicks in der aktuellen Ansicht.
    """

    pool = "interactive"
    title = "Daten werden geladen"

    def __init__(
        self,
        function: Callable[[], Any],
        reporter: ErrorReporter,
        *,
        request_id: int = 0,
        title: str = "",
        token: CancellationToken | None = None,
    ) -> None:
        super().__init__(reporter, token)
        self._function = function
        self.request_id = request_id
        if title:
            self.title = title

    def run_job(self) -> tuple[int, Any]:
        return self.request_id, self._function()
