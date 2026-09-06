"""Rueckmeldungen aus dem Dauerbetrieb.

Vier Beobachtungen, die alle denselben Kern haben: die Anwendung tat etwas
Falsches, ohne es zu sagen.
"""

from __future__ import annotations

import pytest

from app.dto.models import ScanStats
from tests.conftest import pump_until


class TestMehrfachVerwendetePrompts:
    """Ein Prompt fuer mehrere Bilder ist der Regelfall, kein Fehler.

    In der echten Bibliothek standen 345 Warnungen dieser Art in der
    Problemliste - eine je Ordner und Scanlauf. Sie gaben nichts zu tun und
    verdeckten die Meldungen, die etwas bedeuten.
    """

    def test_keine_warnung_mehr_im_scanner(self) -> None:
        import app.filesystem.scanner as scanner

        with open(scanner.__file__, encoding="utf-8") as handle:
            quelltext = handle.read()
        assert "scanner:pair_conflict" not in quelltext
        assert "gehoeren zu mehreren Bildern" not in quelltext

    def test_die_zahl_bleibt_erhalten(self) -> None:
        stats = ScanStats(pair_conflicts=4)
        assert stats.merged(ScanStats(pair_conflicts=3)).pair_conflicts == 7

    def test_zusammenfassung_nennt_es_neutral(self) -> None:
        text = ScanStats(new_images=2, pair_conflicts=4).summary()
        assert "mehrfach verwendet" in text
        assert "Fehler" not in text and "Konflikt" not in text

    def test_scan_meldet_keine_warnung(self, qapp, application, tmp_path) -> None:
        """Ein Ordner mit einem Prompt und mehreren Bildern - der echte Fall."""
        from PIL import Image

        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "serie.txt").write_text("Ein Prompt fuer alles.", encoding="utf-8")
        for index in range(4):
            Image.new("RGB", (16, 16), (index * 50, 80, 140)).save(
                lib / ("serie_%d.jpg" % index)
            )

        gemeldet: list = []

        class Sammler:
            def handle(self, error) -> None:
                gemeldet.append(error)

        application.reporter.add_sink(Sammler())
        application.settings.set("library.roots", [str(lib)])
        application.settings.set("index.scan_on_startup", False)
        application.build_services()

        fertig: list = []
        application.index_service.scan_finished.connect(fertig.append)
        application.index_service.start_scan()
        assert pump_until(qapp, lambda: fertig, 60000)

        stoerend = [e for e in gemeldet if "mehreren Bildern" in str(e.message)]
        assert not stoerend, "Die alte Warnung ist zurueck: " + str(stoerend)


class TestGescheiterteAnalyse:
    """Ein Fehlschlag darf nicht aussehen wie ein leeres Ergebnis.

    Ausloeser war ein einmaliger, nicht reproduzierbarer TypeError im
    Analysejob: die Anwendung meldete anschliessend beruhigend "keine
    Aenderungen", und der Fehler stand nur in der Problemliste.
    """

    def test_dienst_kennt_ein_eigenes_fehlersignal(self, qapp, application) -> None:
        application.build_services()
        assert hasattr(application.analysis_service, "analysis_failed")

    def test_fehlschlag_meldet_nicht_erfolg(self, qapp, application) -> None:
        application.build_services()
        dienst = application.analysis_service

        erfolge: list = []
        fehler: list = []
        dienst.analysis_finished.connect(erfolge.append)
        dienst.analysis_failed.connect(fehler.append)

        dienst._analysing = True
        dienst._on_analysis_failed(1, "TypeError: irgendetwas")
        dienst._on_analysis_finished(1, "failed")

        assert fehler == ["TypeError: irgendetwas"]
        assert erfolge == [], "Ein Fehlschlag wurde als Erfolg gemeldet"

    def test_erfolg_bleibt_erfolg(self, qapp, application) -> None:
        application.build_services()
        dienst = application.analysis_service
        erfolge: list = []
        fehler: list = []
        dienst.analysis_finished.connect(erfolge.append)
        dienst.analysis_failed.connect(fehler.append)

        dienst._analysing = True
        dienst._on_analysis_result(1, "ergebnis")
        dienst._on_analysis_finished(1, "done")

        assert erfolge == ["ergebnis"]
        assert fehler == []

    def test_der_fehler_wird_nicht_mitgeschleppt(self, qapp, application) -> None:
        """Nach einem Fehlschlag darf der naechste Lauf wieder Erfolg melden."""
        application.build_services()
        dienst = application.analysis_service
        dienst._analysing = True
        dienst._on_analysis_failed(1, "kaputt")
        dienst._on_analysis_finished(1, "failed")

        erfolge: list = []
        dienst.analysis_finished.connect(erfolge.append)
        dienst._analysing = True
        dienst._on_analysis_result(2, "ergebnis")
        dienst._on_analysis_finished(2, "done")
        assert erfolge == ["ergebnis"]


class TestPools:
    """Die Erreichbarkeitspruefung braucht eine eigene Spur.

    Sie lief im io-Pool, gemeinsam mit Abgleich und Vorschaubildern. Sind
    dessen vier Faeden mit Zugriffen auf ein haengendes NAS belegt, kommt
    ausgerechnet die Pruefung nicht mehr dran, die das feststellen soll.
    """

    def test_probe_hat_eigenen_pool(self, qapp, application) -> None:
        application.build_services()
        groessen = application.jobs.pool_sizes()
        assert "probe" in groessen
        assert groessen["probe"] >= 1

    def test_probejob_nutzt_ihn(self) -> None:
        from app.services.watch_service import NasProbeJob

        assert NasProbeJob.pool == "probe"

    def test_vorschaubilder_gehen_vor(self) -> None:
        """Sonst wartet der Benutzer waehrend eines Hintergrundscans auf
        Kacheln, die er gerade ansieht."""
        import app.services.image_service as service

        assert service.THUMBNAIL_PRIORITY > 0

        from app.jobs.scan_jobs import StartupScanJob
        from app.jobs.thumbnail_jobs import ThumbnailJob

        assert ThumbnailJob.pool == StartupScanJob.pool == "io"
