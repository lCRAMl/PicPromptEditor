"""Tokenbasierter Promptvergleich.

Spezifikation 01 verlangt eine Gegenueberstellung wie in WinMerge, aber
"tokenbasiert statt zeilenbasiert". Spezifikation 05 praezisiert: eine Aenderung
von "blue eyes" zu "green eyes" ist eine gezielte Tokenaenderung, keine
Zeilenaenderung.

Der Vergleich laeuft deshalb in drei Stufen:

1. difflib.SequenceMatcher ueber die SEGMENT-IDs in Dokumentreihenfolge. Das
   liefert Bloecke: gleich, ersetzt, geloescht, eingefuegt. Verglichen werden
   Ganzzahlen, nicht Text - zwei Saetze sind gleich, wenn ihre Normalform gleich
   ist, unabhaengig von Schreibweise und Zeilenumbruch.

2. Innerhalb eines "ersetzt"-Blocks werden entfernte und hinzugefuegte Segmente
   nach Dice gepaart. Erst dadurch wird aus "Satz weg, Satz neu" eine Aenderung
   an derselben Stelle. Ohne diesen Schritt zeigte die Ansicht bei jeder
   Wortaenderung eine rote und eine gruene Zeile statt einer gelben.

3. Innerhalb eines gepaarten Satzes wird auf Wortebene verfeinert, damit die
   Hervorhebung auf die tatsaechlich geaenderten Woerter zeigt und nicht auf den
   ganzen Satz.

Die Engine kennt weder Datenbank noch GUI. Sie bekommt Segmentlisten und liefert
Spannen im Originaltext.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum

from app.analysis.variants import DEFAULT_CHANGE_THRESHOLD


class DiffKind(str, Enum):
    EQUAL = "equal"
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class DiffSegment:
    """Ein Segment, wie es der Vergleich braucht."""

    token_id: int
    text: str
    char_start: int
    char_end: int
    negative: bool = False

    @property
    def words(self) -> frozenset[str]:
        return frozenset(self.text.split())


@dataclass(frozen=True, slots=True)
class WordSpan:
    """Ein geaendertes Wort innerhalb eines gepaarten Satzes."""

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class DiffSpan:
    """Eine Hervorhebung im Originaltext einer Seite."""

    start: int
    end: int
    kind: DiffKind
    partner: int | None = None
    words: tuple[WordSpan, ...] = ()


@dataclass
class DiffStats:
    common: int = 0
    changed: int = 0
    added: int = 0
    removed: int = 0
    left_total: int = 0
    right_total: int = 0

    @property
    def different(self) -> int:
        return self.changed + self.added + self.removed

    @property
    def agreement(self) -> float:
        """Anteil gemeinsamer Segmente an allen beteiligten Segmenten."""
        total = self.common + self.different
        return self.common / total if total else 0.0

    def summary(self) -> str:
        parts = [str(self.common) + " gemeinsam"]
        if self.changed:
            parts.append(str(self.changed) + " geaendert")
        if self.added:
            parts.append(str(self.added) + " nur rechts")
        if self.removed:
            parts.append(str(self.removed) + " nur links")
        return ", ".join(parts)


@dataclass
class PromptDiff:
    left: tuple[DiffSpan, ...] = ()
    right: tuple[DiffSpan, ...] = ()
    stats: DiffStats = field(default_factory=DiffStats)
    pairs: tuple[tuple[int, int], ...] = ()

    @property
    def is_identical(self) -> bool:
        return self.stats.different == 0


def _dice(left: frozenset[str], right: frozenset[str]) -> float:
    total = len(left) + len(right)
    return (2 * len(left & right) / total) if total else 0.0


def _pair_block(
    removed: list[DiffSegment],
    added: list[DiffSegment],
    threshold: float,
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Paart entfernte und hinzugefuegte Segmente eines Blocks nach Dice.

    Liefert Indexpaare sowie die ungepaarten Indizes beider Seiten. Die Zuordnung
    ist gierig nach absteigender Ueberlappung, damit jedes Segment hoechstens
    einmal gepaart wird.
    """
    scored: list[tuple[float, int, int]] = []
    for left_index, left_segment in enumerate(removed):
        for right_index, right_segment in enumerate(added):
            overlap = _dice(left_segment.words, right_segment.words)
            if overlap >= threshold:
                scored.append((overlap, left_index, right_index))
    scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    pairs: list[tuple[int, int]] = []
    used_left: set[int] = set()
    used_right: set[int] = set()
    for _overlap, left_index, right_index in scored:
        if left_index in used_left or right_index in used_right:
            continue
        used_left.add(left_index)
        used_right.add(right_index)
        pairs.append((left_index, right_index))

    lonely_left = [i for i in range(len(removed)) if i not in used_left]
    lonely_right = [i for i in range(len(added)) if i not in used_right]
    return pairs, lonely_left, lonely_right


def refine_words(left: DiffSegment, right: DiffSegment) -> tuple[tuple[WordSpan, ...], tuple[WordSpan, ...]]:
    """Wortgenaue Unterschiede innerhalb eines gepaarten Satzes.

    Die Spannen sind absolut im Originaltext, damit der Editor sie ohne weitere
    Umrechnung verwenden kann.
    """
    left_words = _word_positions(left)
    right_words = _word_positions(right)
    matcher = difflib.SequenceMatcher(
        None,
        [word for word, _s, _e in left_words],
        [word for word, _s, _e in right_words],
        autojunk=False,
    )

    left_spans: list[WordSpan] = []
    right_spans: list[WordSpan] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for _word, start, end in left_words[i1:i2]:
            left_spans.append(WordSpan(start, end))
        for _word, start, end in right_words[j1:j2]:
            right_spans.append(WordSpan(start, end))
    return tuple(left_spans), tuple(right_spans)


def _word_positions(segment: DiffSegment) -> list[tuple[str, int, int]]:
    """Woerter eines Segments mit ihren Positionen im Originaltext.

    Gerechnet wird auf dem gespeicherten Segmenttext; dessen Laenge kann von der
    Spanne im Original abweichen, wenn dort mehrfache Leerzeichen stehen. In dem
    Fall wird die Verfeinerung ausgelassen, statt falsche Stellen zu markieren.
    """
    text = segment.text
    if segment.char_end - segment.char_start != len(text):
        return []

    found: list[tuple[str, int, int]] = []
    position = 0
    for word in text.split():
        index = text.find(word, position)
        if index < 0:
            continue
        found.append((word, segment.char_start + index, segment.char_start + index + len(word)))
        position = index + len(word)
    return found


def compare_prompts(
    left: list[DiffSegment],
    right: list[DiffSegment],
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    refine: bool = True,
) -> PromptDiff:
    """Stellt zwei Prompts segmentweise gegenueber."""
    matcher = difflib.SequenceMatcher(
        None,
        [segment.token_id for segment in left],
        [segment.token_id for segment in right],
        autojunk=False,
    )

    left_spans: list[DiffSpan] = []
    right_spans: list[DiffSpan] = []
    pairs: list[tuple[int, int]] = []
    stats = DiffStats(left_total=len(left), right_total=len(right))

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                left_index, right_index = i1 + offset, j1 + offset
                left_spans.append(_span(left[left_index], DiffKind.EQUAL, right_index))
                right_spans.append(_span(right[right_index], DiffKind.EQUAL, left_index))
            stats.common += i2 - i1
            continue

        removed = left[i1:i2]
        added = right[j1:j2]
        block_pairs, lonely_left, lonely_right = _pair_block(
            removed, added, change_threshold
        )

        for left_offset, right_offset in block_pairs:
            left_index, right_index = i1 + left_offset, j1 + right_offset
            left_words: tuple[WordSpan, ...] = ()
            right_words: tuple[WordSpan, ...] = ()
            if refine:
                left_words, right_words = refine_words(
                    left[left_index], right[right_index]
                )
            left_spans.append(
                _span(left[left_index], DiffKind.CHANGED, right_index, left_words)
            )
            right_spans.append(
                _span(right[right_index], DiffKind.CHANGED, left_index, right_words)
            )
            pairs.append((left_index, right_index))
        stats.changed += len(block_pairs)

        for offset in lonely_left:
            left_spans.append(_span(left[i1 + offset], DiffKind.REMOVED))
        stats.removed += len(lonely_left)

        for offset in lonely_right:
            right_spans.append(_span(right[j1 + offset], DiffKind.ADDED))
        stats.added += len(lonely_right)

    left_spans.sort(key=lambda span: span.start)
    right_spans.sort(key=lambda span: span.start)
    return PromptDiff(
        left=tuple(left_spans),
        right=tuple(right_spans),
        stats=stats,
        pairs=tuple(pairs),
    )


def _span(
    segment: DiffSegment,
    kind: DiffKind,
    partner: int | None = None,
    words: tuple[WordSpan, ...] = (),
) -> DiffSpan:
    return DiffSpan(
        start=segment.char_start,
        end=segment.char_end,
        kind=kind,
        partner=partner,
        words=words,
    )
