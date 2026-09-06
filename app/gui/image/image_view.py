"""Bildansicht mit Zoom und Schwenken.

QGraphicsView statt QLabel: Zoom und Schwenken sind damit Transformationen der
Ansicht statt wiederholter Skalierung eines Pixmaps. Bei einem 4000-Pixel-Bild
ist das der Unterschied zwischen fluessigem und ruckelndem Zoom.

Das Originalbild wird ueber den ImageService geladen und kommt als Bytes an -
diese Klasse fasst keine Datei an.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
    QWidget,
)

from app.dto.models import ImageItem

MIN_SCALE = 0.05
MAX_SCALE = 20.0


class ImageView(QGraphicsView):
    """context_action(str, ImageItem), image_clicked(ImageItem)"""

    context_action = pyqtSignal(str, object)
    image_clicked = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._item: ImageItem | None = None
        self._fitted = True
        #: Nur fuer die Beschriftung des Kontextmenues. Wird von aussen
        #: gesetzt, damit der Konstruktor unveraendert bleibt - ein
        #: positionell eingefuegtes Argument hat in M3 schon einmal Schaden
        #: angerichtet (siehe doc/STATUS.md).
        self._selection = None

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    # Inhalt -------------------------------------------------------------------

    @property
    def current_item(self) -> ImageItem | None:
        return self._item

    def show_placeholder(self, text: str = "Kein Bild ausgewaehlt") -> None:
        self._scene.clear()
        self._pixmap_item = None
        self._item = None
        self._scene.addText(text)

    def show_image(self, item: ImageItem, data: bytes | None) -> None:
        """Zeigt ein Bild. data=None bedeutet: Original nicht lesbar."""
        self._item = item
        self._scene.clear()
        self._pixmap_item = None

        if data is None:
            self._scene.addText("Bild nicht lesbar:\n" + item.filename)
            return

        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self._scene.addText("Bild beschaedigt:\n" + item.filename)
            return

        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fit_to_window()

    def show_pixmap(self, item: ImageItem, pixmap: QPixmap) -> None:
        """Zeigt ein bereits geladenes Vorschaubild als Zwischenstand.

        Damit erscheint sofort etwas, waehrend das Original im Hintergrund
        nachgeladen wird.
        """
        self._item = item
        self._scene.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fit_to_window()

    # Zoom ---------------------------------------------------------------------

    def fit_to_window(self) -> None:
        if self._pixmap_item is None:
            return
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._fitted = True

    def zoom_to_original(self) -> None:
        if self._pixmap_item is None:
            return
        self.resetTransform()
        self._fitted = False

    def zoom_by(self, factor: float) -> None:
        if self._pixmap_item is None:
            return
        current = self.transform().m11()
        target = current * factor
        if target < MIN_SCALE or target > MAX_SCALE:
            return
        self.scale(factor, factor)
        self._fitted = False

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 (Qt)
        if self._pixmap_item is None:
            super().wheelEvent(event)
            return
        self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().resizeEvent(event)
        if self._fitted:
            self.fit_to_window()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt)
        """Doppelklick oeffnet das Original im Standardviewer.

        Spezifikation 01 nennt dafuer den Linksklick. Ein einfacher Klick
        kollidiert allerdings mit dem Schwenken, das dieselbe Taste braucht -
        deshalb der Doppelklick.
        """
        if self._item is not None:
            self.image_clicked.emit(self._item)
        event.accept()

    # Kontextmenue -------------------------------------------------------------

    def set_selection_service(self, service) -> None:
        self._selection = service

    def _mark_label(self) -> str:
        """Nennt, was der Klick tut - nicht, was der Zustand ist."""
        if self._selection is not None and self._item is not None:
            if self._selection.is_marked(self._item.id):
                return "Markierung entfernen"
        return "Fuer Vergleich markieren"

    def _show_menu(self, position: QPoint) -> None:
        if self._item is None:
            return
        menu = QMenu(self)
        entries = [
            ("fit", "An Fenster anpassen"),
            ("original", "Originalgroesse"),
            (None, None),
            ("open", "Im Standardviewer oeffnen"),
            ("reveal", "Im Explorer anzeigen"),
            (None, None),
            ("mark", self._mark_label()),
            ("compare", "Vergleich starten"),
            ("evolution", "Prompt Evolution anzeigen"),
        ]
        for action_id, label in entries:
            if action_id is None:
                menu.addSeparator()
                continue
            action = menu.addAction(label)
            action.setData(action_id)
            action.setEnabled(True)

        chosen = menu.exec(self.viewport().mapToGlobal(position))
        if chosen is None:
            return
        action_id = str(chosen.data())
        if action_id == "fit":
            self.fit_to_window()
        elif action_id == "original":
            self.zoom_to_original()
        else:
            self.context_action.emit(action_id, self._item)
