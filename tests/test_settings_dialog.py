"""Einstellungsdialog: erzeugt er sich richtig aus dem Schema, und schreibt er richtig.

Der Dialog fuehrt bewusst keine eigene Liste der Einstellungen. Diese Tests
halten das fest: eine neue Einstellung im Schema muss von selbst erscheinen,
und keine darf unerreichbar sein.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
)

from app.config.schema import SETTINGS, all_defs
from app.gui.dialogs.settings_dialog import GROUPS, ListEditor, SettingsDialog


@pytest.fixture
def dialog(qapp, application):
    application.build_services()
    instance = SettingsDialog(application.settings, application.analysis_service)
    yield instance
    instance.deleteLater()


class TestAufbau:
    def test_jede_einstellung_ist_erreichbar(self, dialog) -> None:
        """Keine Einstellung darf im Dialog fehlen - sonst ist sie unerreichbar."""
        fehlend = {d.key for d in all_defs()} - set(dialog.values())
        assert not fehlend, "Nicht im Dialog: " + ", ".join(sorted(fehlend))

    def test_keine_gruppe_ohne_kategorie(self, dialog) -> None:
        gruppen = {d.group for d in all_defs()}
        namen = dict(GROUPS)
        for gruppe in gruppen:
            assert namen.get(gruppe, gruppe) in dialog._titles, (
                "Gruppe '" + gruppe + "' hat keine Kategorie"
            )

    def test_kategorienliste_und_seiten_passen_zusammen(self, dialog) -> None:
        assert dialog._nav.count() == dialog._pages.count() == len(dialog._titles)

    def test_kategorie_umschalten_wechselt_die_seite(self, dialog) -> None:
        dialog._nav.setCurrentRow(2)
        assert dialog._pages.currentIndex() == 2
        dialog._nav.setCurrentRow(0)
        assert dialog._pages.currentIndex() == 0

    def test_i18n_ist_verschwunden(self) -> None:
        """Mehrsprachigkeit wurde gestrichen; der tote Regler ist entfernt."""
        assert "ui.language" not in SETTINGS

    @pytest.mark.parametrize(
        "key, widget",
        [
            ("index.scan_on_startup", QCheckBox),          # bool
            ("index.background_scan_interval_s", QSpinBox),  # int mit Bereich
            ("analysis.cluster_threshold", QDoubleSpinBox),  # float mit Bereich
            ("ui.theme", QComboBox),                        # str mit Auswahl
            ("library.prompt_extension", QLineEdit),        # freier Text
            ("library.roots", ListEditor),                  # Liste
        ],
    )
    def test_passendes_eingabefeld_je_typ(self, dialog, key, widget) -> None:
        _definition, editor = dialog._editors[key]
        assert isinstance(editor, widget)

    def test_bereiche_stammen_aus_dem_validator(self, dialog) -> None:
        """Die Grenzen werden gelesen, nicht im Dialog wiederholt."""
        definition, editor = dialog._editors["index.background_scan_interval_s"]
        low, high = definition.bounds
        assert (editor.minimum(), editor.maximum()) == (low, high)

        definition, editor = dialog._editors["analysis.cluster_threshold"]
        low, high = definition.bounds
        assert (editor.minimum(), editor.maximum()) == (pytest.approx(low), pytest.approx(high))

    def test_auswahl_stammt_aus_dem_validator(self, dialog) -> None:
        definition, editor = dialog._editors["ui.theme"]
        vorhanden = [editor.itemText(i) for i in range(editor.count())]
        assert tuple(vorhanden) == definition.allowed

    def test_neustart_wird_gekennzeichnet(self, dialog) -> None:
        betroffen = [d for d in all_defs() if d.requires_restart]
        assert betroffen, "Ohne solche Einstellungen ist der Test sinnlos"
        # Die Kennzeichnung haengt am Beschriftungstext, nicht am Eingabefeld.
        from PyQt6.QtWidgets import QLabel

        beschriftungen = [
            w.text() for w in dialog.findChildren(QLabel) if w.text()
        ]
        for definition in betroffen:
            passend = [t for t in beschriftungen if t.startswith(definition.description)]
            assert passend, "Keine Beschriftung fuer " + definition.key
            assert any(t.endswith("*") for t in passend), (
                definition.key + " ist nicht als neustartpflichtig gekennzeichnet"
            )


class TestLaden:
    def test_gespeicherte_werte_erscheinen(self, qapp, application) -> None:
        application.settings.set("index.background_scan_interval_s", 1234)
        application.settings.set("ui.theme", "dark")
        application.settings.set("index.scan_on_startup", False)
        application.build_services()

        dialog = SettingsDialog(application.settings, application.analysis_service)
        werte = dialog.values()
        assert werte["index.background_scan_interval_s"] == 1234
        assert werte["ui.theme"] == "dark"
        assert werte["index.scan_on_startup"] is False
        dialog.deleteLater()

    def test_listen_erscheinen_vollstaendig(self, qapp, application, tmp_path) -> None:
        wurzeln = [str(tmp_path / "a"), str(tmp_path / "b")]
        application.settings.set("library.roots", wurzeln)
        application.build_services()

        dialog = SettingsDialog(application.settings, application.analysis_service)
        assert dialog.values()["library.roots"] == wurzeln
        dialog.deleteLater()


class TestSchreiben:
    def test_aenderung_wird_uebernommen(self, dialog, application) -> None:
        _definition, editor = dialog._editors["index.background_scan_interval_s"]
        editor.setValue(4321)

        geschrieben, abgewiesen, _neustart = dialog.apply()

        assert "index.background_scan_interval_s" in geschrieben
        assert not abgewiesen
        assert application.settings.get_int("index.background_scan_interval_s") == 4321

    def test_unveraendertes_wird_nicht_geschrieben(self, dialog) -> None:
        geschrieben, abgewiesen, _neustart = dialog.apply()
        assert geschrieben == []
        assert abgewiesen == []

    def test_zahlen_ausserhalb_des_bereichs_sind_gar_nicht_eingebbar(self, dialog) -> None:
        """Der Bereich des Eingabefelds verhindert den Fehler, statt ihn zu melden."""
        definition, editor = dialog._editors["analysis.cluster_threshold"]
        low, high = definition.bounds
        editor.setValue(high + 5.0)
        assert editor.value() == pytest.approx(high)
        editor.setValue(low - 5.0)
        assert editor.value() == pytest.approx(low)

    def test_ungueltiger_wert_reisst_die_uebrigen_nicht_mit(
        self, dialog, application
    ) -> None:
        """Ein durchgefallener Wert darf die gueltigen nicht blockieren."""
        _d1, gut = dialog._editors["index.background_scan_interval_s"]
        gut.setValue(777)
        # thumbnails.sizes verlangt Zahlen zwischen 16 und 2048.
        _d2, liste = dialog._editors["thumbnails.sizes"]
        liste.set_value(["keine-zahl"])

        geschrieben, abgewiesen, _neustart = dialog.apply()

        assert "index.background_scan_interval_s" in geschrieben
        assert application.settings.get_int("index.background_scan_interval_s") == 777
        assert abgewiesen, "Der ungueltige Wert wurde nicht bemaengelt"
        assert any("roessenstufen" in text or "Vorschau" in text or "int" in text.lower()
                   for text in abgewiesen), abgewiesen

    def test_neustartpflicht_wird_gemeldet(self, dialog, application) -> None:
        betroffen = [d for d in all_defs() if d.requires_restart and d.type == "int"]
        if not betroffen:
            pytest.skip("Keine neustartpflichtige Zahleinstellung vorhanden")
        definition = betroffen[0]
        _d, editor = dialog._editors[definition.key]
        editor.setValue(editor.value() + 1)

        _geschrieben, _abgewiesen, neustart = dialog.apply()
        assert neustart is True


class TestNeuanalyse:
    def test_analyseaenderung_verlangt_neuanalyse(self) -> None:
        """Der Fund aus der Kalibrierung: sonst bleibt die Aenderung wirkungslos."""
        assert SettingsDialog._needs_reanalysis(["analysis.cluster_threshold"])
        assert SettingsDialog._needs_reanalysis(["parsing.format"])

    def test_anzeigeaenderung_verlangt_keine(self) -> None:
        assert not SettingsDialog._needs_reanalysis(["ui.theme"])
        assert not SettingsDialog._needs_reanalysis(
            ["index.scan_on_startup", "cache.thumbnail_max_mb"]
        )

    def test_ohne_dienst_kein_absturz(self, qapp, application) -> None:
        """Der Dialog muss auch ohne Analysedienst benutzbar sein."""
        application.build_services()
        dialog = SettingsDialog(application.settings, None)
        dialog._offer_reanalysis()  # darf nichts tun und nicht scheitern
        dialog.deleteLater()


class TestAussehen:
    """Der Dialog war zwar vollstaendig, sah aber unaufgeraeumt aus.

    Zwei messbare Ursachen: die Reiterleiste brauchte 1404 Pixel bei 720 Pixel
    Fensterbreite - Qt blendete Pfeiltasten ein, und die Haelfte der Kategorien
    war unsichtbar. Und die Zahlenfelder richteten ihre Breite nach ihrem
    Wertebereich, von 14 bis 108 Pixel; untereinander stand nichts.
    """

    def test_alle_kategorien_sind_erreichbar(self, dialog) -> None:
        """Eine Liste rollt notfalls, blendet aber nichts aus."""
        assert dialog._nav.count() >= 9
        for row in range(dialog._nav.count()):
            assert dialog._nav.item(row).text().strip()

    def test_zahlenfelder_sind_gleich_breit(self, dialog) -> None:
        from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox
        from app.gui.dialogs.settings_dialog import FIELD_WIDTH

        breiten = {
            editor.width()
            for _definition, editor in dialog._editors.values()
            if isinstance(editor, (QSpinBox, QDoubleSpinBox, QComboBox))
        }
        assert breiten == {FIELD_WIDTH}, "Uneinheitliche Feldbreiten: " + str(sorted(breiten))

    def test_beschriftungen_beginnen_an_derselben_stelle(self, dialog) -> None:
        """Sonst springt die Feldspalte beim Wechsel der Kategorie."""
        from PyQt6.QtWidgets import QLabel
        from app.gui.dialogs.settings_dialog import LABEL_WIDTH

        breiten = {
            w.width() for w in dialog.findChildren(QLabel)
            if w.width() > 0 and w.toolTip()
        }
        assert breiten == {LABEL_WIDTH}, sorted(breiten)

    def test_jeder_bereich_passt_ohne_rollen(self, dialog) -> None:
        """Die Fenstergroesse folgt dem umfangreichsten Bereich."""
        zu_hoch = []
        for index in range(dialog._pages.count()):
            inhalt = dialog._pages.widget(index).widget()
            if inhalt.sizeHint().height() > dialog.height():
                zu_hoch.append(dialog._titles[index])
        assert not zu_hoch, "Bereiche passen nicht ins Fenster: " + ", ".join(zu_hoch)

    def test_reihenfolge_folgt_dem_schema(self, dialog) -> None:
        """Nicht alphabetisch: im Schema stehen verwandte Einstellungen beieinander."""
        aus_schema = [d.key for d in all_defs() if d.group == "analysis"]
        im_dialog = [k for k in dialog._editors if k.startswith("analysis.")]
        assert im_dialog == aus_schema


class TestFarbregler:
    """Drei Regler fuer die Einfaerbung im Editor - je Schema gespeichert.

    Die Anwendung hat ein helles und ein dunkles Schema. Ein einziger
    gemeinsamer Farbwert wuerde in einem der beiden unlesbar: ein dunkles Blau
    verschwindet auf dunklem Grund. Sichtbar ist deshalb ein Regler - der fuer
    das gerade aktive Schema -, gespeichert wird getrennt.
    """

    def _dialog(self, application, wahl: str):
        from app.gui.theme import ThemeManager

        application.build_services()
        theme = ThemeManager(wahl, application.settings)
        return SettingsDialog(application.settings, application.analysis_service, theme=theme), theme

    def test_jeder_regler_zeigt_auf_etwas_das_es_gibt(self) -> None:
        """Frueher stand hier eine feste Anzahl - das hat nichts geprueft.

        Bedeutsam ist nicht, WIE VIELE Farben einstellbar sind, sondern dass
        jeder Eintrag beide Enden trifft: einen Schluessel im Schema und ein
        Feld der Palette. Ein Tippfehler an einem der beiden Enden bliebe sonst
        stumm - der Regler erschiene im Dialog und wirkte nirgends.
        """
        import dataclasses

        from app.gui.theme import OVERRIDABLE, Palette

        felder = {f.name for f in dataclasses.fields(Palette)}
        for schluessel, ziele in OVERRIDABLE.items():
            assert schluessel in SETTINGS, "kein solcher Schluessel: " + schluessel
            assert ziele, "Eintrag ohne Ziel: " + schluessel
            for feld in ziele:
                assert feld in felder, "kein solches Palettenfeld: " + feld

    def test_die_rahmenfarben_sind_einstellbar(self) -> None:
        """Alle Flaechen sind gleich gefaerbt - die Rahmen tragen die Gliederung."""
        from app.gui.theme import OVERRIDABLE

        assert OVERRIDABLE.get("ui.color_accent") == ("accent",)
        assert OVERRIDABLE.get("ui.color_accent_dim") == ("accent_dim",)

    def test_die_hintergrundfarbe_trifft_alle_flaechen(self) -> None:
        """Eine Einstellung, vier Felder.

        Waere hier nur `window` genannt, faerbte sie die Leisten um und liesse
        Editor, Kachelraster und Eingabefelder auf dem alten Wert - genau der
        Flickenteppich, den die eine Flaechenfarbe beseitigt hat.
        """
        from app.gui.theme import OVERRIDABLE

        assert set(OVERRIDABLE["ui.color_background"]) == {
            "window", "surface", "tile_background", "input_background",
        }

    def test_regler_zeigt_die_vorgabe_des_aktiven_schemas(self, qapp, application) -> None:
        from app.gui.dialogs.settings_dialog import ColorEditor

        dialog, theme = self._dialog(application, "dark")
        _definition, editor = dialog._editors["ui.color_segment_rare"]
        assert isinstance(editor, ColorEditor)
        assert editor._scheme == "dark"
        assert editor.effective_color().name() == theme.palette.segment_rare.name()
        dialog.deleteLater()

    def test_aenderung_trifft_nur_das_aktive_schema(self, qapp, application) -> None:
        from app.gui.theme import ThemeManager

        dialog, theme = self._dialog(application, "dark")
        _definition, editor = dialog._editors["ui.color_segment_rare"]
        vorher_hell = ThemeManager("light", application.settings).palette.segment_rare.name()

        editor._stored["dark"] = "#7f1d1d"
        geschrieben, abgewiesen, _neustart = dialog.apply()
        assert "ui.color_segment_rare" in geschrieben and not abgewiesen

        theme.refresh()
        assert theme.palette.segment_rare.name() == "#7f1d1d"
        nachher_hell = ThemeManager("light", application.settings).palette.segment_rare.name()
        assert nachher_hell == vorher_hell, "Das helle Schema wurde mitveraendert"
        dialog.deleteLater()

    def test_zuruecksetzen_stellt_die_vorgabe_wieder_her(self, qapp, application) -> None:
        dialog, theme = self._dialog(application, "dark")
        _definition, editor = dialog._editors["ui.color_segment_rare"]
        vorgabe = editor.effective_color().name()

        editor._stored["dark"] = "#7f1d1d"
        dialog.apply()
        editor._clear()
        dialog.apply()
        theme.refresh()

        assert theme.palette.segment_rare.name() == vorgabe
        dialog.deleteLater()

    def test_ungueltige_farbe_wird_abgewiesen(self, qapp, application) -> None:
        dialog, _theme = self._dialog(application, "dark")
        _definition, editor = dialog._editors["ui.color_segment_rare"]
        editor._stored["dark"] = "himmelblau"

        _geschrieben, abgewiesen, _neustart = dialog.apply()
        assert abgewiesen, "Eine unsinnige Farbe muss auffallen"
        dialog.deleteLater()

    def test_gespeicherte_farbe_erscheint_im_regler(self, qapp, application) -> None:
        application.settings.set(
            "ui.color_segment_common", '{"dark": "#123456", "light": "#abcdef"}'
        )
        dialog, _theme = self._dialog(application, "dark")
        _definition, editor = dialog._editors["ui.color_segment_common"]
        assert editor.effective_color().name() == "#123456"
        dialog.deleteLater()

        dialog2, _t2 = self._dialog(application, "light")
        _d2, editor2 = dialog2._editors["ui.color_segment_common"]
        assert editor2.effective_color().name() == "#abcdef"
        dialog2.deleteLater()


class TestEinFehlschlagReisstNichtsMit:
    """Gemeldet: OK im Einstellungsdialog endete im Absturzdialog mit
    `TimeoutError`, und die Hintergrundfarbe war danach nicht gespeichert.

    `apply` schreibt Einstellung fuer Einstellung und faengt Fehler ab - aber
    nur `ValidationError` und `ValueError`. Der `TimeoutError` aus dem
    Schreibweg war nicht vorgesehen; er verliess die Schleife, und alles, was
    in der Aufzaehlung dahinter stand, wurde nie geschrieben.

    Der eigentliche Zeitablauf ist im Schreibweg beseitigt. Diese Tests halten
    die zweite Haelfte fest: die Zusicherung des Docstrings - ein Fehlschlag
    reisst die uebrigen nicht mit - muss fuer JEDE Ausnahme gelten, nicht nur
    fuer die zwei, an die jemand gedacht hat.
    """

    def _zwei_zahlenregler(self, dialog) -> tuple[str, str]:
        """Zwei Schluessel in der Reihenfolge, in der `apply` sie abarbeitet."""
        zahlen = [
            key
            for key, (_definition, editor) in dialog._editors.items()
            if isinstance(editor, QSpinBox)
        ]
        assert len(zahlen) >= 2
        return zahlen[0], zahlen[-1]

    def test_ein_werfendes_set_stoppt_die_schleife_nicht(self, dialog) -> None:
        from app.config.schema import SETTINGS

        stolperstein, danach = self._zwei_zahlenregler(dialog)
        original = dialog._settings.set

        def set_mit_stolperstein(key, value):
            if key == stolperstein:
                raise TimeoutError()
            return original(key, value)

        dialog._settings.set = set_mit_stolperstein
        try:
            for key in (stolperstein, danach):
                editor = dialog._editors[key][1]
                editor.setValue(min(editor.value() + 1, editor.maximum()))
            written, rejected, _restart = dialog.apply()
        finally:
            dialog._settings.set = original

        assert danach in written, (
            "Die Einstellung hinter dem Fehlschlag wurde nicht geschrieben -"
            " genau so ging die Hintergrundfarbe verloren"
        )
        assert any(SETTINGS[stolperstein].description in r for r in rejected)

    def test_apply_wirft_nicht_nach_aussen(self, dialog) -> None:
        """Was hier herausfliegt, landet als Absturzdialog vor dem Benutzer."""
        original = dialog._settings.set

        def set_wirft(key, value):
            raise TimeoutError()

        dialog._settings.set = set_wirft
        try:
            key = self._zwei_zahlenregler(dialog)[0]
            editor = dialog._editors[key][1]
            editor.setValue(min(editor.value() + 1, editor.maximum()))
            written, rejected, _restart = dialog.apply()
        finally:
            dialog._settings.set = original

        assert written == []
        assert rejected
