"""Datenbankzugriff auf die Tabelle settings.

**Keine festen Fristen mehr.** Alle Schreibvorgaenge laufen durch den EINEN
Schreibfaden. Steckt dort ein grosser Vorgang - eine Analyse, ein Abgleich -,
dann wartet alles dahinter, und zwar voellig zu Recht. Wer mit
`result(timeout=15)` wartete, brach in genau dieser Lage ab, obwohl nichts
haengt: die Aufgabe lief kurz darauf durch.

Im Betrieb sah das so aus, dass der Einstellungsdialog beim Klick auf OK mit
`TimeoutError` abstuerzte. Schlimmer als der Absturz war die Folge: die
Schleife in `SettingsDialog.apply` brach beim ERSTEN Zeitablauf ab, alle
danach kommenden Einstellungen wurden nie geschrieben - die Hintergrundfarbe
war nach dem naechsten Start wieder weg, obwohl sie im laufenden Programm
richtig aussah.

Deshalb schreibt `upsert` ohne zu warten. Das ist hier gefahrlos: der
SettingsService haelt den Wert bereits typisiert im Speicher, die
Warteschlange ist strikt FIFO, und `Application.shutdown` leert sie vor dem
Schliessen. Wo ein Ergebnis gebraucht wird - eine Zeilenzahl -, wird ueber
`await_result` gewartet: das misst Stillstand statt Gesamtdauer.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

from app.database.connection import Database
from app.database.writer import DbWriter


class SettingsRepository:
    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    def load_all(self) -> dict[str, tuple[str | None, str]]:
        """Liefert {key: (value, value_type)} fuer alle gespeicherten Einstellungen."""
        rows = self._db.query("SELECT key, value, value_type FROM settings")
        return {r["key"]: (r["value"], r["value_type"]) for r in rows}

    def upsert(self, key: str, value: str, value_type: str) -> None:
        """Schreibt EINE Einstellung. Wartet bewusst nicht auf das Ergebnis.

        Der Aufrufer ist in aller Regel der GUI-Faden. Ein Wartevorgang dort
        friert das Fenster ein, und ein Zeitablauf storniert die Aufgabe nicht
        einmal - sie liefe trotzdem, nur haette der Aufrufer schon aufgegeben.
        """
        self._writer.submit(
            lambda conn: conn.execute(
                "INSERT INTO settings(key, value, value_type, updated_at)"
                " VALUES (?, ?, ?, unixepoch('subsec'))"
                " ON CONFLICT(key) DO UPDATE SET"
                " value = excluded.value,"
                " value_type = excluded.value_type,"
                " updated_at = excluded.updated_at",
                (key, value, value_type),
            ),
            label="settings.upsert",
        )

    def upsert_many(self, items: Iterable[tuple[str, str, str]]) -> int:
        rows = list(items)
        if not rows:
            return 0

        def run(conn: sqlite3.Connection) -> int:
            cur = conn.executemany(
                "INSERT INTO settings(key, value, value_type, updated_at)"
                " VALUES (?, ?, ?, unixepoch('subsec'))"
                " ON CONFLICT(key) DO UPDATE SET"
                " value = excluded.value,"
                " value_type = excluded.value_type,"
                " updated_at = excluded.updated_at",
                rows,
            )
            return cur.rowcount

        return int(self._writer.await_result(self._writer.submit(run, label="settings.upsert_many")))

    def delete(self, key: str) -> None:
        self._writer.execute("DELETE FROM settings WHERE key = ?", (key,), label="settings.delete")

    def delete_unknown(self, known_keys: Iterable[str]) -> int:
        keys = list(known_keys)
        placeholders = ",".join("?" * len(keys)) if keys else "''"

        def run(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "DELETE FROM settings WHERE key NOT IN (" + placeholders + ")", keys
            )
            return cur.rowcount

        return int(self._writer.await_result(self._writer.submit(run, label="settings.prune")))
