"""Ordnerbaum.

Der Baum wird ausschliesslich aus der Tabelle folders aufgebaut. Er fasst kein
Verzeichnis an - Spezifikation 02 verbietet der Oberflaeche jeden Dateizugriff,
und auf einem nicht erreichbaren NAS wuerde jedes Aufklappen blockieren.

Die Ordnerliste kommt flach und nach Tiefe sortiert aus dem Service. Weil Eltern
dadurch immer vor ihren Kindern stehen, genuegt ein einziger Durchlauf zum
Aufbau der Hierarchie.
"""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QMenu, QTreeView, QWidget

from app.dto.models import FolderNode

FOLDER_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class FolderTree(QTreeView):
    """folder_selected(FolderNode, mit_unterordnern), context_action(str, FolderNode)"""

    folder_selected = pyqtSignal(object, bool)
    context_action = pyqtSignal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = QStandardItemModel(self)
        self._model.setHorizontalHeaderLabels(["Ordner"])
        self.setModel(self._model)

        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setExpandsOnDoubleClick(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.clicked.connect(self._on_clicked)

        self._show_counts = True
        self._items: dict[int, QStandardItem] = {}

    # Aufbau -------------------------------------------------------------------

    def set_show_counts(self, enabled: bool) -> None:
        self._show_counts = enabled

    def set_folders(self, folders: list[FolderNode]) -> None:
        """Baut den Baum neu auf und stellt die Aufklappzustaende wieder her."""
        expanded = self._expanded_ids()
        selected = self.current_folder()

        self._model.removeRows(0, self._model.rowCount())
        self._items.clear()

        for node in folders:  # nach Tiefe sortiert: Eltern kommen zuerst
            item = QStandardItem(self._label(node))
            item.setEditable(False)
            item.setData(node, FOLDER_ROLE)
            item.setToolTip(self._tooltip(node))
            self._items[node.id] = item

            parent_item = self._items.get(node.parent_id) if node.parent_id else None
            if parent_item is not None:
                parent_item.appendRow(item)
            else:
                self._model.appendRow(item)

        self._restore_expanded(expanded)
        if selected is not None:
            self.select_folder(selected.id)
        elif self._model.rowCount():
            self.expand(self._model.index(0, 0))

    def _label(self, node: FolderNode) -> str:
        """Name und in Klammern die Bilder im Unterordner "ai".

        In dieser Bibliothek liegen die Bild-Prompt-Paare fast ausnahmslos in
        einem Unterordner "ai" (99,6 Prozent aller Prompts); die Zahl des
        Elternordners zaehlt dagegen Beiwerk. Hat ein Ordner keinen solchen
        Unterordner, gilt seine eigene Zahl - sonst stuende ausgerechnet bei
        den ai-Ordnern selbst nichts.
        """
        if not self._show_counts:
            return node.name
        anzahl = node.ai_image_count or node.image_count
        if not anzahl:
            return node.name
        return node.name + "  (" + str(anzahl) + ")"

    @staticmethod
    def _tooltip(node: FolderNode) -> str:
        lines = [node.path]
        if node.ai_image_count or node.ai_prompt_count:
            lines.append(
                "ai: " + str(node.ai_image_count) + " Bilder, "
                + str(node.ai_prompt_count) + " Prompts"
            )
        lines.append(
            "in diesem Ordner: " + str(node.image_count) + " Bilder, "
            + str(node.prompt_count) + " Prompts"
        )
        return "\n".join(lines)

    # Zustand ------------------------------------------------------------------

    def current_folder(self) -> FolderNode | None:
        index = self.currentIndex()
        return index.data(FOLDER_ROLE) if index.isValid() else None

    def select_folder(self, folder_id: int) -> bool:
        item = self._items.get(folder_id)
        if item is None:
            return False
        index = item.index()
        self.setCurrentIndex(index)
        self.scrollTo(index)
        return True

    def _expanded_ids(self) -> set[int]:
        found: set[int] = set()
        for folder_id, item in self._items.items():
            if self.isExpanded(item.index()):
                found.add(folder_id)
        return found

    def _restore_expanded(self, folder_ids: set[int]) -> None:
        for folder_id in folder_ids:
            item = self._items.get(folder_id)
            if item is not None:
                self.setExpanded(item.index(), True)

    # Ereignisse ---------------------------------------------------------------

    def _on_clicked(self, index: QModelIndex) -> None:
        node = index.data(FOLDER_ROLE)
        if node is not None:
            self.folder_selected.emit(node, False)

    def _show_menu(self, position: QPoint) -> None:
        index = self.indexAt(position)
        node = index.data(FOLDER_ROLE) if index.isValid() else None
        if node is None:
            return

        # Kein "Ordner analysieren" mehr. Der Eintrag stand hier seit M1
        # dauerhaft ausgegraut mit dem Vermerk "ab M4" - M4 kam, und die
        # Analyse laeuft ueber die ganze Bibliothek statt ueber einen Ordner,
        # weil eine Promptfamilie quer durch die Ordner geht. Ein Menuepunkt,
        # der nie anklickbar wird, ist ein Versprechen, das niemand einloest.
        menu = QMenu(self)
        entries = [
            ("open", "Inhalt anzeigen"),
            ("open_tab", "In neuem Tab oeffnen"),
            (None, None),
            ("rescan", "Diesen Ordner neu einlesen"),
            ("reveal", "Im Explorer oeffnen"),
        ]
        for action_id, label in entries:
            if action_id is None:
                menu.addSeparator()
                continue
            action = menu.addAction(label)
            action.setData(action_id)

        chosen = menu.exec(self.viewport().mapToGlobal(position))
        if chosen is None:
            return
        action_id = str(chosen.data())
        if action_id == "open":
            self.folder_selected.emit(node, False)
        else:
            self.context_action.emit(action_id, node)
