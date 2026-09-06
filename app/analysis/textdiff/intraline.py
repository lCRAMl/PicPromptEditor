"""Unterschiede INNERHALB einer Zeile - Wort- oder Zeichenebene.

Die zweite Hervorhebungsebene. Die erste sagt "diese Zeile ist anders", diese
sagt "genau diese drei Woerter sind anders". Ohne sie muesste der Benutzer zwei
lange Saetze selbst vergleichen - also genau die Arbeit tun, die das Werkzeug
ihm abnehmen soll.

Geliefert wird eine **luecklose Ueberdeckung** der Zeile: jedes Zeichen liegt in
genau einem Abschnitt, auch die unveraenderten. Der Renderer laeuft die
Abschnitte einmal durch und faerbt, was nicht EQUAL ist. Das ist weniger
fehleranfaellig, als ihm nur die Unterschiede zu geben und die Luecken selbst
ausrechnen zu lassen.

Alle Positionen zaehlen **ab Zeilenanfang**, nicht ab Dokumentanfang. Sie
ueberstehen damit jede Ausrichtung unveraendert - genau das war der wiederkehrende
Fehler des Vorgaengers, dessen Spannen zweimal umgerechnet werden mussten.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.analysis.textdiff.model import DiffSegment, SegmentStatus
from app.analysis.textdiff.options import INTRA_CHARACTER, CompareOptions

#: Tokenisierung fuer den Wortmodus. Drei Klassen, jede fuer sich:
#:
#:   \w+          Wort einschliesslich Ziffern und Unterstrich
#:   \s+          Leerraum am Stueck
#:   [^\w\s]      ein einzelnes Satz- oder Sonderzeichen
#:
#: Die dritte Klasse ist der Grund, warum "foo(bar, 100)" vernuenftig zerfaellt:
#: Klammern und Komma werden eigene Tokens, statt am Wort zu kleben. Ohne sie
#: gaelte "foo(bar," als ein Token und eine Aenderung von 100 auf 120 faerbte
#: den halben Ausdruck.
_TOKENS = re.compile(r"\w+|\s+|[^\w\s]")

#: Ab dieser Zeilenlaenge wird nicht mehr verfeinert. Der Zeichenmodus ist
#: quadratisch im schlechtesten Fall; bei einer Zeile aus 20.000 Zeichen - in
#: dieser Bibliothek durchaus vorhanden, wenn ein Prompt ohne Umbrueche
#: gespeichert wurde - dauert das laenger als der ganze uebrige Vergleich.
#: Die Zeile bleibt dann flaechig als geaendert markiert, was richtig, nur
#: weniger genau ist.
MAX_REFINE_LENGTH = 8000


def segments_for_pair(
    left: str, right: str, options: CompareOptions
) -> tuple[tuple[DiffSegment, ...], tuple[DiffSegment, ...]]:
    """Die Abschnitte beider Seiten eines geaenderten Zeilenpaares.

    Bei Gleichheit liefert die Funktion je einen EQUAL-Abschnitt ueber die ganze
    Zeile - nicht etwa gar nichts. Der Renderer soll sich nicht mit zwei
    Sonderfaellen befassen muessen.
    """
    if left == right:
        return _whole(left, SegmentStatus.EQUAL), _whole(right, SegmentStatus.EQUAL)

    if len(left) > MAX_REFINE_LENGTH or len(right) > MAX_REFINE_LENGTH:
        return (
            _whole(left, SegmentStatus.CHANGED),
            _whole(right, SegmentStatus.CHANGED),
        )

    if options.intra_line_mode == INTRA_CHARACTER:
        links_teile = list(left)
        rechts_teile = list(right)
    else:
        links_teile = _TOKENS.findall(left)
        rechts_teile = _TOKENS.findall(right)

    links_roh: list[tuple[int, int, SegmentStatus]] = []
    rechts_roh: list[tuple[int, int, SegmentStatus]] = []

    matcher = SequenceMatcher(
        None,
        _compare_forms(links_teile, options),
        _compare_forms(rechts_teile, options),
        autojunk=False,
    )
    links_pos = _offsets(links_teile)
    rechts_pos = _offsets(rechts_teile)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            _collect(links_roh, links_pos, i1, i2, SegmentStatus.EQUAL)
            _collect(rechts_roh, rechts_pos, j1, j2, SegmentStatus.EQUAL)
            continue
        if tag == "replace":
            _collect(links_roh, links_pos, i1, i2, SegmentStatus.CHANGED)
            _collect(rechts_roh, rechts_pos, j1, j2, SegmentStatus.CHANGED)
            continue
        if tag == "delete":
            # Nur links vorhanden. Eigener Zustand, damit die Oberflaeche das
            # Entfernte von einem Austausch unterscheiden kann - §13 des
            # Auftrags sieht dafuer eine eigene Farbe vor.
            _collect(links_roh, links_pos, i1, i2, SegmentStatus.REMOVED)
            continue
        if tag == "insert":
            _collect(rechts_roh, rechts_pos, j1, j2, SegmentStatus.ADDED)

    return (
        _finish(left, links_roh, options),
        _finish(right, rechts_roh, options),
    )


def similarity(left: str, right: str) -> float:
    """Aehnlichkeit zweier Zeilen zwischen 0 und 1.

    Grundlage fuer die Zuordnung innerhalb eines Ersetzungsblocks. Zwei
    Vorstufen sparen die teure Rechnung:

    * gleiche Zeilen sind 1,0 - ohne Vergleich,
    * `real_quick_ratio` und `quick_ratio` sind obere Schranken. Liegt schon die
      Schranke unter dem, was noch als Zuordnung in Frage kaeme, ist die genaue
      Zahl gleichgueltig.

    Der Wert selbst kommt aus SequenceMatcher ueber die ZEICHEN. Ueber Woerter
    waere er grober, und gerade bei Prompts unterscheiden sich verwandte Zeilen
    oft nur in einer Zahl.
    """
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0

    matcher = SequenceMatcher(None, left, right, autojunk=False)
    if matcher.real_quick_ratio() < _CUTOFF or matcher.quick_ratio() < _CUTOFF:
        return 0.0
    return matcher.ratio()


#: Unterhalb dieses Werts lohnt die genaue Rechnung nicht. Bewusst niedriger als
#: die Vorgabe von `similarity_threshold`, damit eine hoehergesetzte Schwelle
#: nicht ploetzlich Zuordnungen findet, die eine niedrigere verworfen hat.
_CUTOFF = 0.2


# Intern -----------------------------------------------------------------------


def _whole(text: str, status: SegmentStatus) -> tuple[DiffSegment, ...]:
    if not text:
        return ()
    return (DiffSegment(0, len(text), text, status),)


def _offsets(teile: list[str]) -> list[tuple[int, int]]:
    """Anfang und Ende jedes Tokens in der Zeile."""
    stellen: list[tuple[int, int]] = []
    position = 0
    for teil in teile:
        stellen.append((position, position + len(teil)))
        position += len(teil)
    return stellen


def _compare_forms(teile: list[str], options: CompareOptions) -> list[str]:
    """Die Form, in der die Tokens verglichen werden.

    Nur Gross- und Kleinschreibung wird hier beruecksichtigt. Die
    Leerraum-Optionen bleiben aussen vor: sie wirken auf Zeilenebene und
    entscheiden, ob eine Zeile ueberhaupt als geaendert gilt. Innerhalb einer
    bereits als geaendert erkannten Zeile soll die Hervorhebung zeigen, was
    tatsaechlich dasteht.
    """
    if not options.ignore_case:
        return teile
    return [teil.casefold() for teil in teile]


def _collect(
    aus: list[tuple[int, int, SegmentStatus]],
    stellen: list[tuple[int, int]],
    von: int,
    bis: int,
    status: SegmentStatus,
) -> None:
    if von >= bis:
        return
    aus.append((stellen[von][0], stellen[bis - 1][1], status))


def _finish(
    text: str,
    roh: list[tuple[int, int, SegmentStatus]],
    options: CompareOptions,
) -> tuple[DiffSegment, ...]:
    """Aus den Rohbereichen die endgueltige, lueckenlose Ueberdeckung.

    Zwei Aufraeumschritte, die beide der Lesbarkeit dienen:

    **Leerraum an den Raendern eines Unterschieds wandert ins EQUAL.** Sonst
    faerbt eine Aenderung von "Speed = 100" zu "Speed = 120" das Leerzeichen vor
    der Zahl mit, und die Markierung wirkt um ein Zeichen verrutscht.

    **Benachbarte Unterschiede werden zusammengezogen.** Aus "1", "0", "0" wird
    ein Bereich "100" statt dreier. Im Zeichenmodus ist das der Unterschied
    zwischen einer Markierung und einem Flimmern.
    """
    if not roh:
        return _whole(text, SegmentStatus.EQUAL)

    getrimmt = [
        eintrag
        for eintrag in (_trim(text, start, ende, status) for start, ende, status in roh)
        if eintrag is not None
    ]
    if not getrimmt:
        return _whole(text, SegmentStatus.EQUAL)

    verschmolzen: list[tuple[int, int, SegmentStatus]] = []
    for start, ende, status in getrimmt:
        if verschmolzen and verschmolzen[-1][2] is status and verschmolzen[-1][1] >= start:
            vorher = verschmolzen[-1]
            verschmolzen[-1] = (vorher[0], max(vorher[1], ende), status)
        else:
            verschmolzen.append((start, ende, status))

    # Luecken zwischen den Bereichen mit EQUAL fuellen.
    abschnitte: list[DiffSegment] = []
    position = 0
    for start, ende, status in verschmolzen:
        if start > position:
            abschnitte.append(
                DiffSegment(position, start, text[position:start], SegmentStatus.EQUAL)
            )
        abschnitte.append(DiffSegment(start, ende, text[start:ende], status))
        position = ende
    if position < len(text):
        abschnitte.append(
            DiffSegment(position, len(text), text[position:], SegmentStatus.EQUAL)
        )
    return tuple(abschnitte)


def _trim(
    text: str, start: int, ende: int, status: SegmentStatus
) -> tuple[int, int, SegmentStatus] | None:
    """Randleerraum aus einem Unterschied herausnehmen.

    EQUAL bleibt unangetastet - dort schadet Leerraum nicht, und ein
    beschnittener EQUAL-Bereich risse Loecher in die Ueberdeckung.
    """
    if status is SegmentStatus.EQUAL:
        return (start, ende, status)
    while start < ende and text[start].isspace():
        start += 1
    while ende > start and text[ende - 1].isspace():
        ende -= 1
    return (start, ende, status) if ende > start else None
