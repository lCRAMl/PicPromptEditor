"""Serialisierter Datenbank-Schreibthread.

Alle Schreibvorgaenge der Anwendung laufen durch genau diesen Thread. Aufrufer
uebergeben eine Funktion, die eine sqlite3.Connection bekommt, und erhalten ein
concurrent.futures.Future zurueck. Damit ist der Aufrufer frei, ob er wartet
(Future.result) oder nicht (fire and forget).

Drei Eigenschaften, die Spezifikation 08 direkt einfordert:

* Batching: aufeinanderfolgende Aufgaben werden in EINER Transaktion ausgefuehrt.
  Bei der Erstindexierung ist das der Unterschied zwischen wenigen Sekunden und
  vielen Minuten, weil jedes COMMIT im WAL-Modus einen fsync kostet.
* Isolation: jede Aufgabe laeuft zusaetzlich in einem eigenen SAVEPOINT. Wirft
  eine Aufgabe, wird nur diese zurueckgerollt; die uebrigen Aufgaben des Batches
  bleiben gueltig. Ohne Savepoints wuerde ein einziger fehlerhafter Datensatz
  tausend gute Datensaetze mit sich reissen.
* Sichtbarkeit: ein Future wird erst aufgeloest, NACHDEM der Batch committet ist.
  Wuerde es innerhalb der Transaktion aufgeloest, kaeme future.result() zurueck,
  bevor eine Leseverbindung die Aenderung sehen kann - jedes Read-after-Write
  waere dann ein Gluecksspiel.

Die Reihenfolge ist strikt FIFO. Schreibvorgaenge duerfen nicht nach Prioritaet
umsortiert werden: ein spaeter eingereihtes INSERT vor ein frueher eingereihtes
DELETE zu ziehen aendert das Ergebnis. Prioritaeten gehoeren in den JobManager,
nicht in das serialisierte Schreibprotokoll.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable

from app.core.errors import AppError, ErrorReporter, Severity
from app.database.connection import Database

WriteFn = Callable[[sqlite3.Connection], Any]


@dataclass
class _Task:
    sequence: int
    fn: WriteFn
    future: Future
    label: str = ""


class DbWriter:
    """Einziger Schreibpfad in die Datenbank."""

    STOP = object()

    def __init__(
        self,
        database: Database,
        reporter: ErrorReporter | None = None,
        *,
        batch_size: int = 256,
        batch_linger: float = 0.02,
    ) -> None:
        self._db = database
        self._reporter = reporter
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._conn: sqlite3.Connection | None = None
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._batch_size = batch_size
        self._batch_linger = batch_linger
        self._started = threading.Event()
        self._stats = {"tasks": 0, "batches": 0, "failed": 0}
        #: Groesser als 0, solange der Schreiber in einem Stapel steckt - von
        #: BEGIN bis COMMIT. Das ist das dritte Lebenszeichen und das
        #: wichtigste: eine EINZELNE lange Aufgabe (das Ersetzen aller
        #: Familien schreibt ueber hunderttausend Zeilen in einem Zug) laesst
        #: den Aufgabenzaehler minutenlang stillstehen, obwohl der Schreiber
        #: durchgehend arbeitet.
        self._active = 0
        #: Zaehlt JEDE abgearbeitete Aufgabe, sofort nach ihrer Ausfuehrung -
        #: unabhaengig davon, ob der Batch schon committet ist. `stats["tasks"]`
        #: taugt als Lebenszeichen naemlich nicht: es waechst erst NACH dem
        #: COMMIT, und ein grosser Batch saehe damit minutenlang aus wie
        #: Stillstand. Nur fuer `await_result`; nach aussen bedeutungslos.
        self._progress = 0

    # Lebenszyklus -------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="db-writer", daemon=True)
        self._thread.start()
        self._started.wait(timeout=10.0)

    def stop(self, timeout: float = 10.0) -> None:
        if self._thread is None:
            return
        self._queue.put(self.STOP)
        self._thread.join(timeout=timeout)
        self._thread = None

    def configure(self, *, batch_size: int | None = None, batch_linger: float | None = None) -> None:
        """Stellt die Stapelbildung nachtraeglich ein.

        Noetig, weil der Schreiber VOR den Einstellungen entsteht - die liegen
        ja in der Datenbank, in die er schreibt. Ohne diesen Weg waere
        `jobs.db_write_batch` ein Regler ohne Wirkung; im Dialog stand er
        trotzdem.

        Die Werte werden in jedem Schleifendurchlauf frisch gelesen, ein
        laufender Stapel wird also nicht gestoert.
        """
        if batch_size is not None:
            self._batch_size = max(1, int(batch_size))
        if batch_linger is not None:
            self._batch_linger = max(0.0, float(batch_linger))

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def is_working(self) -> bool:
        """Ob der Schreiber gerade in einem Stapel steckt.

        Unterscheidet "arbeitet an etwas Grossem" von "haengt". Ohne diese
        Auskunft sind beide von aussen nicht zu trennen, und `await_result`
        muesste raten.
        """
        return self._active > 0

    # Einreichen ---------------------------------------------------------------

    def submit(self, fn: WriteFn, *, label: str = "") -> Future:
        """Reiht eine Schreibaufgabe ein. Ausfuehrung streng in Einreihungsreihenfolge.

        Das Future wird erst aufgeloest, wenn der Batch committet ist. Wer
        future.result() abwartet, darf die Aenderung danach also lesen.
        """
        future: Future = Future()
        if self._thread is None:
            future.set_exception(RuntimeError("DbWriter wurde nicht gestartet"))
            return future
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        self._queue.put(_Task(seq, fn, future, label))
        return future

    def execute(self, sql: str, params: Any = (), *, label: str = "") -> Future:
        def run(conn: sqlite3.Connection) -> int:
            cur = conn.execute(sql, params)
            return cur.lastrowid if cur.lastrowid is not None else cur.rowcount

        return self.submit(run, label=label or sql[:40])

    def executemany(self, sql: str, rows: Any, *, label: str = "") -> Future:
        def run(conn: sqlite3.Connection) -> int:
            cur = conn.executemany(sql, rows)
            return cur.rowcount

        return self.submit(run, label=label or sql[:40])

    def flush(self, timeout: float | None = 30.0) -> None:
        """Wartet, bis alle bisher eingereihten Aufgaben committet sind."""
        self.submit(lambda conn: None, label="flush").result(timeout=timeout)

    #: Wie lange der Schreiber weder etwas fertigstellen noch an etwas
    #: arbeiten darf, bevor `await_result` aufgibt. Nicht die Gesamtdauer.
    IDLE_TIMEOUT = 60.0
    #: Abstand, in dem waehrend des Wartens nach Fortschritt gesehen wird.
    PROGRESS_POLL = 2.0
    #: Absolute Obergrenze. Sie greift nur, wenn der Schreiber die ganze Zeit
    #: zu arbeiten VORGIBT - etwa weil eine Aufgabe in einer Endlosschleife
    #: haengt. Grosszuegig gewaehlt: das Ersetzen aller Familien darf laut
    #: seiner eigenen Frist zehn Minuten dauern, und alles dahinter wartet
    #: zwangslaeufig mit.
    MAX_WAIT = 900.0

    def await_result(
        self,
        future: Future,
        *,
        idle_timeout: float | None = None,
        poll: float | None = None,
        max_wait: float | None = None,
    ) -> Any:
        """Wartet auf ein Future, solange der Schreiber ueberhaupt vorankommt.

        **Eine feste Frist ist hier das falsche Mass.** Der Schreiber ist ein
        einzelner Faden mit einer Warteschlange; ein grosser Vorgang - das
        Ersetzen aller Familien etwa, das seinerseits zehn Minuten dauern darf -
        haelt alles dahinter auf. Wer mit `result(timeout=60)` wartet, bricht
        dann ab, obwohl nichts haengt: der Vorgang, auf den er wartet, laeuft
        gleich danach erfolgreich durch.

        Genau das ist im Betrieb passiert. Der Abgleich brach dreimal mit
        `TimeoutError` in `folders.upsert` ab, waehrend im Hintergrund die
        Analyse schrieb. Schlimmer noch: eine abgelaufene Frist **storniert
        nichts**. Die Aufgabe blieb in der Warteschlange und wurde ausgefuehrt -
        der Lauf war abgebrochen, die Daten aber geschrieben.

        Hier wird deshalb nicht die Gesamtdauer begrenzt, sondern der
        **Stillstand**: aufgegeben wird erst, wenn der Schreiber `idle_timeout`
        lang keine einzige Aufgabe mehr fertiggestellt hat. Dann haengt er
        tatsaechlich, und eine Meldung ist berechtigt.
        """
        wartezeit = self.IDLE_TIMEOUT if idle_timeout is None else idle_timeout
        obergrenze = self.MAX_WAIT if max_wait is None else max_wait
        takt = self.PROGRESS_POLL if poll is None else poll
        takt = max(0.01, min(takt, wartezeit))

        zuletzt = self._progress
        stillstand = 0.0
        gesamt = 0.0
        while True:
            try:
                return future.result(timeout=takt)
            except FutureTimeout:
                gesamt += takt
                fertig = self._progress
                if fertig != zuletzt or self.is_working:
                    # Entweder wurde etwas fertig, oder der Schreiber steckt
                    # gerade in einem Stapel. Beides ist Arbeit, kein Haenger.
                    zuletzt, stillstand = fertig, 0.0
                else:
                    stillstand += takt
                    if stillstand >= wartezeit:
                        raise
                if obergrenze and gesamt >= obergrenze:
                    raise

    # Ausfuehrung --------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._conn = self._db.open_writer_connection()
        except Exception as exc:
            self._report(exc, "database.writer", critical=True)
            self._started.set()
            return
        self._started.set()

        running = True
        while running:
            first = self._queue.get()
            if first is self.STOP:
                break
            batch = [first]
            deadline = time.monotonic() + self._batch_linger
            while len(batch) < self._batch_size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if item is self.STOP:
                    running = False
                    break
                batch.append(item)
            # Bewusst nicht sortiert: die Einreihungsreihenfolge ist bedeutungstragend.
            self._run_batch(batch)

        if self._conn is not None:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self._conn.close()
            self._conn = None

    def _run_batch(self, batch: list[_Task]) -> None:
        conn = self._conn
        if conn is None:
            for task in batch:
                task.future.set_exception(RuntimeError("Schreibverbindung fehlt"))
            return

        # Ab hier gilt der Schreiber als beschaeftigt - einschliesslich BEGIN
        # und COMMIT. Wer auf ein Future wartet, soll waehrenddessen nicht
        # aufgeben, auch wenn EINE Aufgabe den ganzen Stapel minutenlang
        # ausfuellt.
        self._active += 1
        try:
            self._run_batch_locked(conn, batch)
        finally:
            self._active -= 1

    def _run_batch_locked(self, conn: sqlite3.Connection, batch: list[_Task]) -> None:
        self._stats["batches"] += 1
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            self._report(exc, "database.writer.begin")
            for task in batch:
                task.future.set_exception(exc)
            return

        # Ergebnisse werden gesammelt und erst nach dem COMMIT ausgeliefert.
        outcomes: list[tuple[_Task, Any, BaseException | None]] = []
        for index, task in enumerate(batch):
            savepoint = "sp_" + str(index)
            try:
                conn.execute("SAVEPOINT " + savepoint)
                result = task.fn(conn)
                conn.execute("RELEASE " + savepoint)
                self._progress += 1
                outcomes.append((task, result, None))
            except Exception as exc:
                # Auch ein Fehlschlag ist Fortschritt - der Schreiber haengt nicht.
                self._progress += 1
                self._stats["failed"] += 1
                try:
                    conn.execute("ROLLBACK TO " + savepoint)
                    conn.execute("RELEASE " + savepoint)
                except sqlite3.Error:
                    pass
                self._report(exc, "database.writer", label=task.label)
                outcomes.append((task, None, exc))

        commit_error: BaseException | None = None
        try:
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            commit_error = exc
            self._report(exc, "database.writer.commit")
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass

        for task, result, error in outcomes:
            if commit_error is not None:
                # Auch die im Savepoint erfolgreichen Aufgaben sind zurueckgerollt.
                task.future.set_exception(commit_error)
            elif error is not None:
                task.future.set_exception(error)
            else:
                self._stats["tasks"] += 1
                task.future.set_result(result)

    def _report(
        self, exc: BaseException, source: str, *, critical: bool = False, label: str = ""
    ) -> None:
        if self._reporter is None:
            return
        self._reporter.report(
            AppError.from_exception(
                exc,
                source=source,
                severity=Severity.CRITICAL if critical else Severity.ERROR,
                message_key=source + ":" + type(exc).__name__,
                recoverable=not critical,
                label=label,
            )
        )
