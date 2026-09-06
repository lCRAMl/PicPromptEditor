"""Detailtab eines einzelnen Bildes.

Spezifikation 01 zum Thumbnail Explorer: "Linksklick laedt Bild und Prompt,
Doppelklick oeffnet einen neuen Tab." In M1 oeffnete der Doppelklick noch den
Windows-Standardviewer; das war eine Abkuerzung und ist hier korrigiert.

Der Tab zeigt dasselbe Bild gross und den vollstaendigen Prompt daneben. Er
eignet sich zum laengeren Lesen eines Prompts, waehrend im Bibliothekstab
weitergesucht wird.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QSplitter, QWidget

from app.dto.models import ImageItem


class DetailTab(QWidget):
    """context_action(str, ImageItem)"""

    context_action = pyqtSignal(str, object)

    def __init__(
        self,
        application,
        item: ImageItem,
        *,
        font_size: int = 10,
        palette=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        from app.gui.common.preview import PreviewController

        self.item = item
        self.key = "detail:" + str(item.id)
        self.title = item.filename

        self.preview = PreviewController(application, font_size, palette=palette, parent=self)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.preview.image_view)
        split.addWidget(self.preview.editor_panel())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([900, 600])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)

        self.preview.context_action.connect(self.context_action.emit)
        self.preview.open_requested.connect(
            lambda target: self.context_action.emit("open", target)
        )
        self.preview.show_item(item)

    def current_item(self) -> ImageItem:
        return self.item

    def set_palette_colors(self, palette) -> None:
        """Nur der Editor traegt Farbe; das Raster fehlt in diesem Tab."""
        self.preview.set_palette_colors(palette)

    def set_tile_size(self, size: int) -> None:
        """Kein Kachelraster in diesem Tab."""
