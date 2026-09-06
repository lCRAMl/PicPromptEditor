"""Zeichenpositionen aus der Datenbank in Dokumentpositionen umrechnen.

Die Zeichenspannen der Segmente zaehlen im **Rohtext** der Datei. Qt zaehlt im
**Dokument**: `setPlainText()` wandelt jedes Wagenruecklauf-Zeilenvorschub-Paar
in einen einzelnen Umbruch, das Wagenrueckluf-Zeichen verschwindet also.

Bei Windows-Zeilenenden verschiebt sich jede Hervorhebung dadurch mit jeder
vorangehenden Zeile um ein weiteres Zeichen nach links. Das trifft **jede**
Ansicht, die Spannen aus der Datenbank auf einen Qt-Text legt - den
Prompt-Editor ebenso wie die Vergleichsansicht. Deshalb steht die Rechnung
hier und nicht in einer der beiden.
"""

from __future__ import annotations

import bisect
from dataclasses import replace

CRLF = chr(13) + chr(10)


def dropped_positions(raw: str) -> list[int]:
    """Positionen der Zeichen, die Qt beim Anzeigen verwirft.

    Das ist genau das Wagenrueckluf-Zeichen eines CRLF-Paares. Ein
    alleinstehendes Wagenrueckluf-Zeichen wird zu einem Umbruch und bleibt
    damit ein Zeichen - es verschiebt nichts.
    """
    found: list[int] = []
    index = raw.find(CRLF)
    while index != -1:
        found.append(index)
        index = raw.find(CRLF, index + 2)
    return found


def shift(position: int, dropped: list[int]) -> int:
    """Position abzueglich der davor verworfenen Zeichen."""
    return position - bisect.bisect_left(dropped, position)


def translate(spans, raw: str):
    """Rechnet die Spannen einer Liste um.

    Erwartet Objekte mit `start` und `end` als Datenklasse. Ohne
    Wagenrueckluf-Zeichen bleibt die Liste unveraendert - der Normalfall unter
    Linux und bei sauber geschriebenen Dateien.
    """
    if not spans or not raw:
        return list(spans)
    positions = dropped_positions(raw)
    if not positions:
        return list(spans)
    return [
        replace(span, start=shift(span.start, positions), end=shift(span.end, positions))
        for span in spans
    ]
