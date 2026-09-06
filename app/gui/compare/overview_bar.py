"""Uebersichtsleiste: alle Unterschiede eines Prompts auf einen Blick.

Ein langer Prompt passt nicht ins Sichtfenster. Ohne Uebersicht muss man
scrollen, um zu wissen, ob unten noch etwas kommt - und das ist genau die
Frage, mit der man einen Vergleich aufmacht.

Die Leiste bildet alle Anzeigezeilen auf ihre Hoehe ab und traegt fuer jeden
Difference Block einen farbigen Strich ein, in derselben Farbe wie im Text.
Dazu ein Rahmen fuer den gerade sichtbaren Ausschnitt, wie ihn Editoren mit
Minimap zeigen. Ein Klick springt an die Stelle.

Gerechnet wird in **Zeilen**, nicht mehr in Zeichen. Das ist der Unterschied
zur frueheren Fassung und der Grund, warum die Striche jetzt dort sitzen, wo
der Unterschied auch tatsaechlich steht: eine Zeile aus vier Zeichen und eine
aus vierhundert nehmen im Editor dieselbe Hoehe ein, in einer Zeichenzaehlung
aber das Hundertfache.

Bewusst kein verkleinertes Abbild des Textes: bei einem Prompt aus vierhundert
Zeilen waere das ein grauer Streifen ohne Aussage. Die Unterschiede sind die
Auskunft, nicht der Text.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPaintEvent
from PyQt6.QtWidgets import QWidget

from app.analysis.textdiff import ComparisonResult, DiffStatus, DifferenceBlock
from app.gui.theme import Palette

#: Breite der Leiste. Schmal genug, um neben dem Text nicht zu stoeren, breit
#: genug, um mit der Maus getroffen zu werden.
BAR_WIDTH = 14
#: Kleinste Hoehe eines Strichs. Ohne sie waere ein einzelner geaenderter Satz
#: in einem langen Prompt unsichtbar - also genau der Fall, der zaehlt.
MIN_MARK = 3


class OverviewBar(QWidget):
    """row_clicked(display_index) - der Benutzer moechte zu dieser Zeile."""

    row_clicked = pyqtSignal(int)

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._blocks: tuple[DifferenceBlock, ...] = ()
        self._rows = 1
        self._visible = (0.0, 1.0)
        self._current: int | None = None
        self.setFixedWidth(BAR_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Unterschiede im ganzen Prompt - klicken springt hin")

    # Inhalt -------------------------------------------------------------------

    def set_result(self, result: ComparisonResult | None) -> None:
        """Nur die UNTERSCHIEDE eintragen.

        Gleiche Abschnitte sind der Normalfall und machen bei aehnlichen
        Prompts fast die ganze Laenge aus. Eine Leiste, die sie mitzeichnet,
        waere durchgehend gefaerbt und damit ohne Aussage.

        Ignorierte Unterschiede kommen mit hinein - sie sind ja sichtbar, und
        wer die Vergleichsoptionen eingeschaltet hat, will trotzdem wissen, wo
        etwas nachgegeben hat. Sie bekommen ihre eigene, blassere Farbe.
        """
        if result is None:
            self._blocks = ()
            self._rows = 1
        else:
            self._blocks = result.differences
            self._rows = max(1, result.row_count)
        self.update()

    def set_current_block(self, index: int | None) -> None:
        if index == self._current:
            return
        self._current = index
        self.update()

    def set_visible_range(self, first: float, last: float) -> None:
        """Der gerade sichtbare Ausschnitt, als Anteil von 0 bis 1."""
        self._visible = (max(0.0, first), min(1.0, last))
        self.update()

    def set_palette_colors(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def clear(self) -> None:
        self._blocks = ()
        self._current = None
        self.update()

    @property
    def block_count(self) -> int:
        return len(self._blocks)

    # Zeichnen -----------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt)
        painter = QPainter(self)
        theme = self._palette
        painter.fillRect(self.rect(), theme.diff_placeholder)

        hoehe = self.height()
        for block in self._blocks:
            oben = int(hoehe * block.first_row / self._rows)
            unten = int(hoehe * (block.last_row + 1) / self._rows)
            painter.fillRect(
                QRect(2, oben, self.width() - 4, max(MIN_MARK, unten - oben)),
                self._color_for(block),
            )

        # Der sichtbare Ausschnitt als Rahmen, wie in einer Minimap.
        erste, letzte = self._visible
        if letzte > erste:
            rahmen = QRect(
                0,
                int(hoehe * erste),
                self.width() - 1,
                max(MIN_MARK, int(hoehe * (letzte - erste))),
            )
            painter.setPen(QColor(theme.text_muted))
            painter.drawRect(rahmen)
        painter.end()

    def _color_for(self, block: DifferenceBlock) -> QColor:
        theme = self._palette
        if block.index == self._current:
            return theme.diff_current
        if block.type is DiffStatus.IGNORED:
            return theme.diff_ignored
        if block.type is DiffStatus.MOVED:
            return theme.diff_moved
        return theme.diff_block

    # Bedienung ----------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt)
        if self.height() > 0:
            anteil = event.position().y() / self.height()
            zeile = int(anteil * self._rows)
            self.row_clicked.emit(max(0, min(self._rows - 1, zeile)))
        super().mousePressEvent(event)
