"""Eine Seite der Vergleichsansicht: Text, Zeilennummern, Faerbung.

Bewusst ein `QTextEdit` und nicht der `PromptEditor` aus `app/gui/editor`. Der
Grund ist der Rollbalken:

* `QPlainTextEdit` zaehlt senkrecht in **Anzeigezeilen**. Ein Abstand unter
  einer Zeile ist fuer ihn nicht vorhanden - der Hoehenausgleich, von dem die
  ganze Ausrichtung abhaengt, waere damit unsichtbar.
* `QTextEdit` zaehlt in **Pixeln**. Sind beide Dokumente gleich hoch, ist der
  Gleichlauf eine Wertuebernahme ohne Umrechnung, und es gibt nichts mehr, was
  driften koennte.

Der Preis ist, dass `QTextEdit` bei sehr grossen Dokumenten langsamer aufbaut.
Fuer Prompts von einigen hundert Zeilen faellt das nicht ins Gewicht; fuer die
Genauigkeit der Ausrichtung ist es entscheidend.

Die Zeilennummernspalte sitzt im linken Rand des Sichtfensters
(`setViewportMargins`). Ein Platzhalter bekommt **keine** Nummer - er ist keine
Zeile. Genau daran erkennt der Benutzer auf einen Blick, dass dort nichts
fehlt, sondern die andere Seite mehr hat.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QPainter,
    QTextBlockFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import QMenu, QTextEdit, QWidget

from app.analysis.textdiff import ComparisonResult, DiffStatus
from app.gui.compare.diff_highlighter import DiffHighlighter
from app.gui.theme import Palette

#: Seiten der Ansicht. Als Zeichenkette, weil sie unveraendert in Aktionen und
#: Signalen weitergereicht wird.
LEFT = "left"
RIGHT = "right"

#: Luft links und rechts der Zeilennummern.
GUTTER_PADDING = 10


class LineNumberArea(QWidget):
    """Der Streifen mit den Zeilennummern.

    Ein eigenes Widget statt eines Anhaengsels im Sichtfenster: so bekommt es
    seine Zeichenereignisse von Qt und muss nicht bei jedem Bildlauf von Hand
    angestossen werden.
    """

    def __init__(self, pane: "DiffPane") -> None:
        super().__init__(pane)
        self._pane = pane

    def sizeHint(self):  # noqa: N802 (Qt)
        from PyQt6.QtCore import QSize

        return QSize(self._pane.gutter_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt)
        self._pane.paint_line_numbers(self, event)


class DiffPane(QTextEdit):
    """context_action(str) - eine Anforderung aus dem Kontextmenue."""

    context_action = pyqtSignal(str)

    def __init__(
        self,
        side: str,
        palette: Palette,
        *,
        font_size: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._side = side
        self._palette = palette
        self._result: ComparisonResult | None = None
        self._numbers: tuple[int | None, ...] = ()
        self._current_block: int | None = None

        self.highlighter = DiffHighlighter(self.document(), palette, side)

        self.setReadOnly(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.setPlaceholderText("Kein Prompt geladen")

        # Die Spalte muss VOR set_font_size stehen: die Schriftgroesse
        # bestimmt ihre Breite, und das Setzen laesst sie sich neu vermessen.
        self._gutter = LineNumberArea(self)
        # Der Zeilenhintergrund wird in paintEvent selbst gemalt. Qt darf das
        # Sichtfenster deshalb nicht vorher fuellen, und beim Rollen muss die
        # ganze Flaeche neu gezeichnet werden - beim Blitten bliebe sonst der
        # Hintergrund der alten Zeile stehen, waehrend der Text der neuen
        # darueber landet.
        self.viewport().setAutoFillBackground(False)
        self.verticalScrollBar().valueChanged.connect(self._gutter.update)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)
        self.textChanged.connect(self._refresh_gutter_width)
        self.set_font_size(font_size)

    # Eigenschaften ------------------------------------------------------------

    @property
    def side(self) -> str:
        return self._side

    @property
    def result(self) -> ComparisonResult | None:
        return self._result

    def set_font_size(self, points: int) -> None:
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(points)
        self.setFont(font)
        self._refresh_gutter_width()

    def set_palette_colors(self, palette: Palette) -> None:
        self._palette = palette
        self.highlighter.set_palette_colors(palette)
        self.viewport().update()
        self._gutter.update()

    def set_wrap(self, enabled: bool) -> None:
        """Zeilenumbruch ein- oder ausschalten.

        Aus entspricht WinMerge: eine logische Zeile ist genau eine
        Anzeigezeile, die Ausrichtung stimmt dann von selbst. An ist die
        Vorgabe, weil Prompts Fliesstext sind und waagerechtes Scrollen ueber
        vierhundert Zeichen niemandem hilft; die gleiche Hoehe stellt dann der
        Hoehenausgleich her.
        """
        self.setLineWrapMode(
            QTextEdit.LineWrapMode.WidgetWidth
            if enabled
            else QTextEdit.LineWrapMode.NoWrap
        )

    # Inhalt -------------------------------------------------------------------

    def show_result(self, result: ComparisonResult | None) -> None:
        """Uebernimmt das Vergleichsmodell.

        Der Renderer rechnet hier **nichts** aus. Er setzt den Text, den das
        Modell liefert, und faerbt nach den Zustaenden, die darin stehen. Wo
        frueher Zeichenpositionen zweimal umgerechnet wurden, steht jetzt eine
        Zuweisung.
        """
        self._result = result
        self._current_block = None

        if result is None:
            self.clear()
            self._numbers = ()
            self.highlighter.set_rows(())
            self._refresh_gutter_width()
            self.viewport().update()
            return

        if self._side == LEFT:
            text = result.left_display_text()
            self._numbers = result.left_line_numbers()
        else:
            text = result.right_display_text()
            self._numbers = result.right_line_numbers()

        self.setPlainText(text)
        self.highlighter.set_rows(result.rows)
        self.moveCursor(QTextCursor.MoveOperation.Start)
        self._refresh_gutter_width()
        self.viewport().update()

    def set_current_block(self, index: int | None) -> None:
        """Hebt den gerade angesprungenen Unterschied besonders hervor."""
        if index == self._current_block:
            return
        self._current_block = index
        self.viewport().update()

    @property
    def current_block(self) -> int | None:
        return self._current_block

    # Hoehenausgleich ----------------------------------------------------------

    def base_heights(self) -> list[float]:
        """Hoehe jeder Anzeigezeile OHNE die Ausgleichsabstaende.

        Nachgemessen an Qt 6.11: `blockBoundingRect().height()` zaehlt die
        Blockraender **nicht** mit - nur `top()` verschiebt sich durch sie.
        Das ist hier ein Gluecksfall, denn damit ist die Messung idempotent:
        ein bereits gesetzter Abstand verfaelscht sie nicht, und es muss
        nichts zurueckgesetzt werden, bevor gemessen wird.

        Genau darauf faellt man sonst herein - wer diese Zahl fuer die
        tatsaechlich belegte Hoehe haelt, misst nach dem Ausgleich dieselben
        Unterschiede wie davor und haelt ihn fuer wirkungslos.
        """
        self._ensure_layout()
        layout = self.document().documentLayout()
        hoehen: list[float] = []
        block = self.document().firstBlock()
        while block.isValid():
            hoehen.append(layout.blockBoundingRect(block).height())
            block = block.next()
        return hoehen

    def effective_heights(self) -> list[float]:
        """Der tatsaechlich belegte Platz je Zeile, Abstaende eingerechnet.

        Gerechnet aus den Oberkanten: was zwischen dem Anfang dieser und dem
        Anfang der naechsten Zeile liegt, ist der Platz dieser Zeile. Das ist
        die Groesse, die entscheidet, ob links und rechts buendig stehen -
        und die einzige, mit der sich der Ausgleich nachweisen laesst.
        """
        self._ensure_layout()
        document = self.document()
        layout = document.documentLayout()

        raender: list[float] = []
        letzte_hoehe = 0.0
        block = document.firstBlock()
        while block.isValid():
            rand = layout.blockBoundingRect(block)
            raender.append(rand.top())
            if not block.next().isValid():
                letzte_hoehe = rand.height() + block.blockFormat().bottomMargin()
            block = block.next()

        if not raender:
            return []
        return [
            nachher - vorher for vorher, nachher in zip(raender, raender[1:])
        ] + [letzte_hoehe]

    def _ensure_layout(self) -> None:
        """Erzwingt ein gueltiges Textlayout vor dem Messen.

        Ohne gesetzte Textbreite liefert `blockBoundingRect` lauter Nullen,
        solange das Widget nie sichtbar war - der Umbruch hat dann noch nicht
        stattgefunden, es gibt also nichts zu messen. Ein Ausgleich auf lauter
        Nullen ist kein Ausgleich, sondern ein stiller Fehlschlag: alle
        Abstaende werden null, die Seiten bleiben ungleich hoch, und nichts
        weist darauf hin.
        """
        document = self.document()
        if self.lineWrapMode() == QTextEdit.LineWrapMode.NoWrap:
            document.setTextWidth(-1)
            return
        breite = float(self.viewport().width())
        if breite > 0:
            document.setTextWidth(breite)

    def clear_row_margins(self) -> None:
        self.apply_row_margins([])

    def apply_row_margins(self, margins: list[float]) -> None:
        """Setzt den Abstand unter jeder Zeile.

        Ein leerer Wert bedeutet null. Das Setzen laeuft in EINEM
        Bearbeitungsschritt (`beginEditBlock`), sonst loest jeder einzelne Block
        ein eigenes Neuzeichnen aus - bei vierhundert Zeilen vierhundert Mal.

        Die Signalsperre ist ebenfalls noetig: `textChanged` wuerde sonst den
        Aufrufer erneut anstossen, der gerade dabei ist, die Abstaende zu
        setzen.
        """
        document = self.document()
        gesperrt = self.blockSignals(True)
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        try:
            block = document.firstBlock()
            index = 0
            while block.isValid():
                gewuenscht = margins[index] if index < len(margins) else 0.0
                format_ = block.blockFormat()
                if abs(format_.bottomMargin() - gewuenscht) > 0.01:
                    neu = QTextBlockFormat(format_)
                    neu.setBottomMargin(gewuenscht)
                    cursor.setPosition(block.position())
                    cursor.setBlockFormat(neu)
                block = block.next()
                index += 1
        finally:
            cursor.endEditBlock()
            self.blockSignals(gesperrt)
        self._gutter.update()

    # Zeilen anspringen --------------------------------------------------------

    def row_top(self, display_index: int) -> float:
        """Oberer Rand einer Anzeigezeile im Dokument, in Pixeln.

        Enthaelt die Ausgleichsabstaende - `top()` verschiebt sich durch sie,
        anders als `height()`. Damit ist diese Zahl der unmittelbare Nachweis
        dafuer, dass Zeile n auf beiden Seiten auf gleicher Hoehe beginnt.
        """
        self._ensure_layout()
        block = self.document().findBlockByNumber(display_index)
        if not block.isValid():
            return 0.0
        return self.document().documentLayout().blockBoundingRect(block).top()

    def scroll_to_row(self, display_index: int, *, margin_rows: int = 2) -> None:
        """Bringt eine Zeile ins Sichtfenster, mit etwas Luft darueber.

        Die Luft ist kein Schmuck: ein Unterschied unmittelbar am oberen Rand
        laesst nicht erkennen, was davorsteht - und genau das braucht man, um
        ihn einzuordnen.
        """
        oben = self.row_top(display_index)
        if margin_rows > 0 and display_index > 0:
            voriges = max(0, display_index - margin_rows)
            oben = self.row_top(voriges)
        bar = self.verticalScrollBar()
        bar.setValue(max(bar.minimum(), min(bar.maximum(), int(oben))))

    def top_row(self) -> int:
        """Die oberste ganz oder teilweise sichtbare Anzeigezeile."""
        cursor = self.cursorForPosition(self.viewport().rect().topLeft())
        return cursor.blockNumber()

    def row_at(self, point) -> int:
        return self.cursorForPosition(point).blockNumber()

    # Faerbung -----------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt)
        """Faerbt ganze Zeilen ueber die volle Breite - HINTER dem Text.

        Der naheliegende Weg waere eine Extraauswahl mit
        `FullWidthSelection`. Er ist nachgemessen falsch: eine solche Auswahl
        wird ueber die Zeichenformate gezeichnet und verdeckt damit genau die
        Wortmarkierung, die den Unterschied erst brauchbar macht. Sie verdeckt
        auch eine zweite, spaeter hinzugefuegte Extraauswahl - die Rangfolge
        laesst sich auf diesem Weg also gar nicht herstellen.

        Deshalb wird der Zeilenhintergrund selbst gemalt, bevor Qt das
        Dokument zeichnet. Die Zeichenformate des Highlighters liegen dann
        darueber, und die Rangfolge aus §14 des Auftrags stimmt:

            Wortunterschied  ueber  Zeilenfarbe  ueber  Grundfarbe

        Nebenbei loest es den Platzhalter mit: eine Zeile ohne ein einziges
        Zeichen laesst sich ueber Zeichenformate nicht einfaerben, ueber ein
        gemaltes Rechteck sehr wohl.
        """
        if self._result is not None:
            self._paint_row_backgrounds(event)
        super().paintEvent(event)

    def _paint_row_backgrounds(self, event) -> None:
        painter = QPainter(self.viewport())
        layout = self.document().documentLayout()
        versatz = self.verticalScrollBar().value()
        breite = self.viewport().width()
        sichtbar = event.rect()

        block = self.document().firstBlock()
        while block.isValid():
            rand = layout.blockBoundingRect(block)
            oben = rand.top() - versatz
            if oben > sichtbar.bottom():
                break  # weiter unten ist nichts mehr zu sehen
            if oben + rand.height() >= sichtbar.top():
                index = block.blockNumber()
                if index < len(self._result.rows):
                    painter.fillRect(
                        QRectF(0.0, oben, float(breite), rand.height()),
                        self._row_color(self._result.rows[index]),
                    )
            block = block.next()
        painter.end()

    def _row_color(self, row) -> QColor:
        """Die Hintergrundfarbe einer Zeile auf DIESER Seite.

        Der Platzhalter hat Vorrang vor dem Zustand der Zeile: eine Zeile, die
        auf dieser Seite gar nicht existiert, ist grau - unabhaengig davon, ob
        die Gegenseite sie als hinzugefuegt oder als geaendert fuehrt.

        Gleiche Zeilen bekommen ausdruecklich die Grundfarbe des Widgets und
        nicht etwa nichts. Gemalt wird hinter dem Text; wer hier nichts malt,
        laesst stehen, was vorher an dieser Stelle stand.
        """
        theme = self._palette
        ist_platzhalter = (
            row.left_is_placeholder if self._side == LEFT else row.right_is_placeholder
        )
        if ist_platzhalter:
            return theme.diff_placeholder

        if row.status is DiffStatus.EQUAL:
            return self.palette().base().color()
        if row.status is DiffStatus.IGNORED:
            return theme.diff_ignored
        if row.status is DiffStatus.MOVED:
            return theme.diff_moved

        if (
            self._current_block is not None
            and row.difference_index == self._current_block
        ):
            return theme.diff_current
        return theme.diff_block

    # Zeilennummern ------------------------------------------------------------

    def gutter_width(self) -> int:
        stellen = max(2, len(str(max((n or 0) for n in self._numbers) if self._numbers else 1)))
        return GUTTER_PADDING * 2 + self.fontMetrics().horizontalAdvance("9") * stellen

    def _refresh_gutter_width(self) -> None:
        breite = self.gutter_width()
        self.setViewportMargins(breite, 0, 0, 0)
        self._place_gutter()

    def _place_gutter(self) -> None:
        rand = self.contentsRect()
        self._gutter.setGeometry(
            QRect(rand.left(), rand.top(), self.gutter_width(), rand.height())
        )

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().resizeEvent(event)
        self._place_gutter()

    def paint_line_numbers(self, area: QWidget, event) -> None:
        """Zeichnet die Nummern der sichtbaren Zeilen.

        Gezeichnet wird nur, was im Ereignisbereich liegt. Bei einem Prompt aus
        vierhundert Zeilen alle vierhundert Nummern zu zeichnen, um zwanzig
        sichtbare zu treffen, waere bei jedem Bildlauf verschwendete Arbeit.
        """
        painter = QPainter(area)
        theme = self._palette
        # Der Streifen traegt die Fensterfarbe, die Ziffern die eigene Farbe
        # aus dem Theme (editorLineNumber.foreground). Hier stand vorher ein
        # fest verdrahtetes Hellgrau - das passte weder zum Dunkelmodus noch
        # zum Akzentschema und war die einzige Literalfarbe der Ansicht.
        painter.fillRect(event.rect(), theme.window)
        painter.setPen(theme.line_number)

        document = self.document()
        layout = document.documentLayout()
        versatz = self.verticalScrollBar().value()
        hoehe_metrik = self.fontMetrics().height()

        block = document.firstBlock()
        while block.isValid():
            rand = layout.blockBoundingRect(block)
            oben = rand.top() - versatz
            if oben > event.rect().bottom():
                break
            if oben + rand.height() >= event.rect().top():
                index = block.blockNumber()
                nummer = self._numbers[index] if index < len(self._numbers) else None
                if nummer is not None:
                    painter.drawText(
                        QRect(
                            0,
                            int(oben),
                            self.gutter_width() - GUTTER_PADDING,
                            max(hoehe_metrik, int(rand.height())),
                        ),
                        int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
                        str(nummer),
                    )
            block = block.next()
        painter.end()

    # Kontextmenue -------------------------------------------------------------

    def _show_menu(self, position) -> None:
        menu = QMenu(self)
        auswahl = self.textCursor().selectedText().strip()

        kopieren = menu.addAction("Kopieren")
        kopieren.setEnabled(bool(auswahl))
        alles = menu.addAction("Gesamten Prompt kopieren")
        alles.setEnabled(bool(self.toPlainText()))
        menu.addSeparator()

        for kennung, beschriftung in (
            ("previous_difference", "Vorheriger Unterschied"),
            ("next_difference", "Naechster Unterschied"),
        ):
            eintrag = menu.addAction(beschriftung)
            eintrag.setData(kennung)
        menu.addSeparator()
        for kennung, beschriftung in (
            ("copy_left_to_right", "Diesen Unterschied nach rechts uebernehmen"),
            ("copy_right_to_left", "Diesen Unterschied nach links uebernehmen"),
        ):
            eintrag = menu.addAction(beschriftung)
            eintrag.setData(kennung)

        gewaehlt = menu.exec(self.mapToGlobal(position))
        if gewaehlt is None:
            return
        if gewaehlt is kopieren:
            self.copy()
        elif gewaehlt is alles:
            self._copy_source_text()
        elif gewaehlt.data():
            self.context_action.emit(str(gewaehlt.data()))

    def _copy_source_text(self) -> None:
        """Kopiert den QUELLTEXT, nicht die Anzeige.

        Der Unterschied sind die Platzhalter. Wer den Prompt herauskopiert, will
        den Prompt - nicht eine Fassung mit eingestreuten Leerzeilen, die nur
        fuer die Gegenueberstellung existieren.
        """
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return
        if self._result is None:
            clipboard.setText(self.toPlainText())
            return
        clipboard.setText(
            self._result.left_source_text()
            if self._side == LEFT
            else self._result.right_source_text()
        )
