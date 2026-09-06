"""Zeichnet eine Kachel des Thumbnail Explorers.

Ein eigener Delegate statt der Standarddarstellung, weil je Kachel mehr gezeigt
wird als ein Bild: Auswahlzustand, Vergleichsmarkierung, fehlender Prompt und
ein Platzhalter, solange das Vorschaubild noch entsteht.

Der Delegate haelt keinen Zustand und faellt nie auf einen Datenbank- oder
Dateizugriff zurueck. Er zeichnet ausschliesslich, was das Modell liefert.
"""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from app.gui.library.thumbnail_model import (
    HAS_PROMPT_ROLE,
    ITEM_ROLE,
    MARKED_ROLE,
    PIXMAP_ROLE,
    SLOT_ROLE,
)
from app.gui import icons
from app.gui.theme import Palette

LABEL_HEIGHT = 18
PADDING = 6


#: Groesse des Promptsymbols in Bildpunkten, abhaengig von der Kachelgroesse.
MIN_BADGE = 14
MAX_BADGE = 32
BADGE_MARGIN = 4
#: Deckkraft der Vergleichsmarkierung. Genug, um sie zu sehen, wenig genug,
#: dass das Bild darunter erkennbar bleibt.
MARK_ALPHA = 170
#: Kleinste Kantenlaenge und Dicke der Eckwinkel.
MIN_CORNER = 12
MIN_THICK = 3

class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, palette: Palette, tile_size: int = 256, parent=None) -> None:
        super().__init__(parent)
        self._palette = palette
        self._tile = tile_size

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette

    def set_tile_size(self, size: int) -> None:
        self._tile = size

    def _prompt_icon(self, size: int) -> QPixmap:
        """Das Promptsymbol in der gewuenschten Kantenlaenge.

        Kommt aus dem mitgelieferten VS-Code-Iconset und wird in der
        ZIELGROESSE gerendert, nicht aus einer grossen Fassung verkleinert.
        Bei achtzehn Bildpunkten ist das ein sichtbarer Unterschied.

        Zwischengespeichert wird in `app/gui/icons.py`, je Groesse und
        Farbschema. Der Schluessel enthaelt das Schema, deshalb braucht ein
        Themewechsel hier nichts zu leeren - er greift einfach auf einen
        anderen Eintrag zu.
        """
        return icons.prompt_badge(size, self._palette.dark)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(self._tile + 2 * PADDING, self._tile + LABEL_HEIGHT + 2 * PADDING)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        theme = self._palette
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        background = theme.tile_selected if selected else theme.tile_background
        painter.fillRect(rect, background)

        image_rect = QRect(
            rect.left() + PADDING,
            rect.top() + PADDING,
            rect.width() - 2 * PADDING,
            rect.height() - LABEL_HEIGHT - 2 * PADDING,
        )

        pixmap = index.data(PIXMAP_ROLE)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            scaled = pixmap.scaled(
                image_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            target = QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(image_rect.center())
            painter.drawPixmap(target, scaled)
        else:
            self._paint_placeholder(painter, image_rect, theme)

        self._paint_label(painter, rect, index, theme, selected)

        if index.data(MARKED_ROLE):
            self._paint_marker(painter, image_rect, theme, index.data(SLOT_ROLE))
        # Hinweis auf eine vorhandene Promptdatei. Frueher stand hier ein Kreuz
        # in der Warnfarbe fuer das FEHLEN eines Prompts - das sah aus wie ein
        # Schliessknopf und stand auf drei Vierteln aller Kacheln. Jetzt
        # umgekehrt: ein kleines Symbol kennzeichnet die Ausnahme, naemlich das
        # Vorhandensein.
        if index.data(HAS_PROMPT_ROLE):
            self._paint_prompt_badge(painter, image_rect)

        painter.setPen(QPen(theme.tile_border, 1))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        painter.restore()

    # Teilzeichnungen ----------------------------------------------------------

    def _paint_placeholder(self, painter: QPainter, rect: QRect, theme: Palette) -> None:
        """Platzhalter, solange das Vorschaubild im Hintergrund entsteht.

        Spezifikation 06 verlangt genau das: die Ansicht zeigt sofort etwas und
        wird nach Fertigstellung aktualisiert.
        """
        painter.fillRect(rect, theme.placeholder)
        painter.setPen(QPen(theme.text_muted, 1, Qt.PenStyle.DotLine))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _paint_label(
        self,
        painter: QPainter,
        rect: QRect,
        index: QModelIndex,
        theme: Palette,
        selected: bool,
    ) -> None:
        item = index.data(ITEM_ROLE)
        text = item.filename if item is not None else ""
        label_rect = QRect(
            rect.left() + PADDING,
            rect.bottom() - LABEL_HEIGHT - PADDING // 2,
            rect.width() - 2 * PADDING,
            LABEL_HEIGHT,
        )
        metrics = QFontMetrics(painter.font())
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, label_rect.width())
        painter.setPen(QPen(QColor(0, 0, 0) if not theme.dark else QColor(230, 230, 230)))
        if selected:
            painter.setPen(QPen(QColor(0, 0, 0) if not theme.dark else QColor(255, 255, 255)))
        painter.drawText(label_rect, int(Qt.AlignmentFlag.AlignCenter), elided)

    def _paint_marker(
        self, painter: QPainter, rect: QRect, theme: Palette, slot: int | None
    ) -> None:
        """Kennzeichnung fuer den Vergleich - als vier Ecken, nicht als Flaeche.

        Frueher ein durchgehender Rahmen. Vor allem aber blieb der Pinsel der
        Plakette gesetzt, sodass der anschliessend gezeichnete Kachelrahmen die
        GANZE Kachel orange fuellte - das Bild war nicht mehr zu sehen. Deshalb
        hier save/restore um alles.

        Die Ecken lassen die Bildmitte frei und sind trotzdem von jeder Seite
        aus zu erkennen. Halbdurchsichtig, damit auch unter der Markierung noch
        etwas vom Bild durchscheint.
        """
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)

        farbe = QColor(theme.marker_compare)
        farbe.setAlpha(MARK_ALPHA)
        painter.setBrush(farbe)

        kante = max(MIN_CORNER, min(rect.width(), rect.height()) // 4)
        dicke = max(MIN_THICK, kante // 4)
        for links in (True, False):
            for oben in (True, False):
                x = rect.left() if links else rect.right() - kante + 1
                y = rect.top() if oben else rect.bottom() - kante + 1
                # Ein L aus zwei Balken: waagerecht am Rand, senkrecht daran.
                painter.drawRect(QRect(x, rect.top() if oben else rect.bottom() - dicke + 1,
                                       kante, dicke))
                painter.drawRect(QRect(rect.left() if links else rect.right() - dicke + 1,
                                       y, dicke, kante))

        # Die Plakette traegt A oder B: welches Bild links und welches rechts
        # landet, ist damit schon vor dem Oeffnen des Vergleichs erkennbar.
        badge = QRect(rect.right() - 24, rect.top() + 6, 18, 18)
        painter.setBrush(QColor(theme.marker_compare))
        painter.drawEllipse(badge)
        if slot is not None:
            painter.setPen(QPen(QColor(255, 255, 255)))
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                badge, int(Qt.AlignmentFlag.AlignCenter), "AB"[slot] if slot < 2 else "?"
            )
        painter.restore()

    def _paint_prompt_badge(self, painter: QPainter, rect: QRect) -> None:
        """Kleines Symbol unten rechts in der Kachel."""
        size = max(MIN_BADGE, min(MAX_BADGE, rect.width() // 8))
        icon = self._prompt_icon(size)
        if icon.isNull():
            return

        target = QRect(
            rect.right() - icon.width() - BADGE_MARGIN,
            rect.bottom() - icon.height() - BADGE_MARGIN,
            icon.width(),
            icon.height(),
        )
        # Ohne Glaettung zeichnen: das Symbol liegt bereits in der Zielgroesse
        # vor, jede weitere Umrechnung wuerde es nur weichzeichnen.
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        painter.drawPixmap(target.topLeft(), icon)
        painter.restore()
