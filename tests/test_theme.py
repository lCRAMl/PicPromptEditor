"""Farbschema: die Anwendung folgt Windows, und beide Ebenen stimmen ueberein.

Zwei Ebenen muessen zusammenpassen. Die Palette faerbt, was die Anwendung
selbst zeichnet (Kacheln, Hervorhebungen); der Stil faerbt, was Qt zeichnet
(Menues, Knoepfe, Dialoge). Stimmen sie nicht ueberein, entsteht ein
Flickenteppich - dunkle Kacheln in einem hellen Fenster. Genau das war der
Zustand vor M8.

**Zur Aussagekraft dieser Tests:** die Testsitzung laeuft unter der
Qt-Plattform "offscreen", und die liefert fuer beide Schemata dieselbe helle
Standardpalette - ein dunkles Schema gibt es dort nicht, und eine Schemawahl
nimmt sie gar nicht erst entgegen. Alles, was ein tatsaechlich dunkles Fenster
braucht, wird dort uebersprungen, statt eine Pruefung vorzutaeuschen, die
nichts prueft.

Vollstaendig laufen sie auf der echten Plattform:

    QT_QPA_PLATFORM=windows python -m pytest tests/test_theme.py

Dort greifen alle zwoelf. Wer den Dunkelmodus anfasst, sollte sie so laufen
lassen - im normalen Lauf bleiben acht davon stumm.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication

from app.gui.theme import (
    STYLE,
    ThemeManager,
    active_style_name,
    apply_application_style,
    style_sheet,
)


def fensterhelligkeit(qapp) -> int:
    return qapp.palette().window().color().lightness()


def ist_dunkel(qapp) -> bool:
    return fensterhelligkeit(qapp) < 128


@pytest.fixture
def dunkel_moeglich(qapp) -> bool:
    """Kann diese Qt-Plattform ueberhaupt ein dunkles Schema?"""
    apply_application_style("light")
    hell = fensterhelligkeit(qapp)
    apply_application_style("dark")
    dunkel = fensterhelligkeit(qapp)
    if hell - dunkel <= 100:
        pytest.skip(
            "Die Plattform '"
            + QGuiApplication.platformName()
            + "' kennt kein dunkles Schema (hell="
            + str(hell)
            + ", dunkel="
            + str(dunkel)
            + ")"
        )
    return True


class TestAnwendungsstil:
    def test_stil_beherrscht_dunkel(self, qapp) -> None:
        """Auf Windows 10 kann das nur Fusion; die nativen Stile bleiben hell.

        Gefragt wird die eigene Buchfuehrung, nicht `style().objectName()`:
        unter einem Anwendungs-Stylesheet schiebt Qt einen Proxy davor, dessen
        objectName leer ist. Seit dem Akzentrahmen ist immer eines gesetzt.
        """
        apply_application_style("system")
        assert active_style_name().lower() == STYLE.lower()
        assert qapp.style() is not None

    def test_der_stil_wird_nicht_bei_jedem_aufruf_neu_erzeugt(self, qapp) -> None:
        """Ein erneutes setStyle() verwirft die Schemawahl.

        Der Wachposten dagegen fragte frueher `style().objectName()` ab - unter
        einem Stylesheet dauerhaft leer, also dauerhaft ungleich "fusion". Der
        Stil waere bei jedem Aufruf neu gesetzt worden, und genau die
        Schemawahl waere verlorengegangen, die er schuetzen soll.
        """
        apply_application_style("system")
        vorher = qapp.style()
        apply_application_style("system")
        assert qapp.style() is vorher, "Der Stil wurde ohne Not neu erzeugt"

    def test_wahl_wird_an_qt_gemeldet(self, qapp) -> None:
        """Die ausdrueckliche Wahl muss bei Qt ankommen, nicht nur in der Palette."""
        hints = QGuiApplication.styleHints()
        if not hasattr(hints, "setColorScheme"):
            pytest.skip("Qt ohne setColorScheme")

        apply_application_style("dark")
        if hints.colorScheme() == Qt.ColorScheme.Unknown:
            pytest.skip(
                "Die Plattform '" + QGuiApplication.platformName()
                + "' nimmt keine Schemawahl entgegen"
            )
        assert hints.colorScheme() == Qt.ColorScheme.Dark
        apply_application_style("light")
        assert hints.colorScheme() == Qt.ColorScheme.Light

    def test_erzwungenes_hell_und_dunkel(self, qapp, dunkel_moeglich) -> None:
        apply_application_style("dark")
        assert ist_dunkel(qapp)
        apply_application_style("light")
        assert not ist_dunkel(qapp)

    def test_wechsel_in_beide_richtungen(self, qapp, dunkel_moeglich) -> None:
        """Der Rueckweg ist der Punkt, an dem standardPalette() versagt hat.

        Ohne die zwischengespeicherten Paletten blieb ein Wechsel zurueck auf
        hell wirkungslos, weil setColorScheme ueber ein Ereignis wirkt.
        """
        for erwartet_dunkel, wahl in [
            (True, "dark"), (False, "light"), (True, "dark"),
            (False, "light"), (True, "dark"), (False, "light"),
        ]:
            apply_application_style(wahl)
            assert ist_dunkel(qapp) is erwartet_dunkel, (
                "Wechsel nach " + wahl + " hat die Palette nicht umgestellt"
            )

    def test_system_folgt_dem_betriebssystem(self, qapp, dunkel_moeglich) -> None:
        apply_application_style("system")
        hints = QGuiApplication.styleHints()
        erwartet = hints.colorScheme() == Qt.ColorScheme.Dark
        assert ist_dunkel(qapp) is erwartet


class TestUebereinstimmung:
    @pytest.mark.parametrize("wahl", ["system", "light", "dark"])
    def test_palette_und_stil_sagen_dasselbe(
        self, qapp, dunkel_moeglich, wahl: str
    ) -> None:
        """Der eigentliche Fehler war, dass beide Ebenen auseinanderliefen."""
        apply_application_style(wahl)
        manager = ThemeManager(wahl)
        assert manager.is_dark is ist_dunkel(qapp), (
            "Bei ui.theme=" + wahl + " zeichnet die Anwendung "
            + ("dunkel" if manager.is_dark else "hell")
            + ", Qt aber " + ("dunkel" if ist_dunkel(qapp) else "hell")
        )

    def test_preferenzwechsel_meldet_und_zieht_nach(self, qapp) -> None:
        """set_preference muss beide Ebenen umstellen, nicht nur eine.

        Der Signalteil gilt auf jeder Plattform; die Farbe nur dort, wo es ein
        dunkles Schema gibt.
        """
        apply_application_style("light")
        manager = ThemeManager("light")
        assert manager.is_dark is False

        gemeldet: list = []
        manager.palette_changed.connect(gemeldet.append)
        manager.set_preference("dark")

        assert gemeldet, "palette_changed wurde nicht gemeldet"
        assert gemeldet[0].dark is True
        assert manager.is_dark is True


class TestPalette:
    def test_beide_paletten_unterscheiden_sich_deutlich(
        self, qapp, dunkel_moeglich
    ) -> None:
        apply_application_style("light")
        hell = fensterhelligkeit(qapp)
        apply_application_style("dark")
        dunkel = fensterhelligkeit(qapp)
        assert hell - dunkel > 100

    def test_text_bleibt_lesbar(self, qapp) -> None:
        """Ein dunkler Hintergrund mit dunklem Text waere unbrauchbar."""
        for wahl in ("light", "dark"):
            apply_application_style(wahl)
            hintergrund = qapp.palette().window().color().lightness()
            text = qapp.palette().windowText().color().lightness()
            assert abs(hintergrund - text) > 100, "Zu wenig Kontrast bei " + wahl

    def test_eigene_palette_deckt_beide_schemata_ab(self) -> None:
        """Jede semantische Farbe existiert hell und dunkel."""
        from app.gui.theme import _DARK, _LIGHT

        assert _LIGHT.dark is False and _DARK.dark is True
        # Diese Farben sind in beiden Schemata ABSICHTLICH gleich. Sie stehen
        # so auch in den VS-Code-Vorlagen: ein Wert, der auf hellem wie auf
        # dunklem Grund lesbar ist, zweimal einzutragen erzeugt nur die
        # Gelegenheit, ihn auseinanderlaufen zu lassen.
        absichtlich_gleich = {
            "error",        # errorForeground #F85149 in beiden Modern-Themes
            "line_number",  # editorLineNumber.foreground #6E7681 in beiden
            "accent_text",  # Schrift auf dem Akzent ist immer weiss
        }
        for feld in _LIGHT.__dataclass_fields__:
            if feld == "dark" or feld in absichtlich_gleich:
                continue
            hell = getattr(_LIGHT, feld)
            dunkel = getattr(_DARK, feld)
            assert hell != dunkel, "Farbe '" + feld + "' ist in beiden gleich"

    def test_die_absichtlichen_ausnahmen_gibt_es_wirklich(self) -> None:
        """Ein Ausnahmenamen, den es nicht mehr gibt, macht die Liste stumpf."""
        from app.gui.theme import _LIGHT

        for feld in ("error", "line_number", "accent_text"):
            assert feld in _LIGHT.__dataclass_fields__


class TestAkzentUndStylesheet:
    """Der Azure-Rahmen aus VS Code.

    Er ist KEIN Palettenwert, sondern Styling - Fusion zeichnet von sich aus
    nichts dergleichen. Deshalb wird hier das Stylesheet geprueft und nicht
    nur die Farbe.
    """

    def test_der_akzent_ist_der_vs_code_wert(self) -> None:
        from app.gui.theme import _DARK, _LIGHT

        assert _DARK.accent.name() == "#0078d4", "Dark Modern: focusBorder"
        assert _LIGHT.accent.name() == "#005fb8", "Light Modern: focusBorder"

    def test_fokus_traegt_den_akzent(self) -> None:
        from app.gui.theme import _DARK

        qss = style_sheet(_DARK)
        assert ":focus" in qss
        assert _DARK.accent.name() in qss

    def test_der_aktive_tab_traegt_die_linie_oben(self) -> None:
        """tab.activeBorderTop - das auffaelligste Merkmal des Themes."""
        from app.gui.theme import ACCENT_WIDTH, _DARK

        qss = style_sheet(_DARK)
        assert "QTabBar::tab:selected" in qss
        erwartet = "border-top: " + str(ACCENT_WIDTH) + "px solid " + _DARK.accent.name()
        assert erwartet in qss, "Die Akzentlinie ueber dem aktiven Tab fehlt"

    def test_das_innenmass_der_tabs_steht_nur_noch_hier(self) -> None:
        """Zwei Regelsaetze fuer dieselben Tabs sind eine Falle.

        Das Hauptfenster hatte ein eigenes Stylesheet am Tab-Widget. Ein
        Stylesheet am Widget verdraengt fuer diese Eigenschaften das globale -
        die Akzentlinie waere dort verlorengegangen.
        """
        import app.gui.main_window as fenster

        with open(fenster.__file__, encoding="utf-8") as handle:
            quelle = handle.read()
        assert "QTabBar::tab" not in quelle, (
            "Das Hauptfenster stylt die Tabs wieder selbst"
        )
        from app.gui.theme import _LIGHT

        assert "QTabBar::tab {" in style_sheet(_LIGHT), (
            "Das Innenmass ist beim Verschieben verlorengegangen"
        )

    def test_beide_schemata_ergeben_verschiedene_stylesheets(self) -> None:
        from app.gui.theme import _DARK, _LIGHT

        assert style_sheet(_LIGHT) != style_sheet(_DARK)

    def test_keine_literalfarbe_im_stylesheet(self) -> None:
        """Jede Farbe muss aus der Palette kommen, sonst laeuft sie auseinander."""
        import re

        from app.gui.theme import _DARK

        erlaubt = {
            getattr(_DARK, feld).name()
            for feld in _DARK.__dataclass_fields__
            if feld != "dark"
        }
        gefunden = set(re.findall(r"#[0-9a-fA-F]{6}", style_sheet(_DARK)))
        assert gefunden <= erlaubt, (
            "Farben ohne Palettenherkunft: " + str(sorted(gefunden - erlaubt))
        )

    def test_die_palette_kommt_bei_qt_an(self, qapp) -> None:
        """Ohne das faerbt Qt weiter in den Standardfarben von Fusion."""
        from PyQt6.QtGui import QPalette

        from app.gui.theme import _DARK

        apply_application_style("dark")
        qp = qapp.palette()
        assert qp.color(QPalette.ColorRole.Highlight).name() == _DARK.accent.name()
        assert qp.color(QPalette.ColorRole.Window).name() == _DARK.window.name()
        assert qp.color(QPalette.ColorRole.Base).name() == _DARK.surface.name()

    def test_das_stylesheet_wird_gesetzt(self, qapp) -> None:
        apply_application_style("dark")
        assert "QTabBar::tab:selected" in qapp.styleSheet()


class TestFlaecheUndRahmen:
    """Alle Flaechen eine Farbe, die Rahmen tragen die Struktur.

    Der Zustand davor: vier Grautoene, die sich um wenige Prozent
    unterschieden (#181818 Leiste, #1F1F1F Flaechen, #202020 Kacheln,
    #313131 Eingaben) und Rahmen in #2B2B2B, die auf dunklem Grund praktisch
    unsichtbar waren. Das Ergebnis war weder flach noch gegliedert.
    """

    def test_jede_flaeche_traegt_die_farbe_der_obersten_leiste(self) -> None:
        from app.gui.theme import _DARK, _LIGHT

        for palette in (_DARK, _LIGHT):
            leiste = palette.window.name()
            for feld in ("surface", "tile_background", "input_background"):
                assert getattr(palette, feld).name() == leiste, (
                    feld + " weicht von der Leistenfarbe ab: "
                    + getattr(palette, feld).name() + " statt " + leiste
                )

    def test_der_ruhende_rahmen_ist_derselbe_blauton_wie_der_fokus(self) -> None:
        """Zwei Staerken EINER Farbe, nicht zwei Farben."""
        from app.gui.theme import _DARK, _LIGHT

        for palette in (_DARK, _LIGHT):
            ruhe, fokus = palette.accent_dim, palette.accent
            assert ruhe != fokus, "Ruhe und Fokus waeren nicht zu unterscheiden"
            # Blau heisst hier: der Blauanteil dominiert deutlich.
            assert ruhe.blue() > ruhe.red(), "accent_dim ist kein Blauton"
            assert ruhe.blue() > ruhe.green()

    def test_der_ruhende_rahmen_hebt_sich_vom_grund_ab(self) -> None:
        """Der Fehler vorher: #2B2B2B auf #1F1F1F - rechnerisch da, optisch nicht."""
        from app.gui.theme import _DARK, _LIGHT

        for palette in (_DARK, _LIGHT):
            grund = palette.window
            rahmen = palette.accent_dim
            abstand = abs(grund.lightness() - rahmen.lightness())
            assert abstand >= 20, (
                "Der Rahmen liegt zu dicht am Grund (Helligkeitsabstand "
                + str(abstand) + ")"
            )

    def test_jedes_bedienelement_bekommt_einen_rahmen(self) -> None:
        """Eine Ansicht ohne Rahmen faellt auf gleichfarbigem Grund als Loch auf."""
        from app.gui.theme import _DARK

        qss = style_sheet(_DARK)
        for klasse in (
            "QLineEdit",
            "QPlainTextEdit",
            "QTextEdit",
            "QComboBox",
            "QSpinBox",
            "QTreeView",
            "QListView",
            "QTableView",
            "QTreeWidget",
            "QListWidget",
            "QTableWidget",
        ):
            assert klasse + "," in qss or klasse + " {" in qss, (
                klasse + " bekommt keinen Rahmen"
            )

    def test_der_zustand_haengt_an_jeder_klasse_einzeln(self) -> None:
        """`QLineEdit, QTextEdit:focus` gaelte nur fuer die LETZTE Klasse.

        Ein Fehler, der stumm bleibt, weil eines der Felder ja richtig
        aussieht - und der genau dann auffaellt, wenn man ihn nicht sucht.
        """
        from app.gui.theme import _DARK

        qss = style_sheet(_DARK)
        for zeile in qss.splitlines():
            # Nur Auswahlzeilen betrachten, die einen Zustand tragen. Die
            # Deklarationen dahinter ("{ border: ... }") gehoeren nicht dazu.
            auswahl = zeile.split("{")[0].strip()
            if ":" not in auswahl or "," not in auswahl:
                continue
            teile = [t.strip() for t in auswahl.split(",") if t.strip()]
            fehlend = [t for t in teile if ":" not in t]
            assert not fehlend, (
                "Diese Klassen stehen ohne Zustand in einer Zustandsregel und"
                " wuerden dauerhaft gelten: " + str(fehlend) + "\n  " + auswahl
            )

    def test_der_fokus_bleibt_unterscheidbar(self) -> None:
        from app.gui.theme import _DARK

        qss = style_sheet(_DARK)
        assert "border: 1px solid " + _DARK.accent_dim.name() in qss, "Ruhezustand"
        assert "border: 1px solid " + _DARK.accent.name() in qss, "Fokus"


class TestEinstellbareLinienfarbe:
    """Die Rahmenfarbe laesst sich im Einstellungsdialog aendern.

    Der Punkt, an dem es leicht schiefgeht: das Stylesheet wurde aus den
    KONSTANTEN gebaut. Eine gewaehlte Farbe haette dann nur veraendert, was
    die Anwendung selbst malt - Rahmen, Rollbalken und Pfeile zeichnet aber
    Qt, und die waeren auf dem alten Wert geblieben.
    """

    def test_das_stylesheet_folgt_der_uebergebenen_palette(self, qapp) -> None:
        from dataclasses import replace

        from PyQt6.QtGui import QColor

        from app.gui.theme import _DARK

        eigen = replace(
            _DARK,
            accent=QColor(0xFF, 0x00, 0x88),
            accent_dim=QColor(0x77, 0x00, 0x44),
        )
        apply_application_style("dark", eigen)
        try:
            qss = qapp.styleSheet()
            assert "#ff0088" in qss, "Die Akzentfarbe kam nicht im Stylesheet an"
            assert "#770044" in qss, "Die Linienfarbe kam nicht im Stylesheet an"
            assert _DARK.accent.name() not in qss, "Der alte Wert steht noch drin"
        finally:
            apply_application_style("dark")

    def test_die_palette_von_qt_folgt_ebenfalls(self, qapp) -> None:
        from dataclasses import replace

        from PyQt6.QtGui import QColor, QPalette

        from app.gui.theme import _DARK

        eigen = replace(_DARK, accent=QColor(0xFF, 0x00, 0x88))
        apply_application_style("dark", eigen)
        try:
            assert qapp.palette().color(QPalette.ColorRole.Highlight).name() == "#ff0088"
        finally:
            apply_application_style("dark")

    def test_der_manager_reicht_die_ueberschreibung_durch(self, qapp, application) -> None:
        """Der Weg, den der Einstellungsdialog tatsaechlich geht."""
        import json

        from app.gui.theme import ThemeManager

        application.settings.set(
            "ui.color_accent", json.dumps({"dark": "#ff0088", "light": "#ff0088"})
        )
        manager = ThemeManager("dark", application.settings)
        try:
            assert manager.palette.accent.name() == "#ff0088"
            manager.refresh()
            assert "#ff0088" in qapp.styleSheet(), (
                "refresh() hat den Stil nicht neu gesetzt"
            )
        finally:
            application.settings.set("ui.color_accent", "")
            apply_application_style("dark")


class TestRollbalkenUndPfeile:
    """Was in x2.jpg noch grau war beziehungsweise ganz fehlte."""

    def test_rollbalken_bekommen_leiste_und_schieber(self) -> None:
        from app.gui.theme import _DARK

        qss = style_sheet(_DARK)
        for teil in (
            "QScrollBar:vertical",
            "QScrollBar:horizontal",
            "QScrollBar::handle:vertical",
            "QScrollBar::handle:horizontal",
        ):
            assert teil in qss, teil + " fehlt"
        assert _DARK.accent_dim.name() in qss

    def test_jede_pfeilspitze_hat_ein_bild(self) -> None:
        """Die Regel, die zweimal Pfeile gekostet hat.

        Nachgemessen an Qt 6.11: bekommt ein Bedienelement einen eigenen
        Rahmen, hoert Qt auf, seine Pfeilspitzen selbst zu zeichnen - uebrig
        bleibt eine leere Flaeche. Genau so standen im Einstellungsdialog
        sieben Zahlenfelder ohne Pfeiltaste da. Der CSS-Kniff mit drei
        durchsichtigen Raendern hilft nicht; Qt macht daraus einen
        ausgefuellten Block, kein Dreieck.

        Wer also ein Unterelement mit Pfeil stylt, MUSS ein Bild mitgeben.
        """
        from app.gui.theme import _DARK

        # Blockweise pruefen, nicht zeilenweise: eine Regel verteilt sich im
        # erzeugten Stylesheet oft auf Auswahl- und Deklarationszeilen.
        qss = style_sheet(_DARK)
        for block in qss.split("}"):
            auswahl = block.split("{")[0]
            if "-arrow" not in auswahl:
                continue
            assert "image: url(" in block, (
                "Pfeilspitze ohne Bild - sie bleibt leer:\n  " + block.strip()
            )

    def test_die_pfeilbilder_liegen_im_paket(self) -> None:
        from pathlib import Path

        from app.gui.theme import arrow_url

        for richtung in ("up", "down", "left", "right"):
            for dunkel in (True, False):
                pfad = arrow_url(richtung, dunkel)
                assert Path(pfad).is_file(), "fehlt: " + pfad
                assert "\\" not in pfad, (
                    "url() braucht Schraegstriche - mit Backslashes bleibt der"
                    " Pfad stumm unaufloesbar: " + pfad
                )

    def test_die_pfeile_folgen_dem_farbschema(self) -> None:
        """Ein Pfeil ist keine Linie - er soll wie Text lesbar sein."""
        from app.gui.theme import arrow_url

        assert arrow_url("up", True) != arrow_url("up", False)

    def test_ankreuzfelder_bekommen_einen_rahmen(self) -> None:
        from app.gui.theme import _DARK

        qss = style_sheet(_DARK)
        assert "QCheckBox::indicator" in qss
        assert "QRadioButton::indicator" in qss

    def test_die_legende_holt_ihren_rahmen_aus_der_palette(self, qapp) -> None:
        """Sie trug als einzige Stelle eine feste graue Rahmenfarbe.

        Geprueft wird das Ergebnis, nicht der Quelltext: nach
        `set_palette_colors` muss in jedem Feld der Akzentton stehen.
        """
        from app.gui.editor.legend import HighlightLegend
        from app.gui.theme import _DARK

        legende = HighlightLegend(_DARK)
        felder = (
            legende._rare,
            legende._common,
            legende._negative,
            legende._added,
            legende._modified,
        )
        for feld in felder:
            qss = feld.styleSheet()
            assert _DARK.accent_dim.name() in qss, (
                "Feld ohne Akzentrahmen: " + feld.text() + "\n  " + qss
            )
            assert "rgba(128,128,128" not in qss, "noch die feste Graufarbe"
        legende.deleteLater()


class TestDuenneLinien:
    """Jede Flaeche traegt eine duenne Linie - siehe vscode.png.

    Gemeldet fehlten: der Rahmen um die Bildansicht, die Linie unter der
    Tableiste, der Rahmen um Dateiname und Bildmasse, und der Rahmen um das
    Programm selbst. Der Trenner war umgekehrt zu dick.
    """

    def test_die_bildansicht_bekommt_einen_rahmen(self) -> None:
        """QGraphicsView fiel durch jede bisherige Regel."""
        from app.gui.theme import _DARK

        assert "QGraphicsView" in style_sheet(_DARK)

    def test_der_programmrahmen_ist_benannt(self) -> None:
        """Das Tab-Widget laeuft im Dokumentmodus und zeichnet keinen Rahmen.

        Deshalb traegt ihn ein eigener Behaelter - und der braucht einen
        Namen, auf den die Regel zeigen kann.
        """
        from app.gui.theme import _DARK

        assert "QWidget#fensterRahmen" in style_sheet(_DARK)

    def test_der_editorbereich_ist_eine_flaeche(self) -> None:
        """Kopfzeile, Text und Legende gehoeren in EINEN Rahmen.

        Vorher stand die Zeile mit Dateiname und Bildmassen frei im Schwarzen,
        waehrend der Text darunter umrandet war.
        """
        from app.gui.theme import _DARK

        qss = style_sheet(_DARK)
        assert "QWidget#editorPanel" in qss
        assert "QWidget#editorPanel QPlainTextEdit" in qss, (
            "Ohne diese Regel liegen zwei Linien zwei Bildpunkte nebeneinander"
        )

    def test_die_tableiste_hat_eine_trennlinie(self) -> None:
        from app.gui.theme import _DARK

        assert "QTabBar { background:" in style_sheet(_DARK)
        assert "border-bottom" in style_sheet(_DARK)

    def test_der_trenner_ist_duenn(self) -> None:
        """Qts Vorgabe wirkt blau eingefaerbt wie ein Balken, nicht wie eine Linie."""
        from app.gui.theme import SPLITTER_WIDTH, _DARK

        assert SPLITTER_WIDTH <= 3
        qss = style_sheet(_DARK)
        assert "QSplitter::handle:horizontal { width: " + str(SPLITTER_WIDTH) in qss
        assert "QSplitter::handle:vertical { height: " + str(SPLITTER_WIDTH) in qss

    def test_der_benannte_behaelter_existiert_auch_wirklich(self, qapp) -> None:
        """Eine Regel auf einen Namen, den niemand traegt, ist stumm."""
        import app.gui.main_window as fenster

        with open(fenster.__file__, encoding="utf-8") as handle:
            assert 'setObjectName("fensterRahmen")' in handle.read()

        import app.gui.common.preview as vorschau

        with open(vorschau.__file__, encoding="utf-8") as handle:
            assert 'setObjectName("editorPanel")' in handle.read()


class TestHintergrundfarbe:
    """Die Flaechenfarbe des ganzen Programms ist einstellbar.

    Der Haken: alle Flaechen tragen dieselbe Farbe. Eine Einstellung, die nur
    `window` setzt, faerbt die Leisten um und laesst Editor, Kachelraster und
    Eingabefelder auf dem alten Wert - genau der Flickenteppich, den die eine
    Flaechenfarbe beseitigt hat.
    """

    def _mit(self, **werte):
        import json

        class Fake:
            def __init__(self, w):
                self._w = {k: json.dumps(v) for k, v in w.items()}

            def get_str(self, key):
                return self._w.get(key, "")

        return Fake(werte)

    def test_alle_flaechen_ziehen_mit(self) -> None:
        from app.gui.theme import _DARK, with_overrides

        palette = with_overrides(
            _DARK, self._mit(**{"ui.color_background": {"dark": "#101820"}})
        )
        for feld in ("window", "surface", "tile_background", "input_background"):
            assert getattr(palette, feld).name() == "#101820", feld + " blieb zurueck"

    def test_der_platzhalter_hebt_sich_ab(self) -> None:
        """Auf dunklem Grund heller, auf hellem dunkler - sonst unsichtbar."""
        from app.gui.theme import _DARK, with_overrides

        dunkel = with_overrides(
            _DARK, self._mit(**{"ui.color_background": {"dark": "#101820"}})
        )
        assert dunkel.placeholder.lightness() > dunkel.window.lightness()

        hell = with_overrides(
            _DARK, self._mit(**{"ui.color_background": {"dark": "#f0f0f0"}})
        )
        assert hell.placeholder.lightness() < hell.window.lightness()

    def test_heller_hintergrund_dreht_die_schrift(self) -> None:
        """Sonst hellgraue Schrift auf Weiss - das Programm waere unlesbar.

        Die Regel ist der Grund, warum die Einstellung gefahrlos ist: die
        Schrift folgt der HELLIGKEIT des Hintergrunds, nicht dem Schema.
        """
        from app.gui.theme import _DARK, with_overrides

        palette = with_overrides(
            _DARK, self._mit(**{"ui.color_background": {"dark": "#ffffff"}})
        )
        assert palette.text.lightness() < 128, (
            "Helle Schrift auf weissem Grund - unlesbar"
        )
        assert abs(palette.window.lightness() - palette.text.lightness()) > 100

    def test_passender_hintergrund_laesst_die_schrift_in_ruhe(self) -> None:
        from app.gui.theme import _DARK, with_overrides

        palette = with_overrides(
            _DARK, self._mit(**{"ui.color_background": {"dark": "#101820"}})
        )
        assert palette.text.name() == _DARK.text.name()

    def test_eine_ausdrueckliche_wahl_schlaegt_die_ableitung(self) -> None:
        """Wer Hintergrund UND Akzent setzt, soll beides bekommen."""
        from app.gui.theme import _DARK, with_overrides

        palette = with_overrides(
            _DARK,
            self._mit(
                **{
                    "ui.color_background": {"dark": "#ffffff"},
                    "ui.color_accent": {"dark": "#ff0088"},
                }
            ),
        )
        assert palette.accent.name() == "#ff0088"
        assert palette.window.name() == "#ffffff"

    def test_ohne_einstellung_bleibt_alles(self) -> None:
        from app.gui.theme import _DARK, with_overrides

        palette = with_overrides(_DARK, self._mit())
        assert palette.window.name() == _DARK.window.name()
        assert palette.text.name() == _DARK.text.name()


class TestFarbeUeberlebtDenNeustart:
    """Gemeldet: "die Einstellung fuer die Hintergrundfarbe wird nicht
    gespeichert fuer den naechsten Aufruf des Programms."

    Gespeichert war sie. Nachgesehen in der Datenbank stand die Farbe da,
    Zeitstempel und alles. Nur getragen hat sie beim Start niemand: der
    einzige Aufruf von `apply_application_style` vor dem Hauptfenster bekam
    keine Palette und baute deshalb aus den Konstanten. Die eingestellte Farbe
    erschien erst, nachdem der Einstellungsdialog einmal geoeffnet und mit OK
    geschlossen wurde - denn nur dort lief `ThemeManager.refresh`.

    Fuer den Benutzer war das von "nicht gespeichert" nicht zu unterscheiden.
    """

    def _mit(self, **werte):
        import json

        class Fake:
            def __init__(self, w):
                self._w = {k: json.dumps(v) for k, v in w.items()}

            def get_str(self, key):
                return self._w.get(key, "")

        return Fake(werte)

    def test_der_startaufruf_traegt_die_eingestellte_farbe(self, qapp) -> None:
        """Ohne fertige Palette, aber mit settings: die Wahl muss ankommen."""
        from app.gui.theme import _LIGHT, apply_application_style, style_sheet, with_overrides

        try:
            apply_application_style(
                "light", settings=self._mit(**{"ui.color_background": {"light": "#123456"}})
            )
            erwartet = with_overrides(
                _LIGHT, self._mit(**{"ui.color_background": {"light": "#123456"}})
            )
            assert "#123456" in style_sheet(erwartet)
            assert "#123456" in qapp.styleSheet()
        finally:
            apply_application_style("system")

    def test_ohne_settings_bleibt_es_bei_den_konstanten(self, qapp) -> None:
        """Der alte Aufruf ohne Einstellungen darf sich nicht veraendern."""
        from app.gui.theme import _LIGHT, apply_application_style, style_sheet

        try:
            apply_application_style("light")
            assert qapp.styleSheet() == style_sheet(_LIGHT)
        finally:
            apply_application_style("system")

    def test_startaufruf_und_themenmanager_kommen_zum_selben_ergebnis(self, qapp) -> None:
        """Beide Wege muessen dieselbe Palette ergeben - sonst blitzt es auf."""
        from app.gui.theme import ThemeManager, apply_application_style, style_sheet

        settings = self._mit(
            **{
                "ui.color_background": {"light": "#123456", "dark": "#123456"},
                "ui.color_accent": {"light": "#ff0088", "dark": "#ff0088"},
            }
        )
        try:
            apply_application_style("light", settings=settings)
            vom_start = qapp.styleSheet()
            manager = ThemeManager("light", settings)
            assert vom_start == style_sheet(manager.palette)
        finally:
            apply_application_style("system")
