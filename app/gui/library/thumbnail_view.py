"""Thumbnail Explorer - virtualisierte Kachelansicht.

QListView im IconMode ist ohne Zutun nicht ausreichend virtualisiert: Qt
berechnet beim Modellwechsel die Position jedes Elements. Drei Einstellungen
machen daraus eine Ansicht, die auch mit hunderttausend Kacheln fluessig bleibt:

    setUniformItemSizes(True)   alle Kacheln gleich gross, damit Qt die Position
                                rechnen statt messen kann
    setLayoutMode(Batched)      Layout in Haeppchen statt in einem Block
    setBatchSize(...)           Groesse dieser Haeppchen

Das Nachfordern der Vorschaubilder haengt am Bildlauf und ist entprellt: waehrend
schnellen Scrollens wird nichts angefordert, erst bei Ruhe. Sonst erzeugt ein
Durchziehen des Rollbalkens Auftraege fuer die gesamte Bibliothek.
"""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QPoint, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QResizeEvent, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QMenu,
    QStyleOptionViewItem,
    QWidget,
)

from app.dto.models import ImageItem
from app.gui.library.delegate import ThumbnailDelegate
from app.gui.library.thumbnail_model import ITEM_ROLE, ThumbnailModel
from app.gui.theme import Palette
from app.services.selection_service import SelectionService

MIN_TILE = 64
MAX_TILE = 512


class ThumbnailView(QListView):
    """selection_changed(ImageItem), item_activated(ImageItem),
    context_action(str, ImageItem)"""

    selection_changed = pyqtSignal(object)
    item_activated = pyqtSignal(object)
    context_action = pyqtSignal(str, object)

    def __init__(
        self,
        palette: Palette,
        selection: SelectionService,
        tile_size: int = 256,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tile = tile_size
        self._model = ThumbnailModel(selection, self)
        self._delegate = ThumbnailDelegate(palette, tile_size, self)

        self.setModel(self._model)
        self.setItemDelegate(self._delegate)

        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(200)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Der senkrechte Rollbalken bleibt IMMER stehen, auch wenn er nichts zu
        # rollen hat. Das ist keine Geschmacksfrage, sondern Voraussetzung fuer
        # das Einrasten der Fensterbreite:
        #
        # Erscheint er erst beim Ueberlaufen, springt die Breite des
        # Sichtfensters um seine Staerke. Die Fenstergroesse rastet aber auf
        # einem Grundmass ein, das VORHER berechnet wurde - danach fehlen genau
        # diese Bildpunkte, und die letzte Kachelspalte ist angeschnitten.
        #
        # Aufgefallen ist es, als das Stylesheet die Leiste von 14 auf 16
        # Bildpunkte verbreiterte: der Fehler war vorher schon da, nur zwei
        # Bildpunkte kleiner. Eine feste Reserve macht ihn unmoeglich, statt
        # ihn kleiner zu machen.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._request_timer = QTimer(self)
        self._request_timer.setInterval(120)
        self._request_timer.setSingleShot(True)
        self._request_timer.timeout.connect(self.request_visible)

        self.verticalScrollBar().valueChanged.connect(self._schedule_request)
        self.customContextMenuRequested.connect(self._show_menu)
        self.doubleClicked.connect(self._on_double_click)
        self._model.modelReset.connect(self._schedule_request)

    # Zugriff ------------------------------------------------------------------

    @property
    def thumbnail_model(self) -> ThumbnailModel:
        return self._model

    @property
    def tile_size(self) -> int:
        return self._tile

    def set_palette_colors(self, palette: Palette) -> None:
        self._delegate.set_palette(palette)
        self.viewport().update()

    def set_tile_size(self, size: int) -> None:
        size = max(MIN_TILE, min(MAX_TILE, size))
        if size == self._tile:
            return
        self._tile = size
        self._delegate.set_tile_size(size)
        self.reset()
        self._schedule_request()

    def current_item(self) -> ImageItem | None:
        index = self.currentIndex()
        return index.data(ITEM_ROLE) if index.isValid() else None

    def selected_items(self) -> list[ImageItem]:
        return [i.data(ITEM_ROLE) for i in self.selectedIndexes() if i.isValid()]

    # Ereignisse ---------------------------------------------------------------

    def selectionChanged(self, selected, deselected) -> None:  # noqa: N802 (Qt)
        super().selectionChanged(selected, deselected)
        item = self.current_item()
        if item is not None:
            self.selection_changed.emit(item)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt)
        super().resizeEvent(event)
        self._schedule_request()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt)
        """Strg plus Rad skaliert die Kacheln, wie in Bildverwaltungen ueblich."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = 32 if event.angleDelta().y() > 0 else -32
            self.set_tile_size(self._tile + step)
            event.accept()
            return
        super().wheelEvent(event)

    def _on_double_click(self, index) -> None:
        item = index.data(ITEM_ROLE)
        if item is not None:
            self.item_activated.emit(item)

    # Nachfordern --------------------------------------------------------------

    def _schedule_request(self) -> None:
        self._request_timer.start()

    def request_visible(self) -> None:
        """Fordert den sichtbaren Bereich plus Vorladebereich an.

        Der sichtbare Bereich wird GERECHNET, nicht abgetastet. Zuvor stand
        hier indexAt() auf den beiden Ecken des Sichtfensters, und das ging
        stillschweigend schief: die Ansicht legt ihre Elemente stapelweise aus
        (LayoutMode.Batched, batchSize 200), und ausserhalb des bereits
        ausgelegten Stapels trifft indexAt() nichts. Die untere Ecke lieferte
        deshalb praktisch immer einen ungueltigen Index, das Ende fiel auf den
        Ersatzwert 200 zurueck - und sobald man ueber Zeile 200 hinausgescrollt
        war, lag der Anfang hinter dem Ende. items_in_range() liefert dann eine
        leere Liste, und es wurde **gar kein** Vorschaubild mehr nachgeladen.

        Da alle Kacheln gleich gross sind (setUniformItemSizes), ist die
        Rechnung einfach und trifft immer.
        """
        total = self._model.rowCount()
        if total == 0:
            return

        step = self._grid_step()
        columns = max(1, self.viewport().width() // step.width())
        row_height = max(1, step.height())

        bar = self.verticalScrollBar()
        top = bar.value()
        first_line = top // row_height
        lines_visible = self.viewport().height() // row_height + 2

        first_row = int(first_line * columns)
        last_row = int((first_line + lines_visible) * columns) - 1

        # Eine Bildschirmseite ober- und unterhalb vorladen, damit beim
        # Scrollen nicht erst nachgeladen wird, wenn die Kachel schon zu sehen ist.
        margin = max(columns, last_row - first_row + 1)
        first_row -= margin
        last_row += margin

        # Am oberen und unteren Anschlag bis zum jeweiligen Ende gehen. Die
        # gerechnete Zeilenhoehe weicht um wenige Pixel von der Auslegung durch
        # Qt ab; ueber tausend Zeilen summiert sich das, und ohne diese Klammer
        # blieben die letzten Kacheln der Liste ohne Vorschaubild.
        if top <= row_height:
            first_row = 0
        if top >= bar.maximum() - row_height:
            last_row = total - 1

        self._model.request_range(first_row, min(total - 1, last_row))

    def surplus_width(self) -> int:
        """Wie viele Bildpunkte breiter die Ansicht ist als ganze Kachelspalten.

        Gemessen am SICHTFENSTER, nicht am Widget: der Rollbalken gehoert nicht
        zur Flaeche, in der Kacheln liegen. Sonst stuende er halb ueber der
        letzten Spalte.
        """
        step = self._grid_step().width()
        if step <= 0:
            return 0
        usable = self.viewport().width() - self._edge()
        if usable < step:
            return 0
        return usable % step

    def grid_step_width(self) -> int:
        """Spaltenmass, oeffentlich fuer die Fenstergroesse."""
        return self._grid_step().width()

    def edge_width(self) -> int:
        return self._edge()

    def _edge(self) -> int:
        """Was Qt zusaetzlich zu den Spalten braucht.

        Ausgemessen, nicht hergeleitet: eine Schrittweite von einem Bildpunkt
        ueber den ganzen Bereich zeigt, dass n Spalten ab
        `n * Spaltenmass + spacing + 1` erscheinen - bei 4 Bildpunkten Abstand
        also ab n * 272 + 5. Der zusaetzliche Bildpunkt ist Qts Innenleben;
        gegen eine Messung laesst sich schlecht argumentieren.
        """
        return self.spacing() + 1

    def exact_width_for(self, columns: int) -> int:
        """Sichtfensterbreite, in die genau so viele Spalten passen."""
        return max(1, columns) * self._grid_step().width() + self._edge()

    def visible_columns(self) -> int:
        step = self._grid_step().width()
        if step <= 0:
            return 0
        return max(0, (self.viewport().width() - self._edge()) // step)

    def _grid_step(self) -> QSize:
        """Rastermass einer Kachel einschliesslich Abstand."""
        grid = self.gridSize()
        if grid.isValid() and grid.width() > 0 and grid.height() > 0:
            return grid
        # Der Abstand zaehlt EINMAL je Kachel, nicht zweimal. Qt legt sie so:
        #   Rand(spacing) | Kachel | spacing | Kachel | spacing | ...
        # Der Spaltenabstand ist also Kachelbreite + spacing. Mit dem doppelten
        # Abstand gerechnet, war das Raster je Spalte vier Bildpunkte zu breit;
        # das Einrasten liess dadurch rechts eine Luecke von fast einem
        # Kachelviertel stehen.
        hint = self.itemDelegate().sizeHint(QStyleOptionViewItem(), QModelIndex())
        spacing = self.spacing()
        return QSize(
            max(1, hint.width() + spacing), max(1, hint.height() + spacing)
        )

    # Kontextmenue -------------------------------------------------------------

    def _show_menu(self, position: QPoint) -> None:
        index = self.indexAt(position)
        item = index.data(ITEM_ROLE) if index.isValid() else None
        if item is None:
            return

        menu = QMenu(self)
        entries = [
            ("detail", "In neuem Tab oeffnen"),
            ("open", "Im Standardviewer oeffnen"),
            ("reveal", "Im Explorer anzeigen"),
            (None, None),
            # Der Eintrag nennt, was der Klick TUT - nicht, was der Zustand
            # ist. Ein Menuepunkt "markieren", der in Wahrheit die Markierung
            # entfernt, laesst einen zweimal klicken, um es zu verstehen.
            ("mark", "Markierung entfernen" if self._model.is_marked(item.id)
                     else "Fuer Vergleich markieren"),
            ("compare", "Vergleich starten"),
            (None, None),
            ("similar", "Aehnliche Prompts finden"),
            ("family", "Ganze Promptfamilie anzeigen"),
            ("evolution", "Prompt Evolution anzeigen"),
        ]
        for action_id, label in entries:
            if action_id is None:
                menu.addSeparator()
                continue
            action = menu.addAction(label)
            action.setData(action_id)
            # Alle Eintraege sind ab M6 verfuegbar.
            action.setEnabled(True)

        chosen = menu.exec(self.viewport().mapToGlobal(position))
        if chosen is not None:
            self.context_action.emit(str(chosen.data()), item)
