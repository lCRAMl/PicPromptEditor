"""Datenstrukturen der Parsing-Pipeline.

Ein geparster Prompt zerfaellt in drei Ebenen:

    Sektion   ein Block des Dokuments (Prosa, Tag-Liste, Parameterblock,
              Ueberschrift). Bestimmt, welche Segmentart folgt und ob die
              Segmente negativ sind.
    Segment   die atomare Analyseeinheit. Bei Prosa ein Satz, bei einer
              Tag-Liste ein Komma-Element, bei Parametern ein Schluessel-Wert-Paar.
    Parameter zusaetzlich strukturiert erfasste Schluessel-Wert-Paare
              (Kamera, Objektiv, Blende, ISO ...).

Jedes Segment traegt seine Zeichenpositionen im Originaltext. Ohne sie liesse
sich ein Suchtreffer nicht auf seine Stelle zurueckbilden, und die tokenbasierte
Hervorhebung der Vergleichsansicht (Spezifikation 01) waere nicht umsetzbar.

PARSE_VERSION wird erhoeht, wenn sich Segmentierung oder Normalisierung aendern.
Prompts mit kleinerer Version gelten dann automatisch als neu zu parsen, ohne
dass Millionen Dirty-Flags geschrieben werden muessen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

PARSE_VERSION = 1


class SectionKind(str, Enum):
    PROSE = "prose"
    TAG_LIST = "tag_list"
    PARAMS = "params"
    HEADER = "header"
    UNKNOWN = "unknown"


class SegmentKind(str, Enum):
    SENTENCE = "sentence"
    TAG = "tag"
    PARAM = "param"
    HEADER = "header"
    WORD = "word"


@dataclass(frozen=True, slots=True)
class Section:
    ordinal: int
    kind: SectionKind
    label: str | None
    label_norm: str | None
    negative: bool
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class Segment:
    """Ein Segment mit Original- und Vergleichsform.

    text_raw ist der Ausschnitt aus dem Originaltext, text_norm die
    Vergleichsform. Beide werden gebraucht: die Anzeige zeigt das Original,
    Haeufigkeit und Aehnlichkeit rechnen mit der Normalform.
    """

    kind: SegmentKind
    text_raw: str
    text_norm: str
    text_hash: str
    position: int
    section_ordinal: int
    negative: bool = False
    weight: float = 1.0
    char_start: int = 0
    char_end: int = 0

    @property
    def word_count(self) -> int:
        return len(self.text_norm.split())


@dataclass(frozen=True, slots=True)
class PromptParam:
    key_norm: str
    key_raw: str
    value_text: str
    value_num: float | None
    unit: str | None
    section_ordinal: int
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class ParsedPrompt:
    format: str
    sections: tuple[Section, ...] = ()
    segments: tuple[Segment, ...] = ()
    params: tuple[PromptParam, ...] = ()
    word_count: int = 0
    parse_version: int = PARSE_VERSION

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    def segments_of(self, kind: SegmentKind) -> tuple[Segment, ...]:
        return tuple(s for s in self.segments if s.kind is kind)

    @property
    def positive_segments(self) -> tuple[Segment, ...]:
        return tuple(s for s in self.segments if not s.negative)

    @property
    def negative_segments(self) -> tuple[Segment, ...]:
        return tuple(s for s in self.segments if s.negative)

    def to_json(self) -> str:
        """Kompakte Struktur fuer prompts.parse_json.

        Editor und Vergleichsansicht lesen daraus Spannen und Segmentgrenzen,
        ohne die relationalen Tabellen abzufragen.
        """
        import json

        return json.dumps(
            {
                "v": self.parse_version,
                "format": self.format,
                "sections": [
                    {
                        "o": s.ordinal,
                        "k": s.kind.value,
                        "l": s.label,
                        "n": int(s.negative),
                        "a": s.char_start,
                        "b": s.char_end,
                    }
                    for s in self.sections
                ],
                "segments": [
                    {
                        "k": s.kind.value,
                        "t": s.text_raw,
                        "h": s.text_hash,
                        "p": s.position,
                        "s": s.section_ordinal,
                        "n": int(s.negative),
                        "w": s.weight,
                        "a": s.char_start,
                        "b": s.char_end,
                    }
                    for s in self.segments
                ],
                "params": [
                    {
                        "k": p.key_norm,
                        "r": p.key_raw,
                        "v": p.value_text,
                        "n": p.value_num,
                        "u": p.unit,
                        "a": p.char_start,
                        "b": p.char_end,
                    }
                    for p in self.params
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass
class ParseStats:
    """Zaehlwerk eines Parserlaufs, fuer Fortschritt und Protokoll."""

    prompts: int = 0
    segments: int = 0
    params: int = 0
    failed: int = 0
    duration: float = 0.0
    formats: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        if not self.prompts:
            return "nichts zu parsen"
        parts = [str(self.prompts) + " Prompts", str(self.segments) + " Segmente"]
        if self.params:
            parts.append(str(self.params) + " Parameter")
        if self.failed:
            parts.append(str(self.failed) + " Fehler")
        return ", ".join(parts)
