"""Promptfamilien aus dem Nachbarschaftsgraphen.

Spezifikation 05 verlangt Cluster aehnlicher Prompts. Verwendet wird Union-Find
ueber die Kanten oberhalb des Schwellwerts, nicht ein globales Clusterverfahren.

Der Grund ist die inkrementelle Aktualisierung: kommt ein Prompt hinzu, muessen
bei Union-Find nur seine eigenen Kanten betrachtet werden. Ein Verfahren wie
k-Means oder Leiden wuerde die gesamte Bibliothek neu einteilen und dabei auch
Cluster umbauen, die sich gar nicht geaendert haben - fuer den Benutzer sieht das
aus, als waere die Ordnung willkuerlich.

Union-Find ist zudem parameterarm: der einzige Regler ist der Schwellwert, und
der ist erklaerbar ("ab welcher Aehnlichkeit gehoeren zwei Prompts zusammen").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


class UnionFind:
    """Mengenverwaltung mit Pfadverkuerzung und Vereinigung nach Rang."""

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}
        self._rank: dict[int, int] = {}

    def add(self, item: int) -> None:
        if item not in self._parent:
            self._parent[item] = item
            self._rank[item] = 0

    def find(self, item: int) -> int:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        # Pfadverkuerzung: der naechste Aufruf trifft die Wurzel direkt.
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: int, right: int) -> bool:
        a, b = self.find(left), self.find(right)
        if a == b:
            return False
        if self._rank[a] < self._rank[b]:
            a, b = b, a
        self._parent[b] = a
        if self._rank[a] == self._rank[b]:
            self._rank[a] += 1
        return True

    def groups(self) -> dict[int, list[int]]:
        found: dict[int, list[int]] = {}
        for item in self._parent:
            found.setdefault(self.find(item), []).append(item)
        return found


@dataclass
class Cluster:
    members: list[int]
    representative: int
    base_signature: frozenset[int]
    average_similarity: float = 0.0
    similarity_to_base: dict[int, float] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass
class ClusterStats:
    clusters: int = 0
    clustered_prompts: int = 0
    singletons: int = 0
    largest: int = 0
    duration: float = 0.0

    def summary(self) -> str:
        if not self.clusters:
            return "keine Familien gefunden"
        return (
            str(self.clusters)
            + " Familien, "
            + str(self.clustered_prompts)
            + " Prompts, groesste "
            + str(self.largest)
        )


def build_clusters(
    neighbours: dict[int, list],
    threshold: float,
    minimum_size: int = 2,
) -> tuple[list[list[int]], ClusterStats]:
    """Gruppiert Prompts ueber Kanten oberhalb des Schwellwerts."""
    union = UnionFind()
    for key, entries in neighbours.items():
        union.add(key)
        for entry in entries:
            if entry.similarity >= threshold:
                union.union(key, entry.key)

    groups = [members for members in union.groups().values() if len(members) >= minimum_size]
    groups.sort(key=len, reverse=True)

    stats = ClusterStats(
        clusters=len(groups),
        clustered_prompts=sum(len(g) for g in groups),
        singletons=sum(1 for members in union.groups().values() if len(members) < minimum_size),
        largest=len(groups[0]) if groups else 0,
    )
    return groups, stats


def base_signature(
    members: Sequence[int],
    segments_of: dict[int, frozenset[int]],
    min_commonness: float,
) -> frozenset[int]:
    """Die Segmente, die den Basisprompt einer Familie ausmachen.

    Spezifikation 05: "Haeufig gemeinsam auftretende Tokens bilden einen
    Basisprompt." Haeufig heisst hier: in mindestens min_commonness der
    Mitglieder enthalten - bezogen auf die FAMILIE, nicht auf die Bibliothek.
    Ein Satz kann bibliotheksweit selten und innerhalb einer Familie
    allgegenwaertig sein; genau der macht die Familie aus.
    """
    if not members:
        return frozenset()
    counts: dict[int, int] = {}
    for member in members:
        for segment in segments_of.get(member, frozenset()):
            counts[segment] = counts.get(segment, 0) + 1

    needed = max(1, round(len(members) * min_commonness))
    return frozenset(segment for segment, count in counts.items() if count >= needed)


def pick_representative(
    members: Sequence[int],
    segments_of: dict[int, frozenset[int]],
    signature: frozenset[int],
) -> tuple[int, dict[int, float]]:
    """Waehlt den realen Prompt, der die Basis am besten vertritt.

    Spezifikation 03 laesst prompt_variants.base_prompt_id auf prompts.id
    zeigen. Ein aus der Schnittmenge gebildeter Basisprompt existiert aber in
    der Regel als kein realer Prompt. Der Repraesentant ist sein reales
    Gegenstueck.

    Ausgewaehlt wird primaer nach ABDECKUNG der Signatur, nicht nach Jaccard.
    Der Unterschied ist wesentlich: bei einer Basis {100, 101} bewertet Jaccard
    einen Prompt {100} mit 0.5 und einen Prompt {100, 101, 300, 301, 302} mit
    0.4 - der Prompt mit der halben Basis gaebe also den Basisprompt ab. Jedes
    Geschwister saehe dann so aus, als haette es den fehlenden Satz
    hinzugefuegt. Bei Gleichstand entscheidet, wer weniger Zusaetzliches
    mitbringt.

    Der zurueckgegebene Wert je Mitglied ist weiterhin die Jaccard-Aehnlichkeit
    zur Basis; sie landet als similarity_to_base in cluster_members und
    beantwortet die andere Frage: wie nah ist dieses Mitglied an der Basis.
    """
    if not members:
        return 0, {}
    if not signature:
        return min(members), {member: 0.0 for member in members}

    scores: dict[int, float] = {}
    ranking: dict[int, tuple[float, int]] = {}
    for member in members:
        own = segments_of.get(member, frozenset())
        shared = len(signature & own)
        union = signature | own
        scores[member] = shared / len(union) if union else 0.0
        ranking[member] = (shared / len(signature), len(own - signature))

    # Meiste Abdeckung, dann wenigste Zusaetze, dann kleinste Id - damit die
    # Wahl bei gleichwertigen Kandidaten reproduzierbar bleibt.
    best = min(
        members,
        key=lambda member: (-ranking[member][0], ranking[member][1], member),
    )
    return best, scores
