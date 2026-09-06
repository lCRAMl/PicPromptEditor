"""Tests des Konfigurationssystems: Bootstrap, Validierung, Migration, Ereignisse."""

from __future__ import annotations

import json

import pytest

from app.config.bootstrap import BootstrapConfig, load_bootstrap, save_bootstrap
from app.config.schema import CONFIG_VERSION, SETTINGS, ValidationError, all_defs
from app.config.settings_service import SettingsService, UnknownSettingError
from app.repositories.settings_repo import SettingsRepository


class TestBootstrap:
    def test_first_run_creates_defaults(self, tmp_path) -> None:
        target = tmp_path / "bootstrap.json"
        config, notes = load_bootstrap(target)
        assert target.exists()
        assert config.first_run is True
        assert config.log_level == "INFO"
        assert any("Standardwerte" in n for n in notes)

    def test_roundtrip_keeps_values(self, tmp_path) -> None:
        target = tmp_path / "bootstrap.json"
        save_bootstrap(BootstrapConfig(db_path="D:/x/library.db", log_level="DEBUG"), target)
        config, _ = load_bootstrap(target)
        assert config.db_path == "D:/x/library.db"
        assert config.log_level == "DEBUG"
        assert config.first_run is False

    def test_invalid_log_level_falls_back(self, tmp_path) -> None:
        target = tmp_path / "bootstrap.json"
        target.write_text(json.dumps({"log_level": "SCHWATZHAFT"}), encoding="utf-8")
        config, notes = load_bootstrap(target)
        assert config.log_level == "INFO"
        assert any("log_level" in n for n in notes)

    def test_corrupt_file_is_quarantined_not_fatal(self, tmp_path) -> None:
        """Ohne lesbare Bootstrap-Datei koennte die Anwendung nicht einmal starten,
        um den Fehler anzuzeigen. Sie wird deshalb beiseitegelegt."""
        target = tmp_path / "bootstrap.json"
        target.write_text("{ das ist kein JSON", encoding="utf-8")
        config, notes = load_bootstrap(target)
        assert config.db_path
        assert any("unlesbar" in n for n in notes)
        assert list(tmp_path.glob("bootstrap.corrupt-*.json"))
        assert target.exists(), "eine frische Standarddatei muss wieder da sein"

    def test_unknown_keys_are_ignored(self, tmp_path) -> None:
        target = tmp_path / "bootstrap.json"
        target.write_text(json.dumps({"log_level": "INFO", "zukunft": 1}), encoding="utf-8")
        config, notes = load_bootstrap(target)
        assert config.log_level == "INFO"
        assert any("zukunft" in n for n in notes)


class TestSchema:
    def test_every_default_passes_its_own_validator(self) -> None:
        """Ein Standardwert, der die eigene Validierung nicht besteht, waere eine
        Falle: der erste Start wuerde ihn sofort wieder zuruecksetzen."""
        for definition in all_defs():
            assert definition.coerce(definition.serialize(definition.default)) is not None, definition.key

    def test_bool_coercion_accepts_db_representation(self) -> None:
        definition = SETTINGS["ui.show_folder_counts"]
        assert definition.coerce("1") is True
        assert definition.coerce("0") is False
        with pytest.raises(ValidationError):
            definition.coerce("vielleicht")

    def test_range_validators_reject_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            SETTINGS["analysis.similarity_threshold"].coerce(1.5)
        with pytest.raises(ValidationError):
            SETTINGS["jobs.io_threads"].coerce(0)

    def test_choice_validator(self) -> None:
        assert SETTINGS["ui.theme"].coerce("dark") == "dark"
        with pytest.raises(ValidationError):
            SETTINGS["ui.theme"].coerce("neon")

    def test_thumbnail_sizes_are_sorted_and_deduplicated(self) -> None:
        assert SETTINGS["thumbnails.sizes"].coerce("[512, 128, 128, 256]") == [128, 256, 512]


class TestSettingsService:
    def _service(self, database, writer, reporter) -> SettingsService:
        service = SettingsService(SettingsRepository(database, writer), reporter)
        service.load()
        return service

    def test_load_seeds_all_defaults(self, database, writer, reporter) -> None:
        service = self._service(database, writer, reporter)
        writer.flush()
        stored = database.scalar("SELECT COUNT(*) FROM settings")
        assert stored == len(SETTINGS)
        assert service.get("thumbnails.default_size") == 256
        assert service.get("ui.editor_read_only") is True

    def test_missing_setting_is_added_on_next_start(self, database, writer, reporter) -> None:
        """Neue Programmversionen duerfen Einstellungen hinzufuegen, ohne eine
        bestehende Installation zu beschaedigen (Spezifikation 11)."""
        self._service(database, writer, reporter)
        writer.flush()
        writer.execute("DELETE FROM settings WHERE key = 'ui.theme'").result(timeout=10)
        service = self._service(database, writer, reporter)
        writer.flush()
        assert service.get("ui.theme") == "system"
        assert database.scalar("SELECT COUNT(*) FROM settings WHERE key='ui.theme'") == 1

    def test_invalid_stored_value_is_repaired_and_logged(
        self, database, writer, reporter, collected
    ) -> None:
        self._service(database, writer, reporter)
        writer.flush()
        writer.execute(
            "UPDATE settings SET value = 'siebzehn' WHERE key = 'thumbnails.default_size'"
        ).result(timeout=10)

        service = self._service(database, writer, reporter)
        assert service.get("thumbnails.default_size") == 256
        assert any("zurueckgesetzt" in e.message for e in collected)

    def test_set_persists_and_survives_reload(self, database, writer, reporter) -> None:
        service = self._service(database, writer, reporter)
        service.set("thumbnails.default_size", 512)
        writer.flush()
        again = self._service(database, writer, reporter)
        assert again.get("thumbnails.default_size") == 512

    def test_set_rejects_invalid_value(self, database, writer, reporter, collected) -> None:
        service = self._service(database, writer, reporter)
        with pytest.raises(ValidationError):
            service.set("analysis.cluster_threshold", 3.0)
        from app.config.schema import SETTINGS

        assert service.get("analysis.cluster_threshold") == SETTINGS[
            "analysis.cluster_threshold"
        ].default
        assert any("abgelehnt" in e.message for e in collected)

    def test_unknown_key_raises(self, database, writer, reporter) -> None:
        service = self._service(database, writer, reporter)
        with pytest.raises(UnknownSettingError):
            service.get("gibt.es.nicht")

    def test_change_notification_fires_once(self, database, writer, reporter) -> None:
        service = self._service(database, writer, reporter)
        seen: list[tuple[str, object]] = []
        service.subscribe("jobs.io_threads", lambda k, v: seen.append((k, v)))

        service.set("jobs.io_threads", 8)
        service.set("jobs.io_threads", 8)  # unveraendert, darf nicht erneut feuern

        assert seen == [("jobs.io_threads", 8)]

    def test_global_listener_sees_every_change(self, database, writer, reporter) -> None:
        service = self._service(database, writer, reporter)
        seen: list[str] = []
        service.subscribe(None, lambda k, v: seen.append(k))
        service.set("ui.theme", "dark")
        service.set("ui.editor_font_size", 12)
        assert seen == ["ui.theme", "ui.editor_font_size"]

    def test_broken_listener_does_not_break_set(
        self, database, writer, reporter, collected
    ) -> None:
        service = self._service(database, writer, reporter)

        def bad(key: str, value: object) -> None:
            raise RuntimeError("Zuhoerer defekt")

        service.subscribe("ui.theme", bad)
        service.set("ui.theme", "dark")

        assert service.get("ui.theme") == "dark"
        assert any(e.source == "config.listener" for e in collected)

    def test_config_version_is_recorded(self, database, writer, reporter) -> None:
        service = self._service(database, writer, reporter)
        assert service.get("system.config_version") == CONFIG_VERSION


class TestEinstellungenUeberlebenEinenBeschaeftigtenSchreiber:
    """Gemeldet: der Einstellungsdialog stuerzte beim Klick auf OK mit
    `TimeoutError` ab, und die Hintergrundfarbe war nach dem naechsten Start
    wieder weg.

    Beides derselbe Fehler. `SettingsRepository.upsert` wartete mit einer
    festen Frist von 15 Sekunden auf den EINEN Schreibfaden. Steckt dort ein
    groesserer Vorgang, ist die Frist abgelaufen, ohne dass irgendetwas
    haengt - die Aufgabe lief kurz darauf durch. Der Zeitablauf storniert sie
    naemlich nicht einmal.

    Die Folge war schlimmer als der Absturz: die Schleife in
    `SettingsDialog.apply` schrieb Einstellung fuer Einstellung und brach beim
    ERSTEN Zeitablauf ab. Alles, was in der Aufzaehlung dahinter stand - und
    die Hintergrundfarbe steht hinter den Akzentfarben -, wurde nie
    geschrieben.
    """

    def _service(self, database, writer, reporter) -> SettingsService:
        service = SettingsService(SettingsRepository(database, writer), reporter)
        service.load()
        writer.flush()
        return service

    def test_set_wartet_nicht_auf_den_schreiber(self, database, writer, reporter) -> None:
        """Ein beschaeftigter Schreibfaden darf `set` weder aufhalten noch werfen."""
        import threading
        import time

        service = self._service(database, writer, reporter)

        blockade = threading.Event()
        writer.submit(lambda conn: blockade.wait(timeout=30.0), label="haenger")
        try:
            began = time.monotonic()
            service.set("ui.color_background", '{"dark": "#123456"}')
            service.set("thumbnails.default_size", 512)
            gebraucht = time.monotonic() - began
        finally:
            blockade.set()

        assert gebraucht < 5.0, (
            "set() hat auf den Schreibfaden gewartet - im GUI-Faden friert"
            " genau das das Fenster ein"
        )
        assert service.get("ui.color_background") == '{"dark": "#123456"}'

    def test_der_wert_landet_trotzdem_in_der_datenbank(
        self, database, writer, reporter
    ) -> None:
        """Nicht warten heisst nicht vergessen: die Warteschlange ist FIFO."""
        import threading

        service = self._service(database, writer, reporter)

        blockade = threading.Event()
        writer.submit(lambda conn: blockade.wait(timeout=30.0), label="haenger")
        service.set("ui.color_background", '{"dark": "#123456"}')
        blockade.set()
        writer.flush()

        gespeichert = database.scalar(
            "SELECT value FROM settings WHERE key = 'ui.color_background'"
        )
        assert gespeichert == '{"dark": "#123456"}'

    def test_eine_spaetere_einstellung_geht_nicht_verloren(
        self, database, writer, reporter
    ) -> None:
        """Der Kern der Meldung: was hinter dem Stolperstein steht, muss ankommen."""
        import threading

        service = self._service(database, writer, reporter)

        blockade = threading.Event()
        writer.submit(lambda conn: blockade.wait(timeout=30.0), label="haenger")
        for key, value in (
            ("ui.color_accent", '{"dark": "#6fc3df"}'),
            ("ui.color_accent_dim", '{"dark": "#6fc3df"}'),
            ("ui.color_background", '{"dark": "#000000"}'),
        ):
            service.set(key, value)
        blockade.set()
        writer.flush()

        neu = SettingsService(SettingsRepository(database, writer), reporter)
        neu.load()
        assert neu.get("ui.color_background") == '{"dark": "#000000"}'
        assert neu.get("ui.color_accent") == '{"dark": "#6fc3df"}'

    def test_keine_feste_frist_mehr_im_einstellungsspeicher(self) -> None:
        """Wie fuer den Scanpfad festgehalten: keine Uhr im Schreibweg."""
        import pathlib

        quelle = (
            pathlib.Path(__file__).resolve().parent.parent
            / "app"
            / "repositories"
            / "settings_repo.py"
        ).read_text(encoding="utf-8")
        assert ".result(timeout=" not in quelle, (
            "settings_repo wartet wieder mit fester Frist - genau daran ist der"
            " Einstellungsdialog im Betrieb abgestuerzt"
        )


class TestVerwaisteEinstellungenVerschwinden:
    """Gestrichene Einstellungen blieben ewig in der Tabelle stehen.

    Nachgesehen in einer gewachsenen Installation: `ui.language` (i18n
    gestrichen), `compare.allow_write_back` (die Uebernahme schreibt nie mehr
    in die Vorlage) und `ui.tab_close_margin` standen noch da. Schaden
    richteten sie keinen an, aber jede Auskunft ueber den Konfigurationsstand
    war damit unzuverlaessig - und `SettingsRepository.delete_unknown` gab es
    die ganze Zeit, gerufen hat es nur niemand.
    """

    def _mit_leiche(self, database, writer, reporter, version: int):
        repo = SettingsRepository(database, writer)
        service = SettingsService(repo, reporter)
        service.load()
        writer.flush()
        repo.upsert("ui.language", "de", "str")
        repo.upsert("system.config_version", str(version), "int")
        writer.flush()
        return repo

    def test_die_leiche_wird_entfernt(self, database, writer, reporter) -> None:
        self._mit_leiche(database, writer, reporter, CONFIG_VERSION)

        SettingsService(SettingsRepository(database, writer), reporter).load()
        writer.flush()

        keys = {r["key"] for r in database.query("SELECT key FROM settings")}
        assert "ui.language" not in keys
        assert keys == set(SETTINGS), "es darf weder etwas fehlen noch uebrig sein"

    def test_beim_herabstufen_bleibt_alles_stehen(
        self, database, writer, reporter
    ) -> None:
        """Unbekannt heisst hier "aus einer neueren Fassung", nicht "Leiche"."""
        self._mit_leiche(database, writer, reporter, CONFIG_VERSION + 5)

        SettingsService(SettingsRepository(database, writer), reporter).load()
        writer.flush()

        keys = {r["key"] for r in database.query("SELECT key FROM settings")}
        assert "ui.language" in keys, (
            "Beim Zurueckwechseln auf eine aeltere Programmfassung wurde die"
            " Konfiguration der neueren zerstoert"
        )

    def test_ohne_leiche_wird_nichts_geschrieben(
        self, database, writer, reporter, collected
    ) -> None:
        SettingsService(SettingsRepository(database, writer), reporter).load()
        writer.flush()
        assert not any(e.message_key == "config:pruned" for e in collected)
