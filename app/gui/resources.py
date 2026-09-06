"""Wo die mitgelieferten Programmressourcen liegen.

Zwei Faelle, und sie unterscheiden sich:

* **Am Quelltext gearbeitet**: die Dateien liegen neben dem Paket, unter
  `app/gui/assets`.
* **Als .exe gepackt**: PyInstaller entpackt sie in ein Verzeichnis, dessen
  Pfad zur Laufzeit in `sys._MEIPASS` steht.

Diese Datei kennt keinen Qt-Typ und braucht keine laufende Anwendung: sie
rechnet nur einen Pfad aus. Dadurch laesst sich schon mit `python -m app
--check` feststellen, ob ein Paket vollstaendig ist - ohne eine Oberflaeche zu
starten.
"""

from __future__ import annotations

import sys
from pathlib import Path

ASSET_DIR = "assets"


def base_dir() -> Path:
    """Das Verzeichnis, unter dem `assets` zu finden ist."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "app" / "gui"
    return Path(__file__).resolve().parent


def asset_path(name: str) -> Path:
    return base_dir() / ASSET_DIR / name


#: Was vorhanden sein muss, damit die Oberflaeche vollstaendig ist.
#:
#: Das Promptsymbol der Kacheln kommt seit der Umstellung auf das
#: VS-Code-Aussehen aus dem mitgelieferten Iconset - beide Fassungen werden
#: gebraucht, weil sich Hell- und Dunkelmodus verschiedener Dateien bedienen.
#: Fehlt eine, zeichnet die Kachel ohne Symbol weiter; der Startbericht sagt
#: dann aber, woran es liegt.
#: Das Programmsymbol - Fenster, Taskleiste und .exe.
APP_ICON = "PPEicon.ico"

_ICONS = "themes/theme-defaults/fileicons/images/"
_ARROWS = "icons/arrow-"
REQUIRED = (
    APP_ICON,
    _ICONS + "document-dark.svg",
    _ICONS + "document-light.svg",
    # Die Pfeilspitzen der Zahlenfelder, Auswahlfelder und Rollbalken. Fehlen
    # sie, zeichnet Qt an ihrer Stelle nichts - und die Ursache waere ohne
    # diesen Eintrag nicht zu erraten, weil das Bedienelement sonst normal
    # aussieht.
) + tuple(
    _ARROWS + richtung + "-" + schema + ".svg"
    for richtung in ("up", "down", "left", "right")
    for schema in ("dark", "light")
)


def missing_assets() -> list[str]:
    """Welche Ressourcen fehlen. Leere Liste heisst: alles da.

    Wird beim Startbericht ausgewertet. Ein Paket, dem die Ressourcen fehlen,
    startet sonst klaglos und zeigt nur an einer Stelle nichts an - ein Fehler,
    der erst beim Hinsehen auffaellt.
    """
    return [name for name in REQUIRED if not asset_path(name).is_file()]
