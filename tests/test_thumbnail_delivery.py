"""Vorschaubilder erreichen JEDE Ansicht, nicht nur die erste.

Der Fehler dahinter: `request_thumbnails` hielt ein Bild fuer erledigt, sobald
es im RAM-Cache lag - unter der Annahme, wer es anfordert, habe es ohnehin
schon. Fuer eine neu geoeffnete Ansicht stimmt das nicht.

Folge: wer einen Ordner ansah und ihn danach in einem Tab oeffnete, bekam dort
leere Kacheln. Und es wurde auch nichts nachgeholt - die Bilder galten als
vorhanden. Genau die gemeldete Beobachtung: "die Kacheln sind da, aber die
Bilder nicht, und es werden auch keine Thumbs erzeugt."
"""

from __future__ import annotations

import pytest
from PIL import Image

from tests.conftest import pump_until


@pytest.fixture
def bibliothek(tmp_path):
    """Ein ai-Unterordner mit Bild-Prompt-Paaren, wie in der echten Bibliothek."""
    lib = tmp_path / "lib"
    ai = lib / "Person" / "ai"
    ai.mkdir(parents=True)
    for index in range(8):
        Image.new("RGB", (48, 48), (index * 30 % 255, 80, 140)).save(
            ai / ("bild%02d.jpg" % index)
        )
        (ai / ("bild%02d.txt" % index)).write_text("Prompt %d" % index, encoding="utf-8")
    return lib


@pytest.fixture
def app(qapp, application, bibliothek):
    application.settings.set("library.roots", [str(bibliothek)])
    application.settings.set("index.scan_on_startup", False)
    application.build_services()
    fertig: list = []
    application.index_service.scan_finished.connect(fertig.append)
    application.index_service.start_scan()
    assert pump_until(qapp, lambda: fertig, 60000), "Scan wurde nicht fertig"
    return application


def bilder_im_modell(model) -> int:
    from app.gui.library.thumbnail_model import PIXMAP_ROLE as rolle

    return sum(
        1
        for row in range(model.rowCount())
        if model.data(model.index(row, 0), rolle) is not None
    )


class TestZweiteAnsicht:
    def test_zwischengespeichertes_wird_ausgeliefert(self, qapp, app) -> None:
        """Der Kern: ein Bild im RAM muss an den Anfragenden gehen."""
        items = app.images.by_folder([f.id for f in app.folders.all_folders()])
        assert items, "Die Testbibliothek ist leer"

        geliefert: list = []
        app.image_service.thumbnails_ready.connect(lambda batch: geliefert.extend(batch))

        app.image_service.request_thumbnails(items)
        assert pump_until(qapp, lambda: len(geliefert) >= len(items), 30000)
        erste_runde = len(geliefert)

        # Zweite Anfrage fuer dieselben Bilder: jetzt liegen sie im RAM. Sie
        # muessen trotzdem ausgeliefert werden - eine neue Ansicht hat sie nicht.
        geliefert.clear()
        app.image_service.request_thumbnails(items)
        assert pump_until(qapp, lambda: geliefert, 15000), (
            "Bereits zwischengespeicherte Vorschaubilder wurden verschluckt"
        )
        assert len(geliefert) == erste_runde

    def test_ohne_erneute_dateiarbeit(self, qapp, app) -> None:
        """Die zweite Auslieferung darf keinen Auftrag mehr erzeugen."""
        items = app.images.by_folder([f.id for f in app.folders.all_folders()])
        geliefert: list = []
        app.image_service.thumbnails_ready.connect(lambda batch: geliefert.extend(batch))
        app.image_service.request_thumbnails(items)
        assert pump_until(qapp, lambda: len(geliefert) >= len(items), 30000)

        geliefert.clear()
        app.image_service.request_thumbnails(items)
        assert pump_until(qapp, lambda: geliefert, 15000)
        assert app.jobs.active_count == 0

    def test_kacheln_im_tab_bleiben_nicht_leer(self, qapp, app) -> None:
        """Der gemeldete Ablauf, vollstaendig nachgestellt."""
        from app.gui.main_window import MainWindow

        fenster = MainWindow(app)
        fenster.show()
        qapp.processEvents()
        try:
            ziel = {n.name: n for n in app.image_service.folder_tree()}["ai"]

            # 1) Ordner in der Bibliothek ansehen - fuellt den RAM-Cache.
            fenster._on_folder_selected(ziel, False)
            assert pump_until(
                qapp, lambda: fenster._explorer.item_count() > 0, 30000
            )
            fenster._explorer.view.request_visible()
            bibliothek_modell = fenster._explorer.view.thumbnail_model
            assert pump_until(
                qapp,
                lambda: bilder_im_modell(bibliothek_modell) == bibliothek_modell.rowCount(),
                30000,
            ), "Schon die Bibliotheksansicht bekam keine Bilder"

            # 2) Denselben Ordner in einem Tab oeffnen.
            fenster._open_folder_tab(ziel)
            # Der Titel traegt den Elternordner: "Person/ai". Ohne ihn hiessen
            # alle Tabs dieser Bibliothek gleich.
            tab = None
            for index in range(fenster._tabs.count()):
                if fenster._tabs.tabText(index).endswith("ai"):
                    tab = fenster._tabs.widget(index)
            assert tab is not None, (
                "Der Ordnertab wurde nicht geoeffnet. Titel: "
                + str([fenster._tabs.tabText(i) for i in range(fenster._tabs.count())])
            )
            assert pump_until(qapp, lambda: tab.explorer.item_count() > 0, 30000)
            tab.explorer.view.request_visible()

            tab_modell = tab.explorer.view.thumbnail_model
            assert pump_until(
                qapp,
                lambda: bilder_im_modell(tab_modell) == tab_modell.rowCount(),
                30000,
            ), (
                "Die Kacheln im Tab blieben leer: "
                + str(bilder_im_modell(tab_modell))
                + " von "
                + str(tab_modell.rowCount())
            )
        finally:
            fenster.close()
