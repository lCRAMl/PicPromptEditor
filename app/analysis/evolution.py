"""Aufbau der Prompt-Evolutionsbaeume.

Spezifikation 05 verlangt Evolutionsbaeume, sagt aber nicht, woher die Richtung
kommt. "Varianten entstehen durch hinzugefuegte Tokens" beschreibt eine
Verwandtschaft, keine Eltern-Kind-Ordnung: aus zwei aehnlichen Prompts allein
laesst sich nicht ablesen, welcher aus welchem entstanden ist.

Die Richtung kommt deshalb aus der Datei-Aenderungszeit:

    Wurzel  = der aelteste Prompt der Familie
    Elter   = der aehnlichste Prompt, der ZEITLICH FRUEHER liegt

Damit ist der Baum wohldefiniert (jeder Knoten ausser der Wurzel hat genau einen
Elter, und der ist strikt aelter, also kann kein Kreis entstehen) und er liest
sich als das, was er sein soll: eine Abfolge von Eingriffen. Ohne Zeitachse
waere es nur eine Aehnlichkeitshierarchie mit willkuerlicher Spitze.

Grenze: sehr grosse Familien werden auf die juengsten Mitglieder beschnitten
(analysis.evolution_max_nodes). Die Elternsuche ist innerhalb einer Familie
quadratisch; das ist bei Hunderten Mitgliedern richtig und bei Zehntausenden
nicht mehr. Ein Baum mit zehntausend Knoten waere ohnehin nicht lesbar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Sequence

ALGO_VERSION = 1


@dataclass(frozen=True, slots=True)
class EvolutionInput:
    """Ein Familienmitglied, wie der Baumbau es braucht."""

    prompt_id: int
    modified_time: int
    segments: frozenset[int]
    label: str = ""
    image_id: int | None = None
    image_count: int = 0


@dataclass
class EvolutionNode:
    prompt_id: int
    parent_id: int | None
    depth: int
    modified_time: int
    similarity: float = 0.0
    label: str = ""
    image_id: int | None = None
    image_count: int = 0
    added: tuple[int, ...] = ()
    removed: tuple[int, ...] = ()
    changed: tuple[tuple[int, int], ...] = ()
    children: list[int] = field(default_factory=list)

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    def change_summary(self) -> str:
        if self.is_root:
            return "Ausgangsprompt"
        parts = []
        if self.changed:
            parts.append(str(len(self.changed)) + " geaendert")
        if self.added:
            parts.append(str(len(self.added)) + " ergaenzt")
        if self.removed:
            parts.append(str(len(self.removed)) + " entfernt")
        return ", ".join(parts) if parts else "unveraendert"


@dataclass
class EvolutionTree:
    cluster_id: int
    root_id: int | None = None
    nodes: dict[int, EvolutionNode] = field(default_factory=dict)
    truncated: int = 0
    algo_version: int = ALGO_VERSION

    @property
    def size(self) -> int:
        return len(self.nodes)

    @property
    def depth(self) -> int:
        return max((node.depth for node in self.nodes.values()), default=0)

    def children_of(self, prompt_id: int) -> list[int]:
        node = self.nodes.get(prompt_id)
        return list(node.children) if node else []

    def path_to_root(self, prompt_id: int) -> list[int]:
        """Kette von diesem Prompt zurueck zur Wurzel - die Entstehungsgeschichte."""
        path: list[int] = []
        current: int | None = prompt_id
        seen: set[int] = set()
        while current is not None and current not in seen:
            seen.add(current)
            path.append(current)
            node = self.nodes.get(current)
            current = node.parent_id if node else None
        return path

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": self.algo_version,
                "cluster": self.cluster_id,
                "root": self.root_id,
                "truncated": self.truncated,
                "nodes": [
                    {
                        "p": node.prompt_id,
                        "e": node.parent_id,
                        "d": node.depth,
                        "t": node.modified_time,
                        "s": round(node.similarity, 4),
                        "l": node.label,
                        "i": node.image_id,
                        "c": node.image_count,
                        "a": list(node.added),
                        "r": list(node.removed),
                        "g": [list(pair) for pair in node.changed],
                        "k": list(node.children),
                    }
                    for node in self.nodes.values()
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, payload: str) -> "EvolutionTree":
        data = json.loads(payload)
        tree = cls(
            cluster_id=int(data.get("cluster", 0)),
            root_id=data.get("root"),
            truncated=int(data.get("truncated", 0)),
            algo_version=int(data.get("v", ALGO_VERSION)),
        )
        for entry in data.get("nodes", []):
            node = EvolutionNode(
                prompt_id=int(entry["p"]),
                parent_id=entry.get("e"),
                depth=int(entry.get("d", 0)),
                modified_time=int(entry.get("t", 0)),
                similarity=float(entry.get("s", 0.0)),
                label=entry.get("l", ""),
                image_id=entry.get("i"),
                image_count=int(entry.get("c", 0)),
                added=tuple(entry.get("a", ())),
                removed=tuple(entry.get("r", ())),
                changed=tuple(tuple(pair) for pair in entry.get("g", ())),
                children=list(entry.get("k", ())),
            )
            tree.nodes[node.prompt_id] = node
        return tree


def build_tree(
    cluster_id: int,
    members: Sequence[EvolutionInput],
    similarity: Callable[[frozenset[int], frozenset[int]], float],
    max_nodes: int = 500,
    change_pairing: Callable[[set[int], set[int]], list[tuple[int, int]]] | None = None,
) -> EvolutionTree:
    """Baut den Evolutionsbaum einer Familie.

    similarity vergleicht zwei Segmentmengen; change_pairing paart entfernte und
    hinzugefuegte Segmente zu Aenderungen. Beide werden hereingereicht, damit
    diese Datei weder Datenbank noch Gewichtungen kennen muss.
    """
    tree = EvolutionTree(cluster_id=cluster_id)
    if not members:
        return tree

    ordered = sorted(members, key=lambda entry: (entry.modified_time, entry.prompt_id))
    if len(ordered) > max_nodes:
        # Die juengsten behalten: die aktuelle Entwicklung ist interessanter als
        # ihre Vorgeschichte, und ein Baum mit tausend Knoten ist unlesbar.
        tree.truncated = len(ordered) - max_nodes
        ordered = ordered[-max_nodes:]

    root = ordered[0]
    tree.root_id = root.prompt_id
    tree.nodes[root.prompt_id] = EvolutionNode(
        prompt_id=root.prompt_id,
        parent_id=None,
        depth=0,
        modified_time=root.modified_time,
        label=root.label,
        image_id=root.image_id,
        image_count=root.image_count,
    )

    for index in range(1, len(ordered)):
        current = ordered[index]
        best_parent = ordered[0]
        best_score = -1.0
        # Nur zeitlich fruehere Mitglieder kommen als Elter in Frage. Das macht
        # den Baum kreisfrei, ohne eine Zyklenpruefung zu brauchen.
        for candidate in ordered[:index]:
            score = similarity(current.segments, candidate.segments)
            if score > best_score:
                best_score = score
                best_parent = candidate

        parent_node = tree.nodes[best_parent.prompt_id]
        added = current.segments - best_parent.segments
        removed = best_parent.segments - current.segments
        changed: list[tuple[int, int]] = []
        if change_pairing is not None and added and removed:
            pairs = change_pairing(set(removed), set(added))
            changed = [tuple(pair) for pair in pairs]
            paired_removed = {pair[0] for pair in changed}
            paired_added = {pair[1] for pair in changed}
            removed = frozenset(removed - paired_removed)
            added = frozenset(added - paired_added)

        node = EvolutionNode(
            prompt_id=current.prompt_id,
            parent_id=best_parent.prompt_id,
            depth=parent_node.depth + 1,
            modified_time=current.modified_time,
            similarity=max(0.0, best_score),
            label=current.label,
            image_id=current.image_id,
            image_count=current.image_count,
            added=tuple(sorted(added)),
            removed=tuple(sorted(removed)),
            changed=tuple(changed),
        )
        tree.nodes[node.prompt_id] = node
        parent_node.children.append(node.prompt_id)

    return tree


@dataclass
class EvolutionStats:
    trees: int = 0
    nodes: int = 0
    skipped: int = 0
    reused: int = 0
    truncated: int = 0
    duration: float = 0.0
    #: Es gab nichts zu tun, weil jede Familie bereits einen gueltigen Baum
    #: hat. Das ist der Normalfall beim zweiten Aufruf - und etwas voellig
    #: anderes als "keine Daten vorhanden".
    up_to_date: int = 0

    def summary(self) -> str:
        if self.up_to_date:
            return "alle " + str(self.up_to_date) + " Baeume sind aktuell"
        if not self.trees and not self.reused:
            return "keine Evolutionsdaten"
        parts = [str(self.trees) + " Baeume neu", str(self.nodes) + " Knoten"]
        if self.reused:
            parts.append(str(self.reused) + " aus dem Cache")
        if self.truncated:
            parts.append(str(self.truncated) + " Knoten gekuerzt")
        return ", ".join(parts)
