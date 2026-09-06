"""Die Kette in einem Aufruf.

    Text -> normalize -> sequence -> aligner -> intraline -> blocks -> Modell

`compare_texts` ist die einzige Funktion, die die Anwendung braucht. Alles
darunter ist austauschbar, ohne dass ein Aufrufer etwas merkt.

Der Vergleich wird **einmal** gerechnet und als Modell abgelegt. Er gehoert
nicht in ein Zeichenereignis: bei einem Prompt aus vierhundert Zeilen wuerde
jedes Scrollen den ganzen Diff neu anwerfen.
"""

from __future__ import annotations

from app.analysis.textdiff import blocks
from app.analysis.textdiff.aligner import align_lines
from app.analysis.textdiff.model import ComparisonResult
from app.analysis.textdiff.normalize import split_lines
from app.analysis.textdiff.options import DEFAULT_OPTIONS, CompareOptions
from app.analysis.textdiff.sequence import strategy_for


def compare_texts(
    left: str,
    right: str,
    options: CompareOptions | None = None,
) -> ComparisonResult:
    """Vergleicht zwei Texte zeilenweise und liefert das vollstaendige Modell.

    Beide Texte duerfen leer sein. Zwei leere Texte ergeben null Zeilen und
    null Unterschiede - nicht etwa eine Zeile, die mit sich selbst
    uebereinstimmt.
    """
    settings = options or DEFAULT_OPTIONS

    linke_zeilen = split_lines(left, settings)
    rechte_zeilen = split_lines(right, settings)

    zeilen = align_lines(
        linke_zeilen, rechte_zeilen, settings, strategy_for(settings.algorithm)
    )
    fertige_zeilen, unterschiede = blocks.build_blocks(zeilen)

    return ComparisonResult(
        rows=fertige_zeilen,
        differences=unterschiede,
        left_lines=linke_zeilen,
        right_lines=rechte_zeilen,
        options=settings,
    )
