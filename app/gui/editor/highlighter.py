"""Hervorhebung der Promptsegmente im Editor.

Spezifikation 01 verlangt "Hervorhebung wichtiger Promptbestandteile". Wichtig
heisst hier: unterscheidungskraeftig. Ein Satz, der in jedem Prompt der
Bibliothek steht, ist Textbaustein; einer, der nur hier vorkommt, macht dieses
Bild aus. Genau das misst idf.

    idf niedrig  -> haeufig, Textbaustein          -> zurueckhaltend
    idf hoch     -> selten, kennzeichnend          -> hervorgehoben
    negativ      -> eigener Farbton, unabhaengig von der Haeufigkeit

Der Highlighter fasst NIE eine Datenbank an. Er bekommt eine fertige Liste von
Spannen in den Speicher gelegt; highlightBlock ist damit eine reine
Listenoperation. Eine Abfrage je Textblock waere bei einem vierhundert Zeilen
langen Prompt vierhundert Abfragen pro Neuzeichnung.

QSyntaxHighlighter arbeitet blockweise, die Spannen sind dokumentweit. Die
Umrechnung erfolgt ueber die Position des aktuellen Blocks im Dokument.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from app.gui.theme import Palette


@dataclass(frozen=True, slots=True)
class SegmentSpan:
    start: int
    end: int
    idf: float
    doc_count: int
    negative: bool
    kind: str
    #: Ohne die Id laesst sich ein Segment nicht mit dem Baum abgleichen: die
    #: Evolution rechnet auf Token-Ids, die Anzeige auf Zeichenspannen.
    token_id: int = 0


class SegmentHighlighter(QSyntaxHighlighter):
    def __init__(
        self,
        document: QTextDocument,
        palette: Palette,
        min_idf: float = 1.5,
    ) -> None:
        super().__init__(document)
        self._palette = palette
        self._min_idf = min_idf
        self._spans: list[SegmentSpan] = []
        self._enabled = True
        #: Token-Ids, die gegenueber dem Elternknoten neu bzw. geaendert sind.
        self._added: frozenset[int] = frozenset()
        self._changed: frozenset[int] = frozenset()

    # Steuerung ----------------------------------------------------------------

    def set_spans(self, spans: list[SegmentSpan]) -> None:
        """Uebernimmt die Spannen des aktuellen Prompts und zeichnet neu."""
        self._spans = sorted(spans, key=lambda s: s.start)
        self.rehighlight()

    def set_change_tokens(self, added, changed) -> None:
        """Hebt hervor, was sich gegenueber dem Elternknoten geaendert hat.

        Der Evolutionsbaum sagt in der Spalte "Aenderung" zwar "2 ergaenzt",
        zeigte aber nicht WO. Genau das ist die Frage, derentwegen man den Baum
        aufmacht.

        Entfernte Segmente lassen sich hier nicht zeigen: sie stehen im
        Elternprompt, nicht in diesem. Dafuer ist die Vergleichsansicht da.
        """
        self._added = frozenset(added or ())
        self._changed = frozenset(changed or ())
        self.rehighlight()

    def clear_spans(self) -> None:
        if self._spans:
            self._spans = []
            self.rehighlight()

    def set_palette_colors(self, palette: Palette) -> None:
        self._palette = palette
        self.rehighlight()

    def set_min_idf(self, value: float) -> None:
        self._min_idf = value
        self.rehighlight()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.rehighlight()

    def span_at(self, position: int) -> SegmentSpan | None:
        """Segment an einer Dokumentposition, etwa unter dem Mauszeiger."""
        for span in self._spans:
            if span.start <= position < span.end:
                return span
        return None

    @property
    def span_count(self) -> int:
        return len(self._spans)

    # Zeichnen -----------------------------------------------------------------

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt)
        if not self._enabled or not self._spans:
            return

        block_start = self.currentBlock().position()
        block_end = block_start + len(text)

        for span in self._spans:
            if span.end <= block_start:
                continue
            if span.start >= block_end:
                break  # Spannen sind sortiert, weiter hinten kommt nichts mehr
            start = max(span.start, block_start) - block_start
            end = min(span.end, block_end) - block_start
            if end > start:
                self.setFormat(start, end - start, self._format_for(span))

    def _format_for(self, span: SegmentSpan) -> QTextCharFormat:
        theme = self._palette
        fmt = QTextCharFormat()

        # Die Aenderung gegenueber dem Elternknoten hat Vorrang vor der
        # Haeufigkeitsfaerbung: wer einen Baum ansieht, will wissen, was NEU
        # ist - nicht, wie selten es in der Bibliothek vorkommt.
        if span.token_id and span.token_id in self._added:
            fmt.setBackground(theme.diff_added)
            fmt.setFontWeight(600)
            return fmt
        if span.token_id and span.token_id in self._changed:
            fmt.setBackground(theme.diff_changed)
            fmt.setFontWeight(600)
            return fmt

        if span.negative:
            fmt.setBackground(theme.segment_negative)
            return fmt

        if span.idf >= self._min_idf:
            fmt.setBackground(theme.segment_rare)
            fmt.setFontWeight(600)
        elif span.doc_count > 1:
            fmt.setBackground(theme.segment_common)
            fmt.setForeground(QColor(theme.text_muted))
        return fmt
