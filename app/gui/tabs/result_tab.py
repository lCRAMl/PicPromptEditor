"""Ergebnistab: Kachelraster links, Vorschau rechts.

Spezifikation 01 verlangt eigene Tabs fuer Suchergebnisse, Analysen und
Segmentsuche. Ein solcher Tab muss selbsttragend sein: er ersetzt die gesamte
Ansicht des Bibliothekstabs, also auch dessen Bildansicht und Prompt-Editor.
Ein reines Kachelraster wuerde bedeuten, dass man zum Lesen eines Prompts
zurueck in die Bibliothek wechseln muss.

Der Ordnerbaum fehlt bewusst - er gehoert zur Navigation der Bibliothek, nicht
zu einer bereits feststehenden Ergebnismenge.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QSplitter, QWidget

from app.gui.library.grid_snap import GridSnapper

from app.dto.models import ImageItem, ResultSet
from app.gui.common.preview import PreviewController
from app.gui.library.explorer_panel import ExplorerPanel
from app.gui.theme import Palette
from app.services.selection_service import SelectionService


class ResultTab(QWidget):
    """context_action(str, ImageItem), item_activated(ImageItem)"""

    context_action = pyqtSignal(str, object)
    item_activated = pyqtSignal(object)

    def __init__(
        self,
        application,
        palette: Palette,
        selection: SelectionService,
        *,
        title: str,
        key: str,
        tile_size: int = 256,
        font_size: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.key = key

        self.explorer = ExplorerPanel(
            application, palette, selection, tile_size, show_origin=True, parent=self
        )
        self.preview = PreviewController(application, font_size, palette=palette, parent=self)

        preview_split = QSplitter(Qt.Orientation.Vertical)
        preview_split.addWidget(self.preview.image_view)
        preview_split.addWidget(self.preview.editor_panel())
        preview_split.setStretchFactor(0, 3)
        preview_split.setStretchFactor(1, 2)
        preview_split.setSizes([420, 320])

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.explorer)
        split.addWidget(preview_split)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([900, 560])
        self._splitter = split

        # Der Trenner rastet auf ganze Kachelspalten ein - dieselbe Frage wie
        # bei der Fensterbreite, nur eine Ebene tiefer. Ohne das endet das
        # Raster auf einer halben Spalte, und rechts steht ein angeschnittenes
        # Bild.
        self._snapper = GridSnapper(
            split,
            self.explorer.view,
            index=0,
            enabled=lambda: _snap_enabled(application),
            parent=self,
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)

        self.explorer.selection_changed.connect(self.preview.show_item)
        self.explorer.item_activated.connect(self.item_activated.emit)
        self.explorer.context_action.connect(self.context_action.emit)
        self.preview.context_action.connect(self.context_action.emit)
        self.preview.open_requested.connect(
            lambda item: self.context_action.emit("open", item)
        )

    # Inhalt -------------------------------------------------------------------

    def set_results(self, results: ResultSet) -> None:
        self.explorer.set_results(results)
        if results.items:
            # Erste Kachel vorauswaehlen, damit der Tab nicht leer wirkt.
            self.explorer.view.setCurrentIndex(self.explorer.model.index(0))
        else:
            self.preview.clear()

    def apply_thumbnails(self, batch: list[tuple[int, bytes]]) -> None:
        self.explorer.apply_thumbnails(batch)

    def current_item(self) -> ImageItem | None:
        return self.explorer.current_item()

    def set_tile_size(self, size: int) -> None:
        self.explorer.set_tile_size(size)

    def set_palette_colors(self, palette: Palette) -> None:
        self.explorer.set_palette_colors(palette)
        self.preview.set_palette_colors(palette)


def _snap_enabled(application) -> bool:
    """Ob die Einstellung das Einrasten erlaubt.

    Bei jedem Einrasten frisch gefragt statt einmal gemerkt: eine Aenderung im
    Einstellungsdialog soll sofort wirken und nicht erst beim naechsten
    Oeffnen des Tabs.
    """
    settings = getattr(application, "settings", None)
    if settings is None:
        return True
    try:
        return bool(settings.get_bool("ui.snap_window_to_tiles"))
    except Exception:  # noqa: BLE001 - eine fehlende Einstellung ist kein Grund,
        return True     # das Einrasten abzuschalten
