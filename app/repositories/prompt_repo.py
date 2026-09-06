"""Datenbankzugriff auf die Tabelle prompts und den FTS5-Index.

Der Prompttext liegt vollstaendig lokal. Editor, Suche, Vergleich und Evolution
lesen niemals eine Promptdatei vom NAS.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Iterable, Sequence

from app.database.connection import Database
from app.database.writer import DbWriter
from app.dto.models import PromptData

_COLUMNS = (
    "id, filename, full_path, prompt_text, encoding, token_count, word_count,"
    " is_present, file_size, modified_time"
)

# Zeichen, die in der FTS5-MATCH-Syntax eine Sonderbedeutung haben.
_FTS_SPECIAL = re.compile(r'["\'()*:^-]')
_TOKEN_SPLIT = re.compile(r"\s+")


def escape_fts_query(text: str, prefix: bool = True) -> str:
    """Wandelt eine Benutzereingabe in einen sicheren FTS5-Ausdruck.

    Ein roher Suchstring ist keine gueltige MATCH-Syntax: Anfuehrungszeichen,
    Klammern, Doppelpunkte und Bindestriche haben dort eigene Bedeutung und
    fuehren zu Syntaxfehlern statt zu Treffern. Jedes Wort wird deshalb als
    Phrase in Anfuehrungszeichen gesetzt und optional mit Praefix-Stern versehen.
    """
    words = [w for w in _TOKEN_SPLIT.split(text.strip()) if w]
    parts: list[str] = []
    for word in words:
        cleaned = _FTS_SPECIAL.sub(" ", word).strip()
        if not cleaned:
            continue
        parts.append('"' + cleaned.replace('"', "") + '"' + ("*" if prefix else ""))
    return " ".join(parts)


def _prompt(row: sqlite3.Row) -> PromptData:
    return PromptData(
        id=row["id"],
        filename=row["filename"],
        full_path=row["full_path"],
        text=row["prompt_text"],
        encoding=row["encoding"],
        token_count=row["token_count"],
        word_count=row["word_count"],
        is_present=bool(row["is_present"]),
        file_size=row["file_size"],
        modified_time=row["modified_time"],
    )


class PromptRepository:
    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    # Lesen --------------------------------------------------------------------

    def by_id(self, prompt_id: int) -> PromptData | None:
        row = self._db.query_one(
            "SELECT " + _COLUMNS + " FROM prompts WHERE id = ?", (prompt_id,)
        )
        return _prompt(row) if row is not None else None

    def by_image_id(self, image_id: int) -> PromptData | None:
        row = self._db.query_one(
            "SELECT p." + _COLUMNS.replace(", ", ", p.") + " FROM prompts p"
            " JOIN images i ON i.prompt_id = p.id WHERE i.id = ?",
            (image_id,),
        )
        return _prompt(row) if row is not None else None

    def existing_signatures(self, folder_id: int) -> dict[str, tuple[int, int, int]]:
        """stem_norm -> (id, file_size, modified_time)."""
        rows = self._db.query(
            "SELECT id, stem_norm, file_size, modified_time FROM prompts WHERE folder_id = ?",
            (folder_id,),
        )
        return {r["stem_norm"]: (r["id"], r["file_size"], r["modified_time"]) for r in rows}

    def count(self, present_only: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM prompts"
        if present_only:
            sql += " WHERE is_present = 1"
        return int(self._db.scalar(sql, default=0) or 0)

    def search_ids(
        self, query: str, limit: int = 5000, folder_ids: Sequence[int] | None = None
    ) -> list[int]:
        """Volltextsuche. Liefert Bild-Ids, denn das Raster zeigt Bilder.

        folder_ids schraenkt auf einen Ordnerbereich ein. Die Einschraenkung
        gehoert in die Abfrage: nachtraegliches Filtern im Speicher wuerde erst
        zehntausend Treffer holen, um zwanzig zu behalten.
        """
        expression = escape_fts_query(query)
        if not expression:
            return []

        sql = (
            "SELECT i.id FROM prompts_fts f"
            " JOIN prompts p ON p.id = f.rowid"
            " JOIN images i ON i.prompt_id = p.id"
            " WHERE f.prompts_fts MATCH ? AND i.is_present = 1"
        )
        params: list[object] = [expression]
        if folder_ids:
            sql += " AND i.folder_id IN (" + ",".join("?" * len(folder_ids)) + ")"
            params.extend(folder_ids)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        return [r["id"] for r in self._db.query(sql, tuple(params))]

    # Schreiben ----------------------------------------------------------------

    def insert_many(self, rows: Iterable[tuple]) -> None:
        """rows: (folder_id, filename, full_path, stem_norm, prompt_text,
        text_hash, encoding, file_size, modified_time, word_count)."""
        batch = list(rows)
        if not batch:
            return
        self._writer.await_result(
            self._writer.executemany(
                "INSERT INTO prompts"
                " (folder_id, filename, full_path, stem_norm, prompt_text, text_hash,"
                "  encoding, file_size, modified_time, word_count, is_present, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, unixepoch('subsec'))"
                " ON CONFLICT(folder_id, stem_norm) DO UPDATE SET"
                " filename = excluded.filename,"
                " full_path = excluded.full_path,"
                " prompt_text = excluded.prompt_text,"
                " text_hash = excluded.text_hash,"
                " encoding = excluded.encoding,"
                " file_size = excluded.file_size,"
                " modified_time = excluded.modified_time,"
                " word_count = excluded.word_count,"
                " parse_version = CASE WHEN prompts.text_hash = excluded.text_hash"
                "                      THEN prompts.parse_version ELSE 0 END,"
                " is_present = 1,"
                " updated_at = excluded.updated_at",
                batch,
                label="prompts.insert",
            )
        )

    def reset_signatures(self) -> None:
        """Gegenstueck zu ImageRepository.reset_signatures fuer Promptdateien."""
        self._writer.await_result(
            self._writer.execute(
                "UPDATE prompts SET file_size = -1, modified_time = -1",
                label="prompts.reset_signatures",
            )
        )

    def mark_absent(self, prompt_ids: Sequence[int]) -> None:
        if not prompt_ids:
            return
        self._writer.await_result(
            self._writer.executemany(
                "UPDATE prompts SET is_present = 0, updated_at = unixepoch('subsec') WHERE id = ?",
                [(i,) for i in prompt_ids],
                label="prompts.absent",
            )
        )
