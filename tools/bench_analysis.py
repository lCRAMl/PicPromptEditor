"""Belastungsmessung der Analysekette.

Beantwortet die Frage, die die Architektur behauptet: bleibt die
Aehnlichkeitsberechnung unterhalb des quadratischen Aufwands, und wie stark
wirkt die Kandidatenauswahl?

Aufruf:
    python tools/bench_analysis.py --prompts 5000 --families 200

Gemessen wird auf synthetischen Prompts im realen Format des Projekts. Die
Anzahl der Familien steuert, wie viele Prompts sich eine Basis teilen - der
entscheidende Parameter fuer die Selektivitaet des invertierten Index.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.similarity import (  # noqa: E402
    PromptVector,
    SimilarityEngine,
    idf_from_counts,
)
from app.analysis.clustering import build_clusters  # noqa: E402


def build_corpus(
    prompts: int, families: int, base_size: int, variable_size: int, seed: int
) -> tuple[list[PromptVector], dict[int, float]]:
    """Erzeugt Segmentmengen mit realistischer Struktur.

    Jede Familie hat eine gemeinsame Basis; jedes Mitglied ergaenzt eigene
    Segmente. Dazu kommt ein bibliotheksweiter Bausteinvorrat, den alle teilen -
    genau das Muster, das die Prompts dieses Projekts zeigen.
    """
    rng = random.Random(seed)
    boilerplate = list(range(1000))
    family_bases = {
        f: [10_000 + f * 100 + i for i in range(base_size)] for f in range(families)
    }

    vectors: list[PromptVector] = []
    counts: dict[int, int] = {}
    for index in range(prompts):
        family = index % families
        segments = set(rng.sample(boilerplate, min(8, len(boilerplate))))
        segments.update(family_bases[family])
        segments.update(
            1_000_000 + index * 100 + i for i in range(rng.randint(1, variable_size))
        )
        vectors.append(PromptVector(index, frozenset(segments)))
        for segment in segments:
            counts[segment] = counts.get(segment, 0) + 1

    idf = {
        segment: idf_from_counts(count, prompts) for segment, count in counts.items()
    }
    return vectors, idf


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", type=int, default=5000)
    parser.add_argument("--families", type=int, default=200)
    parser.add_argument("--base-size", type=int, default=25)
    parser.add_argument("--variable-size", type=int, default=6)
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--cluster-threshold", type=float, default=0.7)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    vectors, idf = build_corpus(
        args.prompts, args.families, args.base_size, args.variable_size, args.seed
    )
    build_time = time.perf_counter() - started

    engine = SimilarityEngine(idf, threshold=args.threshold, top_k=args.top_k)
    started = time.perf_counter()
    neighbours, stats = engine.build(vectors)
    similarity_time = time.perf_counter() - started

    started = time.perf_counter()
    groups, cluster_stats = build_clusters(neighbours, args.cluster_threshold)
    cluster_time = time.perf_counter() - started

    all_pairs = args.prompts * (args.prompts - 1) // 2
    saved = 100.0 * (1 - stats.comparisons / all_pairs) if all_pairs else 0.0

    print("Korpus")
    print("  Prompts          :", args.prompts)
    print("  Familien (Vorgabe):", args.families)
    print("  Aufbau           :", format(build_time, ".2f"), "s")
    print()
    print("Aehnlichkeit")
    print("  Vergleiche       :", stats.comparisons)
    print("  alle Paare waeren:", all_pairs)
    print("  eingespart       :", format(saved, ".1f"), "%")
    print("  Kanten           :", stats.edges)
    print("  Dauer            :", format(similarity_time, ".2f"), "s")
    if similarity_time > 0:
        print("  Durchsatz        :", format(args.prompts / similarity_time, ".0f"), "Prompts/s")
    print()
    print("Familien")
    print("  gefunden         :", cluster_stats.clusters)
    print("  groesste         :", cluster_stats.largest)
    print("  zugeordnet       :", cluster_stats.clustered_prompts)
    print("  Dauer            :", format(cluster_time, ".3f"), "s")

    if cluster_stats.largest > args.prompts * 0.5:
        print()
        print("  WARNUNG: eine Familie umfasst mehr als die Haelfte der Bibliothek.")
        print("  Der Cluster-Schwellwert ist zu niedrig - Union-Find verkettet dann")
        print("  ueber Zwischenglieder alles miteinander.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
