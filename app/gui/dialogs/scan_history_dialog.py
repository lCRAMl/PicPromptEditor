"""Abgleichprotokoll und Ueberwachungszustand.

Beantwortet die Fragen, die im Dauerbetrieb aufkommen: laeuft die Ueberwachung
ueberhaupt, wann war der letzte erfolgreiche Abgleich, und wird er langsamer.

Bewusst ein Dialog auf Anforderung und keine Dauereinblendung - Spezifikation 08
verlangt eine unaufdringliche Statusanzeige, keine Instrumententafel.
"""

from __future__ import annotations

import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ScanHistoryDialog(QDialog):
    def __init__(self, index_service, watch_service, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = index_service
        self._watch = watch_service
        self.setWindowTitle("Abgleichprotokoll")
        self.resize(860, 520)

        layout = QVBoxLayout(self)
        self._summary = QLabel()
        self._summary.setContentsMargins(4, 4, 4, 4)
        self._summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._summary)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(7)
        self._tree.setHeaderLabels(
            ["Zeit", "Art", "Dauer", "Neu", "Geaendert", "Entfernt", "Status"]
        )
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._tree, 1)

        buttons = QDialogButtonBox()
        refresh = buttons.addButton("Aktualisieren", QDialogButtonBox.ButtonRole.ActionRole)
        probe = buttons.addButton("NAS jetzt pruefen", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        refresh.clicked.connect(self.reload)
        probe.clicked.connect(self._probe)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.reload()

    def reload(self) -> None:
        self._tree.clear()
        records = self._index.scan_history(200)
        for record in records:
            item = QTreeWidgetItem(
                [
                    _format_time(record.scan_time),
                    "vollstaendig" if record.scan_type == "full" else "inkrementell",
                    format(record.duration, ".2f") + " s",
                    str(record.new_files),
                    str(record.changed_files),
                    str(record.deleted_files),
                    _status_label(record.status, record.errors),
                ]
            )
            if record.note:
                item.setToolTip(0, record.note)
            self._tree.addTopLevelItem(item)
        for column in range(1, 7):
            self._tree.resizeColumnToContents(column)

        self._summary.setText(self._describe(records))

    def _describe(self, records) -> str:
        status = self._watch.status() if self._watch is not None else {}
        lines = []

        if status:
            watcher = "aktiv" if status.get("watcher_enabled") else "aus"
            lines.append(
                "Datei-Watcher: " + watcher
                + " auf " + str(status.get("watched", 0)) + " Ordnern"
                + "   |   naechster turnusmaessiger Abgleich in "
                + str(status.get("next_periodic_s", 0)) + " s"
            )
            nas = status.get("nas") or {}
            if nas:
                unreachable = [root for root, value in nas.items() if value == "open"]
                if unreachable:
                    lines.append("NAS: nicht erreichbar: " + ", ".join(unreachable))
                else:
                    lines.append(
                        "NAS: erreichbar (" + str(len(nas)) + " Wurzel(n))"
                    )

        last = self._index.last_successful_scan()
        if last is not None:
            lines.append(
                "Letzter erfolgreicher Abgleich: " + _format_time(last.scan_time)
                + "   |   " + format(last.duration, ".2f") + " s"
                + "   |   " + (last.note or "keine Aenderungen")
            )
        else:
            lines.append("Noch kein erfolgreicher Abgleich protokolliert")

        lines.append(str(len(records)) + " Eintraege im Protokoll")
        return "\n".join(lines)

    def _probe(self) -> None:
        if self._watch is not None:
            self._watch.probe_now()


def _format_time(value: float) -> str:
    if not value:
        return ""
    return datetime.datetime.fromtimestamp(value).strftime("%d.%m.%Y %H:%M:%S")


def _status_label(status: str, errors: int) -> str:
    if status == "ok":
        return "erfolgreich"
    if status == "cancelled":
        return "abgebrochen"
    return "Fehler" + ((" (" + str(errors) + ")") if errors else "")
