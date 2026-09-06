"""Datenbankzugriff auf error_log und system_status.

DatabaseSink haengt am ErrorReporter und schreibt aggregiert: gleiche Fehlerart
erhoeht count und last_seen statt eine neue Zeile anzulegen. Der Aggregations-
schluessel ist (severity, source, message_key, resolved), abgesichert durch den
UNIQUE-Index ux_error_log_dedupe.

Das Schreiben laeuft ueber den DbWriter und wartet nicht auf das Ergebnis. Ein
Fehler darf niemals den Thread aufhalten, in dem er aufgetreten ist - erst recht
nicht den GUI-Thread.
"""

from __future__ import annotations

import sqlite3

from app.core.errors import AppError, Severity
from app.database.connection import Database
from app.database.writer import DbWriter
from app.dto.models import ErrorEntry

_UPSERT = (
    "INSERT INTO error_log"
    " (timestamp, last_seen, severity, source, message_key, message, details, count, resolved)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0)"
    " ON CONFLICT(severity, source, message_key, resolved) DO UPDATE SET"
    " last_seen = excluded.last_seen,"
    " message = excluded.message,"
    " details = COALESCE(excluded.details, error_log.details),"
    " count = error_log.count + 1"
)


def _resolve_one(conn: sqlite3.Connection, entry_id: int) -> int:
    """Hakt EINE offene Zeile ab, notfalls durch Zusammenfuehren.

    Laeuft im Schreibfaden und innerhalb des Savepoints der Aufgabe: schlaegt
    etwas fehl, bleibt keine halbe Zusammenfuehrung zurueck.

    Liefert 1, wenn etwas geschehen ist, sonst 0. Eine bereits abgehakte oder
    verschwundene Zeile ist kein Fehler - zwei Klicks auf denselben Eintrag
    sollen nicht anders ausgehen als einer.
    """
    offen = conn.execute(
        "SELECT severity, source, message_key, message, details, count, last_seen"
        " FROM error_log WHERE id = ? AND resolved = 0",
        (entry_id,),
    ).fetchone()
    if offen is None:
        return 0

    schluessel = (offen["severity"], offen["source"], offen["message_key"])
    zwilling = conn.execute(
        "SELECT id FROM error_log WHERE resolved = 1"
        " AND severity = ? AND source = ? AND message_key = ?",
        schluessel,
    ).fetchone()

    if zwilling is None:
        conn.execute("UPDATE error_log SET resolved = 1 WHERE id = ?", (entry_id,))
        return 1

    # Zusammenfuehren: die Anzahl beider Zeilen addieren, den juengeren
    # Zeitpunkt und die neuere Meldung uebernehmen. Details nur, wenn die
    # abgehakte Zeile keine hat - eine vorhandene Fehlerbeschreibung ist mehr
    # wert als die neueste.
    conn.execute(
        "UPDATE error_log SET"
        " count = count + ?,"
        " last_seen = MAX(last_seen, ?),"
        " message = ?,"
        " details = COALESCE(details, ?)"
        " WHERE id = ?",
        (offen["count"], offen["last_seen"], offen["message"], offen["details"],
         zwilling["id"]),
    )
    conn.execute("DELETE FROM error_log WHERE id = ?", (entry_id,))
    return 1


class ErrorRepository:
    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    def record(self, error: AppError, *, wait: bool = False) -> None:
        params = (
            error.timestamp,
            error.timestamp,
            int(error.severity),
            error.source,
            error.message_key,
            error.message,
            error.details,
        )
        future = self._writer.submit(
            lambda conn: conn.execute(_UPSERT, params), label="error_log"
        )
        if wait:
            future.result(timeout=15.0)

    def open_entries(self, min_severity: Severity = Severity.WARNING, limit: int = 200) -> list[ErrorEntry]:
        rows = self._db.query(
            "SELECT id, timestamp, last_seen, severity, source, message, details, count, resolved"
            " FROM error_log WHERE resolved = 0 AND severity >= ?"
            " ORDER BY severity DESC, last_seen DESC LIMIT ?",
            (int(min_severity), limit),
        )
        return [
            ErrorEntry(
                id=r["id"],
                timestamp=r["timestamp"],
                last_seen=r["last_seen"],
                severity=int(r["severity"]),
                severity_label=Severity(r["severity"]).label,
                source=r["source"],
                message=r["message"],
                details=r["details"],
                count=r["count"],
                resolved=bool(r["resolved"]),
            )
            for r in rows
        ]

    def counts_by_severity(self) -> dict[Severity, int]:
        rows = self._db.query(
            "SELECT severity, SUM(count) AS total FROM error_log WHERE resolved = 0 GROUP BY severity"
        )
        result: dict[Severity, int] = {}
        for r in rows:
            try:
                result[Severity(r["severity"])] = int(r["total"] or 0)
            except ValueError:
                continue
        return result

    def resolve(self, entry_id: int, *, wait: bool = True) -> None:
        """Markiert einen Eintrag als erledigt.

        **Warum das keine einfache UPDATE-Anweisung sein darf.** Der
        Eindeutigkeitsschluessel ist (severity, source, message_key, resolved),
        und `resolved` gehoert absichtlich dazu: nachdem ein Fehler abgehakt
        wurde, soll ein erneutes Auftreten eine neue offene Zeile bekommen und
        nicht die alte wiederbeleben.

        Genau daran scheitert ein blosses `SET resolved = 1`, sobald derselbe
        Fehler zum zweiten Mal abgehakt wird:

            1. Fehler tritt auf        -> Zeile (X, resolved=0)
            2. Benutzer hakt ab        -> Zeile (X, resolved=1)
            3. Fehler tritt erneut auf -> neue Zeile (X, resolved=0)
            4. Benutzer hakt ab        -> Kollision mit der Zeile aus Schritt 2

        Im Betrieb sah das so aus, dass der oberste Eintrag der Liste sich
        nicht abhaken liess und nach jedem Versuch wieder dastand - waehrend
        im Protokoll ein IntegrityError auflief.

        Deshalb wird zusammengefuehrt: gibt es die abgehakte Zwillingszeile
        schon, wandern Anzahl und Zeitpunkt dorthin und die offene Zeile faellt
        weg. Sonst genuegt das Setzen des Merkmals.

        `wait` ist per Vorgabe gesetzt, weil der Aufrufer die Liste unmittelbar
        danach neu einliest. Ohne Warten liest er den Stand VOR dem Commit -
        der Eintrag stuende wieder da, und zwar auch dann, wenn alles
        funktioniert hat. Es ist eine einzelne Zeile; die Wartezeit liegt im
        Bereich des Stapelverzugs von 20 ms.
        """
        future = self._writer.submit(
            lambda conn: _resolve_one(conn, entry_id), label="error_log.resolve"
        )
        if wait:
            self._writer.await_result(future, idle_timeout=15.0)

    def resolve_source(self, source: str, *, wait: bool = False) -> None:
        """Hakt alle offenen Eintraege einer Quelle ab.

        Zeile fuer Zeile ueber dieselbe Zusammenfuehrung wie `resolve`. Ein
        gemeinsames UPDATE waere kuerzer und liefe in dieselbe Kollision,
        sobald zu einer der Zeilen schon eine abgehakte Fassung existiert -
        dann bliebe die GANZE Anweisung wirkungslos, nicht nur die eine Zeile.
        """

        def run(conn: sqlite3.Connection) -> int:
            ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM error_log WHERE source = ? AND resolved = 0",
                    (source,),
                ).fetchall()
            ]
            for entry_id in ids:
                _resolve_one(conn, entry_id)
            return len(ids)

        future = self._writer.submit(run, label="error_log.resolve_source")
        if wait:
            self._writer.await_result(future, idle_timeout=15.0)

    def prune(self, max_rows: int) -> None:
        """Haelt das Protokoll begrenzt. Aufgeloeste und alte Zeilen fallen zuerst."""

        def run(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "DELETE FROM error_log WHERE id IN ("
                " SELECT id FROM error_log ORDER BY resolved DESC, last_seen ASC"
                " LIMIT MAX(0, (SELECT COUNT(*) FROM error_log) - ?))",
                (max_rows,),
            )
            return cur.rowcount

        self._writer.submit(run, label="error_log.prune")

    # system_status ------------------------------------------------------------

    def set_status(self, key: str, value: str) -> None:
        self._writer.execute(
            "INSERT INTO system_status(key, value, updated_at)"
            " VALUES (?, ?, unixepoch('subsec'))"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, value),
        )

    def status(self) -> dict[str, str]:
        rows = self._db.query("SELECT key, value FROM system_status")
        return {r["key"]: (r["value"] or "") for r in rows}


class DatabaseSink:
    """ErrorSink, der Fehlerobjekte aggregiert in error_log schreibt.

    **Die eine Meldung, die hier NICHT hineindarf, ist der fehlgeschlagene
    Schreibvorgang des Fehlerprotokolls selbst.** Sonst entsteht eine
    Rueckkopplung, die sich nicht von selbst beruhigt: der Schreibfaden
    scheitert an einer error_log-Aufgabe, meldet den Fehlschlag, die Meldung
    reiht die naechste error_log-Aufgabe ein, die aus demselben Grund
    scheitert - und so fort. Der Schreibfaden hat danach dauerhaft zu tun,
    und JEDER andere Schreibvorgang wartet dahinter. Genau so sieht es von
    aussen aus, als haenge das Programm.

    Im Protokoll landet der Fehlschlag trotzdem: der LoggingSink haengt am
    selben Reporter und wird hier nicht angetastet.
    """

    def __init__(self, repository: ErrorRepository, min_severity: Severity = Severity.WARNING) -> None:
        self._repo = repository
        self._min = min_severity

    def handle(self, error: AppError) -> None:
        if error.severity < self._min:
            return
        if _is_error_log_write_failure(error):
            return
        self._repo.record(error)


def _is_error_log_write_failure(error: AppError) -> bool:
    """Ob die Meldung vom gescheiterten Schreiben des Fehlerprotokolls stammt.

    Erkannt an Quelle UND Aufgabenname: der DbWriter gibt den `label` der
    gescheiterten Aufgabe im Kontext mit. Nur `source` zu pruefen waere zu
    grob - ein gescheiterter Bildschreibvorgang kommt aus derselben Quelle und
    soll sehr wohl protokolliert werden.
    """
    if not error.source.startswith("database.writer"):
        return False
    label = str(error.context.get("label", ""))
    return label.startswith("error_log")
