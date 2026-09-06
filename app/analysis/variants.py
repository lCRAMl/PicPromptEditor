"""Variantenbildung: was unterscheidet einen Prompt von seinem Basisprompt.

Spezifikation 05: "Die Engine bestimmt fuer jeden Prompt den wahrscheinlichsten
Basisprompt sowie die zugehoerigen Abweichungen. Hinzugefuegte, entfernte und
geaenderte Tokens werden gespeichert."

Der Unterschied zwischen "entfernt plus hinzugefuegt" und "geaendert" ist keine
Spitzfindigkeit. Wird "warm golden sunlight dominates the scene" zu "cold blue
moonlight dominates the scene", ist das EINE Aenderung an einer Stelle - nicht
ein verschwundener und ein neuer Satz. Nur mit dieser Unterscheidung liest sich
ein Evolutionsbaum spaeter als Abfolge gezielter Eingriffe.

Erkannt wird die Paarung ueber die Wortueberlappung nach Dice:

    dice(A,B) = 2 * |A geschnitten B| / (|A| + |B|)

und nicht ueber Jaccard. Der Unterschied entscheidet den Regelfall: fuer
"warm golden sunlight dominates the scene" gegen "cold blue moonlight dominates
the scene" liefert Jaccard 0.33 und Dice 0.50. Bei einer Schwelle von 0.4
verfehlt Jaccard genau die Aenderung, um die es geht, waehrend Dice sie erkennt
und unverwandte Saetze weiterhin bei 0.0 laesst.

Die Zuordnung erfolgt gierig nach absteigender Ueberlappung, damit jedes Segment
hoechstens einmal gepaart wird.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

DEFAULT_CHANGE_THRESHOLD = 0.4


@dataclass(frozen=True, slots=True)
class SegmentChange:
    removed: int
    added: int
    overlap: float


@dataclass
class VariantDiff:
    """Der Unterschied eines Prompts zu seinem Basisprompt."""

    prompt_id: int
    base_prompt_id: int
    similarity: float
    common: tuple[int, ...] = ()
    added: tuple[int, ...] = ()
    removed: tuple[int, ...] = ()
    changed: tuple[SegmentChange, ...] = ()

    @property
    def added_count(self) -> int:
        return len(self.added)

    @property
    def removed_count(self) -> int:
        return len(self.removed)

    @property
    def changed_count(self) -> int:
        return len(self.changed)

    @property
    def is_identical(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def to_json(self) -> str:
        return json.dumps(
            {
                "common": list(self.common),
                "added": list(self.added),
                "removed": list(self.removed),
                "changed": [
                    {"r": c.removed, "a": c.added, "o": round(c.overlap, 3)}
                    for c in self.changed
                ],
            },
            separators=(",", ":"),
        )

    def summary(self) -> str:
        parts = []
        if self.changed:
            parts.append(str(len(self.changed)) + " geaendert")
        if self.added:
            parts.append(str(len(self.added)) + " ergaenzt")
        if self.removed:
            parts.append(str(len(self.removed)) + " entfernt")
        return ", ".join(parts) if parts else "unveraendert"


def detect_changes(
    removed: set[int],
    added: set[int],
    words_of: dict[int, frozenset[str]],
    threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> tuple[list[SegmentChange], set[int], set[int]]:
    """Paart entfernte und hinzugefuegte Segmente zu Aenderungen.

    Liefert (Aenderungen, verbleibende Entfernungen, verbleibende Ergaenzungen).
    """
    if not removed or not added:
        return [], set(removed), set(added)

    scored: list[tuple[float, int, int]] = []
    for r in removed:
        left = words_of.get(r, frozenset())
        if not left:
            continue
        for a in added:
            right = words_of.get(a, frozenset())
            if not right:
                continue
            shared = len(left & right)
            total = len(left) + len(right)
            overlap = (2 * shared / total) if total else 0.0
            if overlap >= threshold:
                scored.append((overlap, r, a))

    scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    changes: list[SegmentChange] = []
    used_removed: set[int] = set()
    used_added: set[int] = set()
    for overlap, r, a in scored:
        if r in used_removed or a in used_added:
            continue
        used_removed.add(r)
        used_added.add(a)
        changes.append(SegmentChange(removed=r, added=a, overlap=overlap))

    return changes, removed - used_removed, added - used_added


def build_variant(
    prompt_id: int,
    base_prompt_id: int,
    prompt_segments: frozenset[int],
    base_segments: frozenset[int],
    similarity: float,
    words_of: dict[int, frozenset[str]] | None = None,
    change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> VariantDiff:
    """Stellt einen Prompt seinem Basisprompt gegenueber."""
    common = prompt_segments & base_segments
    removed = base_segments - prompt_segments
    added = prompt_segments - base_segments

    changes: list[SegmentChange] = []
    if words_of:
        changes, removed, added = detect_changes(
            set(removed), set(added), words_of, change_threshold
        )

    return VariantDiff(
        prompt_id=prompt_id,
        base_prompt_id=base_prompt_id,
        similarity=similarity,
        common=tuple(sorted(common)),
        added=tuple(sorted(added)),
        removed=tuple(sorted(removed)),
        changed=tuple(changes),
    )


def words_from_texts(texts: dict[int, str]) -> dict[int, frozenset[str]]:
    """Wortmengen je Segment, Grundlage der Aenderungserkennung."""
    return {
        token_id: frozenset(text.split()) for token_id, text in texts.items() if text
    }
