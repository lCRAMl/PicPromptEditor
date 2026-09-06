"""Legende zu den Einfaerbungen im Prompt-Editor.

Der Editor faerbt Segmente nach ihrer Unterscheidungskraft ein. Das ist die
zentrale Auskunft der Anwendung - welcher Satz macht diesen Prompt besonders,
und welcher steht ohnehin in jedem zweiten. Ohne Legende ist die Faerbung aber
nur bunt: niemand errraet, dass Grau "Textbaustein" heisst und Blau "selten".

Die Farben kommen aus derselben Palette wie die Hervorhebung selbst, nicht aus
eigenen Literalen. Sonst wuerde die Legende beim Wechsel zwischen hellem und
dunklem Schema etwas anderes zeigen als der Editor.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from app.gui.theme import Palette

SWATCH = 11


class ColorSwatch(QLabel):
    """Ein Farbfeld mit Beschriftung."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._color = QColor(Qt.GlobalColor.transparent)
        self._bold = False

    def set_color(
        self,
        color: QColor,
        *,
        bold: bool = False,
        muted: QColor | None = None,
        border: QColor | None = None,
    ) -> None:
        self._color = QColor(color)
        self._bold = bold
        # Der Rahmen kommt aus der Palette. Vorher stand hier ein festes
        # rgba(128,128,128,0.5) - die einzige Literalfarbe der Legende, und im
        # Akzentschema die einzige Stelle, an der ein Element grau umrandet war.
        rahmen = QColor(border).name() if border is not None else "rgba(128,128,128,0.5)"
        parts = [
            "background-color: " + self._color.name(),
            "border: 1px solid " + rahmen,
            "border-radius: 3px",
            "padding: 1px 6px",
        ]
        if bold:
            parts.append("font-weight: 600")
        if muted is not None:
            parts.append("color: " + QColor(muted).name())
        self.setStyleSheet("; ".join(parts))


class HighlightLegend(QWidget):
    """Erklaert die drei Einfaerbungen des Editors in einer Zeile."""

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        self._intro = QLabel("Einfaerbung:")
        layout.addWidget(self._intro)

        self._rare = ColorSwatch("selten - macht diesen Prompt aus")
        self._common = ColorSwatch("Textbaustein - steht in vielen Prompts")
        self._negative = ColorSwatch("Negativ-Prompt")
        for swatch in (self._rare, self._common, self._negative):
            layout.addWidget(swatch)

        # Nur im Evolutionsbaum sichtbar: dort faerbt der Editor nach
        # Aenderung statt nach Haeufigkeit, und ohne Legende waere die
        # abweichende Farbe unerklaerlich.
        self._added = ColorSwatch("neu gegenueber dem Elternknoten")
        self._modified = ColorSwatch("geaendert")
        for swatch in (self._added, self._modified):
            swatch.setVisible(False)
            layout.addWidget(swatch)
        layout.addStretch(1)

        self.set_palette_colors(palette)

    def set_palette_colors(self, palette: Palette) -> None:
        # Fett und gedaempft genau wie im Highlighter, damit die Legende auch
        # die Schriftauszeichnung zeigt und nicht nur die Flaeche.
        rahmen = palette.accent_dim
        self._rare.set_color(palette.segment_rare, bold=True, border=rahmen)
        self._common.set_color(
            palette.segment_common, muted=palette.text_muted, border=rahmen
        )
        self._negative.set_color(palette.segment_negative, border=rahmen)
        self._added.set_color(palette.diff_added, bold=True, border=rahmen)
        self._modified.set_color(palette.diff_changed, bold=True, border=rahmen)
        self._intro.setStyleSheet("color: " + QColor(palette.text_muted).name())

    def set_change_mode(self, active: bool) -> None:
        """Blendet die Felder fuer neu und geaendert ein oder aus."""
        for swatch in (self._added, self._modified):
            swatch.setVisible(active)
        for swatch in (self._rare, self._common):
            swatch.setVisible(not active)

    def set_threshold_hint(self, min_idf: float) -> None:
        """Nennt den Schwellwert, ab dem ein Segment als selten gilt."""
        self._rare.setToolTip(
            "Ab einer Unterscheidungskraft von "
            + format(min_idf, ".1f")
            + " (Einstellung ui.highlight_min_idf)"
        )
