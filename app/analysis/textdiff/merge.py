"""Bloecke von einer Seite auf die andere uebertragen.

Die eine Regel, um die es hier geht:

    **Uebertragen wird das Quellmodell, niemals die Anzeige.**

Der gerenderte Text enthaelt Platzhalter. Wuerde er mitkopiert, entstuenden auf
der Zielseite Leerzeilen, die niemand geschrieben hat - und beim naechsten
Vergleich waeren sie echte Zeilen, die als Unterschied auftauchen. Aus einer
Uebernahme wuerde so bei jedem Durchgang neuer Muell.

Statt dessen wird der Bereich `left_slice` bzw. `right_slice` benutzt. Er zeigt
in die Liste der **realen** Zeilen und ist auch dann sinnvoll, wenn eine Seite
im Block nichts beitraegt: (7, 7) heisst "an Position 7 einfuegen, nichts
entfernen". Drei Faelle, ein Mechanismus:

    Aenderung   (3, 5) wird durch (3, 5) der anderen Seite ersetzt
    Einfuegung  (7, 7) wird durch (7, 9) ersetzt  -> zwei Zeilen kommen dazu
    Entfernung  (7, 9) wird durch (7, 7) ersetzt  -> zwei Zeilen fallen weg

Die Funktionen hier aendern nichts. Sie liefern den neuen Text; ob und wie er
gespeichert wird, entscheidet eine Schicht darueber.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis.textdiff.model import NEWLINE, ComparisonResult, DifferenceBlock

#: Richtungen. Als Zeichenketten statt als Aufzaehlung, weil sie unveraendert
#: aus einer Qt-Aktion (`action.data()`) hereinkommen.
TO_RIGHT = "left_to_right"
TO_LEFT = "right_to_left"


@dataclass(frozen=True, slots=True)
class MergeResult:
    """Das Ergebnis einer Uebernahme - beide Seiten als vollstaendiger Text.

    Immer beide, auch wenn sich nur eine geaendert hat. Der Aufrufer vergleicht
    anschliessend neu und braucht dafuer ohnehin beide Seiten; ihn raten zu
    lassen, welche gerade aktuell ist, waere eine Fehlerquelle ohne Gewinn.
    """

    left_text: str
    right_text: str
    #: Welche Seite sich tatsaechlich geaendert hat: "left" oder "right".
    changed_side: str
    #: Wie viele reale Zeilen ersetzt und wie viele eingesetzt wurden.
    lines_removed: int
    lines_inserted: int

    @property
    def is_noop(self) -> bool:
        return self.lines_removed == 0 and self.lines_inserted == 0


def merge_block(
    result: ComparisonResult, block: DifferenceBlock, direction: str
) -> MergeResult:
    """Uebertraegt einen Difference Block auf die andere Seite.

    Der Aufrufer bekommt neuen Text zurueck und rechnet den Vergleich damit
    neu. Das ist billiger, als es klingt, und schliesst eine ganze Klasse von
    Fehlern aus: es gibt keinen Zustand, der zwischen zwei Uebernahmen
    veralten koennte.
    """
    linke = [zeile.text for zeile in result.left_lines]
    rechte = [zeile.text for zeile in result.right_lines]

    if direction == TO_RIGHT:
        quelle, ziel = linke, rechte
        quell_bereich, ziel_bereich = block.left_slice, block.right_slice
        seite = "right"
    elif direction == TO_LEFT:
        quelle, ziel = rechte, linke
        quell_bereich, ziel_bereich = block.right_slice, block.left_slice
        seite = "left"
    else:
        raise ValueError("Unbekannte Richtung: " + str(direction))

    uebernommen = quelle[quell_bereich[0] : quell_bereich[1]]
    ersetzt = ziel_bereich[1] - ziel_bereich[0]
    neu = ziel[: ziel_bereich[0]] + uebernommen + ziel[ziel_bereich[1] :]

    if direction == TO_RIGHT:
        rechte = neu
    else:
        linke = neu

    return MergeResult(
        left_text=NEWLINE.join(linke),
        right_text=NEWLINE.join(rechte),
        changed_side=seite,
        lines_removed=ersetzt,
        lines_inserted=len(uebernommen),
    )


def merge_all(result: ComparisonResult, direction: str) -> MergeResult:
    """Uebertraegt saemtliche Bloecke auf einmal.

    Von hinten nach vorn, damit die Bereiche der noch nicht bearbeiteten
    Bloecke gueltig bleiben: jede Uebernahme verschiebt alles, was dahinter
    liegt. Von vorn gearbeitet zeigte der zweite Bereich bereits ins Leere.

    Das Ergebnis ist zwangslaeufig, dass die Zielseite der Quellseite gleicht -
    aber nur in den Bloecken, nicht durch stumpfes Ueberschreiben. Ignorierte
    Unterschiede bleiben, wie sie sind.
    """
    linke = [zeile.text for zeile in result.left_lines]
    rechte = [zeile.text for zeile in result.right_lines]
    entfernt = 0
    eingefuegt = 0

    for block in reversed(result.real_differences):
        if direction == TO_RIGHT:
            uebernommen = linke[block.left_slice[0] : block.left_slice[1]]
            entfernt += block.right_slice[1] - block.right_slice[0]
            eingefuegt += len(uebernommen)
            rechte = (
                rechte[: block.right_slice[0]]
                + uebernommen
                + rechte[block.right_slice[1] :]
            )
        elif direction == TO_LEFT:
            uebernommen = rechte[block.right_slice[0] : block.right_slice[1]]
            entfernt += block.left_slice[1] - block.left_slice[0]
            eingefuegt += len(uebernommen)
            linke = (
                linke[: block.left_slice[0]]
                + uebernommen
                + linke[block.left_slice[1] :]
            )
        else:
            raise ValueError("Unbekannte Richtung: " + str(direction))

    return MergeResult(
        left_text=NEWLINE.join(linke),
        right_text=NEWLINE.join(rechte),
        changed_side="right" if direction == TO_RIGHT else "left",
        lines_removed=entfernt,
        lines_inserted=eingefuegt,
    )
