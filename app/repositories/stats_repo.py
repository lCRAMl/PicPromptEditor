"""Datenbankzugriff auf library_stats und analysis_state.

library_stats haelt die Zaehler, aus denen die View token_stats commonness und
idf berechnet. Sie werden inkrementell gefuehrt, weil eine Neuberechnung der
Gesamtzahl bei jedem Import genau das waere, was Spezifikation 04 ausschliesst.
"""

from __future__ import annotations

import sqlite3

from app.database.connection import Database
from app.database.writer import DbWriter


class StatsRepository:
    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    # library_stats ------------------------------------------------------------

    def all_counters(self) -> dict[str, int]:
        rows = self._db.query("SELECT key, int_value FROM library_stats")
        return {r["key"]: int(r["int_value"]) for r in rows}

    def counter(self, key: str, default: int = 0) -> int:
        value = self._db.scalar(
            "SELECT int_value FROM library_stats WHERE key = ?", (key,), default=default
        )
        return int(value if value is not None else default)

    def set_counter(self, key: str, value: int, *, wait: bool = False) -> None:
        future = self._writer.execute(
            "INSERT INTO library_stats(key, int_value, updated_at)"
            " VALUES (?, ?, unixepoch('subsec'))"
            " ON CONFLICT(key) DO UPDATE SET int_value = excluded.int_value,"
            " updated_at = excluded.updated_at",
            (key, int(value)),
            label="stats.set",
        )
        if wait:
            future.result(timeout=15.0)

    def bump_counter(self, key: str, delta: int, *, wait: bool = False) -> None:
        future = self._writer.execute(
            "INSERT INTO library_stats(key, int_value, updated_at)"
            " VALUES (?, ?, unixepoch('subsec'))"
            " ON CONFLICT(key) DO UPDATE SET"
            " int_value = MAX(0, library_stats.int_value + ?),"
            " updated_at = unixepoch('subsec')",
            (key, max(0, int(delta)), int(delta)),
            label="stats.bump",
        )
        if wait:
            future.result(timeout=15.0)

    def recount_from_tables(self) -> dict[str, int]:
        """Setzt die Zaehler aus den Tabellen neu. Nur bei Neuindizierung oder Reparatur.

        Die SELECTs tragen alle eine WHERE-Klausel. Bei INSERT ... SELECT ... ON CONFLICT
        kann SQLite sonst nicht entscheiden, ob das ON zum Upsert oder zu einem JOIN
        gehoert, und meldet einen Syntaxfehler.
        """

        def run(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO library_stats(key, int_value, updated_at)"
                " SELECT 'image_count', COUNT(*), unixepoch('subsec') FROM images WHERE is_present = 1"
                " ON CONFLICT(key) DO UPDATE SET int_value = excluded.int_value,"
                " updated_at = excluded.updated_at"
            )
            conn.execute(
                "INSERT INTO library_stats(key, int_value, updated_at)"
                " SELECT 'prompt_count', COUNT(*), unixepoch('subsec') FROM prompts WHERE is_present = 1"
                " ON CONFLICT(key) DO UPDATE SET int_value = excluded.int_value,"
                " updated_at = excluded.updated_at"
            )
            conn.execute(
                "INSERT INTO library_stats(key, int_value, updated_at)"
                " SELECT 'distinct_prompt_texts', COUNT(DISTINCT text_hash), unixepoch('subsec')"
                " FROM prompts WHERE is_present = 1 AND text_hash <> ''"
                " ON CONFLICT(key) DO UPDATE SET int_value = excluded.int_value,"
                " updated_at = excluded.updated_at"
            )
            conn.execute(
                "INSERT INTO library_stats(key, int_value, updated_at)"
                " SELECT 'token_count', COUNT(*), unixepoch('subsec') FROM tokens WHERE true"
                " ON CONFLICT(key) DO UPDATE SET int_value = excluded.int_value,"
                " updated_at = excluded.updated_at"
            )
            conn.execute(
                "INSERT INTO library_stats(key, int_value, updated_at)"
                " SELECT 'cluster_count', COUNT(*), unixepoch('subsec') FROM prompt_clusters WHERE true"
                " ON CONFLICT(key) DO UPDATE SET int_value = excluded.int_value,"
                " updated_at = excluded.updated_at"
            )

        self._writer.submit(run, label="stats.recount").result(timeout=60.0)
        return self.all_counters()

    # analysis_state -----------------------------------------------------------

    def stage_state(self, stage: str) -> dict[str, object] | None:
        row = self._db.query_one("SELECT * FROM analysis_state WHERE stage = ?", (stage,))
        return dict(row) if row is not None else None

    def all_stage_states(self) -> list[dict[str, object]]:
        return [dict(r) for r in self._db.query("SELECT * FROM analysis_state ORDER BY stage")]

    def set_stage_status(self, stage: str, status: str) -> None:
        self._writer.execute(
            "UPDATE analysis_state SET status = ? WHERE stage = ?", (status, stage),
            label="stage.status",
        )

    def finish_stage(self, stage: str, duration: float, processed: int, failed: int) -> None:
        self._writer.execute(
            "UPDATE analysis_state SET status = 'idle', last_run = unixepoch('subsec'),"
            " last_duration = ?, processed = ?, failed = ? WHERE stage = ?",
            (duration, processed, failed, stage),
            label="stage.finish",
        )

    def bump_algo_version(self, stage: str) -> int:
        """Invalidiert eine Analysestufe komplett, ohne Millionen Dirty-Flags zu schreiben."""

        def run(conn: sqlite3.Connection) -> int:
            conn.execute(
                "UPDATE analysis_state SET algo_version = algo_version + 1 WHERE stage = ?",
                (stage,),
            )
            row = conn.execute(
                "SELECT algo_version FROM analysis_state WHERE stage = ?", (stage,)
            ).fetchone()
            return int(row[0]) if row else 0

        return int(self._writer.submit(run, label="stage.bump").result(timeout=15.0))

    # dirty_prompts ------------------------------------------------------------

    def mark_dirty(self, prompt_ids: list[int], stage: str, reason: str = "") -> None:
        if not prompt_ids:
            return
        rows = [(pid, stage, reason) for pid in prompt_ids]
        self._writer.executemany(
            "INSERT INTO dirty_prompts(prompt_id, stage, reason, marked_at)"
            " VALUES (?, ?, ?, unixepoch('subsec'))"
            " ON CONFLICT(prompt_id, stage) DO UPDATE SET"
            " marked_at = unixepoch('subsec'), reason = excluded.reason",
            rows,
            label="dirty.mark",
        )

    def claim_dirty(self, stage: str, limit: int) -> list[int]:
        """Holt Arbeit atomar mit DELETE ... RETURNING.

        Zwei parallele Analysejobs koennen denselben Prompt dadurch nicht doppelt
        bearbeiten. Verfuegbar ab SQLite 3.35; hier laeuft 3.49.
        """

        def run(conn: sqlite3.Connection) -> list[int]:
            rows = conn.execute(
                "DELETE FROM dirty_prompts WHERE prompt_id IN ("
                " SELECT prompt_id FROM dirty_prompts WHERE stage = ?"
                " ORDER BY marked_at LIMIT ?) AND stage = ?"
                " RETURNING prompt_id",
                (stage, limit, stage),
            ).fetchall()
            return [int(r[0]) for r in rows]

        return list(self._writer.submit(run, label="dirty.claim").result(timeout=30.0))

    def dirty_count(self, stage: str | None = None) -> int:
        if stage is None:
            return int(self._db.scalar("SELECT COUNT(*) FROM dirty_prompts", default=0) or 0)
        return int(
            self._db.scalar(
                "SELECT COUNT(*) FROM dirty_prompts WHERE stage = ?", (stage,), default=0
            )
            or 0
        )
