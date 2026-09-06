"""Tests der M2-Bausteine: Markierungsdienst, Tabverwaltung, Suchleiste, Ergebnistabs."""

from __future__ import annotations

import pytest
from PIL import Image
from PyQt6.QtWidgets import QLabel, QTabWidget, QWidget

from app.application import Application
from app.config.bootstrap import BootstrapConfig, save_bootstrap
from app.gui.common.search_bar import SCOPE_FOLDER, SCOPE_LIBRARY, SearchBar
from app.gui.tabs.tab_manager import MAX_DYNAMIC_TABS, TabManager
from app.services.selection_service import MAX_MARKS, SelectionService
from tests.conftest import pump_until


class TestSelectionService:
    def test_toggle_marks_and_unmarks(self) -> None:
        selection = SelectionService()
        assert selection.toggle(1) is True
        assert selection.marks() == [1]
        assert selection.toggle(1) is False
        assert selection.marks() == []

    def test_slot_reflects_order(self) -> None:
        """A und B bestimmen, welches Bild im Vergleich links steht."""
        selection = SelectionService()
        selection.toggle(10)
        selection.toggle(20)
        assert selection.slot_of(10) == 0
        assert selection.slot_of(20) == 1
        assert selection.slot_of(30) is None

    def test_third_mark_replaces_the_oldest(self) -> None:
        """Berechenbarer als eine stumm abgelehnte Markierung."""
        selection = SelectionService()
        for image_id in (1, 2, 3):
            selection.toggle(image_id)
        assert selection.marks() == [2, 3]
        assert len(selection.marks()) == MAX_MARKS

    def test_can_compare_requires_exactly_two(self) -> None:
        selection = SelectionService()
        assert not selection.can_compare
        selection.toggle(1)
        assert not selection.can_compare
        selection.toggle(2)
        assert selection.can_compare

    def test_signal_carries_current_marks(self, qapp) -> None:
        selection = SelectionService()
        seen: list[list[int]] = []
        selection.marks_changed.connect(seen.append)

        selection.toggle(5)
        selection.toggle(6)
        selection.clear()

        assert seen == [[5], [5, 6], []]

    def test_clear_without_marks_is_silent(self, qapp) -> None:
        selection = SelectionService()
        seen = []
        selection.marks_changed.connect(seen.append)
        selection.clear()
        assert seen == []


class TestTabManager:
    @pytest.fixture
    def manager(self, qapp):
        tabs = QTabWidget()
        fixed = QLabel("Bibliothek")
        tabs.addTab(fixed, "Bibliothek")
        tabs.setTabsClosable(True)
        yield TabManager(tabs), tabs
        tabs.deleteLater()

    def test_open_adds_a_tab(self, manager) -> None:
        control, tabs = manager
        control.open(QLabel("A"), "k1", "Ergebnis A")
        assert tabs.count() == 2
        assert control.is_open("k1")

    def test_opening_the_same_key_focuses_instead_of_duplicating(self, manager) -> None:
        """Zweimal dieselbe Suche darf nicht zwei Tabs erzeugen."""
        control, tabs = manager
        first = control.open(QLabel("A"), "k1", "Ergebnis A")
        second = control.open(QLabel("A erneut"), "k1", "Ergebnis A")
        assert tabs.count() == 2
        assert first is second

    def test_close_removes_the_tab(self, manager) -> None:
        control, tabs = manager
        control.open(QLabel("A"), "k1", "A")
        assert control.close_key("k1") is True
        assert tabs.count() == 1
        assert not control.is_open("k1")

    def test_library_tab_cannot_be_closed(self, manager) -> None:
        """Spezifikation 01: das Bibliothekstab ist fest."""
        control, tabs = manager
        control._on_close_requested(0)
        assert tabs.count() == 1
        assert tabs.tabText(0) == "Bibliothek"

    def test_close_all_keeps_the_library(self, manager) -> None:
        control, tabs = manager
        for i in range(3):
            control.open(QLabel(str(i)), "k" + str(i), "Tab " + str(i))
        assert control.close_all() == 3
        assert tabs.count() == 1

    def test_limit_evicts_the_oldest(self, manager) -> None:
        """Jeder Ergebnistab haelt Vorschaubilder im Speicher.

        Ohne Obergrenze waechst der Verbrauch mit jeder Suche weiter.
        """
        control, tabs = manager
        for i in range(MAX_DYNAMIC_TABS + 3):
            control.open(QLabel(str(i)), "k" + str(i), "Tab " + str(i), focus=False)
        assert control.count() == MAX_DYNAMIC_TABS
        assert not control.is_open("k0")
        assert control.is_open("k" + str(MAX_DYNAMIC_TABS + 2))

    def test_long_titles_are_elided_but_kept_in_the_tooltip(self, manager) -> None:
        control, tabs = manager
        title = "Suche: ein ziemlich langer Suchbegriff der nicht in die Leiste passt"
        control.open(QLabel("x"), "k1", title)
        index = tabs.indexOf(control.widget_for("k1"))
        assert len(tabs.tabText(index)) < len(title)
        assert tabs.tabToolTip(index) == title

    def test_focus_returns_false_for_unknown_key(self, manager) -> None:
        control, _tabs = manager
        assert control.focus("gibtsnicht") is False


class TestSearchBar:
    def test_debounce_collapses_keystrokes(self, qapp) -> None:
        """Ohne Entprellung setzt jeder Tastendruck eine FTS5-Abfrage ab."""
        bar = SearchBar(debounce_ms=80)
        requests: list[tuple[str, str]] = []
        bar.search_requested.connect(lambda text, scope: requests.append((text, scope)))

        for fragment in ("g", "go", "gol", "gold"):
            bar._input.setText(fragment)

        assert requests == [], "waehrend des Tippens darf nichts abgesetzt werden"
        assert pump_until(qapp, lambda: requests, 3000)
        assert requests == [("gold", SCOPE_LIBRARY)], "nur die letzte Eingabe zaehlt"

    def test_return_searches_immediately(self, qapp) -> None:
        bar = SearchBar(debounce_ms=5000)
        requests = []
        bar.search_requested.connect(lambda text, scope: requests.append(text))
        bar._input.setText("sofort")
        bar._input.returnPressed.emit()
        assert requests == ["sofort"]

    def test_clearing_emits_cleared(self, qapp) -> None:
        bar = SearchBar(debounce_ms=20)
        cleared = []
        bar.cleared.connect(lambda: cleared.append(True))
        bar._input.setText("etwas")
        bar._input.setText("")
        assert cleared == [True]

    def test_scope_change_repeats_the_search(self, qapp) -> None:
        bar = SearchBar(debounce_ms=5000)
        bar.set_folder_scope_enabled(True)
        requests = []
        bar.search_requested.connect(lambda text, scope: requests.append(scope))
        bar._input.setText("gold")
        bar._input.returnPressed.emit()
        bar._scope.setCurrentIndex(1)
        assert requests == [SCOPE_LIBRARY, SCOPE_FOLDER]

    def test_folder_scope_falls_back_when_disabled(self, qapp) -> None:
        """Ohne ausgewaehlten Ordner ergibt der Ordnerbereich keinen Sinn."""
        bar = SearchBar()
        bar.set_folder_scope_enabled(True)
        bar._scope.setCurrentIndex(1)
        assert bar.scope == SCOPE_FOLDER
        bar.set_folder_scope_enabled(False)
        assert bar.scope == SCOPE_LIBRARY


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "lib"
    for folder_name, count in (("shoot_a", 5), ("shoot_b", 3)):
        folder = root / folder_name
        folder.mkdir(parents=True)
        for i in range(count):
            stem = folder_name + "_" + str(i).zfill(3)
            Image.new("RGB", (48, 36), (30 * i, 80, 160)).save(folder / (stem + ".jpg"))
            text = "warm golden sunlight dominates the scene."
            if folder_name == "shoot_b":
                text = "cold blue moonlight dominates the scene."
            (folder / (stem + ".txt")).write_text(
                text + "\nA cinematic editorial portrait.", encoding="utf-8"
            )
    return root


@pytest.fixture
def window(qapp, tmp_path, library):
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
    app = Application(bootstrap_file=bootstrap)
    assert app.startup().ok
    app.settings.set("library.roots", [str(library)])
    app.settings.set("index.scan_on_startup", False)
    app.build_services()

    finished = []
    app.index_service.scan_finished.connect(finished.append)
    app.index_service.start_scan()
    assert pump_until(qapp, lambda: finished, 30000)

    from app.gui.main_window import MainWindow

    instance = MainWindow(app)
    assert pump_until(qapp, lambda: instance._folder_tree._items, 20000)
    yield instance
    instance.close()
    app.shutdown()


def _select_folder(qapp, window, name: str):
    nodes = {n.name: n for n in window._images.folder_tree()}
    window._on_folder_selected(nodes[name], False)
    assert pump_until(qapp, lambda: window._explorer.item_count() > 0, 20000)
    return nodes[name]


class TestSearchIntoTabs:
    def test_search_fills_the_library_explorer(self, qapp, window) -> None:
        _select_folder(qapp, window, "shoot_a")
        window._on_search_requested("moonlight", SCOPE_LIBRARY)
        assert pump_until(qapp, lambda: "Suche" in window._status._origin.text()
                          or window._explorer.item_count() == 3, 20000)
        assert pump_until(qapp, lambda: window._explorer.item_count() == 3, 20000)

    def test_folder_scope_limits_the_result(self, qapp, window) -> None:
        """Dieselbe Suche, einmal global und einmal im Ordner."""
        folder = _select_folder(qapp, window, "shoot_b")

        window._on_search_requested("dominates", SCOPE_LIBRARY)
        assert pump_until(qapp, lambda: window._explorer.item_count() == 8, 20000)

        window._on_search_requested("dominates", SCOPE_FOLDER)
        assert pump_until(qapp, lambda: window._explorer.item_count() == 3, 20000)
        assert folder.name == "shoot_b"

    def test_no_hits_clears_the_explorer(self, qapp, window) -> None:
        _select_folder(qapp, window, "shoot_a")
        window._on_search_requested("gibtesnirgends", SCOPE_LIBRARY)
        assert pump_until(qapp, lambda: window._explorer.item_count() == 0, 20000)
        assert "keine Treffer" in window._status._origin.text()

    def test_clearing_the_search_restores_the_folder(self, qapp, window) -> None:
        _select_folder(qapp, window, "shoot_a")
        window._on_search_requested("moonlight", SCOPE_LIBRARY)
        assert pump_until(qapp, lambda: window._explorer.item_count() == 3, 20000)

        window._on_search_cleared()
        assert pump_until(qapp, lambda: window._explorer.item_count() == 5, 20000)

    def test_result_can_be_pinned_to_its_own_tab(self, qapp, window) -> None:
        """Spezifikation 01: Suchergebnisse gehoeren in ein eigenes Tab."""
        _select_folder(qapp, window, "shoot_a")
        window._on_search_requested("moonlight", SCOPE_LIBRARY)
        assert pump_until(qapp, lambda: window._explorer.item_count() == 3, 20000)

        window._open_search_tab()
        assert window._tab_manager.count() == 1
        tab = window._tab_manager.dynamic_widgets()[0]
        assert tab.explorer.item_count() == 3
        assert "moonlight" in tab.key

    def test_pinning_twice_focuses_the_existing_tab(self, qapp, window) -> None:
        _select_folder(qapp, window, "shoot_a")
        window._on_search_requested("moonlight", SCOPE_LIBRARY)
        assert pump_until(qapp, lambda: window._explorer.item_count() == 3, 20000)

        window._open_search_tab()
        window._open_search_tab()
        assert window._tab_manager.count() == 1

    def test_pinning_an_empty_result_does_nothing(self, qapp, window) -> None:
        window._open_search_tab()
        assert window._tab_manager.count() == 0


class TestDetailTab:
    def test_double_click_opens_a_detail_tab(self, qapp, window) -> None:
        """In M1 oeffnete der Doppelklick noch den Windows-Viewer."""
        _select_folder(qapp, window, "shoot_a")
        item = window._explorer.model.item_at(0)

        window._open_detail_tab(item)

        assert window._tab_manager.count() == 1
        tab = window._tab_manager.dynamic_widgets()[0]
        assert tab.current_item().id == item.id
        assert pump_until(qapp, lambda: tab.preview.editor.prompt is not None, 20000)
        assert "golden sunlight" in tab.preview.editor.toPlainText()

    def test_same_image_does_not_open_twice(self, qapp, window) -> None:
        _select_folder(qapp, window, "shoot_a")
        item = window._explorer.model.item_at(0)
        window._open_detail_tab(item)
        window._open_detail_tab(item)
        assert window._tab_manager.count() == 1

    def test_two_previews_do_not_cross_answers(self, qapp, window) -> None:
        """Beide Vorschauen haengen am selben PromptService.

        Ohne Anfragenummern wuerde die Antwort fuer den Detailtab im
        Bibliothekstab landen und umgekehrt.
        """
        _select_folder(qapp, window, "shoot_a")
        first = window._explorer.model.item_at(0)
        second = window._explorer.model.item_at(1)

        window._preview.show_item(first)
        window._open_detail_tab(second)
        tab = window._tab_manager.dynamic_widgets()[0]

        assert pump_until(
            qapp,
            lambda: window._preview.editor.prompt is not None
            and tab.preview.editor.prompt is not None,
            20000,
        )
        assert window._preview.editor.prompt.filename.startswith(first.stem)
        assert tab.preview.editor.prompt.filename.startswith(second.stem)


class TestMarksAcrossTabs:
    def test_mark_is_visible_in_every_tab(self, qapp, window) -> None:
        """Spezifikation 01: Markierungen sind dauerhaft sichtbar.

        Mit mehreren Tabs heisst das: derselbe Zustand ueberall.
        """
        from app.gui.library.thumbnail_model import MARKED_ROLE

        _select_folder(qapp, window, "shoot_a")
        window._on_search_requested("golden", SCOPE_LIBRARY)
        assert pump_until(qapp, lambda: window._explorer.item_count() == 5, 20000)
        window._open_search_tab()
        tab = window._tab_manager.dynamic_widgets()[0]

        item = window._explorer.model.item_at(0)
        window._on_image_action("mark", item)

        library_model = window._explorer.model
        assert library_model.data(library_model.index(0), MARKED_ROLE) is True

        tab_model = tab.explorer.model
        row = next(
            i for i in range(tab_model.rowCount()) if tab_model.item_at(i).id == item.id
        )
        assert tab_model.data(tab_model.index(row), MARKED_ROLE) is True

    def test_clearing_marks_updates_all_tabs(self, qapp, window) -> None:
        from app.gui.library.thumbnail_model import MARKED_ROLE

        _select_folder(qapp, window, "shoot_a")
        item = window._explorer.model.item_at(0)
        window._on_image_action("mark", item)
        window._selection.clear()

        model = window._explorer.model
        assert model.data(model.index(0), MARKED_ROLE) is False


class TestMarkerPainting:
    """Prueft, dass die Markierung tatsaechlich gezeichnet wird.

    Der Zustand allein genuegt nicht: Spezifikation 01 verlangt eine sichtbare
    Kennzeichnung. Ein Fehler im Delegate waere sonst nur am Bildschirm zu
    bemerken - und auf zufaellig gefaerbten Testbildern auch dort kaum.
    """

    def _render(self, marked_slot):
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QPainter, QPixmap
        from PyQt6.QtWidgets import QStyleOptionViewItem

        from app.dto.models import ImageItem, ResultSet, ThumbnailState
        from app.gui.library.delegate import ThumbnailDelegate
        from app.gui.library.thumbnail_model import ThumbnailModel
        from app.gui.theme import _LIGHT

        selection = SelectionService()
        model = ThumbnailModel(selection)
        item = ImageItem(
            id=1, folder_id=1, filename="a.jpg", stem="a", full_path="Z:/a.jpg",
            thumbnail_key="k", thumbnail_state=ThumbnailState.READY,
            prompt_id=1, file_size=1, modified_time=0,
        )
        model.set_results(ResultSet(items=[item]))
        if marked_slot is not None:
            selection.toggle(1)

        delegate = ThumbnailDelegate(_LIGHT, 128)
        canvas = QPixmap(160, 180)
        canvas.fill()
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 160, 180)
        painter = QPainter(canvas)
        delegate.paint(painter, option, model.index(0))
        painter.end()
        return canvas.toImage()

    def test_marked_tile_differs_from_unmarked(self, qapp) -> None:
        plain = self._render(None)
        marked = self._render(0)
        assert plain != marked, "die Markierung muss sichtbar gezeichnet werden"

    def test_marker_colour_appears(self, qapp) -> None:
        from app.gui.theme import _LIGHT

        marked = self._render(0)
        wanted = _LIGHT.marker_compare.rgb()
        hits = sum(
            1
            for x in range(marked.width())
            for y in range(marked.height())
            if marked.pixel(x, y) == wanted
        )
        assert hits > 50, "Rahmen und Plakette muessen in der Markierungsfarbe erscheinen"


class TestUngesicherteAenderungenBeimSchliessen:
    """Ein Klick auf das Schliessen-Kreuz warf eine Zusammenfuehrung weg.

    Der Vergleichstab bietet `has_unsaved_changes()` an, seit sich Bloecke von
    Hand uebernehmen lassen. Der Tabverwalter hat nie gefragt: `_remove` lief
    unbesehen. Wer zwei Prompts zusammengefuehrt und dann den Tab geschlossen
    hat, stand ohne Rueckfrage wieder vor dem alten Stand.

    Gefragt wird ueber die Form, nicht ueber einen Typ - der Tabverwalter
    kennt den Vergleichstab nicht und soll ihn nicht kennen.
    """

    class SchmutzigerTab(QWidget):
        def __init__(self, schmutzig: bool = True) -> None:
            super().__init__()
            self._schmutzig = schmutzig

        def has_unsaved_changes(self) -> bool:
            return self._schmutzig

    def _verwalter(self, qapp):
        tabs = QTabWidget()
        tabs.setTabsClosable(True)
        tabs.addTab(QLabel("Bibliothek"), "Bibliothek")
        return tabs, TabManager(tabs)

    def test_nein_laesst_den_tab_offen(self, qapp, monkeypatch) -> None:
        tabs, manager = self._verwalter(qapp)
        manager.open(self.SchmutzigerTab(), "compare:1:2", "Vergleich")
        monkeypatch.setattr(TabManager, "_frage_verwerfen", lambda self, text: False)

        tabs.tabCloseRequested.emit(tabs.indexOf(manager.widget_for("compare:1:2")))
        assert manager.is_open("compare:1:2")

    def test_ja_schliesst_ihn(self, qapp, monkeypatch) -> None:
        tabs, manager = self._verwalter(qapp)
        manager.open(self.SchmutzigerTab(), "compare:1:2", "Vergleich")
        monkeypatch.setattr(TabManager, "_frage_verwerfen", lambda self, text: True)

        tabs.tabCloseRequested.emit(tabs.indexOf(manager.widget_for("compare:1:2")))
        assert not manager.is_open("compare:1:2")

    def test_ein_sauberer_tab_wird_nicht_gefragt(self, qapp, monkeypatch) -> None:
        tabs, manager = self._verwalter(qapp)
        manager.open(self.SchmutzigerTab(schmutzig=False), "detail:7", "Bild")

        def darf_nicht(self, text):
            raise AssertionError("Ohne Aenderungen darf nichts gefragt werden")

        monkeypatch.setattr(TabManager, "_frage_verwerfen", darf_nicht)
        tabs.tabCloseRequested.emit(tabs.indexOf(manager.widget_for("detail:7")))
        assert not manager.is_open("detail:7")

    def test_close_all_laesst_die_schmutzigen_stehen(self, qapp, monkeypatch) -> None:
        tabs, manager = self._verwalter(qapp)
        manager.open(self.SchmutzigerTab(schmutzig=False), "detail:1", "A")
        manager.open(self.SchmutzigerTab(), "compare:1:2", "B")
        monkeypatch.setattr(TabManager, "_frage_verwerfen", lambda self, text: False)

        manager.close_all()
        assert not manager.is_open("detail:1")
        assert manager.is_open("compare:1:2")

    def test_die_obergrenze_fragt_nicht_und_wirft_nichts_weg(self, qapp) -> None:
        """Eine Verdraengung ohne Zutun darf weder fragen noch loeschen."""
        tabs, manager = self._verwalter(qapp)
        for i in range(MAX_DYNAMIC_TABS + 3):
            manager.open(self.SchmutzigerTab(), "compare:" + str(i), "V" + str(i))

        assert manager.count() == MAX_DYNAMIC_TABS + 3, (
            "Die Obergrenze hat ungesicherte Arbeit verdraengt"
        )

    def test_ein_defektes_widget_blockiert_das_schliessen_nicht(self, qapp) -> None:
        tabs, manager = self._verwalter(qapp)

        class Kaputt(QWidget):
            def has_unsaved_changes(self):
                raise RuntimeError("kaputt")

        manager.open(Kaputt(), "detail:9", "K")
        tabs.tabCloseRequested.emit(tabs.indexOf(manager.widget_for("detail:9")))
        assert not manager.is_open("detail:9")
