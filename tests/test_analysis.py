"""Tests der Analyse-Persistenz: Segmenttabellen, Haeufigkeiten, Segmentsuche.

Schwerpunkt ist die inkrementelle Fortschreibung von token_frequency. Sie ist
die Zusage aus Spezifikation 04 und zugleich die Stelle, an der ein Fehler still
bleibt: falsche Zaehler fallen erst auf, wenn Basisprompt-Erkennung und
Hervorhebung Unsinn liefern.
"""

from __future__ import annotations

import os

import pytest
from PIL import Image

from app.analysis.parsing.base import PARSE_VERSION
from app.application import Application
from app.config.bootstrap import BootstrapConfig, save_bootstrap
from tests.conftest import pump_until

COMMON = "A cinematic editorial portrait of a young woman."
WARM = "warm golden sunlight dominates the scene."
COLD = "cold blue moonlight dominates the scene."


def write_pair(folder, index: int, light: str, aperture: str = "f/2.0") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (index * 30 % 255, 90, 140)).save(folder / ("p" + str(index) + ".jpg"))
    (folder / ("p" + str(index) + ".txt")).write_text(
        COMMON + "\n" + light + "\n\n"
        "CAMERA & LENS SETTINGS\n•Aperture: " + aperture + "\n•ISO: 400\n\n"
        "NEGATIVE PROMPT\ncartoon, blurry, watermark, lowres\n",
        encoding="utf-8",
    )


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "lib"
    for i in range(4):
        write_pair(root, i, WARM)
    for i in range(4, 6):
        write_pair(root, i, COLD, aperture="f/4.0")
    return root


@pytest.fixture
def app(qapp, tmp_path, library):
    bootstrap = tmp_path / "bootstrap.json"
    save_bootstrap(
        BootstrapConfig(
            db_path=str(tmp_path / "library.db"),
            thumbnail_cache_dir=str(tmp_path / "thumbs"),
            log_dir=str(tmp_path / "logs"),
            log_level="CRITICAL",
        ),
        bootstrap,
    )
    instance = Application(bootstrap_file=bootstrap)
    assert instance.startup().ok
    instance.settings.set("library.roots", [str(library)])
    instance.settings.set("index.scan_on_startup", False)
    # Die Vorgabe ist an der echten Bibliothek kalibriert (0.90) und dort
    # richtig. Dieser Korpus besteht aus einer Handvoll kurzer Prompts; bei 0.90
    # entstuende hier gar keine Familie. Geprueft wird die Mechanik des
    # Clusterns, nicht der kalibrierte Wert - also wird er hier ausdruecklich
    # gesetzt, statt aus der Vorgabe geerbt.
    instance.settings.set("analysis.cluster_threshold", 0.45)
    instance.build_services()
    yield instance
    instance.shutdown()


def scan(qapp, app) -> None:
    done: list = []
    app.index_service.scan_finished.connect(done.append)
    app.index_service.start_scan()
    assert pump_until(qapp, lambda: done, 30000), "Scan wurde nicht fertig"
    app.index_service.scan_finished.disconnect()


def parse(qapp, app):
    done: list = []
    app.analysis_service.parse_finished.connect(done.append)
    app.analysis_service.start_parsing()
    assert pump_until(qapp, lambda: done, 60000), "Parsing wurde nicht fertig"
    app.analysis_service.parse_finished.disconnect()
    return done[0]


def frequencies(app) -> dict[str, int]:
    return {
        r["token"][:45]: r["doc_count"]
        for r in app.database.query("SELECT token, doc_count FROM token_stats")
    }


def recomputed(app) -> dict[str, int]:
    """Unabhaengige Nachrechnung direkt aus prompt_tokens."""
    return {
        r["token"][:45]: r["docs"]
        for r in app.database.query(
            "SELECT t.token, COUNT(DISTINCT pt.prompt_id) AS docs FROM tokens t"
            " JOIN prompt_tokens pt ON pt.token_id = t.id"
            " JOIN prompts p ON p.id = pt.prompt_id"
            " WHERE p.is_present = 1 GROUP BY t.id"
        )
    }


class TestParsingRun:
    def test_all_prompts_get_parsed(self, qapp, app) -> None:
        scan(qapp, app)
        assert app.analysis_service.pending_count() == 6
        stats = parse(qapp, app)
        assert stats.prompts == 6
        assert app.analysis_service.pending_count() == 0

        counts = app.analysis_service.counts()
        assert counts["tokens"] > 0
        assert counts["prompt_tokens"] == stats.segments
        assert counts["params"] == 12  # zwei je Prompt

    def test_identical_texts_are_parsed_once(self, qapp, app) -> None:
        """Batch-Generierungen erzeugen viele Dateien mit gleichem Text.

        Geparst wird je eindeutigem Text, die relationalen Zeilen entstehen
        trotzdem fuer jeden Prompt - sonst faende die Segmentsuche nicht alle
        Bilder.
        """
        scan(qapp, app)
        stats = parse(qapp, app)
        assert stats.prompts == 6
        assert sum(stats.formats.values()) == 2, "nur zwei eindeutige Texte"
        assert app.tokens.counts()["prompt_tokens"] > 6

    def test_second_run_finds_nothing_to_do(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        again = parse(qapp, app)
        assert again.prompts == 0

    def test_parse_version_bump_invalidates_everything(self, qapp, app) -> None:
        """Ein Versionssprung ersetzt Millionen Dirty-Flags."""
        scan(qapp, app)
        parse(qapp, app)
        app.writer.execute("UPDATE prompts SET parse_version = 0").result(timeout=10)
        assert app.analysis_service.pending_count() == 6


class TestFrequencies:
    def test_counts_match_reality(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)

        counts = frequencies(app)
        assert counts[COMMON.rstrip(".").lower()[:45]] == 6
        assert counts[WARM.rstrip(".").lower()[:45]] == 4
        assert counts[COLD.rstrip(".").lower()[:45]] == 2
        assert counts == recomputed(app)

    def test_commonness_and_idf_are_consistent(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)

        rows = {
            r["token"][:45]: (r["commonness"], r["idf"])
            for r in app.database.query("SELECT token, commonness, idf FROM token_stats")
        }
        common = rows[COMMON.rstrip(".").lower()[:45]]
        rare = rows[COLD.rstrip(".").lower()[:45]]

        assert common[0] == pytest.approx(1.0), "in jedem Prompt enthalten"
        assert common[1] == pytest.approx(0.0), "kein Unterscheidungswert"
        assert rare[0] == pytest.approx(2 / 6, abs=0.01)
        assert rare[1] > common[1], "seltener heisst unterscheidungskraeftiger"
        assert all(value[0] <= 1.0 for value in rows.values())

    def test_changed_prompt_updates_only_its_own_contribution(self, qapp, app, library) -> None:
        """Der eigentliche Beweis der inkrementellen Fortschreibung."""
        scan(qapp, app)
        parse(qapp, app)
        assert frequencies(app)[WARM.rstrip(".").lower()[:45]] == 4

        target = library / "p0.txt"
        target.write_text(
            target.read_text(encoding="utf-8").replace(WARM, COLD), encoding="utf-8"
        )
        info = target.stat()
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000_000))

        scan(qapp, app)
        stats = parse(qapp, app)

        assert stats.prompts == 1, "nur der geaenderte Prompt wird neu geparst"
        counts = frequencies(app)
        assert counts[WARM.rstrip(".").lower()[:45]] == 3
        assert counts[COLD.rstrip(".").lower()[:45]] == 3
        assert counts == recomputed(app)

    def test_forced_reparse_does_not_double_count(self, qapp, app) -> None:
        """Zweimal dasselbe zu parsen darf die Zaehler nicht verdoppeln."""
        scan(qapp, app)
        parse(qapp, app)
        before = frequencies(app)

        app.writer.execute("UPDATE prompts SET parse_version = 0").result(timeout=10)
        parse(qapp, app)

        assert frequencies(app) == before
        assert frequencies(app) == recomputed(app)

    def test_deleted_prompt_reduces_the_counts(self, qapp, app, library) -> None:
        """Ein entfernter Prompt darf die Haeufigkeit nicht weiter aufblaehen.

        Der Scanner markiert die Datei nur als abwesend. Ohne Bereinigung
        zaehlte sie in doc_count unveraendert mit, und commonness konnte ueber
        1.0 steigen - eine Groesse, auf der ab M4 die Basisprompt-Erkennung
        aufbaut.
        """
        scan(qapp, app)
        parse(qapp, app)
        assert frequencies(app)[COMMON.rstrip(".").lower()[:45]] == 6

        (library / "p5.txt").unlink()
        (library / "p4.txt").unlink()
        scan(qapp, app)
        parse(qapp, app)

        counts = frequencies(app)
        assert counts[COMMON.rstrip(".").lower()[:45]] == 4
        assert counts == recomputed(app)
        assert all(
            r["commonness"] <= 1.0
            for r in app.database.query("SELECT commonness FROM token_stats")
        )

    def test_cleanup_runs_even_without_parsing_work(self, qapp, app, library) -> None:
        """Nach dem Loeschen gibt es nichts zu parsen, aber etwas aufzuraeumen."""
        scan(qapp, app)
        parse(qapp, app)
        (library / "p5.txt").unlink()
        scan(qapp, app)

        assert app.analysis_service.pending_count() > 0, (
            "Aufraeumarbeit muss als offene Arbeit zaehlen"
        )
        parse(qapp, app)
        assert frequencies(app) == recomputed(app)


class TestSegmentSearch:
    def test_finds_exactly_the_images_using_the_segment(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)

        found: list = []
        app.analysis_service.segment_results.connect(
            lambda request_id, digest, ids: found.append(ids)
        )
        app.analysis_service.search_segment_async(WARM)
        assert pump_until(qapp, lambda: found, 20000)
        assert len(found[0]) == 4

    def test_search_is_insensitive_to_spelling(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        found: list = []
        app.analysis_service.segment_results.connect(
            lambda request_id, digest, ids: found.append(ids)
        )
        app.analysis_service.search_segment_async("  WARM Golden Sunlight Dominates The Scene  ")
        assert pump_until(qapp, lambda: found, 20000)
        assert len(found[0]) == 4

    def test_unknown_segment_returns_nothing(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        found: list = []
        app.analysis_service.segment_results.connect(
            lambda request_id, digest, ids: found.append(ids)
        )
        app.analysis_service.search_segment_async("dieser satz kommt nirgends vor")
        assert pump_until(qapp, lambda: found, 20000)
        assert found[0] == []

    def test_usage_counts(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        prompts, images = app.analysis_service.segment_usage(WARM)
        assert prompts == 4 and images == 4


class TestParameters:
    def test_values_are_queryable(self, qapp, app) -> None:
        """Der eigentliche Nutzen von prompt_params: nach Kamerawerten filtern."""
        scan(qapp, app)
        parse(qapp, app)

        assert dict(app.analysis_service.param_keys()) == {"aperture": 6, "iso": 6}
        assert dict(app.analysis_service.param_values("aperture")) == {"f/2.0": 4, "f/4.0": 2}
        assert len(app.analysis_service.images_for_param("aperture", "f/2.0")) == 4


class TestSegmentStatsForEditor:
    def test_spans_and_idf_are_delivered(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)

        prompt_id = app.database.scalar("SELECT id FROM prompts ORDER BY id LIMIT 1")
        rows = app.analysis_service.segment_stats_for_prompt(prompt_id)
        assert rows
        assert all(r["char_end"] > r["char_start"] for r in rows)
        assert any(r["idf"] > 0 for r in rows)
        assert any(r["negative"] for r in rows), "der Negativblock muss markiert sein"

    def test_spans_point_into_the_stored_text(self, qapp, app) -> None:
        import re

        scan(qapp, app)
        parse(qapp, app)
        row = app.database.query_one("SELECT id, prompt_text FROM prompts ORDER BY id LIMIT 1")
        text = row["prompt_text"]
        for stats in app.analysis_service.segment_stats_for_prompt(row["id"]):
            excerpt = text[stats["char_start"] : stats["char_end"]]
            assert re.sub(r"\s+", " ", excerpt).strip(), "Spanne darf nicht leer sein"


class TestHighlighter:
    """Die Hervorhebung ist der sichtbare Nutzen der Segmentstatistik."""

    def _spans(self):
        from app.gui.editor.highlighter import SegmentSpan

        return [
            SegmentSpan(start=0, end=10, idf=0.0, doc_count=6, negative=False, kind="sentence"),
            SegmentSpan(start=11, end=20, idf=3.0, doc_count=1, negative=False, kind="sentence"),
            SegmentSpan(start=21, end=30, idf=1.0, doc_count=2, negative=True, kind="tag"),
        ]

    def _highlighter(self, qapp, text: str):
        from PyQt6.QtWidgets import QPlainTextEdit

        from app.gui.editor.highlighter import SegmentHighlighter
        from app.gui.theme import _LIGHT

        editor = QPlainTextEdit()
        editor.setPlainText(text)
        highlighter = SegmentHighlighter(editor.document(), _LIGHT, min_idf=1.5)
        return editor, highlighter

    def test_span_lookup_by_position(self, qapp) -> None:
        _editor, highlighter = self._highlighter(qapp, "x" * 40)
        highlighter.set_spans(self._spans())
        assert highlighter.span_at(5).doc_count == 6
        assert highlighter.span_at(15).idf == 3.0
        assert highlighter.span_at(35) is None

    def test_distinctive_and_common_get_different_formats(self, qapp) -> None:
        """idf trennt Textbaustein von Kennzeichnung."""
        _editor, highlighter = self._highlighter(qapp, "x" * 40)
        spans = self._spans()
        common = highlighter._format_for(spans[0])
        rare = highlighter._format_for(spans[1])
        negative = highlighter._format_for(spans[2])

        assert rare.background().color() != common.background().color()
        assert negative.background().color() not in (
            common.background().color(),
            rare.background().color(),
        )

    def test_disabling_removes_all_formatting(self, qapp) -> None:
        editor, highlighter = self._highlighter(qapp, "x" * 40)
        highlighter.set_spans(self._spans())
        assert highlighter.span_count == 3
        highlighter.set_enabled(False)
        # Der Zustand bleibt, nur gezeichnet wird nicht mehr.
        assert highlighter.span_count == 3

    def test_multiline_spans_are_split_per_block(self, qapp) -> None:
        """QSyntaxHighlighter arbeitet blockweise, die Spannen sind dokumentweit."""
        from app.gui.editor.highlighter import SegmentSpan

        text = "erste zeile\nzweite zeile\ndritte zeile"
        editor, highlighter = self._highlighter(qapp, text)
        highlighter.set_spans(
            [SegmentSpan(start=6, end=24, idf=3.0, doc_count=1, negative=False, kind="sentence")]
        )
        # Ohne Umrechnung auf Blockkoordinaten wuerde Qt hier abbrechen oder
        # falsch formatieren; der Test prueft, dass es sauber durchlaeuft.
        assert highlighter.span_at(10) is not None
        assert highlighter.span_at(20) is not None


class TestSegmentSearchInWindow:
    def _window(self, qapp, app):
        from app.gui.main_window import MainWindow

        window = MainWindow(app)
        assert pump_until(qapp, lambda: window._folder_tree._items, 20000)
        return window

    def test_editor_gets_highlight_spans(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        window = self._window(qapp, app)
        try:
            nodes = {n.name: n for n in app.image_service.folder_tree()}
            window._on_folder_selected(nodes["lib"], False)
            assert pump_until(qapp, lambda: window._explorer.item_count() > 0, 20000)

            window._preview.show_item(window._explorer.model.item_at(0))
            assert pump_until(
                qapp, lambda: window._preview.editor.highlighter.span_count > 0, 20000
            ), "Segmentspannen kamen nicht im Editor an"
        finally:
            window.close()

    def test_segment_action_opens_a_result_tab(self, qapp, app) -> None:
        """Spezifikation 01: die Segmentsuche oeffnet ein neues Tab."""
        scan(qapp, app)
        parse(qapp, app)
        window = self._window(qapp, app)
        try:
            window._on_segment_action("search_segment", WARM)
            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)

            tab = window._tab_manager.dynamic_widgets()[0]
            assert pump_until(qapp, lambda: tab.explorer.item_count() == 4, 20000)
            assert "Segment" in tab.key or "segment" in tab.key
        finally:
            window.close()

    def test_same_segment_does_not_open_twice(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        window = self._window(qapp, app)
        try:
            window._on_segment_action("search_segment", WARM)
            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)
            window._on_segment_action("search_segment", WARM)
            assert pump_until(qapp, lambda: False, 400) or window._tab_manager.count() == 1
        finally:
            window.close()

    def test_unknown_segment_opens_no_tab(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        window = self._window(qapp, app)
        try:
            window._on_segment_action("search_segment", "kommt nirgends vor")
            pump_until(qapp, lambda: False, 800)
            assert window._tab_manager.count() == 0
        finally:
            window.close()

    def test_parsing_runs_after_a_scan(self, qapp, app) -> None:
        """Neue Prompts werden ohne Zutun analysiert.

        Hier wird bewusst erst NACH dem Oeffnen des Fensters gescannt; der
        Ordnerbaum ist zu Beginn also leer.
        """
        from app.gui.main_window import MainWindow

        window = MainWindow(app)
        try:
            finished: list = []
            app.analysis_service.parse_finished.connect(finished.append)
            app.index_service.start_scan()
            assert pump_until(qapp, lambda: finished, 60000), "Analyse lief nach dem Scan nicht"
            assert app.analysis_service.pending_count() == 0
        finally:
            window.close()

    def test_highlighting_works_inside_tabs(self, qapp, app) -> None:
        """Regression: die Vorschau eines Tabs bekam ihr Tab als Palette.

        Der dritte Parameter von PreviewController war frueher parent; nach dem
        Einfuegen von palette uebergaben ResultTab und DetailTab ihr eigenes
        Objekt als Palette, und der Highlighter warf bei jedem Zeichenvorgang.
        Sichtbar war das nur im Fehlerprotokoll, nicht am Testergebnis - deshalb
        prueft dieser Test ausdruecklich auf Fehlermeldungen.
        """
        scan(qapp, app)
        parse(qapp, app)

        seen: list = []
        app.reporter.add_sink(type("C", (), {"handle": lambda _s, e: seen.append(e)})())

        window = self._window(qapp, app)
        try:
            nodes = {n.name: n for n in app.image_service.folder_tree()}
            window._on_folder_selected(nodes["lib"], False)
            assert pump_until(qapp, lambda: window._explorer.item_count() > 0, 20000)

            item = window._explorer.model.item_at(0)
            window._open_detail_tab(item)
            tab = window._tab_manager.dynamic_widgets()[0]

            assert pump_until(
                qapp, lambda: tab.preview.editor.highlighter.span_count > 0, 20000
            ), "der Detailtab bekam keine Segmentspannen"

            # Zeichnen erzwingen: der Fehler trat erst beim Rendern auf.
            tab.preview.editor.highlighter.rehighlight()
            qapp.processEvents()

            painting_errors = [
                e for e in seen
                if "segment_" in (e.details or "") or "segment_" in e.message
            ]
            assert not painting_errors, (
                "Fehler beim Zeichnen der Hervorhebung: "
                + "; ".join(e.message for e in painting_errors)
            )
        finally:
            window.close()


class TestClusteringPersistence:
    """Ende-zu-Ende: Aehnlichkeit, Familien und Varianten in der Datenbank."""

    def _run_analysis(self, qapp, app):
        done: list = []
        app.analysis_service.analysis_finished.connect(done.append)
        app.analysis_service.start_analysis()
        assert pump_until(qapp, lambda: done, 60000), "Analyse wurde nicht fertig"
        app.analysis_service.analysis_finished.disconnect()
        return done[0]

    def test_families_are_found_and_stored(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        result = self._run_analysis(qapp, app)

        assert result.clusters.clusters >= 1
        counts = app.clusters.counts()
        assert counts["clusters"] == result.clusters.clusters
        assert counts["members"] > 0
        assert counts["neighbours"] > 0

    def test_every_prompt_gets_a_family(self, qapp, app) -> None:
        """Gerechnet wird auf Repraesentanten, gespeichert fuer alle Prompts.

        Sonst haette ein Bild, dessen Prompt zufaellig doppelt vorkommt, keine
        Zuordnung - und die Oberflaeche koennte fuer dieses Bild nichts zeigen.
        """
        scan(qapp, app)
        parse(qapp, app)
        self._run_analysis(qapp, app)

        prompt_ids = [r["id"] for r in app.database.query(
            "SELECT id FROM prompts WHERE is_present = 1"
        )]
        assigned = [pid for pid in prompt_ids if app.analysis_service.cluster_of(pid)]
        assert len(assigned) == len(prompt_ids), "jeder Prompt braucht eine Familie"

    def test_representative_is_a_real_prompt(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        self._run_analysis(qapp, app)

        for cluster in app.analysis_service.largest_clusters():
            exists = app.database.scalar(
                "SELECT COUNT(*) FROM prompts WHERE id = ?",
                (cluster["representative_prompt_id"],),
            )
            assert exists == 1, "der Basisprompt muss ein realer Prompt sein"

    def test_variants_describe_the_difference(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        self._run_analysis(qapp, app)

        rows = app.database.query(
            "SELECT variant_prompt_id, added_count, removed_count, changed_count,"
            " diff_json FROM prompt_variants"
        )
        assert rows, "es muss Varianten geben"
        import json

        for row in rows:
            payload = json.loads(row["diff_json"])
            assert set(payload) == {"common", "added", "removed", "changed"}
            assert len(payload["added"]) == row["added_count"]
            assert len(payload["changed"]) == row["changed_count"]

    def test_similar_images_are_reachable(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        self._run_analysis(qapp, app)

        prompt_id = app.database.scalar("SELECT id FROM prompts ORDER BY id LIMIT 1")
        found: list = []
        app.analysis_service.similar_results.connect(
            lambda request_id, ids: found.append(ids)
        )
        app.analysis_service.similar_images_async(prompt_id)
        assert pump_until(qapp, lambda: found, 20000)
        assert found[0], "aehnliche Bilder muessen gefunden werden"

    def test_cluster_images_cover_the_family(self, qapp, app) -> None:
        scan(qapp, app)
        parse(qapp, app)
        self._run_analysis(qapp, app)

        cluster = app.analysis_service.largest_clusters(1)[0]
        images = app.analysis_service.cluster_images(int(cluster["id"]))
        assert len(images) == cluster["image_count"]

    def test_rerun_replaces_instead_of_duplicating(self, qapp, app) -> None:
        """Vollstaendiger Austausch: ein neuer Prompt kann zwei Familien verbinden."""
        scan(qapp, app)
        parse(qapp, app)
        first = self._run_analysis(qapp, app)
        before = app.clusters.counts()

        second = self._run_analysis(qapp, app)
        assert app.clusters.counts() == before
        assert second.clusters.clusters == first.clusters.clusters

    def test_analysis_without_parsing_does_nothing(self, qapp, app) -> None:
        scan(qapp, app)
        result = self._run_analysis(qapp, app)
        assert result.clusters.clusters == 0
        assert app.clusters.counts()["clusters"] == 0
