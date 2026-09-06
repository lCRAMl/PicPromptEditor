"""SelectionService - anwendungsweite Vergleichsmarkierungen.

Spezifikation 01 verlangt, dass markierte Vergleichsbilder "dauerhaft sichtbar
gekennzeichnet" werden. Sobald es mehrere Tabs gibt, heisst das: die Markierung
gehoert nicht in ein einzelnes Kachelmodell, sondern in einen gemeinsamen
Zustand. Sonst waere ein Bild im Suchergebnis markiert und dasselbe Bild im
Bibliothekstab nicht.

Es sind hoechstens zwei Markierungen zugleich moeglich, weil der Vergleich
genau zwei Bilder gegenueberstellt. Eine dritte Markierung verdraengt die
aelteste - das ist berechenbarer, als die Markierung stumm abzulehnen.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

MAX_MARKS = 2


class SelectionService(QObject):
    """marks_changed(list[int]) - die aktuellen Markierungen in Reihenfolge."""

    marks_changed = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self._marks: list[int] = []

    # Abfragen -----------------------------------------------------------------

    def marks(self) -> list[int]:
        return list(self._marks)

    def is_marked(self, image_id: int) -> bool:
        return image_id in self._marks

    def slot_of(self, image_id: int) -> int | None:
        """0 fuer A, 1 fuer B, None wenn nicht markiert."""
        try:
            return self._marks.index(image_id)
        except ValueError:
            return None

    @property
    def can_compare(self) -> bool:
        return len(self._marks) == MAX_MARKS

    # Aendern ------------------------------------------------------------------

    def toggle(self, image_id: int) -> bool:
        """Markiert oder entmarkiert. Liefert den neuen Zustand."""
        if image_id in self._marks:
            self._marks.remove(image_id)
            self._emit()
            return False

        self._marks.append(image_id)
        while len(self._marks) > MAX_MARKS:
            self._marks.pop(0)
        self._emit()
        return True

    def mark(self, image_id: int) -> None:
        if image_id not in self._marks:
            self.toggle(image_id)

    def clear(self) -> None:
        if self._marks:
            self._marks.clear()
            self._emit()

    def _emit(self) -> None:
        self.marks_changed.emit(list(self._marks))
