"""ImageService - Ordnerbaum, Ergebnismengen und Vorschaubilder.

Die Oberflaeche kennt weder Repositories noch Cache noch Dateisystem. Sie fragt
hier nach einem Vorschaubild und bekommt entweder sofort eines (RAM-Cache) oder
nichts - im zweiten Fall wird im Hintergrund erzeugt und spaeter per Signal
nachgereicht.

Zwei Mechanismen sind fuer die Bedienbarkeit entscheidend:

* Anfragegenerationen. Jede Ergebnisanfrage bekommt eine fortlaufende Nummer.
  Klickt der Benutzer weiter, bevor das Ergebnis da ist, wird das alte Ergebnis
  verworfen statt angezeigt.
* Sammelmeldung fertiger Thumbnails. Die fertigen Bilder werden gesammelt und
  alle 60 ms als ein Signal ausgeliefert. Einzelmeldungen wuerden bei einem
  Stapel von zweihundert Kacheln zweihundert Neuzeichnungen ausloesen.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.cache.lru import SizedLruCache
from app.core.cancellation import CancellationToken
from app.core.errors import ErrorReporter
from app.dto.models import FolderNode, ImageItem, ResultSet
from app.filesystem.file_access import FileAccess
from app.filesystem.nas_health import NasHealthMonitor
from app.filesystem.thumbnail_cache import ThumbnailCache
from app.filesystem.thumbnail_renderer import ThumbnailRenderer
from app.jobs.job_manager import JobManager
from app.jobs.scan_jobs import QueryJob
from app.jobs.thumbnail_jobs import ThumbnailBatchResult, ThumbnailJob
from app.repositories.folder_repo import FolderRepository
from app.repositories.image_repo import ImageRepository

THUMBNAIL_GROUP = "thumbnails"
#: Vorschaubilder gehen im io-Pool vor dem Abgleich.
THUMBNAIL_PRIORITY = 10


class ImageService(QObject):
    """results_ready(ResultSet), thumbnails_ready(list[tuple[int, bytes]])."""

    results_ready = pyqtSignal(object)
    thumbnails_ready = pyqtSignal(object)
    folders_ready = pyqtSignal(object)

    def __init__(
        self,
        folders: FolderRepository,
        images: ImageRepository,
        cache: ThumbnailCache,
        renderer: ThumbnailRenderer,
        health: NasHealthMonitor,
        files: FileAccess,
        jobs: JobManager,
        reporter: ErrorReporter,
        *,
        ram_cache_bytes: int = 256 * 1024 * 1024,
        sizes: tuple[int, ...] = (128, 256, 512),
        display_size: int = 256,
    ) -> None:
        super().__init__()
        self._folders = folders
        self._images = images
        self._cache = cache
        self._renderer = renderer
        self._health = health
        self._files = files
        self._jobs = jobs
        self._reporter = reporter
        self._sizes = sizes
        self._display_size = display_size

        self._ram = SizedLruCache[tuple[str, int], bytes](ram_cache_bytes, len)
        self._request_id = 0
        self._lock = threading.RLock()
        self._pending: set[int] = set()

        self._inbox: list[tuple[int, bytes]] = []
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(60)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_inbox)

    # Einstellungen ------------------------------------------------------------

    @property
    def display_size(self) -> int:
        return self._display_size

    def set_display_size(self, size_px: int) -> None:
        if size_px != self._display_size:
            self._display_size = size_px

    def set_ram_limit(self, max_bytes: int) -> None:
        self._ram.set_max_bytes(max_bytes)

    def cache_stats(self) -> dict[str, float | int]:
        return self._ram.stats()

    # Ordnerbaum ---------------------------------------------------------------

    def folder_tree(self) -> list[FolderNode]:
        """Alle Ordner, flach und nach Tiefe sortiert. Die GUI baut daraus den Baum."""
        return self._folders.all_folders()

    def items_for(self, image_ids: list[int]) -> list[ImageItem]:
        """Elemente zu einer Id-Menge, ohne Ergebnismenge und ohne Signal.

        Der Evolutionsbaum braucht die Elemente fuer Vorschau und Symbole,
        aber nicht als Ergebnismenge - load_ids_async wuerde das
        Kachelraster des Bibliothekstabs ueberschreiben.
        """
        return self._images.by_ids(image_ids)

    def folder_scope(self, folder: FolderNode) -> list[int]:
        """Ordner samt Unterordnern. Grundlage der ordnerbezogenen Suche."""
        return self._folders.descendant_ids(folder.id)

    def load_folder_tree_async(self) -> None:
        job = QueryJob(
            self._folders.all_folders, self._reporter, title="Ordnerbaum wird geladen"
        )
        job.signals.result.connect(lambda _id, payload: self.folders_ready.emit(payload[1]))
        self._jobs.submit(job)

    # Ergebnismengen -----------------------------------------------------------

    def next_request_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def is_current(self, request_id: int) -> bool:
        with self._lock:
            return request_id == self._request_id

    def load_folder_async(self, folder: FolderNode, include_subfolders: bool = False) -> int:
        """Laedt den Inhalt eines Ordners in den Thumbnail Explorer."""
        request_id = self.next_request_id()
        self._jobs.cancel_group(THUMBNAIL_GROUP)

        def query() -> ResultSet:
            ids = (
                self._folders.descendant_ids(folder.id)
                if include_subfolders
                else [folder.id]
            )
            items = self._images.by_folder(ids)
            origin = "Ordner: " + folder.path
            if include_subfolders:
                origin += " (mit Unterordnern)"
            return ResultSet(items=items, origin=origin, request_id=request_id)

        job = QueryJob(query, self._reporter, request_id=request_id, title="Ordner wird geladen")
        job.signals.result.connect(self._on_results)
        self._jobs.submit(job)
        return request_id

    def load_ids_async(self, image_ids: list[int], origin: str) -> int:
        """Zeigt eine beliebige Id-Menge, etwa ein Suchergebnis."""
        request_id = self.next_request_id()
        self._jobs.cancel_group(THUMBNAIL_GROUP)

        def query() -> ResultSet:
            order = {image_id: position for position, image_id in enumerate(image_ids)}
            items = self._images.by_ids(image_ids)
            items.sort(key=lambda item: order.get(item.id, 0))
            return ResultSet(items=items, origin=origin, request_id=request_id)

        job = QueryJob(query, self._reporter, request_id=request_id, title="Ergebnis wird geladen")
        job.signals.result.connect(self._on_results)
        self._jobs.submit(job)
        return request_id

    def load_folder_for(self, folder: FolderNode, origin: str, deliver) -> None:
        """Laedt einen Ordner samt Unterordnern fuer EINEN Empfaenger.

        Samt Unterordner, weil der Ordnerbaum in Klammern die Zahl aus
        name/ai nennt: wer darauf klickt, erwartet diese Bilder zu sehen und
        nicht die paar Dateien, die daneben im Elternordner liegen.
        """

        def query() -> ResultSet:
            ids = self._folders.descendant_ids(folder.id)
            return ResultSet(items=self._images.by_folder(ids), origin=origin)

        job = QueryJob(query, self._reporter, title="Ordner wird geladen")
        job.signals.result.connect(lambda _id, payload: deliver(payload[1]))
        self._jobs.submit(job)

    def load_newest_templates(self, limit: int, origin: str, deliver) -> None:
        """Die neuesten Bilder aus den Namensordnern, ohne deren Unterordner."""

        def query() -> ResultSet:
            return ResultSet(items=self._images.newest_templates(limit), origin=origin)

        job = QueryJob(query, self._reporter, title="Vorlagen werden geladen")
        job.signals.result.connect(lambda _id, payload: deliver(payload[1]))
        self._jobs.submit(job)

    def load_newest_pairs(self, limit: int, origin: str, deliver) -> None:
        """Die zuletzt entstandenen Bild-Prompt-Paare der ganzen Bibliothek.

        Geht wie load_ids_for an EINEN Empfaenger, nicht ueber results_ready:
        das Ergebnis gehoert in seinen Tab, nicht zusaetzlich ins
        Bibliothekstab.
        """

        def query() -> ResultSet:
            return ResultSet(items=self._images.newest_pairs(limit), origin=origin)

        job = QueryJob(query, self._reporter, title="Neueste Paare werden geladen")
        job.signals.result.connect(lambda _id, payload: deliver(payload[1]))
        self._jobs.submit(job)

    def load_ids_for(self, image_ids: list[int], origin: str, deliver) -> None:
        """Laedt eine Id-Menge fuer EINEN bestimmten Empfaenger.

        load_ids_async sendet sein Ergebnis ueber results_ready an alle
        Zuhoerer - richtig fuer das Bibliothekstab, falsch fuer einen einzelnen
        Ergebnistab, dessen Inhalt sonst auch im Bibliothekstab landen wuerde.
        Hier geht das Ergebnis ausschliesslich an den uebergebenen Rueckruf.
        """
        if not image_ids:
            deliver(ResultSet(items=[], origin=origin))
            return

        def query() -> ResultSet:
            order = {image_id: position for position, image_id in enumerate(image_ids)}
            items = self._images.by_ids(image_ids)
            items.sort(key=lambda item: order.get(item.id, 0))
            return ResultSet(items=items, origin=origin)

        job = QueryJob(query, self._reporter, title="Ergebnis wird geladen")
        job.signals.result.connect(lambda _id, payload: deliver(payload[1]))
        self._jobs.submit(job)

    def _on_results(self, _job_id: int, payload: object) -> None:
        request_id, result = payload  # type: ignore[misc]
        if not self.is_current(request_id):
            return  # veraltete Anfrage, der Benutzer ist weitergeklickt
        self.results_ready.emit(result)

    # Vorschaubilder -----------------------------------------------------------

    def cached_thumbnail(self, item: ImageItem) -> bytes | None:
        """Sofortzugriff aus dem RAM-Cache. Kein Datei- oder Datenbankzugriff."""
        if not item.thumbnail_key:
            return None
        return self._ram.get((item.thumbnail_key, self._display_size))

    def request_thumbnails(self, items: list[ImageItem]) -> None:
        """Liefert vorhandene Vorschaubilder aus und reiht fehlende ein.

        Was im RAM liegt, wird SOFORT ausgeliefert, nicht uebersprungen. Genau
        das war ein Fehler: frueher galt ein Bild im RAM-Cache als erledigt -
        unter der Annahme, wer es anfordert, habe es ohnehin schon. Fuer eine
        neu geoeffnete Ansicht stimmt das nicht. Wer einen Ordner ansah und ihn
        danach in einem Tab oeffnete, bekam dort leere Kacheln, und es wurde
        auch nichts nachgeholt: die Bilder galten als vorhanden, der Tab hatte
        sie aber nie bekommen.
        """
        missing: list[ImageItem] = []
        cached: list[tuple[int, bytes]] = []
        with self._lock:
            for item in items:
                if not item.thumbnail_key:
                    continue
                ready = self._ram.get((item.thumbnail_key, self._display_size))
                if ready is not None:
                    cached.append((item.id, ready))
                    continue
                if item.id in self._pending:
                    continue
                self._pending.add(item.id)
                missing.append(item)

        if cached:
            # Ueber denselben Weg wie frisch erzeugte: thumbnails_ready
            # erreicht alle Ansichten, und das Sammeln im Postfach verhindert
            # ein Neuzeichnen je Bild.
            self._inbox.extend(cached)
            if not self._flush_timer.isActive():
                self._flush_timer.start()

        if not missing:
            return

        job = ThumbnailJob(
            missing,
            self._cache,
            self._renderer,
            self._health,
            self._reporter,
            sizes=self._sizes,
            display_size=self._display_size,
            token=CancellationToken(),
        )
        job.signals.result.connect(self._on_thumbnails)
        job.signals.finished.connect(
            lambda _id, _state, ids=[i.id for i in missing]: self._release_pending(ids)
        )
        # Hoehere Prioritaet als der Abgleich: waehrend ein Hintergrundscan
        # laeuft, wartet der Benutzer sonst auf Kacheln, die er gerade ansieht,
        # bis der Scan durch ist.
        self._jobs.submit(job, group=THUMBNAIL_GROUP, priority=THUMBNAIL_PRIORITY)

    def _release_pending(self, image_ids: list[int]) -> None:
        with self._lock:
            self._pending.difference_update(image_ids)

    def _on_thumbnails(self, _job_id: int, payload: object) -> None:
        result: ThumbnailBatchResult = payload  # type: ignore[assignment]
        for image_id, key, size_px, data in result.ready:
            self._ram.put((key, size_px), data)
            self._inbox.append((image_id, data))

        if result.records:
            self._images.record_thumbnails(result.records)
        if result.states:
            self._images.set_thumbnail_state([(state, i) for state, i in result.states])

        if self._inbox and not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush_inbox(self) -> None:
        if not self._inbox:
            return
        batch = self._inbox
        self._inbox = []
        self.thumbnails_ready.emit(batch)

    # Externe Anzeige ----------------------------------------------------------

    def open_in_viewer(self, item: ImageItem) -> bool:
        """Oeffnet das Originalbild im Standardprogramm des Systems."""
        return self._files.open_in_default_viewer(item.full_path)

    def reveal_in_explorer(self, item: ImageItem) -> bool:
        return self._files.reveal_in_explorer(item.full_path)

    def open_folder(self, path: str) -> bool:
        return self._files.open_folder(path)

    def load_full_image(self, item: ImageItem) -> bytes | None:
        """Laedt das Originalbild fuer die Detailansicht.

        Einer der wenigen erlaubten NAS-Zugriffe zur Laufzeit. Er blockiert den
        aufrufenden Thread und gehoert deshalb in einen Job, nicht in den
        GUI-Thread.
        """
        return self._files.read_bytes(item.full_path)
