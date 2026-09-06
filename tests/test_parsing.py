"""Tests der Parsing-Engine.

Der Kern sind Golden-Tests am realen Promptformat des Projekts: Prosa-Absaetze,
CAPS-Sektionen, Bullet-Parameter, Tag-Listen und ein Negativblock.
"""

from __future__ import annotations

import re

import pytest

from app.analysis.parsing.base import PARSE_VERSION, SectionKind, SegmentKind
from app.analysis.parsing.formats.a1111 import A1111Parser, looks_like_a1111
from app.analysis.parsing.formats.prose_sectioned import (
    ProseSectionedParser,
    looks_like_header,
    looks_like_tag_list,
    parse_number,
    split_sentences,
)
from app.analysis.parsing.pipeline import ParsingPipeline, detect_format
from app.analysis.parsing.tokenizer import analyse_tag, extract_adapters, strip_weights

SAMPLE = """A extreme low angle  hyper-realistic, cinematic editorial portrait of a young woman photographed indoors against a plain wall.

Pose variation: Use a unique natural portrait pose for each image. The subject must never face the camera directly and must never be photographed front-on. Favor three-quarter, candid, seated, leaning, and dynamic angled poses.
She wears a necklace that has a tiny golden/diamond key with a heart on it.

She looks directly into the camera with a confident, dominant, arrogant expression.
Maintain the makeup from the first image and add smokey eyes.

FITMENT: Follow the exact head tilt, rotation and angle, naturally conforming to the skull and hairline. Never enlarge, widen or stretch the crown to fit the hair.

warm golden sunlight dominates the scene.
Skin texture is realistic and detailed, with natural pores and soft highlights.

CAMERA & LENS SETTINGS
•Camera: Full-frame mirrorless (cinema-grade look)
•Lens: 50mm prime
•Aperture: f/2.0 (soft background separation, sharp subject)
•Shutter Speed: 1/125s
•ISO: 400
•Aspect Ratio: 3:4 vertical

STYLE & QUALITY TAGS
hyper-realistic, cinematic lighting, fine-art portrait photography, natural skin texture, moody color grading, photorealism

NEGATIVE PROMPT
cartoon, illustration, anime, CGI, plastic skin, distorted anatomy, watermark, low resolution
"""


@pytest.fixture
def parsed():
    return ProseSectionedParser().parse(SAMPLE)


class TestWeights:
    @pytest.mark.parametrize(
        "raw,text,weight",
        [
            ("masterpiece", "masterpiece", 1.0),
            ("(masterpiece)", "masterpiece", 1.1),
            ("((masterpiece))", "masterpiece", 1.21),
            ("(((masterpiece)))", "masterpiece", 1.331),
            ("[masterpiece]", "masterpiece", 0.9091),
            ("[[masterpiece]]", "masterpiece", 0.8264),
            ("(masterpiece:1.2)", "masterpiece", 1.2),
            ("(masterpiece:0.5)", "masterpiece", 0.5),
            ("((masterpiece:1.2))", "masterpiece", 1.32),
        ],
    )
    def test_conventions(self, raw: str, text: str, weight: float) -> None:
        """Spezifikation 04: Gewichtungsinformationen duerfen nicht verlorengehen."""
        result = strip_weights(raw)
        assert result.text == text
        assert result.weight == pytest.approx(weight, abs=0.001)

    def test_escaped_parentheses_stay_text(self) -> None:
        assert strip_weights(r"a \(literal\) paren").text == "a (literal) paren"
        assert strip_weights(r"a \(literal\) paren").weight == 1.0

    def test_adapters_are_removed_and_reported(self) -> None:
        """Ein LoRA-Verweis beschreibt kein Bildmerkmal.

        Bliebe er im Segment, waeren zwei sonst identische Prompts allein wegen
        unterschiedlicher Adaptergewichte verschieden.
        """
        text, adapters = extract_adapters("portrait <lora:styleA:0.8> and <lora:styleB> more")
        assert "lora" not in text
        assert [(a.name, a.weight) for a in adapters] == [("styleA", 0.8), ("styleB", 1.0)]

    def test_analyse_tag_combines_both(self) -> None:
        weighted, adapters = analyse_tag("(blue eyes:1.4) <lora:x:0.5>")
        assert weighted.text == "blue eyes"
        assert weighted.weight == pytest.approx(1.4)
        assert len(adapters) == 1

    def test_unbalanced_brackets_do_not_crash(self) -> None:
        assert strip_weights("((unclosed").text
        assert strip_weights("unopened))").text


class TestClassificationRules:
    def test_caps_header(self) -> None:
        assert looks_like_header("CAMERA & LENS SETTINGS")
        assert looks_like_header("NEGATIVE PROMPT")
        assert not looks_like_header("A cinematic portrait of a woman.")
        assert not looks_like_header("Pose variation: use a natural pose")

    def test_tag_list_detection(self) -> None:
        assert looks_like_tag_list("cartoon, illustration, anime, CGI, plastic skin")
        assert not looks_like_tag_list(
            "She looks into the camera. Her expression is calm. The light is warm."
        )

    def test_sentence_split_keeps_numbers_intact(self) -> None:
        """f/2.0, 1/125s, 3:4, 100 % und 30-90 duerfen nicht zerrissen werden."""
        text = (
            "Aperture is f/2.0 here. Shutter 1/125s and ratio 3:4. "
            "Her face remains 100% unaltered. Rotation of 30–90 degrees."
        )
        sentences = [chunk for _s, _e, chunk in split_sentences(text)]
        assert len(sentences) == 4
        assert "f/2.0" in sentences[0]
        assert "1/125s" in sentences[1] and "3:4" in sentences[1]
        assert "30–90" in sentences[3]

    def test_parse_number(self) -> None:
        assert parse_number("400") == (400.0, None)
        assert parse_number("50mm") == (50.0, "mm")
        assert parse_number("85%") == (85.0, "%")
        assert parse_number("Full-frame mirrorless") == (None, None)


class TestProseParser:
    def test_structure_matches_the_real_format(self, parsed) -> None:
        kinds = {k: 0 for k in ("sentence", "tag", "param", "header")}
        for segment in parsed.segments:
            kinds[segment.kind.value] += 1

        assert kinds["header"] == 3, "drei CAPS-Ueberschriften"
        assert kinds["param"] == 6, "sechs Bullet-Parameter"
        assert kinds["tag"] == 14, "sechs Style-Tags plus acht Negativ-Tags"
        assert kinds["sentence"] >= 10
        assert parsed.format == "prose_sectioned"
        assert parsed.parse_version == PARSE_VERSION

    def test_negative_block_is_marked(self, parsed) -> None:
        """Ab der Ueberschrift NEGATIVE PROMPT gelten Segmente als negativ."""
        negative = [s.text_norm for s in parsed.negative_segments if s.kind is SegmentKind.TAG]
        assert "cartoon" in negative
        assert "watermark" in negative
        assert len(negative) == 8

        positive = [s.text_norm for s in parsed.positive_segments if s.kind is SegmentKind.TAG]
        assert "photorealism" in positive
        assert "cartoon" not in positive

    def test_parameters_are_extracted(self, parsed) -> None:
        """Kamerawerte werden strukturiert erfasst, nicht nur als Text."""
        params = {p.key_norm: p for p in parsed.params}
        assert params["aperture"].value_text.startswith("f/2.0")
        assert params["lens"].value_text == "50mm prime"
        assert params["iso"].value_num == 400.0
        assert "camera" in params and "shutter speed" in params

    def test_spans_map_onto_the_original_text(self, parsed) -> None:
        """Ohne korrekte Spannen ist die Hervorhebung im Editor verschoben.

        Genau hier lagen zwei Fehler: bei beschrifteten Bloecken begann die
        Spanne beim Label statt beim Inhalt, bei Bullet-Parametern schloss sie
        das Aufzaehlungszeichen ein.
        """
        for segment in parsed.segments:
            excerpt = SAMPLE[segment.char_start : segment.char_end]
            assert re.sub(r"\s+", " ", excerpt).strip() == segment.text_raw, segment.text_raw

    def test_labelled_block_span_starts_at_the_content(self, parsed) -> None:
        fitment = [s for s in parsed.segments if s.text_raw.startswith("Follow the exact")]
        assert fitment, "der FITMENT-Block muss als Prosa geparst sein"
        assert SAMPLE[fitment[0].char_start :].startswith("Follow the exact")

    def test_positions_are_consecutive(self, parsed) -> None:
        assert [s.position for s in parsed.segments] == list(range(parsed.segment_count))

    def test_sections_carry_labels(self, parsed) -> None:
        labels = {s.label for s in parsed.sections if s.label}
        assert "CAMERA & LENS SETTINGS" in labels
        assert "NEGATIVE PROMPT" in labels
        assert "FITMENT" in labels

    def test_normalisation_unifies_spelling(self) -> None:
        first = ProseSectionedParser().parse("Warm   GOLDEN sunlight.")
        second = ProseSectionedParser().parse("warm golden sunlight")
        assert first.segments[0].text_hash == second.segments[0].text_hash

    def test_empty_input(self) -> None:
        parsed = ProseSectionedParser().parse("")
        assert parsed.segment_count == 0

    def test_json_roundtrip_is_compact_and_complete(self, parsed) -> None:
        import json

        payload = json.loads(parsed.to_json())
        assert payload["v"] == PARSE_VERSION
        assert len(payload["segments"]) == parsed.segment_count
        assert len(payload["params"]) == len(parsed.params)
        assert payload["segments"][0]["a"] == parsed.segments[0].char_start


class TestA1111Parser:
    RAW = (
        "masterpiece, best quality, 1girl, (blue eyes:1.3)\n"
        "Negative prompt: lowres, bad anatomy, (worst quality:1.4)\n"
        "Steps: 30, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 3820192, Size: 512x768"
    )

    def test_detection(self) -> None:
        assert looks_like_a1111(self.RAW)
        assert detect_format(self.RAW) == "a1111"
        assert detect_format("A cinematic portrait. Warm light.") == "prose_sectioned"

    def test_positive_and_negative_are_separated(self) -> None:
        parsed = A1111Parser().parse(self.RAW)
        positive = [s.text_norm for s in parsed.positive_segments if s.kind is SegmentKind.TAG]
        negative = [s.text_norm for s in parsed.negative_segments]
        assert positive == ["masterpiece", "best quality", "1girl", "blue eyes"]
        assert negative == ["lowres", "bad anatomy", "worst quality"]

    def test_weights_survive(self) -> None:
        parsed = A1111Parser().parse(self.RAW)
        weights = {s.text_norm: s.weight for s in parsed.segments if s.weight != 1.0}
        assert weights == {"blue eyes": 1.3, "worst quality": 1.4}

    def test_parameters(self) -> None:
        parsed = A1111Parser().parse(self.RAW)
        params = {p.key_norm: p for p in parsed.params}
        assert params["steps"].value_num == 30.0
        assert params["cfg scale"].value_num == 7.0
        assert params["sampler"].value_text == "DPM++ 2M Karras"
        assert params["size"].value_text == "512x768"

    def test_spans_map_onto_the_original(self) -> None:
        parsed = A1111Parser().parse(self.RAW)
        for segment in parsed.segments:
            excerpt = self.RAW[segment.char_start : segment.char_end]
            assert re.sub(r"\s+", " ", excerpt).strip() == segment.text_raw


class TestPipeline:
    def test_auto_detection(self) -> None:
        pipeline = ParsingPipeline()
        assert pipeline.parse(TestA1111Parser.RAW).format == "a1111"
        assert pipeline.parse(SAMPLE).format == "prose_sectioned"
        assert pipeline.parse("   ").format == "empty"

    def test_forced_format_overrides_detection(self) -> None:
        pipeline = ParsingPipeline(format_preference="prose_sectioned")
        assert pipeline.parse(TestA1111Parser.RAW).format == "prose_sectioned"

    def test_min_segment_words_filters(self) -> None:
        pipeline = ParsingPipeline(min_segment_words=3)
        parsed = pipeline.parse("a, bb, three word tag, cc, dd, ee")
        assert all(s.word_count >= 3 for s in parsed.segments if s.kind is SegmentKind.TAG)
        assert [s.position for s in parsed.segments] == list(range(parsed.segment_count))

    def test_custom_negative_marker(self) -> None:
        text = "positive sentence here.\n\nUNWANTED\nblurry, ugly, watermark, bad hands\n"
        pipeline = ParsingPipeline(negative_markers=("UNWANTED",))
        parsed = pipeline.parse(text)
        assert {s.text_norm for s in parsed.negative_segments} >= {"blurry", "ugly"}

    def test_pipeline_is_reusable(self) -> None:
        """Die Pipeline haelt keinen Zustand ueber einen Prompt hinaus."""
        pipeline = ParsingPipeline()
        first = pipeline.parse(SAMPLE)
        second = pipeline.parse(SAMPLE)
        assert first.segment_count == second.segment_count
        assert [s.text_hash for s in first.segments] == [s.text_hash for s in second.segments]
