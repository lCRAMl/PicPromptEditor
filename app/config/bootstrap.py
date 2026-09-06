"""Bootstrap-Konfiguration - die einzigen Einstellungen ausserhalb der Datenbank.

Spezifikation 11 verlangt, dass alle Einstellungen in der Tabelle settings liegen.
Fuer drei Werte ist das nicht moeglich: Datenbankpfad, Thumbnail-Cache-Pfad und
Logging-Ziel werden gebraucht, BEVOR die Datenbank geoeffnet ist. Sie in die
Datenbank zu legen, deren Pfad sie selbst bestimmen, ist zirkulaer.

Loesung: eine winzige JSON-Datei im Anwendungsdatenverzeichnis. Alles andere
bleibt in settings, und der Zugriff laeuft wie gefordert nur ueber Services.

Eine beschaedigte Datei wird beiseitegelegt statt gemeldet-und-abgebrochen: ohne
lesbare Bootstrap-Konfiguration kann die Anwendung sonst nicht einmal starten,
um den Fehler anzuzeigen.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.core.paths import (
    app_data_dir,
    default_cache_dir,
    default_db_path,
    default_log_dir,
    ensure_dir,
)

BOOTSTRAP_FILENAME = "bootstrap.json"
BOOTSTRAP_VERSION = 1

_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass
class BootstrapConfig:
    db_path: str = field(default_factory=lambda: str(default_db_path()))
    thumbnail_cache_dir: str = field(default_factory=lambda: str(default_cache_dir()))
    log_dir: str = field(default_factory=lambda: str(default_log_dir()))
    log_level: str = "INFO"
    version: int = BOOTSTRAP_VERSION
    first_run: bool = True

    def validated(self) -> tuple["BootstrapConfig", list[str]]:
        """Setzt unbrauchbare Werte auf Standardwerte zurueck und meldet, was korrigiert wurde."""
        notes: list[str] = []
        if not str(self.db_path).strip():
            self.db_path = str(default_db_path())
            notes.append("db_path war leer, Standardwert eingesetzt")
        if not str(self.thumbnail_cache_dir).strip():
            self.thumbnail_cache_dir = str(default_cache_dir())
            notes.append("thumbnail_cache_dir war leer, Standardwert eingesetzt")
        if not str(self.log_dir).strip():
            self.log_dir = str(default_log_dir())
            notes.append("log_dir war leer, Standardwert eingesetzt")
        level = str(self.log_level).upper()
        if level not in _VALID_LEVELS:
            notes.append("log_level '" + str(self.log_level) + "' ungueltig, INFO eingesetzt")
            level = "INFO"
        self.log_level = level
        return self, notes


def bootstrap_path() -> Path:
    return app_data_dir() / BOOTSTRAP_FILENAME


def load_bootstrap(path: str | Path | None = None) -> tuple[BootstrapConfig, list[str]]:
    """Laedt die Bootstrap-Konfiguration. Liefert (config, hinweise)."""
    target = Path(path) if path is not None else bootstrap_path()
    notes: list[str] = []

    if not target.exists():
        config, fixes = BootstrapConfig().validated()
        config.first_run = True
        notes.extend(fixes)
        notes.append("Keine Bootstrap-Konfiguration gefunden, Standardwerte angelegt")
        save_bootstrap(config, target)
        return config, notes

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Wurzelelement ist kein Objekt")
    except (OSError, ValueError) as exc:
        quarantine = target.with_suffix(".corrupt-" + str(int(time.time())) + ".json")
        try:
            target.replace(quarantine)
            notes.append("Bootstrap-Konfiguration unlesbar (" + str(exc) + "), verschoben nach " + quarantine.name)
        except OSError:
            notes.append("Bootstrap-Konfiguration unlesbar (" + str(exc) + ") und nicht verschiebbar")
        config, fixes = BootstrapConfig().validated()
        notes.extend(fixes)
        save_bootstrap(config, target)
        return config, notes

    known = {f for f in BootstrapConfig.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        notes.append("Unbekannte Bootstrap-Schluessel ignoriert: " + ", ".join(sorted(unknown)))
    config = BootstrapConfig(**{k: v for k, v in data.items() if k in known})
    config.first_run = False
    config, fixes = config.validated()
    notes.extend(fixes)

    if config.version != BOOTSTRAP_VERSION:
        notes.append(
            "Bootstrap-Version " + str(config.version) + " auf " + str(BOOTSTRAP_VERSION) + " gehoben"
        )
        config.version = BOOTSTRAP_VERSION
        save_bootstrap(config, target)
    return config, notes


def save_bootstrap(config: BootstrapConfig, path: str | Path | None = None) -> Path:
    """Schreibt die Konfiguration atomar (Temporaerdatei plus Ersetzen)."""
    target = Path(path) if path is not None else bootstrap_path()
    ensure_dir(target.parent)
    payload = asdict(config)
    payload.pop("first_run", None)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(target)
    return target
