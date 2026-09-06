"""Tests der Querschnittsschicht: Fehlerobjekte, Abbruch, Pfade, Textnormalisierung."""

from __future__ import annotations

import threading

import pytest

from app.core.cancellation import CancellationToken, CancelledError
from app.core.errors import AppError, ErrorReporter, Severity, guarded
from app.core.paths import long_path, normalize_path, normalize_stem, shard_path
from app.core.text import (
    decode_bytes,
    normalize_segment,
    normalize_whitespace,
    segment_hash,
    text_hash,
)


class TestErrors:
    def test_message_key_defaults_to_source_and_message(self) -> None:
        error = AppError(Severity.ERROR, "scanner", "Datei fehlt")
        assert error.message_key == "scanner:Datei fehlt"

    def test_from_exception_captures_traceback(self) -> None:
        try:
            raise ValueError("kaputt")
        except ValueError as exc:
            error = AppError.from_exception(exc, source="parser")
        assert error.exc_type == "ValueError"
        assert error.severity is Severity.ERROR
        assert "ValueError" in (error.details or "")
        assert error.message_key == "parser:ValueError"

    def test_reporter_fans_out_to_all_sinks(self, reporter: ErrorReporter) -> None:
        seen_a: list[AppError] = []
        seen_b: list[AppError] = []
        reporter.add_sink(type("A", (), {"handle": lambda self, e: seen_a.append(e)})())
        reporter.add_sink(type("B", (), {"handle": lambda self, e: seen_b.append(e)})())
        reporter.warning("x", "hinweis")
        assert len(seen_a) == len(seen_b) == 1

    def test_broken_sink_does_not_block_the_others(self, reporter: ErrorReporter) -> None:
        """Ein defekter Logger darf die Fehlerbehandlung nicht mitreissen."""
        seen: list[AppError] = []

        class Broken:
            def handle(self, error: AppError) -> None:
                raise RuntimeError("Sink defekt")

        reporter.add_sink(Broken())
        reporter.add_sink(type("Good", (), {"handle": lambda self, e: seen.append(e)})())
        reporter.error("x", "trotzdem")
        assert len(seen) == 1

    def test_counts_are_tracked(self, reporter: ErrorReporter) -> None:
        reporter.info("a", "i")
        reporter.warning("a", "w")
        reporter.warning("a", "w2")
        counts = reporter.counts()
        assert counts[Severity.INFO] == 1
        assert counts[Severity.WARNING] == 2

    def test_reporter_is_thread_safe(self, reporter: ErrorReporter) -> None:
        seen: list[AppError] = []
        lock = threading.Lock()

        class Collector:
            def handle(self, error: AppError) -> None:
                with lock:
                    seen.append(error)

        reporter.add_sink(Collector())

        def worker() -> None:
            for _ in range(50):
                reporter.info("thread", "tick")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(seen) == 400
        assert reporter.counts()[Severity.INFO] == 400

    def test_guarded_swallows_and_reports(self, reporter: ErrorReporter, collected) -> None:
        @guarded(reporter, "jobs.demo", default=-1)
        def boom() -> int:
            raise KeyError("weg")

        assert boom() == -1
        assert collected[0].source == "jobs.demo"
        assert collected[0].message_key == "jobs.demo:boom"

    def test_guarded_passes_through_success(self, reporter: ErrorReporter, collected) -> None:
        @guarded(reporter, "jobs.demo")
        def fine(a: int, b: int) -> int:
            return a + b

        assert fine(2, 3) == 5
        assert collected == []


class TestCancellation:
    def test_cancel_is_visible(self) -> None:
        token = CancellationToken()
        assert not token.cancelled
        token.cancel()
        assert token.cancelled

    def test_raise_if_cancelled(self) -> None:
        token = CancellationToken()
        token.raise_if_cancelled()
        token.cancel()
        with pytest.raises(CancelledError):
            token.raise_if_cancelled()

    def test_parent_cancels_children(self) -> None:
        """Ein abgeloester Suchvorgang muss seine Teiljobs mitnehmen."""
        parent = CancellationToken()
        a, b = parent.child(), parent.child()
        grandchild = a.child()
        parent.cancel()
        assert a.cancelled and b.cancelled and grandchild.cancelled

    def test_child_of_cancelled_parent_starts_cancelled(self) -> None:
        parent = CancellationToken()
        parent.cancel()
        assert parent.child().cancelled

    def test_child_cancel_does_not_affect_parent(self) -> None:
        parent = CancellationToken()
        child = parent.child()
        child.cancel()
        assert child.cancelled and not parent.cancelled


class TestPaths:
    def test_normalize_path_is_case_insensitive(self) -> None:
        assert normalize_path(r"Z:\Lib\Bilder") == normalize_path(r"z:/lib/bilder")

    def test_normalize_path_ignores_trailing_separator(self) -> None:
        assert normalize_path(r"Z:\Lib\\") == normalize_path(r"Z:\Lib")

    def test_normalize_stem_pairs_image_and_prompt(self) -> None:
        """Die Paar-Identitaet muss unabhaengig von Endung und Schreibweise sein."""
        assert normalize_stem("2026-09-01_00-28-53.JPG") == normalize_stem(
            "2026-09-01_00-28-53.txt"
        )

    def test_long_path_prefix_for_deep_paths(self) -> None:
        deep = "\\\\nas\\share\\" + "\\".join("ordner" + str(i) for i in range(40)) + "\\bild.jpg"
        result = long_path(deep)
        assert len(deep) > 240
        assert result.startswith("\\\\?\\UNC\\nas\\share\\")

    def test_long_path_leaves_short_paths_alone(self) -> None:
        assert long_path(r"Z:\lib\bild.jpg") == r"Z:\lib\bild.jpg"

    def test_shard_path_splits_into_subdirectories(self) -> None:
        assert shard_path("a1b2c3d4") == "a1/b2/a1b2c3d4"


class TestText:
    def test_utf8_is_preferred_over_cp1252(self) -> None:
        """Die Promptdateien sind UTF-8 mit typografischen Zeichen.

        Wuerden sie als cp1252 gelesen, entstuenden Mojibake-Folgen und jedes
        Segment waere ein anderes als beim naechsten Lauf.
        """
        raw = "Aperture: f/2.0 \u2022 ISO: 400\u2013640".encode("utf-8")
        text, encoding = decode_bytes(raw)
        assert encoding == "utf-8"
        assert "\u2022" in text and "\u2013" in text

    def test_bom_is_detected_and_stripped(self) -> None:
        text, encoding = decode_bytes("\ufeffwarm golden".encode("utf-8-sig"))
        assert encoding == "utf-8-sig"
        assert text == "warm golden"

    def test_cp1252_fallback_for_non_utf8(self) -> None:
        text, encoding = decode_bytes(b"caf\xe9 lighting")
        assert encoding == "cp1252"
        assert text == "caf\xe9 lighting"

    def test_decode_never_raises(self) -> None:
        text, encoding = decode_bytes(bytes(range(256)))
        assert isinstance(text, str) and encoding

    def test_trailing_punctuation_is_stripped(self) -> None:
        """Sonst waeren 'photorealism' und 'photorealism.' zwei Segmente.

        Genau dieser Fall trat im letzten Tag der Beispieldatei auf.
        """
        assert normalize_segment("photorealism") == normalize_segment(" Photorealism. ")

    def test_case_and_whitespace_are_normalized(self) -> None:
        assert normalize_segment("Warm   GOLDEN\n sunlight") == "warm golden sunlight"

    def test_nfkc_unifies_typographic_variants(self) -> None:
        assert normalize_segment("the dress\u2019s color") == normalize_segment("the dress\u2019s color")
        assert normalize_whitespace("a\t\tb") == "a b"

    def test_hashes_are_stable_and_distinct(self) -> None:
        assert segment_hash("warm golden") == segment_hash("warm golden")
        assert segment_hash("warm golden") != segment_hash("cold blue")
        assert text_hash("a") != text_hash("b")
        assert len(segment_hash("x")) == 32
