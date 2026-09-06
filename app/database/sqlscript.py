"""Ausfuehrung mehrteiliger SQL-Skripte innerhalb einer bestehenden Transaktion.

sqlite3.Connection.executescript() ist hier nicht verwendbar: es committet eine
offene Transaktion implizit, bevor es das Skript startet. Eine Migration wuerde
damit ihren Transaktionsschutz verlieren und koennte ein halb angelegtes Schema
hinterlassen.

Stattdessen wird das Skript in einzelne Statements zerlegt und per execute()
ausgefuehrt. Die Zerlegung stuetzt sich auf sqlite3.complete_statement(), das
auch Trigger-Bodies korrekt behandelt: das Semikolon nach einer Anweisung
innerhalb von BEGIN ... END beendet das Statement nicht.
"""

from __future__ import annotations

import sqlite3
from typing import Iterator


def split_statements(script: str) -> Iterator[str]:
    """Zerlegt ein SQL-Skript in vollstaendige Einzelstatements.

    Reine Kommentarzeilen werden vorher entfernt. Damit kann eine Klammer oder
    ein Apostroph in einem Kommentar die Statementerkennung nicht stoeren.
    """
    buffer = ""
    for raw_line in script.splitlines():
        if raw_line.strip().startswith("--"):
            continue
        buffer += raw_line + "\n"
        if buffer.strip() and sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement.rstrip(";").strip():
                yield statement
    tail = buffer.strip()
    if tail.rstrip(";").strip():
        yield tail


def exec_script(conn: sqlite3.Connection, script: str) -> int:
    """Fuehrt alle Statements des Skripts aus. Liefert deren Anzahl.

    Setzt keine eigene Transaktionssteuerung, damit der Aufrufer die Atomaritaet
    kontrolliert.
    """
    count = 0
    for statement in split_statements(script):
        conn.execute(statement)
        count += 1
    return count
