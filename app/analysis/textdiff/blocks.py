"""Zusammenhaengende Unterschiede zu Bloecken buendeln.

Ein Difference Block ist die Einheit, in der ein Mensch denkt und in der das
Programm handelt: "Naechster Unterschied" springt von Block zu Block, nicht von
Zeile zu Zeile, und "nach rechts uebernehmen" uebertraegt einen Block, nicht
eine Zeile. Drei geaenderte Zeilen hintereinander sind **ein** Unterschied,
nicht drei.

Aufeinanderfolgende Zeilen kommen in denselben Block, sobald beide vom
Zustand EQUAL abweichen - auch wenn die eine CHANGED und die naechste ADDED
ist. Genau das ist der uebliche Fall: eine Zeile wurde geaendert und
gleichzeitig eine neue eingefuegt.

IGNORED bricht einen Block **nicht** auf, bildet aber auch keinen eigenen -
sonst zerfiele ein Unterschied, in dessen Mitte zufaellig eine nur anders
eingerueckte Zeile steht, in zwei Haelften.
"""

from __future__ import annotations

from dataclasses import replace

from app.analysis.textdiff.model import (
    REAL_DIFFERENCES,
    AlignedRow,
    DiffStatus,
    DifferenceBlock,
)


def build_blocks(
    zeilen: list[AlignedRow],
) -> tuple[tuple[AlignedRow, ...], tuple[DifferenceBlock, ...]]:
    """Buendelt die Zeilen und stempelt ihnen ihre Blocknummer auf.

    Geliefert werden die Zeilen **noch einmal** - mit gesetztem
    `difference_index`. Die Zeilen in den Bloecken sind dieselben Objekte wie
    die in der Liste; wer eine Zeile aus einem Block in der Hand hat, bekommt
    ueber `display_index` genau den Eintrag der Gesamtliste.
    """
    gruppen = _group(zeilen)

    fertige: list[AlignedRow] = list(zeilen)
    bloecke: list[DifferenceBlock] = []

    for nummer, (erste, letzte) in enumerate(gruppen):
        gestempelt = [
            replace(zeilen[n], difference_index=nummer)
            for n in range(erste, letzte + 1)
        ]
        for zeile in gestempelt:
            fertige[zeile.display_index] = zeile
        bloecke.append(_block(nummer, tuple(gestempelt), zeilen, erste, letzte))

    return tuple(fertige), tuple(bloecke)


def _group(zeilen: list[AlignedRow]) -> list[tuple[int, int]]:
    """Bereiche aufeinanderfolgender Zeilen, die nicht EQUAL sind."""
    gruppen: list[tuple[int, int]] = []
    beginn: int | None = None

    for zeile in zeilen:
        if zeile.status is DiffStatus.EQUAL:
            if beginn is not None:
                gruppen.append((beginn, zeile.display_index - 1))
                beginn = None
            continue
        if beginn is None:
            beginn = zeile.display_index

    if beginn is not None:
        gruppen.append((beginn, zeilen[-1].display_index))
    return gruppen


def _block(
    nummer: int,
    zeilen: tuple[AlignedRow, ...],
    alle: list[AlignedRow],
    erste: int,
    letzte: int,
) -> DifferenceBlock:
    linke = [z.left_line for z in zeilen if z.left_line is not None]
    rechte = [z.right_line for z in zeilen if z.right_line is not None]

    return DifferenceBlock(
        index=nummer,
        type=_type_of(zeilen),
        left_start=linke[0].line_number if linke else None,
        left_end=linke[-1].line_number if linke else None,
        right_start=rechte[0].line_number if rechte else None,
        right_end=rechte[-1].line_number if rechte else None,
        left_slice=_slice(alle, erste, letzte, links=True),
        right_slice=_slice(alle, erste, letzte, links=False),
        first_row=erste,
        last_row=letzte,
        rows=zeilen,
    )


def _type_of(zeilen: tuple[AlignedRow, ...]) -> DiffStatus:
    """Der Zustand des Blocks als Ganzes.

    Die Regel folgt dem, was der Benutzer sieht: traegt nur eine Seite reale
    Zeilen bei, ist es ein Einfuegen oder ein Entfernen. Tragen beide bei, ist
    es eine Aenderung - auch wenn im Block einzelne Zeilen fuer sich genommen
    nur auf einer Seite stehen.
    """
    zustaende = {z.status for z in zeilen}
    echte = zustaende & set(REAL_DIFFERENCES)
    if not echte:
        return DiffStatus.IGNORED
    if echte == {DiffStatus.MOVED}:
        return DiffStatus.MOVED
    if echte == {DiffStatus.ADDED}:
        return DiffStatus.ADDED
    if echte == {DiffStatus.REMOVED}:
        return DiffStatus.REMOVED
    return DiffStatus.CHANGED


def _slice(
    alle: list[AlignedRow], erste: int, letzte: int, *, links: bool
) -> tuple[int, int]:
    """Halboffener Bereich in die Quellzeilenliste einer Seite.

    Der Sonderfall ist der Block, in dem diese Seite gar keine Zeile hat. Dann
    muss trotzdem eine Stelle herauskommen - naemlich die, an der eingefuegt
    werden wuerde. Sie ergibt sich aus der letzten realen Zeile **vor** dem
    Block: steht dort Zeile 7, ist die Einfuegestelle 7 (0-basiert also
    unmittelbar dahinter). Ohne diese Angabe waere "diesen Block nach rechts
    uebernehmen" bei einer reinen Einfuegung nicht ausfuehrbar.
    """
    def zeile_von(row: AlignedRow):
        return row.left_line if links else row.right_line

    innen = [zeile_von(alle[n]) for n in range(erste, letzte + 1)]
    vorhanden = [z for z in innen if z is not None]
    if vorhanden:
        # line_number ist 1-basiert, der Bereich 0-basiert.
        return (vorhanden[0].line_number - 1, vorhanden[-1].line_number)

    for n in range(erste - 1, -1, -1):
        davor = zeile_von(alle[n])
        if davor is not None:
            return (davor.line_number, davor.line_number)
    return (0, 0)
