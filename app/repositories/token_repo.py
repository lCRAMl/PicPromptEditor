"""Datenbankzugriff auf tokens, prompt_tokens, prompt_sections, prompt_params.

Kern ist sync_many: es schreibt die Parseergebnisse eines Stapels von Prompts
und fuehrt token_frequency dabei INKREMENTELL fort. Spezifikation 04 schliesst
Neuberechnungen aus - bei jedem Import die Haeufigkeit aller Segmente neu zu
zaehlen waere ein Vollscan der groessten Tabelle.

Die Fortschreibung arbeitet mit Differenzen:

    alte Beitraege des Prompts abziehen -> alte Zeilen loeschen
    -> neue Zeilen schreiben -> neue Beitraege addieren

Alles in einer einzigen Schreibaufgabe, also in einem Savepoint. Ein Abbruch
mittendrin kann keine halb fortgeschriebenen Zaehler hinterlassen.

Ein Stapel statt eines Prompts je Aufruf, weil sonst je Prompt ein Dutzend
Anweisungen anfaellt. Bei zehntausend Prompts ist das der Unterschied zwischen
Sekunden und Minuten.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Sequence

from app.analysis.parsing.base import ParsedPrompt
from app.database.connection import Database
from app.database.writer import DbWriter

_CHUNK = 400


class TokenRepository:
    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    # Schreiben ----------------------------------------------------------------

    def sync_many(self, batch: Sequence[tuple[int, ParsedPrompt]]) -> int:
        """Schreibt Sektionen, Segmente und Parameter eines Stapels. Liefert die Segmentzahl."""
        if not batch:
            return 0

        def run(conn: sqlite3.Connection) -> int:
            prompt_ids = [prompt_id for prompt_id, _ in batch]
            old = self._collect_old_contributions(conn, prompt_ids)

            token_ids = self._ensure_tokens(conn, batch)
            self._delete_previous(conn, prompt_ids)
            section_ids = self._insert_sections(conn, batch)
            written = self._insert_tokens(conn, batch, token_ids, section_ids)
            self._insert_params(conn, batch, section_ids)

            new = self._collect_new_contributions(batch, token_ids)
            self._apply_frequency_delta(conn, old, new)
            self._update_prompts(conn, batch)
            return written

        return int(self._writer.submit(run, label="tokens.sync").result(timeout=300.0))

    # Teilschritte -------------------------------------------------------------

    @staticmethod
    def _collect_old_contributions(
        conn: sqlite3.Connection, prompt_ids: Sequence[int]
    ) -> dict[int, tuple[int, int, int]]:
        """token_id -> (doc_count, total_count, neg_doc_count) der bisherigen Zeilen."""
        found: dict[int, tuple[int, int, int]] = {}
        for start in range(0, len(prompt_ids), _CHUNK):
            chunk = prompt_ids[start : start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                "SELECT token_id, COUNT(DISTINCT prompt_id) AS docs, COUNT(*) AS total,"
                " COUNT(DISTINCT CASE WHEN negative = 1 THEN prompt_id END) AS negs"
                " FROM prompt_tokens WHERE prompt_id IN (" + placeholders + ")"
                " GROUP BY token_id",
                tuple(chunk),
            ).fetchall()
            for row in rows:
                previous = found.get(row["token_id"], (0, 0, 0))
                found[row["token_id"]] = (
                    previous[0] + row["docs"],
                    previous[1] + row["total"],
                    previous[2] + row["negs"],
                )
        return found

    @staticmethod
    def _ensure_tokens(
        conn: sqlite3.Connection, batch: Sequence[tuple[int, ParsedPrompt]]
    ) -> dict[tuple[str, str], int]:
        """Legt fehlende Segmente an und liefert (kind, hash) -> token_id."""
        unique: dict[tuple[str, str], tuple] = {}
        for _prompt_id, parsed in batch:
            for segment in parsed.segments:
                key = (segment.kind.value, segment.text_hash)
                if key not in unique:
                    unique[key] = (
                        segment.kind.value,
                        segment.text_norm,
                        segment.text_hash,
                        len(segment.text_norm),
                        segment.word_count,
                        segment.text_raw[:300],
                    )
        if not unique:
            return {}

        conn.executemany(
            "INSERT INTO tokens(kind, token, token_hash, char_len, word_len, sample_text)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(kind, token_hash) DO NOTHING",
            list(unique.values()),
        )

        mapping: dict[tuple[str, str], int] = {}
        hashes = [key[1] for key in unique]
        for start in range(0, len(hashes), _CHUNK):
            chunk = hashes[start : start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                "SELECT id, kind, token_hash FROM tokens WHERE token_hash IN ("
                + placeholders
                + ")",
                tuple(chunk),
            ).fetchall()
            for row in rows:
                mapping[(row["kind"], row["token_hash"])] = row["id"]
        return mapping

    @staticmethod
    def _delete_previous(conn: sqlite3.Connection, prompt_ids: Sequence[int]) -> None:
        for start in range(0, len(prompt_ids), _CHUNK):
            chunk = prompt_ids[start : start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for table in ("prompt_tokens", "prompt_params", "prompt_sections"):
                conn.execute(
                    "DELETE FROM " + table + " WHERE prompt_id IN (" + placeholders + ")",
                    tuple(chunk),
                )

    @staticmethod
    def _insert_sections(
        conn: sqlite3.Connection, batch: Sequence[tuple[int, ParsedPrompt]]
    ) -> dict[tuple[int, int], int]:
        rows = [
            (
                prompt_id,
                section.ordinal,
                section.kind.value,
                section.label,
                section.label_norm,
                int(section.negative),
                section.char_start,
                section.char_end,
            )
            for prompt_id, parsed in batch
            for section in parsed.sections
        ]
        if not rows:
            return {}
        conn.executemany(
            "INSERT INTO prompt_sections"
            " (prompt_id, ordinal, kind, label, label_norm, negative, char_start, char_end)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

        mapping: dict[tuple[int, int], int] = {}
        prompt_ids = [prompt_id for prompt_id, _ in batch]
        for start in range(0, len(prompt_ids), _CHUNK):
            chunk = prompt_ids[start : start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            found = conn.execute(
                "SELECT id, prompt_id, ordinal FROM prompt_sections"
                " WHERE prompt_id IN (" + placeholders + ")",
                tuple(chunk),
            ).fetchall()
            for row in found:
                mapping[(row["prompt_id"], row["ordinal"])] = row["id"]
        return mapping

    @staticmethod
    def _insert_tokens(
        conn: sqlite3.Connection,
        batch: Sequence[tuple[int, ParsedPrompt]],
        token_ids: dict[tuple[str, str], int],
        section_ids: dict[tuple[int, int], int],
    ) -> int:
        rows = []
        for prompt_id, parsed in batch:
            for segment in parsed.segments:
                token_id = token_ids.get((segment.kind.value, segment.text_hash))
                if token_id is None:
                    continue
                rows.append(
                    (
                        prompt_id,
                        token_id,
                        section_ids.get((prompt_id, segment.section_ordinal)),
                        segment.position,
                        segment.weight,
                        int(segment.negative),
                        segment.char_start,
                        segment.char_end,
                    )
                )
        if rows:
            conn.executemany(
                "INSERT INTO prompt_tokens"
                " (prompt_id, token_id, section_id, position, weight, negative,"
                "  char_start, char_end)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    @staticmethod
    def _insert_params(
        conn: sqlite3.Connection,
        batch: Sequence[tuple[int, ParsedPrompt]],
        section_ids: dict[tuple[int, int], int],
    ) -> None:
        rows = [
            (
                prompt_id,
                section_ids.get((prompt_id, param.section_ordinal)),
                param.key_norm,
                param.key_raw,
                param.value_text,
                param.value_num,
                param.unit,
                param.char_start,
                param.char_end,
            )
            for prompt_id, parsed in batch
            for param in parsed.params
        ]
        if rows:
            conn.executemany(
                "INSERT INTO prompt_params"
                " (prompt_id, section_id, key_norm, key_raw, value_text, value_num, unit,"
                "  char_start, char_end)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    @staticmethod
    def _collect_new_contributions(
        batch: Sequence[tuple[int, ParsedPrompt]], token_ids: dict[tuple[str, str], int]
    ) -> dict[int, tuple[int, int, int]]:
        docs: dict[int, set[int]] = defaultdict(set)
        totals: dict[int, int] = defaultdict(int)
        negatives: dict[int, set[int]] = defaultdict(set)

        for prompt_id, parsed in batch:
            for segment in parsed.segments:
                token_id = token_ids.get((segment.kind.value, segment.text_hash))
                if token_id is None:
                    continue
                docs[token_id].add(prompt_id)
                totals[token_id] += 1
                if segment.negative:
                    negatives[token_id].add(prompt_id)

        return {
            token_id: (len(prompts), totals[token_id], len(negatives.get(token_id, ())))
            for token_id, prompts in docs.items()
        }

    @staticmethod
    def _drop_unused_tokens(conn: sqlite3.Connection, token_ids: Sequence[int]) -> int:
        """Entfernt aus der uebergebenen Menge die Segmente ohne Verwendung."""
        removed = 0
        for start in range(0, len(token_ids), _CHUNK):
            chunk = token_ids[start : start + _CHUNK]
            if not chunk:
                continue
            placeholders = ",".join("?" * len(chunk))
            cur = conn.execute(
                "DELETE FROM tokens WHERE id IN (" + placeholders + ")"
                " AND NOT EXISTS (SELECT 1 FROM prompt_tokens pt WHERE pt.token_id = tokens.id)",
                tuple(chunk),
            )
            removed += cur.rowcount
        return removed

    @staticmethod
    def _apply_frequency_delta(
        conn: sqlite3.Connection,
        old: dict[int, tuple[int, int, int]],
        new: dict[int, tuple[int, int, int]],
    ) -> None:
        rows = []
        for token_id in set(old) | set(new):
            before = old.get(token_id, (0, 0, 0))
            after = new.get(token_id, (0, 0, 0))
            delta = (after[0] - before[0], after[1] - before[1], after[2] - before[2])
            if delta == (0, 0, 0):
                continue
            rows.append((token_id, *delta, *delta))

        if not rows:
            return
        conn.executemany(
            "INSERT INTO token_frequency"
            " (token_id, doc_count, total_count, neg_doc_count, updated_at)"
            " VALUES (?, MAX(0, ?), MAX(0, ?), MAX(0, ?), unixepoch('subsec'))"
            " ON CONFLICT(token_id) DO UPDATE SET"
            " doc_count = MAX(0, token_frequency.doc_count + ?),"
            " total_count = MAX(0, token_frequency.total_count + ?),"
            " neg_doc_count = MAX(0, token_frequency.neg_doc_count + ?),"
            " updated_at = unixepoch('subsec')",
            rows,
        )

    @staticmethod
    def _update_prompts(
        conn: sqlite3.Connection, batch: Sequence[tuple[int, ParsedPrompt]]
    ) -> None:
        conn.executemany(
            "UPDATE prompts SET parse_json = ?, parse_version = ?, format = ?,"
            " token_count = ?, section_count = ?, word_count = ?,"
            " updated_at = unixepoch('subsec') WHERE id = ?",
            [
                (
                    parsed.to_json(),
                    parsed.parse_version,
                    parsed.format,
                    parsed.segment_count,
                    len(parsed.sections),
                    parsed.word_count,
                    prompt_id,
                )
                for prompt_id, parsed in batch
            ],
        )

    # Lesen --------------------------------------------------------------------

    def unparsed_prompt_ids(self, parse_version: int, limit: int = 500) -> list[int]:
        """Prompts, die noch nie oder mit aelterer Version geparst wurden.

        Ein Versionssprung invalidiert damit die ganze Bibliothek, ohne dass
        Millionen Dirty-Flags geschrieben werden muessen.
        """
        rows = self._db.query(
            "SELECT id FROM prompts WHERE is_present = 1 AND parse_version < ?"
            " ORDER BY id LIMIT ?",
            (parse_version, limit),
        )
        return [r["id"] for r in rows]

    def stale_analysis_count(self) -> int:
        """Prompts, die abwesend sind, aber noch Analysezeilen halten."""
        return int(
            self._db.scalar(
                "SELECT COUNT(DISTINCT pt.prompt_id) FROM prompt_tokens pt"
                " JOIN prompts p ON p.id = pt.prompt_id WHERE p.is_present = 0",
                default=0,
            )
            or 0
        )

    def unparsed_count(self, parse_version: int) -> int:
        return int(
            self._db.scalar(
                "SELECT COUNT(*) FROM prompts WHERE is_present = 1 AND parse_version < ?",
                (parse_version,),
                default=0,
            )
            or 0
        )

    def prompt_texts(self, prompt_ids: Sequence[int]) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []
        for start in range(0, len(prompt_ids), _CHUNK):
            chunk = prompt_ids[start : start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self._db.query(
                "SELECT id, prompt_text FROM prompts WHERE id IN (" + placeholders + ")",
                tuple(chunk),
            )
            result.extend((r["id"], r["prompt_text"]) for r in rows)
        return result

    def token_id_for(self, kind: str, text_hash: str) -> int | None:
        return self._db.scalar(
            "SELECT id FROM tokens WHERE kind = ? AND token_hash = ?", (kind, text_hash)
        )

    def images_for_segment(
        self, text_hash: str, kind: str | None = None, limit: int = 5000
    ) -> list[int]:
        """Segmentsuche: alle Bilder, deren Prompt dieses Segment enthaelt.

        Der Weg fuehrt ueber prompt_tokens statt ueber den Volltext. Spezifikation
        05 verlangt genau das - keine Volltextdurchlaeufe fuer eine Segmentfrage.
        """
        sql = (
            "SELECT DISTINCT i.id FROM tokens t"
            " JOIN prompt_tokens pt ON pt.token_id = t.id"
            " JOIN images i ON i.prompt_id = pt.prompt_id"
            " WHERE t.token_hash = ? AND i.is_present = 1"
        )
        params: list[object] = [text_hash]
        if kind is not None:
            sql += " AND t.kind = ?"
            params.append(kind)
        sql += " ORDER BY i.filename LIMIT ?"
        params.append(limit)
        return [r["id"] for r in self._db.query(sql, tuple(params))]

    def segment_usage(self, text_hash: str) -> tuple[int, int]:
        """(Anzahl Prompts, Anzahl Bilder) fuer ein Segment."""
        row = self._db.query_one(
            "SELECT COALESCE(f.doc_count, 0) AS prompts,"
            " (SELECT COUNT(*) FROM prompt_tokens pt JOIN images i ON i.prompt_id = pt.prompt_id"
            "  WHERE pt.token_id = t.id AND i.is_present = 1) AS images"
            " FROM tokens t LEFT JOIN token_frequency f ON f.token_id = t.id"
            " WHERE t.token_hash = ?",
            (text_hash,),
        )
        return (row["prompts"], row["images"]) if row is not None else (0, 0)

    def segment_stats_for_prompt(self, prompt_id: int) -> list[dict[str, object]]:
        """Segmente eines Prompts samt Haeufigkeit - Grundlage der Editor-Hervorhebung."""
        rows = self._db.query(
            "SELECT pt.char_start, pt.char_end, pt.negative, pt.weight,"
            " s.kind, s.token, s.doc_count, s.commonness, s.idf, s.token_id"
            " FROM prompt_tokens pt JOIN token_stats s ON s.token_id = pt.token_id"
            " WHERE pt.prompt_id = ? ORDER BY pt.position",
            (prompt_id,),
        )
        return [dict(r) for r in rows]

    def diff_segments_for(self, prompt_id: int) -> list:
        """Segmente eines Prompts in Dokumentreihenfolge, fuer die Vergleichsansicht.

        Geliefert wird die Normalform samt Zeichenspanne im Originaltext. Der
        Vergleich rechnet auf token_id, die Anzeige braucht die Spanne.
        """
        from app.analysis.diff import DiffSegment

        rows = self._db.query(
            "SELECT pt.token_id, pt.char_start, pt.char_end, pt.negative, t.token"
            " FROM prompt_tokens pt JOIN tokens t ON t.id = pt.token_id"
            " WHERE pt.prompt_id = ? ORDER BY pt.position",
            (prompt_id,),
        )
        return [
            DiffSegment(
                token_id=r["token_id"],
                text=r["token"],
                char_start=r["char_start"],
                char_end=r["char_end"],
                negative=bool(r["negative"]),
            )
            for r in rows
        ]

    def top_segments(
        self, kind: str | None = None, limit: int = 100, min_docs: int = 2
    ) -> list[dict[str, object]]:
        sql = "SELECT * FROM token_stats WHERE doc_count >= ?"
        params: list[object] = [min_docs]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY doc_count DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self._db.query(sql, tuple(params))]

    def param_values(self, key_norm: str, limit: int = 200) -> list[tuple[str, int]]:
        """Belegte Werte eines Parameters mit Haeufigkeit, etwa alle Blendenwerte."""
        rows = self._db.query(
            "SELECT value_text, COUNT(*) AS uses FROM prompt_params"
            " WHERE key_norm = ? GROUP BY value_text ORDER BY uses DESC LIMIT ?",
            (key_norm, limit),
        )
        return [(r["value_text"], r["uses"]) for r in rows]

    def param_keys(self) -> list[tuple[str, int]]:
        rows = self._db.query(
            "SELECT key_norm, COUNT(*) AS uses FROM prompt_params"
            " GROUP BY key_norm ORDER BY uses DESC"
        )
        return [(r["key_norm"], r["uses"]) for r in rows]

    def images_for_param(self, key_norm: str, value_text: str, limit: int = 5000) -> list[int]:
        rows = self._db.query(
            "SELECT DISTINCT i.id FROM prompt_params p"
            " JOIN images i ON i.prompt_id = p.prompt_id"
            " WHERE p.key_norm = ? AND p.value_text = ? AND i.is_present = 1"
            " ORDER BY i.filename LIMIT ?",
            (key_norm, value_text, limit),
        )
        return [r["id"] for r in rows]

    def counts(self) -> dict[str, int]:
        return {
            "tokens": int(self._db.scalar("SELECT COUNT(*) FROM tokens", default=0) or 0),
            "prompt_tokens": int(
                self._db.scalar("SELECT COUNT(*) FROM prompt_tokens", default=0) or 0
            ),
            "sections": int(
                self._db.scalar("SELECT COUNT(*) FROM prompt_sections", default=0) or 0
            ),
            "params": int(self._db.scalar("SELECT COUNT(*) FROM prompt_params", default=0) or 0),
        }

    def purge_absent_prompts(self) -> int:
        """Entfernt Parseergebnisse geloeschter Prompts und korrigiert die Zaehler.

        Der Scanner markiert verschwundene Dateien nur als abwesend, damit die
        Historie erhalten bleibt. Ihre Segmentzeilen wuerden die Haeufigkeiten
        aber weiter aufblaehen: ein geloeschter Prompt zaehlte in doc_count
        unveraendert mit, und damit waeren commonness und idf falsch - Groessen,
        auf denen ab M4 die Basisprompt-Erkennung aufbaut.

        Liefert die Zahl der bereinigten Prompts.
        """

        def run(conn: sqlite3.Connection) -> int:
            rows = conn.execute(
                "SELECT DISTINCT pt.prompt_id FROM prompt_tokens pt"
                " JOIN prompts p ON p.id = pt.prompt_id"
                " WHERE p.is_present = 0"
            ).fetchall()
            prompt_ids = [r[0] for r in rows]
            if not prompt_ids:
                return 0

            old = TokenRepository._collect_old_contributions(conn, prompt_ids)
            TokenRepository._delete_previous(conn, prompt_ids)
            TokenRepository._apply_frequency_delta(conn, old, {})
            # Segmente, die dadurch von keinem Prompt mehr verwendet werden,
            # gleich mit entfernen. Gezielt auf die betroffenen Ids beschraenkt -
            # ein Anti-Join ueber die ganze Tabelle waere bei jedem Lauf zu teuer.
            TokenRepository._drop_unused_tokens(conn, list(old))
            for start in range(0, len(prompt_ids), _CHUNK):
                chunk = prompt_ids[start : start + _CHUNK]
                placeholders = ",".join("?" * len(chunk))
                conn.execute(
                    "UPDATE prompts SET parse_version = 0, parse_json = NULL"
                    " WHERE id IN (" + placeholders + ")",
                    tuple(chunk),
                )
            return len(prompt_ids)

        return int(self._writer.submit(run, label="tokens.purge_absent").result(timeout=120.0))

    def purge_orphan_tokens(self) -> int:
        """Entfernt Segmente, die kein Prompt mehr verwendet."""

        def run(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "DELETE FROM tokens WHERE id NOT IN (SELECT DISTINCT token_id FROM prompt_tokens)"
            )
            return cur.rowcount

        return int(self._writer.submit(run, label="tokens.purge").result(timeout=120.0))
