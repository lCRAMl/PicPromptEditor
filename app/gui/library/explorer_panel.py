"""Kachelraster mit Herkunftszeile.

Spezifikation 01: "Eine Statuszeile zeigt die Herkunft der aktuellen Ergebnisse."
Im Bibliothekstab steht diese Zeile in der Fensterstatusleiste, in einem
Ergebnistab direkt ueber dem Raster - deshalb traegt der Baustein seine eigene
Zeile mit und der Aufrufer entscheidet, ob er sie zeigt.

Der Baustein kapselt ausserdem das Nachfordern der Vorschaubilder. Jede Instanz
haengt an denselben ImageService, hat aber ihr eigenes Modell: ein Ergebnistab
behaelt seinen Inhalt, waehrend im Bibliothekstab weitergeklickt wird.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.dto.models import ImageItem, ResultSet
from app.gui.library.thumbnail_model import ThumbnailModel
from app.gui.library.thumbnail_view import ThumbnailView
from app.gui.theme import Palette
from app.services.selection_service import SelectionService


class ExplorerPanel(QWidget):
    """selection_changed(ImageItem), item_activated(ImageItem),
    context_action(str, ImageItem)"""

    selection_changed = pyqtSignal(object)
    item_activated = pyqtSignal(object)
    context_action = pyqtSignal(str, object)

    def __init__(
        self,
        application,
        palette: Palette,
        selection: SelectionService,
        tile_size: int = 256,
        show_origin: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._images = application.image_service
        self._request_id = 0

        self.view = ThumbnailView(palette, selection, tile_size, self)
        self._origin = QLabel("")
        self._origin.setContentsMargins(4, 2, 4, 2)
        self._origin.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._origin.setVisible(show_origin)

        # Sortierung. Die Reihenfolge, in der die Datenbank liefert, ist fuer
        # den Betrachter willkuerlich; bei tausend Bildern in einem Ordner ist
        # "neueste zuerst" oft das, was man sucht.
        self._sort = QComboBox()
        self._sort.addItems([label for label, _f, _d in ThumbnailModel.SORT_ORDERS])
        self._sort.setToolTip("Sortierung der angezeigten Bilder")
        self._sort.currentIndexChanged.connect(self._on_sort)

        kopf = QHBoxLayout()
        kopf.setContentsMargins(0, 0, 0, 0)
        kopf.setSpacing(6)
        kopf.addWidget(self._origin, 1)
        kopf.addWidget(QLabel("Sortierung:"))
        kopf.addWidget(self._sort)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(kopf)
        layout.addWidget(self.view, 1)

        self.view.selection_changed.connect(self.selection_changed.emit)
        self.view.item_activated.connect(self.item_activated.emit)
        self.view.context_action.connect(self.context_action.emit)
        self.view.thumbnail_model.thumbnails_needed.connect(self._images.request_thumbnails)

    # Inhalt -------------------------------------------------------------------

    @property
    def model(self):
        return self.view.thumbnail_model

    @property
    def origin_text(self) -> str:
        return self._origin.text()

    def item_count(self) -> int:
        return self.view.thumbnail_model.rowCount()

    def current_item(self) -> ImageItem | None:
        return self.view.current_item()

    def set_results(self, results: ResultSet) -> None:
        self.view.thumbnail_model.set_results(results)
        # Die gewaehlte Sortierung gilt weiter, auch fuer das naechste
        # Ergebnis - sonst muesste man sie nach jedem Ordnerklick neu setzen.
        # Bei "Wie geliefert" (Vorgabe) bleibt die Reihenfolge der Abfrage.
        if self._sort.currentIndex() > 0:
            self.view.thumbnail_model.sort_items(self._sort.currentIndex())
        self._origin.setText(results.origin + "  -  " + str(len(results)) + " Bilder")

    def _on_sort(self, index: int) -> None:
        self.view.thumbnail_model.sort_items(index)
        self.view.scrollToTop()
        self.view.request_visible()

    @property
    def sort_box(self) -> QComboBox:
        return self._sort

    def clear(self) -> None:
        self.view.thumbnail_model.clear()
        self._origin.setText("")

    def apply_thumbnails(self, batch: list[tuple[int, bytes]]) -> None:
        self.view.thumbnail_model.set_thumbnails(batch)

    def set_tile_size(self, size: int) -> None:
        self.view.set_tile_size(size)

    def set_palette_colors(self, palette: Palette) -> None:
        self.view.set_palette_colors(palette)

    def refresh_visible(self) -> None:
        self.view.request_visible()
