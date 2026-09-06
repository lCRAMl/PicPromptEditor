"""Statusleiste, Sortierung, Promptsymbol, Einrasten der Fensterbreite, Bau.

Fuenf Rueckmeldungen aus dem Praxistest, hier festgehalten.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMainWindow

from app.dto.models import ImageItem, ResultSet
from app.gui.library.delegate import MAX_BADGE, MIN_BADGE, ThumbnailDelegate
from app.gui.library.thumbnail_model import ThumbnailModel
from app.gui.library.thumbnail_view import ThumbnailView
from app.gui.resources import asset_path, missing_assets
from app.gui.status.status_bar import ACTIVITY_WIDTH, StatusBar
from app.gui.theme import _DARK, _LIGHT
from app.services.selection_service import SelectionService


def make_item(index: int, name: str, modified: float = 0.0, size: int = 0, prompt=None):
    return ImageItem(
        id=index,
        folder_id=1,
        filename=name,
        stem=name.rsplit(".", 1)[0],
        full_path="Z:/lib/" + name,
        thumbnail_key="k%d" % index,
        thumbnail_state="ready",
        prompt_id=prompt,
        file_size=size,
        modified_time=modified,
    )


class TestStatusleiste:
    """Die Leiste zappelte, weil die Tatigkeitsanzeige den Ordnerpfad zeigt.

    Ordnerpfade sind unterschiedlich lang; ohne feste Breite schiebt jeder
    neue Pfad die Anzeigen rechts daneben hin und her.
    """

    @pytest.fixture
    def leiste(self, qapp):
        fenster = QMainWindow()
        bar = StatusBar(_LIGHT)
        fenster.setStatusBar(bar)
        fenster.resize(1400, 700)
        fenster.show()
        qapp.processEvents()
        yield bar
        fenster.close()

    def test_breite_bleibt_gleich(self, qapp, leiste) -> None:
        breiten = set()
        for pfad in (
            "Z:/a",
            "Z:/Instagram/Alyson Hannigan/ai",
            "Z:/Instagram/Reese Witherspoon/reese_ai/tiefer/noch tiefer/ganz unten",
            "Z:/b",
        ):
            leiste.set_activity(1, "Abgleich: " + pfad)
            qapp.processEvents()
            breiten.add(leiste._activity.width())
        assert breiten == {ACTIVITY_WIDTH}, "Die Anzeige veraendert ihre Breite: " + str(breiten)

    def test_langer_pfad_wird_gekuerzt_nicht_abgeschnitten(self, qapp, leiste) -> None:
        pfad = "Z:/" + "/".join("ordner%02d" % i for i in range(20))
        leiste.set_activity(1, "Abgleich: " + pfad)
        assert len(leiste._activity.text()) < len(pfad)
        assert leiste._activity.toolTip().endswith(pfad), "Der volle Pfad fehlt im Tooltip"

    def test_ohne_arbeit_ist_die_anzeige_leer(self, qapp, leiste) -> None:
        leiste.set_activity(1, "Abgleich: Z:/x")
        leiste.set_activity(0, "")
        assert leiste._activity.text() == ""
        assert leiste._activity.toolTip() == ""


class TestSortierung:
    @pytest.fixture
    def modell(self, qapp):
        model = ThumbnailModel(SelectionService())
        model.set_results(
            ResultSet(
                items=[
                    make_item(1, "charlie.jpg", modified=300.0, size=30, prompt=None),
                    make_item(2, "alpha.jpg", modified=100.0, size=10, prompt=7),
                    make_item(3, "bravo.jpg", modified=200.0, size=20, prompt=None),
                ],
                origin="test",
            )
        )
        return model

    def _namen(self, modell) -> list[str]:
        return [modell.items_in_range(0, 99)[i].filename for i in range(modell.rowCount())]

    def test_vorgabe_laesst_die_reihenfolge_der_abfrage(self, modell) -> None:
        """Sonst waere die Relevanz einer Suche und die Zeitordnung der
        Ansicht "neueste Paare" zerstoert."""
        vorher = self._namen(modell)
        modell.sort_items(0)
        assert self._namen(modell) == vorher

    def test_zurueck_zur_reihenfolge_der_abfrage(self, modell) -> None:
        geliefert = self._namen(modell)
        modell.sort_items(1)
        assert self._namen(modell) != geliefert
        modell.sort_items(0)
        assert self._namen(modell) == geliefert

    def test_name_aufsteigend_und_absteigend(self, modell) -> None:
        modell.sort_items(1)
        assert self._namen(modell) == ["alpha.jpg", "bravo.jpg", "charlie.jpg"]
        modell.sort_items(2)
        assert self._namen(modell) == ["charlie.jpg", "bravo.jpg", "alpha.jpg"]

    def test_nach_zeitstempel(self, modell) -> None:
        modell.sort_items(3)  # neueste zuerst
        assert self._namen(modell) == ["charlie.jpg", "bravo.jpg", "alpha.jpg"]
        modell.sort_items(4)  # aelteste zuerst
        assert self._namen(modell) == ["alpha.jpg", "bravo.jpg", "charlie.jpg"]

    def test_nach_dateigroesse(self, modell) -> None:
        modell.sort_items(5)
        assert self._namen(modell) == ["charlie.jpg", "bravo.jpg", "alpha.jpg"]

    def test_mit_prompt_zuerst(self, modell) -> None:
        modell.sort_items(7)
        assert self._namen(modell)[0] == "alpha.jpg", "Das Bild mit Prompt muss vorn stehen"

    def test_zeilenzuordnung_bleibt_gueltig(self, modell) -> None:
        """Nach dem Sortieren muss row_of_id noch stimmen - sonst landen die
        Vorschaubilder auf den falschen Kacheln."""
        modell.sort_items(2)
        for row in range(modell.rowCount()):
            item = modell.items_in_range(row, row)[0]
            assert modell._row_by_id[item.id] == row

    def test_unbekannter_index_aendert_nichts(self, modell) -> None:
        vorher = self._namen(modell)
        modell.sort_items(99)
        assert self._namen(modell) == vorher


class TestPromptsymbol:
    """Das Symbol kommt aus dem mitgelieferten VS-Code-Iconset."""

    def test_beide_fassungen_liegen_im_paket(self) -> None:
        from pathlib import Path

        from app.gui import icons

        assert missing_assets() == []
        for dunkel in (True, False):
            # icon_file() liefert bereits einen vollstaendigen Pfad.
            assert Path(icons.icon_file(icons.PROMPT_ICON, dunkel)).is_file()

    def test_symbol_wird_geladen(self, qapp) -> None:
        delegate = ThumbnailDelegate(_LIGHT, 256)
        assert not delegate._prompt_icon(18).isNull()

    def test_hell_und_dunkel_sind_verschiedene_dateien(self, qapp) -> None:
        """Sonst steht im Dunkelmodus schwarz auf schwarz."""
        from app.gui import icons

        assert icons.icon_file(icons.PROMPT_ICON, True) != icons.icon_file(
            icons.PROMPT_ICON, False
        )
        hell = ThumbnailDelegate(_LIGHT, 256)._prompt_icon(24)
        dunkel = ThumbnailDelegate(_DARK, 256)._prompt_icon(24)
        assert hell.toImage() != dunkel.toImage(), (
            "Beide Schemata bekommen dasselbe Bild"
        )

    def test_in_der_zielgroesse_gerendert(self, qapp) -> None:
        """Der Grund fuer SVG: kein Verkleinern aus 512 Bildpunkten.

        Ein gerendertes Symbol hat genau die angeforderte Kantenlaenge - nicht
        die naechstbeste, die beim Skalieren mit Seitenverhaeltnis herauskommt.
        """
        delegate = ThumbnailDelegate(_LIGHT, 256)
        for kante in (14, 18, 23, 32):
            assert delegate._prompt_icon(kante).width() == kante

    def test_groesse_folgt_der_kachel_und_bleibt_im_rahmen(self, qapp) -> None:
        delegate = ThumbnailDelegate(_LIGHT, 256)
        klein = delegate._prompt_icon(max(MIN_BADGE, min(MAX_BADGE, 128 // 8)))
        gross = delegate._prompt_icon(max(MIN_BADGE, min(MAX_BADGE, 512 // 8)))
        assert MIN_BADGE <= klein.width() <= MAX_BADGE
        assert MIN_BADGE <= gross.width() <= MAX_BADGE
        assert gross.width() >= klein.width()

    def test_jede_groesse_nur_einmal_gerendert(self, qapp) -> None:
        delegate = ThumbnailDelegate(_LIGHT, 256)
        erst = delegate._prompt_icon(20)
        nochmal = delegate._prompt_icon(20)
        assert erst is nochmal, "Das Symbol wird bei jedem Zeichnen neu gerendert"

    def test_fehlendes_symbol_bricht_das_zeichnen_nicht_ab(self, qapp) -> None:
        from app.gui import icons

        icons.clear_cache()
        try:
            leer = icons.render("gibt-es-nicht", 18, True)
            assert leer.isNull(), "Ein fehlendes Symbol muss leer sein, nicht werfen"
        finally:
            icons.clear_cache()

    def test_kein_kreuz_mehr(self) -> None:
        import app.gui.library.delegate as delegate

        with open(delegate.__file__, encoding="utf-8") as handle:
            assert "_paint_missing_prompt" not in handle.read()


class TestEinrasten:
    @pytest.fixture
    def ansicht(self, qapp):
        view = ThumbnailView(_LIGHT, SelectionService())
        view.thumbnail_model.set_results(
            ResultSet(items=[make_item(i, "p%d.jpg" % i) for i in range(200)], origin="t")
        )
        view.resize(900, 600)
        view.show()
        qapp.processEvents()
        yield view
        view.deleteLater()

    def test_ueberstand_ist_kleiner_als_eine_kachel(self, qapp, ansicht) -> None:
        step = ansicht._grid_step().width()
        for breite in range(600, 1400, 37):
            ansicht.resize(breite, 600)
            qapp.processEvents()
            assert 0 <= ansicht.surplus_width() < step

    def test_ueberstand_misst_am_sichtfenster(self, qapp, ansicht) -> None:
        """Der Rollbalken darf nicht mitzaehlen, sonst stuende er ueber der
        letzten Spalte."""
        rand = ansicht.width() - ansicht.viewport().width()
        for spalten in (3, 4, 5):
            ansicht.resize(ansicht.exact_width_for(spalten) + rand, 600)
            qapp.processEvents()
            assert ansicht.surplus_width() == 0, str(spalten) + " Spalten"
            assert ansicht.visible_columns() == spalten

    def test_spaltenmass_entspricht_der_auslegung(self, qapp, ansicht) -> None:
        """Gerechnetes Mass gegen Qts tatsaechliche Auslegung.

        Hier lag der Fehler: mit dem doppelten Abstand gerechnet war das Mass
        je Spalte vier Bildpunkte zu breit, und das Einrasten liess rechts eine
        Luecke von fast einem Kachelviertel stehen.
        """
        ansicht.resize(1200, 600)
        qapp.processEvents()
        modell = ansicht.thumbnail_model
        rechtecke = [ansicht.visualRect(modell.index(i, 0)) for i in range(12)]
        linke = sorted({r.left() for r in rechtecke})
        abstaende = {linke[i + 1] - linke[i] for i in range(len(linke) - 1)}
        assert abstaende == {ansicht._grid_step().width()}, (
            "Qt legt mit " + str(abstaende) + " aus, gerechnet wird mit "
            + str(ansicht._grid_step().width())
        )

    def test_sichtbare_spalten_stimmen(self, qapp, ansicht) -> None:
        ansicht.resize(1200, 600)
        qapp.processEvents()
        modell = ansicht.thumbnail_model
        rechtecke = [ansicht.visualRect(modell.index(i, 0)) for i in range(14)]
        obere = [r for r in rechtecke if r.top() == rechtecke[0].top()]
        assert ansicht.visible_columns() == len(obere)

    def test_zu_schmal_meldet_keinen_ueberstand(self, qapp, ansicht) -> None:
        ansicht.resize(60, 600)
        qapp.processEvents()
        assert ansicht.surplus_width() == 0


class TestBau:
    def test_version_steht_in_der_titelzeile(self) -> None:
        from app import APP_NAME, version_label

        label = version_label()
        assert label.startswith(APP_NAME)
        assert any(z.isdigit() for z in label), "Ohne Ziffer keine Version: " + label

    def test_quellversion_wird_nie_ueberschrieben(self) -> None:
        """Sonst schriebe jeder Bau die Version des vorigen fort."""
        from app import SOURCE_VERSION

        assert SOURCE_VERSION and SOURCE_VERSION[0].isdigit()

    def test_pyside_ist_ausgeschlossen(self) -> None:
        """Die wichtigste Zeile im Bauskript: PyQt6 und PySide6 bringen
        gleichnamige Qt6-DLLs mit, und die .exe stirbt an der falschen."""
        import AUTOBUILD

        assert "PySide6" in AUTOBUILD.EXCLUDED
        assert "shiboken6" in AUTOBUILD.EXCLUDED

    def test_einstiegspunkt_existiert(self) -> None:
        import pathlib

        import AUTOBUILD

        assert (pathlib.Path(AUTOBUILD.ROOT) / AUTOBUILD.ENTRY_POINT).is_file()

    def test_version_ohne_git(self) -> None:
        """Dieses Projekt ist kein Git-Repository - der Bau muss trotzdem gehen."""
        import AUTOBUILD

        version, _commit, _url = AUTOBUILD.get_version()
        assert version, "Ohne Git kam keine Version heraus"


class TestNeuestePaare:
    """Bibliotheksweite Ansicht: was zuletzt entstanden ist."""

    def test_nur_bilder_mit_prompt(self, database, writer) -> None:
        from app.repositories.image_repo import ImageRepository

        repo = ImageRepository(database, writer)
        assert repo.newest_pairs(10) == [], "Ohne Daten darf nichts kommen"

    def test_reihenfolge_und_grenze(self, qapp, application, tmp_path) -> None:
        """Neueste zuerst, und die Obergrenze wird eingehalten."""
        import os

        from PIL import Image

        from tests.conftest import pump_until

        lib = tmp_path / "lib"
        lib.mkdir()
        for index in range(6):
            Image.new("RGB", (16, 16), (index * 30, 80, 140)).save(
                lib / ("bild%d.jpg" % index)
            )
            if index % 2 == 0:
                target = lib / ("bild%d.txt" % index)
                target.write_text("Prompt %d" % index, encoding="utf-8")
                stamp = 1_700_000_000 + index * 1000
                os.utime(target, (stamp, stamp))

        application.settings.set("library.roots", [str(lib)])
        application.settings.set("index.scan_on_startup", False)
        application.build_services()
        fertig: list = []
        application.index_service.scan_finished.connect(fertig.append)
        application.index_service.start_scan()
        assert pump_until(qapp, lambda: fertig, 60000)

        paare = application.images.newest_pairs(50)
        assert [p.filename for p in paare] == ["bild4.jpg", "bild2.jpg", "bild0.jpg"]
        assert all(p.prompt_id is not None for p in paare), "Ein Bild ohne Prompt kam mit"
        assert len(application.images.newest_pairs(2)) == 2
