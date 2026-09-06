"""Aehnlichkeit zwischen Prompts auf Basis ihrer Segmentmengen.

Spezifikation 05 fordert "fuer jeden Prompt eine Aehnlichkeitsanalyse", sagt
aber nicht wie. Naiv sind das bei hunderttausend Prompts fuenf Milliarden Paare.
Drei Stufen halten den Aufwand beherrschbar:

1. Deduplizieren ueber den Prompttext. Batch-Generierungen erzeugen viele
   Dateien mit identischem Text; gerechnet wird einmal je eindeutigem Text.

2. Kandidaten ueber einen invertierten Index auf den SELTENEN Segmenten. Zwei
   Prompts, die kein einziges seltenes Segment teilen, koennen nicht aehnlich
   sein - sie muessen also gar nicht verglichen werden. Haeufige Segmente
   bleiben aus dem Index heraus: ein Satz, der in achtzig Prozent aller Prompts
   steht, wuerde fast die ganze Bibliothek als Kandidat liefern und traegt
   zugleich am wenigsten Unterscheidungskraft bei.

3. Exakt bewerten nur auf den Kandidaten, mit idf-gewichteter Jaccard:

       sim(A,B) = Summe idf(s) fuer s in A geschnitten B
                / Summe idf(s) fuer s in A vereinigt B

   Gewichtet, weil ein gemeinsamer Textbaustein weniger ueber Verwandtschaft
   aussagt als ein gemeinsamer seltener Satz.

Gespeichert wird nur Top-k je Prompt. Alle Paare waeren quadratisch, Top-k ist
linear.

Die Engine kennt weder Datenbank noch GUI. Sie bekommt fertige Mengen und
liefert Kanten.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from app.core.cancellation import CancellationToken

# Segmente, die in mehr als diesem Anteil der Prompts vorkommen, wandern nicht
# in den invertierten Index. Sie sind Textbausteine, keine Kennzeichen.
DEFAULT_MAX_POSTING_RATIO = 0.4
# Untergrenze der Postingliste. Ohne sie ist die Verhaeltnisregel bei kleinen
# Bibliotheken schaedlich: bei fuenf Dokumenten liegt das Limit bei zwei, und
# damit fallen genau die gemeinsamen Saetze aus dem Index, die eine Familie
# ausmachen. Bis zu dieser Groesse wird schlicht alles indiziert.
MIN_POSTING_FLOOR = 200
# So viele der seltensten Segmente eines Prompts erzeugen seine Kandidaten.
DEFAULT_PROBE_SEGMENTS = 12


@dataclass(frozen=True, slots=True)
class PromptVector:
    """Ein Prompt als Segmentmenge."""

    key: int
    segments: frozenset[int]

    def __len__(self) -> int:
        return len(self.segments)


@dataclass(frozen=True, slots=True)
class Neighbour:
    key: int
    similarity: float


@dataclass
class SimilarityStats:
    prompts: int = 0
    comparisons: int = 0
    edges: int = 0
    skipped_empty: int = 0
    duration: float = 0.0

    def summary(self) -> str:
        if not self.prompts:
            return "nichts zu vergleichen"
        parts = [str(self.prompts) + " Prompts", str(self.edges) + " Kanten"]
        if self.comparisons:
            parts.append(str(self.comparisons) + " Vergleiche")
        return ", ".join(parts)


def idf_from_counts(doc_count: int, total_prompts: int) -> float:
    """Unterscheidungskraft eines Segments. Dieselbe Formel wie in der View token_stats."""
    return math.log(max(1, total_prompts) / max(1, doc_count))


class SimilarityEngine:
    def __init__(
        self,
        idf: dict[int, float],
        *,
        max_posting_ratio: float = DEFAULT_MAX_POSTING_RATIO,
        probe_segments: int = DEFAULT_PROBE_SEGMENTS,
        threshold: float = 0.5,
        top_k: int = 20,
    ) -> None:
        self._idf = idf
        self._max_posting_ratio = max_posting_ratio
        self._probe = max(1, probe_segments)
        self._threshold = threshold
        self._top_k = max(1, top_k)

    # Oeffentlich --------------------------------------------------------------

    def build(
        self,
        vectors: Sequence[PromptVector],
        token: CancellationToken | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[dict[int, list[Neighbour]], SimilarityStats]:
        """Berechnet fuer jeden Prompt seine naechsten Nachbarn."""
        stats = SimilarityStats(prompts=len(vectors))
        if len(vectors) < 2:
            return {}, stats

        usable = [v for v in vectors if v.segments]
        stats.skipped_empty = len(vectors) - len(usable)
        if len(usable) < 2:
            return {}, stats

        index = self._build_index(usable)
        weights = {v.key: self._weight(v.segments) for v in usable}
        by_key = {v.key: v for v in usable}

        result: dict[int, list[Neighbour]] = {}
        total = len(usable)

        for position, vector in enumerate(usable, start=1):
            if token is not None and token.cancelled:
                break
            candidates = self._candidates(vector, index)
            heap: list[tuple[float, int]] = []

            for other_key in candidates:
                if other_key == vector.key:
                    continue
                other = by_key[other_key]
                stats.comparisons += 1
                score = self._similarity(vector, other, weights)
                if score < self._threshold:
                    continue
                if len(heap) < self._top_k:
                    heapq.heappush(heap, (score, other_key))
                elif score > heap[0][0]:
                    heapq.heapreplace(heap, (score, other_key))

            if heap:
                ranked = sorted(heap, key=lambda entry: (-entry[0], entry[1]))
                result[vector.key] = [Neighbour(key, score) for score, key in ranked]
                stats.edges += len(ranked)

            if progress is not None and position % 200 == 0:
                progress(position, total)

        if progress is not None:
            progress(total, total)
        return result, stats

    def similarity_of(self, left: PromptVector, right: PromptVector) -> float:
        """Einzelvergleich, etwa fuer die Vergleichsansicht."""
        weights = {
            left.key: self._weight(left.segments),
            right.key: self._weight(right.segments),
        }
        return self._similarity(left, right, weights)

    def difference(
        self, left: PromptVector, right: PromptVector
    ) -> tuple[frozenset[int], frozenset[int], frozenset[int]]:
        """(gemeinsam, nur links, nur rechts) - Grundlage von Variante und Vergleich."""
        common = left.segments & right.segments
        return common, left.segments - right.segments, right.segments - left.segments

    # Intern -------------------------------------------------------------------

    def _weight(self, segments: Iterable[int]) -> float:
        return sum(self._idf.get(segment, 0.0) for segment in segments)

    def _build_index(self, vectors: Sequence[PromptVector]) -> dict[int, list[int]]:
        """Invertierter Index, beschraenkt auf hinreichend seltene Segmente."""
        postings: dict[int, list[int]] = {}
        for vector in vectors:
            for segment in vector.segments:
                postings.setdefault(segment, []).append(vector.key)

        limit = max(MIN_POSTING_FLOOR, int(len(vectors) * self._max_posting_ratio))
        return {
            segment: keys for segment, keys in postings.items() if len(keys) <= limit
        }

    def _candidates(self, vector: PromptVector, index: dict[int, list[int]]) -> set[int]:
        """Kandidaten aus den seltensten Segmenten des Prompts.

        Nur die seltensten, weil jedes weitere Segment die Kandidatenmenge
        vergroessert, ohne Treffer hinzuzufuegen, die die haeufigen Segmente
        nicht ohnehin liefern.
        """
        probes = heapq.nlargest(
            self._probe, vector.segments, key=lambda s: self._idf.get(s, 0.0)
        )
        candidates: set[int] = set()
        for segment in probes:
            posting = index.get(segment)
            if posting:
                candidates.update(posting)
        return candidates

    def _similarity(
        self, left: PromptVector, right: PromptVector, weights: dict[int, float]
    ) -> float:
        common = left.segments & right.segments
        if not common:
            return 0.0
        shared = self._weight(common)
        union = weights[left.key] + weights[right.key] - shared
        return shared / union if union > 0 else 0.0


def build_vectors(rows: Iterable[tuple[int, int]]) -> list[PromptVector]:
    """Baut Vektoren aus (prompt_id, token_id)-Paaren."""
    grouped: dict[int, set[int]] = {}
    for prompt_id, token_id in rows:
        grouped.setdefault(prompt_id, set()).add(token_id)
    return [PromptVector(key, frozenset(segments)) for key, segments in grouped.items()]
