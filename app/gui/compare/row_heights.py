"""Gleiche Zeilenhoehen auf beiden Seiten - die reine Rechnung.

Das Problem, das diese Datei loest, ist der eigentliche Grund fuer den Umbau
der Vergleichsansicht.

Die Ausrichtung sorgt dafuer, dass beide Seiten gleich viele **logische** Zeilen
haben. Bei eingeschaltetem Zeilenumbruch heisst das noch lange nicht, dass sie
gleich hoch sind: eine Prosazeile aus 400 Zeichen belegt links vier
Anzeigezeilen und rechts sechs, weil rechts ein paar Woerter mehr stehen. Ab
dieser Zeile laeuft die eine Seite der anderen davon - und zwar immer weiter,
Zeile um Zeile.

Der frueher benutzte anteilige Gleichlauf (Wert durch Maximum, auf die andere
Reichweite umgerechnet) verdeckt das nur an den Raendern: bei 0 % und bei 100 %
stimmt er exakt, dazwischen driftet er. Genau so hat sich der Fehler gezeigt -
oben buendig, nach etwas Scrollen immer weiter auseinander.

Die Loesung ist nicht, den Gleichlauf zu verbessern, sondern ihn ueberfluessig
zu machen: bekommt jede Anzeigezeile auf beiden Seiten **dieselbe Hoehe in
Pixeln**, sind beide Dokumente exakt gleich hoch, und der Gleichlauf ist eine
schlichte Wertuebernahme ohne jede Umrechnung.

Erreicht wird das ueber einen Abstand unter der jeweils kuerzeren Zeile
(`QTextBlockFormat.setBottomMargin`). Der Abstand ist kein Text - er erscheint
in keiner Zwischenablage, in keiner Datei und in keiner Zeilenzaehlung.

Hier steht nur die Rechnung, ohne Qt. Qt verwaltet Rollbalken-Reichweiten
selbst und ueberschreibt gesetzte Werte; was sich als reine Funktion pruefen
laesst, gehoert deshalb aus dem Widget heraus.
"""

from __future__ import annotations

#: Unterschiede unterhalb dieser Pixelzahl bleiben unbehandelt. Fliesskommamasse
#: aus dem Textlayout weichen um Bruchteile voneinander ab; ein Abstand von
#: 0,3 px waere reine Unruhe im Layout, ohne sichtbaren Gewinn.
EPSILON = 0.5


def equalizing_margins(
    left_heights: list[float], right_heights: list[float]
) -> tuple[list[float], list[float]]:
    """Abstaende, die beide Seiten Zeile fuer Zeile gleich hoch machen.

    Erwartet die Hoehen **ohne** bereits gesetzte Abstaende - sonst summierte
    sich bei jedem Durchlauf ein weiterer dazu, und die Zeilen wuechsen bei
    jedem Fensterwechsel ein Stueck.

    Geliefert werden zwei gleich lange Listen. Die kuerzere Zeile bekommt die
    Differenz, die laengere eine Null.

    Ungleich lange Eingaben sind ein Fehler des Aufrufers - nach der Ausrichtung
    hat jede Seite genau so viele Zeilen wie die andere. Statt zu werfen, wird
    auf die kuerzere Laenge gearbeitet: ein Zeichenereignis ist der falsche Ort
    fuer eine Ausnahme, und ein um eine Zeile unvollstaendiger Ausgleich ist
    immer noch besser als eine leere Ansicht.
    """
    anzahl = min(len(left_heights), len(right_heights))
    links: list[float] = [0.0] * len(left_heights)
    rechts: list[float] = [0.0] * len(right_heights)

    for index in range(anzahl):
        unterschied = left_heights[index] - right_heights[index]
        if unterschied > EPSILON:
            rechts[index] = unterschied
        elif unterschied < -EPSILON:
            links[index] = -unterschied

    return links, rechts


def is_aligned(
    left_heights: list[float], right_heights: list[float], tolerance: float = EPSILON
) -> bool:
    """Ob beide Seiten bereits Zeile fuer Zeile gleich hoch sind.

    Die Pruefung, die ein Test stellen kann, ohne Pixel nachzumessen: nach dem
    Anwenden der Abstaende muss sie wahr sein. Sie prueft **jede** Zeile, nicht
    nur die Gesamthoehe - zwei Dokumente koennen insgesamt gleich hoch sein und
    trotzdem in der Mitte auseinanderlaufen.
    """
    if len(left_heights) != len(right_heights):
        return False
    return all(
        abs(links - rechts) <= tolerance
        for links, rechts in zip(left_heights, right_heights)
    )


def cumulative_offsets(heights: list[float]) -> list[float]:
    """Der obere Rand jeder Zeile, vom Dokumentanfang an gerechnet.

    Damit laesst sich eine Zeile anspringen, ohne Qt nach ihrer Position zu
    fragen - und in einem Test nachweisen, dass Zeile n auf beiden Seiten auf
    derselben Hoehe beginnt.
    """
    rand: list[float] = []
    laufend = 0.0
    for hoehe in heights:
        rand.append(laufend)
        laufend += hoehe
    return rand
