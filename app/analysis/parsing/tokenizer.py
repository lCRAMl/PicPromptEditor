"""Gewichtungen und Sonderkonstrukte in Tag-Segmenten.

Spezifikation 04 verlangt, dass Gewichtungsinformationen nicht verlorengehen.
Die Konventionen stammen aus den gaengigen Bildgeneratoren:

    (x)          Faktor 1.1
    ((x))        Faktor 1.1 * 1.1 = 1.21, beliebig tief geschachtelt
    [x]          Faktor 1/1.1, ebenfalls schachtelbar
    (x:1.2)      ausdruecklicher Faktor
    <lora:n:0.8> Modelladapter, kein Bildinhalt
    \\(          maskierte Klammer, gehoert zum Text

Bewusst ein kleiner rekursiver Parser statt einer Regex-Kette: bei
Verschachtelung wird jede Regex-Loesung entweder falsch oder unlesbar. Der
Parser laeuft einmal ueber die Zeichen und traegt den aktuellen Faktor mit.

Das Primaerformat dieses Projekts nutzt diese Konstrukte nicht - die Prompts
sind Prosa. Das Datenmodell haelt sie trotzdem vor, weil Tag-Listen im selben
Prompt vorkommen und aeltere Dateien anders aussehen koennen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ROUND_STEP = 1.1
SQUARE_STEP = 1 / 1.1

_LORA = re.compile(r"<\s*(lora|lyco|hypernet|embedding)\s*:\s*([^:>]+?)\s*(?::\s*([-\d.]+)\s*)?>", re.I)
_EXPLICIT_WEIGHT = re.compile(r"^(.*?):\s*(-?\d+(?:\.\d+)?)$", re.S)


@dataclass(frozen=True, slots=True)
class WeightedText:
    text: str
    weight: float


@dataclass(frozen=True, slots=True)
class Adapter:
    """Ein LoRA- oder aehnlicher Adapterverweis."""

    kind: str
    name: str
    weight: float


def extract_adapters(text: str) -> tuple[str, tuple[Adapter, ...]]:
    """Entfernt Adapterverweise aus dem Text und liefert sie getrennt zurueck.

    Ein LoRA-Verweis beschreibt kein Bildmerkmal. Bliebe er im Segment, wuerden
    zwei sonst identische Prompts allein wegen unterschiedlicher Adaptergewichte
    als verschieden gelten.
    """
    adapters: list[Adapter] = []

    def replace(match: re.Match[str]) -> str:
        raw_weight = match.group(3)
        try:
            weight = float(raw_weight) if raw_weight is not None else 1.0
        except ValueError:
            weight = 1.0
        adapters.append(Adapter(match.group(1).lower(), match.group(2).strip(), weight))
        return " "

    cleaned = _LORA.sub(replace, text)
    return cleaned, tuple(adapters)


def strip_weights(text: str) -> WeightedText:
    """Loest Klammergewichte auf und liefert den reinen Text mit Gesamtfaktor.

    Enthaelt ein Segment mehrere unterschiedlich gewichtete Teile, gilt der
    Faktor des laengsten Teils. Ein einzelner Zahlenwert je Segment ist eine
    Vereinfachung, aber die Alternative waere, das Segment aufzuspalten - und
    damit die Segmentgrenzen des Benutzers zu ignorieren.
    """
    pieces: list[tuple[str, float]] = []
    buffer: list[str] = []
    factors: list[float] = []
    index = 0
    length = len(text)

    def flush() -> None:
        chunk = "".join(buffer).strip()
        buffer.clear()
        if chunk:
            factor = 1.0
            for value in factors:
                factor *= value
            pieces.append((chunk, factor))

    while index < length:
        char = text[index]

        if char == "\\" and index + 1 < length and text[index + 1] in "()[]":
            buffer.append(text[index + 1])
            index += 2
            continue

        if char in "([":
            flush()
            factors.append(ROUND_STEP if char == "(" else SQUARE_STEP)
            index += 1
            continue

        if char in ")]":
            chunk = "".join(buffer).strip()
            buffer.clear()
            explicit = _EXPLICIT_WEIGHT.match(chunk) if char == ")" else None
            if explicit is not None:
                inner = explicit.group(1).strip()
                try:
                    value = float(explicit.group(2))
                except ValueError:
                    value = 1.0
                outer = 1.0
                for factor in factors[:-1]:
                    outer *= factor
                if inner:
                    pieces.append((inner, outer * value))
            elif chunk:
                factor = 1.0
                for value in factors:
                    factor *= value
                pieces.append((chunk, factor))
            if factors:
                factors.pop()
            index += 1
            continue

        buffer.append(char)
        index += 1

    flush()

    if not pieces:
        return WeightedText("", 1.0)

    combined = " ".join(chunk for chunk, _ in pieces).strip()
    combined = re.sub(r"\s+", " ", combined)
    dominant = max(pieces, key=lambda entry: len(entry[0]))
    return WeightedText(combined, round(dominant[1], 4))


def analyse_tag(text: str) -> tuple[WeightedText, tuple[Adapter, ...]]:
    """Vollstaendige Analyse eines Tag-Segments."""
    cleaned, adapters = extract_adapters(text)
    return strip_weights(cleaned), adapters
