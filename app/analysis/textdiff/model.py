"""Die Datenklassen des Vergleichs.

Alles, was der Vergleich liefert, steht hier - und zwar ausschliesslich als
**semantischer Zustand**. Kein Feld traegt eine Farbe, eine Schriftart oder eine
Pixelangabe. Welche Farbe "changed" bekommt, entscheidet allein der Renderer
anhand seines Themes; im Dunkelmodus ist es eine andere als im hellen, und das
Modell muss davon nichts wissen.

Der zentrale Begriff ist die **AlignedRow**: eine Zeile der gemeinsamen
Darstellung. Sie traegt links und rechts je eine Quellzeile - oder None. None
bedeutet: auf dieser Seite gibt es an dieser Stelle keine reale Zeile. Die
Oberflaeche zeigt dort einen grauen Platzhalter, damit die korrespondierenden
Textbereiche nebeneinander stehen bleiben.

Ein Platzhalter ist **keine Leerzeile im Text**. Er hat keine Zeilennummer, er
wird nie gespeichert, und beim Uebertragen eines Blocks auf die andere Seite
wird er in ein Einfuegen oder Loeschen uebersetzt - nicht als leere Zeile
mitkopiert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.analysis.textdiff.options import CompareOptions

NEWLINE = chr(10)


class DiffStatus(str, Enum):
    """Zustand einer ausgerichteten Zeile und eines Unterschiedsblocks.

    Beide benutzen dasselbe Vokabular. Ein Block aus lauter ADDED-Zeilen ist
    ein ADDED-Block; sobald beide Seiten reale Zeilen beitragen, ist er
    CHANGED.
    """

    EQUAL = "equal"
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"
    IGNORED = "ignored"
    MOVED = "moved"


class SegmentStatus(str, Enum):
    """Zustand eines Abschnitts INNERHALB einer Zeile."""

    EQUAL = "equal"
    CHANGED = "changed"
    ADDED = "added"
    REMOVED = "removed"


#: Zustaende, die einen echten Unterschied bedeuten. IGNORED zaehlt nicht dazu:
#: der Unterschied existiert, wurde aber durch eine Vergleichsoption
#: ausdruecklich fuer unerheblich erklaert. Er bleibt sichtbar, aber er faellt
#: nicht in die Zaehlung, und "Naechster Unterschied" springt ihn nicht an.
REAL_DIFFERENCES = (
    DiffStatus.CHANGED,
    DiffStatus.ADDED,
    DiffStatus.REMOVED,
    DiffStatus.MOVED,
)


@dataclass(frozen=True, slots=True)
class SourceLine:
    """Eine reale Zeile einer der beiden Seiten.

    `text` ist der Originaltext ohne Zeilenende. `normalized_text` ist die
    Vergleichsform nach den eingestellten Optionen - sie entscheidet ueber
    gleich oder ungleich, wird aber niemals angezeigt und niemals gespeichert.
    """

    line_number: int
    text: str
    normalized_text: str
    eol: str = NEWLINE

    @property
    def is_blank(self) -> bool:
        return not self.text.strip()


@dataclass(frozen=True, slots=True)
class DiffSegment:
    """Ein Abschnitt innerhalb einer Zeile, mit Positionen IN DIESER ZEILE.

    Spaltenbasiert, nicht dokumentbasiert. Das ist der Unterschied zum
    Vorgaenger: dessen Spannen zaehlten im Rohtext der Datei und mussten zweimal
    umgerechnet werden, bevor sie im Editor stimmten - erst um die verworfenen
    Wagenrueckluf-Zeichen, dann um die eingeschobenen Leerzeilen. Eine Position
    relativ zum Zeilenanfang ueberlebt jede Ausrichtung unveraendert.
    """

    start: int
    end: int
    text: str
    status: SegmentStatus

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class AlignedRow:
    """Eine Zeile der gemeinsamen Darstellung.

    `display_index` ist auf beiden Seiten dieselbe Zahl - das ist der ganze
    Zweck der Ausrichtung. Die Oberflaeche liest daraus ohne weitere Rechnung
    ab, welche Zeile links neben welcher Zeile rechts steht.
    """

    display_index: int
    left_line: SourceLine | None
    right_line: SourceLine | None
    status: DiffStatus
    left_segments: tuple[DiffSegment, ...] = ()
    right_segments: tuple[DiffSegment, ...] = ()
    difference_index: int | None = None
    #: Nur bei MOVED gesetzt: display_index der Gegenstelle.
    moved_partner: int | None = None

    @property
    def left_is_placeholder(self) -> bool:
        return self.left_line is None

    @property
    def right_is_placeholder(self) -> bool:
        return self.right_line is None

    @property
    def left_text(self) -> str:
        return self.left_line.text if self.left_line is not None else ""

    @property
    def right_text(self) -> str:
        return self.right_line.text if self.right_line is not None else ""

    @property
    def is_difference(self) -> bool:
        return self.status in REAL_DIFFERENCES


@dataclass(frozen=True, slots=True)
class DifferenceBlock:
    """Zusammenhaengende Zeilen, die sich unterscheiden.

    Zwei Zaehlweisen nebeneinander, jede fuer ihren Zweck:

    `left_start` / `left_end` sind **Zeilennummern** (1-basiert, wie der Editor
    sie anzeigt) oder None, wenn diese Seite im Block keine reale Zeile hat.
    Sie sind fuer die Anzeige da - "Zeilen 12 bis 15".

    `left_slice` ist ein **halboffener Bereich in die Quellzeilenliste**
    (0-basiert). Er bleibt auch dann sinnvoll, wenn die Seite nichts beitraegt:
    (7, 7) heisst "hier einfuegen, nichts entfernen". Genau diese Angabe
    braucht das Uebertragen eines Blocks; mit den nullbaren Zeilennummern
    liesse sich die Einfuegestelle gar nicht ausdruecken.
    """

    index: int
    type: DiffStatus
    left_start: int | None
    left_end: int | None
    right_start: int | None
    right_end: int | None
    left_slice: tuple[int, int]
    right_slice: tuple[int, int]
    first_row: int
    last_row: int
    rows: tuple[AlignedRow, ...] = ()

    @property
    def row_count(self) -> int:
        return self.last_row - self.first_row + 1

    @property
    def is_real(self) -> bool:
        return self.type in REAL_DIFFERENCES

    def describe(self) -> str:
        """Kurzfassung fuer die Statusleiste."""
        def seite(start: int | None, ende: int | None) -> str:
            if start is None:
                return "-"
            return str(start) if start == ende else str(start) + "-" + str(ende)

        return (
            "A " + seite(self.left_start, self.left_end)
            + "  |  B " + seite(self.right_start, self.right_end)
        )


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """Das vollstaendige Ergebnis eines Vergleichs.

    Enthaelt beide Modelle nebeneinander, die §18 des Auftrags verlangt:

      SOURCE MODEL   `left_lines` / `right_lines` - was in der Datei steht
      DISPLAY MODEL  `rows` - was der Editor zeigt, samt Platzhaltern

    Gespeichert wird ausschliesslich das Source Model.
    """

    rows: tuple[AlignedRow, ...]
    differences: tuple[DifferenceBlock, ...]
    left_lines: tuple[SourceLine, ...]
    right_lines: tuple[SourceLine, ...]
    options: CompareOptions = field(default_factory=CompareOptions)

    # Darstellung --------------------------------------------------------------

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def left_display_text(self) -> str:
        """Der linke Text mit Platzhaltern als Leerzeilen.

        Die Platzhalter bekommen KEINE Leerzeichen zum Auffuellen. Beim
        Herauskopieren blieben sie sonst als unsichtbarer Unrat zurueck; die
        Faerbung uebernimmt statt dessen eine Auswahl ueber die volle Breite.
        """
        return NEWLINE.join(row.left_text for row in self.rows)

    def right_display_text(self) -> str:
        return NEWLINE.join(row.right_text for row in self.rows)

    def left_placeholder_rows(self) -> tuple[int, ...]:
        return tuple(r.display_index for r in self.rows if r.left_is_placeholder)

    def right_placeholder_rows(self) -> tuple[int, ...]:
        return tuple(r.display_index for r in self.rows if r.right_is_placeholder)

    def left_line_numbers(self) -> tuple[int | None, ...]:
        """Je Anzeigezeile die reale Zeilennummer - oder None beim Platzhalter.

        Genau das braucht die Zeilennummernspalte: ein Platzhalter traegt keine
        Nummer, weil er keine Zeile ist.
        """
        return tuple(
            r.left_line.line_number if r.left_line is not None else None
            for r in self.rows
        )

    def right_line_numbers(self) -> tuple[int | None, ...]:
        return tuple(
            r.right_line.line_number if r.right_line is not None else None
            for r in self.rows
        )

    # Quellmodell --------------------------------------------------------------

    def left_source_text(self) -> str:
        return NEWLINE.join(line.text for line in self.left_lines)

    def right_source_text(self) -> str:
        return NEWLINE.join(line.text for line in self.right_lines)

    # Unterschiede -------------------------------------------------------------

    @property
    def real_differences(self) -> tuple[DifferenceBlock, ...]:
        return tuple(b for b in self.differences if b.is_real)

    @property
    def difference_count(self) -> int:
        return len(self.real_differences)

    @property
    def is_identical(self) -> bool:
        return self.difference_count == 0

    def block_at_row(self, display_index: int) -> DifferenceBlock | None:
        for block in self.differences:
            if block.first_row <= display_index <= block.last_row:
                return block
        return None

    def next_difference(self, after_row: int) -> DifferenceBlock | None:
        """Der erste echte Unterschied, der unterhalb dieser Zeile beginnt."""
        for block in self.real_differences:
            if block.first_row > after_row:
                return block
        return None

    def previous_difference(self, before_row: int) -> DifferenceBlock | None:
        for block in reversed(self.real_differences):
            if block.last_row < before_row:
                return block
        return None

    def counts(self) -> dict[str, int]:
        """Zeilenweise Kennzahlen fuer die Statusleiste."""
        tally = {status.value: 0 for status in DiffStatus}
        for row in self.rows:
            tally[row.status.value] += 1
        return tally

    def summary(self) -> str:
        """Eine Zeile Text fuer die Statusleiste."""
        if self.is_identical:
            ignoriert = self.counts()[DiffStatus.IGNORED.value]
            if ignoriert:
                return (
                    "Keine erheblichen Unterschiede  -  "
                    + str(ignoriert)
                    + " durch die Vergleichsoptionen ignoriert"
                )
            return "Die Prompts sind identisch"

        tally = self.counts()
        teile = [str(self.difference_count) + " Unterschiede"]
        for status, wort in (
            (DiffStatus.CHANGED, "geaendert"),
            (DiffStatus.ADDED, "nur B"),
            (DiffStatus.REMOVED, "nur A"),
            (DiffStatus.MOVED, "verschoben"),
            (DiffStatus.IGNORED, "ignoriert"),
        ):
            if tally[status.value]:
                teile.append(str(tally[status.value]) + " " + wort)
        return "   |   ".join(
            [teile[0], ", ".join(teile[1:])] if len(teile) > 1 else teile
        )
