"""Text in Quellzeilen zerlegen und die Vergleichsform bilden.

Zwei Aufgaben, die zusammengehoeren:

**Zerlegen.** Aus einem Text werden SourceLine-Objekte. Jede kennt ihre
1-basierte Zeilennummer, ihren Originaltext ohne Zeilenende und das Zeilenende,
das dort tatsaechlich stand. Letzteres wird gebraucht, wenn "Zeilenenden
vergleichen" eingeschaltet ist und beim Zurueckschreiben, damit eine Datei mit
Windows-Enden nicht stillschweigend auf Unix-Enden umgestellt wird.

**Normalisieren.** Die Vergleichsform entsteht aus den Optionen. Sie entscheidet
ueber gleich und ungleich und wird nirgends angezeigt.

Der Text kommt in dieser Anwendung aus der Datenbank und ist damit bereits
dekodiert - Encoding-Erkennung, BOM und UTF-16 erledigt `app/core/text.py`
beim Einlesen der Datei, lange bevor ein Vergleich stattfindet. Was hier bleibt,
ist die Behandlung der Zeilenenden.
"""

from __future__ import annotations

import re

from app.analysis.textdiff.model import SourceLine
from app.analysis.textdiff.options import CompareOptions

LF = chr(10)
CR = chr(13)
CRLF = CR + LF

_WHITESPACE_RUN = re.compile(r"[ \t\f\v]+")
_ALL_WHITESPACE = re.compile(r"\s+")

#: Zeilenenden in der Reihenfolge, in der geprueft werden muss: CRLF vor CR,
#: sonst wird ein CRLF als einzelnes CR gelesen und das LF beginnt eine
#: Geisterzeile.
_ENDINGS = (CRLF, LF, CR)


def split_lines(text: str, options: CompareOptions) -> tuple[SourceLine, ...]:
    """Zerlegt einen Text in Quellzeilen.

    Ein leerer Text hat null Zeilen, nicht eine leere. Der Unterschied faellt
    beim Vergleich einer leeren gegen eine gefuellte Datei auf: sonst stuende
    einer echten ersten Zeile eine erfundene Leerzeile gegenueber statt eines
    Platzhalters.

    Ein abschliessendes Zeilenende erzeugt ebenfalls keine zusaetzliche Zeile.
    "A\\nB\\n" hat zwei Zeilen, nicht drei.
    """
    if not text:
        return ()

    zeilen: list[SourceLine] = []
    position = 0
    nummer = 1
    laenge = len(text)

    while position < laenge:
        ende, eol = _find_ending(text, position)
        roh = text[position:ende]
        zeilen.append(
            SourceLine(
                line_number=nummer,
                text=roh,
                normalized_text=normalize(roh, options),
                eol=eol,
            )
        )
        nummer += 1
        position = ende + len(eol)
        if not eol:
            break

    return tuple(zeilen)


def _find_ending(text: str, start: int) -> tuple[int, str]:
    """Ende der Zeile ab `start` und das dort stehende Zeilenende.

    Liefert (Position des Zeilenendes, Zeilenende). Steht keines mehr da -
    letzte Zeile ohne abschliessenden Umbruch - ist das Zeilenende leer.
    """
    kandidaten = [
        (text.find(eol, start), eol) for eol in _ENDINGS
    ]
    treffer = [(pos, eol) for pos, eol in kandidaten if pos != -1]
    if not treffer:
        return len(text), ""
    # Kleinste Position gewinnt; bei Gleichstand das laengste Zeilenende,
    # damit CRLF nicht als CR gelesen wird.
    treffer.sort(key=lambda eintrag: (eintrag[0], -len(eintrag[1])))
    return treffer[0]


def normalize(text: str, options: CompareOptions) -> str:
    """Vergleichsform einer Zeile.

    Die Reihenfolge ist festgelegt: erst Leerraum, dann Schreibweise. Umgekehrt
    waere das Ergebnis dasselbe, aber die Regel muss eine sein, damit zwei
    Aufrufe mit denselben Optionen garantiert dasselbe liefern.
    """
    if options.ignore_whitespace:
        text = _ALL_WHITESPACE.sub("", text)
    elif options.ignore_whitespace_changes:
        text = _WHITESPACE_RUN.sub(" ", text).strip()

    if options.ignore_case:
        text = text.casefold()

    return text


def comparison_key(line: SourceLine, options: CompareOptions) -> str:
    """Der Schluessel, nach dem zwei Zeilen als gleich gelten.

    Bei eingeschaltetem `compare_eol` geht das Zeilenende mit ein. Sonst nicht -
    und das ist die Vorgabe, weil die Bibliothek Promptdateien mit Windows- und
    mit Unix-Enden nebeneinander enthaelt. Ohne diese Ausnahme meldete der
    Vergleich zweier inhaltsgleicher Dateien jede einzelne Zeile als geaendert.
    """
    if not options.compare_eol:
        return line.normalized_text
    return line.normalized_text + "\x00" + line.eol


def is_ignorable(line: SourceLine | None, options: CompareOptions) -> bool:
    """Ob diese Zeile durch eine Option unerheblich geworden ist.

    Nur Leerzeilen, und nur bei eingeschalteter Option. Ein Platzhalter (None)
    ist nie ignorierbar - er ist gar keine Zeile.
    """
    if line is None:
        return False
    return options.ignore_blank_lines and line.is_blank
