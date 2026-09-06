"""Suchleiste mit Entprellung und Bereichsauswahl.

Zwei Dinge, die eine Volltextsuche erst benutzbar machen:

* Entprellung. Ohne sie setzt jeder Tastendruck eine FTS5-Abfrage ab. Bei
  hunderttausend Prompts sind das waehrend eines getippten Wortes acht Abfragen,
  von denen sieben verworfen werden. Die Verzoegerung ist ueber
  search.debounce_ms konfigurierbar.
* Bereich. "Ganze Bibliothek" ist der Normalfall, "Aktueller Ordner" beantwortet
  die haeufige Frage "wo in diesem Shooting kam das vor". Die Einschraenkung
  passiert in der Abfrage, nicht nachtraeglich.

Die Leiste kennt weder Datenbank noch Service; sie meldet nur, was gesucht
werden soll.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

SCOPE_LIBRARY = "library"
SCOPE_FOLDER = "folder"


class SearchBar(QWidget):
    """search_requested(text, scope), cleared()"""

    search_requested = pyqtSignal(str, str)
    cleared = pyqtSignal()

    def __init__(
        self,
        debounce_ms: int = 250,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Volltextsuche ueber alle Prompts")
        self._input.setClearButtonEnabled(True)

        self._scope = QComboBox()
        self._scope.addItem("Ganze Bibliothek", SCOPE_LIBRARY)
        self._scope.addItem("Aktueller Ordner", SCOPE_FOLDER)

        self._button = QPushButton("Suchen")
        self._button.setDefault(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(QLabel("Suche:"))
        layout.addWidget(self._input, 1)
        layout.addWidget(self._scope)
        layout.addWidget(self._button)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, debounce_ms))
        self._timer.timeout.connect(self._emit_search)

        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._emit_now)
        self._button.clicked.connect(self._emit_now)
        self._scope.currentIndexChanged.connect(self._on_scope_changed)

    # Zustand ------------------------------------------------------------------

    @property
    def text(self) -> str:
        return self._input.text().strip()

    @property
    def scope(self) -> str:
        return str(self._scope.currentData())

    def set_debounce(self, milliseconds: int) -> None:
        self._timer.setInterval(max(0, milliseconds))

    def set_folder_scope_enabled(self, enabled: bool) -> None:
        """Ohne ausgewaehlten Ordner ergibt der Ordnerbereich keinen Sinn."""
        model = self._scope.model()
        item = model.item(1) if model is not None else None
        if item is not None:
            item.setEnabled(enabled)
        if not enabled and self.scope == SCOPE_FOLDER:
            self._scope.setCurrentIndex(0)

    def clear(self) -> None:
        self._input.clear()

    def focus(self) -> None:
        self._input.setFocus()
        self._input.selectAll()

    # Ereignisse ---------------------------------------------------------------

    def _on_text_changed(self, text: str) -> None:
        if not text.strip():
            self._timer.stop()
            self.cleared.emit()
            return
        self._timer.start()

    def _on_scope_changed(self, _index: int) -> None:
        if self.text:
            self._emit_now()

    def _emit_now(self) -> None:
        self._timer.stop()
        self._emit_search()

    def _emit_search(self) -> None:
        text = self.text
        if text:
            self.search_requested.emit(text, self.scope)
