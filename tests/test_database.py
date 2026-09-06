"""Tests der Datenbankschicht: Schema, Migration, Writer, Views."""

from __future__ import annotations

import sqlite3
import threading

import pytest

from app.database.connection import Database
from app.database.migrations import SCHEMA_VERSION, apply_migrations
from app.database.sqlscript import split_statements
from app.database.writer import DbWriter

EXPECTED_TABLES = {
    "folders", "images", "prompts", "prompt_sections", "prompt_tokens", "prompt_params",
    "tokens", "token_frequency", "thumbnails", "prompt_clusters", "cluster_members",
    "prompt_variants", "prompt_neighbors", "evolution_cache", "dirty_prompts",
    "analysis_state", "settings", "library_stats", "scan_history", "error_log",
    "system_status", "schema_migrations",
}


def test_schema_is_complete(database: Database) -> None:
    names = database.table_names()
    missing = EXPECTED_TABLES - names
    assert not missing, "fehlende Tabellen: " + ", ".join(sorted(missing))
    assert {"token_stats", "v_orphans"} <= names
    assert database.user_version == SCHEMA_VERSION


def test_wal_and_pragmas_active(database: Database) -> None:
    """WAL muss vor der ersten Leseverbindung stehen.

    Wird journal_mode erst gesetzt, wenn schon eine Verbindung offen ist, greift
    er in der Datei, aber die bestehende Verbindung meldet weiter den alten Modus.
    Der Test prueft deshalb beide Sichten.
    """
    assert database.journal_mode == "wal"
    assert str(database.scalar("PRAGMA journal_mode")).lower() == "wal"
    assert int(database.scalar("PRAGMA foreign_keys")) == 1


def test_integrity_and_foreign_keys_clean(database: Database) -> None:
    ok, detail = database.integrity_check()
    assert ok, detail
    assert database.query("PRAGMA foreign_key_check") == []


def test_migration_is_idempotent(database: Database, reporter) -> None:
    assert apply_migrations(database, reporter) == []


def test_reader_connection_is_thread_local(database: Database) -> None:
    seen: list[int] = []

    def worker() -> None:
        seen.append(id(database.reader()))

    main_id = id(database.reader())
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(seen)) == 4, "jeder Thread braucht eine eigene Verbindung"
    assert main_id not in seen


def test_split_statements_keeps_trigger_bodies_intact() -> None:
    script = """
    -- Kommentar mit Semikolon; wird entfernt
    CREATE TABLE a(x);
    CREATE TRIGGER t AFTER INSERT ON a BEGIN
        INSERT INTO a VALUES(1);
        INSERT INTO a VALUES(2);
    END;
    INSERT INTO a VALUES('mit;Semikolon');
    """
    statements = list(split_statements(script))
    assert len(statements) == 3
    assert statements[1].startswith("CREATE TRIGGER")
    assert statements[1].count("INSERT INTO") == 2, "Trigger-Body darf nicht zerschnitten werden"
    assert statements[1].rstrip().endswith("END;")
    assert "mit;Semikolon" in statements[2], "Semikolon im String darf nicht trennen"


class TestWriter:
    def test_write_and_read_back(self, database: Database, writer: DbWriter) -> None:
        writer.execute(
            "INSERT INTO folders(path, path_norm, is_root) VALUES (?, ?, 1)",
            ("Z:/lib", "z:/lib"),
        ).result(timeout=10)
        assert database.scalar("SELECT COUNT(*) FROM folders") == 1

    def test_failing_task_does_not_lose_the_batch(
        self, database: Database, writer: DbWriter, collected
    ) -> None:
        """Der Kern der Savepoint-Isolation.

        Drei Aufgaben in einem Batch, die mittlere verletzt eine Bedingung. Die
        beiden anderen muessen ueberleben - sonst reisst ein einziger fehlerhafter
        Datensatz beim Import tausend gute mit.
        """
        good_a = writer.execute(
            "INSERT INTO folders(path, path_norm) VALUES (?, ?)", ("a", "a")
        )
        bad = writer.execute(
            "INSERT INTO folders(path, path_norm, is_root) VALUES (?, ?, 99)", ("b", "b")
        )
        good_b = writer.execute(
            "INSERT INTO folders(path, path_norm) VALUES (?, ?)", ("c", "c")
        )

        good_a.result(timeout=10)
        with pytest.raises(sqlite3.IntegrityError):
            bad.result(timeout=10)
        good_b.result(timeout=10)

        paths = {r["path"] for r in database.query("SELECT path FROM folders")}
        assert paths == {"a", "c"}
        assert writer.stats["failed"] == 1
        assert any(e.source.startswith("database.writer") for e in collected)

    def test_batching_groups_many_writes(self, database: Database, writer: DbWriter) -> None:
        futures = [
            writer.execute(
                "INSERT INTO folders(path, path_norm) VALUES (?, ?)",
                ("p" + str(i), "p" + str(i)),
            )
            for i in range(300)
        ]
        for f in futures:
            f.result(timeout=30)
        assert database.scalar("SELECT COUNT(*) FROM folders") == 300
        assert writer.stats["batches"] < 300, "Schreibvorgaenge muessen gebuendelt werden"

    def test_submit_before_start_fails_fast(self, database: Database) -> None:
        idle = DbWriter(database)
        with pytest.raises(RuntimeError):
            idle.submit(lambda conn: None).result(timeout=5)

    def test_result_implies_visibility_for_readers(
        self, database: Database, writer: DbWriter
    ) -> None:
        """Regression: future.result() muss NACH dem COMMIT zurueckkehren.

        Wurde das Future noch innerhalb der Transaktion aufgeloest, sah eine
        Leseverbindung die Aenderung teilweise noch nicht - ein sporadischer
        Fehler, der jedes Read-after-Write der Anwendung betrifft.
        """
        for i in range(60):
            key = "k" + str(i)
            writer.execute(
                "INSERT INTO settings(key, value, value_type) VALUES (?, '1', 'str')", (key,)
            ).result(timeout=10)
            assert database.scalar(
                "SELECT COUNT(*) FROM settings WHERE key = ?", (key,)
            ) == 1, "nach result() muss der Leser den Wert sehen"

            writer.execute("DELETE FROM settings WHERE key = ?", (key,)).result(timeout=10)
            assert database.scalar(
                "SELECT COUNT(*) FROM settings WHERE key = ?", (key,)
            ) == 0, "nach result() muss der Leser die Loeschung sehen"

    def test_write_order_is_preserved(self, database: Database, writer: DbWriter) -> None:
        """Der Writer darf Schreibvorgaenge nicht umsortieren.

        Frueher sortierte der Batch nach Prioritaet. Ein spaeter eingereihtes
        INSERT konnte damit vor ein frueher eingereihtes DELETE rutschen und das
        Ergebnis umkehren.
        """
        writer.execute(
            "INSERT INTO settings(key, value, value_type) VALUES ('order', 'a', 'str')"
        )
        writer.execute("DELETE FROM settings WHERE key = 'order'")
        writer.execute(
            "INSERT INTO settings(key, value, value_type) VALUES ('order', 'b', 'str')"
        )
        writer.flush()
        assert database.scalar("SELECT value FROM settings WHERE key = 'order'") == "b"

    def test_commit_failure_fails_every_future_in_the_batch(
        self, database: Database, writer: DbWriter
    ) -> None:
        """Schlaegt das COMMIT fehl, ist auch scheinbar Erfolgreiches zurueckgerollt.

        Die zweite Aufgabe rollt die Transaktion des Batches von innen zurueck.
        Das anschliessende COMMIT scheitert, und die erste Aufgabe darf ihr
        Future dann nicht als Erfolg melden - ihre Zeile existiert nicht.
        """
        ok = writer.submit(
            lambda conn: conn.execute(
                "INSERT INTO settings(key, value, value_type) VALUES ('x', '1', 'str')"
            )
        )
        writer.submit(lambda conn: conn.execute("ROLLBACK"))

        with pytest.raises(Exception):
            ok.result(timeout=10)
        assert database.scalar("SELECT COUNT(*) FROM settings WHERE key = 'x'") == 0


class TestViews:
    def _seed_tokens(self, database: Database, writer: DbWriter) -> None:
        writer.execute(
            "INSERT INTO library_stats(key, int_value) VALUES ('prompt_count', 100)"
            " ON CONFLICT(key) DO UPDATE SET int_value = 100"
        ).result(timeout=10)
        writer.execute(
            "INSERT INTO tokens(id, kind, token, token_hash) VALUES"
            " (1, 'sentence', 'common line', 'h1'), (2, 'sentence', 'rare line', 'h2')"
        ).result(timeout=10)
        writer.execute(
            "INSERT INTO token_frequency(token_id, doc_count, total_count) VALUES"
            " (1, 100, 120), (2, 1, 1)"
        ).result(timeout=10)

    def test_token_stats_separates_commonness_from_idf(
        self, database: Database, writer: DbWriter
    ) -> None:
        """commonness fuer Basisprompterkennung, idf fuer Hervorhebung.

        Die Spezifikationsdateien nutzen ein einziges Feld "importance" fuer beides.
        Das ist widerspruechlich: Basisprompts bestehen aus haeufigen Segmenten,
        hervorheben will man die seltenen.

        Der Nenner ist prompt_count, weil doc_count Prompt-Zeilen zaehlt (Schema v2).
        """
        self._seed_tokens(database, writer)
        rows = {
            r["token_id"]: r
            for r in database.query("SELECT token_id, commonness, idf FROM token_stats")
        }
        assert rows[1]["commonness"] == pytest.approx(1.0)
        assert rows[1]["idf"] == pytest.approx(0.0)
        assert rows[2]["commonness"] == pytest.approx(0.01)
        assert rows[2]["idf"] > 4.0
        assert rows[2]["idf"] > rows[1]["idf"]

    def test_token_stats_survives_empty_library(self, database: Database) -> None:
        """Keine Division durch Null bei leerer Bibliothek."""
        assert database.query("SELECT * FROM token_stats") == []

    def test_orphans_view_reports_both_directions(
        self, database: Database, writer: DbWriter
    ) -> None:
        writer.execute(
            "INSERT INTO folders(id, path, path_norm) VALUES (1, 'Z:/f', 'z:/f')"
        ).result(timeout=10)
        writer.execute(
            "INSERT INTO prompts(id, folder_id, filename, full_path, stem_norm, prompt_text)"
            " VALUES (1, 1, 'a.txt', 'Z:/f/a.txt', 'a', 'text')"
        ).result(timeout=10)
        writer.execute(
            "INSERT INTO images(id, folder_id, filename, full_path, stem_norm, extension)"
            " VALUES (1, 1, 'b.jpg', 'Z:/f/b.jpg', 'b', '.jpg')"
        ).result(timeout=10)

        kinds = {r["kind"] for r in database.query("SELECT kind FROM v_orphans")}
        assert kinds == {"image_only", "prompt_only"}


class TestFtsSync:
    def _insert_prompt(self, writer: DbWriter, text: str) -> None:
        writer.execute(
            "INSERT INTO folders(id, path, path_norm) VALUES (1, 'Z:/f', 'z:/f')"
        ).result(timeout=10)
        writer.execute(
            "INSERT INTO prompts(id, folder_id, filename, full_path, stem_norm, prompt_text)"
            " VALUES (1, 1, 'a.txt', 'Z:/f/a.txt', 'a', ?)",
            (text,),
        ).result(timeout=10)

    def test_insert_is_indexed(self, database: Database, writer: DbWriter) -> None:
        self._insert_prompt(writer, "warm golden sunlight dominates the scene")
        rows = database.query("SELECT rowid FROM prompts_fts WHERE prompts_fts MATCH ?", ("golden",))
        assert [r["rowid"] for r in rows] == [1]

    def test_phrase_search_works(self, database: Database, writer: DbWriter) -> None:
        self._insert_prompt(writer, "cinematic editorial portrait of a young woman")
        rows = database.query(
            "SELECT rowid FROM prompts_fts WHERE prompts_fts MATCH ?", ('"editorial portrait"',)
        )
        assert len(rows) == 1

    def test_update_removes_stale_index_entry(self, database: Database, writer: DbWriter) -> None:
        self._insert_prompt(writer, "warm golden sunlight")
        writer.execute("UPDATE prompts SET prompt_text = ? WHERE id = 1", ("cold blue moonlight",)).result(timeout=10)
        assert database.query("SELECT rowid FROM prompts_fts WHERE prompts_fts MATCH ?", ("golden",)) == []
        assert len(database.query("SELECT rowid FROM prompts_fts WHERE prompts_fts MATCH ?", ("moonlight",))) == 1

    def test_metadata_update_does_not_touch_the_index(
        self, database: Database, writer: DbWriter
    ) -> None:
        """Der Trigger haengt an UPDATE OF prompt_text.

        Ein Statusfeld zu aktualisieren darf keine Volltext-Neuindizierung ausloesen,
        sonst kostet jeder Scan das Vielfache.
        """
        self._insert_prompt(writer, "warm golden sunlight")
        before = database.scalar("SELECT COUNT(*) FROM prompts_fts")
        writer.execute("UPDATE prompts SET parse_version = 7 WHERE id = 1").result(timeout=10)
        assert database.scalar("SELECT COUNT(*) FROM prompts_fts") == before
        assert len(database.query("SELECT rowid FROM prompts_fts WHERE prompts_fts MATCH ?", ("golden",))) == 1

    def test_delete_removes_index_entry(self, database: Database, writer: DbWriter) -> None:
        self._insert_prompt(writer, "warm golden sunlight")
        writer.execute("DELETE FROM prompts WHERE id = 1").result(timeout=10)
        assert database.query("SELECT rowid FROM prompts_fts WHERE prompts_fts MATCH ?", ("golden",)) == []


class TestWartenAufFortschritt:
    """`await_result` begrenzt den STILLSTAND, nicht die Gesamtdauer.

    Im Betrieb gemeldet: der Abgleich brach dreimal mit TimeoutError in
    `folders.upsert` ab. Nicht weil der Schreiber haing, sondern weil vor der
    Aufgabe ein minutenlanger Vorgang der Analyse in der Warteschlange stand.
    Schlimmer noch - eine abgelaufene Frist storniert nichts: die Aufgabe lief
    danach durch, der Lauf war aber abgebrochen.
    """

    def test_langer_vorlauf_laesst_das_warten_nicht_scheitern(self, writer) -> None:
        """Der gemeldete Fall: viele Aufgaben davor, jede fuer sich kurz."""
        import time

        def langsam(_conn):
            time.sleep(0.02)
            return 1

        for _ in range(40):
            writer.submit(langsam, label="vorlauf")
        letzte = writer.submit(lambda conn: "fertig", label="ziel")

        # Die Gesamtdauer liegt deutlich ueber der Stillstandsfrist.
        assert writer.await_result(letzte, idle_timeout=0.3, poll=0.05) == "fertig"

    def test_eine_einzelne_lange_aufgabe_ist_kein_stillstand(self, writer) -> None:
        """Der Fall, an dem die erste Fassung noch scheiterte.

        `stats["tasks"]` und der Fortschrittszaehler wachsen erst, wenn eine
        Aufgabe FERTIG ist. Das Ersetzen aller Familien schreibt aber ueber
        hunderttausend Zeilen in EINEM Zug - minutenlang steht der Zaehler
        still, obwohl der Schreiber durchgehend arbeitet. Alles, was dahinter
        wartet, gab auf.

        Deshalb gibt es ein drittes Lebenszeichen: `is_working`.
        """
        import threading

        losgelassen = threading.Event()
        writer.submit(lambda conn: losgelassen.wait(timeout=5.0), label="lang")
        ziel = writer.submit(lambda conn: "fertig", label="ziel")
        try:
            # Die Stillstandsfrist ist um ein Vielfaches kuerzer als die
            # laufende Aufgabe - und trotzdem darf nicht abgebrochen werden.
            treffer = threading.Timer(0.6, losgelassen.set)
            treffer.start()
            assert writer.await_result(ziel, idle_timeout=0.2, poll=0.05) == "fertig"
        finally:
            losgelassen.set()

    def test_der_schreiber_arbeitet_sichtbar(self, writer) -> None:
        """`is_working` unterscheidet "arbeitet" von "haengt"."""
        import threading
        import time

        assert not writer.is_working
        losgelassen = threading.Event()
        laeuft = threading.Event()

        def lang(_conn):
            laeuft.set()
            losgelassen.wait(timeout=5.0)
            return 1

        ziel = writer.submit(lang, label="lang")
        assert laeuft.wait(timeout=5.0)
        time.sleep(0.05)
        assert writer.is_working, "Der laufende Stapel wird nicht gemeldet"
        losgelassen.set()
        writer.await_result(ziel, idle_timeout=5.0)
        time.sleep(0.05)
        assert not writer.is_working

    def test_die_obergrenze_greift_bei_einem_echten_haenger(self, writer) -> None:
        """Eine Aufgabe, die nie zurueckkehrt, darf nicht ewig binden.

        `is_working` allein wuerde hier fuer immer "arbeitet" melden - deshalb
        gibt es zusaetzlich eine absolute Obergrenze.
        """
        import threading
        from concurrent.futures import TimeoutError as FutureTimeout

        import pytest as _pytest

        blockade = threading.Event()
        writer.submit(lambda conn: blockade.wait(timeout=30.0), label="haenger")
        ziel = writer.submit(lambda conn: "nie", label="ziel")
        try:
            with _pytest.raises(FutureTimeout):
                writer.await_result(ziel, idle_timeout=5.0, poll=0.05, max_wait=0.4)
        finally:
            blockade.set()
            writer.await_result(ziel, idle_timeout=5.0)

    def test_fertiges_future_kehrt_sofort_zurueck(self, writer) -> None:
        fertig = writer.submit(lambda conn: 42, label="sofort")
        writer.await_result(fertig, idle_timeout=5.0)
        assert writer.await_result(fertig, idle_timeout=0.05, poll=0.01) == 42

    def test_ein_fehler_kommt_unveraendert_durch(self, writer) -> None:
        import pytest as _pytest

        def wirft(_conn):
            raise ValueError("kaputt")

        with _pytest.raises(ValueError):
            writer.await_result(writer.submit(wirft, label="fehler"), idle_timeout=5.0)

    def test_der_scanpfad_wartet_nicht_mehr_auf_die_uhr(self) -> None:
        """Die Stellen, an denen der Abgleich abbrach, tragen keine Frist mehr."""
        import pathlib

        wurzel = pathlib.Path(__file__).resolve().parent.parent / "app" / "repositories"
        for name in ("folder_repo.py", "image_repo.py", "prompt_repo.py"):
            quelle = (wurzel / name).read_text(encoding="utf-8")
            assert ".result(timeout=" not in quelle, (
                name + " wartet wieder mit fester Frist - genau daran ist der"
                " Abgleich im Betrieb dreimal gescheitert"
            )
            assert "await_result" in quelle


class TestFehlerAbhaken:
    """Der Eintrag, der sich nicht abhaken liess.

    Gemeldet: der oberste Eintrag der Fehlerliste kam nach jedem "Als erledigt
    markieren" wieder, und im Protokoll lief ein IntegrityError auf.

    Zwei unabhaengige Fehler mit demselben Symptom - beide werden hier
    festgehalten.
    """

    @pytest.fixture
    def repo(self, database, writer):
        from app.repositories.error_repo import ErrorRepository

        return ErrorRepository(database, writer)

    def _fehler(self, quelle="jobs.Test", schluessel="test:Boom", meldung="Boom"):
        from app.core.errors import AppError, Severity

        return AppError(
            source=quelle,
            message=meldung,
            severity=Severity.ERROR,
            message_key=schluessel,
        )

    def _offen(self, repo):
        from app.core.errors import Severity

        return repo.open_entries(min_severity=Severity.WARNING)

    def test_einmal_abhaken(self, repo, writer) -> None:
        repo.record(self._fehler(), wait=True)
        eintraege = self._offen(repo)
        assert len(eintraege) == 1

        repo.resolve(eintraege[0].id)
        assert self._offen(repo) == []

    def test_zweimal_abhaken_derselben_fehlerart(self, repo, writer) -> None:
        """DER gemeldete Fall.

        Nach dem ersten Abhaken existiert eine Zeile mit resolved=1. Tritt der
        Fehler erneut auf, entsteht daneben eine offene Zeile. Ein blosses
        `SET resolved = 1` kollidiert dann mit dem Eindeutigkeitsschluessel
        (severity, source, message_key, resolved) - der Eintrag blieb stehen.
        """
        repo.record(self._fehler(), wait=True)
        repo.resolve(self._offen(repo)[0].id)
        assert self._offen(repo) == []

        # Derselbe Fehler noch einmal.
        repo.record(self._fehler(), wait=True)
        offen = self._offen(repo)
        assert len(offen) == 1, "Nach dem Abhaken muss eine NEUE Zeile entstehen"

        repo.resolve(offen[0].id)
        assert self._offen(repo) == [], (
            "Der Eintrag liess sich nicht abhaken - genau der gemeldete Fehler"
        )

    def test_dreimal_hintereinander(self, repo, writer) -> None:
        """Auch beim dritten Mal, und ohne dass Zeilen verlorengehen."""
        for durchgang in range(3):
            repo.record(self._fehler(), wait=True)
            offen = self._offen(repo)
            assert len(offen) == 1, "Durchgang " + str(durchgang)
            repo.resolve(offen[0].id)
        assert self._offen(repo) == []

    def test_die_anzahl_geht_beim_zusammenfuehren_nicht_verloren(
        self, repo, writer, database
    ) -> None:
        """Zusammenfuehren heisst addieren, nicht ueberschreiben."""
        for _ in range(3):
            repo.record(self._fehler(), wait=True)
        repo.resolve(self._offen(repo)[0].id)

        for _ in range(2):
            repo.record(self._fehler(), wait=True)
        repo.resolve(self._offen(repo)[0].id)

        zeilen = database.query(
            "SELECT count, resolved FROM error_log WHERE source = ?", ("jobs.Test",)
        )
        assert len(zeilen) == 1, "Es darf genau eine abgehakte Zeile uebrigbleiben"
        assert zeilen[0]["count"] == 5, "3 + 2 Vorkommen erwartet"
        assert zeilen[0]["resolved"] == 1

    def test_abhaken_wartet_auf_das_schreiben(self, repo, writer) -> None:
        """Der zweite Fehler mit demselben Symptom.

        Der Dialog liest die Liste unmittelbar nach dem Abhaken neu ein. Lief
        das Schreiben nebenher, las er den Stand VOR dem Commit - und der
        Eintrag stand wieder da, auch wenn alles funktioniert hatte.
        """
        repo.record(self._fehler(), wait=True)
        repo.resolve(self._offen(repo)[0].id)
        # Ohne Zutun, ohne flush: unmittelbar danach muss die Liste leer sein.
        assert self._offen(repo) == []

    def test_verschiedene_fehlerarten_bleiben_getrennt(self, repo, writer) -> None:
        repo.record(self._fehler(schluessel="a:Eins"), wait=True)
        repo.record(self._fehler(schluessel="b:Zwei"), wait=True)
        offen = self._offen(repo)
        assert len(offen) == 2

        repo.resolve(offen[0].id)
        assert len(self._offen(repo)) == 1

    def test_zweimal_derselbe_eintrag_ist_harmlos(self, repo, writer) -> None:
        """Zwei Klicks sollen nicht anders ausgehen als einer."""
        repo.record(self._fehler(), wait=True)
        entry_id = self._offen(repo)[0].id
        repo.resolve(entry_id)
        repo.resolve(entry_id)
        assert self._offen(repo) == []

    def test_quelle_abhaken_kollidiert_nicht(self, repo, writer) -> None:
        """resolve_source lief in dieselbe Falle - und riss mehr mit.

        Ein gemeinsames UPDATE ueber alle Zeilen einer Quelle waere bei EINER
        Kollision vollstaendig wirkungslos geblieben, nicht nur fuer die eine
        Zeile.
        """
        repo.record(self._fehler(schluessel="a:Eins"), wait=True)
        repo.resolve(self._offen(repo)[0].id)

        repo.record(self._fehler(schluessel="a:Eins"), wait=True)
        repo.record(self._fehler(schluessel="b:Zwei"), wait=True)
        assert len(self._offen(repo)) == 2

        repo.resolve_source("jobs.Test", wait=True)
        assert self._offen(repo) == []


class TestAbhakenFriertNichtEin:
    """Gemeldet: "beim Druecken des Buttons 'als erledigt markieren' haengt
    sich das ganze Programm auf."

    Der Knopf rief `resolve` mit `wait=True`, und gewartet wird ueber
    `DbWriter.await_result`. Das misst absichtlich STILLSTAND statt
    Gesamtdauer - solange der Schreibfaden vorankommt oder in einem Stapel
    steckt, wartet es weiter, bis zu `MAX_WAIT`. Das ist im Hintergrund genau
    richtig und im GUI-Faden genau falsch: dort bedeutet es ein Fenster, das
    eine Viertelstunde lang auf nichts mehr reagiert.

    Der Weg fuer die Oberflaeche ist deshalb `wait=False`. Die Zeile
    verschwindet sofort aus der Liste, die Aufgabe steht in der
    Warteschlange - FIFO, und `Application.shutdown` leert sie.
    """

    @pytest.fixture
    def repo(self, database, writer):
        from app.repositories.error_repo import ErrorRepository

        return ErrorRepository(database, writer)

    def _eintrag(self, repo, writer):
        from app.core.errors import AppError, Severity

        repo.record(
            AppError(
                source="jobs.Test",
                message="Boom",
                severity=Severity.ERROR,
                message_key="test:Boom",
            ),
            wait=True,
        )
        return repo.open_entries(min_severity=Severity.WARNING)[0]

    def test_ohne_warten_kehrt_es_sofort_zurueck(self, repo, writer) -> None:
        import threading
        import time

        eintrag = self._eintrag(repo, writer)

        blockade = threading.Event()
        writer.submit(lambda conn: blockade.wait(timeout=30.0), label="haenger")
        try:
            began = time.monotonic()
            repo.resolve(eintrag.id, wait=False)
            gebraucht = time.monotonic() - began
        finally:
            blockade.set()

        assert gebraucht < 5.0, (
            "resolve(wait=False) hat auf den Schreibfaden gewartet - im"
            " GUI-Faden ist das das eingefrorene Fenster"
        )

    def test_die_aenderung_geht_dabei_nicht_verloren(self, repo, writer) -> None:
        import threading

        from app.core.errors import Severity

        eintrag = self._eintrag(repo, writer)

        blockade = threading.Event()
        writer.submit(lambda conn: blockade.wait(timeout=30.0), label="haenger")
        repo.resolve(eintrag.id, wait=False)
        blockade.set()
        writer.flush()

        assert repo.open_entries(min_severity=Severity.WARNING) == []


class TestDasFehlerprotokollMeldetSichNichtSelbst:
    """Ein gescheiterter Schreibvorgang AM Fehlerprotokoll darf nicht ins
    Fehlerprotokoll.

    Sonst schliesst sich ein Kreis, der sich nicht von selbst oeffnet: der
    Schreibfaden scheitert an einer error_log-Aufgabe, meldet den Fehlschlag,
    die Meldung reiht die naechste error_log-Aufgabe ein, die aus demselben
    Grund scheitert. Der Schreibfaden hat danach dauerhaft zu tun, und jeder
    andere Schreibvorgang - eine Einstellung zum Beispiel - wartet dahinter.
    Von aussen sieht das aus wie ein haengendes Programm.
    """

    def _sink(self, database, writer):
        from app.repositories.error_repo import DatabaseSink, ErrorRepository

        repo = ErrorRepository(database, writer)
        return DatabaseSink(repo), repo

    def _fehler(self, source, label):
        from app.core.errors import AppError, Severity

        return AppError(
            severity=Severity.ERROR,
            source=source,
            message="IntegrityError",
            message_key=source + ":IntegrityError",
            context={"label": label},
        )

    def test_der_kreis_ist_unterbrochen(self, database, writer) -> None:
        sink, repo = self._sink(database, writer)
        sink.handle(self._fehler("database.writer", "error_log"))
        writer.flush()
        assert database.scalar("SELECT COUNT(*) FROM error_log") == 0

    def test_andere_schreibfehler_werden_weiter_protokolliert(
        self, database, writer
    ) -> None:
        """Nur die eine Rueckkopplung wird unterbunden, nicht die Meldung."""
        sink, repo = self._sink(database, writer)
        sink.handle(self._fehler("database.writer", "images.upsert"))
        writer.flush()
        assert database.scalar("SELECT COUNT(*) FROM error_log") == 1
