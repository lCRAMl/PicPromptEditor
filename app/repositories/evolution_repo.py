"""Datenbankzugriff auf evolution_cache.

Spezifikation 06 ist hier ungewoehnlich deutlich: ein Evolution-Tab darf beim
Oeffnen NIEMALS neu rechnen. Liegt ein gueltiger Eintrag vor, wird er sofort
geladen; neu gerechnet wird nur bei fehlendem Eintrag, gesetztem dirty-Flag oder
auf ausdrueckliche Anforderung.

Genau darum ist tree_json eine fertige Struktur und keine Sammlung von Kanten,
die die Oberflaeche erst zusammensetzen muesste. Ein Tab-Wechsel soll eine
Abfrage kosten, nicht einen Baumaufbau.

algo_version dient der Massen-Invalidierung: aendert sich das Verfahren, macht
ein Versionssprung alle Baeume ungueltig, ohne dass Zeilen angefasst werden.
"""

from __future__ import annotations

from typing import Sequence

from app.analysis.evolution import EvolutionInput, EvolutionTree
from app.database.connection import Database
from app.database.writer import DbWriter

_CHUNK = 400


class EvolutionRepository:
    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    # Laden --------------------------------------------------------------------

    def tree_for_cluster(self, cluster_id: int, algo_version: int) -> EvolutionTree | None:
        """Fertiger Baum aus dem Cache. None heisst: muss gerechnet werden."""
        row = self._db.query_one(
            "SELECT tree_json FROM evolution_cache"
            " WHERE cluster_id = ? AND dirty = 0 AND algo_version = ?",
            (cluster_id, algo_version),
        )
        if row is None:
            return None
        try:
            return EvolutionTree.from_json(row["tree_json"])
        except (ValueError, KeyError):
            # Ein unbrauchbarer Eintrag ist kein Grund zu scheitern - er wird
            # behandelt, als fehlte er, und beim naechsten Lauf neu gebaut.
            return None

    def available_clusters(
        self, algo_version: int, limit: int = 200
    ) -> list[dict[str, int]]:
        """Familien mit fertigem Baum, die groesste zuerst.

        Nur was tatsaechlich im Cache liegt - eine Auswahlliste, die Familien
        anbietet, deren Baum erst gerechnet werden muesste, fuehrt beim
        Umschalten ins Leere. `dirty = 0` und die Algorithmusversion sind
        dieselben Bedingungen wie beim Lesen des Baums.
        """
        rows = self._db.query(
            "SELECT e.cluster_id AS cluster_id, e.node_count AS node_count,"
            "       c.image_count AS image_count"
            " FROM evolution_cache e"
            " JOIN prompt_clusters c ON c.id = e.cluster_id"
            " WHERE e.dirty = 0 AND e.algo_version = ?"
            " ORDER BY e.node_count DESC, e.cluster_id ASC LIMIT ?",
            (algo_version, limit),
        )
        return [
            {
                "cluster_id": int(r["cluster_id"]),
                "node_count": int(r["node_count"] or 0),
                "image_count": int(r["image_count"] or 0),
            }
            for r in rows
        ]

    def cluster_for_prompt(self, prompt_id: int) -> int | None:
        return self._db.scalar(
            "SELECT cluster_id FROM cluster_members WHERE prompt_id = ?", (prompt_id,)
        )

    def cluster_for_image(self, image_id: int) -> int | None:
        return self._db.scalar(
            "SELECT m.cluster_id FROM images i"
            " JOIN cluster_members m ON m.prompt_id = i.prompt_id"
            " WHERE i.id = ?",
            (image_id,),
        )

    def has_prompt(self, image_id: int) -> bool:
        """Hat dieses Bild ueberhaupt eine Promptdatei?

        Der weitaus haeufigste Grund fuer einen fehlenden Baum. In einer
        Bibliothek aus Bildersammlungen ist die Mehrzahl der Bilder ohne
        zugehoerige Textdatei - dann fehlt nicht der Baum, sondern die
        Grundlage.
        """
        return bool(
            self._db.scalar(
                "SELECT 1 FROM images WHERE id = ? AND prompt_id IS NOT NULL",
                (image_id,),
                default=0,
            )
        )

    def members_for_cluster(self, cluster_id: int) -> list[EvolutionInput]:
        """Alle Mitglieder samt Zeitstempel, Segmentmenge und Vorschaubild.

        In einer Abfrage, weil der Baumbau ohnehin alles braucht und ein Zugriff
        je Mitglied bei grossen Familien hunderte Abfragen waeren.
        """
        rows = self._db.query(
            "SELECT p.id, p.modified_time, p.prompt_text,"
            " (SELECT i.id FROM images i WHERE i.prompt_id = p.id AND i.is_present = 1"
            "  ORDER BY i.filename LIMIT 1) AS image_id,"
            " (SELECT COUNT(*) FROM images i WHERE i.prompt_id = p.id AND i.is_present = 1)"
            "  AS image_count"
            " FROM cluster_members m JOIN prompts p ON p.id = m.prompt_id"
            " WHERE m.cluster_id = ? AND p.is_present = 1",
            (cluster_id,),
        )
        if not rows:
            return []

        segments = self._segments_for([r["id"] for r in rows])
        return [
            EvolutionInput(
                prompt_id=r["id"],
                modified_time=int(r["modified_time"] or 0),
                segments=segments.get(r["id"], frozenset()),
                label=_short_label(r["prompt_text"]),
                image_id=r["image_id"],
                image_count=int(r["image_count"] or 0),
            )
            for r in rows
        ]

    def _segments_for(self, prompt_ids: Sequence[int]) -> dict[int, frozenset[int]]:
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
        return {key: frozenset(value) for key, value in grouped.items()}

    def segment_texts(self, token_ids: Sequence[int]) -> dict[int, str]:
        """Segmenttexte fuer die Kantenbeschriftung im Baum."""
        if not token_ids:
            return {}
        found: dict[int, str] = {}
        unique = list(dict.fromkeys(token_ids))
        for start in range(0, len(unique), _CHUNK):
            chunk = unique[start : start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self._db.query(
                "SELECT id, token FROM tokens WHERE id IN (" + placeholders + ")",
                tuple(chunk),
            )
            found.update({r["id"]: r["token"] for r in rows})
        return found

    def images_for_prompt(self, prompt_id: int) -> list[int]:
        rows = self._db.query(
            "SELECT id FROM images WHERE prompt_id = ? AND is_present = 1 ORDER BY filename",
            (prompt_id,),
        )
        return [r["id"] for r in rows]

    def stale_clusters(self, algo_version: int) -> list[int]:
        """Familien ohne gueltigen Baum - der Arbeitsvorrat des Jobs."""
        rows = self._db.query(
            "SELECT c.id FROM prompt_clusters c"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM evolution_cache e"
            "   WHERE e.cluster_id = c.id AND e.dirty = 0 AND e.algo_version = ?)"
            " ORDER BY c.member_count DESC",
            (algo_version,),
        )
        return [r["id"] for r in rows]

    def counts(self) -> dict[str, int]:
        return {
            "trees": int(
                self._db.scalar("SELECT COUNT(*) FROM evolution_cache", default=0) or 0
            ),
            "dirty": int(
                self._db.scalar(
                    "SELECT COUNT(*) FROM evolution_cache WHERE dirty = 1", default=0
                )
                or 0
            ),
        }

    # Schreiben ----------------------------------------------------------------

    def store(self, trees: Sequence[EvolutionTree], algo_version: int) -> int:
        if not trees:
            return 0
        rows = [
            (
                tree.cluster_id,
                tree.root_id,
                tree.to_json(),
                tree.size,
                algo_version,
            )
            for tree in trees
        ]
        self._writer.executemany(
            "INSERT INTO evolution_cache"
            " (cluster_id, base_prompt_id, tree_json, node_count, algo_version,"
            "  generated_at, dirty)"
            " VALUES (?, ?, ?, ?, ?, unixepoch('subsec'), 0)"
            " ON CONFLICT(cluster_id) DO UPDATE SET"
            " base_prompt_id = excluded.base_prompt_id,"
            " tree_json = excluded.tree_json,"
            " node_count = excluded.node_count,"
            " algo_version = excluded.algo_version,"
            " generated_at = excluded.generated_at,"
            " dirty = 0",
            rows,
            label="evolution.store",
        ).result(timeout=300.0)
        return len(rows)

    def mark_dirty(self, cluster_ids: Sequence[int]) -> None:
        if not cluster_ids:
            return
        self._writer.executemany(
            "UPDATE evolution_cache SET dirty = 1 WHERE cluster_id = ?",
            [(cid,) for cid in cluster_ids],
            label="evolution.dirty",
        )

    def mark_all_dirty(self) -> None:
        """Nach einer Neuberechnung der Familien sind alle Baeume ueberholt."""
        self._writer.execute(
            "UPDATE evolution_cache SET dirty = 1", label="evolution.dirty_all"
        ).result(timeout=60.0)

    def clear(self) -> None:
        self._writer.execute(
            "DELETE FROM evolution_cache", label="evolution.clear"
        ).result(timeout=60.0)


def _short_label(text: str | None, limit: int = 90) -> str:
    """Erste aussagekraeftige Zeile eines Prompts als Knotenbeschriftung."""
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) > 3:
            return stripped if len(stripped) <= limit else stripped[: limit - 1] + "…"
    return ""
