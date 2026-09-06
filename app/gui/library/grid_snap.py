"""Einen Trenner auf ganze Kachelspalten einrasten lassen.

Das Hauptfenster laesst schon die FENSTERBREITE in Kachelschritten einrasten.
In einem Tab mit Kachelraster gibt es dieselbe Frage noch einmal, nur eine
Ebene tiefer: der Trenner zwischen Raster und Vorschau kann das Raster genauso
auf einer halben Spalte enden lassen.

Der Weg ist ein anderer als beim Fenster. Dort uebernimmt der Fenstermanager
das Einrasten ueber `setSizeIncrement`, weil er den Rand beim Ziehen selbst
bewegt. Ein QSplitter kennt nichts dergleichen; er muss nachtraeglich
korrigiert werden.

**Deshalb der Zeitgeber.** Waehrend des Ziehens zu korrigieren laesst den
Trenner unter dem Mauszeiger springen: er wird gegen die Bewegung
zurueckgesetzt, bekommt das naechste Ereignis, wird wieder zurueckgesetzt.
Korrigiert wird erst, wenn eine kurze Zeit lang nichts mehr passiert ist -
also wenn der Benutzer losgelassen hat.

Die Rechnung selbst steht als reine Funktion daneben. Sie ist damit ohne
Fenster pruefbar, und Qt kann sie nicht durch eine eigene Layoutrunde
verfaelschen.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer

#: Wartezeit nach der letzten Bewegung. Dieselbe Groessenordnung wie beim
#: Fenster; kurz genug, dass es sich wie Einrasten anfuehlt, lang genug, dass
#: es nicht in die Bewegung hineinfunkt.
SNAP_DELAY_MS = 180


def snapped_sizes(sizes: list[int], surplus: int, index: int = 0) -> list[int] | None:
    """Verschiebt den Ueberhang vom Raster zur Nachbarflaeche.

    `surplus` ist, was das Kachelraster ueber ganze Spalten hinaus belegt.
    Diese Bildpunkte wandern zum Nachbarn - das Raster wird also NIE groesser,
    nur genauer. Ein Trenner, der von selbst nach aussen wandert, waere
    uebergriffig; einer, der ein angeschnittenes Bild wegnimmt, ist Hilfe.

    Liefert None, wenn nichts zu tun ist oder das Ergebnis unsinnig waere -
    dann bleibt der Trenner unberuehrt.
    """
    if surplus <= 0 or len(sizes) < 2:
        return None
    if not 0 <= index < len(sizes):
        return None

    nachbar = index + 1 if index + 1 < len(sizes) else index - 1
    neu = list(sizes)
    if neu[index] - surplus < 1:
        return None
    neu[index] -= surplus
    neu[nachbar] += surplus
    return neu


class GridSnapper(QObject):
    """Haelt einen Trenner auf ganzen Kachelspalten.

    `view` muss `surplus_width()` beherrschen - das kann das Kachelraster.
    `enabled` wird bei jedem Einrasten frisch gefragt, damit die Einstellung
    sofort wirkt und nicht erst beim naechsten Oeffnen des Tabs.
    """

    def __init__(self, splitter, view, *, index: int = 0, enabled=None, parent=None) -> None:
        super().__init__(parent)
        self._splitter = splitter
        self._view = view
        self._index = index
        self._enabled = enabled
        self._busy = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SNAP_DELAY_MS)
        self._timer.timeout.connect(self.snap_now)
        splitter.splitterMoved.connect(lambda *_a: self._timer.start())

    def snap_now(self) -> bool:
        """Rastet sofort ein. Liefert, ob etwas veraendert wurde."""
        if self._busy:
            return False
        if self._enabled is not None and not self._enabled():
            return False

        neu = snapped_sizes(
            self._splitter.sizes(), self._view.surplus_width(), self._index
        )
        if neu is None:
            return False

        # Die Sperre ist noetig, weil setSizes() erneut splitterMoved ausloest
        # und damit den Zeitgeber neu startet - eine Schleife, die nie
        # stillstuende.
        self._busy = True
        try:
            self._splitter.setSizes(neu)
        finally:
            self._busy = False
        return True
