"""Pfadhilfen fuer Windows, UNC-Freigaben und lange Pfade.

Zwei Windows-Eigenheiten, die auf einem NAS regelmaessig zuschlagen:

* Pfade ueber etwa 260 Zeichen brauchen ein Praefix. Fuer lokale Pfade ist das
  der Vier-Zeichen-Praefix aus Backslash, Backslash, Fragezeichen, Backslash;
  fuer UNC-Pfade zusaetzlich das Segment UNC. Ohne das schlagen open() und
  os.stat() mit ENOENT fehl, obwohl die Datei existiert.
* Vergleiche muessen case-insensitiv erfolgen, waehrend ein Linux-basiertes NAS
  selbst case-sensitiv ist. Deshalb wird neben dem Originalpfad immer eine
  normalisierte Form gespeichert und nur diese fuer Eindeutigkeit verwendet.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePath

from app import APP_NAME

_LONG_PATH_LIMIT = 240
_WIN_LONG_PREFIX = "\\\\" + "?" + "\\"
_WIN_UNC_PREFIX = _WIN_LONG_PREFIX + "UNC" + "\\"


def app_data_dir() -> Path:
    """Beschreibbares Verzeichnis fuer Bootstrap-Konfiguration, Datenbank, Logs, Cache."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / APP_NAME


def default_db_path() -> Path:
    return app_data_dir() / "library.db"


def default_cache_dir() -> Path:
    return app_data_dir() / "thumbnails"


def default_log_dir() -> Path:
    return app_data_dir() / "logs"


def normalize_path(path: str | PurePath) -> str:
    """Vergleichsform eines Pfades: Trennzeichen vereinheitlicht, casefold.

    Bewusst OHNE resolve() oder stat(). Beides wuerde auf einem nicht erreichbaren
    NAS blockieren, und eine reine Stringoperation darf nie am Netzwerk haengen.
    """
    text = str(path)
    if sys.platform == "win32":
        text = text.replace("/", "\\")
    stripped = text.rstrip("\\/")
    return (stripped or text).casefold()


def same_path(left: str | PurePath, right: str | PurePath) -> bool:
    """Ob zwei Pfade dieselbe Datei bezeichnen.

    Gebraucht wird das dort, wo ein Schreibvorgang eine bestimmte Datei
    ausdruecklich NICHT treffen darf. Die Pfade kommen dabei aus zwei Quellen
    mit verschiedenen Gewohnheiten: die Datenbank haelt sie so, wie der Scanner
    sie gesehen hat (`Z:/Ordner\\Datei.txt` - gemischte Trennzeichen sind unter
    Windows normal), der Dateidialog liefert Schraegstriche. Ein Vergleich mit
    == wuerde dieselbe Datei fuer zwei verschiedene halten und die Schutzregel
    stillschweigend aushebeln.

    Bewusst ohne `resolve()`: das wuerde auf einer nicht erreichbaren Freigabe
    blockieren. Symbolische Verknuepfungen und Junctions bleiben damit
    unerkannt - dafuer haengt die Pruefung nie am Netzwerk.
    """
    return normalize_path(left) == normalize_path(right)


def normalize_stem(filename: str | PurePath) -> str:
    """Paar-Identitaet von Bild und Prompt: Dateiname ohne Endung, casefold."""
    return PurePath(str(filename)).stem.casefold()


def is_unc(path: str | PurePath) -> bool:
    text = str(path)
    return text.startswith("\\\\") or text.startswith("//")


def long_path(path: str | PurePath) -> str:
    """Windows-Langpfadform, falls noetig. Auf anderen Plattformen unveraendert."""
    text = str(path)
    if sys.platform != "win32":
        return text
    if len(text) < _LONG_PATH_LIMIT or text.startswith(_WIN_LONG_PREFIX):
        return text
    text = text.replace("/", "\\")
    if text.startswith("\\\\"):
        return _WIN_UNC_PREFIX + text.lstrip("\\")
    return _WIN_LONG_PREFIX + text


def ensure_dir(path: str | PurePath) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def shard_path(key: str, depth: int = 2, width: int = 2) -> str:
    """Mehrstufige Verzeichnisaufteilung fuer den Thumbnail-Cache.

    Hunderttausend Dateien in einem Verzeichnis machen jede Verzeichnisoperation
    langsam. Die Aufteilung nach den ersten Zeichen des Schluessels haelt die
    einzelnen Ordner klein.
    """
    parts = []
    for i in range(depth):
        chunk = key[i * width : (i + 1) * width]
        parts.append(chunk if chunk else "00")
    parts.append(key)
    return "/".join(parts)
