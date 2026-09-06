"""Datenbankzugriff auf scan_history.

Spezifikation 03 sieht die Tabelle zur Protokollierung aller Scanvorgaenge vor.
Sie beantwortet im Betrieb die Fragen, die sonst niemand beantworten kann: wann
lief der letzte erfolgreiche Abgleich, wie lange dauert er inzwischen, und hat
sich zuletzt ueberhaupt etwas geaendert.

Das Protokoll wird begrenzt gehalten. Ein periodischer Abgleich alle
funfzehn Minuten schreibt im Dauerbetrieb rund fuenfunddreissigtausend Zeilen
im Jahr; interessant sind davon die letzten paar hundert.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import Future
from dataclasses import dataclass

from app.database.connection import Database
from app.database.writer import DbWriter

MAX_ROWS = 500


@dataclass(frozen=True, slots=True)
class ScanRecord:
    id: int
    scan_time: float
    duration: float
    scan_type: str
    new_files: int
    changed_files: int
    deleted_files: int
    errors: int
    status: str
    note: str


class ScanRepository:
    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    def record(
        self,
        *,
        duration: float,
        scan_type: str,
        new_files: int,
        changed_files: int,
        deleted_files: int,
        errors: int,
        status: str,
        note: str = "",
    ) -> Future:
        """Traegt einen Scanlauf ein und kuerzt das Protokoll.

        Beides in einer Schreibaufgabe, damit das Kuerzen nicht als eigener
        Auftrag in der Warteschlange landet und dort mit dem naechsten Scan
        konkurriert.

        Liefert das Future der Schreibaufgabe. Wer den Eintrag unmittelbar
        danach lesen will, wartet darauf - der Writer arbeitet asynchron, und
        ohne dieses Warten fehlt dem Leser genau die eben geschriebene Zeile.
        """

        def run(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO scan_history"
                " (scan_time, duration, scan_type, new_files, changed_files,"
                "  deleted_files, errors, status, note)"
                " VALUES (unixepoch('subsec'), ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    duration,
                    scan_type,
                    new_files,
                    changed_files,
                    deleted_files,
                    errors,
                    status,
                    note,
                ),
            )
            # Nach id als zweitem Kriterium, nicht nach scan_time allein:
            # unixepoch('subsec') liefert mehreren Laeufen in derselben
            # Millisekunde denselben Wert, und bei Gleichstand ist die
            # Reihenfolge in SQLite beliebig. Ohne den Zusatz koennte das
            # Kuerzen ausgerechnet die juengsten Zeilen loeschen.
            conn.execute(
                "DELETE FROM scan_history WHERE id IN ("
                " SELECT id FROM scan_history ORDER BY scan_time DESC, id DESC"
                " LIMIT -1 OFFSET ?)",
                (MAX_ROWS,),
            )

        return self._writer.submit(run, label="scan_history.record")

    def recent(self, limit: int = 50) -> list[ScanRecord]:
        rows = self._db.query(
            "SELECT id, scan_time, duration, scan_type, new_files, changed_files,"
            " deleted_files, errors, status, note FROM scan_history"
            " ORDER BY scan_time DESC, id DESC LIMIT ?",
            (limit,),
        )
        return [
            ScanRecord(
                id=r["id"],
                scan_time=r["scan_time"],
                duration=r["duration"] or 0.0,
                scan_type=r["scan_type"],
                new_files=r["new_files"],
                changed_files=r["changed_files"],
                deleted_files=r["deleted_files"],
                errors=r["errors"],
                status=r["status"],
                note=r["note"] or "",
            )
            for r in rows
        ]

    def last_successful(self) -> ScanRecord | None:
        found = [r for r in self.recent(20) if r.status == "ok"]
        return found[0] if found else None

    def count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM scan_history", default=0) or 0)
