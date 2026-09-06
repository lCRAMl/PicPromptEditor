"""WatchService - haelt die Bibliothek ohne Zutun aktuell.

Drei Aufgaben:

1. **Periodischer Abgleich** als Primaermechanismus. SMB meldet Aenderungen
   anderer Rechner grundsaetzlich nicht; ohne Takt wuerde eine Bibliothek, die
   von einem zweiten Rechner befuellt wird, nie aktuell.
2. **Datei-Watcher** als Beschleuniger fuer lokale Aenderungen. Bewusst nur auf
   den Wurzeln und dem gerade sichtbaren Ordner - ein Watcher ueber
   zehntausende Verzeichnisse sprengt die Handle-Grenzen von Windows.
3. **NAS-Pruefung** im Takt, damit der Circuit Breaker ueberhaupt etwas weiss.

Die entscheidende Regel: **die Pruefung laeuft NIEMALS im GUI-Thread.** Ein
os.stat auf eine tote SMB-Freigabe blockiert je nach Konfiguration zwanzig
Sekunden und laenger. Genau dagegen existiert der Breaker; ihn aus dem
GUI-Thread zu pruefen wuerde die Blockade herbeifuehren, die er verhindern soll.
Die Pruefung ist deshalb ein Job im io-Pool.

Die Entscheidungslogik (Entprellung, Faelligkeit) liegt Qt-frei in
app/filesystem/change_tracker.py.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from app.core.errors import ErrorReporter
from app.filesystem.change_tracker import ChangeTracker, SyncRequest
from app.filesystem.nas_health import NasHealthMonitor
from app.jobs.job import Job
from app.jobs.job_manager import JobManager

TICK_MS = 1000
PROBE_GROUP = "nas_probe"


class NasProbeJob(Job):
    """Prueft die Erreichbarkeit der Wurzeln - im io-Pool, nicht im GUI-Thread."""

    #: Eigene Spur, nicht der io-Pool: siehe JobManager. Eine Pruefung, die
    #: hinter haengenden Dateizugriffen wartet, kann nichts feststellen.
    pool = "probe"
    title = "NAS-Erreichbarkeit wird geprueft"

    def __init__(self, health: NasHealthMonitor, roots: list[str], reporter) -> None:
        super().__init__(reporter)
        self._health = health
        self._roots = roots

    def run_job(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for root in self._roots:
            self.check_cancelled()
            result[root] = self._health.probe(root)
        return result


class WatchService(QObject):
    """sync_requested(reason), nas_state_changed(dict)"""

    sync_requested = pyqtSignal(str)
    nas_state_changed = pyqtSignal(object)

    def __init__(
        self,
        index_service,
        health: NasHealthMonitor,
        jobs: JobManager,
        settings,
        reporter: ErrorReporter,
    ) -> None:
        super().__init__()
        self._index = index_service
        self._health = health
        self._jobs = jobs
        self._settings = settings
        self._reporter = reporter

        self._tracker = ChangeTracker(
            debounce_seconds=settings.get_int("index.watcher_debounce_ms") / 1000.0,
            periodic_seconds=float(settings.get_int("index.background_scan_interval_s")),
            watcher_enabled=settings.get_bool("index.watcher_enabled"),
        )

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._watcher.fileChanged.connect(self._on_directory_changed)

        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._on_tick)

        self._probe_timer = QTimer(self)
        self._probe_timer.setInterval(
            max(5, settings.get_int("system.nas_probe_interval_s")) * 1000
        )
        self._probe_timer.timeout.connect(self.probe_now)

        self._last_nas_state: dict[str, str] = {}
        self._probing = False
        self._extra_paths: list[str] = []

        self._index.scan_finished.connect(self._on_scan_finished)
        settings.subscribe("index.watcher_enabled", self._on_watcher_setting)
        settings.subscribe("index.watcher_debounce_ms", self._on_debounce_setting)
        settings.subscribe("index.background_scan_interval_s", self._on_interval_setting)
        settings.subscribe("system.nas_probe_interval_s", self._on_probe_setting)
        settings.subscribe("library.roots", lambda _k, _v: self.refresh_paths())

    # Lebenszyklus -------------------------------------------------------------

    def start(self) -> None:
        self.refresh_paths()
        self._tracker.reset(time.monotonic())
        self._tick.start()
        self._probe_timer.start()
        self.probe_now()

    def stop(self) -> None:
        self._tick.stop()
        self._probe_timer.stop()
        self._clear_watch_paths()

    @property
    def tracker(self) -> ChangeTracker:
        return self._tracker

    def watched_paths(self) -> list[str]:
        return list(self._watcher.directories())

    # Pfade --------------------------------------------------------------------

    def refresh_paths(self) -> None:
        """Ueberwacht die Wurzeln und den zuletzt angesehenen Ordner.

        Nicht den ganzen Baum: Windows begrenzt die Zahl gleichzeitig
        ueberwachter Verzeichnisse, und bei einer Bibliothek mit tausenden
        Ordnern waere die Grenze sofort erreicht. Was der Watcher nicht sieht,
        holt der periodische Abgleich nach.
        """
        self._clear_watch_paths()
        roots = self._roots()
        wanted = list(roots)
        wanted.extend(self._extra_paths)

        for path in dict.fromkeys(wanted):
            if not path:
                continue
            # addPath meldet selbst, wenn der Pfad nicht existiert; ein nicht
            # erreichbares NAS ist hier kein Fehlerfall, sondern Normalbetrieb.
            self._watcher.addPath(path)

    def _roots(self) -> list[str]:
        """Die Wurzeln, beim Breaker angemeldet.

        register_root() ist die Voraussetzung dafuer, dass der Breaker eine
        Wurzel ueberhaupt kennt: fuer einen unbekannten Pfad verwirft er jede
        Rueckmeldung, und snapshot() bliebe leer. Beim Start ist die Liste oft
        noch leer, weil die Wurzeln erst danach eingerichtet werden - deshalb
        hier und nicht nur einmalig in Application.
        """
        roots = [str(root) for root in self._settings.get_list("library.roots") if root]
        for root in roots:
            self._health.register_root(root)
        return roots

    def watch_folder(self, path: str) -> None:
        """Nimmt den gerade angesehenen Ordner zusaetzlich in die Ueberwachung."""
        if not path:
            return
        self._extra_paths = [path]
        self.refresh_paths()

    def _clear_watch_paths(self) -> None:
        directories = self._watcher.directories()
        if directories:
            self._watcher.removePaths(directories)
        files = self._watcher.files()
        if files:
            self._watcher.removePaths(files)

    # Ereignisse ---------------------------------------------------------------

    def _on_directory_changed(self, path: str) -> None:
        self._tracker.note_change(path, time.monotonic())

    def _on_tick(self) -> None:
        if self._index.scan_running:
            self._tracker.suspend()
            return

        request = self._tracker.due(time.monotonic())
        if request is None:
            return
        self._trigger(request)

    def _trigger(self, request: SyncRequest) -> None:
        now = time.monotonic()
        self._tracker.note_sync_started(now)
        if self._index.start_scan() is None:
            # Abgewiesen, etwa weil keine Wurzel konfiguriert ist. Der Takt
            # laeuft trotzdem weiter, sonst bliebe der Tracker fuer immer
            # ausgesetzt.
            self._tracker.note_sync_finished(now)
            return
        self.sync_requested.emit(request.reason.value)

    def _on_scan_finished(self, _stats) -> None:
        self._tracker.note_sync_finished(time.monotonic())

    # NAS ----------------------------------------------------------------------

    def probe_now(self) -> None:
        """Prueft die Erreichbarkeit aller Wurzeln im Hintergrund."""
        if self._probing:
            return
        roots = self._roots()
        if not roots:
            return

        self._probing = True
        job = NasProbeJob(self._health, roots, self._reporter)
        job.signals.result.connect(self._on_probe_result)
        job.signals.finished.connect(lambda _id, _state: self._clear_probing())
        self._jobs.submit(job, group=PROBE_GROUP)

    def _clear_probing(self) -> None:
        self._probing = False

    def _on_probe_result(self, _job_id: int, _payload: object) -> None:
        state = self._health.snapshot()
        if state != self._last_nas_state:
            self._last_nas_state = state
            self.nas_state_changed.emit(state)

    # Einstellungen ------------------------------------------------------------

    def _on_watcher_setting(self, _key: str, value: object) -> None:
        self._tracker.watcher_enabled = bool(value)
        if not value:
            self._clear_watch_paths()
        else:
            self.refresh_paths()

    def _on_debounce_setting(self, _key: str, value: object) -> None:
        self._tracker.debounce_seconds = int(value) / 1000.0

    def _on_interval_setting(self, _key: str, value: object) -> None:
        self._tracker.periodic_seconds = float(value)

    def _on_probe_setting(self, _key: str, value: object) -> None:
        self._probe_timer.setInterval(max(5, int(value)) * 1000)

    # Zustand ------------------------------------------------------------------

    def status(self) -> dict[str, object]:
        now = time.monotonic()
        return {
            "watcher_enabled": self._tracker.watcher_enabled,
            "watched": len(self._watcher.directories()),
            "pending": len(self._tracker.pending_paths),
            "next_periodic_s": round(self._tracker.seconds_until_periodic(now)),
            "nas": self._health.snapshot(),
        }
