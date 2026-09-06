"""Parser fuer Langform-Prosa mit Sektionen - das Primaerformat dieses Projekts.

Aufbau der realen Dateien:

    <Prosa-Absaetze, durch Leerzeilen getrennt>
    Pose variation: <Label + Prosa>
    FITMENT: <Label + Prosa>

    CAMERA & LENS SETTINGS          <- CAPS-Ueberschrift
    - Camera: Full-frame mirrorless <- Bullet-Parameter
    - Aperture: f/2.0

    STYLE & QUALITY TAGS            <- CAPS-Ueberschrift
    hyper-realistic, cinematic lighting, ...   <- Tag-Liste

    NEGATIVE PROMPT                 <- ab hier negativ
    cartoon, illustration, ...

Die Pruefreihenfolge je Zeile ist bedeutungstragend und wurde am Beispielprompt
korrigiert:

    1. Bullet mit "Schluessel: Wert"  -> Parameter
    2. kurze Zeile, fast nur Grossbuchstaben, kein Satzende -> Ueberschrift
    3. "LABEL:" gefolgt von langem Text -> beschriftete Prosa
    4. sonst Textzeile

Ohne Schritt 1 vor Schritt 2 wird eine Zeile wie "- ISO: 400-640" als
Ueberschrift erkannt, weil sie keinen Kleinbuchstaben enthaelt.
"""

from __future__ import annotations

import re

from app.analysis.parsing.base import (
    PARSE_VERSION,
    ParsedPrompt,
    PromptParam,
    Section,
    SectionKind,
    Segment,
    SegmentKind,
)
from app.analysis.parsing.tokenizer import analyse_tag
from app.core.text import normalize_segment, normalize_whitespace, segment_hash

FORMAT_ID = "prose_sectioned"

_BULLET = re.compile(r"^\s*[•·\*▪●]\s*")
_KEY_VALUE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 &/_\-]{1,40}?)\s*:\s*(\S.*)$")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")

# Ein Punkt zwischen Ziffern trennt nicht: f/2.0, 1/125s, 3:4, 100%, 30-90
# bleiben zusammen. Getrennt wird nur vor einem Grossbuchstaben oder einem
# oeffnenden Anfuehrungszeichen, oder am Zeilenende.
_SENTENCE = re.compile(
    r'(?<![0-9])(?<=[.!?])[ \t]+(?=[A-Z“"(ÄÖÜ])|(?<=[.!?])\s*\n'
)

_NUMBER = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*([A-Za-z%/]*)\s*$")

DEFAULT_NEGATIVE_MARKERS = ("NEGATIVE PROMPT", "NEGATIVE", "NEGATIVE PROMPTS")

_MIN_HEADER_UPPER_RATIO = 0.9
_MAX_HEADER_LENGTH = 60
_MIN_TAG_PARTS = 4
_MAX_TAG_AVERAGE = 45
_MAX_INLINE_PARAM_LENGTH = 80


def looks_like_header(line: str) -> bool:
    """Kurze Zeile ohne Satzende, fast ausschliesslich Grossbuchstaben."""
    text = line.strip()
    if not 2 <= len(text) <= _MAX_HEADER_LENGTH or text.endswith((".", ",")):
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(c.isupper() for c in letters) / len(letters) >= _MIN_HEADER_UPPER_RATIO


def looks_like_tag_list(text: str) -> bool:
    """Kommaliste kurzer Elemente statt eines Fliesstextes."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) < _MIN_TAG_PARTS:
        return False
    if text.count(".") > 1:
        return False
    average = sum(len(p) for p in parts) / len(parts)
    return average <= _MAX_TAG_AVERAGE and all(len(p) <= 90 for p in parts)


def split_sentences(text: str, offset: int = 0) -> list[tuple[int, int, str]]:
    """Zerlegt Fliesstext in Saetze. Liefert (start, ende, text) im Original."""
    found: list[tuple[int, int, str]] = []
    position = 0
    for match in _SENTENCE.finditer(text):
        chunk = text[position : match.start()]
        if chunk.strip():
            found.append(_trimmed_span(text, position, match.start(), offset))
        position = match.end()
    if text[position:].strip():
        found.append(_trimmed_span(text, position, len(text), offset))
    return found


def _trimmed_span(text: str, start: int, end: int, offset: int) -> tuple[int, int, str]:
    """Schneidet Rand-Whitespace ab, damit die Spanne auf den Inhalt zeigt."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return offset + start, offset + end, text[start:end]


def parse_number(value: str) -> tuple[float | None, str | None]:
    """Zerlegt "400", "2.0", "50mm" oder "85%" in Zahl und Einheit."""
    match = _NUMBER.match(value)
    if match is None:
        return None, None
    try:
        number = float(match.group(1).replace(",", "."))
    except ValueError:
        return None, None
    unit = match.group(2) or None
    return number, unit


class ProseSectionedParser:
    def __init__(self, negative_markers: tuple[str, ...] = DEFAULT_NEGATIVE_MARKERS) -> None:
        self._markers = tuple(m.upper() for m in negative_markers)

    # Oeffentlich --------------------------------------------------------------

    def parse(self, text: str) -> ParsedPrompt:
        sections: list[Section] = []
        segments: list[Segment] = []
        params: list[PromptParam] = []

        negative = False
        ordinal = 0
        position = 0
        buffer: list[str] = []
        buffer_start = 0
        pending_label: str | None = None
        block_label: str | None = None

        def flush_block(end: int) -> None:
            nonlocal ordinal, position, buffer_start, block_label
            raw = "\n".join(buffer)
            buffer.clear()
            label = block_label
            block_label = None
            if not raw.strip():
                return
            start, stop, content = _trimmed_span(raw, 0, len(raw), buffer_start)
            if not content:
                return

            kind = SectionKind.TAG_LIST if looks_like_tag_list(content) else SectionKind.PROSE
            sections.append(
                Section(
                    ordinal=ordinal,
                    kind=kind,
                    label=label,
                    label_norm=normalize_segment(label) if label else None,
                    negative=negative,
                    char_start=start,
                    char_end=stop,
                )
            )
            if kind is SectionKind.TAG_LIST:
                self._emit_tags(content, start, ordinal, negative, position, segments)
            else:
                self._emit_sentences(content, start, ordinal, negative, position, segments)
            position = len(segments)
            ordinal += 1

        cursor = 0
        for line in text.split("\n"):
            line_start = cursor
            cursor += len(line) + 1

            if not line.strip():
                flush_block(line_start)
                buffer_start = cursor
                continue

            kind, payload, payload_offset = self._classify(line)

            if kind == "param":
                flush_block(line_start)
                key_raw, value = payload
                self._emit_param(
                    key_raw, value, line, line_start, payload_offset,
                    ordinal, params, segments, position, negative,
                )
                sections.append(
                    Section(
                        ordinal=ordinal,
                        kind=SectionKind.PARAMS,
                        label=pending_label,
                        label_norm=normalize_segment(pending_label) if pending_label else None,
                        negative=negative,
                        char_start=line_start + payload_offset,
                        char_end=line_start + len(line.rstrip()),
                    )
                )
                position = len(segments)
                ordinal += 1
                buffer_start = cursor
                continue

            if kind == "header":
                flush_block(line_start)
                header = payload
                normalized = normalize_segment(header)
                sections.append(
                    Section(
                        ordinal=ordinal,
                        kind=SectionKind.HEADER,
                        label=header,
                        label_norm=normalized,
                        negative=negative,
                        char_start=line_start,
                        char_end=line_start + len(line),
                    )
                )
                segments.append(
                    Segment(
                        kind=SegmentKind.HEADER,
                        text_raw=header,
                        text_norm=normalized,
                        text_hash=segment_hash(normalized),
                        position=position,
                        section_ordinal=ordinal,
                        negative=negative,
                        char_start=line_start + payload_offset,
                        char_end=line_start + len(line.rstrip()),
                    )
                )
                position = len(segments)
                ordinal += 1
                # Die Ueberschrift bestimmt, ob das Folgende negativ ist.
                negative = any(marker in header.upper() for marker in self._markers)
                pending_label = header
                buffer_start = cursor
                continue

            if kind == "labelled":
                flush_block(line_start)
                label, rest = payload
                block_label = label
                buffer.append(rest)
                buffer_start = line_start + payload_offset
                continue

            if not buffer:
                buffer_start = line_start
            buffer.append(line)

        flush_block(len(text))

        return ParsedPrompt(
            format=FORMAT_ID,
            sections=tuple(sections),
            segments=tuple(segments),
            params=tuple(params),
            word_count=len(text.split()),
            parse_version=PARSE_VERSION,
        )

    # Intern -------------------------------------------------------------------

    def _classify(self, line: str) -> tuple[str, object, int]:
        """Liefert (Art, Nutzlast, Versatz der Nutzlast innerhalb der Zeile).

        Der Versatz ist noetig, damit die Zeichenspannen auf den Inhalt zeigen
        und nicht auf das Aufzaehlungszeichen oder das Label davor. Ihn ueber
        line.index(...) zu suchen ist falsch: bei "FITMENT: Follow ..." findet
        die Suche nach "F" das F von FITMENT.
        """
        bullet = _BULLET.match(line)
        bullet_length = bullet.end() if bullet else 0
        core = line[bullet_length:]
        key_value = _KEY_VALUE.match(core)

        # 1. Bullet mit Schluessel-Wert: immer ein Parameter.
        if key_value is not None and bullet is not None:
            return "param", (key_value.group(1), key_value.group(2)), bullet_length

        # 2. Ueberschrift.
        if looks_like_header(line):
            return "header", line.strip(), len(line) - len(line.lstrip())

        if key_value is not None:
            value = key_value.group(2)
            value_offset = bullet_length + key_value.start(2)
            # 3. Label mit langem Text: beschrifteter Prosablock.
            if len(value) > _MAX_INLINE_PARAM_LENGTH:
                return "labelled", (key_value.group(1).strip(), value), value_offset
            # Kurze eigenstaendige Zeile: ebenfalls ein Parameter.
            if len(line.strip()) < _MAX_INLINE_PARAM_LENGTH:
                return "param", (key_value.group(1), value), bullet_length
            return "labelled", (key_value.group(1).strip(), value), value_offset

        return "text", line, 0

    def _emit_sentences(
        self,
        content: str,
        offset: int,
        ordinal: int,
        negative: bool,
        position: int,
        out: list[Segment],
    ) -> None:
        for start, end, raw in split_sentences(content, offset):
            normalized = normalize_segment(raw)
            if not normalized:
                continue
            out.append(
                Segment(
                    kind=SegmentKind.SENTENCE,
                    text_raw=normalize_whitespace(raw),
                    text_norm=normalized,
                    text_hash=segment_hash(normalized),
                    position=position + len(out) - len(out),
                    section_ordinal=ordinal,
                    negative=negative,
                    char_start=start,
                    char_end=end,
                )
            )
        self._renumber(out)

    def _emit_tags(
        self,
        content: str,
        offset: int,
        ordinal: int,
        negative: bool,
        position: int,
        out: list[Segment],
    ) -> None:
        cursor = 0
        for part in content.split(","):
            start = content.index(part, cursor) if part else cursor
            cursor = start + len(part)
            span_start, span_end, raw = _trimmed_span(content, start, cursor, offset)
            if not raw.strip():
                continue
            weighted, _adapters = analyse_tag(raw)
            normalized = normalize_segment(weighted.text or raw)
            if not normalized:
                continue
            out.append(
                Segment(
                    kind=SegmentKind.TAG,
                    text_raw=normalize_whitespace(raw),
                    text_norm=normalized,
                    text_hash=segment_hash(normalized),
                    position=0,
                    section_ordinal=ordinal,
                    negative=negative,
                    weight=weighted.weight,
                    char_start=span_start,
                    char_end=span_end,
                )
            )
        self._renumber(out)

    def _emit_param(
        self,
        key_raw: str,
        value: str,
        line: str,
        line_start: int,
        payload_offset: int,
        ordinal: int,
        params: list[PromptParam],
        segments: list[Segment],
        position: int,
        negative: bool,
    ) -> None:
        key = key_raw.strip()
        value_text = value.strip()
        number, unit = parse_number(value_text)
        value_start = (
            line_start + line.rindex(value_text) if value_text in line else line_start
        )

        params.append(
            PromptParam(
                key_norm=normalize_segment(key),
                key_raw=key,
                value_text=value_text,
                value_num=number,
                unit=unit,
                section_ordinal=ordinal,
                char_start=value_start,
                char_end=value_start + len(value_text),
            )
        )

        normalized = normalize_segment(key) + ": " + normalize_segment(value_text)
        segments.append(
            Segment(
                kind=SegmentKind.PARAM,
                text_raw=key + ": " + value_text,
                text_norm=normalized,
                text_hash=segment_hash(normalized),
                position=0,
                section_ordinal=ordinal,
                negative=negative,
                char_start=line_start + payload_offset,
                char_end=line_start + len(line.rstrip()),
            )
        )
        self._renumber(segments)

    @staticmethod
    def _renumber(segments: list[Segment]) -> None:
        """Haelt position fortlaufend. Die Reihenfolge im Prompt ist Analysedatum."""
        for index, segment in enumerate(segments):
            if segment.position != index:
                segments[index] = Segment(
                    kind=segment.kind,
                    text_raw=segment.text_raw,
                    text_norm=segment.text_norm,
                    text_hash=segment.text_hash,
                    position=index,
                    section_ordinal=segment.section_ordinal,
                    negative=segment.negative,
                    weight=segment.weight,
                    char_start=segment.char_start,
                    char_end=segment.char_end,
                )
