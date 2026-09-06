"""Farbschema und semantische Farbtokens.

Qt 6.5 und neuer liefert QStyleHints.colorScheme() und das Signal
colorSchemeChanged. Damit laesst sich der Windows-Hell-/Dunkelmodus ohne
Registry-Abfrage und ohne Polling uebernehmen; hier laeuft Qt 6.11.

Die Diff-Farben aus Spezifikation 01 (Gelb, Gruen, Rot, Weiss) gelten nur fuer
den hellen Modus. Weiss fuer "identisch" waere im dunklen Modus falsch, deshalb
ist "identisch" hier keine Farbe, sondern die Abwesenheit einer Hervorhebung.
Die uebrigen drei existieren als Tokenpaar hell/dunkel.

**Achtung, zwei Saetze Diff-Farben.** `diff_changed`, `diff_added` und
`diff_removed` gehoeren dem **Prompt-Editor** und dem Evolutionsbaum: dort
bedeutet die Farbe die Richtung einer Aenderung gegenueber dem Elternknoten,
und Gruen/Rot ist dafuer richtig. Die `diff_*`-Tokens weiter unten gehoeren der
**Vergleichsansicht** und folgen WinMerge. Wer sie verwechselt, bekommt in der
Vergleichsansicht ein Git-Schema und im Baum ein WinMerge-Schema - beides
falsch am jeweiligen Ort.

Zwei Ebenen, die beide bedient werden muessen:

* Die **Palette** hier faerbt, was die Anwendung selbst zeichnet: Kacheln,
  Hervorhebungen, Statusleiste.
* Der **Stil** faerbt, was Qt zeichnet: Menues, Knoepfe, Dialoge, Baumansichten.

Ohne die zweite Ebene entsteht ein Flickenteppich - dunkle Kacheln in einem
hellen Fenster. Auf Windows 10 beherrscht von den vier verfuegbaren Stilen nur
**Fusion** ein dunkles Schema; windowsvista, windows11 und Windows liefern dort
unveraendert rgb(212,208,200). Deshalb wird Fusion gesetzt, sobald die
Anwendung startet. Der Preis ist ein etwas flacheres, weniger natives Aussehen -
auf Windows 10 gibt es fuer echtes Dunkel keine Alternative.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication


@dataclass(frozen=True)
class Palette:
    """Semantische Farben. Kein Widget benutzt Literalfarben."""

    dark: bool
    tile_background: QColor
    tile_selected: QColor
    tile_border: QColor
    placeholder: QColor
    text_muted: QColor
    marker_compare: QColor
    segment_common: QColor
    segment_rare: QColor
    segment_negative: QColor
    diff_changed: QColor
    diff_added: QColor
    diff_removed: QColor
    warning: QColor
    error: QColor
    # --- Grundfarben und Akzent, aus VS Code "Modern" ------------------------
    #: Der Akzent. Rahmen um das fokussierte Feld, Linie ueber dem aktiven Tab,
    #: Auswahl, Knopffuellung. Die eine Farbe, an der man das Programm erkennt.
    accent: QColor
    #: Der Akzent im Ruhezustand. Jedes Bedienelement traegt ihn als Rahmen -
    #: auf einer durchgehend schwarzen Flaeche sind die Rahmen das Einzige,
    #: was noch Struktur gibt. Der volle `accent` bleibt dem Fokus vorbehalten,
    #: sonst waere nicht mehr zu sehen, wo man gerade ist.
    accent_dim: QColor
    accent_hover: QColor
    accent_text: QColor
    #: Rahmenfarben des Fensters: `window` fuer Leisten und Raender,
    #: `surface` fuer Inhaltsflaechen (Editor, Kachelraster).
    window: QColor
    surface: QColor
    border: QColor
    text: QColor
    input_background: QColor
    input_border: QColor
    selection_background: QColor
    selection_text: QColor
    #: Zeilennummern in der Vergleichsansicht.
    line_number: QColor
    # --- Vergleichsansicht, WinMerge-Schema (siehe unten) ---------------------
    diff_block: QColor
    diff_word: QColor
    diff_word_deleted: QColor
    diff_placeholder: QColor
    diff_ignored: QColor
    diff_moved: QColor
    diff_current: QColor


# Die sieben diff_*-Tokens der Vergleichsansicht folgen dem Standardschema von
# WinMerge. Es ist bewusst KEIN Gruen/Rot-Schema wie bei Git: dort bedeutet die
# Farbe die Richtung der Aenderung, hier bedeutet sie die Art. Links und rechts
# tragen dieselbe Blockfarbe; ob etwas hinzugekommen oder weggefallen ist,
# erkennt man am grauen Platzhalter der Gegenseite.
#
#   diff_block          #EFCB05  ganze geaenderte Zeile        (COLORREF 0x05CBEF)
#   diff_word           #F1E2AD  Wortunterschied darin         (COLORREF 0xADE2F1)
#   diff_word_deleted   #FFAA82  entfallener Teil darin        (COLORREF 0x82AAFF)
#   diff_placeholder    #C0C0C0  Zeile fehlt auf dieser Seite  (COLORREF 0xC0C0C0)
#   diff_ignored        #FBF2BF  durch Option unerheblich
#   diff_moved          #FFCC80  verschobener Block
#   diff_current        #F0A000  der gerade angesprungene Unterschied
#
# Die letzten drei sind keine WinMerge-Konstanten, sondern hier gewaehlt - der
# Auftrag nennt fuer sie nur "WinMerge-nah". Abweichung von Spezifikation 01,
# die Gelb/Gruen/Rot vorsieht; ausdruecklicher Wunsch, Abweichungsregister
# Nr. 30.
#
# Die dunklen Werte sind keine Umrechnung der hellen, sondern eigene Toene. Ein
# aufgehelltes #EFCB05 auf dunklem Grund blendet, ein abgedunkeltes ist von
# Braun nicht zu unterscheiden. Massgeblich ist allein, dass die Rangfolge aus
# §14 des Auftrags erhalten bleibt: diff_word muss sich in beiden Modi vom
# umgebenden diff_block abheben.
# --- Herkunft der Grundfarben -------------------------------------------------
#
# Die Werte stammen aus den VS-Code-Themes "Dark Modern" und "Light Modern",
# die unter app/gui/assets/themes/theme-defaults/ mitgeliefert werden. Sie sind
# hier als Konstanten EINGETRAGEN und werden nicht zur Laufzeit gelesen:
#
#   * Ein Themewechsel ist nicht vorgesehen (bewusste Entscheidung). Damit
#     entfaellt der ganze Apparat aus Lader, Include-Aufloesung, Rueckfallkette
#     und Auswahl im Einstellungsdialog.
#   * Zur Laufzeit wird keine Datei geoeffnet. Das erspart eine Ausnahme im
#     Architekturtest, der Dateizugriffe ausserhalb app/filesystem verbietet.
#
# Die JSON-Dateien bleiben trotzdem im Paket: sie sind die Quelle dieser Werte
# und der Ausgangspunkt, falls doch einmal umschaltbar werden soll.
#
#   Token                 Dark Modern            Light Modern
#   accent                focusBorder  #0078D4   #005FB8
#   accent_hover          button.hoverBackground  #026EC1 / #0258A8
#   window                sideBar.background      #181818 / #F8F8F8
#
# ACHTUNG - hier weicht die Anwendung bewusst von den Themedateien ab:
# `surface`, `tile_background` und `input_background` tragen NICHT die Werte
# aus editor.background bzw. input.background, sondern alle dieselbe Farbe wie
# `window`. VS Code staffelt seine Flaechen um wenige Prozent Helligkeit; in
# diesem Programm sah das weder flach noch gegliedert aus, sondern nach vier
# zufaelligen Grautoenen. Statt dessen: EINE Flaechenfarbe, und die Rahmen
# tragen die Struktur. Ausdruecklicher Wunsch, Abweichungsregister Nr. 35.
#
#   surface/tile/input    = window                #181818 / #F8F8F8
#   accent_dim            Ruherahmen, eigener Wert #0E4B7E / #A9C9E8
#   border                = accent_dim
#   text                  foreground              #CCCCCC / #3B3B3B
#   text_muted            input.placeholderForeground  #989898 / #767676
#   line_number           editorLineNumber.foreground  #6E7681 (beide)
#   input_background      input.background        #313131 / #FFFFFF
#   input_border          input.border            #3C3C3C / #CECECE
#   error                 errorForeground         #F85149 (beide)
#
# ZWEI Werte stehen in KEINEM der beiden Themes und sind hier gewaehlt:
#
#   selection_background  dunkel #04395E - "list.activeSelectionBackground"
#                         fehlt in Dark Modern; VS Code faellt dort auf einen
#                         eingebauten Wert zurueck. Hell ist #E8E8E8 aus der
#                         Datei, aber ein Grau als Auswahl waere hier zu leise,
#                         weil das Kachelraster keine anderen Anhaltspunkte
#                         bietet - deshalb auch hell der Akzent, aufgehellt.
#   warning               VS Code hat keinen brauchbaren Schluessel dafuer
#                         (editorWarning.foreground fehlt in beiden).
#
# `marker_compare`, die `segment_*` und die sieben `diff_*` bleiben ABSICHTLICH
# unabhaengig vom Theme. Die Diff-Farben folgen WinMerge (siehe unten); VS Code
# kennt dafuer nur Gruen/Rot, und die zu uebernehmen haette genau die
# Darstellung zerstoert, die in M9 entstanden ist.
_LIGHT = Palette(
    dark=False,
    tile_background=QColor(0xF8, 0xF8, 0xF8),
    tile_selected=QColor(0xCC, 0xE0, 0xF5),
    tile_border=QColor(0xA9, 0xC9, 0xE8),
    placeholder=QColor(0xEF, 0xEF, 0xEF),
    text_muted=QColor(0x76, 0x76, 0x76),
    marker_compare=QColor(230, 126, 34),
    segment_common=QColor(238, 238, 238),
    segment_rare=QColor(214, 234, 248),
    segment_negative=QColor(250, 227, 227),
    diff_changed=QColor(255, 244, 179),
    diff_added=QColor(198, 239, 206),
    diff_removed=QColor(255, 199, 206),
    warning=QColor(176, 122, 0),
    error=QColor(0xF8, 0x51, 0x49),
    accent=QColor(0x00, 0x5F, 0xB8),
    accent_dim=QColor(0xA9, 0xC9, 0xE8),
    accent_hover=QColor(0x02, 0x58, 0xA8),
    accent_text=QColor(0xFF, 0xFF, 0xFF),
    window=QColor(0xF8, 0xF8, 0xF8),
    surface=QColor(0xF8, 0xF8, 0xF8),
    border=QColor(0xA9, 0xC9, 0xE8),
    text=QColor(0x3B, 0x3B, 0x3B),
    input_background=QColor(0xF8, 0xF8, 0xF8),
    input_border=QColor(0xA9, 0xC9, 0xE8),
    selection_background=QColor(0xCC, 0xE0, 0xF5),
    selection_text=QColor(0x00, 0x00, 0x00),
    line_number=QColor(0x6E, 0x76, 0x81),
    diff_block=QColor(239, 203, 5),
    diff_word=QColor(241, 226, 173),
    diff_word_deleted=QColor(255, 170, 130),
    diff_placeholder=QColor(192, 192, 192),
    diff_ignored=QColor(251, 242, 191),
    diff_moved=QColor(255, 204, 128),
    diff_current=QColor(240, 160, 0),
)

_DARK = Palette(
    dark=True,
    tile_background=QColor(0x18, 0x18, 0x18),
    tile_selected=QColor(0x04, 0x39, 0x5E),
    tile_border=QColor(0x0E, 0x4B, 0x7E),
    placeholder=QColor(0x1F, 0x1F, 0x1F),
    text_muted=QColor(0x98, 0x98, 0x98),
    marker_compare=QColor(240, 150, 60),
    segment_common=QColor(52, 52, 56),
    segment_rare=QColor(30, 58, 80),
    segment_negative=QColor(72, 40, 40),
    diff_changed=QColor(90, 78, 30),
    diff_added=QColor(40, 84, 52),
    diff_removed=QColor(96, 42, 46),
    warning=QColor(230, 180, 60),
    error=QColor(0xF8, 0x51, 0x49),
    accent=QColor(0x00, 0x78, 0xD4),
    accent_dim=QColor(0x0E, 0x4B, 0x7E),
    accent_hover=QColor(0x02, 0x6E, 0xC1),
    accent_text=QColor(0xFF, 0xFF, 0xFF),
    window=QColor(0x18, 0x18, 0x18),
    surface=QColor(0x18, 0x18, 0x18),
    border=QColor(0x0E, 0x4B, 0x7E),
    text=QColor(0xCC, 0xCC, 0xCC),
    input_background=QColor(0x18, 0x18, 0x18),
    input_border=QColor(0x0E, 0x4B, 0x7E),
    selection_background=QColor(0x04, 0x39, 0x5E),
    selection_text=QColor(0xFF, 0xFF, 0xFF),
    line_number=QColor(0x6E, 0x76, 0x81),
    diff_block=QColor(90, 78, 30),
    diff_word=QColor(138, 120, 56),
    diff_word_deleted=QColor(138, 74, 50),
    diff_placeholder=QColor(58, 58, 62),
    diff_ignored=QColor(62, 58, 40),
    diff_moved=QColor(122, 90, 46),
    diff_current=QColor(168, 122, 32),
)


#: Der einzige Stil, der auf Windows 10 ein dunkles Schema beherrscht.
STYLE = "Fusion"

#: Welcher Stil zuletzt gesetzt wurde. Eigene Buchfuehrung, weil
#: `QApplication.style().objectName()` unter einem Anwendungs-Stylesheet leer
#: ist: Qt schiebt dann einen Proxy davor, und PyQt6 gibt dessen Basisstil
#: nicht heraus. Nachgemessen an Qt 6.11.
_ACTIVE_STYLE: dict[str, str] = {}


def active_style_name() -> str:
    """Der zuletzt gesetzte Stil. Leer, solange keiner gesetzt wurde."""
    return _ACTIVE_STYLE.get("name", "")


#: Die beiden Standardpaletten des Stils, einmal beim Start eingesammelt.
#: Grund fuer das Zwischenspeichern: standardPalette() folgt dem gerade
#: eingestellten Schema, und setColorScheme wirkt ueber ein Ereignis. Direkt
#: nacheinander gelesen liefert es deshalb unzuverlaessig noch die Farben des
#: vorherigen Schemas - besonders beim Wechsel von "dem System folgen" auf eine
#: feste Wahl. Einmal eingesammelt sind beide Paletten dagegen jederzeit
#: sofort und in beide Richtungen anwendbar.
_STANDARD: dict[bool, object] = {}


def _capture_standard_palettes(app, style, hints) -> None:
    """Sammelt hell und dunkel einmalig ein. Vor dem ersten Fenster aufrufen."""
    if _STANDARD or hints is None or not hasattr(hints, "setColorScheme"):
        return
    from PyQt6.QtGui import QPalette

    for scheme, dark in ((Qt.ColorScheme.Light, False), (Qt.ColorScheme.Dark, True)):
        hints.setColorScheme(scheme)
        app.processEvents()
        _STANDARD[dark] = QPalette(style.standardPalette())
    hints.setColorScheme(Qt.ColorScheme.Unknown)
    app.processEvents()


def _wants_dark(preference: str, hints) -> bool:
    if preference == "dark":
        return True
    if preference == "light":
        return False
    scheme = getattr(hints, "colorScheme", None)
    if scheme is not None:
        try:
            return scheme() == Qt.ColorScheme.Dark
        except (TypeError, AttributeError):
            pass
    return QGuiApplication.palette().window().color().lightness() < 128


def apply_application_style(
    preference: str = "system",
    palette: "Palette | None" = None,
    settings=None,
) -> None:
    """Setzt Stil und Palette der ganzen Anwendung.

    Vor dem Bau des Hauptfensters aufrufen, damit die Widgets gleich in der
    richtigen Farbe entstehen; ein nachtraeglicher Wechsel liesse sie kurz hell
    aufblitzen.

    Qt::ColorScheme::Unknown bedeutet nicht "unbekannt", sondern "keine
    Vorgabe" - also wieder dem System folgen.

    **`settings` ist der Weg fuer den Programmstart.** Der ThemeManager reicht
    seine fertige Palette ueber `palette` herein; beim Start gibt es ihn aber
    noch nicht, und ohne `settings` baute der erste Aufruf die Palette allein
    aus den Konstanten. Die eingestellten Farben erschienen dann erst, wenn
    jemand den Einstellungsdialog oeffnete und mit OK schloss - fuer den
    Benutzer nicht von "die Farbe wurde nicht gespeichert" zu unterscheiden.
    Gespeichert war sie; nur getragen hat sie niemand bis ins Fenster.
    """
    from PyQt6.QtWidgets import QApplication, QStyleFactory

    app = QApplication.instance()
    if app is None:
        return

    # Den Stil nur einmal setzen: ein erneutes setStyle() mit frisch erzeugtem
    # Objekt verwirft die Schemawahl wieder.
    #
    # Gemerkt wird das in _ACTIVE_STYLE und NICHT ueber style().objectName().
    # Sobald ein Anwendungs-Stylesheet gesetzt ist - und seit dem Akzentrahmen
    # ist es das immer - schiebt Qt einen Proxy davor, dessen objectName leer
    # ist. Die Abfrage waere damit dauerhaft falsch, der Stil wuerde bei jedem
    # Aufruf neu erzeugt, und genau die Schemawahl ginge verloren, die dieser
    # Wachposten schuetzen soll.
    style = app.style()
    if style is None or _ACTIVE_STYLE.get("name", "").lower() != STYLE.lower():
        created = QStyleFactory.create(STYLE)
        if created is not None:
            app.setStyle(created)
            _ACTIVE_STYLE["name"] = STYLE
        style = app.style()
    if style is None:
        return

    hints = QGuiApplication.styleHints()
    _capture_standard_palettes(app, style, hints)

    if hints is not None and hasattr(hints, "setColorScheme"):
        hints.setColorScheme(
            {"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}.get(
                preference, Qt.ColorScheme.Unknown
            )
        )

    # Erst die Standardpalette des Stils als Unterbau - sie fuellt die vielen
    # Rollen, die uns nicht interessieren (Schatten, Rahmenlicht, Mittelton).
    grundlage = _STANDARD.get(_wants_dark(preference, hints))
    app.setPalette(grundlage if grundlage is not None else style.standardPalette())

    # Darueber die eigenen Werte. Die Reihenfolge ist wichtig: umgekehrt wuerde
    # die Standardpalette den Akzent wieder ueberschreiben.
    #
    # `palette` reicht der ThemeManager herein - mit den eingestellten
    # Ueberschreibungen. Ohne diesen Weg baute das Stylesheet immer aus den
    # Konstanten, und eine im Dialog gewaehlte Akzentfarbe waere auf allem
    # wirkungslos geblieben, was Qt selbst zeichnet: Rahmen, Rollbalken,
    # Pfeile. Genau die sollen sich ja aendern lassen.
    if palette is not None:
        tokens = palette
    else:
        # Ohne fertige Palette: das Schema bestimmen und die eingestellten
        # Farben darueberlegen - dieselbe Aufloesung, die der ThemeManager
        # spaeter macht. Ohne `settings` bleibt es bei den Konstanten.
        tokens = with_overrides(
            _DARK if _wants_dark(preference, hints) else _LIGHT, settings
        )
    _apply_palette_colors(app, tokens)
    app.setStyleSheet(style_sheet(tokens))


#: Staerke des Akzentrahmens. Zwei Pixel wie in VS Code - bei einem Pixel geht
#: er auf hochaufloesenden Bildschirmen unter, bei drei wirkt er wie ein Fehler.
ACCENT_WIDTH = 2

#: Staerke der Trenner zwischen den Bereichen. Qts Vorgabe von fuenf bis
#: sechs Bildpunkten wirkt als blau gefaerbter Streifen wie ein Balken;
#: drei sind schmal genug fuer eine Linie und breit genug, um sie mit der
#: Maus noch zu treffen.
SPLITTER_WIDTH = 3


#: Die Pfeilspitzen liegen als Bild bei. Der Grund steht ausfuehrlich in
#: `style_sheet`; kurz: sobald ein Bedienelement einen eigenen Rahmen bekommt,
#: hoert Qt auf, seine Pfeile selbst zu zeichnen, und der CSS-Kniff mit drei
#: durchsichtigen Raendern ergibt in Qt keinen Pfeil, sondern einen Block.
ARROW_DIR = "icons"


def arrow_url(direction: str, dark: bool) -> str:
    """Pfad zur Pfeilspitze, in der Form, die ein Qt-Stylesheet versteht.

    Zwei Fallstricke: `url()` braucht Schraegstriche, auch unter Windows -
    Backslashes gelten dort als Maskierung und der Pfad bleibt stumm
    unaufloesbar. Und die Fassung haengt am Farbschema, nicht am Akzent: ein
    Pfeil ist keine Linie, er soll wie Text lesbar sein.
    """
    from pathlib import Path

    from app.gui.resources import asset_path

    name = ARROW_DIR + "/arrow-" + direction + ("-dark" if dark else "-light") + ".svg"
    return Path(asset_path(name)).as_posix()


def style_sheet(palette: Palette) -> str:
    """Das Stylesheet der ganzen Anwendung.

    Die Palette allein reicht fuer das VS-Code-Aussehen nicht. Sie sagt, WELCHE
    Farben es gibt; sie sagt nicht, dass ein Bedienelement einen blauen Rahmen
    bekommt und der aktive Tab eine blaue Linie obendrueber.

    **Die Rahmen tragen hier die ganze Struktur.** Alle Flaechen haben dieselbe
    Farbe wie die oberste Leiste - Fenster, Editor, Kachelraster, Eingabefelder.
    Auf einem durchgehend gleichfarbigen Grund gibt es keine Kanten mehr, die
    sich aus einem Helligkeitsunterschied ergeben; ohne sichtbare Rahmen liefe
    alles ineinander. Vorher unterschieden sich vier Grautoene um wenige
    Prozent, und die Rahmen lagen mit #2B2B2B so dicht am Grund, dass sie
    praktisch unsichtbar waren - das Ergebnis war weder flach noch gegliedert.

    Deshalb zwei Staerken desselben Blaus:

        accent_dim   jedes Element im Ruhezustand
        accent       das Element mit dem Fokus

    Ein Wechsel der Rahmenfarbe statt der Rahmenbreite - Letzteres verschoebe
    beim Hineinklicken das Layout um einen Pixel.

    Was hier NICHT hineingehoert: alles, was die Anwendung selbst zeichnet.
    Kacheln, Diff-Zeilen und Uebersichtsleiste holen ihre Farben aus der
    Palette und malen sie mit QPainter. Sie zweimal zu beschreiben waere eine
    Quelle fuer Widersprueche.
    """
    def hex_(color: QColor) -> str:
        return QColor(color).name()

    akzent = hex_(palette.accent)
    ruhe = hex_(palette.accent_dim)
    flaeche = hex_(palette.surface)
    fenster = hex_(palette.window)
    schrift = hex_(palette.text)
    gedaempft = hex_(palette.text_muted)
    auswahl = hex_(palette.selection_background)
    auswahl_text = hex_(palette.selection_text)
    auf_akzent = hex_(palette.accent_text)
    schweben = hex_(palette.placeholder)
    breite = str(ACCENT_WIDTH) + "px"
    hoch = arrow_url("up", palette.dark)
    runter = arrow_url("down", palette.dark)
    links_pfeil = arrow_url("left", palette.dark)
    rechts_pfeil = arrow_url("right", palette.dark)

    # Jede Klasse, die einen Rahmen im Ruhezustand traegt. Zusammen aufgezaehlt
    # statt einzeln geregelt, damit keine vergessen wird - eine Ansicht ohne
    # Rahmen faellt auf dem gleichfarbigen Grund sofort als Loch auf.
    eingaben = (
        "QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox,"
        " QComboBox, QAbstractSpinBox, QDateTimeEdit"
    )
    ansichten = (
        "QTreeView, QListView, QTableView, QTreeWidget, QListWidget,"
        " QTableWidget, QGraphicsView"
    )

    def mit_zustand(klassen: str, zustand: str) -> str:
        """Haengt einen Zustand an jede Klasse einer Aufzaehlung.

        `QLineEdit, QTextEdit` + `:focus` wird zu `QLineEdit:focus,
        QTextEdit:focus`. Ein einzelnes `:focus` am Ende der Liste gaelte nur
        fuer die letzte Klasse - ein Fehler, der still bleibt, weil eines der
        Felder ja richtig aussieht.
        """
        return ", ".join(teil.strip() + zustand for teil in klassen.split(","))

    return "\n".join(
        [
            # Grundflaeche: alles in der Farbe der obersten Leiste.
            "QWidget { background: " + fenster + "; color: " + schrift + "; }",
            "QMainWindow, QDialog { background: " + fenster + "; }",
            "QLabel { background: transparent; }",
            # Kopfzeile, Text und Legende der Detailansicht sind EINE Flaeche.
            # Ohne diesen Rahmen stand die Zeile mit Dateiname und Bildmasse
            # frei im Schwarzen, waehrend der Text darunter umrandet war.
            "QWidget#fensterRahmen { border: 1px solid " + ruhe + "; }",
            "QWidget#editorPanel { border: 1px solid " + ruhe + "; }",
            "QWidget#editorPanel QPlainTextEdit, QWidget#editorPanel QTextEdit {",
            "  border: 0;",  # sonst zwei Linien zwei Bildpunkte nebeneinander
            "}",
            # Eingaben und Auswahlfelder -------------------------------------
            eingaben + " {",
            "  background: " + flaeche + ";",
            "  color: " + schrift + ";",
            "  border: 1px solid " + ruhe + ";",
            "  border-radius: 2px;",
            # Ohne Innenabstand klebt der Text am Rahmen. Fusion setzt von
            # sich aus keinen, sobald ein eigener Rahmen im Stylesheet steht.
            "  padding: 3px 6px;",
            "  selection-background-color: " + akzent + ";",
            "  selection-color: " + auf_akzent + ";",
            "}",
            mit_zustand(eingaben, ":focus") + " {",
            "  border: 1px solid " + akzent + ";",
            "}",
            mit_zustand(eingaben, ":hover") + " {",
            "  border: 1px solid " + akzent + ";",
            "}",
            mit_zustand(eingaben, ":disabled") + " {",
            "  color: " + gedaempft + ";",
            "}",
            # Ein AUFKLAPPBARES Auswahlfeld enthaelt intern ein QLineEdit, und
            # ohne diese Regel bekaeme es aus der Eingabefeld-Regel oben einen
            # zweiten Rahmen innerhalb des ersten. Beim Zahlenfeld ist das
            # nachgemessen nicht der Fall - dort umschliesst der Rahmen das
            # ganze Widget samt Pfeiltasten. Die Regel steht also fuer den
            # Auswahlfeld-Fall, nicht fuer beide.
            "QAbstractSpinBox QLineEdit, QComboBox QLineEdit {",
            "  border: 0; background: transparent; padding: 0;",
            "}",
            # KEINE Regel fuer ::drop-down: sie wuerde den Aufklapppfeil
            # kosten (siehe die Erlaeuterung bei den Rollbalken weiter unten).
            "QComboBox QAbstractItemView {",
            "  background: " + flaeche + ";",
            "  border: 1px solid " + akzent + ";",
            "  selection-background-color: " + auswahl + ";",
            "  selection-color: " + auswahl_text + ";",
            "}",
            # Listen, Baeume, Tabellen ---------------------------------------
            ansichten + " {",
            "  background: " + flaeche + ";",
            "  color: " + schrift + ";",
            "  border: 1px solid " + ruhe + ";",
            "  border-radius: 2px;",
            "}",
            mit_zustand(ansichten, ":focus") + " {",
            "  border: 1px solid " + akzent + ";",
            "}",
            "QTreeView::item:selected, QListView::item:selected,"
            " QTableView::item:selected {",
            "  background: " + auswahl + ";",
            "  color: " + auswahl_text + ";",
            "}",
            "QTreeView::item:hover, QListView::item:hover,"
            " QTableView::item:hover { background: " + schweben + "; }",
            "QHeaderView::section {",
            "  background: " + fenster + ";",
            "  color: " + gedaempft + ";",
            "  border: 0;",
            "  border-bottom: 1px solid " + ruhe + ";",
            "  padding: 4px;",
            "}",
            # Tabs ------------------------------------------------------------
            # Das Innenmass stand frueher als eigenes Stylesheet am Widget; zwei
            # konkurrierende Regelsaetze fuer dieselben Tabs sind eine Falle,
            # deshalb steht es jetzt hier bei allem anderen.
            "QTabBar::tab {",
            "  padding: 5px 8px 5px 10px;",
            "  background: " + fenster + ";",
            "  color: " + gedaempft + ";",
            "  border-top: " + breite + " solid transparent;",
            "  border-right: 1px solid " + ruhe + ";",
            "}",
            "QTabBar::tab:selected {",
            "  background: " + flaeche + ";",
            "  color: " + schrift + ";",
            "  border: 1px solid " + akzent + ";",
            "  border-top: " + breite + " solid " + akzent + ";",
            "}",
            # Der Rahmen um den Inhaltsbereich - und damit um das Programm.
            # Er fehlte, weil das Tab-Widget im Dokumentmodus laeuft; dort
            # zeichnet Qt von sich aus keinen. Die Regel holt ihn zurueck.
            "QTabWidget::pane {",
            "  border: 1px solid " + ruhe + ";",
            "  top: -1px;",  # unter der Leiste ansetzen, keine doppelte Linie
            "}",
            # Die Leiste selbst traegt die Trennlinie zum Inhalt. Der
            # Rahmen des Tab-Widgets kann sie nicht liefern: es laeuft im
            # Dokumentmodus und zeichnet dort keinen.
            "QTabBar { background: " + fenster + "; border-bottom: 1px solid "
            + ruhe + "; }",
            # Knoepfe ---------------------------------------------------------
            "QPushButton {",
            "  background: " + flaeche + ";",
            "  color: " + schrift + ";",
            "  border: 1px solid " + ruhe + ";",
            "  border-radius: 2px;",
            "  padding: 4px 12px;",
            "}",
            "QPushButton:hover, QPushButton:focus { border: 1px solid "
            + akzent + "; }",
            "QPushButton:default {",
            "  background: " + akzent + ";",
            "  color: " + auf_akzent + ";",
            "  border: 1px solid " + akzent + ";",
            "}",
            "QPushButton:default:hover { background: "
            + hex_(palette.accent_hover) + "; }",
            "QPushButton:disabled { color: " + gedaempft
            + "; border: 1px solid " + ruhe + "; }",
            # Leisten, Trenner, Menues ----------------------------------------
            "QToolBar { background: " + fenster + "; border: 0; spacing: 2px; }",
            "QStatusBar { background: " + fenster + "; color: " + schrift + "; }",
            "QSplitter::handle { background: " + ruhe + "; }",
            "QSplitter::handle:horizontal { width: " + str(SPLITTER_WIDTH) + "px; }",
            "QSplitter::handle:vertical { height: " + str(SPLITTER_WIDTH) + "px; }",
            "QSplitter::handle:hover { background: " + akzent + "; }",
            "QMenuBar { background: " + fenster + "; color: " + schrift + "; }",
            "QMenuBar::item:selected { background: " + akzent + "; color: "
            + auf_akzent + "; }",
            "QMenu { background: " + flaeche + "; border: 1px solid " + ruhe + "; }",
            "QMenu::item:selected { background: " + akzent + "; color: "
            + auf_akzent + "; }",
            "QGroupBox { border: 1px solid " + ruhe + "; border-radius: 2px;"
            " margin-top: 8px; padding-top: 6px; }",
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; color: "
            + gedaempft + "; }",
            # Rollbalken -------------------------------------------------------
            #
            # HIER GILT EINE REGEL, DIE MAN EINMAL LERNT:
            # Sobald ein Unterelement (::sub-line, ::up-button, ::drop-down)
            # im Stylesheet auftaucht, zeichnet Qt es vollstaendig selbst -
            # und damit auch seine Pfeilspitze NICHT mehr. Uebrig bleibt eine
            # leere Flaeche. Nachgemessen an Qt 6.11 fuer Rollbalken,
            # Zahlenfelder und Auswahlfelder gleichermassen.
            #
            # Der CSS-Kniff mit drei durchsichtigen Raendern hilft nicht: Qt
            # rendert daraus keinen Pfeil, sondern einen ausgefuellten Block.
            # Ein eigenes Pfeilbild waere der dritte Weg, wuerde aber die
            # einstellbare Akzentfarbe nicht mitnehmen.
            #
            # Deshalb: gestylt werden Leiste und Schieber, die Pfeiltasten
            # bleiben Qt ueberlassen. Sie folgen der QPalette und damit dem
            # Farbschema.
            "QScrollBar:vertical { background: " + fenster + "; border: 1px solid "
            + ruhe + "; width: 16px; margin: 16px 0 16px 0; }",
            "QScrollBar:horizontal { background: " + fenster + "; border: 1px solid "
            + ruhe + "; height: 16px; margin: 0 16px 0 16px; }",
            "QScrollBar::handle:vertical, QScrollBar::handle:horizontal {",
            "  background: " + fenster + ";",
            "  border: 1px solid " + ruhe + ";",
            "  border-radius: 2px;",
            "}",
            "QScrollBar::handle:vertical { min-height: 24px; }",
            "QScrollBar::handle:horizontal { min-width: 24px; }",
            "QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {",
            "  border: 1px solid " + akzent + ";",
            "}",
            # Die Rinne bleibt leer - sonst legt Qt dort seinen Grauverlauf hin.
            "QScrollBar::add-page, QScrollBar::sub-page { background: none; }",
            # Die Pfeiltasten der Rollbalken, mit Bild.
            "QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {",
            "  background: " + fenster + ";",
            "  border: 1px solid " + ruhe + ";",
            "  height: 15px; subcontrol-origin: margin;",
            "}",
            "QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {",
            "  background: " + fenster + ";",
            "  border: 1px solid " + ruhe + ";",
            "  width: 15px; subcontrol-origin: margin;",
            "}",
            "QScrollBar::sub-line:vertical { subcontrol-position: top; }",
            "QScrollBar::add-line:vertical { subcontrol-position: bottom; }",
            "QScrollBar::sub-line:horizontal { subcontrol-position: left; }",
            "QScrollBar::add-line:horizontal { subcontrol-position: right; }",
            "QScrollBar::sub-line:hover, QScrollBar::add-line:hover {",
            "  border: 1px solid " + akzent + ";",
            "}",
            "QScrollBar::up-arrow:vertical { image: url(" + hoch + ");"
            " width: 7px; height: 7px; }",
            "QScrollBar::down-arrow:vertical { image: url(" + runter + ");"
            " width: 7px; height: 7px; }",
            "QScrollBar::left-arrow:horizontal { image: url(" + links_pfeil + ");"
            " width: 7px; height: 7px; }",
            "QScrollBar::right-arrow:horizontal { image: url(" + rechts_pfeil + ");"
            " width: 7px; height: 7px; }",
            # Zahlenfelder ------------------------------------------------------
            "QSpinBox::up-button, QDoubleSpinBox::up-button,"
            " QAbstractSpinBox::up-button {",
            "  subcontrol-origin: border; subcontrol-position: top right;",
            "  width: 16px; border-left: 1px solid " + ruhe + ";",
            "  background: " + fenster + ";",
            "}",
            "QSpinBox::down-button, QDoubleSpinBox::down-button,"
            " QAbstractSpinBox::down-button {",
            "  subcontrol-origin: border; subcontrol-position: bottom right;",
            "  width: 16px; border-left: 1px solid " + ruhe + ";",
            "  background: " + fenster + ";",
            "}",
            "QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,"
            " QAbstractSpinBox::up-button:hover,"
            " QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover,"
            " QAbstractSpinBox::down-button:hover { background: " + auswahl + "; }",
            "QSpinBox::up-arrow, QDoubleSpinBox::up-arrow,"
            " QAbstractSpinBox::up-arrow {",
            "  image: url(" + hoch + "); width: 7px; height: 7px;",
            "}",
            "QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,"
            " QAbstractSpinBox::down-arrow {",
            "  image: url(" + runter + "); width: 7px; height: 7px;",
            "}",
            # Auswahlfelder ------------------------------------------------------
            # Das Auswahlfeld behaelt seinen Pfeil auch mit eigenem Rahmen -
            # anders als das Zahlenfeld. Der Knopfbereich bekommt trotzdem eine
            # Regel, damit Fusion dort keinen zweiten Rahmen hineinzeichnet.
            "QComboBox::drop-down { border: 0; width: 18px; }",
            "QComboBox::down-arrow { image: url(" + runter + ");"
            " width: 7px; height: 7px; }",
            # Ankreuzfelder und Optionsfelder ----------------------------------
            # Hier ist es umgekehrt: das Haekchen zeichnet Qt ohnehin nicht als
            # eigenes Unterelement, sondern als Teil des Indikators. Ein
            # gefuellter Kasten im Akzent ist deutlicher als ein Haken auf
            # schwarzem Grund.
            "QCheckBox::indicator, QRadioButton::indicator {",
            "  width: 13px; height: 13px;",
            "  background: " + flaeche + ";",
            "  border: 1px solid " + ruhe + ";",
            "}",
            "QRadioButton::indicator { border-radius: 7px; }",
            "QCheckBox::indicator:hover, QRadioButton::indicator:hover {",
            "  border: 1px solid " + akzent + ";",
            "}",
            "QCheckBox::indicator:checked, QRadioButton::indicator:checked {",
            "  background: " + akzent + ";",
            "  border: 1px solid " + akzent + ";",
            "}",
            # Fortschritt und Werkzeugknoepfe ----------------------------------
            "QProgressBar {",
            "  background: " + flaeche + ";",
            "  border: 1px solid " + ruhe + ";",
            "  border-radius: 2px;",
            "  text-align: center;",
            "  color: " + schrift + ";",
            "}",
            "QProgressBar::chunk { background: " + akzent + "; }",
            "QToolButton {",
            "  background: " + fenster + ";",
            "  border: 1px solid transparent;",
            "  border-radius: 2px;",
            "  padding: 3px;",
            "}",
            "QToolButton:hover { border: 1px solid " + akzent + "; }"
        ]
    )


def _apply_palette_colors(app, palette: Palette) -> None:
    """Traegt die Tokens in die QPalette ein.

    Ohne das bliebe alles, was Qt selbst zeichnet, bei den Standardfarben von
    Fusion. Es ist ausserdem der Grund, warum der Dunkelmodus jetzt auch unter
    der Plattform "offscreen" funktioniert: dort liefert `standardPalette()`
    fuer beide Schemata dieselben hellen Farben, weshalb bisher acht Tests des
    Farbschemas uebersprungen wurden. Ausdrueckliche Werte kennen dieses
    Problem nicht.
    """
    from PyQt6.QtGui import QPalette

    qp = QPalette(app.palette())
    rollen = {
        QPalette.ColorRole.Window: palette.window,
        QPalette.ColorRole.WindowText: palette.text,
        QPalette.ColorRole.Base: palette.surface,
        QPalette.ColorRole.AlternateBase: palette.window,
        QPalette.ColorRole.Text: palette.text,
        QPalette.ColorRole.Button: palette.input_background,
        QPalette.ColorRole.ButtonText: palette.text,
        QPalette.ColorRole.Highlight: palette.accent,
        QPalette.ColorRole.HighlightedText: palette.accent_text,
        QPalette.ColorRole.ToolTipBase: palette.surface,
        QPalette.ColorRole.ToolTipText: palette.text,
        QPalette.ColorRole.PlaceholderText: palette.text_muted,
        QPalette.ColorRole.Link: palette.accent,
    }
    for rolle, farbe in rollen.items():
        qp.setColor(rolle, QColor(farbe))
    # Abgeschaltete Bedienelemente sollen sichtbar abgeschaltet sein, sonst
    # sind "nicht anklickbar" und "gerade nichts ausgewaehlt" nicht zu
    # unterscheiden.
    for rolle in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        qp.setColor(
            QPalette.ColorGroup.Disabled, rolle, QColor(palette.text_muted)
        )
    app.setPalette(qp)


#: Einstellung -> Feld der Palette. Nur die Farben der Editor-Hervorhebung
#: sind einstellbar: sie tragen eine Bedeutung, die jeder anders sehen mag.
#: Die uebrigen Farben folgen dem Systemschema und bleiben es auch.
#: Einstellung -> Palettenfelder, die sie setzt. Bewusst MEHRERE Felder je
#: Eintrag: die Hintergrundfarbe faerbt vier Felder auf einmal, weil alle
#: Flaechen des Programms dieselbe Farbe tragen. Waere hier nur `window`
#: genannt, faerbte die Einstellung die Leisten um und liesse Editor,
#: Kachelraster und Eingabefelder auf dem alten Wert zurueck - genau der
#: Flickenteppich, den die eine Flaechenfarbe beseitigt hat.
OVERRIDABLE: dict[str, tuple[str, ...]] = {
    "ui.color_segment_rare": ("segment_rare",),
    "ui.color_segment_common": ("segment_common",),
    "ui.color_segment_negative": ("segment_negative",),
    # Die Rahmenfarben. Sie stehen hier aus einem anderen Grund als die
    # Segmentfarben: die tragen eine BEDEUTUNG, die jeder anders sehen mag -
    # diese tragen die ganze Gliederung, weil alle Flaechen gleich gefaerbt
    # sind. Wem der Kontrast nicht passt, dem hilft keine andere Einstellung.
    "ui.color_accent": ("accent",),
    "ui.color_accent_dim": ("accent_dim",),
    # Die Flaechenfarbe des ganzen Programms.
    "ui.color_background": ("window", "surface", "tile_background", "input_background"),
}

#: Um wie viel sich die Platzhalterflaeche vom Hintergrund abheben soll. Sie
#: zeigt eine Kachel, deren Vorschaubild noch entsteht; ohne Unterschied waere
#: sie vom leeren Raster nicht zu trennen.
PLACEHOLDER_SHIFT = 8


def _derive_from_background(background: QColor, dark_scheme: bool) -> dict[str, QColor]:
    """Was sich aus einer gewaehlten Hintergrundfarbe zwangslaeufig ergibt.

    Zwei Ableitungen, beide notwendig, damit eine freie Farbwahl das Programm
    nicht unbrauchbar macht:

    **Der Platzhalter** hebt sich immer um dieselbe Kleinigkeit ab - auf
    dunklem Grund heller, auf hellem dunkler. Ein fester Wert waere bei der
    Haelfte aller Farben unsichtbar.

    **Die Schriftfarbe folgt der HELLIGKEIT des Hintergrunds, nicht dem
    Schema.** Wer im Dunkelmodus einen hellen Hintergrund waehlt, bekaeme
    sonst hellgraue Schrift auf Weiss - das Programm waere nicht mehr zu
    lesen, und die Ursache waere nicht zu erraten. Diese Regel ist der Grund,
    warum die Einstellung ueberhaupt gefahrlos ist.
    """
    hell = background.lightness() >= 128
    versatz = -PLACEHOLDER_SHIFT if hell else PLACEHOLDER_SHIFT
    platzhalter = QColor(
        max(0, min(255, background.red() + versatz)),
        max(0, min(255, background.green() + versatz)),
        max(0, min(255, background.blue() + versatz)),
    )
    abgeleitet: dict[str, QColor] = {"placeholder": platzhalter}

    # Nur eingreifen, wenn Hintergrund und Schema NICHT zusammenpassen.
    if hell == dark_scheme:
        gegenschema = _LIGHT if hell else _DARK
        abgeleitet["text"] = QColor(gegenschema.text)
        abgeleitet["text_muted"] = QColor(gegenschema.text_muted)
        abgeleitet["selection_text"] = QColor(gegenschema.selection_text)
    return abgeleitet


def scheme_name(dark: bool) -> str:
    return "dark" if dark else "light"


def read_override(raw: str, dark: bool) -> QColor | None:
    """Die eingestellte Farbe fuer dieses Schema, oder None.

    Gespeichert wird je Schema. Wer im Dunkelmodus eine Farbe waehlt, aendert
    nur den Dunkelmodus - im hellen bleibt die Vorgabe. Sonst landete ein
    dunkles Blau auf hellem Grund und waere kaum zu sehen.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    code = parsed.get(scheme_name(dark))
    if not code:
        return None
    color = QColor(str(code))
    return color if color.isValid() else None


def with_overrides(palette: Palette, settings) -> Palette:
    """Legt die eingestellten Farben ueber die Palette des Schemas.

    Die Reihenfolge ist bedeutsam: erst werden die abgeleiteten Werte der
    Hintergrundfarbe gesetzt, dann die ausdruecklich eingestellten. Eine
    Ableitung darf nie eine Wahl ueberschreiben - wer Schrift und Hintergrund
    beide setzt, soll beides bekommen.
    """
    if settings is None:
        return palette

    def gewaehlt(key: str) -> QColor | None:
        try:
            return read_override(settings.get_str(key), palette.dark)
        except Exception:  # noqa: BLE001 - eine fehlende Einstellung ist kein
            return None     # Grund, die ganze Palette zu verwerfen

    changes: dict[str, QColor] = {}

    hintergrund = gewaehlt("ui.color_background")
    if hintergrund is not None:
        changes.update(_derive_from_background(hintergrund, palette.dark))

    for key, fields in OVERRIDABLE.items():
        color = gewaehlt(key)
        if color is None:
            continue
        for field in fields:
            changes[field] = color

    return replace(palette, **changes) if changes else palette


class ThemeManager(QObject):
    """Beobachtet das Systemfarbschema und liefert die passende Palette.

    palette_changed(Palette)
    """

    palette_changed = pyqtSignal(object)

    def __init__(self, preference: str = "system", settings=None) -> None:
        super().__init__()
        self._preference = preference
        self._settings = settings
        self._palette = self._resolve()
        hints = QGuiApplication.styleHints()
        if hints is not None and hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_scheme_changed)

    @property
    def palette(self) -> Palette:
        return self._palette

    @property
    def is_dark(self) -> bool:
        return self._palette.dark

    def set_preference(self, preference: str) -> None:
        self._preference = preference
        # Zuerst der Stil ohne Palette: _resolve() liest bei "system" das
        # Schema, das setColorScheme gerade erst gesetzt hat - vorher weiss
        # niemand, ob hell oder dunkel gilt. Die Einstellungen kommen trotzdem
        # mit, sonst traegt das Fenster fuer einen Wimpernschlag die
        # Standardfarben des Schemas statt der gewaehlten.
        apply_application_style(preference, settings=self._settings)
        self._apply(self._resolve())

    def _on_scheme_changed(self, _scheme: object) -> None:
        self._apply(self._resolve())

    def refresh(self) -> None:
        """Nach einer Farbaenderung neu aufloesen und melden.

        Eigener Weg statt _apply(): dort wird auf Identitaet verglichen, und
        eine geaenderte Farbe ergibt ein neues, aber gleichrangiges Objekt.
        """
        palette = self._resolve()
        self._palette = palette
        # Auch die von Qt gezeichneten Teile neu einfaerben. Ohne das aendert
        # eine im Dialog gewaehlte Akzentfarbe nur, was die Anwendung selbst
        # malt - die Rahmen, Rollbalken und Pfeile blieben auf dem alten Wert.
        apply_application_style(self._preference, palette)
        self.palette_changed.emit(palette)

    def _apply(self, palette: Palette) -> None:
        if palette is not self._palette:
            self._palette = palette
            # Die von Qt gezeichneten Widgets muessen mitziehen, sonst bleibt
            # das Fenster hell, waehrend die Kacheln dunkel werden.
            apply_application_style(self._preference, palette)
            self.palette_changed.emit(palette)

    def _resolve(self) -> Palette:
        return with_overrides(self._base_palette(), self._settings)

    def _base_palette(self) -> Palette:
        if self._preference == "dark":
            return _DARK
        if self._preference == "light":
            return _LIGHT
        hints = QGuiApplication.styleHints()
        scheme = getattr(hints, "colorScheme", None)
        if scheme is not None:
            try:
                return _DARK if scheme() == Qt.ColorScheme.Dark else _LIGHT
            except (TypeError, AttributeError):
                pass
        # Rueckfallweg: Helligkeit der Fensterfarbe auswerten.
        window = QGuiApplication.palette().window().color()
        return _DARK if window.lightness() < 128 else _LIGHT
