"""Tests der Analysis Engine: Aehnlichkeit, Familien, Varianten."""

from __future__ import annotations

import pytest

from app.analysis.clustering import (
    UnionFind,
    base_signature,
    build_clusters,
    pick_representative,
)
from app.analysis.similarity import (
    MIN_POSTING_FLOOR,
    Neighbour,
    PromptVector,
    SimilarityEngine,
    build_vectors,
    idf_from_counts,
)
from app.analysis.variants import (
    DEFAULT_CHANGE_THRESHOLD,
    build_variant,
    detect_changes,
    words_from_texts,
)
from app.core.cancellation import CancellationToken


def uniform_idf(*segments: int, value: float = 1.0) -> dict[int, float]:
    return {segment: value for segment in segments}


class TestSimilarityEngine:
    def test_identical_sets_score_one(self) -> None:
        idf = uniform_idf(1, 2, 3)
        engine = SimilarityEngine(idf, threshold=0.1)
        a = PromptVector(1, frozenset({1, 2, 3}))
        b = PromptVector(2, frozenset({1, 2, 3}))
        assert engine.similarity_of(a, b) == pytest.approx(1.0)

    def test_disjoint_sets_score_zero(self) -> None:
        engine = SimilarityEngine(uniform_idf(1, 2, 3, 4))
        a = PromptVector(1, frozenset({1, 2}))
        b = PromptVector(2, frozenset({3, 4}))
        assert engine.similarity_of(a, b) == 0.0

    def test_rare_segments_weigh_more(self) -> None:
        """Ein gemeinsamer seltener Satz sagt mehr aus als ein Textbaustein."""
        common_shared = SimilarityEngine({1: 0.1, 9: 5.0, 8: 5.0})
        rare_shared = SimilarityEngine({1: 5.0, 9: 0.1, 8: 0.1})

        a = PromptVector(1, frozenset({1, 9}))
        b = PromptVector(2, frozenset({1, 8}))

        assert rare_shared.similarity_of(a, b) > common_shared.similarity_of(a, b)

    def test_difference_reports_all_three_parts(self) -> None:
        engine = SimilarityEngine(uniform_idf(1, 2, 3, 4))
        a = PromptVector(1, frozenset({1, 2, 3}))
        b = PromptVector(2, frozenset({2, 3, 4}))
        common, only_left, only_right = engine.difference(a, b)
        assert common == {2, 3}
        assert only_left == {1}
        assert only_right == {4}

    def test_top_k_limits_the_result(self) -> None:
        idf = uniform_idf(*range(20))
        vectors = [PromptVector(i, frozenset({0, 1, 2, i + 10})) for i in range(8)]
        engine = SimilarityEngine(idf, threshold=0.1, top_k=3)
        neighbours, _stats = engine.build(vectors)
        assert all(len(entries) <= 3 for entries in neighbours.values())

    def test_threshold_filters_weak_edges(self) -> None:
        idf = uniform_idf(*range(20))
        vectors = [PromptVector(i, frozenset({0, i + 10})) for i in range(5)]
        strict = SimilarityEngine(idf, threshold=0.9, top_k=5)
        loose = SimilarityEngine(idf, threshold=0.1, top_k=5)
        assert not strict.build(vectors)[0]
        assert loose.build(vectors)[0]

    def test_neighbours_are_ordered_by_similarity(self) -> None:
        idf = uniform_idf(*range(30))
        vectors = [
            PromptVector(1, frozenset({1, 2, 3, 4})),
            PromptVector(2, frozenset({1, 2, 3, 4})),      # identisch
            PromptVector(3, frozenset({1, 2, 3, 20})),     # dicht dran
            PromptVector(4, frozenset({1, 21, 22, 23})),   # entfernt
        ]
        neighbours, _ = SimilarityEngine(idf, threshold=0.05, top_k=5).build(vectors)
        scores = [n.similarity for n in neighbours[1]]
        assert scores == sorted(scores, reverse=True)
        assert neighbours[1][0].key == 2

    def test_candidate_generation_avoids_all_pairs(self) -> None:
        """Der eigentliche Zweck des invertierten Index."""
        # Zehn voellig getrennte Gruppen zu je fuenf Prompts.
        vectors = []
        idf = {}
        for group in range(10):
            for member in range(5):
                key = group * 10 + member
                segments = {1000 + group * 10 + i for i in range(4)}
                vectors.append(PromptVector(key, frozenset(segments)))
                for segment in segments:
                    idf[segment] = 2.0

        engine = SimilarityEngine(idf, threshold=0.5, top_k=10)
        _neighbours, stats = engine.build(vectors)
        all_pairs = len(vectors) * (len(vectors) - 1)
        assert stats.comparisons < all_pairs * 0.3, (
            "Kandidatenauswahl muss den Grossteil der Paare vermeiden"
        )

    def test_small_library_indexes_everything(self) -> None:
        """Regression: die Verhaeltnisregel war bei kleinen Bibliotheken schaedlich.

        Bei fuenf Dokumenten lag das Posting-Limit bei zwei - damit fielen genau
        die gemeinsamen Saetze aus dem Index, die eine Familie ausmachen, und es
        entstanden gar keine Kandidaten.
        """
        idf = {1: 0.4, 2: 0.4, 3: 0.4, 10: 1.8, 11: 1.8}
        vectors = [
            PromptVector(1, frozenset({1, 2, 3, 10})),
            PromptVector(2, frozenset({1, 2, 3, 10})),
            PromptVector(3, frozenset({1, 2, 3, 11})),
            PromptVector(4, frozenset({1, 2, 3, 11})),
            PromptVector(5, frozenset({1, 2, 3})),
        ]
        neighbours, stats = SimilarityEngine(idf, threshold=0.3, top_k=5).build(vectors)
        assert stats.comparisons > 0
        assert len(neighbours) == 5, "jeder Prompt muss Nachbarn finden"

    def test_min_posting_floor_is_generous(self) -> None:
        assert MIN_POSTING_FLOOR >= 100

    def test_empty_vectors_are_skipped(self) -> None:
        vectors = [
            PromptVector(1, frozenset()),
            PromptVector(2, frozenset({1})),
            PromptVector(3, frozenset({1})),
        ]
        neighbours, stats = SimilarityEngine({1: 1.0}, threshold=0.1).build(vectors)
        assert stats.skipped_empty == 1
        assert 1 not in neighbours

    def test_cancellation_stops_early(self) -> None:
        idf = uniform_idf(*range(200))
        vectors = [PromptVector(i, frozenset({0, i + 1})) for i in range(200)]
        token = CancellationToken()
        token.cancel()
        neighbours, _stats = SimilarityEngine(idf, threshold=0.1).build(vectors, token)
        assert neighbours == {}

    def test_single_prompt_yields_nothing(self) -> None:
        neighbours, stats = SimilarityEngine({1: 1.0}).build(
            [PromptVector(1, frozenset({1}))]
        )
        assert neighbours == {}
        assert stats.prompts == 1

    def test_build_vectors_groups_rows(self) -> None:
        vectors = {v.key: v.segments for v in build_vectors([(1, 10), (1, 11), (2, 10)])}
        assert vectors[1] == {10, 11}
        assert vectors[2] == {10}

    def test_idf_formula(self) -> None:
        assert idf_from_counts(100, 100) == pytest.approx(0.0)
        assert idf_from_counts(1, 100) > idf_from_counts(50, 100)
        assert idf_from_counts(0, 100) > 0, "Division durch Null muss abgefangen sein"


class TestUnionFind:
    def test_transitive_grouping(self) -> None:
        union = UnionFind()
        union.union(1, 2)
        union.union(2, 3)
        union.union(10, 11)
        groups = {frozenset(members) for members in union.groups().values()}
        assert groups == {frozenset({1, 2, 3}), frozenset({10, 11})}

    def test_repeated_union_is_idempotent(self) -> None:
        union = UnionFind()
        assert union.union(1, 2) is True
        assert union.union(1, 2) is False

    def test_isolated_item_is_its_own_group(self) -> None:
        union = UnionFind()
        union.add(7)
        assert union.groups() == {7: [7]}


class TestClustering:
    def _neighbours(self):
        return {
            1: [Neighbour(2, 0.9), Neighbour(3, 0.8)],
            2: [Neighbour(1, 0.9)],
            3: [Neighbour(1, 0.8)],
            10: [Neighbour(11, 0.95)],
            11: [Neighbour(10, 0.95)],
            50: [Neighbour(51, 0.2)],  # unter jedem Schwellwert
        }

    def test_groups_form_above_the_threshold(self) -> None:
        groups, stats = build_clusters(self._neighbours(), threshold=0.5)
        as_sets = {frozenset(g) for g in groups}
        assert as_sets == {frozenset({1, 2, 3}), frozenset({10, 11})}
        assert stats.clusters == 2
        assert stats.largest == 3

    def test_weak_edges_do_not_group(self) -> None:
        groups, _stats = build_clusters(self._neighbours(), threshold=0.99)
        assert groups == []

    def test_chaining_merges_everything_when_threshold_is_too_low(self) -> None:
        """Der bekannte Nachteil von Union-Find, hier bewusst festgehalten.

        Eine Kette A-B-C-D verbindet A und D, auch wenn sie sich nicht aehneln.
        Deshalb ist der Cluster-Schwellwert der empfindlichste Regler des
        Systems; zu niedrig gesetzt entsteht eine einzige nutzlose Familie.
        """
        chain = {i: [Neighbour(i + 1, 0.5)] for i in range(20)}
        groups, stats = build_clusters(chain, threshold=0.4)
        assert stats.clusters == 1
        assert stats.largest == 21

    def test_base_signature_uses_family_frequency(self) -> None:
        """Ein Satz kann bibliotheksweit selten und in der Familie ueberall sein."""
        segments = {
            1: frozenset({100, 101, 200}),
            2: frozenset({100, 101, 201}),
            3: frozenset({100, 101, 202}),
        }
        signature = base_signature([1, 2, 3], segments, min_commonness=0.9)
        assert signature == {100, 101}

    def test_base_signature_threshold_matters(self) -> None:
        segments = {
            1: frozenset({100, 200}),
            2: frozenset({100, 200}),
            3: frozenset({100}),
        }
        assert base_signature([1, 2, 3], segments, 0.6) == {100, 200}
        assert base_signature([1, 2, 3], segments, 1.0) == {100}

    def test_representative_is_closest_to_the_base(self) -> None:
        """Spezifikation 03 verlangt einen realen Prompt als Basis.

        Die aus der Schnittmenge gebildete Signatur existiert in der Regel als
        kein Prompt; der Repraesentant ist ihr naechstes reales Gegenstueck.
        """
        segments = {
            1: frozenset({100, 101}),          # genau die Basis
            2: frozenset({100, 101, 300, 301, 302}),
            3: frozenset({100}),
        }
        signature = frozenset({100, 101})
        representative, scores = pick_representative([1, 2, 3], segments, signature)
        assert representative == 1
        # Der gespeicherte Wert ist Jaccard zur Basis - eine andere Frage als
        # die Auswahl: Prompt 3 liegt naeher, deckt die Basis aber nur halb ab.
        assert scores[1] == pytest.approx(1.0)
        assert scores[2] == pytest.approx(0.4)
        assert scores[3] == pytest.approx(0.5)

    def test_full_base_beats_partial_base(self) -> None:
        """Der Basisprompt muss die Basis enthalten, nicht nur nah dran sein.

        Jaccard allein wuerde hier den Prompt mit der halben Basis waehlen; jedes
        Geschwister saehe dann so aus, als haette es den fehlenden Satz ergaenzt.
        """
        segments = {
            1: frozenset({100}),                          # halbe Basis, wenig Ballast
            2: frozenset({100, 101, 300, 301, 302, 303}),  # volle Basis, viel Ballast
        }
        representative, _scores = pick_representative(
            [1, 2], segments, frozenset({100, 101})
        )
        assert representative == 2

    def test_fewer_extras_wins_on_equal_coverage(self) -> None:
        segments = {
            1: frozenset({100, 101, 500, 501}),
            2: frozenset({100, 101, 500}),
        }
        representative, _scores = pick_representative(
            [1, 2], segments, frozenset({100, 101})
        )
        assert representative == 2

    def test_representative_is_reproducible_on_ties(self) -> None:
        segments = {5: frozenset({1}), 3: frozenset({1}), 9: frozenset({1})}
        signature = frozenset({1})
        first, _ = pick_representative([5, 3, 9], segments, signature)
        second, _ = pick_representative([9, 5, 3], segments, signature)
        assert first == second == 3

    def test_empty_members(self) -> None:
        assert base_signature([], {}, 0.5) == frozenset()
        assert pick_representative([], {}, frozenset())[0] == 0


class TestVariants:
    def _words(self):
        return words_from_texts(
            {
                10: "warm golden sunlight dominates the scene",
                11: "cold blue moonlight dominates the scene",
                12: "she wears a sleek avant garde crown",
                13: "maintain the makeup and add smokey eyes",
                14: "maintain the makeup and add natural eyes",
            }
        )

    def test_dice_pairs_a_real_change(self) -> None:
        """Der Grund fuer Dice statt Jaccard.

        "warm golden sunlight dominates the scene" gegen "cold blue moonlight
        dominates the scene" ergibt Jaccard 0.33 und Dice 0.50. Bei Schwelle 0.4
        verfehlt Jaccard genau die Aenderung, um die es geht.
        """
        changes, removed, added = detect_changes({10}, {11}, self._words())
        assert len(changes) == 1
        assert changes[0].removed == 10 and changes[0].added == 11
        assert changes[0].overlap == pytest.approx(0.5, abs=0.01)
        assert not removed and not added

    def test_unrelated_segments_are_not_paired(self) -> None:
        changes, removed, added = detect_changes({10}, {12}, self._words())
        assert changes == []
        assert removed == {10} and added == {12}

    def test_each_segment_is_paired_at_most_once(self) -> None:
        changes, _removed, added = detect_changes({10, 13}, {11, 14, 12}, self._words())
        assert len(changes) == 2
        assert {c.removed for c in changes} == {10, 13}
        assert {c.added for c in changes} == {11, 14}
        assert added == {12}, "das uebrige Segment bleibt eine Ergaenzung"

    def test_greedy_order_prefers_the_best_match(self) -> None:
        changes, _r, _a = detect_changes({10, 13}, {11, 14}, self._words())
        assert changes[0].overlap >= changes[1].overlap

    def test_variant_without_words_reports_add_and_remove(self) -> None:
        diff = build_variant(1, 2, frozenset({1, 11}), frozenset({1, 10}), 0.7)
        assert diff.changed_count == 0
        assert diff.added == (11,) and diff.removed == (10,)

    def test_variant_with_words_reports_a_change(self) -> None:
        diff = build_variant(1, 2, frozenset({1, 11}), frozenset({1, 10}), 0.7, self._words())
        assert diff.changed_count == 1
        assert diff.added == () and diff.removed == ()
        assert diff.summary() == "1 geaendert"

    def test_identical_prompt_has_no_diff(self) -> None:
        diff = build_variant(1, 2, frozenset({1, 2}), frozenset({1, 2}), 1.0)
        assert diff.is_identical
        assert diff.summary() == "unveraendert"

    def test_json_is_complete(self) -> None:
        import json

        diff = build_variant(
            1, 2, frozenset({1, 11, 12}), frozenset({1, 10}), 0.7, self._words()
        )
        payload = json.loads(diff.to_json())
        assert payload["common"] == [1]
        assert payload["added"] == [12]
        assert len(payload["changed"]) == 1
        assert payload["changed"][0]["r"] == 10

    def test_threshold_is_configurable(self) -> None:
        assert DEFAULT_CHANGE_THRESHOLD == pytest.approx(0.4)
        strict, _r, _a = detect_changes({10}, {11}, self._words(), threshold=0.9)
        assert strict == []
