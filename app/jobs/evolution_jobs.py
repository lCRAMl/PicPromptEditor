"""Job zum Aufbau der Evolutionsbaeume.

Laeuft im cpu-Pool und ausschliesslich fuer Familien ohne gueltigen Cacheeintrag.
Spezifikation 06 verlangt genau das: Neuberechnung nur bei fehlenden Daten,
gesetztem Dirty-Flag oder ausdruecklicher Anforderung.

Der Job berechnet je Familie einen Baum und schreibt ihn als fertige Struktur.
Die Elternsuche ist innerhalb einer Familie quadratisch - richtig bei Hunderten
Mitgliedern, weshalb die Familiengroesse begrenzt wird.
"""

from __future__ import annotations

import time

from app.analysis.evolution import ALGO_VERSION, EvolutionStats, build_tree
from app.analysis.variants import DEFAULT_CHANGE_THRESHOLD
from app.core.cancellation import CancellationToken
from app.core.errors import ErrorReporter
from app.jobs.job import Job
from app.repositories.evolution_repo import EvolutionRepository

BATCH = 25
LABEL_LIMIT = 80


def _shorten(text: str, limit: int = LABEL_LIMIT) -> str:
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


class EvolutionJob(Job):
    pool = "cpu"
    title = "Evolutionsbaeume werden erstellt"

    def __init__(
        self,
        evolution: EvolutionRepository,
        idf: dict[int, float],
        segment_texts: dict[int, str],
        reporter: ErrorReporter,
        *,
        max_nodes: int = 500,
        change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
        only_cluster: int | None = None,
        token: CancellationToken | None = None,
    ) -> None:
        super().__init__(reporter, token)
        self._repo = evolution
        self._idf = idf
        self._texts = segment_texts
        self._words = {
            token_id: frozenset(text.split())
            for token_id, text in segment_texts.items()
            if text
        }
        self._max_nodes = max_nodes
        self._change_threshold = change_threshold
        self._only_cluster = only_cluster

    def run_job(self) -> EvolutionStats:
        started = time.perf_counter()
        stats = EvolutionStats()

        clusters = (
            [self._only_cluster]
            if self._only_cluster is not None
            else self._repo.stale_clusters(ALGO_VERSION)
        )
        if not clusters:
            stats.duration = time.perf_counter() - started
            return stats

        pending = []
        total = len(clusters)
        for index, cluster_id in enumerate(clusters, start=1):
            self.check_cancelled()
            members = self._repo.members_for_cluster(cluster_id)
            if len(members) < 2:
                stats.skipped += 1
                continue

            tree = build_tree(
                cluster_id,
                members,
                self._similarity,
                max_nodes=self._max_nodes,
                change_pairing=self._pair_changes,
            )
            self._label_nodes(tree)
            stats.trees += 1
            stats.nodes += tree.size
            stats.truncated += tree.truncated
            pending.append(tree)

            if len(pending) >= BATCH:
                self._repo.store(pending, ALGO_VERSION)
                pending = []
            self.report_progress(index, total, "Familien")

        if pending:
            self._repo.store(pending, ALGO_VERSION)

        stats.duration = time.perf_counter() - started
        return stats

    def _label_nodes(self, tree) -> None:
        """Benennt jeden Knoten nach dem, was ihn vom Elter unterscheidet.

        Die erste Zeile des Prompts ist innerhalb einer Familie meist bei allen
        gleich - als Beschriftung waere sie wertlos, weil der Baum dann fuenfmal
        denselben Text zeigte. Aussagekraeftig ist die Aenderung: der neue Satz,
        der ergaenzte, der entfallene. Nur die Wurzel behaelt ihren Promptanfang,
        denn sie hat keine Aenderung.
        """
        for node in tree.nodes.values():
            if node.is_root:
                continue
            if node.changed:
                _removed, added = node.changed[0]
                text = self._texts.get(added, "")
                if text:
                    node.label = "geaendert zu: " + _shorten(text)
                    continue
            if node.added:
                text = self._texts.get(node.added[0], "")
                if text:
                    extra = ""
                    if len(node.added) > 1:
                        extra = "  (+" + str(len(node.added) - 1) + " weitere)"
                    node.label = "ergaenzt: " + _shorten(text) + extra
                    continue
            if node.removed:
                text = self._texts.get(node.removed[0], "")
                if text:
                    node.label = "entfernt: " + _shorten(text)
                    continue
            node.label = "unveraendert"

    # Rechenhilfen -------------------------------------------------------------

    def _similarity(self, left: frozenset[int], right: frozenset[int]) -> float:
        """idf-gewichtete Jaccard, dieselbe Groesse wie in der Familienbildung.

        Waere hier ungewichtet gerechnet, koennte der Baum eine andere Struktur
        zeigen als die Familie, zu der er gehoert.
        """
        common = left & right
        if not common:
            return 0.0
        shared = sum(self._idf.get(segment, 0.0) for segment in common)
        union = sum(self._idf.get(segment, 0.0) for segment in left | right)
        return shared / union if union > 0 else 0.0

    def _pair_changes(self, removed: set[int], added: set[int]) -> list[tuple[int, int]]:
        """Paart entfernte und hinzugefuegte Segmente nach Dice.

        Damit wird aus "Satz weg, Satz neu" eine Aenderung an derselben Stelle -
        dieselbe Regel wie in Variantenbildung und Vergleichsansicht.
        """
        scored: list[tuple[float, int, int]] = []
        for r in removed:
            left = self._words.get(r)
            if not left:
                continue
            for a in added:
                right = self._words.get(a)
                if not right:
                    continue
                total = len(left) + len(right)
                overlap = (2 * len(left & right) / total) if total else 0.0
                if overlap >= self._change_threshold:
                    scored.append((overlap, r, a))

        scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
        pairs: list[tuple[int, int]] = []
        used_removed: set[int] = set()
        used_added: set[int] = set()
        for _overlap, r, a in scored:
            if r in used_removed or a in used_added:
                continue
            used_removed.add(r)
            used_added.add(a)
            pairs.append((r, a))
        return pairs
