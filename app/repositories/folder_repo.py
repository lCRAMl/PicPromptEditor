"""Datenbankzugriff auf die Tabelle folders.

Der Ordnerbaum der Oberflaeche wird ausschliesslich aus dieser Tabelle
aufgebaut. Spezifikation 02 verbietet der GUI jeden Verzeichniszugriff; ohne
parent_id waere jede Baumdarstellung ein Dateisystemzugriff.
"""

from __future__ import annotations

import sqlite3
from pathlib import PurePath

from app.core.paths import normalize_path
from app.database.connection import Database
from app.database.writer import DbWriter
from app.dto.models import FolderNode


def _node(row: sqlite3.Row) -> FolderNode:
    path = row["path"]
    return FolderNode(
        id=row["id"],
        path=path,
        name=PurePath(path).name or path,
        parent_id=row["parent_id"],
        depth=row["depth"],
        image_count=row["image_count"],
        prompt_count=row["prompt_count"],
        last_scan=row["last_scan"],
        is_present=bool(row["is_present"]),
        ai_image_count=_optional(row, "ai_image_count"),
        ai_prompt_count=_optional(row, "ai_prompt_count"),
    )


def _optional(row: sqlite3.Row, name: str) -> int:
    """Spalte lesen, falls die Abfrage sie mitgeliefert hat.

    Nicht jede Abfrage braucht die Zahlen des ai-Unterordners; sie kosten eine
    Unterabfrage je Zeile und lohnen nur fuer den Baum.
    """
    try:
        return int(row[name] or 0)
    except (IndexError, KeyError):
        return 0


class FolderRepository:
    _COLUMNS = (
        "id, path, path_norm, parent_id, depth, is_root, last_scan,"
        " image_count, prompt_count, active, is_present"
    )

    def __init__(self, database: Database, writer: DbWriter) -> None:
        self._db = database
        self._writer = writer

    # Lesen --------------------------------------------------------------------

    #: Zusatzspalten fuer den Baum: die Zahlen des Unterordners "ai".
    #: Verglichen wird ueber path_norm, weil der bereits kleingeschrieben und
    #: mit einheitlichen Trennzeichen abgelegt ist.
    _AI_COLUMNS = (
        ", (SELECT k.image_count FROM folders k"
        "   WHERE k.parent_id = folders.id AND k.path_norm = folders.path_norm || ?"
        "   AND k.is_present = 1) AS ai_image_count"
        ", (SELECT k.prompt_count FROM folders k"
        "   WHERE k.parent_id = folders.id AND k.path_norm = folders.path_norm || ?"
        "   AND k.is_present = 1) AS ai_prompt_count"
    )
    #: Der Name des Unterordners, in dem die Prompts liegen.
    AI_FOLDER = "ai"

    def all_folders(self, include_absent: bool = False) -> list[FolderNode]:
        endung = "\\" + self.AI_FOLDER
        sql = "SELECT " + self._COLUMNS + self._AI_COLUMNS + " FROM folders"
        if not include_absent:
            sql += " WHERE is_present = 1"
        sql += " ORDER BY depth, path"
        return [_node(r) for r in self._db.query(sql, (endung, endung))]

    def roots(self) -> list[FolderNode]:
        return [
            _node(r)
            for r in self._db.query(
                "SELECT " + self._COLUMNS + " FROM folders WHERE is_root = 1 ORDER BY path"
            )
        ]

    def by_id(self, folder_id: int) -> FolderNode | None:
        row = self._db.query_one(
            "SELECT " + self._COLUMNS + " FROM folders WHERE id = ?", (folder_id,)
        )
        return _node(row) if row is not None else None

    def by_path(self, path: str) -> FolderNode | None:
        row = self._db.query_one(
            "SELECT " + self._COLUMNS + " FROM folders WHERE path_norm = ?",
            (normalize_path(path),),
        )
        return _node(row) if row is not None else None

    def id_map(self) -> dict[str, int]:
        """path_norm -> id. Der Scanner haelt das im Speicher, statt je Ordner zu fragen."""
        return {
            r["path_norm"]: r["id"] for r in self._db.query("SELECT id, path_norm FROM folders")
        }

    def descendant_ids(self, folder_id: int) -> list[int]:
        """Ordner und alle Unterordner. Rekursive CTE statt Schleife in Python."""
        rows = self._db.query(
            "WITH RECURSIVE sub(id) AS ("
            "  SELECT id FROM folders WHERE id = ?"
            "  UNION ALL"
            "  SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id"
            ") SELECT id FROM sub",
            (folder_id,),
        )
        return [r["id"] for r in rows]

    # Schreiben ----------------------------------------------------------------

    def upsert_many(self, folders: list[tuple[str, int | None, int, bool]]) -> None:
        """folders: (path, parent_id, depth, is_root)."""
        if not folders:
            return
        rows = [(p, normalize_path(p), parent, depth, int(root)) for p, parent, depth, root in folders]
        # Auf Fortschritt warten, nicht auf die Uhr: waehrend eines Abgleichs
        # kann die Analyse einen minutenlangen Schreibvorgang in der
        # Warteschlange haben, und dieser hier steht dahinter. Eine feste Frist
        # hat den Abgleich deswegen im Betrieb dreimal abgebrochen.
        self._writer.await_result(
            self._writer.executemany(
                "INSERT INTO folders(path, path_norm, parent_id, depth, is_root, updated_at)"
                " VALUES (?, ?, ?, ?, ?, unixepoch('subsec'))"
                " ON CONFLICT(path_norm) DO UPDATE SET"
                " parent_id = excluded.parent_id,"
                " depth = excluded.depth,"
                " is_root = MAX(folders.is_root, excluded.is_root),"
                " is_present = 1,"
                " updated_at = excluded.updated_at",
                rows,
                label="folders.upsert",
            )
        )

    def add_root(self, path: str) -> int:
        def run(conn: sqlite3.Connection) -> int:
            conn.execute(
                "INSERT INTO folders(path, path_norm, parent_id, depth, is_root, updated_at)"
                " VALUES (?, ?, NULL, 0, 1, unixepoch('subsec'))"
                " ON CONFLICT(path_norm) DO UPDATE SET is_root = 1, is_present = 1,"
                " updated_at = unixepoch('subsec')",
                (path, normalize_path(path)),
            )
            row = conn.execute(
                "SELECT id FROM folders WHERE path_norm = ?", (normalize_path(path),)
            ).fetchone()
            return int(row[0])

        return int(
            self._writer.await_result(
                self._writer.submit(run, label="folders.add_root")
            )
        )

    def mark_scanned(self, folder_id: int) -> None:
        self._writer.execute(
            "UPDATE folders SET last_scan = unixepoch('subsec') WHERE id = ?",
            (folder_id,),
            label="folders.scanned",
        )

    def mark_absent(self, folder_ids: list[int]) -> None:
        if not folder_ids:
            return
        self._writer.executemany(
            "UPDATE folders SET is_present = 0, updated_at = unixepoch('subsec') WHERE id = ?",
            [(fid,) for fid in folder_ids],
            label="folders.absent",
        )

    def refresh_counts(self) -> None:
        """Zaehlt Bilder und Prompts je Ordner neu.

        Die SELECTs tragen eine WHERE-Klausel: bei UPDATE ... SET x = (SELECT ...)
        ist das zwar nicht zwingend, haelt die Statements aber mit den Upserts
        an anderer Stelle konsistent lesbar.
        """

        def run(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE folders SET"
                " image_count = (SELECT COUNT(*) FROM images i"
                "                WHERE i.folder_id = folders.id AND i.is_present = 1),"
                " prompt_count = (SELECT COUNT(*) FROM prompts p"
                "                 WHERE p.folder_id = folders.id AND p.is_present = 1)"
            )

        self._writer.await_result(self._writer.submit(run, label="folders.counts"))
