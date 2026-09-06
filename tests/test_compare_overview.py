"""Vergleichsansicht: richtige Stellen, Uebersicht ueber das ganze Dokument.

Zwei Dinge waren gemeldet: die Unterschiede wurden an der falschen Stelle
markiert, und man sah nur den Ausschnitt im Sichtfenster.

Der Versatz war ein Zaehlfehler - die Spannen zaehlten im Rohtext, Qt im
Dokument ohne die Wagenrueckluf-Zeichen. Im Prompt-Editor gilt das weiterhin,
weil dessen Spannen aus der Datenbank kommen.

Die Vergleichsansicht hat das Problem seit dem Umbau **nicht mehr**, und zwar
nicht, weil sie richtig umrechnet, sondern weil sie nichts mehr umzurechnen
hat: der Vergleichskern liefert Abschnitte relativ zum ZEILENANFANG, und die
Zeile ist genau der Block, den Qt zeichnet. Ein Fehler, den es nicht mehr geben
kann, ist besser als einer, der behoben wurde - die Tests hier halten das fest.
"""

from __future__ import annotations

import pytest

from app.analysis.diff import DiffKind, DiffSpan
from app.analysis.textdiff import DiffStatus, SegmentStatus, compare_texts
from app.gui.common.offsets import dropped_positions, translate
from app.gui.compare.overview_bar import OverviewBar
from app.gui.theme import _DARK, _LIGHT

CRLF = chr(13) + chr(10)
LF = chr(10)


class TestKeineUmrechnungMehr:
    def test_vergleichsansicht_rechnet_keine_offsets_um(self) -> None:
        """Der Umbau hat die ganze Fehlerquelle entfernt, nicht nur den Fehler."""
        import app.gui.compare.compare_tab as vergleich
        import app.gui.compare.diff_highlighter as highlighter

        for modul in (vergleich, highlighter):
            with open(modul.__file__, encoding="utf-8") as handle:
                assert "offsets import" not in handle.read(), (
                    modul.__name__ + " rechnet wieder Offsets um -"
                    " die Abschnitte sind zeilenrelativ, das ist unnoetig"
                )

    def test_prompt_editor_rechnet_weiterhin_um(self) -> None:
        """Dort kommen die Spannen aus der Datenbank und zaehlen im Rohtext."""
        import app.gui.editor.prompt_editor as editor

        with open(editor.__file__, encoding="utf-8") as handle:
            assert "offsets import" in handle.read()

    def test_windows_zeilenenden_treffen_ohne_umrechnung(self) -> None:
        """Der Fall, an dem sich der alte Fehler zeigte - jetzt ohne Zutun.

        Bei CRLF verschob sich frueher jede Markierung mit jeder vorangehenden
        Zeile um ein weiteres Zeichen nach links. Zeilenrelative Abschnitte
        koennen das nicht: die Position zaehlt ab Zeilenanfang, und wie die
        Zeile beendet wird, spielt dafuer keine Rolle.
        """
        links = CRLF.join(["Kopf.", "Speed = 100;", "Fuss."])
        rechts = CRLF.join(["Kopf.", "Speed = 120;", "Fuss."])
        ergebnis = compare_texts(links, rechts)

        zeile = ergebnis.rows[1]
        assert zeile.status is DiffStatus.CHANGED
        geaendert = [
            teil
            for teil in zeile.left_segments
            if teil.status is not SegmentStatus.EQUAL
        ]
        assert geaendert, "Kein Wortunterschied gefunden"
        for teil in geaendert:
            assert zeile.left_line.text[teil.start : teil.end] == teil.text
        assert any(teil.text == "100" for teil in geaendert)

    def test_spannen_treffen_nach_der_umrechnung(self) -> None:
        """Der Prompt-Editor braucht die Umrechnung weiterhin."""
        zeilen = ["Erste Zeile.", "Zweite Zeile.", "Dritte Zeile."]
        roh = CRLF.join(zeilen)
        dokument = roh.replace(CRLF, LF)

        spans = []
        for zeile in zeilen:
            start = roh.index(zeile)
            spans.append(DiffSpan(start, start + len(zeile), DiffKind.CHANGED))

        for zeile, span in zip(zeilen, translate(spans, roh)):
            assert dokument[span.start : span.end] == zeile

    def test_ohne_windows_zeilenenden_unveraendert(self) -> None:
        roh = LF.join(["Eins.", "Zwei."])
        spans = [DiffSpan(0, 5, DiffKind.ADDED)]
        assert dropped_positions(roh) == []
        assert translate(spans, roh) == spans


class TestUebersichtsleiste:
    @pytest.fixture
    def leiste(self, qapp):
        return OverviewBar(_LIGHT)

    @pytest.fixture
    def ergebnis(self):
        links = LF.join(["Gleich eins.", "Nur A.", "Gleich zwei.", "Speed = 100;"])
        rechts = LF.join(["Gleich eins.", "Gleich zwei.", "Speed = 120;", "Nur B."])
        return compare_texts(links, rechts)

    def test_gleiche_zeilen_werden_nicht_gezeichnet(self, leiste, ergebnis) -> None:
        """Sonst waere die Leiste bei aehnlichen Prompts durchgehend gefaerbt."""
        leiste.set_result(ergebnis)
        assert leiste.block_count == len(ergebnis.differences)
        assert leiste.block_count < ergebnis.row_count, (
            "Die Leiste zeichnet so viele Striche wie es Zeilen gibt"
        )

    def test_die_leiste_rechnet_in_zeilen(self, leiste, ergebnis) -> None:
        """Der Unterschied zur frueheren Fassung, die in ZEICHEN rechnete.

        Eine Zeile aus vier Zeichen und eine aus vierhundert nehmen im Editor
        dieselbe Hoehe ein. Wer in Zeichen rechnet, setzt den Strich dort, wo
        der Unterschied nicht steht.
        """
        leiste.set_result(ergebnis)
        assert leiste._rows == ergebnis.row_count

    def test_der_aktuelle_block_hebt_sich_ab(self, leiste, ergebnis) -> None:
        leiste.set_result(ergebnis)
        block = ergebnis.real_differences[0]
        normal = leiste._color_for(block)
        leiste.set_current_block(block.index)
        assert leiste._color_for(block) != normal
        assert leiste._color_for(block) == _LIGHT.diff_current

    def test_farben_folgen_dem_schema(self, leiste, ergebnis) -> None:
        leiste.set_result(ergebnis)
        block = ergebnis.differences[0]
        hell = leiste._color_for(block)
        leiste.set_palette_colors(_DARK)
        assert leiste._color_for(block) != hell

    def test_klick_meldet_die_zeile(self, qapp, leiste, ergebnis) -> None:
        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        leiste.set_result(ergebnis)
        leiste.resize(14, 200)
        gemeldet: list = []
        leiste.row_clicked.connect(gemeldet.append)
        ereignis = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(7, 100),
            QPointF(7, 100),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        leiste.mousePressEvent(ereignis)
        assert gemeldet
        assert gemeldet[0] == pytest.approx(ergebnis.row_count // 2, abs=1)

    def test_ohne_ergebnis_stuerzt_nichts_ab(self, leiste) -> None:
        leiste.set_result(None)
        assert leiste.block_count == 0
        assert leiste._rows >= 1

    def test_sichtbarer_ausschnitt_wird_begrenzt(self, leiste) -> None:
        leiste.set_visible_range(-0.5, 2.0)
        assert leiste._visible == (0.0, 1.0)


class TestNeuesteVorlagen:
    """Bilder direkt im Namensordner, ohne dessen Unterordner."""

    def test_nur_ordner_mit_unterordnern(self, qapp, application, tmp_path) -> None:
        from PIL import Image

        from tests.conftest import pump_until

        lib = tmp_path / "lib"
        (lib / "Person" / "ai").mkdir(parents=True)
        (lib / "Ohne").mkdir(parents=True)
        # Vorlage direkt im Namensordner
        Image.new("RGB", (16, 16), (10, 20, 30)).save(lib / "Person" / "vorlage.jpg")
        # Ergebnis im Unterordner - soll NICHT erscheinen
        Image.new("RGB", (16, 16), (40, 50, 60)).save(lib / "Person" / "ai" / "ergebnis.jpg")
        # Blattordner ohne Unterordner - ist selbst ein Ergebnisordner
        Image.new("RGB", (16, 16), (70, 80, 90)).save(lib / "Ohne" / "blatt.jpg")

        application.settings.set("library.roots", [str(lib)])
        application.settings.set("index.scan_on_startup", False)
        application.build_services()
        fertig: list = []
        application.index_service.scan_finished.connect(fertig.append)
        application.index_service.start_scan()
        assert pump_until(qapp, lambda: fertig, 60000)

        namen = [i.filename for i in application.images.newest_templates(50)]
        assert "vorlage.jpg" in namen
        assert "ergebnis.jpg" not in namen, "Ein Bild aus dem Unterordner kam mit"
        assert "blatt.jpg" not in namen, "Ein Blattordner ist keine Vorlagenquelle"

    def test_grenze_wird_eingehalten(self, qapp, application, tmp_path) -> None:
        from PIL import Image

        from tests.conftest import pump_until

        lib = tmp_path / "lib"
        (lib / "Person" / "ai").mkdir(parents=True)
        for index in range(5):
            Image.new("RGB", (16, 16), (index * 40, 0, 0)).save(
                lib / "Person" / ("v%d.jpg" % index)
            )
        application.settings.set("library.roots", [str(lib)])
        application.settings.set("index.scan_on_startup", False)
        application.build_services()
        fertig: list = []
        application.index_service.scan_finished.connect(fertig.append)
        application.index_service.start_scan()
        assert pump_until(qapp, lambda: fertig, 60000)

        assert len(application.images.newest_templates(2)) == 2
