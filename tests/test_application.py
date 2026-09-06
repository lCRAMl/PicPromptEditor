"""Tests der Startsequenz und der Fehlerablage."""

from __future__ import annotations

import sqlite3

from app.application import Application
from app.config.bootstrap import BootstrapConfig, save_bootstrap
from app.core.errors import Severity
from app.database.migrations import SCHEMA_VERSION


def _bootstrap(tmp_path, name: str = "bootstrap.json"):
    target = tmp_path / name
    save_bootstrap(
        BootstrapConfig(
            db_path=str(tmp_path / "library.db"),
            thumbnail_cache_dir=str(tmp_path / "thumbnails"),
            log_dir=str(tmp_path / "logs"),
            log_level="CRITICAL",
        ),
        target,
    )
    return target


class TestStartup:
    def test_cold_start_creates_everything(self, tmp_path) -> None:
        app = Application(bootstrap_file=_bootstrap(tmp_path))
        try:
            result = app.startup()
            assert result.ok
            assert result.schema_version == SCHEMA_VERSION
            assert result.applied_migrations == [
                "1:initial",
                "2:token_stats_denominator",
            ]
            assert (tmp_path / "library.db").exists()
            assert (tmp_path / "thumbnails").is_dir()
            assert app.log_file is not None and app.log_file.exists()
        finally:
            app.shutdown()

    def test_second_start_applies_no_migrations(self, tmp_path) -> None:
        bootstrap = _bootstrap(tmp_path)
        first = Application(bootstrap_file=bootstrap)
        first.startup()
        first.shutdown()

        second = Application(bootstrap_file=bootstrap)
        try:
            result = second.startup()
            assert result.ok
            assert result.applied_migrations == []
        finally:
            second.shutdown()

    def test_corrupt_database_requests_recovery_instead_of_crashing(self, tmp_path) -> None:
        """Spezifikation 08: bei unbrauchbarer Datenbank muss ein
        Wiederherstellungsmodus angeboten werden, nicht ein Absturz."""
        bootstrap = _bootstrap(tmp_path)
        first = Application(bootstrap_file=bootstrap)
        first.startup()
        first.shutdown()

        db_file = tmp_path / "library.db"
        data = bytearray(db_file.read_bytes())
        for offset in range(200, min(len(data), 6000)):
            data[offset] = (data[offset] + 97) % 256
        db_file.write_bytes(bytes(data))

        app = Application(bootstrap_file=bootstrap)
        try:
            result = app.startup()
            assert not result.ok
            assert result.recovery_required
            assert "beschaedigt" in result.message.lower() or "malformed" in result.message.lower()
        finally:
            app.shutdown()

    def test_status_report_is_complete(self, application: Application) -> None:
        report = application.status_report()
        assert report["started"] is True
        assert report["schema_version"] == SCHEMA_VERSION
        assert "counters" in report and "prompt_count" in report["counters"]
        assert "system_status" in report

    def test_shutdown_is_idempotent(self, tmp_path) -> None:
        app = Application(bootstrap_file=_bootstrap(tmp_path))
        app.startup()
        app.shutdown()
        app.shutdown()


class TestErrorPipeline:
    def test_warning_reaches_the_database(self, application: Application) -> None:
        application.reporter.warning("scanner", "Ordner nicht erreichbar")
        application.writer.flush()
        entries = application.errors.open_entries()
        assert any(e.source == "scanner" for e in entries)

    def test_info_is_not_persisted(self, application: Application) -> None:
        """Info-Meldungen gehoeren ins Log, nicht in die Problemliste."""
        application.reporter.info("scanner", "Scan gestartet")
        application.writer.flush()
        assert not any(e.source == "scanner" for e in application.errors.open_entries())

    def test_repeated_errors_aggregate_instead_of_flooding(
        self, application: Application
    ) -> None:
        """Ein NAS-Ausfall waehrend eines Scans darf keine tausend Zeilen erzeugen."""
        for i in range(500):
            application.reporter.error(
                "filesystem",
                "Datei " + str(i) + " nicht lesbar",
                message_key="filesystem:unreachable",
            )
        application.writer.flush()

        rows = application.database.query(
            "SELECT count, message FROM error_log WHERE message_key = 'filesystem:unreachable'"
        )
        assert len(rows) == 1, "gleiche Fehlerart muss eine einzige Zeile bleiben"
        assert rows[0]["count"] == 500

    def test_distinct_errors_stay_separate(self, application: Application) -> None:
        application.reporter.error("a", "eins", message_key="a:1")
        application.reporter.error("b", "zwei", message_key="b:2")
        application.writer.flush()
        keys = {
            r["message_key"]
            for r in application.database.query("SELECT message_key FROM error_log")
        }
        assert {"a:1", "b:2"} <= keys

    def test_resolving_allows_a_fresh_entry(self, application: Application) -> None:
        """resolved ist Teil des Aggregationsschluessels: nach dem Abhaken
        beginnt derselbe Fehler eine neue Zeile."""
        application.reporter.error("x", "boom", message_key="x:boom")
        application.writer.flush()
        entry = application.errors.open_entries()[0]
        application.errors.resolve(entry.id)
        application.writer.flush()

        application.reporter.error("x", "boom", message_key="x:boom")
        application.writer.flush()
        rows = application.database.query(
            "SELECT resolved, count FROM error_log WHERE message_key = 'x:boom' ORDER BY resolved"
        )
        assert len(rows) == 2

    def test_counts_by_severity(self, application: Application) -> None:
        application.reporter.warning("a", "w", message_key="a:w")
        application.reporter.error("b", "e", message_key="b:e")
        application.writer.flush()
        counts = application.errors.counts_by_severity()
        assert counts.get(Severity.WARNING, 0) >= 1
        assert counts.get(Severity.ERROR, 0) >= 1


class TestStatsRepository:
    def test_counters_roundtrip(self, application: Application) -> None:
        application.stats.set_counter("image_count", 42, wait=True)
        assert application.stats.counter("image_count") == 42

    def test_bump_counter_accumulates(self, application: Application) -> None:
        application.stats.set_counter("prompt_count", 10, wait=True)
        application.stats.bump_counter("prompt_count", 5, wait=True)
        application.stats.bump_counter("prompt_count", -3, wait=True)
        assert application.stats.counter("prompt_count") == 12

    def test_dirty_queue_claim_is_exhaustive(self, application: Application) -> None:
        writer, db = application.writer, application.database
        writer.execute(
            "INSERT INTO folders(id, path, path_norm) VALUES (1, 'Z:/f', 'z:/f')"
        ).result(timeout=10)
        rows = [
            (i, 1, "p" + str(i) + ".txt", "Z:/f/p" + str(i) + ".txt", "p" + str(i))
            for i in range(1, 26)
        ]
        writer.executemany(
            "INSERT INTO prompts(id, folder_id, filename, full_path, stem_norm)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        ).result(timeout=10)

        application.stats.mark_dirty(list(range(1, 26)), "parse", "import")
        writer.flush()
        assert application.stats.dirty_count("parse") == 25

        first = application.stats.claim_dirty("parse", 10)
        second = application.stats.claim_dirty("parse", 10)
        third = application.stats.claim_dirty("parse", 10)

        assert len(first) == 10 and len(second) == 10 and len(third) == 5
        assert len(set(first) | set(second) | set(third)) == 25, "kein Prompt doppelt"
        assert application.stats.dirty_count("parse") == 0

    def test_claim_only_takes_its_own_stage(self, application: Application) -> None:
        writer = application.writer
        writer.execute(
            "INSERT INTO folders(id, path, path_norm) VALUES (1, 'Z:/f', 'z:/f')"
        ).result(timeout=10)
        writer.execute(
            "INSERT INTO prompts(id, folder_id, filename, full_path, stem_norm)"
            " VALUES (1, 1, 'a.txt', 'Z:/f/a.txt', 'a')"
        ).result(timeout=10)
        application.stats.mark_dirty([1], "parse")
        application.stats.mark_dirty([1], "similarity")
        writer.flush()

        assert application.stats.claim_dirty("parse", 10) == [1]
        assert application.stats.dirty_count("similarity") == 1

    def test_bump_algo_version_invalidates_a_stage(self, application: Application) -> None:
        before = application.stats.stage_state("similarity")["algo_version"]
        after = application.stats.bump_algo_version("similarity")
        assert after == before + 1

    def test_recount_from_tables(self, application: Application) -> None:
        writer = application.writer
        writer.execute(
            "INSERT INTO folders(id, path, path_norm) VALUES (1, 'Z:/f', 'z:/f')"
        ).result(timeout=10)
        writer.executemany(
            "INSERT INTO prompts(id, folder_id, filename, full_path, stem_norm, text_hash)"
            " VALUES (?, 1, ?, ?, ?, ?)",
            [
                (1, "a.txt", "Z:/f/a.txt", "a", "hash-gleich"),
                (2, "b.txt", "Z:/f/b.txt", "b", "hash-gleich"),
                (3, "c.txt", "Z:/f/c.txt", "c", "hash-anders"),
            ],
        ).result(timeout=10)

        counters = application.stats.recount_from_tables()
        assert counters["prompt_count"] == 3
        assert counters["distinct_prompt_texts"] == 2, (
            "die Analyse-Deduplizierung zaehlt eindeutige Texte, nicht Dateien"
        )


class TestReglerOhneWirkung:
    """Einstellungen, die im Dialog standen und nichts taten.

    Beim Durchsehen fielen siebzehn davon auf. Zwei liessen sich unmittelbar
    anschliessen, weil der Baustein dahinter schon existierte und nur nie
    gerufen wurde. Diese Tests halten sie fest, damit sie nicht wieder
    abfallen - ein Regler ohne Wirkung ist schlimmer als keiner, weil der
    Benutzer ihn verstellt und sich auf die Wirkung verlaesst.
    """

    def test_die_stapelgroesse_kommt_beim_schreiber_an(
        self, application: Application
    ) -> None:
        erwartet = application.settings.get_int("jobs.db_write_batch")
        assert application.writer._batch_size == erwartet

    def test_eine_geaenderte_stapelgroesse_wirkt_beim_naechsten_start(
        self, application: Application
    ) -> None:
        application.settings.set("jobs.db_write_batch", 32)
        application.writer.flush()
        application.writer.configure(
            batch_size=application.settings.get_int("jobs.db_write_batch")
        )
        assert application.writer._batch_size == 32

    def test_das_fehlerprotokoll_wird_begrenzt(self, application: Application) -> None:
        """`system.error_log_max_rows` war eine Zahl, die niemand las."""
        grenze = 100   # der kleinste zulaessige Wert der Einstellung
        application.settings.set("system.error_log_max_rows", grenze)
        for i in range(grenze + 20):
            application.reporter.error("quelle" + str(i), "boom", message_key="k:" + str(i))
        application.writer.flush()
        assert application.database.scalar("SELECT COUNT(*) FROM error_log") > grenze

        application.errors.prune(grenze)
        application.writer.flush()

        assert application.database.scalar("SELECT COUNT(*) FROM error_log") == grenze

    def test_der_start_begrenzt_das_protokoll_von_selbst(
        self, application: Application
    ) -> None:
        """Der Aufruf muss in startup() stehen, nicht nur moeglich sein."""
        import pathlib

        quelle = (
            pathlib.Path(__file__).resolve().parent.parent / "app" / "application.py"
        ).read_text(encoding="utf-8")
        assert 'self.errors.prune(self.settings.get_int("system.error_log_max_rows"))' in quelle
