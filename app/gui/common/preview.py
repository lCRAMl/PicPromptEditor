"""Vorschau eines ausgewaehlten Bildes: Bildansicht plus Prompt.

Bewusst ein Controller und kein fertiges Layout. Das Bibliothekstab ordnet
Editor und Bildansicht dreispaltig neben dem Ordnerbaum an, ein Ergebnistab
zweispaltig neben dem Kachelraster. Waere die Vorschau ein Widget mit festem
Aufbau, muesste eine der beiden Anordnungen den Ladecode duplizieren.

Der Controller erzeugt die Widgets, der Aufrufer platziert sie.

Jede Vorschau fuehrt ihre eigene Anfragenummer. Mehrere Vorschauen teilen sich
denselben PromptService; ohne Nummer wuerde die Antwort auf die Anfrage des
einen Tabs im anderen landen.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.dto.models import ImageItem, PromptData
from app.gui.editor.highlighter import SegmentSpan
from app.gui.editor.prompt_editor import PromptEditor
from app.gui.image.image_view import ImageView
from app.jobs.scan_jobs import QueryJob


class PreviewController(QObject):
    """context_action(str, ImageItem), segment_action(str, text)."""

    context_action = pyqtSignal(str, object)
    open_requested = pyqtSignal(object)
    segment_action = pyqtSignal(str, str)

    def __init__(
        self,
        application,
        font_size: int = 10,
        *,
        palette=None,
        parent: QObject | None = None,
    ) -> None:
        """palette und parent sind bewusst nur als Schluesselwort uebergebbar.

        Als dritte Position stand hier frueher parent. Nach dem Einfuegen von
        palette uebergaben die Aufrufer ihr Tab-Objekt als Palette, und der
        Highlighter warf bei jedem Zeichenvorgang.
        """
        super().__init__(parent)
        self._app = application
        self._images = application.image_service
        self._prompts = application.prompt_service
        self._analysis = getattr(application, "analysis_service", None)

        self.image_view = ImageView()
        self.image_view.set_selection_service(
            getattr(application, "selection_service", None)
        )
        self.image_view.show_placeholder()
        self.image_view.context_action.connect(self.context_action.emit)
        self.image_view.image_clicked.connect(self.open_requested.emit)

        self.editor = PromptEditor(font_size, palette)
        self.editor.segment_action.connect(self.segment_action.emit)
        self.header = QLabel("Prompt")
        self.header.setContentsMargins(4, 2, 4, 2)

        # Ohne Legende ist die Einfaerbung nur bunt: dass Grau "Textbaustein"
        # heisst und Blau "selten", errraet niemand.
        from app.gui.editor.legend import HighlightLegend

        self.legend = HighlightLegend(palette)
        if self._app is not None:
            self.legend.set_threshold_hint(
                self._app.settings.get_float("ui.highlight_min_idf")
            )

        self._item: ImageItem | None = None
        self._pixel_size: tuple[int, int] | None = None
        self._missing_prompt = False
        self._prompt_request = 0
        self._image_request = 0
        self._stats_request = 0

        self._prompts.prompt_ready.connect(self._on_prompt)

    # Widgets ------------------------------------------------------------------

    def editor_panel(self) -> QWidget:
        """Editor mit Kopfzeile als ein Widget, fuer die Platzierung im Splitter."""
        panel = QWidget()
        # Der Name ist der Anker fuer die Rahmenregel im Stylesheet. Ohne ihn
        # gaebe es keine Moeglichkeit, genau diese Zusammenstellung aus
        # Kopfzeile, Text und Legende als eine Flaeche zu umranden.
        panel.setObjectName("editorPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.legend)
        return panel

    def set_change_tokens(self, added=(), changed=()) -> None:
        """Reicht die Aenderung eines Evolutionsknotens an den Editor durch."""
        self.editor.set_change_tokens(added, changed)
        self.legend.set_change_mode(bool(added or changed))

    def set_palette_colors(self, palette) -> None:
        self.editor.set_palette_colors(palette)
        self.legend.set_palette_colors(palette)

    # Zustand ------------------------------------------------------------------

    @property
    def current_item(self) -> ImageItem | None:
        return self._item

    def clear(self) -> None:
        self._item = None
        self._prompt_request = 0
        self._image_request = 0
        self._stats_request += 1
        self.editor.clear_prompt()
        self.set_change_tokens()
        self._pixel_size = None
        self._missing_prompt = False
        self._update_header()
        self.image_view.show_placeholder()

    def show_item(self, item: ImageItem) -> None:
        self._item = item
        self._pixel_size = None
        self._update_header()

        self._prompt_request = self._prompts.load_for_image(item.id)

        # Zuerst das bereits vorhandene Vorschaubild zeigen, dann das Original
        # nachladen. Ohne diesen Zwischenschritt bliebe die Ansicht leer,
        # solange das NAS liefert.
        cached = self._images.cached_thumbnail(item)
        if cached:
            pixmap = QPixmap()
            if pixmap.loadFromData(cached):
                self.image_view.show_pixmap(item, pixmap)

        self._image_request += 1
        request_id = self._image_request
        job = QueryJob(
            lambda: self._images.load_full_image(item),
            self._app.reporter,
            request_id=request_id,
            title="Bild wird geladen",
        )
        job.signals.result.connect(
            lambda _job_id, payload, target=item: self._on_image(payload, target)
        )
        self._app.jobs.submit(job)

    def _update_header(self) -> None:
        """Dateiname, Dateigroesse und - sobald bekannt - die Bildmasse.

        Die Groesse steht sofort zur Verfuegung, die Masse erst, wenn das
        Original geladen ist. Deshalb wird die Zeile zweimal geschrieben statt
        auf beides zu warten: eine Kopfzeile, die eine Sekunde lang leer
        bleibt, ist schlechter als eine, die sich ergaenzt.
        """
        if self._item is None:
            self.header.setText("Prompt")
            return
        teile = ["Prompt  -  " + self._item.filename]
        if self._pixel_size:
            teile.append(str(self._pixel_size[0]) + " x " + str(self._pixel_size[1]) + " px")
        if self._item.file_size:
            teile.append(_format_size(self._item.file_size))
        if self._missing_prompt:
            teile.append("kein Prompt")
        self.header.setText("   |   ".join(teile))

    # Antworten ----------------------------------------------------------------

    def _on_prompt(self, request_id: int, prompt: PromptData | None) -> None:
        if request_id != self._prompt_request:
            return  # Antwort auf eine Anfrage einer anderen Vorschau
        self.editor.show_prompt(prompt)
        self._missing_prompt = prompt is None
        self._update_header()
        if prompt is not None:
            self._load_segment_spans(prompt.id)

    def _load_segment_spans(self, prompt_id: int) -> None:
        """Holt die Segmentstatistik fuer die Hervorhebung im Hintergrund.

        Der Highlighter bekommt danach eine reine Liste; er fragt nie selbst ab.
        """
        if self._analysis is None:
            return
        self._stats_request += 1
        request_id = self._stats_request
        job = QueryJob(
            lambda: self._analysis.segment_stats_for_prompt(prompt_id),
            self._app.reporter,
            request_id=request_id,
            title="Segmentstatistik",
        )
        job.signals.result.connect(self._on_segment_stats)
        self._app.jobs.submit(job)

    def _on_segment_stats(self, _job_id: int, payload: object) -> None:
        request_id, rows = payload  # type: ignore[misc]
        if request_id != self._stats_request:
            return
        spans = [
            SegmentSpan(
                start=int(row["char_start"]),
                end=int(row["char_end"]),
                idf=float(row["idf"] or 0.0),
                doc_count=int(row["doc_count"] or 0),
                negative=bool(row["negative"]),
                kind=str(row["kind"]),
                token_id=int(row["token_id"] or 0),
            )
            for row in rows
            if int(row["char_end"]) > int(row["char_start"])
        ]
        self.editor.set_segment_spans(spans)

    def _on_image(self, payload: object, item: ImageItem) -> None:
        request_id, data = payload  # type: ignore[misc]
        if request_id != self._image_request:
            return  # inzwischen ist ein anderes Bild ausgewaehlt
        self.image_view.show_image(item, data)

        # Die Bildmasse stehen nicht in der Datenbank: der Scanner oeffnet die
        # Dateien nicht, das waere ueber SMB bei zwanzigtausend Bildern zu
        # teuer. Hier ist das Original ohnehin geladen, also kosten sie nichts.
        if data:
            probe = QPixmap()
            if probe.loadFromData(data) and not probe.isNull():
                self._pixel_size = (probe.width(), probe.height())
                self._update_header()


def _format_size(size: int) -> str:
    """Dateigroesse in der Einheit, die am wenigsten Nullen braucht."""
    if size >= 1024 * 1024:
        return format(size / (1024 * 1024), ".1f") + " MB"
    return format(size / 1024, ".0f") + " KB"
