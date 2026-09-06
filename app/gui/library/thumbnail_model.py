"""Modell des Thumbnail Explorers.

Das Modell haelt die vollstaendige Ergebnismenge als schmale ImageItem-Objekte.
Bei hunderttausend Elementen sind das im zweistelligen Megabyte-Bereich - deutlich
guenstiger als eine seitenweise Nachladelogik samt ihrer Fehlerquellen.

Die Bilddaten selbst liegen NICHT im Modell. data() fragt den ImageService nach
einem bereits vorhandenen Vorschaubild; fehlt es, liefert das Modell einen
Platzhalter und meldet den Bedarf an. Das Nachreichen erfolgt gesammelt ueber
set_thumbnails().

Wichtig fuer die Bildlaufleistung: dataChanged wird nie je Bild ausgeloest,
sondern gebuendelt fuer zusammenhaengende Bereiche. Zweihundert Einzelsignale
bedeuten zweihundert Neuzeichnungen des sichtbaren Bereichs.
"""

from __future__ import annotations

from PyQt6.QtCore import QAbstractListModel, QModelIndex, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap

from app.dto.models import ImageItem, ResultSet
from app.services.selection_service import SelectionService

ITEM_ROLE = int(Qt.ItemDataRole.UserRole) + 1
PIXMAP_ROLE = int(Qt.ItemDataRole.UserRole) + 2
MARKED_ROLE = int(Qt.ItemDataRole.UserRole) + 3
HAS_PROMPT_ROLE = int(Qt.ItemDataRole.UserRole) + 4
SLOT_ROLE = int(Qt.ItemDataRole.UserRole) + 5


class ThumbnailModel(QAbstractListModel):
    """thumbnails_needed(list[ImageItem]) - vom View beim Scrollen ausgeloest."""

    thumbnails_needed = pyqtSignal(object)

    def __init__(self, selection: SelectionService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[ImageItem] = []
        self._delivered: list[ImageItem] = []
        self._pixmaps: dict[int, QPixmap] = {}
        self._row_by_id: dict[int, int] = {}
        self._selection = selection
        self._origin = ""
        # Jedes Modell hoert auf den gemeinsamen Markierungszustand. Dadurch
        # zeigt dieselbe Datei in Bibliothek und Suchergebnis dieselbe Markierung.
        self._previous_marks: set[int] = set(selection.marks())
        selection.marks_changed.connect(self._on_marks_changed)

    # Qt-Schnittstelle ---------------------------------------------------------

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]

        if role == int(Qt.ItemDataRole.DisplayRole):
            return item.filename
        if role == ITEM_ROLE:
            return item
        if role == PIXMAP_ROLE:
            return self._pixmaps.get(item.id)
        if role == MARKED_ROLE:
            return self._selection.is_marked(item.id)
        if role == SLOT_ROLE:
            return self._selection.slot_of(item.id)
        if role == HAS_PROMPT_ROLE:
            return item.has_prompt
        if role == int(Qt.ItemDataRole.ToolTipRole):
            lines = [
                item.filename,
                item.full_path,
                str(round(item.file_size / 1024)) + " KB",
            ]
            if not item.has_prompt:
                lines.append("kein Prompt vorhanden")
            return "\n".join(lines)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    # Inhalt -------------------------------------------------------------------

    @property
    def origin(self) -> str:
        return self._origin

    def items(self) -> list[ImageItem]:
        """Kopie der Ergebnismenge, etwa zum Uebernehmen in einen eigenen Tab."""
        return list(self._items)

    def item_at(self, row: int) -> ImageItem | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def items_in_range(self, first: int, last: int) -> list[ImageItem]:
        first = max(0, first)
        last = min(len(self._items) - 1, last)
        return self._items[first : last + 1] if first <= last else []

    #: Sortierschluessel: Anzeigename -> (Feld, absteigend).
    #: Die Reihenfolge ist die des Auswahlfeldes.
    #:
    #: Der erste Eintrag ist die VORGABE und sortiert NICHT um. Das ist
    #: wesentlich: eine Aehnlichkeitssuche liefert nach Relevanz, die Ansicht
    #: "neueste Paare" nach Zeitstempel. Wuerde die Ansicht ungefragt nach
    #: Namen sortieren, waere genau die Ordnung zerstoert, derentwegen die
    #: Abfrage gestellt wurde.
    SORT_ORDERS: tuple[tuple[str, str, bool], ...] = (
        ("Wie geliefert", "", False),
        ("Name (A-Z)", "filename", False),
        ("Name (Z-A)", "filename", True),
        ("Neueste zuerst", "modified_time", True),
        ("Aelteste zuerst", "modified_time", False),
        ("Groesste zuerst", "file_size", True),
        ("Kleinste zuerst", "file_size", False),
        ("Mit Prompt zuerst", "prompt_id", True),
    )

    def sort_items(self, index: int) -> None:
        """Sortiert die vorhandenen Ergebnisse um.

        Sortiert wird im Modell, nicht in der Datenbank: die Ergebnismenge
        liegt bereits vollstaendig vor, und ein neuer Datenbankdurchlauf
        wuerde die Vorschaubilder verwerfen, die gerade erst erzeugt wurden.
        """
        if not 0 <= index < len(self.SORT_ORDERS):
            return
        _label, field, descending = self.SORT_ORDERS[index]

        if not field:
            # Zurueck zur Reihenfolge der Abfrage.
            self.beginResetModel()
            self._items = list(self._delivered)
            self._row_by_id = {item.id: row for row, item in enumerate(self._items)}
            self.endResetModel()
            return

        def key(item):
            value = getattr(item, field, None)
            if field == "prompt_id":
                # Nicht nach der Nummer sortieren, sondern nach "vorhanden":
                # die Nummer selbst sagt nichts ueber das Bild aus.
                return (1 if value else 0, item.filename.lower())
            if field == "filename":
                return (str(value).lower(),)
            return (value or 0, item.filename.lower())

        self.beginResetModel()
        self._items.sort(key=key, reverse=descending)
        self._row_by_id = {item.id: row for row, item in enumerate(self._items)}
        self.endResetModel()

    def set_results(self, results: ResultSet) -> None:
        self.beginResetModel()
        self._items = list(results.items)
        #: Die Reihenfolge, in der die Abfrage geliefert hat - damit
        #: "Wie geliefert" auch nach einem Umsortieren zurueckfindet.
        self._delivered = list(results.items)
        self._origin = results.origin
        self._pixmaps.clear()
        self._row_by_id = {item.id: row for row, item in enumerate(self._items)}
        self.endResetModel()

    def clear(self) -> None:
        self.beginResetModel()
        self._items = []
        self._pixmaps.clear()
        self._row_by_id.clear()
        self._origin = ""
        self.endResetModel()

    # Vorschaubilder -----------------------------------------------------------

    def set_thumbnails(self, batch: list[tuple[int, bytes]]) -> None:
        """Nimmt einen Stapel fertiger Vorschaubilder auf.

        Die betroffenen Zeilen werden zu zusammenhaengenden Bereichen
        zusammengefasst, damit aus einem Stapel wenige dataChanged-Signale
        werden statt eines je Bild.
        """
        rows: list[int] = []
        for image_id, data in batch:
            row = self._row_by_id.get(image_id)
            if row is None:
                continue  # Ergebnis einer inzwischen abgeloesten Ansicht
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                continue
            self._pixmaps[image_id] = pixmap
            rows.append(row)

        if not rows:
            return
        rows.sort()
        start = previous = rows[0]
        for row in rows[1:]:
            if row == previous + 1:
                previous = row
                continue
            self._emit_range(start, previous)
            start = previous = row
        self._emit_range(start, previous)

    def _emit_range(self, first: int, last: int) -> None:
        self.dataChanged.emit(self.index(first), self.index(last), [PIXMAP_ROLE])

    def request_range(self, first: int, last: int) -> None:
        """Meldet Bedarf fuer einen sichtbaren Bereich samt Vorladebereich."""
        needed = [
            item
            for item in self.items_in_range(first, last)
            if item.id not in self._pixmaps
        ]
        if needed:
            self.thumbnails_needed.emit(needed)

    # Vergleichsmarkierung -----------------------------------------------------

    def marked_ids(self) -> list[int]:
        return self._selection.marks()

    def is_marked(self, image_id: int) -> bool:
        return self._selection.is_marked(image_id)

    def toggle_marked(self, image_id: int) -> bool:
        """Delegiert an den gemeinsamen Zustand; die Anzeige folgt ueber das Signal."""
        return self._selection.toggle(image_id)

    def _on_marks_changed(self, marks: list[int]) -> None:
        """Zeichnet die betroffenen Zeilen neu.

        Es wird nicht das ganze Modell invalidiert: bei hunderttausend Kacheln
        waere das fuer eine einzelne Markierung eine sichtbare Verzoegerung.
        Neu gezeichnet werden nur die Zeilen, deren Zustand sich geaendert haben
        kann - die aktuellen und die zuvor markierten.
        """
        affected = set(marks) | self._previous_marks
        self._previous_marks = set(marks)
        for image_id in affected:
            row = self._row_by_id.get(image_id)
            if row is not None:
                self.dataChanged.emit(
                    self.index(row), self.index(row), [MARKED_ROLE, SLOT_ROLE]
                )
