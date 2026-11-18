"""AUTOBUILD - Version festschreiben und die Anwendung paketieren.

Ein Aufruf, drei Schritte:

    python AUTOBUILD.py

  1. Version bestimmen (aus Git, sonst aus app/__init__.py) und in
     `build_version.py` schreiben. Die Anwendung liest diese Datei beim Start
     und zeigt die Version in der Titelzeile.
  2. Mit PyInstaller eine einzelne .exe bauen, deren Dateiname die Version
     traegt.
  3. Eine Verknuepfung daneben legen und den Bauplatz aufraeumen.

**Die Falle dieses Projekts:** PyQt6 und PySide6 liegen in derselben
Python-Installation und bringen gleichnamige Qt6-DLLs mit. Sammelt PyInstaller
PySide6 mit ein, gewinnt beim Start die falsche DLL, und die fertige .exe
stirbt beim Import von QtCore. PySide6 wird deshalb ausdruecklich
ausgeschlossen - das ist die wichtigste Zeile in dieser Datei.

Ohne Git funktioniert der Lauf ebenfalls: dann liefert app/__init__.py die
Version, und Commit-Angaben bleiben leer. Beides ist ein gueltiger Zustand -
der Bau darf nicht davon abhaengen, ob gerade ein Repository danebenliegt.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import APP_NAME, SOURCE_VERSION  # noqa: E402

# =========================
# Konfiguration
# =========================

ENTRY_POINT = "main.py"
VERSION_FILE = ROOT / "build_version.py"
OUTPUT_DIR = ROOT / "output"
BUILD_DIR = ROOT / "pyinstaller_build"

#: Mitzuliefernde Ressourcen: Quelle -> Zielverzeichnis im Paket.
ASSET_PATHS: dict[str, str] = {
    "app/gui/assets": "app/gui/assets",
}

ICON_PATH = ROOT / "app" / "gui" / "assets" / "PPEicon.ico"

#: Was NICHT eingesammelt werden darf. PySide6 und shiboken6 kollidieren mit
#: PyQt6; die uebrigen sind Testwerkzeuge, die im Paket nichts verloren haben.
EXCLUDED = ("PySide6", "shiboken6", "PyQt5", "pytest", "_pytest", "pytestqt")


# =========================
# Version
# =========================


def _git(*args: str) -> str | None:
    try:
        output = subprocess.check_output(
            ["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return output.decode(errors="replace").strip() or None


def get_version() -> tuple[str, str, str]:
    """(Version, Commit, Commit-Adresse).

    Git ist die bessere Quelle, weil sie den Stand des Arbeitsverzeichnisses
    mitzaehlt. Fehlt Git oder ist das Projekt kein Repository, gilt die Version
    aus app/__init__.py - der Bau soll daran nicht scheitern.
    """
    described = _git("describe", "--tags", "--long", "--dirty")
    commit = _git("rev-parse", "--short", "HEAD") or ""
    url = ""

    remote = _git("config", "--get", "remote.origin.url")
    full = _git("rev-parse", "HEAD")
    if remote and full:
        if remote.startswith("git@github.com:"):
            remote = remote.replace("git@github.com:", "https://github.com/")
        if remote.endswith(".git"):
            remote = remote[:-4]
        url = remote + "/commit/" + full

    return described or SOURCE_VERSION, commit, url


def write_version_file(version: str, build_time: str, commit: str, url: str) -> None:
    content = (
        '"""AUTOMATISCH ERZEUGT von AUTOBUILD.py - nicht von Hand aendern."""\n\n'
        'APP_NAME = "' + APP_NAME + '"\n'
        'VERSION = "' + version + '"\n'
        'BUILD_TIME = "' + build_time + '"\n'
        'COMMIT = "' + commit + '"\n'
        'COMMIT_URL = "' + url + '"\n'
    )
    VERSION_FILE.write_text(content, encoding="utf-8")
    print("  Versionsdatei geschrieben: " + VERSION_FILE.name)


# =========================
# Bauen
# =========================


def build(exe_name: str) -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name=" + exe_name,
        "--distpath=" + str(OUTPUT_DIR),
        "--workpath=" + str(BUILD_DIR),
        "--specpath=" + str(BUILD_DIR),
    ]

    for module in EXCLUDED:
        command.append("--exclude-module=" + module)

    for source, target in ASSET_PATHS.items():
        if (ROOT / source).exists():
            command.append("--add-data=" + str(ROOT / source) + os.pathsep + target)

    if ICON_PATH.exists():
        command.append("--icon=" + str(ICON_PATH))

    command.append(str(ROOT / ENTRY_POINT))

    print("\n  " + " ".join(command[2:]) + "\n")
    subprocess.run(command, check=True, cwd=ROOT)


PIN_OK = "  Kachel vorhanden - sie startet jetzt den neuen Bau"
PIN_HINT = ('  Noch keine Kachel. Einmal anheften: Start > Alle Apps > {name} > Rechtsklick > '
            '"An Start anheften". Alte Kacheln mit Versionsnummer im Namen loesen.')


def start_menu_programs_dir() -> str:
    """Startmenue-Ordner "Programme" des Benutzers, auch bei umgeleiteten Profilen.
    Aus ihm speist Windows die Liste "Alle Apps" - und die Kacheln."""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        # 0x0002 = CSIDL_PROGRAMS, 0 = SHGFP_TYPE_CURRENT
        if ctypes.windll.shell32.SHGetFolderPathW(0, 0x0002, 0, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:
        pass
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")


def start_tile_present(shortcut) -> bool:
    """Steht im Startmenue eine Kachel fuer diese Verknuepfung? Gelesen mit Export-StartLayout."""
    with tempfile.TemporaryDirectory() as tmp:
        layout = os.path.join(tmp, "layout.xml")
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Export-StartLayout -Path '{layout}'"],
                       capture_output=True, timeout=60)
        if not os.path.exists(layout):
            return False
        with open(layout, encoding="utf-8", errors="ignore") as fh:
            text = fh.read().lower()
    return "\\" + os.path.basename(str(shortcut)).lower() in text


def check_start_tile(shortcut) -> None:
    """Meldet, ob die Kachel steht. Anheften kann das Skript sie nicht: Windows 10 laesst
    "An Start anheften" nur den Explorer ausfuehren, jedes andere Programm bekommt
    "Zugriff verweigert". Weil der Name der Verknuepfung keine Version traegt, folgt eine
    einmal angeheftete Kachel jedem weiteren Bau und startet immer die neueste Fassung.
    """
    name = os.path.splitext(os.path.basename(str(shortcut)))[0]
    if start_tile_present(shortcut):
        print(PIN_OK)
    else:
        print(PIN_HINT.format(name=name))


def create_shortcut(name: str, exe_path: Path) -> None:
    """Verknuepfung im Startmenue, ohne Version im Namen: eine dort angeheftete Kachel
    startet nach jedem Bau die neueste .exe. Schlaegt sie fehl, ist der Bau trotzdem gut."""
    try:
        import pythoncom
        from win32com.shell import shell

        pythoncom.CoInitialize()
        for _ in range(50):
            if exe_path.exists() and exe_path.stat().st_size > 0:
                break
            time.sleep(0.1)
        else:
            raise FileNotFoundError("Die .exe ist noch nicht fertig: " + str(exe_path))

        link = pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLinkW
        )
        link.SetPath(str(exe_path))
        link.SetWorkingDirectory(str(exe_path.parent))
        link.SetDescription(name)
        shortcut_path = os.path.join(start_menu_programs_dir(), name + ".lnk")
        link.QueryInterface(pythoncom.IID_IPersistFile).Save(shortcut_path, 0)
        print("  Startmenue-Verknuepfung zeigt auf den neuen Bau: " + shortcut_path)
        check_start_tile(shortcut_path)
    except Exception as exc:  # noqa: BLE001 - eine Verknuepfung ist Beiwerk
        print("  Verknuepfung nicht angelegt: " + str(exc))


def clean() -> None:
    shutil.rmtree(BUILD_DIR, ignore_errors=True)


# =========================
# Ablauf
# =========================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Baut PicPromptEditor als .exe")
    parser.add_argument(
        "--version-only",
        action="store_true",
        help="Nur build_version.py schreiben, nicht paketieren",
    )
    parser.add_argument(
        "--keep-build",
        action="store_true",
        help="Bauverzeichnis nicht loeschen (zur Fehlersuche)",
    )
    args = parser.parse_args(argv)

    version, commit, url = get_version()
    build_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("Bau von " + APP_NAME)
    print("  Version    : " + version + ("" if commit else "   (kein Git, Version aus app/__init__.py)"))
    if commit:
        print("  Commit     : " + commit)
    print("  Zeitpunkt  : " + build_time)

    write_version_file(version, build_time, commit, url)
    if args.version_only:
        return 0

    safe_version = version.replace("+", "_").replace(" ", "_").replace("/", "-")
    exe_name = APP_NAME.replace(" ", "") + "_" + safe_version

    OUTPUT_DIR.mkdir(exist_ok=True)
    build(exe_name)

    exe_path = OUTPUT_DIR / (exe_name + ".exe")
    create_shortcut(APP_NAME.replace(" ", ""), exe_path)
    if not args.keep_build:
        clean()

    if exe_path.exists():
        groesse = exe_path.stat().st_size / (1024 * 1024)
        print("\nFertig: " + str(exe_path) + "  (%.1f MB)" % groesse)
        return 0

    print("\nFehlgeschlagen: " + str(exe_path) + " wurde nicht erzeugt", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
