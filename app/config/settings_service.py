"""SettingsService - der einzige erlaubte Zugriffsweg auf Einstellungen.

Ablauf beim Start (Spezifikation 11):
  1. Alle Werte aus der Tabelle settings laden.
  2. Fehlende Einstellungen aus der Registry ergaenzen und persistieren.
  3. Unbrauchbare Werte auf den Standardwert zuruecksetzen und protokollieren.
  4. Konfigurationsversion vergleichen und Migration ausfuehren.

Die Werte liegen anschliessend typisiert im Speicher. get() ist damit ein
Dictionary-Zugriff ohne Datenbankverkehr und aus dem GUI-Thread unbedenklich.

Aenderungsbenachrichtigung laeuft ueber einfache Callbacks statt Qt-Signale,
damit auch Jobs und Analysecode ohne QApplication zuhoeren koennen. Die
Weiterleitung an Qt-Signale erfolgt in der GUI-Schicht.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from app.config.schema import CONFIG_VERSION, SETTINGS, SettingDef, ValidationError, all_defs
from app.core.errors import ErrorReporter
from app.repositories.settings_repo import SettingsRepository

Listener = Callable[[str, Any], None]


class UnknownSettingError(KeyError):
    pass


class SettingsService:
    def __init__(self, repository: SettingsRepository, reporter: ErrorReporter) -> None:
        self._repo = repository
        self._reporter = reporter
        self._values: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._listeners: dict[str, list[Listener]] = {}
        self._global_listeners: list[Listener] = []
        self._loaded = False

    # Laden --------------------------------------------------------------------

    def load(self) -> None:
        stored = self._repo.load_all()
        values: dict[str, Any] = {}
        missing: list[tuple[str, str, str]] = []
        repaired: list[str] = []

        for definition in all_defs():
            if definition.key in stored:
                raw = stored[definition.key][0]
                try:
                    values[definition.key] = definition.coerce(raw)
                    continue
                except (ValidationError, ValueError) as exc:
                    repaired.append(definition.key + " (" + str(exc) + ")")
            values[definition.key] = definition.default
            missing.append(
                (definition.key, definition.serialize(definition.default), definition.type)
            )

        with self._lock:
            self._values = values
            self._loaded = True

        if missing:
            self._repo.upsert_many(missing)
        self._prune_unknown(stored)
        if repaired:
            self._reporter.warning(
                "config",
                "Ungueltige Einstellungen auf Standardwerte zurueckgesetzt: "
                + ", ".join(repaired),
                message_key="config:reset_invalid",
            )

        self._migrate(stored)

    def _prune_unknown(self, stored: dict[str, tuple[str | None, str]]) -> None:
        """Entfernt Zeilen, zu denen es keine Einstellung mehr gibt.

        Ohne das bleibt jede gestrichene Einstellung ewig in der Tabelle
        stehen. Nachgesehen in einer gewachsenen Installation lagen dort drei
        solche Leichen: `ui.language` (i18n gestrichen),
        `compare.allow_write_back` (die Uebernahme schreibt nie mehr in die
        Vorlage) und `ui.tab_close_margin`. Sie schaden nicht, aber sie machen
        jede Auskunft ueber den Konfigurationsstand unzuverlaessig - und
        `delete_unknown` gab es die ganze Zeit, nur gerufen hat es niemand.

        **Nicht beim Herabstufen.** Stammt die Tabelle aus einer NEUEREN
        Programmversion, sind ihre unbekannten Schluessel keine Leichen,
        sondern Einstellungen, die diese Fassung noch nicht kennt. Sie zu
        loeschen hiesse, dem Benutzer beim Zurueckwechseln seine Konfiguration
        zu zerstoeren. Dieselbe Regel gilt schon in `_migrate`.
        """
        unbekannt = set(stored) - set(SETTINGS)
        if not unbekannt:
            return
        try:
            gespeicherte_version = int(stored["system.config_version"][0] or 0)
        except (KeyError, TypeError, ValueError):
            gespeicherte_version = CONFIG_VERSION
        if gespeicherte_version > CONFIG_VERSION:
            return
        self._repo.delete_unknown(list(SETTINGS))
        self._reporter.info(
            "config",
            "Verwaiste Einstellungen entfernt: " + ", ".join(sorted(unbekannt)),
            message_key="config:pruned",
        )

    def _migrate(self, stored: dict[str, tuple[str | None, str]]) -> None:
        """Konfigurationsmigration.

        Neue Programmversionen duerfen Einstellungen hinzufuegen; das erledigt
        bereits das Ergaenzen fehlender Schluessel oben. Hier stehen nur
        Umbenennungen und Bedeutungsaenderungen, die eine Uebernahme brauchen.
        """
        previous = self.get("system.config_version")
        if previous == CONFIG_VERSION:
            return
        if previous < CONFIG_VERSION:
            self._reporter.info(
                "config",
                "Konfiguration von Version " + str(previous) + " auf " + str(CONFIG_VERSION) + " gehoben",
                message_key="config:migrated",
            )
        else:
            self._reporter.warning(
                "config",
                "Konfiguration stammt aus neuerer Programmversion ("
                + str(previous)
                + "), unbekannte Werte bleiben unberuehrt",
                message_key="config:downgrade",
            )
        self.set("system.config_version", CONFIG_VERSION)

    # Lesen --------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        if key not in SETTINGS:
            raise UnknownSettingError(key)
        with self._lock:
            if not self._loaded:
                return SETTINGS[key].default
            return self._values.get(key, default if default is not None else SETTINGS[key].default)

    def get_int(self, key: str) -> int:
        return int(self.get(key))

    def get_float(self, key: str) -> float:
        return float(self.get(key))

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def get_str(self, key: str) -> str:
        return str(self.get(key))

    def get_list(self, key: str) -> list[Any]:
        value = self.get(key)
        return list(value) if isinstance(value, list) else []

    def definition(self, key: str) -> SettingDef:
        if key not in SETTINGS:
            raise UnknownSettingError(key)
        return SETTINGS[key]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    # Schreiben ----------------------------------------------------------------

    def set(self, key: str, value: Any) -> Any:
        """Validiert, speichert und benachrichtigt. Liefert den uebernommenen Wert."""
        definition = self.definition(key)
        try:
            coerced = definition.coerce(value)
        except (ValidationError, ValueError) as exc:
            self._reporter.warning(
                "config",
                "Einstellung " + key + " abgelehnt: " + str(exc),
                message_key="config:reject:" + key,
            )
            raise ValidationError(str(exc)) from exc

        with self._lock:
            unchanged = self._values.get(key) == coerced
            self._values[key] = coerced
        if unchanged:
            return coerced

        self._repo.upsert(key, definition.serialize(coerced), definition.type)
        self._notify(key, coerced)
        return coerced

    def reset(self, key: str) -> Any:
        return self.set(key, self.definition(key).default)

    def reset_all(self) -> None:
        for definition in all_defs():
            self.set(definition.key, definition.default)

    # Benachrichtigung ---------------------------------------------------------

    def subscribe(self, key: str | None, listener: Listener) -> None:
        """key=None hoert auf alle Aenderungen."""
        with self._lock:
            if key is None:
                self._global_listeners.append(listener)
            else:
                self._listeners.setdefault(key, []).append(listener)

    def unsubscribe(self, key: str | None, listener: Listener) -> None:
        with self._lock:
            target = self._global_listeners if key is None else self._listeners.get(key, [])
            if listener in target:
                target.remove(listener)

    def _notify(self, key: str, value: Any) -> None:
        with self._lock:
            listeners = list(self._listeners.get(key, [])) + list(self._global_listeners)
        for listener in listeners:
            try:
                listener(key, value)
            except Exception as exc:
                self._reporter.exception(
                    exc, source="config.listener", message_key="config.listener:" + key
                )
