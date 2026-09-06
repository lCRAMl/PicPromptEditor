"""PicPromptEditor - Prompt-Engineering-Werkzeug fuer grosse Bild-/Prompt-Bibliotheken.

Schichtenmodell (Datenflussrichtung strikt von oben nach unten):

    gui -> services -> analysis | repositories -> filesystem
                          |            |
                          +-> core <---+          (Querschnitt, kennt niemanden)
                          +-> cache
    jobs   : Querschnittsschicht, ruft ausschliesslich services/analysis auf
    dto    : Grenzobjekte zwischen services und gui
    config : Einstellungen, nur ueber SettingsService erreichbar

Rueckwaertsabhaengigkeiten sind verboten. Siehe doc/ARCHITECTURE.md.
"""

#: Die Version im Quelltext. AUTOBUILD.py schreibt beim Paketieren eine
#: `build_version.py` daneben, die sie ueberschreibt - dort steht dann die aus
#: Git ermittelte Fassung samt Bauzeitpunkt. Fehlt die Datei (Arbeit am
#: Quelltext, Testlauf), gilt dieser Wert.
#: Die Version im Quelltext. Sie wird NIE ueberschrieben - AUTOBUILD.py liest
#: sie als Rueckfallwert, wenn kein Git zur Verfuegung steht. Wuerde hier der
#: gebaute Wert stehen, schriebe jeder Bau die Version des vorigen fort.
SOURCE_VERSION = "0.1.0"
APP_NAME = "PicPromptEditor"

__version__ = SOURCE_VERSION
BUILD_TIME = ""
COMMIT = ""

try:  # pragma: no cover - haengt davon ab, ob gebaut wurde
    from build_version import BUILD_TIME, COMMIT  # type: ignore[no-redef]
    from build_version import VERSION as __version__  # type: ignore[no-redef]
except ImportError:
    pass


def version_label() -> str:
    """Was in der Titelzeile steht.

    Nach einem Bau die Version aus Git, sonst die aus dem Quelltext. Der
    Bauzeitpunkt kommt dazu, weil bei mehreren .exe-Fassungen sonst nicht zu
    unterscheiden ist, welche gerade laeuft.
    """
    label = APP_NAME + " " + __version__
    if BUILD_TIME:
        label += "  (" + BUILD_TIME + ")"
    return label
