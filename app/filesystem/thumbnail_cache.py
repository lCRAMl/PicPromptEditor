"""Persistenter Thumbnail-Cache auf der lokalen Platte.

Die Cachedateien liegen zweistufig geshardet unter dem konfigurierten
Verzeichnis:

    <cache>/a1/b2/a1b2c3d4e5f6..._256.webp

Ohne Sharding landen hunderttausend Dateien in einem Verzeichnis, was unter
Windows jede Verzeichnisoperation ausbremst.

Identifiziert wird ueber den thumbnail_key, der aus Pfad, Groesse und
Aenderungsdatum des Originals abgeleitet ist. Aendert sich das Original,
entsteht automatisch ein neuer Schluessel, und der alte Eintrag gilt als
veraltet. Eine gesonderte Invalidierung braucht es dadurch nicht.

Die Verwaltungsdaten (Groesse, letzter Zugriff) liegen in der Tabelle
thumbnails; diese Klasse fasst nur Dateien an.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.core.errors import ErrorReporter
from app.core.paths import ensure_dir, shard_path


class ThumbnailCache:
    def __init__(
        self,
        cache_dir: str | Path,
        reporter: ErrorReporter,
        *,
        extension: str = "webp",
    ) -> None:
        self.root = Path(cache_dir)
        self._reporter = reporter
        self._extension = extension.lstrip(".")
        ensure_dir(self.root)

    # Pfade --------------------------------------------------------------------

    def relative_path(self, key: str, size_px: int) -> str:
        sharded = shard_path(key)
        return sharded + "_" + str(size_px) + "." + self._extension

    def absolute_path(self, key: str, size_px: int) -> Path:
        return self.root / self.relative_path(key, size_px)

    # Zugriff ------------------------------------------------------------------

    def exists(self, key: str, size_px: int) -> bool:
        return self.absolute_path(key, size_px).is_file()

    def load(self, key: str, size_px: int) -> bytes | None:
        target = self.absolute_path(key, size_px)
        try:
            return target.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            self._reporter.warning(
                "filesystem.thumbnails",
                "Cachedatei nicht lesbar: " + str(target),
                message_key="thumbnails:read_failed",
                details=str(exc),
            )
            return None

    def store(self, key: str, size_px: int, data: bytes) -> str:
        """Schreibt atomar und liefert den relativen Pfad.

        Ohne Temporaerdatei und Ersetzen kann ein Programmabbruch mitten im
        Schreiben eine halbe Datei hinterlassen, die beim naechsten Start als
        gueltiges Thumbnail gilt und als kaputtes Bild erscheint.
        """
        relative = self.relative_path(key, size_px)
        target = self.root / relative
        ensure_dir(target.parent)
        temp = target.with_name(target.name + ".tmp")
        temp.write_bytes(data)
        os.replace(temp, target)
        return relative

    def delete(self, key: str, size_px: int) -> bool:
        try:
            self.absolute_path(key, size_px).unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            self._reporter.warning(
                "filesystem.thumbnails",
                "Cachedatei nicht loeschbar",
                message_key="thumbnails:delete_failed",
                details=str(exc),
            )
            return False

    def delete_relative(self, relative_path: str) -> bool:
        try:
            (self.root / relative_path).unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    # Wartung ------------------------------------------------------------------

    def total_bytes(self) -> int:
        """Belegter Platz laut Dateisystem. Nur fuer Diagnose und Reparatur.

        Im Normalbetrieb liefert die Tabelle thumbnails dieselbe Zahl, ohne den
        gesamten Cachebaum zu durchlaufen.
        """
        total = 0
        for current, _dirs, files in os.walk(self.root):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(current, name))
                except OSError:
                    continue
        return total

    def prune_empty_dirs(self) -> int:
        removed = 0
        for current, dirs, files in os.walk(self.root, topdown=False):
            if current == str(self.root):
                continue
            if not dirs and not files:
                try:
                    os.rmdir(current)
                    removed += 1
                except OSError:
                    continue
        return removed
