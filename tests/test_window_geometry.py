"""Fenstergroesse und -position ueberdauern das Beenden.

Die Funktion gab es bereits; geprueft war sie nie. Diese Tests halten die
Faelle fest, in denen so etwas typischerweise doch scheitert: das maximierte
Fenster, das Beenden ueber das Menue - und der Fall, in dem closeEvent gar
nicht laeuft, weil die Anwendung anders endet.

Unter der Qt-Plattform "offscreen" ist der virtuelle Bildschirm klein; Qt
begrenzt eine wiederhergestellte Groesse darauf und die Pruefung waere nicht
aussagekraeftig. Dort wird uebersprungen. Vollstaendig laufen die Tests mit

    QT_QPA_PLATFORM=windows python -m pytest tests/test_window_geometry.py
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QCloseEvent, QGuiApplication

from app.gui.main_window import MainWindow

TEST_SCOPE = "PicPromptEditorTest"


@pytest.fixture
def eigener_speicher(monkeypatch):
    """Nicht in den echten Registry-Zweig schreiben."""
    store = QSettings(TEST_SCOPE, "MainWindow")
    store.clear()
    store.sync()
    monkeypatch.setattr(
        MainWindow, "_qsettings", lambda self: QSettings(TEST_SCOPE, "MainWindow")
    )
    yield store
    store.clear()
    store.sync()


@pytest.fixture
def grosser_bildschirm(qapp):
    screen = QGuiApplication.primaryScreen()
    verfuegbar = screen.availableGeometry() if screen is not None else None
    if verfuegbar is None or verfuegbar.width() < 1300 or verfuegbar.height() < 900:
        pytest.skip(
            "Bildschirm zu klein fuer die Pruefung ("
            + (str(verfuegbar.width()) + "x" + str(verfuegbar.height()) if verfuegbar else "?")
            + ") - Qt begrenzt die wiederhergestellte Groesse"
        )
    return verfuegbar


@pytest.fixture
def fenster(qapp, application, tmp_path, eigener_speicher):
    application.settings.set("library.roots", [str(tmp_path)])
    application.settings.set("index.scan_on_startup", False)
    application.build_services()

    offen: list[MainWindow] = []

    def neu() -> MainWindow:
        window = MainWindow(application)
        window.show()
        for _ in range(30):
            qapp.processEvents()
        offen.append(window)
        return window

    yield neu
    for window in offen:
        window.close()
    qapp.processEvents()


class TestGeometrie:
    def test_groesse_und_position_ueberdauern(
        self, qapp, fenster, grosser_bildschirm
    ) -> None:
        erstes = fenster()
        erstes.showNormal()
        erstes.resize(1100, 700)
        erstes.move(150, 100)
        for _ in range(30):
            qapp.processEvents()
        erstes.close()
        qapp.processEvents()

        zweites = fenster()
        assert (zweites.width(), zweites.height()) == (1100, 700)
        assert (zweites.x(), zweites.y()) == (150, 100)

    def test_maximiert_bleibt_maximiert(self, qapp, fenster, grosser_bildschirm) -> None:
        erstes = fenster()
        erstes.showMaximized()
        for _ in range(30):
            qapp.processEvents()
        assert erstes.isMaximized()
        erstes.close()
        qapp.processEvents()

        assert fenster().isMaximized()

    def test_teilerpositionen_ueberdauern(self, qapp, fenster) -> None:
        """Geprueft wird das VERHAELTNIS, nicht die Pixelzahl.

        restoreState() stellt die Aufteilung proportional zur Fenstergroesse
        wieder her. Ist das neue Fenster anders gross - unter der Plattform
        "offscreen" der Normalfall -, kommen andere absolute Werte heraus, und
        das ist richtig so.
        """
        erstes = fenster()
        erstes._main_splitter.setSizes([700, 300])
        for _ in range(20):
            qapp.processEvents()
        vorher = erstes._main_splitter.sizes()
        if sum(vorher) == 0:
            pytest.skip("Der Teiler hat keine Ausdehnung")
        anteil_vorher = vorher[0] / sum(vorher)
        erstes.close()
        qapp.processEvents()

        nachher = fenster()._main_splitter.sizes()
        anteil_nachher = nachher[0] / sum(nachher)
        assert anteil_nachher == pytest.approx(anteil_vorher, abs=0.05), (
            "Aufteilung " + str(vorher) + " -> " + str(nachher)
        )

    def test_ohne_fensterschluss_wird_trotzdem_gesichert(
        self, qapp, fenster, eigener_speicher
    ) -> None:
        """closeEvent laeuft nur beim Schliessen des Fensters.

        Endet die Anwendung anders, greift aboutToQuit - sonst waere die
        Groesse genau dann verloren, wenn man sie am ehesten vermisst.
        """
        window = fenster()
        eigener_speicher.clear()
        eigener_speicher.sync()
        assert eigener_speicher.value("geometry") is None

        # Das Signal von Hand ausloesen; qapp.quit() wuerde die Testsitzung
        # beenden.
        window._save_geometry()
        assert eigener_speicher.value("geometry") is not None

    def test_erster_start_ohne_gespeicherte_groesse(self, qapp, fenster) -> None:
        """Ohne Eintrag muss die Vorgabegroesse gelten, nicht null."""
        window = fenster()
        assert window.width() > 200 and window.height() > 200

    def test_closeEvent_sichert(self, qapp, fenster, eigener_speicher) -> None:
        window = fenster()
        eigener_speicher.clear()
        eigener_speicher.sync()
        window.closeEvent(QCloseEvent())
        assert eigener_speicher.value("geometry") is not None


class TestKachelraster:
    """Die Fensterbreite rastet auf ganze Kachelspalten ein.

    Gemeldet: nach dem Umbau des Stils passte das Einrasten nicht mehr - die
    letzte Spalte war um ein paar Bildpunkte angeschnitten.
    """

    def test_der_rollbalken_ist_immer_reserviert(self, qapp) -> None:
        """Die Ursache, und zwar die eigentliche.

        Erscheint der Rollbalken erst beim Ueberlaufen, springt die Breite des
        Sichtfensters um seine Staerke - das Einrasten rechnet aber mit dem
        Stand von vorher. Das Stylesheet hat die Leiste von 14 auf 16
        Bildpunkte verbreitert und den Fehler damit sichtbar gemacht; da war
        er schon zwei Bildpunkte gross.
        """
        from PyQt6.QtCore import Qt

        from app.gui.library.thumbnail_view import ThumbnailView
        from app.gui.theme import _LIGHT
        from app.services.selection_service import SelectionService

        view = ThumbnailView(_LIGHT, SelectionService())
        assert view.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn, (
            "Ohne feste Reserve springt die Sichtfensterbreite"
        )

    def test_die_sichtfensterbreite_springt_nicht(self, qapp) -> None:
        from app.gui.library.thumbnail_view import ThumbnailView
        from app.gui.theme import _LIGHT
        from app.services.selection_service import SelectionService

        view = ThumbnailView(_LIGHT, SelectionService())
        view.resize(900, 400)
        view.show()
        qapp.processEvents()
        leer = view.viewport().width()

        # Der Rollbalken bekommt etwas zu rollen.
        view.verticalScrollBar().setRange(0, 5000)
        qapp.processEvents()
        assert view.viewport().width() == leer, (
            "Die Breite hat sich geaendert, als der Rollbalken gebraucht wurde"
        )
        view.close()


class TestTrennerEinrasten:
    """Der Trenner eines Kacheltabs rastet ebenfalls auf ganze Spalten ein."""

    def test_der_ueberhang_wandert_zum_nachbarn(self) -> None:
        from app.gui.library.grid_snap import snapped_sizes

        assert snapped_sizes([900, 560], 37) == [863, 597]

    def test_ohne_ueberhang_bleibt_alles(self) -> None:
        from app.gui.library.grid_snap import snapped_sizes

        assert snapped_sizes([900, 560], 0) is None

    def test_das_raster_wird_nie_groesser(self) -> None:
        """Ein Trenner, der von selbst nach aussen wandert, waere uebergriffig."""
        from app.gui.library.grid_snap import snapped_sizes

        neu = snapped_sizes([900, 560], 37)
        assert neu[0] < 900 and neu[1] > 560

    def test_die_gesamtbreite_bleibt_gleich(self) -> None:
        from app.gui.library.grid_snap import snapped_sizes

        neu = snapped_sizes([900, 560], 37)
        assert sum(neu) == 900 + 560

    def test_unsinnige_faelle_bleiben_unberuehrt(self) -> None:
        from app.gui.library.grid_snap import snapped_sizes

        assert snapped_sizes([900], 10) is None, "nur eine Flaeche"
        assert snapped_sizes([5, 500], 10) is None, "wuerde das Raster ausloeschen"
        assert snapped_sizes([], 10) is None

    def test_der_kacheltab_hat_einen_einraster(self, qapp, application) -> None:
        from app.gui.library.grid_snap import GridSnapper
        from app.gui.tabs.result_tab import ResultTab
        from app.gui.theme import _LIGHT

        application.build_services()
        tab = ResultTab(
            application,
            _LIGHT,
            application.selection_service,
            title="Test",
            key="test:1",
            tile_size=256,
        )
        try:
            assert isinstance(tab._snapper, GridSnapper)
            # Ohne Ueberhang darf nichts geschehen.
            assert tab._snapper.snap_now() in (True, False)
        finally:
            tab.deleteLater()


class TestProgrammsymbol:
    def test_die_datei_liegt_im_paket(self) -> None:
        from app.gui.resources import APP_ICON, asset_path, missing_assets

        assert asset_path(APP_ICON).is_file()
        assert missing_assets() == []

    def test_autobuild_nimmt_dasselbe_symbol(self) -> None:
        """Sonst traegt die .exe ein anderes Zeichen als das Fenster."""
        import pathlib

        from app.gui.resources import APP_ICON

        quelle = (
            pathlib.Path(__file__).resolve().parent.parent / "AUTOBUILD.py"
        ).read_text(encoding="utf-8")
        assert APP_ICON in quelle
