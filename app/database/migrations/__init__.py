"""Schemamigrationen.

Spezifikation 11 fordert automatische Migration beim Start, Spezifikation 03
liefert nur ein statisches Schema. Hier der Mechanismus dazu:

* PRAGMA user_version ist der Wahrheitswert der Schemaversion.
* schema_migrations protokolliert, was wann gelaufen ist.
* Vor der ersten Aenderung an einer bestehenden Datenbank wird eine Sicherung
  angelegt. Eine abgebrochene Migration darf niemals eine unbrauchbare
  library.db hinterlassen.
* Jede Migration laeuft in einer eigenen Transaktion.

Eine neue Migration wird angelegt, indem ein Modul mXXX_name.py mit VERSION,
NAME und up(conn) erstellt und in MIGRATIONS eingetragen wird.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from app.core.errors import AppError, ErrorReporter, Severity
from app.database.connection import Database
from app.database.migrations import m001_initial, m002_token_stats


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up: Callable[[sqlite3.Connection], None]


MIGRATIONS: Sequence[Migration] = (
    Migration(m001_initial.VERSION, m001_initial.NAME, m001_initial.up),
    Migration(m002_token_stats.VERSION, m002_token_stats.NAME, m002_token_stats.up),
)

SCHEMA_VERSION = max(m.version for m in MIGRATIONS)


class MigrationError(RuntimeError):
    pass


def pending(current_version: int) -> list[Migration]:
    return sorted(
        (m for m in MIGRATIONS if m.version > current_version), key=lambda m: m.version
    )


def apply_migrations(
    database: Database, reporter: ErrorReporter | None = None
) -> list[Migration]:
    """Bringt die Datenbank auf SCHEMA_VERSION. Liefert die angewandten Migrationen."""
    current = database.user_version
    todo = pending(current)
    if not todo:
        return []

    is_existing = current > 0
    if is_existing:
        backup = database.path.with_name(
            database.path.stem + ".pre-v" + str(SCHEMA_VERSION) + ".bak"
        )
        try:
            database.backup_to(backup)
            if reporter is not None:
                reporter.info(
                    "database.migrations",
                    "Sicherung vor Migration angelegt: " + str(backup),
                )
        except sqlite3.Error as exc:
            raise MigrationError("Sicherung vor Migration fehlgeschlagen: " + str(exc)) from exc

    conn = database.open_writer_connection()
    applied: list[Migration] = []
    try:
        for migration in todo:
            started = time.perf_counter()
            try:
                conn.execute("BEGIN")
                migration.up(conn)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations(version, name, applied_at, duration)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        migration.version,
                        migration.name,
                        time.time(),
                        time.perf_counter() - started,
                    ),
                )
                # user_version akzeptiert keine Parameterbindung
                conn.execute("PRAGMA user_version=" + str(int(migration.version)))
                conn.execute("COMMIT")
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if reporter is not None:
                    reporter.report(
                        AppError.from_exception(
                            exc,
                            source="database.migrations",
                            severity=Severity.CRITICAL,
                            message="Migration "
                            + str(migration.version)
                            + " ("
                            + migration.name
                            + ") fehlgeschlagen",
                            recoverable=False,
                        )
                    )
                raise MigrationError(
                    "Migration " + str(migration.version) + " fehlgeschlagen: " + str(exc)
                ) from exc
            applied.append(migration)
    finally:
        conn.close()

    return applied
