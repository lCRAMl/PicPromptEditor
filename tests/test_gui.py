"""Tests der Service-Schicht und des Hauptfensters.

Diese Tests fahren die Anwendung vollstaendig hoch - Datenbank, Jobs, Services,
Widgets - und pruefen den Weg vom Ordnerklick bis zur gefuellten Kachel. Sie
laufen ohne Bildschirm ueber die Offscreen-Plattform von Qt.
"""

from __future__ import annotations

import pytest
from PIL import Image
from PyQt6.QtGui import QPixmap

from app.application import Application
from app.config.bootstrap import BootstrapConfig, save_bootstrap
from app.dto.models import ImageItem, ResultSet, ThumbnailState
from app.gui.library.thumbnail_model import PIXMAP_ROLE, SLOT_ROLE, ThumbnailModel
from app.services.selection_service import SelectionService
from tests.conftest import pump_until


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "lib"
    for folder_name, count in (("shoot_a", 6), ("shoot_b", 4)):
        folder = root / folder_name
        folder.mkdir(parents=True)
        for i in range(count):
            stem = folder_name + "_" + str(i).zfill(3)
            Image.new("RGB", (64, 48), (20 * i, 90, 140)).save(folder / (stem + ".jpg"))
            (folder / (stem + ".txt")).write_text(
                "warm golden sunlight dominates the scene.\n"
                "A cinematic editorial portrait number " + str(i) + ".\n"
                "NEGATIVE PROMPT\ncartoon, blurry",
                encoding="utf-8",
            )
    return root


@pytest.fixture
def running_app(qapp, tmp_path, library):
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
    app.index_service.scan_finished.connect(lambda stats: finished.append(stats))
    app.index_service.start_scan()
    assert pump_until(qapp, lambda: finished, 30000), "Scan wurde nicht fertig"

    yield app
    app.shutdown()


class TestThumbnailModel:
    def _items(self, count: int) -> list[ImageItem]:
        return [
            ImageItem(
                id=i,
                folder_id=1,
                filename="bild" + str(i) + ".jpg",
                stem="bild" + str(i),
                full_path="Z:/lib/bild" + str(i) + ".jpg",
                thumbnail_key="key" + str(i),
                thumbnail_state=ThumbnailState.UNKNOWN,
                prompt_id=i if i % 2 == 0 else None,
                file_size=1000,
                modified_time=0,
            )
            for i in range(count)
        ]

    def test_holds_the_whole_result_set(self, qapp) -> None:
        model = ThumbnailModel(SelectionService())
        model.set_results(ResultSet(items=self._items(5000), origin="Test"))
        assert model.rowCount() == 5000
        assert model.origin == "Test"

    def test_data_returns_placeholder_before_the_thumbnail_exists(self, qapp) -> None:
        model = ThumbnailModel(SelectionService())
        model.set_results(ResultSet(items=self._items(3)))
        assert model.data(model.index(0), PIXMAP_ROLE) is None

    def test_set_thumbnails_coalesces_into_ranges(self, qapp) -> None:
        """Ein Stapel darf nicht ein dataChanged je Bild ausloesen.

        Bei zweihundert Kacheln waeren das zweihundert Neuzeichnungen des
        sichtbaren Bereichs.
        """
        model = ThumbnailModel(SelectionService())
        model.set_results(ResultSet(items=self._items(20)))

        signals: list[tuple[int, int]] = []
        model.dataChanged.connect(
            lambda first, last, roles: signals.append((first.row(), last.row()))
        )

        payload = _png_bytes()
        # Zeilen 0-4 und 10-12: zwei zusammenhaengende Bereiche
        batch = [(i, payload) for i in list(range(5)) + [10, 11, 12]]
        model.set_thumbnails(batch)

        assert signals == [(0, 4), (10, 12)]

    def test_unknown_ids_are_ignored(self, qapp) -> None:
        """Ergebnis einer inzwischen abgeloesten Ansicht darf nicht stoeren."""
        model = ThumbnailModel(SelectionService())
        model.set_results(ResultSet(items=self._items(3)))
        model.set_thumbnails([(999, _png_bytes())])  # gehoert zu einem alten Ordner
        assert model.data(model.index(0), PIXMAP_ROLE) is None

    def test_marking_toggles_and_notifies(self, qapp) -> None:
        model = ThumbnailModel(SelectionService())
        model.set_results(ResultSet(items=self._items(4)))
        changed = []
        model.dataChanged.connect(lambda f, l, roles: changed.append(f.row()))

        assert model.toggle_marked(2) is True
        assert model.marked_ids() == [2]
        assert model.toggle_marked(2) is False
        assert model.marked_ids() == []
        assert changed == [2, 2]

    def test_two_models_share_one_selection(self, qapp) -> None:
        """Dieselbe Datei muss in Bibliothek und Ergebnistab gleich aussehen."""
        selection = SelectionService()
        first = ThumbnailModel(selection)
        second = ThumbnailModel(selection)
        items = self._items(4)
        first.set_results(ResultSet(items=items))
        second.set_results(ResultSet(items=items))

        repainted = []
        second.dataChanged.connect(lambda f, l, roles: repainted.append(f.row()))

        first.toggle_marked(2)

        assert second.data(second.index(2), SLOT_ROLE) == 0
        assert repainted == [2], "das zweite Modell muss die Zeile neu zeichnen"

    def test_request_range_reports_only_missing(self, qapp) -> None:
        model = ThumbnailModel(SelectionService())
        model.set_results(ResultSet(items=self._items(10)))
        requested: list[list[ImageItem]] = []
        model.thumbnails_needed.connect(lambda items: requested.append(items))

        model.set_thumbnails([(0, _png_bytes()), (1, _png_bytes())])
        model.request_range(0, 4)

        assert len(requested) == 1
        assert [i.id for i in requested[0]] == [2, 3, 4]


class TestImageService:
    def test_folder_tree_comes_from_the_database(self, running_app) -> None:
        nodes = running_app.image_service.folder_tree()
        names = {n.name for n in nodes}
        assert {"lib", "shoot_a", "shoot_b"} <= names
        by_name = {n.name: n for n in nodes}
        assert by_name["shoot_a"].image_count == 6

    def test_loading_a_folder_delivers_results(self, qapp, running_app) -> None:
        received: list[ResultSet] = []
        running_app.image_service.results_ready.connect(received.append)

        nodes = {n.name: n for n in running_app.image_service.folder_tree()}
        running_app.image_service.load_folder_async(nodes["shoot_a"])

        assert pump_until(qapp, lambda: received)
        assert len(received[0]) == 6
        assert "shoot_a" in received[0].origin

    def test_recursive_load_includes_subfolders(self, qapp, running_app) -> None:
        received: list[ResultSet] = []
        running_app.image_service.results_ready.connect(received.append)

        nodes = {n.name: n for n in running_app.image_service.folder_tree()}
        running_app.image_service.load_folder_async(nodes["lib"], include_subfolders=True)

        assert pump_until(qapp, lambda: received)
        assert len(received[0]) == 10

    def test_stale_results_are_discarded(self, qapp, running_app) -> None:
        """Klickt der Benutzer weiter, darf das alte Ergebnis nicht erscheinen.

        Das aeltere Ergebnis wird ueber seine Anfragenummer erkannt und verworfen.
        """
        service = running_app.image_service
        nodes = {n.name: n for n in service.folder_tree()}

        received: list[ResultSet] = []
        service.results_ready.connect(received.append)

        first = service.load_folder_async(nodes["shoot_a"])
        second = service.load_folder_async(nodes["shoot_b"])
        assert second > first

        assert pump_until(qapp, lambda: received)
        pump_until(qapp, lambda: False, 300)  # kurz nachlaufen lassen

        assert len(received) == 1, "nur das juengste Ergebnis darf ankommen"
        assert "shoot_b" in received[0].origin

    def test_thumbnails_are_generated_and_cached(self, qapp, running_app) -> None:
        service = running_app.image_service
        nodes = {n.name: n for n in service.folder_tree()}

        results: list[ResultSet] = []
        service.results_ready.connect(results.append)
        service.load_folder_async(nodes["shoot_a"])
        assert pump_until(qapp, lambda: results)

        ready: list[tuple[int, bytes]] = []
        service.thumbnails_ready.connect(lambda batch: ready.extend(batch))
        service.request_thumbnails(results[0].items)

        assert pump_until(qapp, lambda: len(ready) >= 6, 30000)
        assert len(ready) == 6
        assert all(data for _id, data in ready)

        # Zweite Anforderung: aus dem RAM bedient, ohne erneute Dateiarbeit -
        # aber sie MUSS ausgeliefert werden.
        #
        # Hier stand frueher `assert not ready`. Das hielt einen Fehler fest:
        # ein Bild im RAM galt als erledigt, unter der Annahme, wer es
        # anfordert, habe es ohnehin schon. Fuer eine neu geoeffnete Ansicht
        # stimmt das nicht - wer einen Ordner ansah und ihn danach in einem Tab
        # oeffnete, bekam dort leere Kacheln. Richtig ist: kein neuer Auftrag,
        # aber Auslieferung.
        before = service.cache_stats()["entries"]
        ready.clear()
        service.request_thumbnails(results[0].items)
        assert pump_until(qapp, lambda: len(ready) >= 6, 5000), (
            "Zwischengespeicherte Bilder wurden verschluckt"
        )
        assert len(ready) == 6
        assert service.cache_stats()["entries"] == before, "Es wurde neu erzeugt"

    def test_cached_thumbnail_is_available_synchronously(self, qapp, running_app) -> None:
        service = running_app.image_service
        nodes = {n.name: n for n in service.folder_tree()}
        results: list[ResultSet] = []
        service.results_ready.connect(results.append)
        service.load_folder_async(nodes["shoot_b"])
        assert pump_until(qapp, lambda: results)

        item = results[0].items[0]
        assert service.cached_thumbnail(item) is None

        ready: list = []
        service.thumbnails_ready.connect(lambda batch: ready.extend(batch))
        service.request_thumbnails([item])
        assert pump_until(qapp, lambda: ready, 20000)
        assert service.cached_thumbnail(item) is not None


class TestPromptService:
    def test_prompt_is_loaded_for_an_image(self, qapp, running_app) -> None:
        service = running_app.image_service
        nodes = {n.name: n for n in service.folder_tree()}
        results: list[ResultSet] = []
        service.results_ready.connect(results.append)
        service.load_folder_async(nodes["shoot_a"])
        assert pump_until(qapp, lambda: results)

        received = []
        running_app.prompt_service.prompt_ready.connect(
            lambda request_id, prompt: received.append((request_id, prompt))
        )
        request = running_app.prompt_service.load_for_image(results[0].items[0].id)

        assert pump_until(qapp, lambda: received)
        answer_id, prompt = received[0]
        assert answer_id == request, "die Antwort muss ihre Anfragenummer tragen"
        assert prompt is not None
        assert "golden sunlight" in prompt.text

    def test_search_returns_image_ids(self, qapp, running_app) -> None:
        found = []
        running_app.prompt_service.search_ready.connect(
            lambda request_id, ids: found.append(ids)
        )
        running_app.prompt_service.search_async("cinematic")
        assert pump_until(qapp, lambda: found)
        assert len(found[0]) == 10

    def test_search_with_no_hits(self, qapp, running_app) -> None:
        found = []
        running_app.prompt_service.search_ready.connect(
            lambda request_id, ids: found.append(ids)
        )
        running_app.prompt_service.search_async("gibtesnirgends")
        assert pump_until(qapp, lambda: found)
        assert found[0] == []


class TestIndexService:
    def test_second_scan_finds_nothing(self, qapp, running_app) -> None:
        finished = []
        running_app.index_service.scan_finished.connect(finished.append)
        running_app.index_service.start_scan()
        assert pump_until(qapp, lambda: finished, 30000)
        assert finished[0].new_images == 0
        assert finished[0].changed == 0

    def test_concurrent_start_is_rejected_with_a_reason(self, qapp, running_app) -> None:
        """Ein still verworfener Start waere fuer den Benutzer nicht erklaerbar."""
        rejected = []
        finished = []
        running_app.index_service.scan_rejected.connect(rejected.append)
        running_app.index_service.scan_finished.connect(finished.append)

        running_app.index_service.start_scan()
        running_app.index_service.start_scan()

        assert rejected and "laeuft bereits" in rejected[0]
        assert pump_until(qapp, lambda: finished, 30000)
        assert running_app.index_service.scan_running is False

    def test_scan_running_is_false_after_finish(self, qapp, running_app) -> None:
        """scan_finished darf erst kommen, wenn ein neuer Scan startbar ist."""
        finished = []
        running_app.index_service.scan_finished.connect(
            lambda stats: finished.append(running_app.index_service.scan_running)
        )
        running_app.index_service.start_scan()
        assert pump_until(qapp, lambda: finished, 30000)
        assert finished[0] is False

    def test_adding_a_known_root_is_refused(self, running_app, library) -> None:
        assert running_app.index_service.add_root(str(library)) is False


class TestMainWindow:
    def test_window_builds_and_shows_the_library_tab(self, qapp, running_app) -> None:
        from app.gui.main_window import MainWindow

        window = MainWindow(running_app)
        try:
            assert window._tabs.count() == 1
            assert window._tabs.tabText(0) == "Bibliothek"
            bar = window._tabs.tabBar()
            assert bar.tabButton(0, bar.ButtonPosition.RightSide) is None, (
                "das Bibliothekstab darf nicht schliessbar sein"
            )
        finally:
            window.close()

    def test_folder_click_fills_the_grid(self, qapp, running_app) -> None:
        from app.gui.main_window import MainWindow

        window = MainWindow(running_app)
        try:
            assert pump_until(qapp, lambda: window._folder_tree._items, 20000)
            nodes = {n.name: n for n in running_app.image_service.folder_tree()}
            window._on_folder_selected(nodes["shoot_a"], False)

            model = window._explorer.model
            assert pump_until(qapp, lambda: model.rowCount() > 0, 20000)
            assert model.rowCount() == 6
            assert "shoot_a" in window._status._origin.text()
        finally:
            window.close()

    def test_selecting_an_image_loads_prompt_and_picture(self, qapp, running_app) -> None:
        from app.gui.main_window import MainWindow

        window = MainWindow(running_app)
        try:
            assert pump_until(qapp, lambda: window._folder_tree._items, 20000)
            nodes = {n.name: n for n in running_app.image_service.folder_tree()}
            window._on_folder_selected(nodes["shoot_a"], False)
            model = window._explorer.model
            assert pump_until(qapp, lambda: model.rowCount() > 0, 20000)

            window._preview.show_item(model.item_at(0))
            assert pump_until(qapp, lambda: window._preview.editor.prompt is not None, 20000)
            assert "golden sunlight" in window._preview.editor.toPlainText()

            assert pump_until(
                qapp, lambda: window._preview.image_view._pixmap_item is not None, 20000
            ), "Bildansicht blieb leer"
        finally:
            window.close()

    def test_editor_is_read_only(self, qapp, running_app) -> None:
        from app.gui.main_window import MainWindow

        window = MainWindow(running_app)
        try:
            assert window._preview.editor.isReadOnly()
        finally:
            window.close()


def _png_bytes() -> bytes:
    """Kleines gueltiges PNG als Testnutzlast."""
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()
