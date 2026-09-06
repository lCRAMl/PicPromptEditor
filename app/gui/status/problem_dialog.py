"""Problemliste - die nicht-aufdringliche Alternative zum Fehlerdialog.

Zeigt die aggregierten Eintraege aus error_log. Die Spalte "Anzahl" ist der
Grund fuer die Aggregation: ein NAS-Ausfall waehrend eines Scans erscheint als
eine Zeile mit hoher Zahl statt als zehntausend Zeilen.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.errors import Severity
from app.dto.models import ErrorEntry
from app.services.system_service import SystemService


class ProblemDialog(QDialog):
    def __init__(self, service: SystemService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("Probleme")
        self.resize(900, 500)

        layout = QVBoxLayout(self)
        self._summary = QLabel()
        layout.addWidget(self._summary)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._tree = QTreeWidget()
        self._tree.setColumnCount(5)
        self._tree.setHeaderLabels(["Stufe", "Anzahl", "Quelle", "Meldung", "Zuletzt"])
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        header = self._tree.header()
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._tree.currentItemChanged.connect(self._on_selection)
        splitter.addWidget(self._tree)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setPlaceholderText("Kein Eintrag ausgewaehlt")
        splitter.addWidget(self._details)
        splitter.setSizes([320, 160])
        layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox()
        refresh = buttons.addButton("Aktualisieren", QDialogButtonBox.ButtonRole.ActionRole)
        resolve = buttons.addButton("Als erledigt markieren", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        refresh.clicked.connect(self.reload)
        resolve.clicked.connect(self._resolve_current)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.reload()

    def reload(self) -> None:
        self._tree.clear()
        entries = self._service.problems()
        for entry in entries:
            item = QTreeWidgetItem(
                [
                    entry.severity_label,
                    str(entry.count),
                    entry.source,
                    entry.message,
                    _format_time(entry.last_seen),
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            self._tree.addTopLevelItem(item)
        for column in range(self._tree.columnCount()):
            if column != 3:
                self._tree.resizeColumnToContents(column)

        self._update_summary()

    def _entries(self) -> list[ErrorEntry]:
        """Was gerade in der Liste steht - nicht, was in der Datenbank steht."""
        items = [self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount())]
        return [i.data(0, Qt.ItemDataRole.UserRole) for i in items if i is not None]

    def _update_summary(self) -> None:
        entries = self._entries()
        errors = sum(1 for e in entries if e.severity >= Severity.ERROR)
        warnings = len(entries) - errors
        self._summary.setText(
            str(len(entries)) + " offene Eintraege  -  "
            + str(errors) + " Fehler, " + str(warnings) + " Warnungen"
        )

    def _on_selection(self, current: QTreeWidgetItem | None, _previous) -> None:
        if current is None:
            self._details.clear()
            return
        entry: ErrorEntry = current.data(0, Qt.ItemDataRole.UserRole)
        lines = [
            entry.severity_label + " aus " + entry.source,
            "Erstmals: " + _format_time(entry.timestamp),
            "Zuletzt : " + _format_time(entry.last_seen),
            "Anzahl  : " + str(entry.count),
            "",
            entry.message,
        ]
        if entry.details:
            lines.extend(["", entry.details])
        self._details.setPlainText("\n".join(lines))

    def _resolve_current(self) -> None:
        """Hakt den ausgewaehlten Eintrag ab, ohne das Fenster einzufrieren.

        **Warum die Zeile sofort verschwindet, statt die Liste neu zu lesen.**
        Beides zusammen geht nicht: die Liste liest ueber eine Leseverbindung,
        der Eintrag verschwindet dort erst NACH dem Commit, und auf den Commit
        zu warten heisst, den GUI-Faden anzuhalten. Genau das war der
        gemeldete Fehler - das Programm haengte sich beim Druck auf diesen
        Knopf auf, weil das Warten erst aufgibt, wenn der Schreibfaden
        stillsteht, und ein beschaeftigter Schreibfaden steht nicht still.

        Also wird die Aufgabe eingereiht und die Zeile hier entfernt. Die
        Reihenfolge im Schreibfaden ist strikt FIFO und `Application.shutdown`
        leert die Warteschlange - die Aenderung geht nicht verloren. Wer den
        wirklichen Stand sehen will, drueckt "Aktualisieren".
        """
        item = self._tree.currentItem()
        if item is None:
            return
        entry: ErrorEntry = item.data(0, Qt.ItemDataRole.UserRole)
        self._service.resolve_problem(entry.id, wait=False)
        index = self._tree.indexOfTopLevelItem(item)
        if index >= 0:
            self._tree.takeTopLevelItem(index)
        self._details.clear()
        self._update_summary()


def _format_time(value: float) -> str:
    import datetime

    if not value:
        return ""
    return datetime.datetime.fromtimestamp(value).strftime("%d.%m.%Y %H:%M:%S")
