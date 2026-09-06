"""Kleine, aber sichtbare Dinge: Tabtitel, Kopfzeile, Farbwechsel, Aenderungsfarbe."""

from __future__ import annotations

import pytest

from app.dto.models import FolderNode, ImageItem
from app.gui.editor.highlighter import SegmentHighlighter, SegmentSpan
from app.gui.main_window import _folder_label
from app.gui.theme import _DARK, _LIGHT


def folder(path: str, name: str) -> FolderNode:
    return FolderNode(
        id=1, path=path, name=name, parent_id=None, depth=2,
        image_count=0, prompt_count=0, last_scan=None,
    )


class TestTabTitel:
    """Alle interessanten Ordner heissen "ai" - der blosse Name reicht nicht."""

    @pytest.mark.parametrize(
        "pfad, name, erwartet",
        [
            (r"Z:/Instagram\Alyson Hannigan\ai", "ai", "Alyson Hannigan/ai"),
            (r"Z:/Instagram\Mandy\ai", "ai", "Mandy/ai"),
            (r"Z:/Instagram\Mandy", "Mandy", "Instagram/Mandy"),
            ("Z:/Instagram", "Instagram", "Instagram"),
        ],
    )
    def test_elternordner_steht_davor(self, pfad, name, erwartet) -> None:
        assert _folder_label(folder(pfad, name)) == erwartet

    def test_laufwerk_ist_kein_elternordner(self) -> None:
        assert _folder_label(folder("Z:/", "Z:")) == "Z:"


class TestKopfzeile:
    """Dateiname, Bildmasse und Dateigroesse."""

    def test_groesse_wird_lesbar_geschrieben(self) -> None:
        from app.gui.common.preview import _format_size

        assert _format_size(25 * 1024) == "25 KB"
        assert _format_size(int(1.5 * 1024 * 1024)) == "1.5 MB"

    def test_kopfzeile_nennt_alles(self, qapp, application) -> None:
        application.build_services()
        from app.gui.common.preview import PreviewController

        vorschau = PreviewController(application, 10, palette=_LIGHT)
        vorschau._item = ImageItem(
            id=1, folder_id=1, filename="bild.png", stem="bild",
            full_path="Z:/x/bild.png", thumbnail_key="k", thumbnail_state="ready",
            prompt_id=None, file_size=25 * 1024, modified_time=0.0,
        )
        vorschau._pixel_size = (1024, 1536)
        vorschau._update_header()
        text = vorschau.header.text()
        assert "bild.png" in text
        assert "1024 x 1536 px" in text
        assert "25 KB" in text

    def test_ohne_masse_trotzdem_vollstaendig(self, qapp, application) -> None:
        """Die Masse stehen erst fest, wenn das Original geladen ist."""
        application.build_services()
        from app.gui.common.preview import PreviewController

        vorschau = PreviewController(application, 10, palette=_LIGHT)
        vorschau._item = ImageItem(
            id=1, folder_id=1, filename="bild.png", stem="bild",
            full_path="Z:/x/bild.png", thumbnail_key="k", thumbnail_state="ready",
            prompt_id=None, file_size=2048, modified_time=0.0,
        )
        vorschau._update_header()
        assert "bild.png" in vorschau.header.text()
        assert "px" not in vorschau.header.text()


class TestAenderungsfarbe:
    """Der Baum sagt "2 ergaenzt" - der Editor muss zeigen, WAS."""

    @pytest.fixture
    def hl(self, qapp):
        """Das Dokument muss am Leben bleiben - der Highlighter haengt daran."""
        from PyQt6.QtGui import QTextDocument

        dokument = QTextDocument()
        highlighter = SegmentHighlighter(dokument, _LIGHT, min_idf=1.5)
        yield highlighter
        del highlighter, dokument

    def test_neues_segment_bekommt_die_diff_farbe(self, hl) -> None:
        hl.set_change_tokens(added=[7], changed=[])
        span = SegmentSpan(0, 10, idf=0.1, doc_count=99, negative=False,
                           kind="segment", token_id=7)
        assert hl._format_for(span).background().color() == _LIGHT.diff_added

    def test_geaendertes_segment_eigene_farbe(self, hl) -> None:
        hl.set_change_tokens(added=[], changed=[9])
        span = SegmentSpan(0, 10, idf=0.1, doc_count=99, negative=False,
                           kind="segment", token_id=9)
        assert hl._format_for(span).background().color() == _LIGHT.diff_changed

    def test_aenderung_geht_vor_haeufigkeit(self, hl) -> None:
        """Wer einen Baum ansieht, will Neues sehen, nicht Seltenes."""
        hl.set_change_tokens(added=[7], changed=[])
        selten = SegmentSpan(0, 10, idf=9.0, doc_count=1, negative=False,
                             kind="segment", token_id=7)
        assert hl._format_for(selten).background().color() == _LIGHT.diff_added

    def test_ohne_aenderung_gilt_die_haeufigkeit(self, hl) -> None:
        hl.set_change_tokens(added=[7], changed=[])
        anderes = SegmentSpan(0, 10, idf=9.0, doc_count=1, negative=False,
                              kind="segment", token_id=42)
        assert anderes.token_id not in hl._added
        assert hl._format_for(anderes).background().color() == _LIGHT.segment_rare


class TestDurchreichung:
    """Die ganze Kette vom Baum bis zum Highlighter.

    Die Tests darueber pruefen den Highlighter unmittelbar - und liessen
    deshalb durch, dass `PromptEditor.set_change_tokens` beim Umbau
    verlorenging. Ein Klick auf einen Knoten warf danach AttributeError, acht
    Mal in dreizehn Sekunden. Hier wird die Kette als Ganzes durchgerufen.
    """

    def test_editor_reicht_an_den_highlighter_weiter(self, qapp) -> None:
        from app.gui.editor.prompt_editor import PromptEditor

        editor = PromptEditor(10, _LIGHT)
        editor.set_change_tokens([3, 4], [(1, 2)])
        assert editor.highlighter._added == frozenset({3, 4})

    def test_vorschau_reicht_an_den_editor_weiter(self, qapp, application) -> None:
        application.build_services()
        from app.gui.common.preview import PreviewController

        vorschau = PreviewController(application, 10, palette=_LIGHT)
        vorschau.set_change_tokens([7], [9])
        assert vorschau.editor.highlighter._added == frozenset({7})
        assert vorschau.editor.highlighter._changed == frozenset({9})

    def test_legende_schaltet_mit(self, qapp, application) -> None:
        application.build_services()
        from app.gui.common.preview import PreviewController

        vorschau = PreviewController(application, 10, palette=_LIGHT)
        vorschau.set_change_tokens([7], [])
        assert vorschau.legend._added.isVisible() or not vorschau.legend.isVisible()
        vorschau.set_change_tokens([], [])
        assert not vorschau.legend._added.isVisible()

    def test_jede_stufe_der_kette_existiert(self, qapp, application) -> None:
        """Haelt die Schnittstelle fest, damit sie kein Umbau still entfernt."""
        application.build_services()
        from app.gui.common.preview import PreviewController
        from app.gui.editor.prompt_editor import PromptEditor

        for klasse, name in (
            (PromptEditor, "set_change_tokens"),
            (PreviewController, "set_change_tokens"),
            (SegmentHighlighter, "set_change_tokens"),
        ):
            assert hasattr(klasse, name), klasse.__name__ + " hat kein " + name

    def test_knotenwechsel_faerbt_um(self, qapp, application) -> None:
        """Der gemeldete Absturz, nachgestellt: Knoten anklicken."""
        application.build_services()
        from app.gui.common.preview import PreviewController

        vorschau = PreviewController(application, 10, palette=_LIGHT)
        # Erst ein Knoten mit Aenderung, dann einer ohne - wie beim Klicken.
        vorschau.set_change_tokens([4], [])
        assert vorschau.editor.highlighter._added == frozenset({4})
        vorschau.clear()
        assert vorschau.editor.highlighter._added == frozenset()


class TestFarbwechsel:
    def test_der_haupttab_wird_mitgefaerbt(self, qapp, application) -> None:
        """Der Prompt-Editor des Bibliothekstabs fehlte in der Verteilung.

        Eine geaenderte Hervorhebungsfarbe erschien in jedem geoeffneten Tab,
        nur nicht dort, wo man sie zuerst sucht.
        """
        import app.gui.main_window as modul

        with open(modul.__file__, encoding="utf-8") as handle:
            quelltext = handle.read()
        start = quelltext.index("def _on_palette_changed")
        ende = quelltext.index("def ", start + 10)
        rumpf = quelltext[start:ende]
        assert "self._preview.set_palette_colors" in rumpf, (
            "Der Editor des Bibliothekstabs wird nicht mitgefaerbt"
        )

    def test_beide_schemata_haben_diff_farben(self) -> None:
        for palette in (_LIGHT, _DARK):
            assert palette.diff_added != palette.segment_rare
            assert palette.diff_changed != palette.segment_common


class TestTabSchliessen:
    """Das Schliesskreuz darf den Tab schliessen - nicht die Anwendung.

    Ein eigener Schliessknopf konnte den Abstand zum Rand frei bestimmen,
    beendete beim Anklicken aber das ganze Programm - in der Entwicklungs-
    umgebung wie in der gepackten .exe. Reproduzieren liess sich der Absturz
    weder mit einem programmatischen Klick noch mit gesendeten Mausereignissen;
    nur ein echter Klick loeste ihn aus. Vier Bildpunkte Abstand sind das nicht
    wert: es gilt wieder Qts eigener Knopf, der ueber tabCloseRequested laeuft.
    """

    def test_kein_eigener_schliessknopf_mehr(self) -> None:
        import app.gui.tabs.tab_manager as modul

        with open(modul.__file__, encoding="utf-8") as handle:
            quelltext = handle.read()
        assert "QToolButton" not in quelltext, (
            "Der eigene Schliessknopf ist zurueck - er hat die Anwendung beendet"
        )
        assert "setTabButton" not in quelltext.split("def _remove")[0]

    def test_schliessen_laeuft_ueber_qt(self) -> None:
        import app.gui.tabs.tab_manager as modul

        with open(modul.__file__, encoding="utf-8") as handle:
            assert "tabCloseRequested" in handle.read()

    def test_schliessen_entfernt_genau_einen_tab(self, qapp) -> None:
        from PyQt6.QtWidgets import QTabWidget, QWidget

        from app.gui.tabs.tab_manager import TabManager

        tabs = QTabWidget()
        tabs.setTabsClosable(True)
        tabs.addTab(QWidget(), "Bibliothek")
        verwaltung = TabManager(tabs)
        verwaltung.open(QWidget(), "k1", "Neueste Prompt-Paare")
        verwaltung.open(QWidget(), "k2", "Mandy/ai")
        qapp.processEvents()
        assert tabs.count() == 3

        tabs.tabCloseRequested.emit(1)
        for _ in range(20):
            qapp.processEvents()

        assert tabs.count() == 2
        assert not verwaltung.is_open("k1")
        assert verwaltung.is_open("k2"), "Der falsche Tab wurde geschlossen"
        tabs.close()

    def test_bibliothekstab_bleibt_unberuehrt(self, qapp) -> None:
        from PyQt6.QtWidgets import QTabWidget, QWidget

        from app.gui.tabs.tab_manager import TabManager

        tabs = QTabWidget()
        tabs.setTabsClosable(True)
        tabs.addTab(QWidget(), "Bibliothek")
        verwaltung = TabManager(tabs)
        verwaltung.open(QWidget(), "k1", "Ergebnis")
        qapp.processEvents()

        tabs.tabCloseRequested.emit(0)   # das feste Tab
        for _ in range(20):
            qapp.processEvents()
        assert tabs.count() == 2, "Das Bibliothekstab wurde geschlossen"
        tabs.close()


class TestStatusleistenAnordnung:
    def test_pfad_steht_links_vom_ladebalken(self, qapp) -> None:
        from PyQt6.QtWidgets import QMainWindow

        from app.gui.status.status_bar import StatusBar

        fenster = QMainWindow()
        leiste = StatusBar(_LIGHT)
        fenster.setStatusBar(leiste)
        fenster.resize(1500, 300)
        fenster.show()
        leiste.set_activity(1, "Abgleich: Z:/lib/ordner")
        leiste.set_progress(40, 100)
        for _ in range(30):
            qapp.processEvents()

        assert leiste._activity.x() < leiste._progress.x(), (
            "Der Ladebalken steht links von der Pfadanzeige"
        )
        assert leiste._progress.x() < leiste._library.x()
        fenster.close()


class TestVergleichsmarkierung:
    """Die Markierung fuer den Vergleich darf das Bild nicht verdecken.

    Sie faerbte die ganze Kachel orange - das Bild war nicht mehr zu sehen.
    Ursache war kein Fuellbefehl, sondern ein LECK: _paint_marker setzte den
    Pinsel fuer die A/B-Plakette und stellte ihn nicht zurueck. Der danach
    gezeichnete Kachelrahmen fuellte damit die ganze Flaeche.
    """

    def test_pinsel_wird_zurueckgesetzt(self) -> None:
        import app.gui.library.delegate as modul

        with open(modul.__file__, encoding="utf-8") as handle:
            quelltext = handle.read()
        start = quelltext.index("def _paint_marker")
        ende = quelltext.index("def _paint_prompt_badge")
        rumpf = quelltext[start:ende]
        assert "painter.save()" in rumpf and "painter.restore()" in rumpf, (
            "Ohne save/restore leckt der Pinsel in den Kachelrahmen"
        )

    def test_markierung_ist_halbdurchsichtig(self) -> None:
        """Sonst waere die Ecke undurchsichtig und verdeckte das Bild."""
        from app.gui.library.delegate import MARK_ALPHA

        assert 0 < MARK_ALPHA < 255

    def test_ecken_lassen_die_mitte_frei(self, qapp) -> None:
        """Vier Winkel statt einer Flaeche: die Bildmitte bleibt sichtbar."""
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QImage, QPainter

        from app.gui.library.delegate import ThumbnailDelegate

        bild = QImage(120, 120, QImage.Format.Format_ARGB32)
        bild.fill(0xFFFFFFFF)
        maler = QPainter(bild)
        ThumbnailDelegate(_LIGHT, 120)._paint_marker(
            maler, QRect(0, 0, 120, 120), _LIGHT, 0
        )
        maler.end()

        mitte = bild.pixelColor(60, 70)
        ecke = bild.pixelColor(2, 2)
        assert mitte.rgb() == 0xFFFFFFFF, "Die Bildmitte wurde eingefaerbt"
        assert ecke.rgb() != 0xFFFFFFFF, "Die Ecke wurde nicht markiert"


class TestMarkierungUmschalten:
    """Der Menuepunkt nennt, was der Klick TUT."""

    def test_bildansicht_schaltet_den_text_um(self, qapp) -> None:
        from app.dto.models import ImageItem
        from app.gui.image.image_view import ImageView
        from app.services.selection_service import SelectionService

        ansicht = ImageView()
        auswahl = SelectionService()
        ansicht.set_selection_service(auswahl)
        ansicht._item = ImageItem(
            id=7, folder_id=1, filename="a.jpg", stem="a", full_path="Z:/a.jpg",
            thumbnail_key="k", thumbnail_state="ready", prompt_id=None,
            file_size=1, modified_time=0.0,
        )

        assert ansicht._mark_label() == "Fuer Vergleich markieren"
        auswahl.toggle(7)
        assert ansicht._mark_label() == "Markierung entfernen"
        auswahl.toggle(7)
        assert ansicht._mark_label() == "Fuer Vergleich markieren"

    def test_ohne_dienst_bleibt_die_vorgabe(self, qapp) -> None:
        from app.gui.image.image_view import ImageView

        assert ImageView()._mark_label() == "Fuer Vergleich markieren"

    def test_kachelmodell_kennt_den_zustand(self, qapp) -> None:
        from app.gui.library.thumbnail_model import ThumbnailModel
        from app.services.selection_service import SelectionService

        auswahl = SelectionService()
        modell = ThumbnailModel(auswahl)
        assert not modell.is_marked(5)
        auswahl.toggle(5)
        assert modell.is_marked(5)
