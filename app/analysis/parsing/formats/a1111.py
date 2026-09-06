"""Parser fuer das A1111-Format.

Aufbau:

    <positiver Prompt, kommasepariert>
    Negative prompt: <negativer Prompt, kommasepariert>
    Steps: 30, Sampler: DPM++ 2M, CFG scale: 7, Seed: 12345, Size: 512x768, ...

Das Primaerformat dieses Projekts ist es nicht; die Unterstuetzung kostet aber
wenig und faengt aeltere oder aus anderen Werkzeugen stammende Dateien ab.

Die Parameterzeile wird in prompt_params zerlegt. Damit werden Fragen wie
"welche Bilder liefen mit CFG 7" beantwortbar, ohne Volltextsuche.
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
from app.analysis.parsing.formats.prose_sectioned import parse_number
from app.analysis.parsing.tokenizer import analyse_tag
from app.core.text import normalize_segment, normalize_whitespace, segment_hash

FORMAT_ID = "a1111"

NEGATIVE_PREFIX = re.compile(r"^\s*negative\s+prompt\s*:\s*", re.I)
PARAM_LINE = re.compile(
    r"^\s*(Steps|Sampler|CFG scale|Seed|Size|Model|Model hash|Denoising strength|"
    r"Clip skip|VAE|Schedule type)\s*:",
    re.I,
)
# Trennt die Parameterzeile an Kommas, die ausserhalb von Anfuehrungszeichen stehen.
_PARAM_SPLIT = re.compile(r',\s*(?=[A-Za-z][A-Za-z0-9 _+-]*\s*:)')


def looks_like_a1111(text: str) -> bool:
    lines = text.splitlines()
    return any(NEGATIVE_PREFIX.match(line) for line in lines) or any(
        PARAM_LINE.match(line) for line in lines
    )


class A1111Parser:
    def parse(self, text: str) -> ParsedPrompt:
        sections: list[Section] = []
        segments: list[Segment] = []
        params: list[PromptParam] = []
        ordinal = 0

        positive_parts: list[tuple[int, str]] = []
        negative_parts: list[tuple[int, str]] = []
        param_lines: list[tuple[int, str]] = []

        cursor = 0
        target = positive_parts
        for line in text.split("\n"):
            line_start = cursor
            cursor += len(line) + 1
            if not line.strip():
                continue
            if PARAM_LINE.match(line):
                param_lines.append((line_start, line))
                continue
            match = NEGATIVE_PREFIX.match(line)
            if match is not None:
                target = negative_parts
                target.append((line_start + match.end(), line[match.end() :]))
                continue
            target.append((line_start, line))

        for parts, negative in ((positive_parts, False), (negative_parts, True)):
            if not parts:
                continue
            start = parts[0][0]
            end = parts[-1][0] + len(parts[-1][1])
            sections.append(
                Section(
                    ordinal=ordinal,
                    kind=SectionKind.TAG_LIST,
                    label="Negative prompt" if negative else None,
                    label_norm="negative prompt" if negative else None,
                    negative=negative,
                    char_start=start,
                    char_end=end,
                )
            )
            for offset, chunk in parts:
                self._emit_tags(chunk, offset, ordinal, negative, segments)
            ordinal += 1

        for offset, line in param_lines:
            sections.append(
                Section(
                    ordinal=ordinal,
                    kind=SectionKind.PARAMS,
                    label="Parameters",
                    label_norm="parameters",
                    negative=False,
                    char_start=offset,
                    char_end=offset + len(line.rstrip()),
                )
            )
            self._emit_params(line, offset, ordinal, params, segments)
            ordinal += 1

        for index, segment in enumerate(segments):
            segments[index] = _with_position(segment, index)

        return ParsedPrompt(
            format=FORMAT_ID,
            sections=tuple(sections),
            segments=tuple(segments),
            params=tuple(params),
            word_count=len(text.split()),
            parse_version=PARSE_VERSION,
        )

    def _emit_tags(
        self, chunk: str, offset: int, ordinal: int, negative: bool, out: list[Segment]
    ) -> None:
        cursor = 0
        for part in chunk.split(","):
            start = cursor
            cursor += len(part) + 1
            stripped = part.strip()
            if not stripped:
                continue
            lead = len(part) - len(part.lstrip())
            weighted, _adapters = analyse_tag(stripped)
            normalized = normalize_segment(weighted.text or stripped)
            if not normalized:
                continue
            out.append(
                Segment(
                    kind=SegmentKind.TAG,
                    text_raw=normalize_whitespace(stripped),
                    text_norm=normalized,
                    text_hash=segment_hash(normalized),
                    position=0,
                    section_ordinal=ordinal,
                    negative=negative,
                    weight=weighted.weight,
                    char_start=offset + start + lead,
                    char_end=offset + start + lead + len(stripped),
                )
            )

    def _emit_params(
        self,
        line: str,
        offset: int,
        ordinal: int,
        params: list[PromptParam],
        segments: list[Segment],
    ) -> None:
        for chunk in _PARAM_SPLIT.split(line.strip()):
            if ":" not in chunk:
                continue
            key_raw, _, value = chunk.partition(":")
            key = key_raw.strip()
            value_text = value.strip()
            if not key or not value_text:
                continue
            number, unit = parse_number(value_text)
            position = offset + line.find(chunk)
            params.append(
                PromptParam(
                    key_norm=normalize_segment(key),
                    key_raw=key,
                    value_text=value_text,
                    value_num=number,
                    unit=unit,
                    section_ordinal=ordinal,
                    char_start=position,
                    char_end=position + len(chunk),
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
                    negative=False,
                    char_start=position,
                    char_end=position + len(chunk),
                )
            )


def _with_position(segment: Segment, index: int) -> Segment:
    if segment.position == index:
        return segment
    return Segment(
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
