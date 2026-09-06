"""Sitzt die Einfaerbung im Editor auf den richtigen Zeichen?

Der Fehler, um den es hier geht, sah aus wie ein Gestaltungsfehler und war
einer im Zaehlen: die Segmentgrenzen aus der Datenbank zaehlen im **Rohtext**
der Datei, Qt zaehlt im **Dokument**. `setPlainText()` wandelt jedes
Wagenruecklauf-Zeilenvorschub-Paar in einen einzelnen Umbruch, das
Wagenrueckluf-Zeichen verschwindet also.

Bei Windows-Zeilenenden verschiebt sich die Hervorhebung dadurch mit jeder
vorangehenden Zeile um ein weiteres Zeichen nach links. In der echten
Bibliothek (9 solcher Zeilenenden in einem mittleren Prompt) zerfiel dadurch
"stylize" in ein farbloses "sty" und ein andersfarbiges "lize" - mitten im
Wort. Von elf Segmenten sassen acht falsch.
"""

from __future__ import annotations

import pytest

from app.gui.editor.highlighter import SegmentSpan
from app.gui.common.offsets import dropped_positions as _dropped_positions
from app.gui.common.offsets import shift as _shift
from app.gui.editor.prompt_editor import PromptEditor
from app.gui.theme import _LIGHT

CR = chr(13)
LF = chr(10)
CRLF = CR + LF


def make_span(start: int, end: int) -> SegmentSpan:
    return SegmentSpan(
        start=start, end=end, idf=3.0, doc_count=1, negative=False, kind="segment"
    )


class TestVerworfeneZeichen:
    def test_ohne_wagenruecklauf_keine_verschiebung(self) -> None:
        assert _dropped_positions("eins" + LF + "zwei") == []

    def test_jedes_paar_zaehlt_einmal(self) -> None:
        raw = "eins" + CRLF + "zwei" + CRLF + "drei"
        assert _dropped_positions(raw) == [4, 10]

    def test_alleinstehender_wagenruecklauf_verschiebt_nicht(self) -> None:
        """Ein einzelnes CR wird zum Umbruch und bleibt damit ein Zeichen."""
        assert _dropped_positions("eins" + CR + "zwei") == []

    def test_verschiebung_waechst_mit_den_zeilen(self) -> None:
        dropped = [4, 10, 16]
        assert _shift(0, dropped) == 0
        assert _shift(4, dropped) == 4
        assert _shift(5, dropped) == 4
        assert _shift(11, dropped) == 9
        assert _shift(20, dropped) == 17


class TestUmrechnungImEditor:
    def _editor(self, qapp, raw: str) -> PromptEditor:
        editor = PromptEditor(11, _LIGHT)
        editor._prompt = type("P", (), {"text": raw})()
        editor.setPlainText(raw)
        return editor

    def test_unix_zeilenenden_bleiben_unveraendert(self, qapp) -> None:
        raw = "Erste Zeile." + LF + "Zweite Zeile."
        editor = self._editor(qapp, raw)
        spans = [make_span(0, 12), make_span(13, 26)]
        assert editor._translate(spans) == spans

    def test_windows_zeilenenden_werden_umgerechnet(self, qapp) -> None:
        raw = "Erste Zeile." + CRLF + "Zweite Zeile." + CRLF + "Dritte Zeile."
        editor = self._editor(qapp, raw)
        dokument = editor.toPlainText()

        # Grenzen im Rohtext bestimmen und pruefen, dass die umgerechneten im
        # Dokument dasselbe Stueck Text treffen.
        for gesucht in ("Erste Zeile.", "Zweite Zeile.", "Dritte Zeile."):
            start = raw.index(gesucht)
            span = make_span(start, start + len(gesucht))
            [neu] = editor._translate([span])
            assert dokument[neu.start : neu.end] == gesucht, (
                "Rohposition " + str(start) + " traf im Dokument "
                + repr(dokument[neu.start : neu.end])
            )

    def test_ohne_umrechnung_zerfaellt_ein_wort(self, qapp) -> None:
        """Der gemeldete Fehler, nachgestellt: 'stylize' zerfaellt."""
        raw = (
            "Zeile eins." + CRLF + "Zeile zwei." + CRLF + "Zeile drei." + CRLF
            + "Do NOT distort or stylize identity."
        )
        editor = self._editor(qapp, raw)
        dokument = editor.toPlainText()
        gesucht = "Do NOT distort or stylize identity."
        start = raw.index(gesucht)
        span = make_span(start, start + len(gesucht))

        # Ohne Umrechnung sitzt die Einfaerbung um drei Zeichen zu weit rechts.
        falsch = dokument[span.start : span.end]
        assert not falsch.startswith("Do NOT"), "Der Fehler laesst sich nicht nachstellen"

        [richtig] = editor._translate([span])
        assert dokument[richtig.start : richtig.end] == gesucht

    def test_leerer_prompt_stuerzt_nicht_ab(self, qapp) -> None:
        editor = PromptEditor(11, _LIGHT)
        editor._prompt = None
        assert editor._translate([make_span(0, 5)]) == [make_span(0, 5)]

    def test_set_segment_spans_rechnet_um(self, qapp) -> None:
        raw = "Eins." + CRLF + "Zwei." + CRLF + "Drei."
        editor = self._editor(qapp, raw)
        start = raw.index("Drei.")
        editor.set_segment_spans([make_span(start, start + 5)])

        gesetzt = editor.highlighter._spans
        assert len(gesetzt) == 1
        assert editor.toPlainText()[gesetzt[0].start : gesetzt[0].end] == "Drei."


class TestKachelKennzeichnung:
    def test_kein_kreuz_mehr_auf_kachlen_ohne_prompt(self) -> None:
        """Das Kreuz sah aus wie ein Schliessknopf und stand auf drei Vierteln
        aller Kacheln - eine Warnung, die ueberall steht, warnt vor nichts."""
        import app.gui.library.delegate as delegate

        quelle = delegate.__file__
        with open(quelle, encoding="utf-8") as handle:
            text = handle.read()
        assert "_paint_missing_prompt" not in text
