"""Symbole aus dem mitgelieferten VS-Code-Iconset.

Bisher war das Promptsymbol ein WebP aus 512 Bildpunkten, das fuer jede
Kachelgroesse heruntergerechnet wurde. Ein SVG ist dafuer besser geeignet, und
zwar nicht aus Prinzip, sondern aus einem messbaren Grund: es wird in der
**Zielgroesse gerendert** statt aus einer grossen Fassung verkleinert. Bei
achtzehn Bildpunkten - der ueblichen Groesse des Symbols in einer Kachel - ist
der Unterschied zwischen "gerendert" und "aus 512 verkleinert" deutlich
sichtbar.

Das Iconset liefert von jedem Symbol eine helle und eine dunkle Fassung. Die
Auswahl trifft deshalb das Farbschema; umgefaerbt wird nichts. Das erspart es,
die SVG-Datei als Text einzulesen und darin herumzuschreiben - Qt laedt sie
selbst, genau wie zuvor die Bilddatei.

Gerendert wird einmal je Groesse und Schema. Bei tausend sichtbaren Kacheln
waere ein Rendern je Zeichenvorgang tausend Renderdurchlaeufe pro Bildaufbau.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap

from app.gui.resources import asset_path

#: Wo das mitgelieferte Iconset liegt, relativ zum Ressourcenverzeichnis.
ICON_DIR = "themes/theme-defaults/fileicons/images"

#: Das Symbol fuer "zu diesem Bild gibt es einen Prompt". Ein Blatt Papier -
#: dasselbe, das VS Code fuer eine Textdatei zeigt.
PROMPT_ICON = "document"


def icon_file(name: str, dark: bool) -> str:
    """Pfad zur passenden Fassung eines Symbols.

    Die Namensgebung des Iconsets ist die von VS Code: `-dark` ist die Fassung
    FUER den Dunkelmodus (also ein helles Symbol), `-light` die fuer den
    hellen. Wer das verwechselt, bekommt schwarz auf schwarz.
    """
    return str(asset_path(ICON_DIR + "/" + name + ("-dark" if dark else "-light") + ".svg"))


#: Gerenderte Fassungen, je (Name, Groesse, Schema).
_CACHE: dict[tuple[str, int, bool], QPixmap] = {}


def render(name: str, size: int, dark: bool) -> QPixmap:
    """Ein Symbol in genau dieser Kantenlaenge.

    Liefert ein leeres QPixmap, wenn die Datei fehlt oder nicht lesbar ist -
    ein fehlendes Symbol darf das Zeichnen einer Kachel nicht abbrechen. Der
    Startbericht meldet fehlende Ressourcen ohnehin (siehe `resources.py`).
    """
    size = max(1, int(size))
    schluessel = (name, size, dark)
    fertig = _CACHE.get(schluessel)
    if fertig is not None:
        return fertig

    pixmap = _render_uncached(name, size, dark)
    _CACHE[schluessel] = pixmap
    return pixmap


def _render_uncached(name: str, size: int, dark: bool) -> QPixmap:
    from PyQt6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(icon_file(name, dark))
    if not renderer.isValid():
        return QPixmap()

    pixmap = QPixmap(size, size)
    # Durchsichtiger Grund: das Symbol liegt auf dem Vorschaubild, nicht auf
    # einer Flaeche. Ohne das Fuellen waere der Inhalt uninitialisierter
    # Speicher und es blitzte Muell auf.
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pixmap


def prompt_badge(size: int, dark: bool) -> QPixmap:
    """Das Promptsymbol fuer die Kachelansicht."""
    return render(PROMPT_ICON, size, dark)


def clear_cache() -> None:
    """Nur fuer Tests. Im Betrieb gibt es keinen Grund, den Cache zu leeren."""
    _CACHE.clear()
