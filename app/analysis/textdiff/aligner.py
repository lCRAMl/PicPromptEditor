"""Ausrichtung: aus Operationen werden Zeilen, die nebeneinander stehen.

Das ist die Stufe, um die es beim ganzen Umbau geht. Der naive Ansatz -
links[0] gegen rechts[0], links[1] gegen rechts[1] - ist hier ausdruecklich
verboten. Fehlt links eine Zeile, verschoebe er ab dieser Stelle **alles**
Weitere gegeneinander, und ab der zweiten Abweichung waere die Anzeige wertlos.

Drei Regeln bestimmen das Ergebnis:

**1. Gleiche Zeilen stehen nebeneinander.** Dafuer sorgt bereits die
Operationsliste aus `sequence.py`.

**2. Fehlende Zeilen bekommen einen Platzhalter.** Er ist keine Leerzeile,
sondern die Auskunft "hier gibt es auf dieser Seite nichts".

**3. Innerhalb eines Ersetzungsblocks werden aehnliche Zeilen einander
zugeordnet.** Nicht stur der Reihe nach. Der Unterschied:

    Speed := 100;      Speed := 120;          Speed := 100;   Speed := 120;
    Enable := TRUE;    Ramp := 50;            <Platzhalter>   Ramp := 50;
                       Enable := TRUE;        Enable := TRUE; Enable := TRUE;

    stur der Reihe nach            nach Aehnlichkeit

Links ist "Enable := TRUE;" gegen "Ramp := 50;" gestellt - zwei Zeilen, die
nichts miteinander zu tun haben. Die wortweise Verfeinerung erzeugt darauf
Konfetti. Rechts steht, was zusammengehoert.

Die Zuordnung ist **kreuzungsfrei**: Paare duerfen sich nicht ueberholen, sonst
liessen sie sich nicht untereinander darstellen. Erreicht wird das durch
Rekursion - das beste Paar wird gesetzt, und die Bereiche davor und dahinter
werden unabhaengig voneinander genauso behandelt. Eine Zuordnung ueber das
gesetzte Paar hinweg ist damit konstruktionsbedingt unmoeglich.
"""

from __future__ import annotations

from app.analysis.textdiff import intraline
from app.analysis.textdiff.model import AlignedRow, DiffStatus, SourceLine
from app.analysis.textdiff.normalize import comparison_key
from app.analysis.textdiff.options import CompareOptions
from app.analysis.textdiff.sequence import DiffOp, DiffStrategy, OpKind

#: Obergrenze fuer die Aehnlichkeitssuche innerhalb eines Ersetzungsblocks.
#: Die Suche ist quadratisch; bei 300 gegen 300 Zeilen waeren das 90.000
#: Zeilenvergleiche fuer einen einzigen Block. So grosse Bloecke entstehen nur,
#: wenn zwei voellig unverwandte Prompts verglichen werden - und dort gibt es
#: ohnehin nichts sinnvoll zuzuordnen. Darueber wird der Reihe nach gepaart.
MAX_PAIRING_CELLS = 40_000

#: Kuerzere Zeilen werden nicht auf Aehnlichkeit geprueft. "ja" und "nein"
#: haben nach SequenceMatcher eine Aehnlichkeit von 0,33 - genug, um bei
#: niedriger Schwelle zusammengespannt zu werden, und doch offensichtlich
#: unsinnig.
MIN_SIMILAR_LENGTH = 3

#: Mindestlaenge fuer die Erkennung verschobener Bloecke. Ohne sie gilt jede
#: Leerzeile als von ueberall nach ueberall verschoben.
MIN_MOVED_LENGTH = 8


def align_lines(
    left: tuple[SourceLine, ...],
    right: tuple[SourceLine, ...],
    options: CompareOptions,
    strategy: DiffStrategy,
) -> list[AlignedRow]:
    """Baut die gemeinsame Zeilenstruktur beider Seiten."""
    schluessel_links = [comparison_key(line, options) for line in left]
    schluessel_rechts = [comparison_key(line, options) for line in right]

    paare: list[tuple[SourceLine | None, SourceLine | None]] = []
    for op in strategy.opcodes(schluessel_links, schluessel_rechts):
        paare.extend(_pairs_for_op(op, left, right, options))

    zeilen = [
        _build_row(index, links, rechts, options)
        for index, (links, rechts) in enumerate(paare)
    ]
    if options.detect_moved:
        zeilen = _mark_moved(zeilen)
    return zeilen


# Operationen ------------------------------------------------------------------


def _pairs_for_op(
    op: DiffOp,
    left: tuple[SourceLine, ...],
    right: tuple[SourceLine, ...],
    options: CompareOptions,
) -> list[tuple[SourceLine | None, SourceLine | None]]:
    if op.kind is OpKind.EQUAL:
        return [
            (left[op.left_start + n], right[op.right_start + n])
            for n in range(op.left_length)
        ]
    if op.kind is OpKind.DELETE:
        return [(line, None) for line in left[op.left_start : op.left_end]]
    if op.kind is OpKind.INSERT:
        return [(None, line) for line in right[op.right_start : op.right_end]]

    return _align_replace(
        list(left[op.left_start : op.left_end]),
        list(right[op.right_start : op.right_end]),
        options,
    )


def _align_replace(
    links: list[SourceLine],
    rechts: list[SourceLine],
    options: CompareOptions,
) -> list[tuple[SourceLine | None, SourceLine | None]]:
    """Zuordnung innerhalb eines Ersetzungsblocks, nach Aehnlichkeit.

    Rekursiv ueber das jeweils beste Paar. Findet sich keins, das die Schwelle
    erreicht, wird der Reihe nach gepaart und die kuerzere Seite mit
    Platzhaltern aufgefuellt - so wie WinMerge es macht.

    Der Reihe nach zu paaren ist NICHT der naive Ansatz aus §4 des Auftrags.
    Der Unterschied ist der Bereich: dort ging es darum, zwei ganze Dateien
    nach Zeilennummer gegenueberzustellen, was ab der ersten Einfuegung alles
    Weitere verschiebt. Hier ist der Bereich ein einzelner Ersetzungsblock,
    dessen Grenzen der Sequenzabgleich bereits richtig gesetzt hat. Innerhalb
    dieser Grenzen ist die Reihenfolge die einzige Auskunft, die es noch gibt.

    Damit daraus keine Fehlinformation wird, unterbleibt bei unaehnlichen
    Paaren die wortweise Verfeinerung - siehe `_worth_refining`. Die Zeile
    gilt dann als geaendert, ohne dass einzelne Woerter markiert werden, die
    nichts miteinander zu tun haben.
    """
    if not links:
        return [(None, zeile) for zeile in rechts]
    if not rechts:
        return [(zeile, None) for zeile in links]

    if len(links) * len(rechts) > MAX_PAIRING_CELLS:
        return _pair_positionally(links, rechts)

    treffer = _best_pair(links, rechts, options)
    if treffer is None:
        return _pair_positionally(links, rechts)

    i, j = treffer
    return (
        _align_replace(links[:i], rechts[:j], options)
        + [(links[i], rechts[j])]
        + _align_replace(links[i + 1 :], rechts[j + 1 :], options)
    )


def _best_pair(
    links: list[SourceLine], rechts: list[SourceLine], options: CompareOptions
) -> tuple[int, int] | None:
    """Das aehnlichste Zeilenpaar des Blocks, oder None.

    Exakte Treffer haben ausdruecklich Vorrang: sobald zwei Zeilen in ihrer
    Vergleichsform uebereinstimmen, wird nicht weitergesucht. Eine nur aehnliche
    Zeile darf eine gleiche niemals verdraengen - §9 des Auftrags besteht darauf,
    und ohne diese Regel haengt das Ergebnis von der Reihenfolge der Suche ab.

    Bei Gleichstand gewinnt das Paar, das dichter an der Diagonalen liegt. Sonst
    entschiede die Aufzaehlungsreihenfolge, und zwei gleich gute Zuordnungen
    lieferten je nach Blockgroesse ein anderes Bild.
    """
    bestes: tuple[int, int] | None = None
    bester_wert = 0.0
    beste_entfernung = 0

    for i, links_zeile in enumerate(links):
        for j, rechts_zeile in enumerate(rechts):
            if links_zeile.normalized_text == rechts_zeile.normalized_text:
                return (i, j)

            if (
                len(links_zeile.normalized_text) < MIN_SIMILAR_LENGTH
                or len(rechts_zeile.normalized_text) < MIN_SIMILAR_LENGTH
            ):
                continue

            wert = intraline.similarity(
                links_zeile.normalized_text, rechts_zeile.normalized_text
            )
            if wert < options.similarity_threshold:
                continue

            entfernung = abs(i - j)
            if wert > bester_wert or (wert == bester_wert and entfernung < beste_entfernung):
                bestes, bester_wert, beste_entfernung = (i, j), wert, entfernung

    return bestes


def _pair_positionally(
    links: list[SourceLine], rechts: list[SourceLine]
) -> list[tuple[SourceLine | None, SourceLine | None]]:
    """Der Reihe nach paaren, die kuerzere Seite mit Platzhaltern auffuellen.

    Zwei Wege fuehren hierher: ein Block, in dem keine zwei Zeilen einander
    aehnlich genug sind, und ein Block, der zu gross fuer die
    Aehnlichkeitssuche ist. In beiden Faellen gibt es nichts sinnvoll
    zuzuordnen, und die Reihenfolge ist die einzige verbliebene Auskunft.
    """
    hoehe = max(len(links), len(rechts))
    return [
        (
            links[n] if n < len(links) else None,
            rechts[n] if n < len(rechts) else None,
        )
        for n in range(hoehe)
    ]


# Zeilen -----------------------------------------------------------------------


def _build_row(
    index: int,
    links: SourceLine | None,
    rechts: SourceLine | None,
    options: CompareOptions,
) -> AlignedRow:
    status = _status_for(links, rechts, options)

    linke_teile: tuple = ()
    rechte_teile: tuple = ()
    if (
        links is not None
        and rechts is not None
        and status in (DiffStatus.CHANGED, DiffStatus.IGNORED)
        and _worth_refining(links.text, rechts.text, options)
    ):
        linke_teile, rechte_teile = intraline.segments_for_pair(
            links.text, rechts.text, options
        )

    return AlignedRow(
        display_index=index,
        left_line=links,
        right_line=rechts,
        status=status,
        left_segments=linke_teile,
        right_segments=rechte_teile,
    )


def _worth_refining(links: str, rechts: str, options: CompareOptions) -> bool:
    """Ob die wortweise Verfeinerung dieser beiden Zeilen etwas aussagt.

    Zwei Zeilen, die nur deshalb nebeneinanderstehen, weil sie an derselben
    Stelle eines Ersetzungsblocks lagen, haben keine gemeinsamen Woerter - nur
    zufaellige Uebereinstimmungen in Artikeln und Satzzeichen. Sie zu
    verfeinern erzeugt Konfetti: ueber die ganze Zeile verstreute
    Markierungen, die nichts bedeuten und die echte Auskunft "diese Zeile ist
    anders" ueberdecken.

    Sehr lange Zeilen kommen ohne Aehnlichkeitsrechnung durch. Nicht aus
    Grosszuegigkeit, sondern weil `similarity` dort selbst quadratisch waere -
    und `segments_for_pair` sie ohnehin nur flaechig markiert.
    """
    if max(len(links), len(rechts)) > intraline.MAX_REFINE_LENGTH:
        return True
    return intraline.similarity(links, rechts) >= options.similarity_threshold


def _status_for(
    links: SourceLine | None, rechts: SourceLine | None, options: CompareOptions
) -> DiffStatus:
    """Der Zustand einer ausgerichteten Zeile.

    Entschieden wird ueber den VERGLEICHSSCHLUESSEL, nicht ueber den Rohtext.
    Nur so wirken die Optionen dort, wo sie wirken sollen - und nur so faellt
    ein Unterschied auf, den es ausschliesslich im Zeilenende gibt: bei
    eingeschaltetem `compare_eol` sind zwei Zeilen mit demselben Text, aber
    verschiedenen Enden, nicht gleich. Ein Vergleich auf `links.text ==
    rechts.text` haette das uebersehen und die Option wirkungslos gemacht.

    Der interessante Fall ist IGNORED. Er entsteht, wenn zwei Zeilen nach den
    eingestellten Optionen als gleich gelten, ihr Text sich aber tatsaechlich
    unterscheidet - etwa nur in der Einrueckung bei eingeschaltetem "Leerraum
    ignorieren". Der Vergleich zaehlt das nicht als Unterschied, aber die
    Oberflaeche zeigt es trotzdem. Es als EQUAL auszugeben hiesse, dem
    Benutzer eine Uebereinstimmung vorzuspiegeln, die im Text nicht steht.
    """
    if links is None and rechts is None:
        return DiffStatus.EQUAL  # kommt nicht vor, aber definiert
    if rechts is None:
        return (
            DiffStatus.IGNORED
            if options.ignore_blank_lines and links.is_blank
            else DiffStatus.REMOVED
        )
    if links is None:
        return (
            DiffStatus.IGNORED
            if options.ignore_blank_lines and rechts.is_blank
            else DiffStatus.ADDED
        )

    if comparison_key(links, options) != comparison_key(rechts, options):
        return DiffStatus.CHANGED
    if links.text == rechts.text:
        return DiffStatus.EQUAL
    # Gleich nach den Optionen, verschieden im Text.
    return DiffStatus.IGNORED


# Verschobene Bloecke ----------------------------------------------------------


def _mark_moved(zeilen: list[AlignedRow]) -> list[AlignedRow]:
    """Erkennt Zeilen, die nur an eine andere Stelle gerutscht sind.

    Bewusst zurueckhaltend: nur eindeutige Treffer zaehlen. Kommt derselbe Text
    mehrfach als entfernt oder mehrfach als hinzugefuegt vor, bleibt alles wie
    es ist - bei Prompts mit wiederkehrenden Textbausteinen waere jede andere
    Regel geraten.

    Die Erkennung ist per Vorgabe abgeschaltet (§13 des Auftrags verlangt das
    ausdruecklich).
    """
    from dataclasses import replace

    entfernt: dict[str, list[int]] = {}
    hinzu: dict[str, list[int]] = {}
    for zeile in zeilen:
        if zeile.status is DiffStatus.REMOVED and zeile.left_line is not None:
            text = zeile.left_line.normalized_text
            if len(text) >= MIN_MOVED_LENGTH:
                entfernt.setdefault(text, []).append(zeile.display_index)
        elif zeile.status is DiffStatus.ADDED and zeile.right_line is not None:
            text = zeile.right_line.normalized_text
            if len(text) >= MIN_MOVED_LENGTH:
                hinzu.setdefault(text, []).append(zeile.display_index)

    partner: dict[int, int] = {}
    for text, stellen in entfernt.items():
        gegen = hinzu.get(text, [])
        if len(stellen) != 1 or len(gegen) != 1:
            continue
        partner[stellen[0]] = gegen[0]
        partner[gegen[0]] = stellen[0]

    if not partner:
        return zeilen
    return [
        replace(zeile, status=DiffStatus.MOVED, moved_partner=partner[zeile.display_index])
        if zeile.display_index in partner
        else zeile
        for zeile in zeilen
    ]
