"""Robuste Textdekodierung und Normalisierung.

Die Promptdateien dieses Projekts sind UTF-8 ohne BOM und enthalten typografische
Zeichen (Bullet U+2022, Halbgeviertstrich U+2013, Geviertstrich U+2014). Werden sie
versehentlich als cp1252 gelesen, entstehen die bekannten Mojibake-Folgen. Deshalb
ist die Reihenfolge strikt: BOM, dann UTF-8 strict, und cp1252 erst dann, wenn
UTF-8 tatsaechlich scheitert. Das erkannte Encoding wird mitgefuehrt, damit ein
spaeteres Zurueckschreiben es beibehalten kann.
"""

from __future__ import annotations

import codecs
import hashlib
import re
import unicodedata
from pathlib import Path

_FALLBACKS = ("utf-8", "cp1252", "latin-1")
_WS = re.compile(r"\s+")
_TRIM_CHARS = " \t\r\n.,;:!?–—•·*-"


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Liefert (text, encoding). Wirft nie, da latin-1 jedes Byte dekodieren kann.

    Ein verbleibendes U+FEFF am Textanfang wird entfernt. Es kommt vor, dass eine
    Datei die Byte-Order-Mark doppelt traegt oder dass ein Editor sie als Zeichen
    einfuegt; unbehandelt wuerde sie am ersten Segment kleben und dieses von allen
    gleichlautenden Segmenten anderer Prompts unterscheiden.
    """
    for bom, enc in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if data.startswith(bom):
            try:
                return data.decode(enc).lstrip("﻿"), enc
            except UnicodeDecodeError:
                break
    for enc in _FALLBACKS:
        try:
            return data.decode(enc).lstrip("﻿"), enc
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace").lstrip("﻿"), "latin-1"


def read_text_file(path: str | Path) -> tuple[str, str]:
    """Liest eine Textdatei und liefert (text, encoding). Nur aus dem Filesystem-Layer."""
    from app.core.paths import long_path

    with open(long_path(path), "rb") as fh:
        return decode_bytes(fh.read())


def normalize_whitespace(text: str) -> str:
    return _WS.sub(" ", text).strip()


def normalize_segment(text: str) -> str:
    """Vergleichsform eines Prompt-Segments.

    NFKC vereinheitlicht typografische Varianten (geschweifte Apostrophe,
    Ligaturen), Whitespace wird zusammengezogen, Rand-Interpunktion entfernt und
    casefold angewandt. Ohne das Entfernen der Rand-Interpunktion gelten
    "photorealism" und "photorealism." als zwei verschiedene Segmente. Genau
    dieser Fall trat beim Test der Tag-Liste im Beispielprompt auf.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _WS.sub(" ", text).strip().strip(_TRIM_CHARS)
    return text.casefold()


def segment_hash(normalized: str) -> str:
    """Kurzschluessel eines normalisierten Segments.

    Segmente sind hier ganze Saetze und damit zu lang fuer einen sinnvollen
    UNIQUE-Index auf der Textspalte. Der Hash ist der Eindeutigkeitsschluessel.
    """
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()


def text_hash(text: str) -> str:
    """Hash des vollstaendigen Prompttextes.

    Grundlage der Analyse-Deduplizierung: Batch-Generierungen erzeugen viele
    Bilder mit identischem Prompttext. Parsing und Similarity laufen einmal pro
    Hash, nicht einmal pro Datei.
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=20).hexdigest()


def cheap_signature(size: int, mtime_ns: int) -> str:
    """Aenderungssignatur ohne Dateiinhalt.

    Ersetzt bei Bildern den Volldatei-Hash der Erstindexierung. Ein Volldurchlauf
    ueber hunderttausend Bilder waere ein Vielfaches der Bibliotheksgroesse an
    Netzwerkverkehr, ohne zusaetzliche Sicherheit fuer den Regelfall.
    """
    return str(size) + ":" + str(mtime_ns)
