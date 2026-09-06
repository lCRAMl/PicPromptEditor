"""Wort- und Zeichenunterschiede innerhalb einer Zeile einfaerben.

Die zweite von zwei Hervorhebungsebenen:

    Ebene 1   ganze Zeile, ueber die volle Breite  -> Extraauswahl im DiffPane
    Ebene 2   die tatsaechlich geaenderten Woerter -> hier

Ebene 2 liegt ueber Ebene 1, weil Zeichenformate ueber der Extraauswahl
gezeichnet werden. Damit ergibt sich die Rangfolge aus §14 des Auftrags ohne
Zutun: Wortunterschied schlaegt Blockfarbe, Blockfarbe schlaegt Platzhalter.

Diese Klasse rechnet **nichts** aus. Sie liest die Abschnitte, die der
Vergleichskern geliefert hat, und faerbt sie. Ihr Vorgaenger bekam Spannen im
Rohtext der Datei und musste sie zweimal umrechnen - erst um die verworfenen
Wagenrueckluf-Zeichen, dann um die eingeschobenen Leerzeilen. Beides entfaellt:
die Abschnitte zaehlen ab Zeilenanfang, und die Zeile ist genau der Block, den
Qt gerade zeichnet.
"""

from __future__ import annotations

from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QTextDocument

from app.analysis.textdiff import AlignedRow, SegmentStatus
from app.gui.theme import Palette


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, document: QTextDocument, palette: Palette, side: str) -> None:
        super().__init__(document)
        self._palette = palette
        self._side = side
        self._rows: tuple[AlignedRow, ...] = ()
        self._enabled = True

    # Steuerung ----------------------------------------------------------------

    def set_rows(self, rows: tuple[AlignedRow, ...]) -> None:
        self._rows = rows
        self.rehighlight()

    def set_palette_colors(self, palette: Palette) -> None:
        self._palette = palette
        self.rehighlight()

    def set_enabled(self, enabled: bool) -> None:
        """Schaltet die Wortebene ab.

        Bei sehr aehnlichen Prompts kann sie unruhig wirken; dann genuegt die
        Zeilenebene. Die Zeilenebene selbst bleibt unberuehrt - sie ist die
        Auskunft, ohne die der Vergleich wertlos waere.
        """
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.rehighlight()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def segments_of(self, display_index: int) -> tuple:
        """Die Abschnitte dieser Zeile auf DIESER Seite - fuer Tests."""
        if not 0 <= display_index < len(self._rows):
            return ()
        row = self._rows[display_index]
        return row.left_segments if self._side == "left" else row.right_segments

    # Zeichnen -----------------------------------------------------------------

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt)
        if not self._enabled or not self._rows:
            return

        index = self.currentBlock().blockNumber()
        if not 0 <= index < len(self._rows):
            return

        for segment in self.segments_of(index):
            if segment.status is SegmentStatus.EQUAL:
                continue
            start = max(0, segment.start)
            ende = min(len(text), segment.end)
            if ende > start:
                self.setFormat(start, ende - start, self._format_for(segment.status))

    def _format_for(self, status: SegmentStatus) -> QTextCharFormat:
        """Farbe eines Abschnitts.

        REMOVED bekommt einen eigenen Ton. Das ist die einzige Stelle, an der
        die Ansicht zwischen "ausgetauscht" und "weggefallen" unterscheidet -
        auf Zeilenebene tun das die Platzhalter, innerhalb einer Zeile gibt es
        keine. Ohne diesen Ton saehe eine Zeile, aus der ein Wort geloescht
        wurde, genauso aus wie eine, in der eines ersetzt wurde.
        """
        fmt = QTextCharFormat()
        theme = self._palette
        if status is SegmentStatus.REMOVED:
            fmt.setBackground(theme.diff_word_deleted)
        else:
            fmt.setBackground(theme.diff_word)
        return fmt
