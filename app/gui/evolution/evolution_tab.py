"""Evolution-Tab: eine Promptfamilie als Stammbaum.

Spezifikation 01: "Die Darstellung aehnelt einem interaktiven Stammbaum oder
Git-Branch... Jeder Knoten besitzt Thumbnail, repraesentativen Prompt,
Bildanzahl und Haeufigkeitsinformationen. Kontextinformationen werden per
Mouseover angezeigt. Doppelklick zeigt alle Bilder eines Knotens im Thumbnail
Explorer."

Umgesetzt als QTreeWidget, nicht als frei gezeichnete Grafik. Gruende:

* Ein Baum ist genau das, was hier dargestellt wird - die Baumdarstellung ist
  keine Notloesung, sondern die passende Form.
* Sie skaliert. Eine QGraphicsScene mit Knoten und Kanten muesste bei
  fuenfhundert Knoten Layout und Kollisionen selbst rechnen; QTreeWidget
  virtualisiert und bleibt fluessig.
* Auf- und Zuklappen, Tastaturnavigation und Sortierung gibt es geschenkt.

**Der Baum zeigt KEINE Vorschaubilder in den Zeilen.** Ein Symbol von 64
Bildpunkten macht jede Zeile viermal so hoch; bei fuenfzig Knoten passt nichts
mehr auf den Schirm, und der Baum - also die Verzweigung, um die es geht - ist
nicht mehr zu ueberblicken. Das Bild erscheint statt dessen beim Ueberfahren
mit der Maus in einem kleinen Fenster daneben, und die ausgewaehlte Zeile zeigt
es ohnehin gross in der Vorschau rechts.

Ueber dem Baum steht ein Auswahlfeld mit den Familien. Ohne das fuehrt der
einzige Weg zu einer anderen Familie ueber ein Bild aus ihr - man muss also
schon wissen, wo man hinwill, um hinzukommen.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.analysis.evolution import EvolutionNode, EvolutionTree
from app.dto.models import ImageItem
from app.gui.common.preview import PreviewController
from app.gui.theme import Palette
from app.jobs.scan_jobs import QueryJob

NODE_ROLE = int(Qt.ItemDataRole.UserRole) + 1

#: Kantenlaenge des Bildes, das beim Ueberfahren erscheint. Gross genug, um
#: das Motiv zu erkennen, klein genug, um den Baum nicht zu verdecken.
HOVER_SIZE = 220
#: Wartezeit, bevor es erscheint. Ohne sie flackert bei jeder Mausbewegung
#: ueber den Baum ein Fenster auf.
HOVER_DELAY_MS = 220


class EvolutionTab(QWidget):
    """context_action(str, ImageItem), node_activated(prompt_id, label)"""

    context_action = pyqtSignal(str, object)
    node_activated = pyqtSignal(int, str)

    def __init__(
        self,
        application,
        tree: EvolutionTree,
        palette: Palette,
        *,
        font_size: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = application
        self._images = application.image_service
        self._evolution = application.evolution_service
        self._palette = palette
        self._tree = tree

        self.key = evolution_key(tree.cluster_id)
        self.title = "Evolution: Familie " + str(tree.cluster_id)

        self._items: dict[int, QTreeWidgetItem] = {}
        self._image_by_prompt: dict[int, int] = {}
        self._pending_items: list[tuple[int, bytes]] = []
        #: Bild-Id -> Vorschaubild. Frueher landeten diese Bilder als Symbol in
        #: der Zeile; jetzt warten sie hier auf das Fenster beim Ueberfahren.
        self._thumbs: dict[int, QPixmap] = {}
        #: Sperre gegen Rueckkopplung: das Fuellen der Auswahlliste loest
        #: currentIndexChanged aus und wuerde den Baum sofort neu laden.
        self._switching = False

        self._build(font_size)
        self._fill_families()
        self._populate(tree)

    # Aufbau -------------------------------------------------------------------

    def _build(self, font_size: int) -> None:
        self._header = QLabel("")
        self._header.setContentsMargins(6, 4, 6, 4)
        self._header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        # Auswahl der Familie. Ohne sie kommt man nur ueber ein Bild aus der
        # Zielfamilie dorthin - man muesste also schon wissen, wo man hinwill.
        self._family = QComboBox()
        self._family.setMinimumWidth(320)
        self._family.currentIndexChanged.connect(self._on_family_chosen)

        familien = QHBoxLayout()
        familien.setContentsMargins(6, 2, 6, 2)
        familien.setSpacing(8)
        familien.addWidget(QLabel("Familie:"))
        familien.addWidget(self._family)
        familien.addStretch(1)
        self._family_row = QWidget()
        self._family_row.setLayout(familien)

        self._view = QTreeWidget()
        self._view.setColumnCount(4)
        self._view.setHeaderLabels(["Prompt", "Aenderung", "Bilder", "Naehe"])
        self._view.setUniformRowHeights(True)
        self._view.setAlternatingRowColors(True)
        self._view.setExpandsOnDoubleClick(False)
        # Die Promptspalte bekommt den ganzen freien Platz, die drei Zahlen
        # rechts nur so viel, wie sie brauchen. Ohne das stand die Beschriftung
        # in einer Spalte von hundert Bildpunkten und war ab der zweiten
        # Baumebene auf "unv..." abgeschnitten - die Einrueckung frisst die
        # Breite, und gerade die tiefen Knoten sind die interessanten.
        kopf = self._view.header()
        kopf.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for spalte in range(1, 4):
            kopf.setSectionResizeMode(spalte, QHeaderView.ResizeMode.ResizeToContents)
        kopf.setStretchLastSection(False)
        self._view.currentItemChanged.connect(self._on_current_changed)
        self._view.itemDoubleClicked.connect(self._on_double_click)
        # Fuer das Bild beim Ueberfahren. `itemEntered` kommt nur, wenn das
        # Widget die Maus verfolgt - ohne das bliebe das Signal stumm.
        self._view.setMouseTracking(True)
        self._view.viewport().setMouseTracking(True)
        self._view.itemEntered.connect(self._on_item_entered)

        self._hover = _HoverPreview(self)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(HOVER_DELAY_MS)
        self._hover_timer.timeout.connect(self._show_hover)
        self._hover_image: int | None = None

        self._preview = PreviewController(
            self._app, font_size, palette=self._palette, parent=self
        )
        self._preview.context_action.connect(self.context_action.emit)
        self._preview.open_requested.connect(
            lambda item: self.context_action.emit("open", item)
        )

        preview_split = QSplitter(Qt.Orientation.Vertical)
        preview_split.addWidget(self._preview.image_view)
        preview_split.addWidget(self._preview.editor_panel())
        preview_split.setStretchFactor(0, 3)
        preview_split.setStretchFactor(1, 2)
        preview_split.setSizes([420, 320])

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._view)
        split.addWidget(preview_split)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([880, 560])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._family_row)
        layout.addWidget(self._header)
        layout.addWidget(split, 1)

        # Eintreffende Vorschaubilder werden gesammelt uebernommen; einzeln
        # gesetzt zeichnete der Baum bei jedem Bild neu.
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(80)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._flush_icons)
        self._images.thumbnails_ready.connect(self._on_thumbnails)

    # Inhalt -------------------------------------------------------------------

    def _populate(self, tree: EvolutionTree) -> None:
        self._view.clear()
        self._items.clear()
        self._image_by_prompt.clear()

        if tree.root_id is None or not tree.nodes:
            self._header.setText("Kein Baum vorhanden")
            return

        self._header.setText(self._describe(tree))
        self._add_node(tree, tree.root_id, None)
        self._view.expandAll()

        root_item = self._items.get(tree.root_id)
        if root_item is not None:
            self._view.setCurrentItem(root_item)
        self._request_icons()

    def _describe(self, tree: EvolutionTree) -> str:
        images = sum(node.image_count for node in tree.nodes.values())
        text = (
            "Familie " + str(tree.cluster_id)
            + "   |   " + str(tree.size) + " Prompts"
            + ", " + str(images) + " Bilder"
            + "   |   Tiefe " + str(tree.depth)
        )
        if tree.truncated:
            text += "   |   " + str(tree.truncated) + " aeltere Knoten ausgelassen"
        return text

    def _add_node(
        self, tree: EvolutionTree, prompt_id: int, parent: QTreeWidgetItem | None
    ) -> None:
        node = tree.nodes.get(prompt_id)
        if node is None:
            return

        item = QTreeWidgetItem(
            [
                node.label or ("Prompt " + str(prompt_id)),
                node.change_summary(),
                str(node.image_count),
                "" if node.is_root else format(node.similarity, ".2f"),
            ]
        )
        item.setData(0, NODE_ROLE, node)
        item.setToolTip(0, self._tooltip(node))
        if parent is None:
            self._view.addTopLevelItem(item)
        else:
            parent.addChild(item)

        self._items[prompt_id] = item
        if node.image_id is not None:
            self._image_by_prompt[node.image_id] = prompt_id

        for child_id in node.children:
            self._add_node(tree, child_id, item)

    @staticmethod
    def _tooltip(node: EvolutionNode) -> str:
        import datetime

        lines = [node.label or ("Prompt " + str(node.prompt_id))]
        if node.modified_time:
            stamp = datetime.datetime.fromtimestamp(node.modified_time / 1_000_000_000)
            lines.append("Erstellt: " + stamp.strftime("%d.%m.%Y %H:%M"))
        lines.append("Bilder: " + str(node.image_count))
        if not node.is_root:
            lines.append("Naehe zum Elter: " + format(node.similarity, ".2f"))
            lines.append("Aenderung: " + node.change_summary())
        lines.append("")
        lines.append("Doppelklick zeigt alle Bilder dieses Knotens")
        return "\n".join(lines)

    # Familienauswahl ----------------------------------------------------------

    def _fill_families(self) -> None:
        """Fuellt die Auswahlliste mit den Familien, die einen Baum haben.

        Nur solche: eine Liste, die Familien anbietet, deren Baum erst
        gerechnet werden muesste, fuehrt beim Umschalten ins Leere.

        Ist die gerade gezeigte Familie nicht dabei - etwa weil ihr Baum
        soeben ungueltig wurde -, wird sie trotzdem eingetragen. Sonst
        stuende in der Auswahl etwas anderes als im Baum darunter.
        """
        familien = []
        try:
            familien = self._evolution.families()
        except Exception:  # noqa: BLE001 - ohne Liste bleibt der Tab bedienbar
            familien = []

        if not any(f["cluster_id"] == self._tree.cluster_id for f in familien):
            familien.insert(
                0,
                {
                    "cluster_id": self._tree.cluster_id,
                    "node_count": self._tree.size,
                    "image_count": 0,
                },
            )

        self._switching = True
        try:
            self._family.clear()
            for eintrag in familien:
                self._family.addItem(
                    _family_label(eintrag), eintrag["cluster_id"]
                )
            stelle = self._family.findData(self._tree.cluster_id)
            if stelle >= 0:
                self._family.setCurrentIndex(stelle)
        finally:
            self._switching = False

    def _on_family_chosen(self, _index: int) -> None:
        if self._switching:
            return
        cluster_id = self._family.currentData()
        if cluster_id is None or cluster_id == self._tree.cluster_id:
            return

        baum = self._evolution.tree_for_cluster(int(cluster_id))
        if baum is None:
            self._header.setText(
                "Zu Familie " + str(cluster_id) + " gibt es keinen fertigen Baum"
            )
            return

        self._tree = baum
        self.key = evolution_key(baum.cluster_id)
        self.title = "Evolution: Familie " + str(baum.cluster_id)
        self._thumbs.clear()
        self._hide_hover()
        self._preview.clear()
        self._populate(baum)

    # Bild beim Ueberfahren ------------------------------------------------------

    def _on_item_entered(self, item: QTreeWidgetItem, _column: int) -> None:
        node = item.data(0, NODE_ROLE) if item is not None else None
        image_id = getattr(node, "image_id", None)
        if image_id is None:
            self._hide_hover()
            return
        if image_id == self._hover_image and self._hover.isVisible():
            return
        self._hover_image = image_id
        self._hover_timer.start()

    def _show_hover(self) -> None:
        """Zeigt das Bild neben dem Mauszeiger.

        Kein Werkzeugtipp: ein QToolTip kann kein Bild aus dem Speicher
        anzeigen, ohne den Umweg ueber eine Datei oder eingebettetes HTML. Ein
        kleines rahmenloses Fenster ist der geradere Weg und laesst sich
        ausserdem so setzen, dass es nicht ueber den Baum faellt.
        """
        from PyQt6.QtGui import QCursor

        if self._hover_image is None:
            return
        pixmap = self._thumbs.get(self._hover_image)
        if pixmap is None or pixmap.isNull():
            return
        self._hover.show_pixmap(pixmap, QCursor.pos())

    def _hide_hover(self) -> None:
        self._hover_timer.stop()
        self._hover_image = None
        self._hover.hide()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt)
        self._hide_hover()
        super().leaveEvent(event)

    # Vorschaubilder -----------------------------------------------------------

    def _request_icons(self) -> None:
        image_ids = [i for i in self._image_by_prompt]
        if not image_ids:
            return

        def load():
            return self._images.items_for(image_ids)

        job = QueryJob(load, self._app.reporter, title="Vorschau des Baums")
        job.signals.result.connect(self._on_items)
        self._app.jobs.submit(job)

    def _on_items(self, _job_id: int, payload: object) -> None:
        _request_id, items = payload  # type: ignore[misc]
        if not items:
            return
        ready: list[tuple[int, bytes]] = []
        for item in items:
            cached = self._images.cached_thumbnail(item)
            if cached:
                ready.append((item.id, cached))
        if ready:
            self._pending_items.extend(ready)
            self._flush_timer.start()
        self._images.request_thumbnails(list(items))

    def _on_thumbnails(self, batch: list) -> None:
        relevant = [
            (image_id, data)
            for image_id, data in batch
            if image_id in self._image_by_prompt
        ]
        if relevant:
            self._pending_items.extend(relevant)
            self._flush_timer.start()

    def _flush_icons(self) -> None:
        """Die eingetroffenen Bilder merken - NICHT in die Zeilen setzen.

        Frueher wurde hier `item.setIcon` aufgerufen. Genau das machte den
        Baum unbrauchbar hoch. Die Bilder liegen jetzt bereit fuer das Fenster
        beim Ueberfahren; die Zeile bleibt eine Textzeile.
        """
        batch, self._pending_items = self._pending_items, []
        for image_id, data in batch:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self._thumbs[image_id] = pixmap
        # Steht der Mauszeiger schon auf einer Zeile, deren Bild gerade
        # eingetroffen ist, wird es sofort gezeigt.
        if self._hover_image in self._thumbs and self._hover.isVisible():
            self._show_hover()

    # Ereignisse ---------------------------------------------------------------

    def _on_current_changed(self, current: QTreeWidgetItem | None, _previous) -> None:
        if current is None:
            return
        node: EvolutionNode = current.data(0, NODE_ROLE)
        if node is None or node.image_id is None:
            self._preview.clear()
            return

        # Was dieser Knoten gegenueber seinem Elternknoten aendert, wird im
        # Prompt eingefaerbt. Die Spalte "Aenderung" sagt bisher nur, WIE VIEL
        # sich geaendert hat - die Frage ist aber, WAS. Entfernte Segmente
        # stehen im Elternprompt und lassen sich hier nicht zeigen; dafuer ist
        # die Vergleichsansicht da.
        self._preview.set_change_tokens(
            node.added, [neu for _alt, neu in node.changed]
        )

        def load():
            return self._images.items_for([node.image_id])

        job = QueryJob(load, self._app.reporter, title="Knoten wird geladen")
        job.signals.result.connect(self._on_preview_item)
        self._app.jobs.submit(job)

    def _on_preview_item(self, _job_id: int, payload: object) -> None:
        _request_id, items = payload  # type: ignore[misc]
        if items:
            self._preview.show_item(items[0])

    def _on_double_click(self, item: QTreeWidgetItem, _column: int) -> None:
        """Spezifikation 01: alle Bilder eines Knotens im Kachelraster zeigen."""
        node: EvolutionNode = item.data(0, NODE_ROLE)
        if node is None:
            return
        label = "Knoten: " + (node.label or ("Prompt " + str(node.prompt_id)))
        self.node_activated.emit(node.prompt_id, label)

    # Schnittstelle fuer den TabManager ----------------------------------------

    def current_item(self) -> ImageItem | None:
        return self._preview.current_item

    def set_palette_colors(self, palette: Palette) -> None:
        self._palette = palette
        self._preview.set_palette_colors(palette)

    def set_tile_size(self, size: int) -> None:
        """Kein Kachelraster in diesem Tab."""


def evolution_key(cluster_id: int) -> str:
    return "evolution:" + str(cluster_id)


def _family_label(entry: dict) -> str:
    """Beschriftung eines Eintrags der Auswahlliste.

    Die Nummer allein sagt niemandem etwas; Knoten- und Bildzahl machen die
    Familien unterscheidbar und geben zugleich einen Anhalt, welche sich zu
    oeffnen lohnt.
    """
    teile = ["Familie " + str(entry["cluster_id"])]
    knoten = entry.get("node_count") or 0
    bilder = entry.get("image_count") or 0
    if knoten:
        teile.append(str(knoten) + " Prompts")
    if bilder:
        teile.append(str(bilder) + " Bilder")
    return "   -   ".join(teile) if len(teile) > 1 else teile[0]


class _HoverPreview(QFrame):
    """Das kleine Fenster, das beim Ueberfahren einer Zeile erscheint.

    Ein eigenes Fenster (`Tool` ohne Rahmen) statt eines Werkzeugtipps: ein
    QToolTip zeigt Text, und ein Bild aus dem Speicher bekommt man dort nur
    ueber eine Datei oder eingebettetes HTML hinein. Ausserdem laesst sich ein
    eigenes Fenster so setzen, dass es neben dem Zeiger steht und nicht ueber
    der Zeile, die man gerade lesen will.
    """

    #: Abstand zum Mauszeiger.
    OFFSET = 18

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setFrameShape(QFrame.Shape.Box)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

    def show_pixmap(self, pixmap: QPixmap, position) -> None:
        skaliert = pixmap.scaled(
            HOVER_SIZE,
            HOVER_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(skaliert)
        self.adjustSize()
        self.move(position.x() + self.OFFSET, position.y() + self.OFFSET)
        self.show()
