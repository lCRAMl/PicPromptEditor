"""IndexService - Wurzelverzeichnisse, Scans und Neuindizierung."""

from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeout

from PyQt6.QtCore import QObject, pyqtSignal

from app.core.errors import ErrorReporter
from app.database.writer import DbWriter
from app.dto.models import ScanStats
from app.filesystem.scanner import Scanner
from app.jobs.job_manager import JobManager
from app.jobs.scan_jobs import FullReindexJob, StartupScanJob
from app.repositories.folder_repo import FolderRepository
from app.repositories.image_repo import ImageRepository
from app.repositories.prompt_repo import PromptRepository
from app.repositories.scan_repo import ScanRepository
from app.repositories.stats_repo import StatsRepository

SCAN_GROUP = "scan"


class IndexService(QObject):
    """scan_finished(ScanStats), scan_progress(current, total, text).

    scan_finished wird erst ausgeloest, wenn der Job tatsaechlich beendet und
    scan_running wieder False ist. Wuerde es schon beim Vorliegen des Ergebnisses
    feuern, koennte ein direkt daraufhin gestarteter zweiter Scan still
    abgewiesen werden, weil der erste formal noch laeuft.
    """

    scan_started = pyqtSignal(str)
    scan_finished = pyqtSignal(object)
    scan_progress = pyqtSignal(int, int, str)
    scan_rejected = pyqtSignal(str)
    roots_changed = pyqtSignal(object)

    def __init__(
        self,
        scanner: Scanner,
        folders: FolderRepository,
        images: ImageRepository,
        prompts: PromptRepository,
        stats: StatsRepository,
        scans: ScanRepository,
        writer: DbWriter,
        jobs: JobManager,
        settings,
        reporter: ErrorReporter,
    ) -> None:
        super().__init__()
        self._scanner = scanner
        self._folders = folders
        self._images = images
        self._prompts = prompts
        self._stats = stats
        self._scans = scans
        self._writer = writer
        self._jobs = jobs
        self._settings = settings
        self._reporter = reporter
        self._scan_running = False
        self._last_stats: ScanStats | None = None
        self._scan_type = "incremental"
        self._pending_record: Future | None = None

    # Wurzeln ------------------------------------------------------------------

    def roots(self) -> list[str]:
        return [str(p) for p in self._settings.get_list("library.roots")]

    def add_root(self, path: str) -> bool:
        current = self.roots()
        from app.core.paths import normalize_path

        known = {normalize_path(p) for p in current}
        if normalize_path(path) in known:
            return False
        current.append(path)
        self._settings.set("library.roots", current)
        self._folders.add_root(path)
        self.roots_changed.emit(current)
        return True

    def remove_root(self, path: str) -> bool:
        from app.core.paths import normalize_path

        target = normalize_path(path)
        remaining = [p for p in self.roots() if normalize_path(p) != target]
        if len(remaining) == len(self.roots()):
            return False
        self._settings.set("library.roots", remaining)
        self.roots_changed.emit(remaining)
        return True

    # Scannen ------------------------------------------------------------------

    @property
    def scan_running(self) -> bool:
        return self._scan_running

    def start_scan(self) -> int | None:
        """Inkrementeller Abgleich aller Wurzeln."""
        roots = self.roots()
        if not roots:
            self.scan_rejected.emit("Kein Wurzelverzeichnis konfiguriert")
            self._reporter.info(
                "services.index",
                "Kein Wurzelverzeichnis konfiguriert",
                message_key="index:no_roots",
            )
            return None
        if self._scan_running:
            self.scan_rejected.emit("Ein Abgleich laeuft bereits")
            return None

        job = StartupScanJob(self._scanner, roots, self._reporter, self._finalize)
        return self._start(job)

    def start_full_reindex(self) -> int | None:
        roots = self.roots()
        if not roots:
            self.scan_rejected.emit("Kein Wurzelverzeichnis konfiguriert")
            return None
        if self._scan_running:
            self.scan_rejected.emit("Ein Abgleich laeuft bereits")
            return None
        job = FullReindexJob(
            self._scanner, roots, self._reporter, self._reset_signatures, self._finalize
        )
        return self._start(job)

    def _start(self, job: StartupScanJob) -> int:
        self._scan_running = True
        self._last_stats = None
        self._scan_type = "full" if isinstance(job, FullReindexJob) else "incremental"
        job.signals.progress.connect(
            lambda _id, current, total, text: self.scan_progress.emit(current, total, text)
        )
        job.signals.result.connect(self._on_scan_result)
        job.signals.finished.connect(self._on_scan_finished)
        job_id = self._jobs.submit(job, group=SCAN_GROUP)
        self.scan_started.emit(self._scan_type)
        return job_id

    def cancel_scan(self) -> None:
        self._jobs.cancel_group(SCAN_GROUP)

    def _finalize(self) -> None:
        """Laeuft im Worker-Thread am Ende des Scanjobs, nicht im GUI-Thread."""
        self._writer.flush(timeout=120.0)
        self._stats.recount_from_tables()

    def _on_scan_result(self, _job_id: int, payload: object) -> None:
        self._last_stats = payload  # type: ignore[assignment]

    def _on_scan_finished(self, _job_id: int, state: str) -> None:
        self._scan_running = False
        stats = self._last_stats
        if stats is None:
            stats = ScanStats(cancelled=state == "cancelled", errors=1 if state == "failed" else 0)
        self._last_stats = None
        self._record(stats, state)
        self.scan_finished.emit(stats)

    def _record(self, stats: ScanStats, state: str) -> None:
        """Protokolliert den Lauf.

        Spezifikation 03 sieht scan_history ausdruecklich vor. Sie beantwortet
        im Betrieb, wann zuletzt erfolgreich abgeglichen wurde und wie lange
        das inzwischen dauert - Fragen, die sonst niemand beantworten kann.
        """
        if stats.cancelled:
            status = "cancelled"
        elif stats.errors or state == "failed":
            status = "error"
        else:
            status = "ok"
        self._pending_record = self._scans.record(
            duration=stats.duration,
            scan_type=self._scan_type,
            new_files=stats.new_images + stats.new_prompts,
            changed_files=stats.changed,
            deleted_files=stats.removed,
            errors=stats.errors,
            status=status,
            note=stats.summary(),
        )

    def scan_history(self, limit: int = 50):
        self._await_record()
        return self._scans.recent(limit)

    def last_successful_scan(self):
        self._await_record()
        return self._scans.last_successful()

    def _await_record(self, timeout: float = 2.0) -> None:
        """Wartet auf den zuletzt eingereihten Protokolleintrag.

        Der DbWriter schreibt asynchron. Ohne dieses Warten fehlt dem
        Abgleichprotokoll ausgerechnet die Zeile des gerade beendeten Laufs -
        die einzige, die den Benutzer in dem Moment interessiert.

        Kein voller flush(): der wuerde auch eine lange Warteschlange eines
        noch laufenden Scans abwarten und die Oberflaeche einfrieren. Laeuft
        die Frist ab, wird ohne den Eintrag gelesen; "Aktualisieren" holt ihn
        nach.
        """
        future = self._pending_record
        if future is None:
            return
        try:
            future.result(timeout=timeout)
        except FutureTimeout:
            return
        except Exception:
            # Der Writer hat den Fehler bereits gemeldet; das Protokoll ist
            # kein Grund, den Aufrufer scheitern zu lassen.
            pass
        self._pending_record = None

    def _reset_signatures(self) -> None:
        """Setzt die Aenderungssignaturen zurueck; das SQL dazu liegt im Repository."""
        self._images.reset_signatures()
        self._prompts.reset_signatures()

    # Zustand ------------------------------------------------------------------

    def library_counters(self) -> dict[str, int]:
        return self._stats.all_counters()
