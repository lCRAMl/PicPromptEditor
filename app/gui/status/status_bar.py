"""Statusleiste nach Vorbild von Visual Studio.

Spezifikation 08 verlangt ausdruecklich, Warnungen und Fehler NICHT ueber
aufdringliche Dialoge zu zeigen, solange keine Benutzeraktion noetig ist.
Stattdessen: eine dauerhafte Anzeige des Systemzustands, die auf Klick eine
Problemliste oeffnet.

Angezeigt werden Herkunft der aktuellen Ergebnisse, laufende Hintergrundarbeit,
Bibliotheksumfang, NAS-Zustand und die Zahl offener Probleme.

**Feste Breiten sind hier keine Kosmetik.** Die Tatigkeitsanzeige nennt waehrend
eines Abgleichs den gerade gelesenen Ordner, und Ordnerpfade sind
unterschiedlich lang. Ohne feste Breite schiebt jeder neue Pfad die Anzeigen
rechts daneben hin und her - die ganze Leiste zappelt, und lesen kann man
waehrenddessen gar nichts. Was nicht hineinpasst, wird in der MITTE gekuerzt:
bei einem Pfad sind Anfang und Ende aussagekraeftig, die Mitte selten.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QLabel, QProgressBar, QPushButton, QStatusBar, QWidget

from app.gui.theme import Palette
from app.services.system_service import SystemStatus


#: Feste Breite der Tatigkeitsanzeige. Breit genug fuer einen brauchbaren
#: Ausschnitt eines Pfades, schmal genug, dass daneben noch Platz bleibt.
ACTIVITY_WIDTH = 320


def elide(label: QLabel, text: str) -> None:
    """Setzt den Text gekuerzt auf die Breite des Labels.

    Der vollstaendige Text bleibt als Tooltip erreichbar - abgeschnitten heisst
    nicht verloren.
    """
    if not text:
        label.setText("")
        label.setToolTip("")
        return
    metrics = QFontMetrics(label.font())
    label.setText(
        metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, label.width() - 8)
    )
    label.setToolTip(text)


class StatusBar(QStatusBar):
    """problems_requested() - der Benutzer moechte die Problemliste sehen."""

    problems_requested = pyqtSignal()

    def __init__(self, palette: Palette, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette

        self._origin = QLabel("Bereit")
        self._origin.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.addWidget(self._origin, 1)

        # Reihenfolge: erst die Tatigkeit (mit dem Pfad), dann der Balken.
        # addPermanentWidget haengt von links nach rechts an; die Pfadanzeige
        # steht damit weiter aussen und der Balken naeher an den Zahlen, zu
        # denen er gehoert.
        self._activity = QLabel("")
        self._activity.setFixedWidth(ACTIVITY_WIDTH)
        self.addPermanentWidget(self._activity)

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(180)
        self._progress.setTextVisible(False)
        self._progress.hide()
        self.addPermanentWidget(self._progress)

        self._library = QLabel("")
        self.addPermanentWidget(self._library)

        self._nas = QLabel("")
        self.addPermanentWidget(self._nas)

        self._problems = QPushButton("Keine Probleme")
        self._problems.setFlat(True)
        self._problems.clicked.connect(self.problems_requested.emit)
        self.addPermanentWidget(self._problems)

    def set_palette_colors(self, palette: Palette) -> None:
        self._palette = palette

    # Anzeige ------------------------------------------------------------------

    def set_origin(self, text: str) -> None:
        """Herkunft der aktuellen Ergebnisse im Thumbnail Explorer."""
        self._origin.setText(text)

    def set_activity(self, active_jobs: int, text: str) -> None:
        if active_jobs <= 0:
            elide(self._activity, "")
            self._progress.hide()
            return
        label = text or "Hintergrundarbeit"
        if active_jobs > 1:
            label += "  (" + str(active_jobs) + ")"
        elide(self._activity, label)

    def set_progress(self, current: int, total: int) -> None:
        if total <= 0 or current >= total:
            self._progress.hide()
            return
        self._progress.setRange(0, total)
        self._progress.setValue(current)
        self._progress.show()

    def set_status(self, status: SystemStatus) -> None:
        self._library.setText(
            str(status.image_count) + " Bilder / " + str(status.prompt_count) + " Prompts"
        )

        unreachable = [root for root, state in status.nas.items() if state == "open"]
        if unreachable:
            self._nas.setText("NAS: " + str(len(unreachable)) + " nicht erreichbar")
            self._nas.setStyleSheet("color: " + self._palette.error.name())
        elif status.nas:
            self._nas.setText("NAS: erreichbar")
            self._nas.setStyleSheet("")
        else:
            self._nas.setText("")

        total = status.warnings + status.errors
        if total == 0:
            self._problems.setText("Keine Probleme")
            self._problems.setStyleSheet("")
            return
        parts = []
        if status.errors:
            parts.append(str(status.errors) + " Fehler")
        if status.warnings:
            parts.append(str(status.warnings) + " Warnungen")
        self._problems.setText(", ".join(parts))
        colour = self._palette.error if status.errors else self._palette.warning
        self._problems.setStyleSheet("color: " + colour.name())
