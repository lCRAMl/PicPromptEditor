"""Zeilenweiser Textvergleich nach dem Vorbild von WinMerge.

Dieses Paket beantwortet **eine** Frage: welche Zeile links gehoert neben
welche Zeile rechts. Es kennt weder Qt noch Datenbank noch Farben - es liefert
ein reines Modell aus Zustaenden, das die Oberflaeche nur noch darstellt.

Die Trennung ist keine Formsache. Der Vorgaenger hat den Unterschied an zwei
Stellen berechnet - einmal segmentweise auf Datenbankspannen, einmal zeilenweise
zur Anzeige - und die Ergebnisse hinterher ineinander umgerechnet. Jede
Aenderung an der Ausrichtung konnte die Einfaerbung verschieben. Hier entsteht
beides aus derselben Rechnung.

Aufbau:

    normalize.py   Text -> SourceLine, Vergleichsform je nach Optionen
    sequence.py    Diff-Algorithmus hinter einer Abstraktion (austauschbar)
    aligner.py     Opcodes -> AlignedRow, aehnliche Zeilen innerhalb eines Blocks
    intraline.py   Wort- und Zeichenunterschiede innerhalb einer Zeile
    blocks.py      zusammenhaengende Zeilen -> DifferenceBlock
    engine.py      compare_texts(): die Kette in einem Aufruf
    merge.py       Bloecke uebertragen - ausschliesslich auf Quellzeilen
    model.py       die Datenklassen, die alle vier Stufen verbinden

Was hier NICHT hineingehoert: QColor, QTextCharFormat, Zeilenhoehen,
Rollbalken. Ein Zustand heisst "changed", nicht "#EFCB05".
"""

from app.analysis.textdiff.engine import compare_texts
from app.analysis.textdiff.merge import MergeResult, merge_block
from app.analysis.textdiff.model import (
    AlignedRow,
    ComparisonResult,
    DiffSegment,
    DiffStatus,
    DifferenceBlock,
    SegmentStatus,
    SourceLine,
)
from app.analysis.textdiff.options import CompareOptions

__all__ = [
    "AlignedRow",
    "CompareOptions",
    "ComparisonResult",
    "DiffSegment",
    "DiffStatus",
    "DifferenceBlock",
    "MergeResult",
    "SegmentStatus",
    "SourceLine",
    "compare_texts",
    "merge_block",
]
