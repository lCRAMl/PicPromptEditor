"""Der Sequenzabgleich - hinter einer Abstraktion, damit er austauschbar ist.

Der Abgleich bekommt zwei Folgen vergleichbarer Werte und liefert Operationen:
EQUAL, INSERT, DELETE, REPLACE. Er weiss nichts von Zeilen, nichts von Text und
nichts von Aehnlichkeit - er sieht Hashwerte.

Zwei Verfahren stehen bereit:

**SequenceMatcher** (Vorgabe). Das Verfahren aus der Standardbibliothek. Gut
erprobt, schnell genug, und in dieser Anwendung seit M5 im Einsatz.

**Patience**. Ordnet zuerst nur die Zeilen zu, die auf **beiden** Seiten genau
einmal vorkommen, und arbeitet die Zwischenraeume rekursiv ab. Der Grund, warum
es hier ueberhaupt steht: laut Messung an der echten Bibliothek beginnen 41 %
aller Prompts mit demselben 500 bis 1.600 Zeichen langen Textbaustein. Bei so
viel Wiederholung ordnet ein Verfahren, das den laengsten gemeinsamen Block
sucht, gern die falsche Wiederholung einander zu - Patience kann das
konstruktionsbedingt nicht, weil es mehrdeutige Zeilen als Anker gar nicht
zulaesst.

Neue Verfahren brauchen nur `opcodes()` zu erfuellen. Der Rest des Pakets kennt
ausschliesslich diese Methode.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Protocol

from app.analysis.textdiff.options import ALGO_PATIENCE, ALGO_SEQUENCE_MATCHER


class OpKind(str, Enum):
    EQUAL = "equal"
    INSERT = "insert"
    DELETE = "delete"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True)
class DiffOp:
    """Eine Operation ueber halboffene Bereiche beider Folgen.

    Halboffen wie ueberall in Python: (2, 5) sind die Elemente 2, 3 und 4.
    Bei INSERT ist der linke Bereich leer, bei DELETE der rechte.
    """

    kind: OpKind
    left_start: int
    left_end: int
    right_start: int
    right_end: int

    @property
    def left_length(self) -> int:
        return self.left_end - self.left_start

    @property
    def right_length(self) -> int:
        return self.right_end - self.right_start


class DiffStrategy(Protocol):
    """Was ein Abgleichverfahren koennen muss - mehr nicht."""

    def opcodes(
        self, left: Sequence[Hashable], right: Sequence[Hashable]
    ) -> list[DiffOp]:
        ...


class SequenceMatcherStrategy:
    """difflib.SequenceMatcher.

    `autojunk` ist ausgeschaltet. Eingeschaltet erklaert die Heuristik jedes
    Element, das in mehr als einem Prozent der Faelle vorkommt, zu Muell und
    schliesst es vom Abgleich aus - bei einer Datei aus 300 Zeilen trifft das
    jede Zeile, die dreimal vorkommt. In Prompts sind das genau die leeren
    Trennzeilen zwischen den Abschnitten, an denen sich die Ausrichtung
    orientieren sollte.
    """

    def opcodes(
        self, left: Sequence[Hashable], right: Sequence[Hashable]
    ) -> list[DiffOp]:
        matcher = SequenceMatcher(None, list(left), list(right), autojunk=False)
        return [
            DiffOp(OpKind(tag), i1, i2, j1, j2)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        ]


class PatienceStrategy:
    """Patience-Diff: nur eindeutige Zeilen dienen als Anker.

    Ablauf je Bereich:

    1. gemeinsamen Anfang und gemeinsames Ende abschneiden,
    2. alle Werte suchen, die links **und** rechts genau einmal vorkommen,
    3. daraus die laengste aufsteigende Folge waehlen - das sind die Anker,
    4. die Zwischenraeume rekursiv genauso behandeln,
    5. bleibt kein Anker uebrig, entscheidet SequenceMatcher.

    Schritt 5 ist wichtig: ohne ihn wuerde ein Bereich ohne eindeutige Zeile
    komplett als REPLACE durchgereicht, und die Ausrichtung waere schlechter
    als beim einfachen Verfahren.
    """

    #: Ab dieser Rekursionstiefe uebernimmt SequenceMatcher. Schutz gegen
    #: pathologische Eingaben; erreicht wird sie bei echten Prompts nie.
    MAX_DEPTH = 32

    def opcodes(
        self, left: Sequence[Hashable], right: Sequence[Hashable]
    ) -> list[DiffOp]:
        links, rechts = list(left), list(right)
        roh: list[DiffOp] = []
        self._recurse(links, rechts, 0, len(links), 0, len(rechts), 0, roh)
        return _merge_ops(roh)

    # Intern -------------------------------------------------------------------

    def _recurse(
        self,
        links: list,
        rechts: list,
        l1: int,
        l2: int,
        r1: int,
        r2: int,
        tiefe: int,
        aus: list[DiffOp],
    ) -> None:
        # Gemeinsamer Anfang.
        while l1 < l2 and r1 < r2 and links[l1] == rechts[r1]:
            aus.append(DiffOp(OpKind.EQUAL, l1, l1 + 1, r1, r1 + 1))
            l1 += 1
            r1 += 1

        # Gemeinsames Ende - erst eingesammelt, spaeter angehaengt.
        schwanz: list[DiffOp] = []
        while l1 < l2 and r1 < r2 and links[l2 - 1] == rechts[r2 - 1]:
            l2 -= 1
            r2 -= 1
            schwanz.append(DiffOp(OpKind.EQUAL, l2, l2 + 1, r2, r2 + 1))
        schwanz.reverse()

        if l1 == l2 and r1 == r2:
            aus.extend(schwanz)
            return
        if l1 == l2:
            aus.append(DiffOp(OpKind.INSERT, l1, l1, r1, r2))
            aus.extend(schwanz)
            return
        if r1 == r2:
            aus.append(DiffOp(OpKind.DELETE, l1, l2, r1, r1))
            aus.extend(schwanz)
            return

        anker = (
            self._anchors(links, rechts, l1, l2, r1, r2)
            if tiefe < self.MAX_DEPTH
            else []
        )
        if not anker:
            aus.extend(_fallback(links, rechts, l1, l2, r1, r2))
            aus.extend(schwanz)
            return

        # Zwischenraeume vor, zwischen und nach den Ankern.
        vorher_l, vorher_r = l1, r1
        for anker_l, anker_r in anker:
            self._recurse(
                links, rechts, vorher_l, anker_l, vorher_r, anker_r, tiefe + 1, aus
            )
            aus.append(DiffOp(OpKind.EQUAL, anker_l, anker_l + 1, anker_r, anker_r + 1))
            vorher_l, vorher_r = anker_l + 1, anker_r + 1
        self._recurse(links, rechts, vorher_l, l2, vorher_r, r2, tiefe + 1, aus)
        aus.extend(schwanz)

    @staticmethod
    def _anchors(
        links: list, rechts: list, l1: int, l2: int, r1: int, r2: int
    ) -> list[tuple[int, int]]:
        """Beidseitig eindeutige Werte, als laengste aufsteigende Folge."""
        links_zaehler: dict = {}
        for index in range(l1, l2):
            links_zaehler.setdefault(links[index], []).append(index)
        rechts_zaehler: dict = {}
        for index in range(r1, r2):
            rechts_zaehler.setdefault(rechts[index], []).append(index)

        paare = [
            (stellen[0], rechts_zaehler[wert][0])
            for wert, stellen in links_zaehler.items()
            if len(stellen) == 1
            and len(rechts_zaehler.get(wert, ())) == 1
        ]
        paare.sort()
        return _longest_increasing(paare)


def _longest_increasing(paare: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Laengste Teilfolge mit streng steigender rechter Komponente.

    Die Paare sind bereits nach links sortiert. Gesucht ist die groesste
    Auswahl, deren rechte Seiten sich nicht ueberkreuzen - gekreuzte Anker
    liessen sich nicht untereinander darstellen.

    Geduldsspiel-Verfahren mit Rueckzeigern: O(n log n).
    """
    if not paare:
        return []

    import bisect

    enden: list[int] = []          # kleinstes rechtes Ende je Laenge
    positionen: list[int] = []     # Index in `paare` dazu
    vorgaenger: list[int] = [-1] * len(paare)

    for index, (_links, rechts) in enumerate(paare):
        stelle = bisect.bisect_left(enden, rechts)
        if stelle == len(enden):
            enden.append(rechts)
            positionen.append(index)
        else:
            enden[stelle] = rechts
            positionen[stelle] = index
        vorgaenger[index] = positionen[stelle - 1] if stelle > 0 else -1

    ergebnis: list[tuple[int, int]] = []
    laufend = positionen[-1]
    while laufend != -1:
        ergebnis.append(paare[laufend])
        laufend = vorgaenger[laufend]
    ergebnis.reverse()
    return ergebnis


def _fallback(
    links: list, rechts: list, l1: int, l2: int, r1: int, r2: int
) -> list[DiffOp]:
    """SequenceMatcher fuer einen Bereich ohne eindeutige Anker."""
    matcher = SequenceMatcher(None, links[l1:l2], rechts[r1:r2], autojunk=False)
    return [
        DiffOp(OpKind(tag), l1 + i1, l1 + i2, r1 + j1, r1 + j2)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
    ]


def _merge_ops(ops: list[DiffOp]) -> list[DiffOp]:
    """Zieht gleichartige, unmittelbar benachbarte Operationen zusammen.

    Die Rekursion erzeugt EQUAL zeilenweise. Ohne dieses Zusammenziehen
    entstuenden bei einer unveraenderten Datei mit 400 Zeilen 400 Operationen
    statt einer, und jede weitere Stufe muesste sie einzeln abarbeiten.

    Ausserdem werden aufeinanderfolgendes DELETE und INSERT zu einem REPLACE
    vereinigt - erst dadurch bekommt die Ausrichtungsstufe die Gelegenheit,
    aehnliche Zeilen einander zuzuordnen.
    """
    if not ops:
        return []

    ops = [op for op in ops if op.left_length or op.right_length]
    zusammen: list[DiffOp] = []
    for op in ops:
        if not zusammen:
            zusammen.append(op)
            continue

        letzte = zusammen[-1]
        luecke = letzte.left_end == op.left_start and letzte.right_end == op.right_start
        if not luecke:
            zusammen.append(op)
            continue

        if letzte.kind is op.kind:
            zusammen[-1] = DiffOp(
                letzte.kind, letzte.left_start, op.left_end, letzte.right_start, op.right_end
            )
        elif {letzte.kind, op.kind} <= {OpKind.DELETE, OpKind.INSERT, OpKind.REPLACE}:
            zusammen[-1] = DiffOp(
                OpKind.REPLACE,
                letzte.left_start,
                op.left_end,
                letzte.right_start,
                op.right_end,
            )
        else:
            zusammen.append(op)

    return zusammen


#: Die verfuegbaren Verfahren unter ihrem Namen aus den Optionen.
_STRATEGIES: dict[str, type] = {
    ALGO_SEQUENCE_MATCHER: SequenceMatcherStrategy,
    ALGO_PATIENCE: PatienceStrategy,
}


def strategy_for(name: str) -> DiffStrategy:
    """Liefert das benannte Verfahren. Unbekannte Namen fallen auf die Vorgabe."""
    return _STRATEGIES.get(name, SequenceMatcherStrategy)()
