"""Datenbankzugriff auf die Tabelle images und die Thumbnail-Verwaltung."""

from __future__ import annotations

import sqlite3
from typing import Iterable, Sequence

from app.database.connection import Database
from app.database.writer import DbWriter
from app.dto.models import ImageItem, ThumbnailState

_ITEM_COLUMNS = (
    "id, folder_id, filename, stem_norm, full_path, thumbnail_key,"
    " thumbnail_state, prompt_id, file_size, modified_time, is_present"
)


def _item(row: sqlite3.Row) -> ImageItem:
    return ImageItem(
        id=row["id"],
        folder_id=row["folder_id"],
        filename=row["filename"],
        stem=row["stem_norm"],
        full_path=row["full_path"],
        thumbnail_key=row["thumbnail_key"],
        thumbnail_state=ThumbnailState(row["thumbnail_state"]),
        prompt_id=row["prompt_id"],
        file_size=row["file_size"],
        modified_time=row["modified_time"],
        is_present=bool(row["is_present"]),
    )


class ImageRepository:
    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    # Lesen --------------------------------------------------------------------

    def by_folder(self, folder_ids: Sequence[int], limit: int = 200000) -> list[ImageItem]:
        """Alle Bilder eines Ordners oder Teilbaums, sortiert nach Dateiname.

        Es wird bewusst der komplette Ergebnissatz geladen statt seitenweise:
        ein ImageItem ist schmal, und selbst hunderttausend davon bleiben im
        zweistelligen Megabyte-Bereich. Das erspart dem Grid die gesamte
        Paging-Logik samt ihrer Fehlerquellen.
        """
        if not folder_ids:
            return []
        placeholders = ",".join("?" * len(folder_ids))
        rows = self._db.query(
            "SELECT " + _ITEM_COLUMNS + " FROM images"
            " WHERE folder_id IN (" + placeholders + ") AND is_present = 1"
            " ORDER BY filename LIMIT ?",
            (*folder_ids, limit),
        )
        return [_item(r) for r in rows]

    def newest_pairs(self, limit: int = 500) -> list[ImageItem]:
        """Die zuletzt entstandenen Bild-Prompt-Paare der ganzen Bibliothek.

        Nur Bilder MIT Prompt: die Frage lautet "was habe ich zuletzt
        erzeugt", und dazu gehoert der Prompt. Bilder ohne Textdatei stellen
        drei Viertel des Bestands und wuerden die Antwort zuschuetten.

        Sortiert nach dem Aenderungsdatum des PROMPTS, nicht des Bildes: beim
        Kopieren einer Bibliothek bekommen Bilder oft neue Zeitstempel, die
        Textdateien seltener - und der Prompt ist das, was den Lauf datiert.
        """
        # Die Spalten muessen qualifiziert werden: images und prompts haben
        # beide eine Spalte id, und ohne Praefix waere die Abfrage mehrdeutig.
        columns = ", ".join("i." + name.strip() for name in _ITEM_COLUMNS.split(","))
        rows = self._db.query(
            "SELECT " + columns + " FROM images i"
            " JOIN prompts p ON p.id = i.prompt_id"
            " WHERE i.is_present = 1 AND p.is_present = 1"
            " ORDER BY p.modified_time DESC, i.id DESC LIMIT ?",
            (limit,),
        )
        return [_item(r) for r in rows]

    def newest_templates(self, limit: int = 500) -> list[ImageItem]:
        """Die neuesten Bilder aus den NAMENSORDNERN, ohne deren Unterordner.

        Der Unterschied zu newest_pairs(): dort zaehlen die Bild-Prompt-Paare
        aus den ai-Ordnern - das laufende Ergebnis. Hier zaehlt, was direkt im
        Namensordner liegt: die Vorlagen, aus denen die Serien entstehen.

        "Direkt darin" heisst: der Ordner hat mindestens einen Unterordner. Ein
        Blattordner ist selbst ein Ergebnisordner und faellt weg.
        """
        columns = ", ".join("i." + name.strip() for name in _ITEM_COLUMNS.split(","))
        rows = self._db.query(
            "SELECT " + columns + " FROM images i"
            " JOIN folders f ON f.id = i.folder_id"
            " WHERE i.is_present = 1 AND f.is_present = 1"
            "   AND EXISTS (SELECT 1 FROM folders k"
            "               WHERE k.parent_id = f.id AND k.is_present = 1)"
            " ORDER BY i.modified_time DESC, i.id DESC LIMIT ?",
            (limit,),
        )
        return [_item(r) for r in rows]

    def by_id(self, image_id: int) -> ImageItem | None:
        row = self._db.query_one(
            "SELECT " + _ITEM_COLUMNS + " FROM images WHERE id = ?", (image_id,)
        )
        return _item(row) if row is not None else None

    def by_ids(self, image_ids: Sequence[int]) -> list[ImageItem]:
        if not image_ids:
            return []
        placeholders = ",".join("?" * len(image_ids))
        rows = self._db.query(
            "SELECT " + _ITEM_COLUMNS + " FROM images WHERE id IN (" + placeholders + ")",
            tuple(image_ids),
        )
        return [_item(r) for r in rows]

    def existing_signatures(self, folder_id: int) -> dict[tuple[str, str], tuple[int, int, int]]:
        """(stem_norm, extension) -> (id, file_size, modified_time).

        Der Scanner laedt das einmal je Ordner und vergleicht im Speicher,
        statt je Datei eine Abfrage abzusetzen.
        """
        rows = self._db.query(
            "SELECT id, stem_norm, extension, file_size, modified_time"
            " FROM images WHERE folder_id = ?",
            (folder_id,),
        )
        return {
            (r["stem_norm"], r["extension"]): (r["id"], r["file_size"], r["modified_time"])
            for r in rows
        }

    def count(self, present_only: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM images"
        if present_only:
            sql += " WHERE is_present = 1"
        return int(self._db.scalar(sql, default=0) or 0)

    def pending_thumbnails(self, image_ids: Sequence[int]) -> list[ImageItem]:
        """Bilder aus der Auswahl, deren Thumbnail noch fehlt oder veraltet ist."""
        if not image_ids:
            return []
        placeholders = ",".join("?" * len(image_ids))
        rows = self._db.query(
            "SELECT " + _ITEM_COLUMNS + " FROM images"
            " WHERE id IN (" + placeholders + ") AND is_present = 1"
            " AND thumbnail_state = ?",
            (*image_ids, int(ThumbnailState.UNKNOWN)),
        )
        return [_item(r) for r in rows]

    # Schreiben ----------------------------------------------------------------

    def insert_many(self, rows: Iterable[tuple]) -> None:
        """rows: (folder_id, filename, full_path, stem_norm, extension,
        file_size, modified_time, thumbnail_key)."""
        batch = list(rows)
        if not batch:
            return
        self._writer.await_result(
            self._writer.executemany(
                "INSERT INTO images"
                " (folder_id, filename, full_path, stem_norm, extension, file_size,"
                "  modified_time, thumbnail_key, is_present, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, unixepoch('subsec'))"
                " ON CONFLICT(folder_id, stem_norm, extension) DO UPDATE SET"
                " filename = excluded.filename,"
                " full_path = excluded.full_path,"
                " file_size = excluded.file_size,"
                " modified_time = excluded.modified_time,"
                " thumbnail_key = excluded.thumbnail_key,"
                " thumbnail_state = CASE WHEN images.thumbnail_key IS excluded.thumbnail_key"
                "                       THEN images.thumbnail_state ELSE 0 END,"
                " is_present = 1,"
                " updated_at = excluded.updated_at",
                batch,
                label="images.insert",
            )
        )

    def reset_signatures(self) -> None:
        """Verwirft Groessen und Zeitstempel, damit ein Scan jede Datei neu einliest.

        Die Datensaetze selbst bleiben erhalten: Ids, Verknuepfungen und
        Analyseergebnisse sollen eine Neuindizierung ueberstehen, soweit die
        Dateien unveraendert sind.
        """
        self._writer.await_result(
            self._writer.execute(
                "UPDATE images SET file_size = -1, modified_time = -1",
                label="images.reset_signatures",
            )
        )

    def mark_absent(self, image_ids: Sequence[int]) -> None:
        if not image_ids:
            return
        self._writer.await_result(
            self._writer.executemany(
                "UPDATE images SET is_present = 0, pair_state = 'image_only',"
                " updated_at = unixepoch('subsec') WHERE id = ?",
                [(i,) for i in image_ids],
                label="images.absent",
            )
        )

    def link_prompts(self, folder_id: int) -> int:
        """Verknuepft Bilder und Prompts eines Ordners ueber stem_norm.

        Bewusst mengenbasiert statt zeilenweise. prompt_id ist nicht unique:
        liegen bild.jpg und bild.png neben einem bild.txt, bekommen beide den
        Prompt. Der Konflikt wird gezaehlt, aber keine Datei verworfen.
        """

        def run(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "UPDATE images SET"
                " prompt_id = (SELECT p.id FROM prompts p"
                "              WHERE p.folder_id = images.folder_id"
                "                AND p.stem_norm = images.stem_norm"
                "                AND p.is_present = 1),"
                " pair_state = CASE WHEN EXISTS ("
                "     SELECT 1 FROM prompts p WHERE p.folder_id = images.folder_id"
                "       AND p.stem_norm = images.stem_norm AND p.is_present = 1)"
                "   THEN 'complete' ELSE 'image_only' END,"
                " updated_at = unixepoch('subsec')"
                " WHERE folder_id = ? AND is_present = 1",
                (folder_id,),
            )
            return cur.rowcount

        return int(
            self._writer.await_result(
                self._writer.submit(run, label="images.link")
            )
        )

    def count_pair_conflicts(self, folder_id: int) -> int:
        """Wie viele Prompts in diesem Ordner an mehr als einem Bild haengen."""
        return int(
            self._db.scalar(
                "SELECT COUNT(*) FROM ("
                " SELECT prompt_id FROM images"
                " WHERE folder_id = ? AND prompt_id IS NOT NULL AND is_present = 1"
                " GROUP BY prompt_id HAVING COUNT(*) > 1)",
                (folder_id,),
                default=0,
            )
            or 0
        )

    def set_thumbnail_state(self, updates: Sequence[tuple[int, int]]) -> None:
        """updates: (state, image_id)."""
        if not updates:
            return
        self._writer.executemany(
            "UPDATE images SET thumbnail_state = ?,"
            " thumbnail_updated = unixepoch('subsec') WHERE id = ?",
            updates,
            label="images.thumb_state",
        )

    # thumbnails ---------------------------------------------------------------

    def record_thumbnails(self, rows: Sequence[tuple[str, int, str, int]]) -> None:
        """rows: (thumbnail_key, size_px, rel_path, bytes)."""
        if not rows:
            return
        self._writer.executemany(
            "INSERT INTO thumbnails(thumbnail_key, size_px, rel_path, bytes,"
            " created_at, last_access)"
            " VALUES (?, ?, ?, ?, unixepoch('subsec'), unixepoch('subsec'))"
            " ON CONFLICT(thumbnail_key, size_px) DO UPDATE SET"
            " rel_path = excluded.rel_path, bytes = excluded.bytes,"
            " last_access = excluded.last_access",
            rows,
            label="thumbnails.record",
        )

    def touch_thumbnails(self, keys: Sequence[tuple[str, int]]) -> None:
        """Aktualisiert last_access fuer die LRU-Bereinigung. keys: (key, size_px)."""
        if not keys:
            return
        self._writer.executemany(
            "UPDATE thumbnails SET last_access = unixepoch('subsec')"
            " WHERE thumbnail_key = ? AND size_px = ?",
            keys,
            label="thumbnails.touch",
        )

    def thumbnail_cache_bytes(self) -> int:
        return int(self._db.scalar("SELECT COALESCE(SUM(bytes), 0) FROM thumbnails", default=0) or 0)

    def stale_thumbnails(self, target_bytes: int) -> list[tuple[str, int, str]]:
        """Aelteste Cacheeintraege, bis die Groesse wieder unter das Ziel faellt."""
        rows = self._db.query(
            "SELECT thumbnail_key, size_px, rel_path, bytes FROM thumbnails"
            " ORDER BY last_access ASC"
        )
        total = self.thumbnail_cache_bytes()
        victims: list[tuple[str, int, str]] = []
        for r in rows:
            if total <= target_bytes:
                break
            victims.append((r["thumbnail_key"], r["size_px"], r["rel_path"]))
            total -= int(r["bytes"])
        return victims

    def forget_thumbnails(self, keys: Sequence[tuple[str, int]]) -> None:
        if not keys:
            return
        self._writer.executemany(
            "DELETE FROM thumbnails WHERE thumbnail_key = ? AND size_px = ?",
            keys,
            label="thumbnails.forget",
        )
