"""Datenbankzugriff auf prompt_neighbors, prompt_clusters, cluster_members, prompt_variants.

Eine Entscheidung praegt dieses Repository: gerechnet wird auf REPRAESENTANTEN,
gespeichert wird fuer ALLE Prompts.

Prompts mit identischem Text sind fuer die Aehnlichkeit ununterscheidbar. Je
Textvariante wird deshalb ein Repraesentant bestimmt (die kleinste Prompt-Id)
und nur dieser verglichen. Bei einer Bibliothek mit dreihundert Prompts aus zwei
Textvarianten sind das zwei Vergleichsobjekte statt dreihundert.

Die Ergebnisse werden anschliessend auf alle Prompts ausgedehnt, damit die
Oberflaeche fuer jedes Bild eine Familie und eine Variante zeigen kann - sonst
haette ein Bild, dessen Prompt zufaellig doppelt vorkommt, keine Zuordnung.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Sequence

from app.analysis.similarity import PromptVector
from app.analysis.variants import VariantDiff
from app.database.connection import Database
from app.database.writer import DbWriter

_CHUNK = 400


class ClusterRepository:
    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    # Laden --------------------------------------------------------------------

    def representatives(self) -> dict[int, list[int]]:
        """repraesentativer prompt_id -> alle Prompts mit demselben Text."""
        rows = self._db.query(
            "SELECT id, text_hash FROM prompts"
            " WHERE is_present = 1 AND parse_version > 0 ORDER BY id"
        )
        by_hash: dict[str, list[int]] = {}
        for row in rows:
            by_hash.setdefault(row["text_hash"], []).append(row["id"])
        return {members[0]: members for members in by_hash.values() if members}

    def vectors_for(self, prompt_ids: Sequence[int]) -> list[PromptVector]:
        grouped: dict[int, set[int]] = {pid: set() for pid in prompt_ids}
        for start in range(0, len(prompt_ids), _CHUNK):
            chunk = prompt_ids[start : start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self._db.query(
                "SELECT prompt_id, token_id FROM prompt_tokens"
                " WHERE prompt_id IN (" + placeholders + ")",
                tuple(chunk),
            )
            for row in rows:
                grouped[row["prompt_id"]].add(row["token_id"])
        return [PromptVector(key, frozenset(values)) for key, values in grouped.items()]

    #: Geglaettetes idf fuer die Aehnlichkeit. Die View liefert ln(N/df); ein
    #: Segment in JEDEM Prompt bekommt damit exakt 0 und traegt zur Aehnlichkeit
    #: nichts bei. In einer homogenen Bibliothek - genau dem Fall, in dem
    #: Familien interessant waeren - ist dann jede Aehnlichkeit null und es
    #: entsteht keine einzige Familie.
    #:
    #: Der additive Sockel ist die uebliche Glaettung (sklearn nennt sie
    #: smooth_idf): gemeinsame Textbausteine zaehlen wenig, aber nicht nichts.
    #: Fuer die Editor-Hervorhebung bleibt es beim ungeglaetteten Wert der View -
    #: dort ist eine Null genau richtig, weil Allerweltssaetze nicht
    #: hervorgehoben werden sollen.
    IDF_FLOOR = 1.0

    def idf_map(self) -> dict[int, float]:
        return {
            r["token_id"]: float(r["idf"] or 0.0) + self.IDF_FLOOR
            for r in self._db.query("SELECT token_id, idf FROM token_stats")
        }

    def segment_words(self) -> dict[int, frozenset[str]]:
        """Wortmengen je Segment - Grundlage der Aenderungserkennung."""
        return {
            r["id"]: frozenset(str(r["token"]).split())
            for r in self._db.query("SELECT id, token FROM tokens")
        }

    def segment_text_map(self) -> dict[int, str]:
        """token_id -> Normalform. Fuer Aenderungserkennung und Knotenbeschriftung."""
        return {
            r["id"]: str(r["token"])
            for r in self._db.query("SELECT id, token FROM tokens")
        }

    def pending_count(self, algo_version: int) -> int:
        """Prompts, die geparst, aber noch nicht in einer Familie sind."""
        return int(
            self._db.scalar(
                "SELECT COUNT(*) FROM prompts p"
                " WHERE p.is_present = 1 AND p.parse_version > 0"
                " AND NOT EXISTS ("
                "   SELECT 1 FROM cluster_members m"
                "   JOIN prompt_clusters c ON c.id = m.cluster_id"
                "   WHERE m.prompt_id = p.id AND c.algo_version = ?)",
                (algo_version,),
                default=0,
            )
            or 0
        )

    # Schreiben ----------------------------------------------------------------

    def replace_all(
        self,
        neighbours: dict[int, list],
        clusters: Sequence,
        variants: Sequence[VariantDiff],
        expansion: dict[int, list[int]],
        algo_version: int,
    ) -> dict[str, int]:
        """Ersetzt saemtliche Analyseergebnisse in einer Transaktion.

        Bewusst ein vollstaendiger Austausch statt einer Differenzbildung:
        Cluster sind globale Strukturen, ein neuer Prompt kann zwei bestehende
        Familien verbinden. Ein Teilupdate muesste diese Faelle einzeln
        behandeln und waere die haeufigste Quelle inkonsistenter Anzeigen.
        Der Austausch ist bezahlbar, weil er nur auf Repraesentanten arbeitet.
        """

        def run(conn: sqlite3.Connection) -> dict[str, int]:
            conn.execute("DELETE FROM prompt_variants")
            conn.execute("DELETE FROM cluster_members")
            conn.execute("DELETE FROM prompt_clusters")
            conn.execute("DELETE FROM prompt_neighbors")

            written = {"neighbours": 0, "clusters": 0, "members": 0, "variants": 0}

            neighbour_rows = []
            for key, entries in neighbours.items():
                for prompt_id in expansion.get(key, [key]):
                    rank = 0
                    # Textgleiche Prompts sind sich selbst die naechsten Nachbarn.
                    for twin in expansion.get(key, []):
                        if twin != prompt_id:
                            neighbour_rows.append((prompt_id, rank, twin, 1.0))
                            rank += 1
                    for entry in entries:
                        for target in expansion.get(entry.key, [entry.key]):
                            neighbour_rows.append(
                                (prompt_id, rank, target, round(entry.similarity, 6))
                            )
                            rank += 1
            if neighbour_rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO prompt_neighbors"
                    " (prompt_id, rank, neighbor_id, similarity) VALUES (?, ?, ?, ?)",
                    neighbour_rows,
                )
                written["neighbours"] = len(neighbour_rows)

            cluster_ids: dict[int, int] = {}
            for cluster in clusters:
                expanded = [
                    prompt_id
                    for member in cluster.members
                    for prompt_id in expansion.get(member, [member])
                ]
                image_count = _count_images(conn, expanded)
                cursor = conn.execute(
                    "INSERT INTO prompt_clusters"
                    " (representative_prompt_id, base_signature, member_count,"
                    "  image_count, avg_similarity, algo_version, generated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, unixepoch('subsec'))",
                    (
                        cluster.representative,
                        _signature_json(cluster.base_signature),
                        len(expanded),
                        image_count,
                        cluster.average_similarity,
                        algo_version,
                    ),
                )
                cluster_id = int(cursor.lastrowid)
                cluster_ids[cluster.representative] = cluster_id
                written["clusters"] += 1

                member_rows = [
                    (
                        cluster_id,
                        prompt_id,
                        cluster.similarity_to_base.get(member, 0.0),
                    )
                    for member in cluster.members
                    for prompt_id in expansion.get(member, [member])
                ]
                conn.executemany(
                    "INSERT OR REPLACE INTO cluster_members"
                    " (cluster_id, prompt_id, similarity_to_base) VALUES (?, ?, ?)",
                    member_rows,
                )
                written["members"] += len(member_rows)

            variant_rows = [
                (
                    diff.base_prompt_id,
                    diff.prompt_id,
                    cluster_ids.get(diff.base_prompt_id),
                    round(diff.similarity, 6),
                    diff.added_count,
                    diff.removed_count,
                    diff.changed_count,
                    diff.to_json(),
                    algo_version,
                )
                for diff in variants
                if diff.base_prompt_id != diff.prompt_id
            ]
            if variant_rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO prompt_variants"
                    " (base_prompt_id, variant_prompt_id, cluster_id, similarity,"
                    "  added_count, removed_count, changed_count, diff_json,"
                    "  algo_version, generated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch('subsec'))",
                    variant_rows,
                )
                written["variants"] = len(variant_rows)

            conn.execute(
                "INSERT INTO library_stats(key, int_value, updated_at)"
                " VALUES ('cluster_count', ?, unixepoch('subsec'))"
                " ON CONFLICT(key) DO UPDATE SET int_value = excluded.int_value,"
                " updated_at = excluded.updated_at",
                (written["clusters"],),
            )
            return written

        return dict(self._writer.submit(run, label="clusters.replace").result(timeout=600.0))

    # Lesen --------------------------------------------------------------------

    def neighbours_of(self, prompt_id: int, limit: int = 20) -> list[tuple[int, float]]:
        rows = self._db.query(
            "SELECT neighbor_id, similarity FROM prompt_neighbors"
            " WHERE prompt_id = ? ORDER BY rank LIMIT ?",
            (prompt_id, limit),
        )
        return [(r["neighbor_id"], r["similarity"]) for r in rows]

    def similar_images(self, prompt_id: int, limit: int = 500) -> list[int]:
        """Bilder aehnlicher Prompts, nach Aehnlichkeit sortiert."""
        rows = self._db.query(
            "SELECT i.id FROM prompt_neighbors n"
            " JOIN images i ON i.prompt_id = n.neighbor_id"
            " WHERE n.prompt_id = ? AND i.is_present = 1"
            " ORDER BY n.rank LIMIT ?",
            (prompt_id, limit),
        )
        return [r["id"] for r in rows]

    def cluster_of(self, prompt_id: int) -> dict[str, object] | None:
        row = self._db.query_one(
            "SELECT c.id, c.representative_prompt_id, c.member_count, c.image_count,"
            " c.avg_similarity, m.similarity_to_base"
            " FROM cluster_members m JOIN prompt_clusters c ON c.id = m.cluster_id"
            " WHERE m.prompt_id = ?",
            (prompt_id,),
        )
        return dict(row) if row is not None else None

    def cluster_images(self, cluster_id: int, limit: int = 5000) -> list[int]:
        rows = self._db.query(
            "SELECT i.id FROM cluster_members m"
            " JOIN images i ON i.prompt_id = m.prompt_id"
            " WHERE m.cluster_id = ? AND i.is_present = 1"
            " ORDER BY m.similarity_to_base DESC, i.filename LIMIT ?",
            (cluster_id, limit),
        )
        return [r["id"] for r in rows]

    def variant_of(self, prompt_id: int) -> dict[str, object] | None:
        row = self._db.query_one(
            "SELECT base_prompt_id, similarity, added_count, removed_count,"
            " changed_count, diff_json FROM prompt_variants WHERE variant_prompt_id = ?",
            (prompt_id,),
        )
        return dict(row) if row is not None else None

    def largest_clusters(self, limit: int = 50) -> list[dict[str, object]]:
        return [
            dict(r)
            for r in self._db.query(
                "SELECT id, representative_prompt_id, member_count, image_count,"
                " avg_similarity FROM prompt_clusters"
                " ORDER BY member_count DESC, id LIMIT ?",
                (limit,),
            )
        ]

    def counts(self) -> dict[str, int]:
        return {
            "clusters": int(
                self._db.scalar("SELECT COUNT(*) FROM prompt_clusters", default=0) or 0
            ),
            "members": int(
                self._db.scalar("SELECT COUNT(*) FROM cluster_members", default=0) or 0
            ),
            "neighbours": int(
                self._db.scalar("SELECT COUNT(*) FROM prompt_neighbors", default=0) or 0
            ),
            "variants": int(
                self._db.scalar("SELECT COUNT(*) FROM prompt_variants", default=0) or 0
            ),
        }


def _signature_json(signature: Iterable[int]) -> str:
    import json

    return json.dumps(sorted(signature), separators=(",", ":"))


def _count_images(conn: sqlite3.Connection, prompt_ids: Sequence[int]) -> int:
    if not prompt_ids:
        return 0
    total = 0
    for start in range(0, len(prompt_ids), _CHUNK):
        chunk = prompt_ids[start : start + _CHUNK]
        placeholders = ",".join("?" * len(chunk))
        row = conn.execute(
            "SELECT COUNT(*) FROM images WHERE is_present = 1 AND prompt_id IN ("
            + placeholders
            + ")",
            tuple(chunk),
        ).fetchone()
        total += int(row[0]) if row else 0
    return total
