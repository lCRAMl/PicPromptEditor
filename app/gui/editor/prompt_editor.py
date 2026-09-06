"""Prompt-Editor.

In M1 schreibgeschuetzt (Einstellung ui.editor_read_only). Der Benutzer liest
und kopiert; ein Rueckschreiben auf das NAS ist bewusst nicht umgesetzt, weil es
atomares Schreiben, Konflikterkennung gegen die Datei-Zeitstempel und eine
anschliessende Neuanalyse erfordert.

Syntaxhervorhebung und Segmentsuche kommen mit der Parsing-Engine in M3. Die
Kontextmenue-Eintraege dafuer sind bereits vorhanden und deaktiviert, damit die
Bedienung sich nicht mehr aendert, wenn die Funktion dazukommt.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication, QTextCursor, QTextFormat
from PyQt6.QtWidgets import QMenu, QPlainTextEdit, QTextEdit, QWidget

from app.dto.models import PromptData
from app.gui.common.offsets import translate
from app.gui.editor.highlighter import SegmentHighlighter, SegmentSpan
from app.gui.theme import Palette


class PromptEditor(QPlainTextEdit):
    """segment_action(str, markierter_text)"""

    segment_action = pyqtSignal(str, str)

    def __init__(
        self,
        font_size: int = 10,
        palette: Palette | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._prompt: PromptData | None = None

        if palette is None:
            from app.gui.theme import _LIGHT

            palette = _LIGHT
        self.highlighter = SegmentHighlighter(self.document(), palette)

        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.set_font_size(font_size)
        self.setPlaceholderText("Kein Prompt geladen")

    # Inhalt -------------------------------------------------------------------

    @property
    def prompt(self) -> PromptData | None:
        return self._prompt

    def set_font_size(self, points: int) -> None:
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(points)
        self.setFont(font)

    def show_prompt(self, prompt: PromptData | None) -> None:
        self._prompt = prompt
        self.highlighter.clear_spans()
        if prompt is None:
            self.clear()
            self.setPlaceholderText("Zu diesem Bild existiert kein Prompt")
            return
        self.setPlainText(prompt.text)
        self.moveCursor(self.textCursor().MoveOperation.Start)

    def show_aligned(self, prompt: PromptData | None, text: str) -> None:
        """Zeigt den ausgerichteten Text statt des urspruenglichen.

        Fuer die Vergleichsansicht: dort stehen zwischen den Zeilen
        Leerzeilen, damit beide Seiten auf gleicher Hoehe liegen.
        """
        self._prompt = prompt
        self.highlighter.clear_spans()
        self.setExtraSelections([])
        self.setPlainText(text)
        self.moveCursor(self.textCursor().MoveOperation.Start)

    def set_segment_spans(self, spans: list[SegmentSpan]) -> None:
        """Uebernimmt die Segmentstatistik des aktuellen Prompts.

        Die Zeichenpositionen stammen aus der Datenbank und zaehlen im
        ROHTEXT der Datei. Qt zaehlt anders: setPlainText() wandelt jedes
        Wagenruecklauf-Zeilenvorschub-Paar in einen einzelnen Umbruch, das
        Wagenrueckluf-Zeichen verschwindet also aus dem Dokument.

        Bei einer Datei mit Windows-Zeilenenden verschiebt sich die
        Hervorhebung dadurch mit jeder vorangehenden Zeile um ein weiteres
        Zeichen nach links. Nach drei Zeilen zerfaellt "stylize" in ein
        farbloses "sty" und ein andersfarbiges "lize" - mitten im Wort.
        Deshalb werden die Positionen hier umgerechnet.
        """
        self.highlighter.set_spans(self._translate(spans))

    def _translate(self, spans: list[SegmentSpan]) -> list[SegmentSpan]:
        """Rechnet Rohtextpositionen in Dokumentpositionen um."""
        raw = self._prompt.text if self._prompt is not None else ""
        return translate(spans, raw)

    def set_filler_lines(self, lines, color) -> None:
        """Faerbt eingeschobene Leerzeilen ueber die ganze Zeilenbreite.

        Ueber Extraauswahl statt ueber den Highlighter: eine leere Zeile hat
        kein Zeichen, das sich einfaerben liesse. FullWidthSelection faerbt
        dagegen die Zeile als Ganzes - auch die leere.

        Der Text bleibt dabei unberuehrt. Ihn mit Leerzeichen aufzufuellen
        waere der naheliegende Weg gewesen und haette beim Herauskopieren
        unsichtbaren Unrat hinterlassen.
        """
        auswahl = []
        document = self.document()
        for nummer in lines:
            block = document.findBlockByNumber(int(nummer))
            if not block.isValid():
                continue
            eintrag = QTextEdit.ExtraSelection()
            eintrag.format.setBackground(color)
            eintrag.format.setProperty(
                QTextFormat.Property.FullWidthSelection, True
            )
            cursor = QTextCursor(block)
            cursor.clearSelection()
            eintrag.cursor = cursor
            auswahl.append(eintrag)
        self.setExtraSelections(auswahl)

    def set_change_tokens(self, added, changed) -> None:
        """Hebt hervor, was sich gegenueber dem Elternknoten geaendert hat.

        Wird vom Evolutionsbaum gesetzt: die Spalte "Aenderung" nennt nur die
        Anzahl, der Editor zeigt die Stelle.
        """
        self.highlighter.set_change_tokens(added, changed)

    def set_palette_colors(self, palette: Palette) -> None:
        self.highlighter.set_palette_colors(palette)

    def clear_prompt(self) -> None:
        self._prompt = None
        self.highlighter.clear_spans()
        self.clear()
        self.setPlaceholderText("Kein Prompt geladen")

    def segment_under_cursor(self) -> str:
        """Text des Segments an der Cursorposition.

        Ohne eigene Auswahl ist damit trotzdem eine Segmentsuche moeglich: ein
        Rechtsklick mitten in einen Satz genuegt.
        """
        span = self.highlighter.span_at(self.textCursor().position())
        if span is None:
            return ""
        return self.toPlainText()[span.start : span.end]

    def selected_text(self) -> str:
        return self.textCursor().selectedText().strip()

    # Kontextmenue -------------------------------------------------------------

    def _show_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        selection = self.selected_text() or self.segment_under_cursor()

        copy_action = menu.addAction("Kopieren")
        copy_action.setEnabled(bool(selection))
        copy_all = menu.addAction("Gesamten Prompt kopieren")
        copy_all.setEnabled(bool(self.toPlainText()))
        menu.addSeparator()

        entries = [
            ("search_segment", "Segment suchen", bool(selection)),
            ("similar", "Aehnliche Prompts finden", True),
            ("evolution", "Prompt Evolution oeffnen", True),
        ]
        for action_id, label, available in entries:
            text = label
            if action_id == "search_segment" and selection:
                shortened = selection if len(selection) <= 40 else selection[:39] + "…"
                text = 'Segment suchen: "' + shortened + '"'
            action = menu.addAction(text)
            action.setData(action_id)
            action.setEnabled(bool(available))  # Evolution ab M6

        chosen = menu.exec(self.mapToGlobal(position))
        if chosen is None:
            return
        if chosen is copy_action:
            self.copy()
        elif chosen is copy_all:
            clipboard = QGuiApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(self.toPlainText())
        elif chosen.data():
            self.segment_action.emit(str(chosen.data()), selection)
