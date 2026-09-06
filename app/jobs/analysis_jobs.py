"""Job der Analysis Engine: Aehnlichkeit, Familien und Varianten.

Laeuft im cpu-Pool. Der Job liest die Segmentmengen einmal in den Speicher,
rechnet dort und schreibt das Ergebnis in einer Transaktion zurueck. Das ist die
richtige Aufteilung, weil die Rechnung viele wahlfreie Zugriffe auf dieselben
Mengen braucht - als SQL formuliert waere sie ein Vielfaches langsamer.

Speicherbedarf: bei zwanzigtausend eindeutigen Prompttexten mit je vierzig
Segmenten sind das rund achthunderttausend Ganzzahlen, also wenige zehn Megabyte.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.analysis.clustering import (
    Cluster,
    ClusterStats,
    base_signature,
    build_clusters,
    pick_representative,
)
from app.analysis.similarity import SimilarityEngine, SimilarityStats
from app.analysis.variants import build_variant
from app.core.cancellation import CancellationToken
from app.core.errors import ErrorReporter
from app.jobs.job import Job
from app.repositories.cluster_repo import ClusterRepository

ALGO_VERSION = 1


@dataclass
class AnalysisResult:
    similarity: SimilarityStats = field(default_factory=SimilarityStats)
    clusters: ClusterStats = field(default_factory=ClusterStats)
    variants: int = 0
    written: dict[str, int] = field(default_factory=dict)
    duration: float = 0.0

    def summary(self) -> str:
        if not self.similarity.prompts:
            return "nichts zu analysieren"
        parts = [
            str(self.similarity.prompts) + " Textvarianten",
            self.clusters.summary(),
        ]
        if self.variants:
            parts.append(str(self.variants) + " Varianten")
        return ", ".join(parts)


class PromptAnalysisJob(Job):
    pool = "cpu"
    title = "Aehnlichkeiten werden berechnet"

    def __init__(
        self,
        clusters: ClusterRepository,
        reporter: ErrorReporter,
        *,
        similarity_threshold: float = 0.55,
        cluster_threshold: float = 0.7,
        neighbours_k: int = 20,
        base_min_commonness: float = 0.6,
        token: CancellationToken | None = None,
    ) -> None:
        super().__init__(reporter, token)
        self._repo = clusters
        self._similarity_threshold = similarity_threshold
        self._cluster_threshold = cluster_threshold
        self._neighbours_k = neighbours_k
        self._base_min_commonness = base_min_commonness

    def run_job(self) -> AnalysisResult:
        started = time.perf_counter()
        result = AnalysisResult()

        expansion = self._repo.representatives()
        if len(expansion) < 2:
            result.duration = time.perf_counter() - started
            return result

        keys = sorted(expansion)
        self.report_progress(0, len(keys), "Segmentmengen werden geladen")
        vectors = self._repo.vectors_for(keys)
        idf = self._repo.idf_map()
        self.check_cancelled()

        engine = SimilarityEngine(
            idf,
            threshold=self._similarity_threshold,
            top_k=self._neighbours_k,
        )
        neighbours, result.similarity = engine.build(
            vectors,
            self.token,
            lambda current, total: self.report_progress(current, total, "Vergleiche"),
        )
        self.check_cancelled()

        segments_of = {vector.key: vector.segments for vector in vectors}
        groups, result.clusters = build_clusters(neighbours, self._cluster_threshold)

        built: list[Cluster] = []
        variants = []
        words = self._repo.segment_words() if groups else {}

        for members in groups:
            self.check_cancelled()
            signature = base_signature(members, segments_of, self._base_min_commonness)
            representative, scores = pick_representative(members, segments_of, signature)
            average = sum(scores.values()) / len(scores) if scores else 0.0

            built.append(
                Cluster(
                    members=members,
                    representative=representative,
                    base_signature=signature,
                    average_similarity=average,
                    similarity_to_base=scores,
                )
            )

            base_prompt_id = representative
            for member in members:
                if member == base_prompt_id:
                    continue
                for prompt_id in expansion.get(member, [member]):
                    variants.append(
                        build_variant(
                            prompt_id,
                            base_prompt_id,
                            segments_of[member],
                            signature,
                            scores.get(member, 0.0),
                            words,
                        )
                    )

        result.variants = len(variants)
        result.written = self._repo.replace_all(
            neighbours, built, variants, expansion, ALGO_VERSION
        )
        result.duration = time.perf_counter() - started
        return result
