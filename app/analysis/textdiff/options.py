"""Vergleichsoptionen.

Die Optionen entscheiden ausschliesslich darueber, was als **gleich** gilt.
Sie veraendern den Originaltext nicht und sind nirgends sichtbar - die
Oberflaeche zeigt immer den echten Text, auch wenn verglichen wurde, als
stuende dort etwas anderes.

Der Unterschied ist wichtiger, als er klingt. Waere die Normalisierung im
angezeigten Text wirksam, wuerde "Gross- und Kleinschreibung ignorieren" den
Prompt in Kleinbuchstaben anzeigen, und "Leerraum ignorieren" wuerde die
Einrueckung der Abschnitte zerstoeren. Beides waere fuer den Benutzer eine
Verfaelschung seiner Daten.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Wortweise Verfeinerung. Der Regelfall: bei Prosa-Prompts sind ganze Woerter
#: die Einheit, in der ein Mensch liest.
INTRA_WORD = "word"
#: Zeichenweise Verfeinerung. Genauer, aber bei langen Saetzen unruhig - eine
#: Aenderung von "colour" zu "color" faerbt sonst nur ein einzelnes "u".
INTRA_CHARACTER = "character"

#: Algorithmus fuer den Zeilenabgleich. Siehe sequence.py.
ALGO_SEQUENCE_MATCHER = "sequence_matcher"
ALGO_PATIENCE = "patience"


@dataclass(frozen=True, slots=True)
class CompareOptions:
    """Was beim Vergleich als gleich gelten soll.

    Die Vorgaben entsprechen WinMerge: nichts wird ignoriert, ausser den
    Zeilenenden. Der Grund fuer diese eine Ausnahme ist praktischer Natur -
    die Bibliothek enthaelt Promptdateien mit Windows- und mit Unix-Enden
    nebeneinander, und ein Vergleich, der jede Zeile deswegen als geaendert
    meldet, ist wertlos.
    """

    #: Gross- und Kleinschreibung ist unerheblich.
    ignore_case: bool = False
    #: Jeder Leerraum faellt weg. "a b" und "ab" sind dann gleich.
    ignore_whitespace: bool = False
    #: Nur AENDERUNGEN am Leerraum sind unerheblich: Folgen von Leerzeichen
    #: werden zu einem einzigen zusammengezogen, Rand-Leerraum faellt weg.
    #: "a   b" und "a b" sind gleich, "a b" und "ab" nicht.
    ignore_whitespace_changes: bool = False
    #: Leerzeilen zaehlen nicht als Unterschied.
    ignore_blank_lines: bool = False
    #: Zeilenenden vergleichen. Aus heisst: CRLF und LF sind dasselbe.
    compare_eol: bool = False
    #: "word" oder "character".
    intra_line_mode: str = INTRA_WORD
    #: Verschobene Bloecke als solche erkennen. Nach §13 des Auftrags
    #: abschaltbar - und aus gutem Grund per Vorgabe aus: bei Prompts mit
    #: wiederkehrenden Textbausteinen erzeugt die Erkennung mehr Rauschen als
    #: Auskunft.
    detect_moved: bool = False
    #: Ab welcher Aehnlichkeit zwei Zeilen innerhalb eines Ersetzungsblocks
    #: einander zugeordnet werden. Darunter gelten sie als unabhaengiges
    #: Loeschen und Einfuegen. 0,5 ist bewusst zurueckhaltend: eine falsche
    #: Zuordnung ist schlimmer als eine fehlende, weil sie zwei unverwandte
    #: Zeilen nebeneinanderstellt und die wortweise Verfeinerung dann
    #: sinnlosen Konfetti erzeugt.
    similarity_threshold: float = 0.5
    #: Welcher Zeilenabgleich benutzt wird.
    algorithm: str = ALGO_SEQUENCE_MATCHER

    @property
    def refine_words(self) -> bool:
        return self.intra_line_mode == INTRA_WORD

    def with_changes(self, **changes: object) -> "CompareOptions":
        """Kopie mit geaenderten Feldern - fuer die Einstellungen der Ansicht."""
        from dataclasses import replace

        return replace(self, **changes)  # type: ignore[arg-type]


#: Die Vorgabe, wenn kein Aufrufer etwas anderes sagt.
DEFAULT_OPTIONS = CompareOptions()
