"""SQLite-Verbindungsverwaltung.

Der wichtigste Punkt der gesamten Infrastruktur, und in den Spezifikationsdateien
nicht behandelt: Nebenlaeufigkeit. Das Modell hier ist

    N Leser  - eine Verbindung pro Thread, thread-local, nur SELECT
    1 Autor  - ein einziger dedizierter Thread, siehe writer.py

SQLite im WAL-Modus erlaubt beliebig viele parallele Leser gleichzeitig mit einem
Autor. Mehrere schreibende Threads wuerden sich dagegen gegenseitig auf
"database is locked" laufen, und zwar sporadisch und unter Last. Deshalb ist
Schreiben hier architektonisch auf einen Thread beschraenkt statt per Retry
abgesichert.

PRAGMA-Begruendung:
    journal_mode=WAL     Leser blockieren den Autor nicht und umgekehrt.
                         EINMALIG pro Datei gesetzt, nicht pro Verbindung: der
                         Journalmodus ist eine persistente Eigenschaft der Datei.
                         Wird er gesetzt, waehrend schon eine Verbindung offen ist,
                         greift er in der Datei, aber die bestehende Verbindung
                         meldet weiter den alten Modus. Deshalb passiert das hier
                         vor der ersten Leseverbindung.
    synchronous=NORMAL   im WAL-Modus crashsicher bis auf die letzte Transaktion;
                         FULL kostet bei Massenimporten ein Vielfaches
    foreign_keys=ON      muss pro Verbindung gesetzt werden, ist nicht persistent
    busy_timeout         Wartezeit statt Sofortfehler bei kurzen Sperren
    temp_store=MEMORY    Sortier- und Join-Zwischenergebnisse ohne Plattenzugriff
    mmap_size            Lesezugriffe ueber Memory-Mapping, deutlich weniger syscalls
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.core.errors import AppError, ErrorReporter, Severity
from app.core.paths import ensure_dir

# Einmalig pro Datei, vor der ersten Verbindung.
FILE_PRAGMAS: tuple[tuple[str, Any], ...] = (("journal_mode", "WAL"),)

READ_PRAGMAS: tuple[tuple[str, Any], ...] = (
    ("foreign_keys", "ON"),
    ("busy_timeout", 5000),
    ("temp_store", "MEMORY"),
    ("cache_size", -16000),
    ("mmap_size", 268435456),
)

WRITE_PRAGMAS: tuple[tuple[str, Any], ...] = (
    ("synchronous", "NORMAL"),
    ("foreign_keys", "ON"),
    ("busy_timeout", 15000),
    ("temp_store", "MEMORY"),
    ("cache_size", -32000),
    ("wal_autocheckpoint", 1000),
)


class DatabaseError(RuntimeError):
    """Datenbankfehler, der nach oben gemeldet werden soll."""


class Database:
    """Verwaltet Lese- und Schreibverbindungen auf genau eine library.db."""

    def __init__(self, path: str | Path, reporter: ErrorReporter | None = None) -> None:
        self.path = Path(path)
        self._reporter = reporter
        self._local = threading.local()
        self._all_readers: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._closed = False
        self.journal_mode = "unknown"
        if str(self.path) != ":memory:":
            ensure_dir(self.path.parent)
            self.journal_mode = self._prepare_file()

    # Dateivorbereitung --------------------------------------------------------

    def _prepare_file(self) -> str:
        """Setzt die persistenten Dateieigenschaften, bevor irgendeine Verbindung entsteht.

        Liefert den tatsaechlich aktiven Journalmodus. Bleibt die Umstellung aus
        (etwa weil die Datei auf einer Freigabe liegt, die kein WAL unterstuetzt),
        wird das gemeldet statt stillschweigend hingenommen: die gesamte
        Nebenlaeufigkeitsstrategie haengt daran.
        """
        conn = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
        try:
            for name, value in FILE_PRAGMAS:
                conn.execute("PRAGMA " + name + "=" + str(value))
            row = conn.execute("PRAGMA journal_mode").fetchone()
            mode = str(row[0]).lower() if row else "unknown"
        except sqlite3.Error as exc:
            self.report(exc, "database.connection", "database:prepare_file")
            return "unknown"
        finally:
            conn.close()

        if mode != "wal" and self._reporter is not None:
            self._reporter.warning(
                "database.connection",
                "WAL-Modus nicht aktiv (aktuell: " + mode + "). Parallele Lese- und"
                " Schreibzugriffe koennen sich blockieren.",
                message_key="database:no_wal",
            )
        return mode

    # Verbindungen -------------------------------------------------------------

    def _configure(self, conn: sqlite3.Connection, pragmas: Iterable[tuple[str, Any]]) -> None:
        conn.row_factory = sqlite3.Row
        for name, value in pragmas:
            conn.execute("PRAGMA " + name + "=" + str(value))

    def reader(self) -> sqlite3.Connection:
        """Thread-lokale Leseverbindung. Erzeugt sie beim ersten Zugriff im Thread."""
        if self._closed:
            raise DatabaseError("Datenbank ist geschlossen")
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self.path), timeout=5.0, check_same_thread=False, isolation_level=None
            )
            self._configure(conn, READ_PRAGMAS)
            self._local.conn = conn
            with self._lock:
                self._all_readers.append(conn)
        return conn

    def open_writer_connection(self) -> sqlite3.Connection:
        """Wird ausschliesslich vom DbWriter-Thread aufgerufen."""
        conn = sqlite3.connect(
            str(self.path), timeout=15.0, check_same_thread=False, isolation_level=None
        )
        self._configure(conn, WRITE_PRAGMAS)
        return conn

    # Lesehilfen ---------------------------------------------------------------

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self.reader().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.reader().execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        return default if row is None else row[0]

    # Zustand und Pruefung -----------------------------------------------------

    @property
    def user_version(self) -> int:
        return int(self.scalar("PRAGMA user_version", default=0) or 0)

    def table_names(self) -> set[str]:
        rows = self.query("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        return {r["name"] for r in rows}

    def quick_check(self) -> tuple[bool, str]:
        """Schnelle Konsistenzpruefung beim Programmstart (Spezifikation 08)."""
        try:
            result = str(self.scalar("PRAGMA quick_check", default="unknown"))
            return result == "ok", result
        except sqlite3.DatabaseError as exc:
            return False, str(exc)

    def integrity_check(self) -> tuple[bool, str]:
        """Vollstaendige Pruefung. Deutlich langsamer, nur auf Anforderung."""
        try:
            result = str(self.scalar("PRAGMA integrity_check", default="unknown"))
            return result == "ok", result
        except sqlite3.DatabaseError as exc:
            return False, str(exc)

    def backup_to(self, target: str | Path) -> Path:
        """Konsistente Onlinesicherung. Wird vor jeder Schemamigration verwendet."""
        target_path = Path(target)
        ensure_dir(target_path.parent)
        with sqlite3.connect(str(target_path)) as dest:
            self.reader().backup(dest)
        return target_path

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for conn in self._all_readers:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._all_readers.clear()

    # Fehlermeldung ------------------------------------------------------------

    def report(self, exc: BaseException, source: str, message_key: str = "") -> None:
        if self._reporter is not None:
            self._reporter.report(
                AppError.from_exception(
                    exc, source=source, severity=Severity.ERROR, message_key=message_key
                )
            )
