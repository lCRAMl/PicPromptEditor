"""Die Parsing-Pipeline: Text hinein, ParsedPrompt heraus.

    Dekodierung -> Formaterkennung -> Sektionierung -> Segmentierung -> Normalisierung

Die Dekodierung passiert bereits beim Einlesen im Scanner; hier beginnt es beim
fertigen Text.

Die Formaterkennung ist bewusst konservativ: erkannt wird nur, was eindeutig
ist. A1111 verraet sich durch "Negative prompt:" oder eine Parameterzeile.
Alles andere geht an den Prosa-Parser, der Fliesstext, Tag-Listen und
Bullet-Parameter gleichermassen behandelt und damit auch als Rueckfallweg
taugt.

Die Pipeline haelt keinen Zustand ueber einen Prompt hinaus und ist damit aus
mehreren Worker-Threads gleichzeitig benutzbar.
"""

from __future__ import annotations

from app.analysis.parsing.base import ParsedPrompt
from app.analysis.parsing.formats.a1111 import A1111Parser, looks_like_a1111
from app.analysis.parsing.formats.prose_sectioned import (
    DEFAULT_NEGATIVE_MARKERS,
    ProseSectionedParser,
)

FORMAT_AUTO = "auto"


def detect_format(text: str) -> str:
    """Erkennt das Promptformat. Im Zweifel prose_sectioned."""
    if not text.strip():
        return "empty"
    if looks_like_a1111(text):
        return "a1111"
    return "prose_sectioned"


class ParsingPipeline:
    def __init__(
        self,
        format_preference: str = FORMAT_AUTO,
        negative_markers: tuple[str, ...] = DEFAULT_NEGATIVE_MARKERS,
        min_segment_words: int = 1,
    ) -> None:
        self._preference = format_preference
        self._min_words = max(1, min_segment_words)
        self._prose = ProseSectionedParser(negative_markers)
        self._a1111 = A1111Parser()

    def parse(self, text: str) -> ParsedPrompt:
        if not text.strip():
            return ParsedPrompt(format="empty")

        chosen = self._preference if self._preference != FORMAT_AUTO else detect_format(text)
        parser = self._a1111 if chosen == "a1111" else self._prose
        parsed = parser.parse(text)
        return self._filter(parsed)

    def _filter(self, parsed: ParsedPrompt) -> ParsedPrompt:
        """Verwirft zu kurze Segmente und nummeriert neu.

        Ein einzelnes Wort wie "and" traegt nichts zur Aehnlichkeit bei, blaeht
        aber tokens und prompt_tokens auf. Die Grenze ist konfigurierbar
        (analysis.min_segment_words) und standardmaessig so gesetzt, dass nichts
        verworfen wird.
        """
        if self._min_words <= 1:
            return parsed

        from app.analysis.parsing.base import Segment

        kept: list[Segment] = []
        for segment in parsed.segments:
            if segment.word_count < self._min_words and segment.kind.value in ("sentence", "tag"):
                continue
            kept.append(
                Segment(
                    kind=segment.kind,
                    text_raw=segment.text_raw,
                    text_norm=segment.text_norm,
                    text_hash=segment.text_hash,
                    position=len(kept),
                    section_ordinal=segment.section_ordinal,
                    negative=segment.negative,
                    weight=segment.weight,
                    char_start=segment.char_start,
                    char_end=segment.char_end,
                )
            )
        return ParsedPrompt(
            format=parsed.format,
            sections=parsed.sections,
            segments=tuple(kept),
            params=parsed.params,
            word_count=parsed.word_count,
            parse_version=parsed.parse_version,
        )
