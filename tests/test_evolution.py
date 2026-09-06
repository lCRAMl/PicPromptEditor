"""Tests der Prompt Evolution: Baumbildung, Cache, Tab."""

from __future__ import annotations

import os

import pytest
from PIL import Image

from app.analysis.evolution import (
    ALGO_VERSION,
    EvolutionInput,
    EvolutionTree,
    build_tree,
)
from app.application import Application
from app.config.bootstrap import BootstrapConfig, save_bootstrap
from tests.conftest import pump_until

BASE = "A cinematic editorial portrait of a young woman.\nThe subject never faces the camera.\n"
WARM = "warm golden sunlight dominates the scene.\n"
CROWN = "She wears a sleek avant-garde crown.\n"
EYES = "Maintain the makeup and add smokey eyes.\n"


def jaccard(left: frozenset[int], right: frozenset[int]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def first_pair(removed: set[int], added: set[int]) -> list[tuple[int, int]]:
    return [(min(removed), min(added))] if removed and added else []


class TestTreeBuilding:
    def _members(self) -> list[EvolutionInput]:
        return [
            EvolutionInput(1, 1000, frozenset({1, 2, 3}), "Basis"),
            EvolutionInput(2, 2000, frozenset({1, 2, 3, 10}), "plus Licht"),
            EvolutionInput(3, 3000, frozenset({1, 2, 3, 10, 11}), "plus Krone"),
            EvolutionInput(4, 4000, frozenset({1, 2, 3, 20}), "anderer Zweig"),
            EvolutionInput(5, 5000, frozenset({1, 2, 3, 10, 12}), "plus Brille"),
        ]

    def test_root_is_the_oldest(self) -> None:
        """Die Richtung kommt aus der Zeit - ohne sie haette der Baum keine Spitze."""
        tree = build_tree(7, self._members(), jaccard)
        assert tree.root_id == 1
        assert tree.nodes[1].is_root
        assert tree.nodes[1].depth == 0

    def test_parent_is_always_older(self) -> None:
        """Damit ist der Baum kreisfrei, ohne Zyklenpruefung."""
        tree = build_tree(7, self._members(), jaccard)
        for node in tree.nodes.values():
            if node.parent_id is None:
                continue
            assert tree.nodes[node.parent_id].modified_time <= node.modified_time

    def test_parent_is_the_most_similar_earlier_prompt(self) -> None:
        tree = build_tree(7, self._members(), jaccard)
        assert tree.nodes[2].parent_id == 1
        assert tree.nodes[3].parent_id == 2, "naeher an 2 als an 1"
        assert tree.nodes[5].parent_id == 2
        assert tree.nodes[4].parent_id == 1, "der andere Zweig haengt an der Basis"

    def test_branching(self) -> None:
        tree = build_tree(7, self._members(), jaccard)
        assert sorted(tree.children_of(2)) == [3, 5]
        assert tree.depth == 2

    def test_path_to_root_is_the_history(self) -> None:
        tree = build_tree(7, self._members(), jaccard)
        assert tree.path_to_root(5) == [5, 2, 1]

    def test_changes_are_recorded(self) -> None:
        tree = build_tree(7, self._members(), jaccard, change_pairing=first_pair)
        assert tree.nodes[2].added == (10,)
        assert tree.nodes[2].removed == ()
        assert tree.nodes[2].change_summary() == "1 ergaenzt"
        assert tree.nodes[1].change_summary() == "Ausgangsprompt"

    def test_replacement_becomes_a_change(self) -> None:
        members = [
            EvolutionInput(1, 1000, frozenset({1, 10}), "vorher"),
            EvolutionInput(2, 2000, frozenset({1, 11}), "nachher"),
        ]
        tree = build_tree(1, members, jaccard, change_pairing=first_pair)
        node = tree.nodes[2]
        assert node.changed == ((10, 11),)
        assert node.added == () and node.removed == ()
        assert node.change_summary() == "1 geaendert"

    def test_equal_timestamps_stay_deterministic(self) -> None:
        """Bei gleicher Zeit entscheidet die Id - sonst waere der Baum zufaellig."""
        members = [
            EvolutionInput(5, 1000, frozenset({1}), "a"),
            EvolutionInput(3, 1000, frozenset({1}), "b"),
            EvolutionInput(9, 1000, frozenset({1}), "c"),
        ]
        first = build_tree(1, members, jaccard)
        second = build_tree(1, list(reversed(members)), jaccard)
        assert first.root_id == second.root_id == 3

    def test_truncation_keeps_the_newest(self) -> None:
        """Ein Baum mit tausend Knoten waere unlesbar; die aktuelle Entwicklung zaehlt."""
        members = [
            EvolutionInput(i, i * 100, frozenset({1, i}), "p" + str(i))
            for i in range(1, 30)
        ]
        tree = build_tree(1, members, jaccard, max_nodes=10)
        assert tree.size == 10
        assert tree.truncated == 19
        assert max(node.modified_time for node in tree.nodes.values()) == 2900
        assert min(node.modified_time for node in tree.nodes.values()) == 2000

    def test_single_member(self) -> None:
        tree = build_tree(1, [EvolutionInput(1, 1000, frozenset({1}))], jaccard)
        assert tree.size == 1
        assert tree.root_id == 1

    def test_empty_family(self) -> None:
        tree = build_tree(1, [], jaccard)
        assert tree.size == 0
        assert tree.root_id is None

    def test_json_roundtrip_is_lossless(self) -> None:
        """tree_json ist die fertige Struktur - ein Tab soll sie nur laden."""
        original = build_tree(7, self._members(), jaccard, change_pairing=first_pair)
        restored = EvolutionTree.from_json(original.to_json())

        assert restored.size == original.size
        assert restored.root_id == original.root_id
        assert restored.cluster_id == original.cluster_id
        for prompt_id, node in original.nodes.items():
            other = restored.nodes[prompt_id]
            assert other.parent_id == node.parent_id
            assert other.depth == node.depth
            assert other.children == node.children
            assert other.added == node.added
            assert other.changed == node.changed
            assert other.label == node.label

    def test_json_survives_truncation_marker(self) -> None:
        members = [
            EvolutionInput(i, i * 100, frozenset({1, i}), "p" + str(i)) for i in range(1, 20)
        ]
        tree = build_tree(1, members, jaccard, max_nodes=5)
        assert EvolutionTree.from_json(tree.to_json()).truncated == tree.truncated


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "lib"
    root.mkdir(parents=True)
    plan = [
        (0, BASE + WARM),
        (1, BASE + WARM + CROWN),
        (2, BASE + WARM + CROWN + EYES),
        (3, BASE + WARM + CROWN + EYES + "Extra detail line here.\n"),
    ]
    for index, text in plan:
        Image.new("RGB", (16, 16), (index * 50, 90, 140)).save(
            root / ("p" + str(index) + ".jpg")
        )
        target = root / ("p" + str(index) + ".txt")
        target.write_text(text, encoding="utf-8")
        # Klare zeitliche Reihenfolge erzwingen; der Baum haengt daran.
        info = target.stat()
        os.utime(
            target,
            ns=(info.st_atime_ns, 1_700_000_000_000_000_000 + index * 3_600_000_000_000),
        )

    # Zwei Faelle, die in einer echten Bibliothek die Mehrheit stellen und
    # frueher dieselbe irrefuehrende Meldung bekamen wie eine noch nicht
    # berechnete Familie:
    #   * ein Bild ganz ohne Promptdatei (in der echten Bibliothek drei
    #     Viertel des Bestands),
    #   * ein Prompt, der zu keinem anderen passt und deshalb allein bleibt.
    # Die Namen beginnen mit zz_, damit sie nach den p*-Dateien einsortieren:
    # mehrere Tests greifen auf 'das erste Bild' zu.
    Image.new("RGB", (16, 16), (10, 20, 30)).save(root / "zz_ohne_prompt.jpg")

    Image.new("RGB", (16, 16), (200, 30, 30)).save(root / "zz_einzelgaenger.jpg")
    (root / "zz_einzelgaenger.txt").write_text(
        os.linesep.join(
            [
                "Voellig anderes Thema ohne jede Beziehung zu den uebrigen Prompts.",
                "Zweite Zeile ueber Fachwerkhaeuser und Regenrinnen.",
                "Dritte Zeile ueber Zahnraeder und Dampfmaschinen.",
                "",
            ]
        ),
        encoding="utf-8",
    )
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
    # Siehe tests/test_analysis.py: die Vorgabe 0.90 ist an der echten
    # Bibliothek kalibriert. Dieser Korpus ist klein und kurz - ohne den
    # ausdruecklichen Wert entstuende keine Familie und damit auch kein Baum.
    instance.settings.set("analysis.cluster_threshold", 0.45)
    instance.build_services()

    for signal, starter in (
        (instance.index_service.scan_finished, instance.index_service.start_scan),
        (instance.analysis_service.parse_finished, instance.analysis_service.start_parsing),
        (instance.analysis_service.analysis_finished, instance.analysis_service.start_analysis),
    ):
        done: list = []
        signal.connect(done.append)
        starter()
        assert pump_until(qapp, lambda: done, 60000)
        signal.disconnect()

    yield instance
    instance.shutdown()


def build_trees(qapp, app):
    done: list = []
    app.evolution_service.build_finished.connect(done.append)
    app.evolution_service.start_build()
    assert pump_until(qapp, lambda: done, 60000), "Baumbau wurde nicht fertig"
    app.evolution_service.build_finished.disconnect()
    return done[0]


class TestEvolutionPersistence:
    def test_trees_are_built_and_stored(self, qapp, app) -> None:
        assert app.evolution_service.pending_count() >= 1
        stats = build_trees(qapp, app)
        assert stats.trees >= 1
        assert stats.nodes >= 2
        assert app.evolution_service.counts()["trees"] == stats.trees
        assert app.evolution_service.pending_count() == 0

    def test_second_run_finds_nothing_to_do(self, qapp, app) -> None:
        build_trees(qapp, app)
        again = build_trees(qapp, app)
        assert again.trees == 0

    def test_tab_load_never_computes(self, qapp, app) -> None:
        """Spezifikation 06 verbietet die Neuberechnung beim Oeffnen ausdruecklich."""
        build_trees(qapp, app)
        image_id = app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")

        received: list = []
        app.evolution_service.tree_ready.connect(lambda r, tree: received.append(tree))
        app.evolution_service.tree_for_image_async(image_id)

        assert pump_until(qapp, lambda: received, 20000)
        assert not app.evolution_service.build_running, "es darf nichts gerechnet werden"
        assert received[0].size >= 2

    def test_missing_tree_is_reported_not_computed(self, qapp, app) -> None:
        """Ohne Cacheeintrag sagt der Dienst das - er rechnet nicht heimlich nach."""
        image_id = app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
        messages: list = []
        app.evolution_service.tree_missing.connect(messages.append)
        app.evolution_service.tree_for_image_async(image_id)

        assert pump_until(qapp, lambda: messages, 20000)
        assert "berechnen" in messages[0]
        assert not app.evolution_service.build_running

    def test_dirty_entry_is_treated_as_missing(self, qapp, app) -> None:
        build_trees(qapp, app)
        app.evolution_service.invalidate_all()
        app.writer.flush()

        assert app.evolution_service.pending_count() >= 1
        image_id = app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
        messages: list = []
        app.evolution_service.tree_missing.connect(messages.append)
        app.evolution_service.tree_for_image_async(image_id)
        assert pump_until(qapp, lambda: messages, 20000)

    def test_corrupt_tree_json_does_not_crash(self, qapp, app) -> None:
        """Ein unbrauchbarer Eintrag gilt als fehlend, nicht als Fehler."""
        build_trees(qapp, app)
        app.writer.execute(
            "UPDATE evolution_cache SET tree_json = 'kein JSON'"
        ).result(timeout=10)

        image_id = app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
        messages: list = []
        app.evolution_service.tree_missing.connect(messages.append)
        app.evolution_service.tree_for_image_async(image_id)
        assert pump_until(qapp, lambda: messages, 20000)

    def test_tree_reflects_chronology(self, qapp, app) -> None:
        build_trees(qapp, app)
        image_id = app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
        received: list = []
        app.evolution_service.tree_ready.connect(lambda r, tree: received.append(tree))
        app.evolution_service.tree_for_image_async(image_id)
        assert pump_until(qapp, lambda: received, 20000)

        tree = received[0]
        names = {
            r["id"]: r["filename"]
            for r in app.database.query("SELECT id, filename FROM prompts")
        }
        assert names[tree.root_id] == "p0.txt", "der aelteste Prompt ist die Wurzel"
        for node in tree.nodes.values():
            if node.parent_id is not None:
                assert tree.nodes[node.parent_id].modified_time <= node.modified_time

    def test_images_for_node(self, qapp, app) -> None:
        build_trees(qapp, app)
        prompt_id = app.database.scalar("SELECT id FROM prompts ORDER BY id LIMIT 1")
        assert len(app.evolution_service.images_for_prompt(prompt_id)) == 1


class TestEvolutionTab:
    def _window(self, qapp, app):
        from app.gui.main_window import MainWindow

        window = MainWindow(app)
        assert pump_until(qapp, lambda: window._folder_tree._items, 20000)
        return window

    def test_tab_opens_with_the_tree(self, qapp, app) -> None:
        build_trees(qapp, app)
        window = self._window(qapp, app)
        try:
            item_id = app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
            item = app.images.by_id(item_id)
            window._show_evolution(item)

            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)
            tab = window._tab_manager.dynamic_widgets()[0]
            assert tab.key.startswith("evolution:")
            assert tab._view.topLevelItemCount() == 1, "genau eine Wurzel"
            assert "Familie" in tab._header.text()
        finally:
            window.close()

    def test_tree_widget_mirrors_the_structure(self, qapp, app) -> None:
        build_trees(qapp, app)
        window = self._window(qapp, app)
        try:
            item = app.images.by_id(
                app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
            )
            window._show_evolution(item)
            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)

            tab = window._tab_manager.dynamic_widgets()[0]
            root = tab._view.topLevelItem(0)
            depth = 0
            current = root
            while current.childCount():
                current = current.child(0)
                depth += 1
            assert depth == tab._tree.depth
            assert len(tab._items) == tab._tree.size
        finally:
            window.close()

    def test_same_family_does_not_open_twice(self, qapp, app) -> None:
        build_trees(qapp, app)
        window = self._window(qapp, app)
        try:
            item = app.images.by_id(
                app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
            )
            window._show_evolution(item)
            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)
            window._show_evolution(item)
            pump_until(qapp, lambda: False, 500)
            assert window._tab_manager.count() == 1
        finally:
            window.close()

    def test_double_click_opens_the_node_images(self, qapp, app) -> None:
        """Spezifikation 01: Doppelklick zeigt alle Bilder eines Knotens."""
        build_trees(qapp, app)
        window = self._window(qapp, app)
        try:
            item = app.images.by_id(
                app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
            )
            window._show_evolution(item)
            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)

            tab = window._tab_manager.dynamic_widgets()[0]
            tab._on_double_click(tab._view.topLevelItem(0), 0)

            assert pump_until(qapp, lambda: window._tab_manager.count() == 2, 20000)
            keys = window._tab_manager.keys()
            assert any(key.startswith("node:") for key in keys)
        finally:
            window.close()

    def test_missing_tree_shows_a_hint(self, qapp, app) -> None:
        window = self._window(qapp, app)
        try:
            item = app.images.by_id(
                app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
            )
            window._show_evolution(item)
            assert pump_until(
                qapp, lambda: "Evolutionsbaum" in window._status.currentMessage(), 20000
            )
            assert window._tab_manager.count() == 0
        finally:
            window.close()

    def test_node_tooltip_carries_context(self, qapp, app) -> None:
        """Spezifikation 01: Kontextinformationen per Mouseover."""
        build_trees(qapp, app)
        window = self._window(qapp, app)
        try:
            item = app.images.by_id(
                app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
            )
            window._show_evolution(item)
            assert pump_until(qapp, lambda: window._tab_manager.count() == 1, 20000)

            tab = window._tab_manager.dynamic_widgets()[0]
            tooltip = tab._view.topLevelItem(0).toolTip(0)
            assert "Bilder:" in tooltip
            assert "Erstellt:" in tooltip
            assert "Doppelklick" in tooltip
        finally:
            window.close()

    def test_new_families_invalidate_the_trees(self, qapp, app) -> None:
        """Nach neu gebildeten Familien sind alte Baeume ueberholt."""
        build_trees(qapp, app)
        assert app.evolution_service.pending_count() == 0

        window = self._window(qapp, app)
        try:
            from app.jobs.analysis_jobs import AnalysisResult
            from app.analysis.clustering import ClusterStats

            result = AnalysisResult(clusters=ClusterStats(clusters=1))
            window._on_analysis_finished(result)
            app.writer.flush()
            assert pump_until(
                qapp, lambda: app.evolution_service.counts()["dirty"] >= 0, 5000
            )
        finally:
            window.close()


class TestNodeLabels:
    """Die Beschriftung muss sagen, was den Knoten ausmacht.

    Die erste Zeile des Prompts ist innerhalb einer Familie meist bei allen
    gleich; als Beschriftung zeigte der Baum dann fuenfmal denselben Text und
    waere unbrauchbar.
    """

    def test_labels_describe_the_change(self, qapp, app) -> None:
        build_trees(qapp, app)
        image_id = app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
        received: list = []
        app.evolution_service.tree_ready.connect(lambda r, tree: received.append(tree))
        app.evolution_service.tree_for_image_async(image_id)
        assert pump_until(qapp, lambda: received, 20000)

        tree = received[0]
        labels = {node.prompt_id: node.label for node in tree.nodes.values()}

        root_label = labels.pop(tree.root_id)
        assert "cinematic" in root_label.lower(), "die Wurzel behaelt ihren Promptanfang"

        for label in labels.values():
            assert label.startswith(("ergaenzt:", "entfernt:", "geaendert zu:")), label

    def test_labels_are_distinct(self, qapp, app) -> None:
        build_trees(qapp, app)
        image_id = app.database.scalar("SELECT id FROM images ORDER BY filename LIMIT 1")
        received: list = []
        app.evolution_service.tree_ready.connect(lambda r, tree: received.append(tree))
        app.evolution_service.tree_for_image_async(image_id)
        assert pump_until(qapp, lambda: received, 20000)

        labels = [node.label for node in received[0].nodes.values()]
        assert len(set(labels)) == len(labels), "jeder Knoten braucht eine eigene Auskunft"

    def test_long_labels_are_shortened(self) -> None:
        from app.jobs.evolution_jobs import LABEL_LIMIT, _shorten

        long_text = "wort " * 60
        assert len(_shorten(long_text)) <= LABEL_LIMIT
        assert _shorten("kurz") == "kurz"
        assert _shorten("mehrfache   leerzeichen") == "mehrfache leerzeichen"


class TestAussagekraeftigeMeldungen:
    """Warum kein Baum da ist - drei Gruende, drei Meldungen.

    Vorher bekamen alle drei dieselbe Meldung: "Ueber Analyse / Evolution
    berechnen anlegen". Sie half nur im dritten Fall. In den beiden anderen
    schickte sie den Benutzer in eine Sackgasse - er berechnete die Baeume und
    bekam anschliessend "keine Evolutionsdaten" zu sehen, obwohl alles in
    Ordnung war. In einer Bibliothek, in der drei Viertel der Bilder gar keine
    Promptdatei haben, ist das der Regelfall, nicht der Sonderfall.
    """

    def _meldung(self, qapp, app, image_id: int) -> str:
        gesehen: list = []
        app.evolution_service.tree_missing.connect(gesehen.append)
        app.evolution_service.tree_for_image_async(image_id)
        assert pump_until(qapp, lambda: gesehen, 20000), "Keine Meldung erhalten"
        app.evolution_service.tree_missing.disconnect()
        return gesehen[0]

    def test_bild_ohne_promptdatei(self, qapp, app) -> None:
        bild = app.database.scalar(
            "SELECT id FROM images WHERE prompt_id IS NULL LIMIT 1"
        )
        if bild is None:
            pytest.skip("Diese Testbibliothek hat kein Bild ohne Prompt")
        meldung = self._meldung(qapp, app, bild)
        assert "keine Promptdatei" in meldung, meldung
        assert "berechnen" not in meldung, "Berechnen hilft hier nicht: " + meldung

    def test_prompt_ohne_familie(self, qapp, app) -> None:
        bild = app.database.scalar(
            "SELECT i.id FROM images i JOIN prompts p ON p.id = i.prompt_id"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM cluster_members m WHERE m.prompt_id = p.id) LIMIT 1"
        )
        if bild is None:
            pytest.skip("In dieser Testbibliothek ist jeder Prompt in einer Familie")
        meldung = self._meldung(qapp, app, bild)
        assert "keiner Familie" in meldung, meldung
        assert "berechnen" not in meldung, "Berechnen hilft hier nicht: " + meldung

    def test_familie_ohne_baum_verweist_aufs_berechnen(self, qapp, app) -> None:
        bild = app.database.scalar(
            "SELECT i.id FROM images i"
            " JOIN cluster_members m ON m.prompt_id = i.prompt_id LIMIT 1"
        )
        if bild is None:
            pytest.skip("Keine Familie mit Bild vorhanden")
        meldung = self._meldung(qapp, app, bild)
        assert "berechnen" in meldung, meldung

    def test_nichts_zu_tun_klingt_nicht_nach_fehlschlag(self, qapp, app) -> None:
        """Der zweite Klick auf "Evolutionsbaeume berechnen"."""
        build_trees(qapp, app)
        assert app.evolution_service.pending_count() == 0

        stats = build_trees(qapp, app)
        assert stats.up_to_date > 0
        assert "aktuell" in stats.summary(), stats.summary()
        assert "keine Evolutionsdaten" not in stats.summary()


class TestBaumUebersichtlich:
    """Der Baum zeigt keine Vorschaubilder mehr in den Zeilen.

    Gemeldet: "das macht den baum so gross". Ein Symbol von 64 Bildpunkten
    vervierfacht die Zeilenhoehe; bei fuenfzig Knoten passt nichts mehr auf
    den Schirm, und die Verzweigung - worum es hier geht - ist nicht mehr zu
    ueberblicken.
    """

    def _tab(self, qapp, app):
        from app.gui.evolution.evolution_tab import EvolutionTab
        from app.gui.theme import _LIGHT

        build_trees(qapp, app)
        familien = app.evolution_service.families()
        assert familien, "Ohne Baum ist der Test sinnlos"
        baum = app.evolution_service.tree_for_cluster(familien[0]["cluster_id"])
        return EvolutionTab(app, baum, _LIGHT, font_size=10)

    def test_keine_symbole_in_den_zeilen(self, qapp, app) -> None:
        tab = self._tab(qapp, app)
        try:
            wurzel = tab._view.topLevelItem(0)
            assert wurzel is not None
            assert wurzel.icon(0).isNull(), "Die Zeile traegt wieder ein Bild"
        finally:
            tab.deleteLater()

    def test_die_zeilen_bleiben_niedrig(self, qapp, app) -> None:
        """Der eigentliche Pruefpunkt - nicht das Fehlen des Symbols, sondern
        die Hoehe, die es verursacht hat."""
        tab = self._tab(qapp, app)
        tab.resize(900, 400)
        tab.show()
        qapp.processEvents()
        try:
            hoehe = tab._view.visualItemRect(tab._view.topLevelItem(0)).height()
            assert 0 < hoehe <= 32, "Zeilenhoehe " + str(hoehe) + " px"
        finally:
            tab.close()
            tab.deleteLater()

    def test_das_bild_wartet_fuer_den_mouseover(self, qapp, app) -> None:
        """Die Bilder werden weiterhin geladen - nur nicht in die Zeile gesetzt."""
        tab = self._tab(qapp, app)
        try:
            assert hasattr(tab, "_thumbs")
            assert hasattr(tab, "_hover")
            # Ueberfahren einer Zeile merkt sich das Bild.
            wurzel = tab._view.topLevelItem(0)
            tab._on_item_entered(wurzel, 0)
            knoten = wurzel.data(0, tab._view.model().index(0, 0).column() + 1)
            assert tab._hover_image is not None or knoten is None
        finally:
            tab.deleteLater()

    def test_die_promptspalte_bekommt_den_platz(self, qapp, app) -> None:
        """Ab der zweiten Baumebene war die Beschriftung auf "unv..." gekuerzt.

        Die Einrueckung frisst die Breite, und gerade die tiefen Knoten sind
        die interessanten.
        """
        from PyQt6.QtWidgets import QHeaderView

        tab = self._tab(qapp, app)
        try:
            # Geprueft wird die Zusage, nicht das Ergebnis in Bildpunkten: wie
            # breit die Spalte tatsaechlich wird, haengt an der Fenstergroesse
            # und am Trenner. Unter der Testplattform "offscreen" bekommt der
            # Baum kaum Breite, und eine Pixelmessung pruefte dort die
            # Umgebung statt der Einstellung.
            kopf = tab._view.header()
            assert kopf.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch, (
                "Die Promptspalte nimmt sich nicht den freien Platz"
            )
            for spalte in range(1, 4):
                assert kopf.sectionResizeMode(spalte) == (
                    QHeaderView.ResizeMode.ResizeToContents
                ), "Spalte " + str(spalte) + " nimmt mehr Platz als noetig"
            assert not kopf.stretchLastSection(), (
                "Sonst waechst die letzte Zahlenspalte statt der Beschriftung"
            )
        finally:
            tab.deleteLater()

    def test_die_promptspalte_waechst_mit_der_breite(self, qapp) -> None:
        """Die Wirkung, geprueft ohne den Rest des Tabs.

        Ein Baum mit genau derselben Kopfeinstellung, aber direkt bemessen -
        so haengt der Test nicht am Trenner und nicht an der Plattform.
        """
        from PyQt6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

        baum = QTreeWidget()
        baum.setColumnCount(4)
        baum.setHeaderLabels(["Prompt", "Aenderung", "Bilder", "Naehe"])
        kopf = baum.header()
        kopf.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for spalte in range(1, 4):
            kopf.setSectionResizeMode(spalte, QHeaderView.ResizeMode.ResizeToContents)
        kopf.setStretchLastSection(False)
        QTreeWidgetItem(baum, ["Ein langer Prompttext", "unveraendert", "1", "1.00"])

        baum.resize(800, 300)
        baum.show()
        qapp.processEvents()
        try:
            breiten = [baum.columnWidth(c) for c in range(4)]
            assert breiten[0] > sum(breiten[1:]), (
                "Die Beschriftung bekommt nicht den freien Platz: " + str(breiten)
            )
        finally:
            baum.close()
            baum.deleteLater()


class TestFamilienauswahl:
    """Ueber dem Baum steht ein Auswahlfeld mit den Familien.

    Ohne das fuehrt der einzige Weg zu einer anderen Familie ueber ein Bild
    aus ihr - man muss also schon wissen, wo man hinwill, um hinzukommen.
    """

    def test_der_dienst_kennt_die_familien(self, qapp, app) -> None:
        build_trees(qapp, app)
        familien = app.evolution_service.families()
        assert familien
        for eintrag in familien:
            assert "cluster_id" in eintrag and "node_count" in eintrag

    def test_nur_familien_mit_fertigem_baum(self, qapp, app) -> None:
        """Eine Auswahl, die ins Leere fuehrt, ist schlimmer als keine."""
        build_trees(qapp, app)
        for eintrag in app.evolution_service.families():
            assert app.evolution_service.tree_for_cluster(eintrag["cluster_id"]) is not None

    def test_die_liste_ist_gefuellt_und_zeigt_die_offene_familie(
        self, qapp, app
    ) -> None:
        from app.gui.evolution.evolution_tab import EvolutionTab
        from app.gui.theme import _LIGHT

        build_trees(qapp, app)
        familien = app.evolution_service.families()
        baum = app.evolution_service.tree_for_cluster(familien[0]["cluster_id"])
        tab = EvolutionTab(app, baum, _LIGHT, font_size=10)
        try:
            assert tab._family.count() >= 1
            assert tab._family.currentData() == baum.cluster_id, (
                "Die Auswahl zeigt eine andere Familie als der Baum darunter"
            )
            assert "Familie" in tab._family.currentText()
        finally:
            tab.deleteLater()

    def test_umschalten_laedt_den_anderen_baum(self, qapp, app) -> None:
        from app.gui.evolution.evolution_tab import EvolutionTab, evolution_key
        from app.gui.theme import _LIGHT

        build_trees(qapp, app)
        familien = app.evolution_service.families()
        if len(familien) < 2:
            pytest.skip("Die Testbibliothek hat nur eine Familie mit Baum")

        baum = app.evolution_service.tree_for_cluster(familien[0]["cluster_id"])
        tab = EvolutionTab(app, baum, _LIGHT, font_size=10)
        try:
            ziel = familien[1]["cluster_id"]
            tab._family.setCurrentIndex(tab._family.findData(ziel))
            qapp.processEvents()
            assert tab._tree.cluster_id == ziel
            assert tab.key == evolution_key(ziel)
        finally:
            tab.deleteLater()

    def test_das_fuellen_loest_kein_umschalten_aus(self, qapp, app) -> None:
        """Ohne Sperre laedt das Fuellen der Liste sofort einen anderen Baum."""
        from app.gui.evolution.evolution_tab import EvolutionTab
        from app.gui.theme import _LIGHT

        build_trees(qapp, app)
        familien = app.evolution_service.families()
        baum = app.evolution_service.tree_for_cluster(familien[-1]["cluster_id"])
        tab = EvolutionTab(app, baum, _LIGHT, font_size=10)
        try:
            assert tab._tree.cluster_id == baum.cluster_id
        finally:
            tab.deleteLater()
