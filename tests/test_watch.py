"""Betriebsfestigkeit: Abgleichprotokoll und selbsttaetige Ueberwachung.

Die Entscheidungslogik der Ueberwachung wird Qt-frei in test_change_tracker.py
geprueft. Hier geht es um die Verdrahtung: schreibt der Dienst wirklich ins
Protokoll, loest der Takt wirklich einen Scan aus, und - der wichtigste Punkt -
blockiert die NAS-Pruefung den GUI-Thread nicht.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.application import Application
from app.database.connection import Database
from app.database.writer import DbWriter
from app.repositories.scan_repo import MAX_ROWS, ScanRepository
from tests.conftest import pump_until


class TestScanRepository:
    def test_eintrag_ist_nach_dem_future_lesbar(
        self, database: Database, writer: DbWriter
    ) -> None:
        scans = ScanRepository(database, writer)
        future = scans.record(
            duration=1.5,
            scan_type="incremental",
            new_files=6,
            changed_files=1,
            deleted_files=2,
            errors=0,
            status="ok",
            note="6 neu",
        )
        future.result(timeout=10)

        records = scans.recent()
        assert len(records) == 1
        record = records[0]
        assert record.scan_type == "incremental"
        assert record.duration == pytest.approx(1.5)
        assert (record.new_files, record.changed_files, record.deleted_files) == (6, 1, 2)
        assert record.status == "ok"
        assert record.note == "6 neu"
        assert record.scan_time > 0

    def test_neueste_zuerst(self, database: Database, writer: DbWriter) -> None:
        scans = ScanRepository(database, writer)
        for index in range(5):
            future = scans.record(
                duration=float(index),
                scan_type="incremental",
                new_files=index,
                changed_files=0,
                deleted_files=0,
                errors=0,
                status="ok",
            )
            future.result(timeout=10)

        durations = [r.duration for r in scans.recent()]
        assert durations == sorted(durations, reverse=True)

    def test_protokoll_waechst_nicht_unbegrenzt(
        self, database: Database, writer: DbWriter
    ) -> None:
        """Ein Takt alle funfzehn Minuten schreibt sonst Zehntausende Zeilen."""
        scans = ScanRepository(database, writer)
        last = None
        for index in range(MAX_ROWS + 25):
            last = scans.record(
                duration=0.1,
                scan_type="incremental",
                new_files=index,
                changed_files=0,
                deleted_files=0,
                errors=0,
                status="ok",
            )
        last.result(timeout=60)

        assert scans.count() == MAX_ROWS
        # Gekuerzt wird am alten Ende, der juengste Lauf bleibt erhalten.
        assert scans.recent(1)[0].new_files == MAX_ROWS + 24

    def test_letzter_erfolg_ueberspringt_fehlschlaege(
        self, database: Database, writer: DbWriter
    ) -> None:
        scans = ScanRepository(database, writer)
        scans.record(
            duration=2.0,
            scan_type="full",
            new_files=10,
            changed_files=0,
            deleted_files=0,
            errors=0,
            status="ok",
            note="der gute Lauf",
        )
        scans.record(
            duration=0.1,
            scan_type="incremental",
            new_files=0,
            changed_files=0,
            deleted_files=0,
            errors=3,
            status="error",
        ).result(timeout=10)
        scans.record(
            duration=0.1,
            scan_type="incremental",
            new_files=0,
            changed_files=0,
            deleted_files=0,
            errors=0,
            status="cancelled",
        ).result(timeout=10)

        last = scans.last_successful()
        assert last is not None
        assert last.note == "der gute Lauf"

    def test_ohne_erfolg_kein_ergebnis(self, database: Database, writer: DbWriter) -> None:
        scans = ScanRepository(database, writer)
        assert scans.last_successful() is None
        assert scans.recent() == []


def build(application: Application, library, **settings) -> Application:
    """Startet die Dienste mit einer Bibliothek und Testeinstellungen."""
    application.settings.set("library.roots", [str(library)])
    application.settings.set("index.scan_on_startup", False)
    for key, value in settings.items():
        application.settings.set(key, value)
    application.build_services()
    return application


@pytest.fixture
def library(tmp_path):
    from PIL import Image

    root = tmp_path / "lib"
    root.mkdir()
    for index in range(3):
        Image.new("RGB", (16, 16), (index * 60, 80, 140)).save(root / ("p%d.jpg" % index))
        (root / ("p%d.txt" % index)).write_text(
            "Ein Satz.\nVariante %d.\n" % index, encoding="utf-8"
        )
    return root


class TestScanHistoryWiring:
    def test_jeder_lauf_landet_im_protokoll(self, qapp, application, library) -> None:
        app = build(application, library)
        finished: list = []
        app.index_service.scan_finished.connect(finished.append)

        app.index_service.start_scan()
        assert pump_until(qapp, lambda: finished, 60000), "Scan wurde nicht fertig"

        # Unmittelbar nach dem Signal lesen - genau das muss der Dienst koennen.
        history = app.index_service.scan_history()
        assert len(history) == 1
        assert history[0].scan_type == "incremental"
        assert history[0].status == "ok"
        assert history[0].new_files == 6
        assert app.index_service.last_successful_scan() is not None

    def test_neuindizierung_wird_als_solche_protokolliert(
        self, qapp, application, library
    ) -> None:
        app = build(application, library)
        finished: list = []
        app.index_service.scan_finished.connect(finished.append)

        app.index_service.start_scan()
        assert pump_until(qapp, lambda: finished, 60000)
        app.index_service.start_full_reindex()
        assert pump_until(qapp, lambda: len(finished) == 2, 60000)

        arten = [r.scan_type for r in app.index_service.scan_history()]
        assert arten[0] == "full"
        assert arten[1] == "incremental"

    def test_scan_started_meldet_die_art(self, qapp, application, library) -> None:
        app = build(application, library)
        started: list = []
        finished: list = []
        app.index_service.scan_started.connect(started.append)
        app.index_service.scan_finished.connect(finished.append)

        app.index_service.start_scan()
        assert started == ["incremental"]
        assert pump_until(qapp, lambda: finished, 60000)


class TestWatchService:
    def test_wurzeln_werden_ueberwacht(self, qapp, application, library) -> None:
        app = build(application, library)
        app.watch_service.start()
        try:
            assert len(app.watch_service.watched_paths()) == 1
            status = app.watch_service.status()
            assert status["watcher_enabled"] is True
            assert status["watched"] == 1
        finally:
            app.watch_service.stop()

    def test_sichtbarer_ordner_kommt_hinzu(self, qapp, application, library) -> None:
        extra = library / "unterordner"
        extra.mkdir()
        app = build(application, library)
        app.watch_service.start()
        try:
            app.watch_service.watch_folder(str(extra))
            assert len(app.watch_service.watched_paths()) == 2
            # Ein zweiter Ordner ersetzt den ersten, statt sich anzusammeln.
            app.watch_service.watch_folder(str(library))
            assert len(app.watch_service.watched_paths()) == 1
        finally:
            app.watch_service.stop()

    def test_takt_loest_von_selbst_einen_abgleich_aus(
        self, qapp, application, library
    ) -> None:
        app = build(application, library, **{"index.background_scan_interval_s": 1})
        reasons: list = []
        finished: list = []
        app.watch_service.sync_requested.connect(reasons.append)
        app.index_service.scan_finished.connect(finished.append)

        app.watch_service.start()
        try:
            assert pump_until(qapp, lambda: reasons, 30000), "Takt hat nicht ausgeloest"
            assert reasons[0] == "periodic"
            assert pump_until(qapp, lambda: finished, 60000)
            # Drei Bilder und drei Prompts; der Scanner zaehlt neue Dateien
            # gemeinsam in new_images.
            assert finished[0].files_seen == 6
        finally:
            app.watch_service.stop()

    def test_watcher_ereignis_loest_entprellt_aus(self, qapp, application, library) -> None:
        app = build(
            application,
            library,
            **{"index.background_scan_interval_s": 0, "index.watcher_debounce_ms": 200},
        )
        reasons: list = []
        finished: list = []
        app.watch_service.sync_requested.connect(reasons.append)
        app.index_service.scan_finished.connect(finished.append)

        app.watch_service.start()
        try:
            # Der Watcher selbst meldet unter offscreen nicht verlaesslich; das
            # Ereignis wird deshalb direkt eingespeist. Geprueft wird die
            # Verdrahtung dahinter, nicht Qt.
            app.watch_service._on_directory_changed(str(library))
            assert app.watch_service.tracker.has_pending

            assert pump_until(qapp, lambda: reasons, 30000), "Watcher hat nicht ausgeloest"
            assert reasons[0] == "watcher"
            assert pump_until(qapp, lambda: finished, 60000)
        finally:
            app.watch_service.stop()

    def test_kein_zweiter_scan_waehrend_einer_laeuft(self, qapp, application, library) -> None:
        app = build(application, library, **{"index.background_scan_interval_s": 1})
        finished: list = []
        app.index_service.scan_finished.connect(finished.append)

        app.index_service.start_scan()
        app.watch_service.start()
        try:
            assert pump_until(qapp, lambda: finished, 60000)
            # Der Takt darf sich nicht in den laufenden Scan draengen.
            assert len(finished) == 1
        finally:
            app.watch_service.stop()

    def test_einstellungen_wirken_sofort(self, qapp, application, library) -> None:
        app = build(application, library)
        tracker = app.watch_service.tracker

        app.settings.set("index.watcher_debounce_ms", 3000)
        assert tracker.debounce_seconds == pytest.approx(3.0)

        app.settings.set("index.background_scan_interval_s", 60)
        assert tracker.periodic_seconds == pytest.approx(60.0)

        app.settings.set("index.watcher_enabled", False)
        assert tracker.watcher_enabled is False
        assert app.watch_service.watched_paths() == []

        app.settings.set("index.watcher_enabled", True)
        assert tracker.watcher_enabled is True
        assert len(app.watch_service.watched_paths()) == 1

    def test_probe_blockiert_den_gui_thread_nicht(self, qapp, application, library) -> None:
        """Der Kern der Zusage: ein haengendes os.stat darf die Bedienung nicht anhalten.

        Die Pruefung wird kuenstlich verlangsamt. Kehrt probe_now() trotzdem
        sofort zurueck, laeuft sie nachweislich nicht im GUI-Thread.
        """
        app = build(application, library)
        gui_thread = threading.current_thread()
        probe_threads: list = []
        original = app.health.probe

        def langsam(path: str) -> bool:
            probe_threads.append(threading.current_thread())
            time.sleep(1.0)
            return original(path)

        app.health.probe = langsam

        started = time.perf_counter()
        app.watch_service.probe_now()
        rueckkehr = time.perf_counter() - started

        assert rueckkehr < 0.3, "probe_now() hat den aufrufenden Thread blockiert"
        assert pump_until(qapp, lambda: probe_threads, 20000), "Pruefung lief nicht an"
        assert probe_threads[0] is not gui_thread

    def test_zustandswechsel_wird_gemeldet(self, qapp, application, library) -> None:
        app = build(application, library)
        states: list = []
        app.watch_service.nas_state_changed.connect(states.append)

        app.watch_service.probe_now()
        assert pump_until(qapp, lambda: states, 20000), "Kein Zustand gemeldet"
        assert states[0][str(library)] == "closed"

        # Unveraenderter Zustand loest kein zweites Signal aus.
        app.watch_service.probe_now()
        assert pump_until(qapp, lambda: len(states) > 1, 3000) is False

    def test_stop_raeumt_die_ueberwachung_ab(self, qapp, application, library) -> None:
        app = build(application, library)
        app.watch_service.start()
        assert app.watch_service.watched_paths()
        app.watch_service.stop()
        assert app.watch_service.watched_paths() == []
