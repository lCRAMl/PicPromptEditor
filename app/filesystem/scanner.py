"""Verzeichnisscan und inkrementelle Indexierung.

Der einzige Ort, an dem Bild- und Promptdateien vom NAS gelesen werden.

Zwei Entscheidungen bestimmen die Kosten:

* os.scandir statt os.listdir plus os.stat. scandir liefert Groesse und
  Zeitstempel auf Windows aus dem Verzeichniseintrag mit, ohne je Datei einen
  weiteren Zugriff. Auf einer SMB-Freigabe ist das der Unterschied zwischen
  einem und zwei Netzwerk-Roundtrips pro Datei.
* Aenderungserkennung ueber Groesse und Zeitstempel. Bilder werden nicht
  gehasht; ein Volldurchlauf ueber hunderttausend Bilder waere ein Vielfaches
  der Bibliotheksgroesse an Netzwerkverkehr. Promptdateien sind klein und
  werden ohnehin vollstaendig gelesen.

Der Scanner kennt keine GUI und keine Jobs. Er bekommt ein CancellationToken
und meldet Fortschritt ueber einen Callback.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Callable

from app.core.cancellation import CancellationToken, CancelledError
from app.core.errors import AppError, ErrorReporter
from app.core.paths import long_path, normalize_path, normalize_stem
from app.core.text import decode_bytes, text_hash
from app.dto.models import ScanStats
from app.filesystem.nas_health import NasHealthMonitor, TargetUnavailableError
from app.repositories.folder_repo import FolderRepository
from app.repositories.image_repo import ImageRepository
from app.repositories.prompt_repo import PromptRepository

ProgressFn = Callable[[str, int, int], None]

# Der Schluessel muss sich aendern, sobald sich das Originalbild aendert.
# Pfad, Groesse und Zeitstempel reichen dafuer; ein Dateihash waere hier
# reine Netzwerklast ohne zusaetzliche Sicherheit.
def thumbnail_key(full_path: str, file_size: int, modified_time: int) -> str:
    import hashlib

    material = normalize_path(full_path) + "|" + str(file_size) + "|" + str(modified_time)
    return hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest()


@dataclass
class _FolderWork:
    folder_id: int
    path: str
    images: list[tuple] = field(default_factory=list)
    prompts: list[tuple] = field(default_factory=list)
    seen_images: set[tuple[str, str]] = field(default_factory=set)
    seen_prompts: set[str] = field(default_factory=set)


class Scanner:
    def __init__(
        self,
        folders: FolderRepository,
        images: ImageRepository,
        prompts: PromptRepository,
        reporter: ErrorReporter,
        health: NasHealthMonitor,
        *,
        image_extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png"),
        prompt_extension: str = ".txt",
        mtime_tolerance_s: float = 2.0,
        max_prompt_bytes: int = 1_000_000,
    ) -> None:
        self._folders = folders
        self._images = images
        self._prompts = prompts
        self._reporter = reporter
        self._health = health
        self._image_ext = tuple(e.lower() for e in image_extensions)
        self._prompt_ext = prompt_extension.lower()
        self._tolerance_ns = int(mtime_tolerance_s * 1_000_000_000)
        self._max_prompt_bytes = max_prompt_bytes

    # Oeffentlich --------------------------------------------------------------

    def scan_root(
        self,
        root_path: str,
        token: CancellationToken,
        progress: ProgressFn | None = None,
    ) -> ScanStats:
        """Scannt eine Wurzel samt Unterverzeichnissen."""
        started = time.perf_counter()
        self._health.register_root(root_path)
        stats = ScanStats()

        try:
            self._health.guard(root_path)
            directories, root_readable = self._collect_directories(root_path, token)
        except TargetUnavailableError as exc:
            self._reporter.warning(
                "filesystem.scanner",
                "Scan uebersprungen, Ziel nicht erreichbar: " + root_path,
                message_key="scanner:unavailable",
                details=str(exc),
            )
            return ScanStats(errors=1, duration=time.perf_counter() - started)
        except CancelledError:
            return ScanStats(cancelled=True, duration=time.perf_counter() - started)
        except OSError as exc:
            self._health.record_failure(root_path, exc)
            self._reporter.report(
                AppError.from_exception(
                    exc, source="filesystem.scanner", message_key="scanner:root_unreadable"
                )
            )
            return ScanStats(errors=1, duration=time.perf_counter() - started)

        if not root_readable:
            # Die Wurzel selbst war nicht lesbar. Hier NICHT record_success zu
            # rufen ist der Punkt: sonst setzt jeder Scan den Fehlerzaehler
            # zurueck, der Schwellwert wird nie erreicht, und der Breaker
            # oeffnet nie - er waere wirkungslos.
            self._folders.refresh_counts()
            return stats.merged(
                ScanStats(errors=1, duration=time.perf_counter() - started)
            )

        self._register_folders(root_path, directories)
        folder_ids = self._folders.id_map()
        total = len(directories)
        abgebrochen = False

        for index, directory in enumerate(directories, start=1):
            if token.cancelled:
                stats = stats.merged(ScanStats(cancelled=True))
                break
            if not self._health.is_available(directory):
                # Die Freigabe ist waehrend des Laufs weggebrochen. Weiterzu-
                # machen hiesse, in jede weitere Zeitueberschreitung zu laufen:
                # bei zehntausend Ordnern zu je zwanzig Sekunden waere die
                # Anwendung tagelang beschaeftigt. Der Rest bleibt stehen, wie
                # er ist, und der naechste Lauf holt ihn nach.
                abgebrochen = True
                self._reporter.warning(
                    "filesystem.scanner",
                    "Abgleich abgebrochen, Ziel nicht mehr erreichbar: " + root_path,
                    message_key="scanner:lost_during_scan",
                    details=str(total - index + 1) + " Ordner nicht geprueft",
                )
                stats = stats.merged(ScanStats(errors=1))
                break
            folder_id = folder_ids.get(normalize_path(directory))
            if folder_id is None:
                continue
            if progress is not None:
                progress(directory, index, total)
            stats = stats.merged(self._scan_folder(folder_id, directory, token))

        self._folders.refresh_counts()
        if not abgebrochen and self._health.is_available(root_path):
            self._health.record_success(root_path)
        return stats.merged(ScanStats(duration=time.perf_counter() - started))

    # Intern -------------------------------------------------------------------

    def _collect_directories(
        self, root: str, token: CancellationToken
    ) -> tuple[list[str], bool]:
        """Sammelt alle Verzeichnisse unterhalb der Wurzel, inklusive ihrer selbst.

        Der zweite Rueckgabewert sagt, ob die Wurzel selbst lesbar war. Ein
        einzelner unlesbarer Unterordner ist Alltag; eine unlesbare Wurzel
        bedeutet, dass die Freigabe weg ist - und nur das darf den Breaker
        belasten.
        """
        found = [root]
        stack = [root]
        root_readable = True
        while stack:
            token.raise_if_cancelled()
            current = stack.pop()
            if not self._health.is_available(current):
                # Der Breaker hat waehrend des Einsammelns geoeffnet. Jeder
                # weitere Versuch waere eine Zeitueberschreitung mehr.
                break
            try:
                with os.scandir(long_path(current)) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                found.append(entry.path)
                                stack.append(entry.path)
                        except OSError:
                            continue
            except PermissionError as exc:
                self._reporter.warning(
                    "filesystem.scanner",
                    "Kein Zugriff auf " + current,
                    message_key="scanner:permission",
                    details=str(exc),
                )
            except OSError as exc:
                if current == root:
                    root_readable = False
                self._health.record_failure(current, exc)
                self._reporter.warning(
                    "filesystem.scanner",
                    "Verzeichnis nicht lesbar: " + current,
                    message_key="scanner:dir_unreadable",
                    details=str(exc),
                )
        return found, root_readable

    def _register_folders(self, root: str, directories: list[str]) -> None:
        by_norm = {normalize_path(d): d for d in directories}
        rows: list[tuple[str, int | None, int, bool]] = []
        root_norm = normalize_path(root)

        # Erst alle Ordner ohne Eltern anlegen, damit die Ids existieren.
        for directory in directories:
            depth = 0 if normalize_path(directory) == root_norm else len(
                PurePath(directory).parts
            ) - len(PurePath(root).parts)
            rows.append((directory, None, depth, normalize_path(directory) == root_norm))
        self._folders.upsert_many(rows)

        # Dann die Elternbeziehung nachtragen.
        ids = self._folders.id_map()
        with_parents: list[tuple[str, int | None, int, bool]] = []
        for directory, parent_id_source, depth, is_root in rows:
            parent_norm = normalize_path(str(PurePath(directory).parent))
            parent_id = ids.get(parent_norm) if parent_norm in by_norm else None
            with_parents.append((directory, parent_id, depth, is_root))
        self._folders.upsert_many(with_parents)

    def _scan_folder(
        self, folder_id: int, directory: str, token: CancellationToken
    ) -> ScanStats:
        known_images = self._images.existing_signatures(folder_id)
        known_prompts = self._prompts.existing_signatures(folder_id)
        work = _FolderWork(folder_id=folder_id, path=directory)
        files_seen = 0
        errors = 0
        changed = 0
        new_files = 0

        try:
            entries = list(os.scandir(long_path(directory)))
        except OSError as exc:
            self._health.record_failure(directory, exc)
            self._reporter.warning(
                "filesystem.scanner",
                "Ordner nicht lesbar: " + directory,
                message_key="scanner:folder_unreadable",
                details=str(exc),
            )
            return ScanStats(folders_seen=1, errors=1)

        for entry in entries:
            if token.cancelled:
                return ScanStats(folders_seen=1, files_seen=files_seen, cancelled=True)
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                suffix = PurePath(entry.name).suffix.lower()
                if suffix not in self._image_ext and suffix != self._prompt_ext:
                    continue

                info = entry.stat()
                files_seen += 1
                stem = normalize_stem(entry.name)

                if suffix == self._prompt_ext:
                    outcome = self._collect_prompt(work, entry, info, stem, known_prompts)
                else:
                    outcome = self._collect_image(work, entry, info, stem, suffix, known_images)
                if outcome == "new":
                    new_files += 1
                elif outcome == "changed":
                    changed += 1
            except OSError as exc:
                errors += 1
                self._reporter.warning(
                    "filesystem.scanner",
                    "Datei nicht lesbar: " + entry.name,
                    message_key="scanner:file_unreadable",
                    details=str(exc),
                )

        if work.prompts:
            self._prompts.insert_many(work.prompts)
        if work.images:
            self._images.insert_many(work.images)

        removed = self._mark_missing(folder_id, work, known_images, known_prompts)
        self._images.link_prompts(folder_id)
        # Ein Prompt an mehreren Bildern ist KEIN Fehler, sondern der
        # Regelfall: wer eine Bildserie aus einem Prompt erzeugt, legt genau
        # eine Textdatei und viele Bilder ab. Frueher stand hier eine Warnung
        # je Ordner - in dieser Bibliothek 345 Stueck, die nichts zu tun gaben
        # und die Problemliste zuschuetteten. Die Zahl wird weiter gezaehlt und
        # in der Zusammenfassung genannt; gemeldet wird sie nicht.
        shared = self._images.count_pair_conflicts(folder_id)
        self._folders.mark_scanned(folder_id)

        return ScanStats(
            folders_seen=1,
            files_seen=files_seen,
            new_images=new_files,
            new_prompts=0,
            changed=changed,
            removed=removed,
            pair_conflicts=shared,
            errors=errors,
        )

    def _unchanged(self, known: tuple[int, int, int] | None, size: int, mtime_ns: int) -> bool:
        """Groesse gleich und Zeitstempel innerhalb der Toleranz.

        Die Toleranz ist noetig, weil SMB-Freigaben Zeitstempel teils runden.
        Ohne sie meldet jeder Scan Aenderungen, die keine sind.
        """
        if known is None:
            return False
        _, known_size, known_mtime = known
        return known_size == size and abs(known_mtime - mtime_ns) <= self._tolerance_ns

    def _collect_image(
        self,
        work: _FolderWork,
        entry: os.DirEntry,
        info: os.stat_result,
        stem: str,
        suffix: str,
        known: dict[tuple[str, str], tuple[int, int, int]],
    ) -> str:
        work.seen_images.add((stem, suffix))
        size = info.st_size
        mtime_ns = info.st_mtime_ns
        previous = known.get((stem, suffix))
        if self._unchanged(previous, size, mtime_ns):
            return "unchanged"
        work.images.append(
            (
                work.folder_id,
                entry.name,
                entry.path,
                stem,
                suffix,
                size,
                mtime_ns,
                thumbnail_key(entry.path, size, mtime_ns),
            )
        )
        return "changed" if previous is not None else "new"

    def _collect_prompt(
        self,
        work: _FolderWork,
        entry: os.DirEntry,
        info: os.stat_result,
        stem: str,
        known: dict[str, tuple[int, int, int]],
    ) -> str:
        work.seen_prompts.add(stem)
        size = info.st_size
        mtime_ns = info.st_mtime_ns
        previous = known.get(stem)
        if self._unchanged(previous, size, mtime_ns):
            return "unchanged"
        if size > self._max_prompt_bytes:
            self._reporter.warning(
                "filesystem.scanner",
                "Promptdatei uebersprungen, zu gross: " + entry.name,
                message_key="scanner:prompt_too_large",
            )
            return "unchanged"

        with open(long_path(entry.path), "rb") as handle:
            raw = handle.read()
        text, encoding = decode_bytes(raw)
        work.prompts.append(
            (
                work.folder_id,
                entry.name,
                entry.path,
                stem,
                text,
                text_hash(text),
                encoding,
                size,
                mtime_ns,
                len(text.split()),
            )
        )
        return "changed" if previous is not None else "new"

    def _mark_missing(
        self,
        folder_id: int,
        work: _FolderWork,
        known_images: dict[tuple[str, str], tuple[int, int, int]],
        known_prompts: dict[str, tuple[int, int, int]],
    ) -> int:
        """Verschwundene Dateien werden deaktiviert, nicht geloescht.

        Spezifikation 03 will die Historie erhalten; ausserdem waere ein
        kurzzeitig nicht erreichbarer Ordner sonst ein Datenverlust.
        """
        gone_images = [
            record[0] for key, record in known_images.items() if key not in work.seen_images
        ]
        gone_prompts = [
            record[0] for stem, record in known_prompts.items() if stem not in work.seen_prompts
        ]
        self._images.mark_absent(gone_images)
        self._prompts.mark_absent(gone_prompts)
        return len(gone_images) + len(gone_prompts)
