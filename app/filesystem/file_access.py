"""Direkter Dateizugriff und Uebergabe an Systemprogramme.

Spezifikation 09 erlaubt Datei- und NAS-Zugriffe ausschliesslich in dieser
Schicht. Das Laden eines Originalbilds und das Oeffnen im Standardviewer gehoeren
dazu - sie standen zunaechst im ImageService und damit an der falschen Stelle.

Alle Funktionen fragen zuerst den Circuit Breaker. Ist das Ziel als nicht
erreichbar bekannt, scheitern sie sofort, statt in einen Systemaufruf zu laufen,
der auf einer toten SMB-Freigabe zwanzig Sekunden haengt.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.core.errors import ErrorReporter
from app.core.paths import long_path
from app.filesystem.nas_health import NasHealthMonitor


class FileAccess:
    def __init__(self, health: NasHealthMonitor, reporter: ErrorReporter) -> None:
        self._health = health
        self._reporter = reporter

    # Lesen --------------------------------------------------------------------

    def read_bytes(self, path: str, *, max_bytes: int = 0) -> bytes | None:
        """Laedt eine Datei vollstaendig. Liefert None statt zu werfen.

        Einer der wenigen erlaubten NAS-Zugriffe zur Laufzeit (Detailansicht,
        Vergleich). Gehoert in einen Job, nicht in den GUI-Thread.
        """
        try:
            self._health.guard(path)
            with open(long_path(path), "rb") as handle:
                data = handle.read(max_bytes) if max_bytes > 0 else handle.read()
            self._health.record_success(path)
            return data
        except OSError as exc:
            self._health.record_failure(path, exc)
            self._reporter.warning(
                "filesystem.access",
                "Datei nicht lesbar: " + Path(path).name,
                message_key="file:read_failed",
                details=str(exc),
            )
            return None

    # Uebergabe an das System --------------------------------------------------

    def open_in_default_viewer(self, path: str) -> bool:
        try:
            self._health.guard(path)
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return True
        except OSError as exc:
            self._reporter.warning(
                "filesystem.access",
                "Nicht zu oeffnen: " + Path(path).name,
                message_key="file:open_failed",
                details=str(exc),
            )
            return False

    def reveal_in_explorer(self, path: str) -> bool:
        """Zeigt die Datei im Dateimanager, mit Auswahl des Eintrags."""
        try:
            self._health.guard(path)
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
            return True
        except OSError as exc:
            self._reporter.warning(
                "filesystem.access",
                "Dateimanager nicht zu oeffnen",
                message_key="file:reveal_failed",
                details=str(exc),
            )
            return False

    def open_folder(self, path: str) -> bool:
        try:
            self._health.guard(path)
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return True
        except OSError as exc:
            self._reporter.warning(
                "filesystem.access",
                "Ordner nicht zu oeffnen: " + str(path),
                message_key="folder:open_failed",
                details=str(exc),
            )
            return False
