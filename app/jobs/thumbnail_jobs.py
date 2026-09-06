"""Jobs zur Erzeugung von Vorschaubildern.

Ein Job verarbeitet einen ganzen Stapel statt eines einzelnen Bildes. Bei
Kachelgroesse 256 sind zwei Bildschirmseiten schnell zweihundert Bilder; ein
QRunnable je Bild waere mehr Verwaltungsaufwand als Arbeit, und der Abbruch beim
Weiterscrollen wuerde zweihundert einzelne Absagen bedeuten.

Der Job meldet Teilergebnisse waehrend der Verarbeitung, damit die Kacheln
nacheinander erscheinen statt alle am Ende.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.cancellation import CancellationToken
from app.core.errors import ErrorReporter
from app.dto.models import ImageItem, ThumbnailState
from app.filesystem.nas_health import NasHealthMonitor, TargetUnavailableError
from app.filesystem.thumbnail_cache import ThumbnailCache
from app.filesystem.thumbnail_renderer import ThumbnailRenderer
from app.jobs.job import Job


@dataclass
class ThumbnailBatchResult:
    """Ergebnis eines Stapels.

    ready:    (image_id, thumbnail_key, size_px, bytes) - direkt anzeigbar
    records:  (thumbnail_key, size_px, rel_path, bytes) - fuer die Tabelle thumbnails
    states:   (state, image_id)                         - fuer images.thumbnail_state
    """

    ready: list[tuple[int, str, int, bytes]] = field(default_factory=list)
    records: list[tuple[str, int, str, int]] = field(default_factory=list)
    states: list[tuple[int, int]] = field(default_factory=list)
    failed: int = 0
    skipped: int = 0


class ThumbnailJob(Job):
    pool = "io"
    title = "Vorschaubilder werden erzeugt"

    def __init__(
        self,
        items: list[ImageItem],
        cache: ThumbnailCache,
        renderer: ThumbnailRenderer,
        health: NasHealthMonitor,
        reporter: ErrorReporter,
        *,
        sizes: tuple[int, ...],
        display_size: int,
        token: CancellationToken | None = None,
    ) -> None:
        super().__init__(reporter, token)
        self._items = items
        self._cache = cache
        self._renderer = renderer
        self._health = health
        self._sizes = sizes
        self._display_size = display_size

    def run_job(self) -> ThumbnailBatchResult:
        result = ThumbnailBatchResult()
        total = len(self._items)

        for index, item in enumerate(self._items, start=1):
            if self.token.cancelled:
                break
            key = item.thumbnail_key
            if not key:
                result.skipped += 1
                continue

            cached = self._cache.load(key, self._display_size)
            if cached is not None:
                result.ready.append((item.id, key, self._display_size, cached))
                result.states.append((int(ThumbnailState.READY), item.id))
                self._emit_partial(result, index, total, item.filename)
                continue

            try:
                self._health.guard(item.full_path)
                rendered = self._renderer.render(item.full_path, self._sizes)
            except TargetUnavailableError:
                result.skipped += 1
                continue
            except (OSError, ValueError) as exc:
                result.failed += 1
                result.states.append((int(ThumbnailState.FAILED), item.id))
                self._health.record_failure(item.full_path, exc)
                self._reporter.warning(
                    "jobs.thumbnails",
                    "Vorschaubild nicht erzeugbar: " + item.filename,
                    message_key="thumbnails:render_failed",
                    details=str(exc),
                )
                continue

            self._health.record_success(item.full_path)
            for thumbnail in rendered.thumbnails:
                try:
                    relative = self._cache.store(key, thumbnail.size_px, thumbnail.data)
                except OSError as exc:
                    self._reporter.warning(
                        "jobs.thumbnails",
                        "Cachedatei nicht schreibbar",
                        message_key="thumbnails:write_failed",
                        details=str(exc),
                    )
                    continue
                result.records.append((key, thumbnail.size_px, relative, len(thumbnail.data)))
                if thumbnail.size_px == self._display_size:
                    result.ready.append((item.id, key, thumbnail.size_px, thumbnail.data))

            result.states.append((int(ThumbnailState.READY), item.id))
            self._emit_partial(result, index, total, item.filename)

        return result

    def _emit_partial(
        self, result: ThumbnailBatchResult, index: int, total: int, name: str
    ) -> None:
        """Meldet Zwischenergebnisse, damit Kacheln nacheinander erscheinen.

        Gesendet wird alle acht Bilder und am Ende. Jedes einzelne Bild zu
        melden erzeugt mehr Signalverkehr als Nutzen.
        """
        if index % 8 == 0 or index == total:
            self.report_progress(index, total, name)
