"""Tests der Vergleichsansicht: Diff-Engine, Service, Tab."""

from __future__ import annotations

import pytest
from PIL import Image

from app.analysis.diff import (
    DiffKind,
    DiffSegment,
    compare_prompts,
    refine_words,
)
from app.application import Application
from app.gui.compare.scroll_sync import ScrollLink, synchronized_value
from app.config.bootstrap import BootstrapConfig, save_bootstrap
from tests.conftest import pump_until

BASE = "A cinematic editorial portrait of a young woman"
WARM = "warm golden sunlight dominates the scene"
COLD = "cold blue moonlight dominates the scene"
SKIN = "Skin texture is realistic and detailed"
CROWN = "She wears a sleek avant-garde crown"


def build_side(sentences: list[str], ids: list[int]) -> tuple[list[DiffSegment], str]:
    """Baut Segmente mit Spannen, die exakt auf den erzeugten Text zeigen."""
    segments: list[DiffSegment] = []
    text = ""
    position = 0
    for token_id, sentence in zip(ids, sentences):
        text += sentence + "\n"
        segments.append(DiffSegment(token_id, sentence, position, position + len(sentence)))
        position += len(sentence) + 1
    return segments, text


class TestDiffEngine:
    def test_identical_prompts(self) -> None:
        left, _ = build_side([BASE, WARM], [1, 2])
        diff = compare_prompts(left, left)
        assert diff.is_identical
        assert diff.stats.common == 2
        assert diff.stats.agreement == pytest.approx(1.0)
        assert all(span.kind is DiffKind.EQUAL for span in diff.left)

    def test_changed_sentence_becomes_one_change(self) -> None:
        """Der Kern der Spezifikation 05.

        "warm golden sunlight" zu "cold blue moonlight" ist EINE Aenderung an
        einer Stelle - nicht ein verschwundener und ein neuer Satz. Ohne die
        Paarung zeigte die Ansicht eine rote und eine gruene Zeile.
        """
        left, _ = build_side([BASE, WARM], [1, 2])
        right, _ = build_side([BASE, COLD], [1, 3])
        diff = compare_prompts(left, right)

        assert diff.stats.changed == 1
        assert diff.stats.added == 0 and diff.stats.removed == 0
        assert [s.kind for s in diff.left] == [DiffKind.EQUAL, DiffKind.CHANGED]
        assert [s.kind for s in diff.right] == [DiffKind.EQUAL, DiffKind.CHANGED]

    def test_unrelated_sentences_are_add_and_remove(self) -> None:
        left, _ = build_side([BASE, WARM], [1, 2])
        right, _ = build_side([BASE, CROWN], [1, 9])
        diff = compare_prompts(left, right)

        assert diff.stats.changed == 0
        assert diff.stats.removed == 1 and diff.stats.added == 1
        assert diff.left[1].kind is DiffKind.REMOVED
        assert diff.right[1].kind is DiffKind.ADDED

    def test_pure_addition(self) -> None:
        left, _ = build_side([BASE], [1])
        right, _ = build_side([BASE, CROWN], [1, 9])
        diff = compare_prompts(left, right)
        assert diff.stats.added == 1
        assert diff.stats.removed == 0
        assert len(diff.left) == 1

    def test_pure_removal(self) -> None:
        left, _ = build_side([BASE, CROWN], [1, 9])
        right, _ = build_side([BASE], [1])
        diff = compare_prompts(left, right)
        assert diff.stats.removed == 1
        assert diff.stats.added == 0

    def test_word_level_refinement_points_at_the_real_change(self) -> None:
        """Ohne Wortebene muesste der Benutzer zwei lange Saetze selbst vergleichen."""
        left, left_text = build_side([WARM], [2])
        right, right_text = build_side([COLD], [3])
        diff = compare_prompts(left, right)

        left_words = [left_text[w.start : w.end] for w in diff.left[0].words]
        right_words = [right_text[w.start : w.end] for w in diff.right[0].words]
        assert left_words == ["warm", "golden", "sunlight"]
        assert right_words == ["cold", "blue", "moonlight"]

    def test_refinement_can_be_switched_off(self) -> None:
        left, _ = build_side([WARM], [2])
        right, _ = build_side([COLD], [3])
        diff = compare_prompts(left, right, refine=False)
        assert diff.left[0].words == ()

    def test_refine_words_skips_on_span_mismatch(self) -> None:
        """Bei abweichender Laenge lieber nichts markieren als das Falsche.

        Der gespeicherte Segmenttext ist normalisiert; steht im Original ein
        doppeltes Leerzeichen, passen die Positionen nicht mehr.
        """
        left = DiffSegment(1, "warm golden light", 0, 25)  # Spanne laenger als Text
        right = DiffSegment(2, "cold golden light", 0, 25)
        assert refine_words(left, right) == ((), ())

    def test_spans_stay_inside_the_original(self) -> None:
        left, left_text = build_side([BASE, WARM, SKIN], [1, 2, 4])
        right, right_text = build_side([BASE, COLD, CROWN], [1, 3, 9])
        diff = compare_prompts(left, right)

        for span in diff.left:
            assert left_text[span.start : span.end].strip()
            for word in span.words:
                assert span.start <= word.start < word.end <= span.end
        for span in diff.right:
            assert right_text[span.start : span.end].strip()

    def test_spans_are_sorted(self) -> None:
        left, _ = build_side([BASE, WARM, SKIN], [1, 2, 4])
        right, _ = build_side([CROWN, BASE, COLD], [9, 1, 3])
        diff = compare_prompts(left, right)
        assert [s.start for s in diff.left] == sorted(s.start for s in diff.left)
        assert [s.start for s in diff.right] == sorted(s.start for s in diff.right)

    def test_partner_index_links_both_sides(self) -> None:
        left, _ = build_side([BASE, WARM], [1, 2])
        right, _ = build_side([BASE, COLD], [1, 3])
        diff = compare_prompts(left, right)
        changed_left = next(s for s in diff.left if s.kind is DiffKind.CHANGED)
        assert changed_left.partner == 1
        assert diff.pairs == ((1, 1),)

    def test_agreement_is_a_ratio(self) -> None:
        left, _ = build_side([BASE, WARM, SKIN], [1, 2, 4])
        right, _ = build_side([BASE, COLD, SKIN], [1, 3, 4])
        diff = compare_prompts(left, right)
        assert diff.stats.common == 2
        assert diff.stats.different == 1
        assert diff.stats.agreement == pytest.approx(2 / 3)

    def test_empty_sides(self) -> None:
        assert compare_prompts([], []).is_identical
        left, _ = build_side([BASE], [1])
        diff = compare_prompts(left, [])
        assert diff.stats.removed == 1

    def test_reordering_is_not_a_change(self) -> None:
        """Zwei vertauschte Saetze bleiben gemeinsame Saetze.

        SequenceMatcher findet den laengsten gemeinsamen Block; der verschobene
        Satz erscheint als Verschiebung, nicht als inhaltliche Aenderung.
        """
        left, _ = build_side([BASE, WARM, SKIN], [1, 2, 4])
        right, _ = build_side([BASE, SKIN, WARM], [1, 4, 2])
        diff = compare_prompts(left, right)
        assert diff.stats.changed == 0
        assert diff.stats.common >= 2


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "lib"
    root.mkdir(parents=True)
    variants = {
        0: BASE + ".\n" + WARM + ".\n" + SKIN + ".\n",
        1: BASE + ".\n" + COLD + ".\n" + SKIN + ".\n",
        2: BASE + ".\n" + WARM + ".\n" + SKIN + ".\n" + CROWN + ".\n",
    }
    for index, text in variants.items():
        Image.new("RGB", (16, 16), (index * 60, 90, 140)).save(
            root / ("p" + str(index) + ".jpg")
        )
        (root / ("p" + str(index) + ".txt")).write_text(text, encoding="utf-8")
    # ein Bild ohne Prompt
    Image.new("RGB", (16, 16), (10, 10, 10)).save(root / "lonely.jpg")
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
    instance.build_services()

    finished: list = []
    instance.index_service.scan_finished.connect(finished.append)
    instance.index_service.start_scan()
    assert pump_until(qapp, lambda: finished, 30000)

    parsed: list = []
    instance.analysis_service.parse_finished.connect(parsed.append)
    instance.analysis_service.start_parsing()
    assert pump_until(qapp, lambda: parsed, 60000)

    yield instance
    instance.shutdown()


def image_ids(app) -> dict[str, int]:
    return {
        r["filename"]: r["id"]
        for r in app.database.query("SELECT id, filename FROM images")
    }


class TestCompareService:
    def test_comparison_is_delivered(self, qapp, app) -> None:
        ids = image_ids(app)
        received: list = []
        app.compare_service.comparison_ready.connect(received.append)
        app.compare_service.compare_async(ids["p0.jpg"], ids["p1.jpg"])

        assert pump_until(qapp, lambda: received, 20000)
        pair = received[0]
        assert pair.complete
        assert pair.left_image.filename == "p0.jpg"
        assert pair.diff is not None
        assert pair.diff.stats.changed == 1, "warm gegen cold ist eine Aenderung"

    def test_addition_is_detected(self, qapp, app) -> None:
        ids = image_ids(app)
        received: list = []
        app.compare_service.comparison_ready.connect(received.append)
        app.compare_service.compare_async(ids["p0.jpg"], ids["p2.jpg"])

        assert pump_until(qapp, lambda: received, 20000)
        assert received[0].diff.stats.added == 1
        assert received[0].diff.stats.changed == 0

    def test_missing_prompt_is_reported_not_crashed(self, qapp, app) -> None:
        """Spezifikation 08: ein fehlender Prompt darf nichts abstuerzen lassen."""
        ids = image_ids(app)
        failures: list = []
        app.compare_service.comparison_failed.connect(failures.append)
        app.compare_service.compare_async(ids["p0.jpg"], ids["lonely.jpg"])

        assert pump_until(qapp, lambda: failures, 20000)
        assert "Prompt" in failures[0]

    def test_unknown_image_is_reported(self, qapp, app) -> None:
        ids = image_ids(app)
        failures: list = []
        app.compare_service.comparison_failed.connect(failures.append)
        app.compare_service.compare_async(ids["p0.jpg"], 999999)
        assert pump_until(qapp, lambda: failures, 20000)

    def test_stale_comparison_is_discarded(self, qapp, app) -> None:
        """Klickt der Benutzer weiter, darf das alte Ergebnis nicht erscheinen."""
        ids = image_ids(app)
        received: list = []
        app.compare_service.comparison_ready.connect(received.append)

        first = app.compare_service.compare_async(ids["p0.jpg"], ids["p1.jpg"])
        second = app.compare_service.compare_async(ids["p0.jpg"], ids["p2.jpg"])
        assert second > first

        assert pump_until(qapp, lambda: received, 20000)
        pump_until(qapp, lambda: False, 300)
        assert len(received) == 1
        assert received[0].right_image.filename == "p2.jpg"

    def test_diff_of_two_prompts_directly(self, app) -> None:
        prompts = {
            r["filename"]: r["id"]
            for r in app.database.query("SELECT id, filename FROM prompts")
        }
        diff = app.compare_service.diff_of(prompts["p0.txt"], prompts["p1.txt"])
        assert diff.stats.changed == 1
        assert diff.stats.common == 2


class TestScrollSync:
    """Der Gleichlauf als reine Funktion - unabhaengig von Qts Innenleben.

    Der Regelfall ist nach dem Hoehenausgleich die IDENTITAET: beide Seiten
    sind gleich hoch, also ist dieselbe Zahl richtig. Der anteilige Weg ist
    der Ausnahmefall fuer die Zeit, in der das Layout noch nicht fertig ist.
    """

    def test_gleiche_reichweite_ist_die_identitaet(self) -> None:
        """Kein Runden, kein Fehler, der sich aufsummieren koennte."""
        for wert in (0, 1, 37, 99, 100):
            assert synchronized_value(wert, 100, 100) == wert

    def test_ungleiche_reichweite_rechnet_anteilig(self) -> None:
        assert synchronized_value(50, 100, 200) == 100
        assert synchronized_value(100, 100, 200) == 200
        assert synchronized_value(0, 100, 200) == 0

    def test_kuerzeres_ziel(self) -> None:
        assert synchronized_value(100, 200, 50) == 25

    def test_quelle_ohne_reichweite_bleibt_am_anfang(self) -> None:
        """Ein kurzer Text hat keinen Rollbalken - alles andere waere geraten."""
        assert synchronized_value(0, 0, 500) == 0

    def test_ziel_ohne_reichweite(self) -> None:
        assert synchronized_value(50, 100, 0) == 0

    def test_versetzter_nullpunkt(self) -> None:
        assert synchronized_value(15, 20, 200, source_min=10, target_min=100) == 150

    def test_kein_drift_ueber_viele_schritte(self) -> None:
        """Der eigentliche Nachweis: bei gleicher Reichweite driftet nichts.

        Der Vorgaenger rechnete auch dann anteilig um. Ueber viele Schritte
        summierten sich die Rundungsfehler, und genau das war der gemeldete
        Fehler - oben buendig, nach etwas Scrollen nicht mehr.
        """
        for schritt in range(0, 1000, 7):
            assert synchronized_value(schritt, 999, 999) == schritt


class TestScrollLink:
    class Balken:
        def __init__(self, maximum: int = 100) -> None:
            self.wert = 0
            self._max = maximum

        def minimum(self) -> int:
            return 0

        def maximum(self) -> int:
            return self._max

        def setValue(self, wert: int) -> None:  # noqa: N802 (Qt-Nachbau)
            self.wert = wert

    def test_uebertraegt_den_wert(self) -> None:
        link = ScrollLink()
        a, b = self.Balken(), self.Balken()
        assert link.transfer(a, b, 42)
        assert b.wert == 42

    def test_sperre_verhindert_rueckkopplung(self) -> None:
        """Ohne sie loest jedes Setzen das Gegensetzen aus, und beide schaukeln sich auf."""
        link = ScrollLink()
        a, b = self.Balken(), self.Balken()

        gesehen: list[int] = []

        class Rueckkopplung(self.Balken):
            def setValue(inner, wert):  # noqa: N802, N805
                gesehen.append(wert)
                super().setValue(wert)
                link.transfer(inner, a, wert)  # der Rueckweg

        link.transfer(a, Rueckkopplung(), 10)
        assert gesehen == [10], "Der Rueckweg war nicht gesperrt"

    def test_sperre_wird_wieder_freigegeben(self) -> None:
        link = ScrollLink()
        a, b = self.Balken(), self.Balken()
        link.transfer(a, b, 5)
        assert not link.busy
        assert link.transfer(a, b, 6), "Nach der ersten Uebertragung war gesperrt"


class TestCompareTab:
    def _window(self, qapp, app):
        from app.gui.main_window import MainWindow

        window = MainWindow(app)
        assert pump_until(qapp, lambda: window._folder_tree._items, 20000)
        return window

    def test_two_marks_enable_the_action(self, qapp, app) -> None:
        window = self._window(qapp, app)
        try:
            ids = image_ids(app)
            assert not window._action_compare.isEnabled()
            app.selection_service.toggle(ids["p0.jpg"])
            assert not window._action_compare.isEnabled()
            app.selection_service.toggle(ids["p1.jpg"])
            assert window._action_compare.isEnabled()
        finally:
            window.close()

    def test_compare_opens_a_tab(self, qapp, app) -> None:
        window = self._window(qapp, app)
        try:
            ids = image_ids(app)
            app.selection_service.toggle(ids["p0.jpg"])
            app.selection_service.toggle(ids["p1.jpg"])
            window._start_compare()

            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)
            tab = window._tab_manager.dynamic_widgets()[0]
            assert tab.key.startswith("compare:")
            assert "p0" in tab._left_pane.toPlainText() or tab._left_pane.toPlainText()
            assert tab._left_pane.highlighter.row_count > 0
            assert tab._right_pane.highlighter.row_count > 0
        finally:
            window.close()

    def test_without_two_marks_nothing_opens(self, qapp, app) -> None:
        window = self._window(qapp, app)
        try:
            app.selection_service.toggle(image_ids(app)["p0.jpg"])
            window._start_compare()
            pump_until(qapp, lambda: False, 500)
            assert window._tab_manager.count() == 0
            assert "zwei Bilder" in window._status.currentMessage()
        finally:
            window.close()

    def test_same_pair_does_not_open_twice(self, qapp, app) -> None:
        window = self._window(qapp, app)
        try:
            ids = image_ids(app)
            app.selection_service.toggle(ids["p0.jpg"])
            app.selection_service.toggle(ids["p1.jpg"])
            window._start_compare()
            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)
            window._start_compare()
            pump_until(qapp, lambda: False, 500)
            assert window._tab_manager.count() == 1
        finally:
            window.close()

    def test_scroll_lock_is_released(self, qapp, app) -> None:
        """Die Rueckkopplungssperre darf nicht haengenbleiben.

        Bliebe sie gesetzt, waere der Gleichlauf nach dem ersten Scrollen tot.
        """
        window = self._window(qapp, app)
        try:
            ids = image_ids(app)
            app.selection_service.toggle(ids["p0.jpg"])
            app.selection_service.toggle(ids["p2.jpg"])
            window._start_compare()
            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)

            tab = window._tab_manager.dynamic_widgets()[0]
            tab._left_pane.verticalScrollBar().setValue(5)
            qapp.processEvents()
            assert not tab._scroll.busy
        finally:
            window.close()

    def test_identical_prompts_are_reported(self, qapp, app) -> None:
        window = self._window(qapp, app)
        try:
            ids = image_ids(app)
            received: list = []
            app.compare_service.comparison_ready.connect(received.append)
            app.compare_service.compare_async(ids["p0.jpg"], ids["p0.jpg"])
            assert pump_until(qapp, lambda: received, 20000)
            window._on_comparison_ready(received[0])

            tab = window._tab_manager.dynamic_widgets()[0]
            assert "identisch" in tab._status.text()
        finally:
            window.close()

    def test_statistics_line_is_informative(self, qapp, app) -> None:
        window = self._window(qapp, app)
        try:
            ids = image_ids(app)
            app.selection_service.toggle(ids["p0.jpg"])
            app.selection_service.toggle(ids["p1.jpg"])
            window._start_compare()
            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)

            text = window._tab_manager.dynamic_widgets()[0]._status.text()
            assert "Unterschied" in text
            assert "Uebereinstimmung" in text
        finally:
            window.close()


class TestAusleiten:
    """Speichern darf NIE eine der beiden Vorlagen ueberschreiben.

    Die Regel ist baulich durchgesetzt, nicht nur vereinbart: der PromptService
    kennt keine Methode, die in `prompt.full_path` schreibt. Diese Tests halten
    genau das fest - eine Vereinbarung haette man versehentlich brechen koennen,
    eine fehlende Methode nicht.
    """

    def _prompt(self, app, name: str):
        ids = image_ids(app)
        bild = app.images.by_id(ids[name])
        return app.prompts.by_id(bild.prompt_id)

    def test_es_gibt_keine_methode_die_das_original_schreibt(self) -> None:
        """Der wichtigste Test der Klasse.

        Er prueft nicht Verhalten, sondern die Bauweise: waere `save_async`
        wieder da, koennte ein Aufrufer das Original ueberschreiben, ohne dass
        einer der anderen Tests etwas merkt.
        """
        from app.services.prompt_service import PromptService

        assert not hasattr(PromptService, "save_async")
        assert hasattr(PromptService, "export_async")

        import inspect

        quelle = inspect.getsource(PromptService)
        assert "full_path" not in quelle, (
            "Der PromptService greift wieder auf prompt.full_path zu"
        )

    def test_das_repository_schreibt_keinen_prompttext(self) -> None:
        from app.repositories.prompt_repo import PromptRepository

        assert not hasattr(PromptRepository, "update_text")

    def test_export_schreibt_die_neue_datei(self, qapp, app, tmp_path) -> None:
        prompt = self._prompt(app, "p0.jpg")
        ziel = tmp_path / "ausgeleitet.txt"
        neu = prompt.text + "Ein zusaetzlicher Satz.\n"

        gemeldet: list = []
        app.prompt_service.save_finished.connect(
            lambda pid, status, meldung: gemeldet.append(status)
        )
        app.prompt_service.export_async(prompt, neu, str(ziel))
        assert pump_until(qapp, lambda: gemeldet, 20000)

        assert gemeldet[0] == "saved"
        assert ziel.exists()
        assert ziel.read_text(encoding="utf-8").replace("\r\n", "\n") == neu.replace(
            "\r\n", "\n"
        )

    def test_das_original_bleibt_unangetastet(self, qapp, app, tmp_path) -> None:
        from pathlib import Path

        prompt = self._prompt(app, "p0.jpg")
        vorher = Path(prompt.full_path).read_bytes()

        fertig: list = []
        app.prompt_service.save_finished.connect(lambda *a: fertig.append(a))
        app.prompt_service.export_async(
            prompt, "Ein voellig anderer Text.\n", str(tmp_path / "woanders.txt")
        )
        assert pump_until(qapp, lambda: fertig, 20000)

        assert Path(prompt.full_path).read_bytes() == vorher

    def test_die_datenbank_bleibt_unangetastet(self, qapp, app, tmp_path) -> None:
        """Die neue Datei ist eine neue Datei, kein geaenderter Prompt.

        Liegt sie in einer Bibliothekswurzel, nimmt der naechste Abgleich sie
        von selbst auf. Die Zeile des Originals bleibt richtig, weil das
        Original unveraendert ist.
        """
        prompt = self._prompt(app, "p1.jpg")
        vorher = app.database.query_one(
            "SELECT prompt_text, parse_version FROM prompts WHERE id = ?", (prompt.id,)
        )

        fertig: list = []
        app.prompt_service.save_finished.connect(lambda *a: fertig.append(a))
        app.prompt_service.export_async(
            prompt, "Etwas ganz anderes.\n", str(tmp_path / "neu.txt")
        )
        assert pump_until(qapp, lambda: fertig, 20000)

        nachher = app.database.query_one(
            "SELECT prompt_text, parse_version FROM prompts WHERE id = ?", (prompt.id,)
        )
        assert nachher["prompt_text"] == vorher["prompt_text"]
        assert nachher["parse_version"] == vorher["parse_version"], (
            "Die Auswertung des Originals wurde ohne Grund verworfen"
        )

    def test_encoding_und_zeilenenden_kommen_vom_original(
        self, qapp, app, tmp_path
    ) -> None:
        prompt = self._prompt(app, "p0.jpg")
        ziel = tmp_path / "enden.txt"

        fertig: list = []
        app.prompt_service.save_finished.connect(lambda *a: fertig.append(a))
        app.prompt_service.export_async(prompt, prompt.text, str(ziel))
        assert pump_until(qapp, lambda: fertig, 20000)

        from pathlib import Path

        assert ziel.read_bytes() == Path(prompt.full_path).read_bytes(), (
            "Derselbe Text muss byte-gleich herauskommen"
        )

    def test_abgeschaltet_wird_nichts_geschrieben(self, qapp, app, tmp_path) -> None:
        app.settings.set("compare.allow_export", False)
        ziel = tmp_path / "darf-nicht.txt"

        gemeldet: list = []
        app.prompt_service.save_finished.connect(
            lambda pid, status, meldung: gemeldet.append(status)
        )
        app.prompt_service.export_async(
            self._prompt(app, "p0.jpg"), "Nein.\n", str(ziel)
        )
        assert pump_until(qapp, lambda: gemeldet, 20000)

        assert gemeldet[0] == "failed"
        assert not ziel.exists()


class TestOriginalpfadWirdAbgelehnt:
    """Die zweite Sperre: der Dialog nimmt keinen der beiden Originalpfade an."""

    def _tab(self, qapp, app):
        from app.gui.main_window import MainWindow

        window = MainWindow(app)
        assert pump_until(qapp, lambda: window._folder_tree._items, 20000)
        ids = image_ids(app)
        app.selection_service.toggle(ids["p0.jpg"])
        app.selection_service.toggle(ids["p1.jpg"])
        window._start_compare()
        assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)
        return window, window._tab_manager.dynamic_widgets()[0]

    def test_beide_originale_gelten_als_original(self, qapp, app) -> None:
        window, tab = self._tab(qapp, app)
        try:
            assert tab._is_original(tab._pair.left_prompt.full_path)
            assert tab._is_original(tab._pair.right_prompt.full_path), (
                "Auch das Gegenstueck ist eine Vorlage - wer A ueber die Datei"
                " von B speichert, vernichtet genauso ein Original"
            )
            assert not tab._is_original("Z:/irgendwo/anders.txt")
        finally:
            window.close()

    def test_gemischte_trennzeichen_taeuschen_die_pruefung_nicht(
        self, qapp, app
    ) -> None:
        """Die Datenbank haelt Pfade so, wie der Scanner sie sah.

        Der Dateidialog liefert Schraegstriche. Ein Vergleich mit == wuerde
        dieselbe Datei fuer zwei verschiedene halten - und die Sperre still
        aushebeln.
        """
        window, tab = self._tab(qapp, app)
        try:
            echt = tab._pair.left_prompt.full_path
            assert tab._is_original(echt.replace("\\", "/"))
            assert tab._is_original(echt.replace("/", "\\"))
            assert tab._is_original(echt.upper()) or tab._is_original(echt.lower())
        finally:
            window.close()

    def test_der_vorschlag_ist_nicht_das_original(self, qapp, app) -> None:
        window, tab = self._tab(qapp, app)
        try:
            for prompt in (tab._pair.left_prompt, tab._pair.right_prompt):
                vorschlag = tab._suggested_name(prompt)
                assert not tab._is_original(vorschlag), (
                    "Ein unbedachtes Bestaetigen wuerde das Original treffen"
                )
                assert vorschlag.endswith(".txt")
        finally:
            window.close()
