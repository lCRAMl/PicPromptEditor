"""Vergleichsansicht - Spezifikation 01, WinMerge-artig.

    +---------+--------------------+--------------------+---------+
    | Bild A  |  Prompt A      | | |  Prompt B      | | | Bild B  |
    +---------+--------------------+--------------------+---------+
              ^ Zeilennummern      ^ Uebersichtsleiste

Die Bilder aussen, die Editoren innen. So liegen die beiden Texte, um die es
geht, unmittelbar nebeneinander; hin- und herspringen ueber ein Bild hinweg
entfaellt.

Diese Klasse **rechnet keinen Unterschied aus**. Sie bekommt ein
ComparisonResult und stellt es dar. Das ist die Trennung, um die es beim ganzen
Umbau ging: der Vorgaenger berechnete an zwei Stellen - segmentweise auf
Datenbankspannen, zeilenweise zur Anzeige - und rechnete die Ergebnisse
hinterher ineinander um. Jede Aenderung an der einen Stelle konnte die andere
verschieben.

Drei Dinge halten die beiden Seiten auf gleicher Hoehe, und alle drei sind
noetig:

1. **Ausrichtung** (im Vergleichskern): beide Seiten haben gleich viele Zeilen,
   fehlende sind Platzhalter.
2. **Hoehenausgleich** (`row_heights.py`): bei eingeschaltetem Umbruch belegt
   dieselbe logische Zeile links vier und rechts sechs Anzeigezeilen. Ein
   Abstand unter der kuerzeren gleicht das aus.
3. **Gleichlauf** (`scroll_sync.py`): danach ist er eine Wertuebernahme.

Faellt einer der drei weg, driften die Seiten beim Scrollen auseinander - erst
langsam, dann immer weiter. Genau das war der gemeldete Fehler.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.analysis.textdiff import ComparisonResult, DifferenceBlock
from app.analysis.textdiff.merge import TO_LEFT, TO_RIGHT, merge_block
from app.core.paths import same_path
from app.dto.models import ComparePair, ImageItem
from app.gui.compare.diff_pane import LEFT, RIGHT, DiffPane
from app.gui.compare.overview_bar import OverviewBar
from app.gui.compare.row_heights import equalizing_margins
from app.gui.compare.scroll_sync import ScrollLink
from app.gui.image.image_view import ImageView
from app.gui.theme import Palette
from app.jobs.scan_jobs import QueryJob

#: Wartezeit, bevor nach einer Groessenaenderung neu ausgeglichen wird. Beim
#: Ziehen am Fensterrand kommen Dutzende Ereignisse in Folge; jedes einzelne
#: auszugleichen hiesse, das Layout waehrend des Ziehens dauernd neu zu
#: berechnen. Der Ausgleich ist ohnehin erst am Ende interessant.
RESIZE_DEBOUNCE_MS = 120


class CompareTab(QWidget):
    """context_action(str, ImageItem)"""

    context_action = pyqtSignal(str, object)

    def __init__(
        self,
        application,
        pair: ComparePair,
        palette: Palette,
        *,
        font_size: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app = application
        self._images = application.image_service
        self._prompts = getattr(application, "prompt_service", None)
        self._compare = getattr(application, "compare_service", None)
        self._settings = getattr(application, "settings", None)
        self._palette = palette
        self._pair = pair
        self._result: ComparisonResult | None = None
        self._current = -1
        # Zwei getrennte Sperren, senkrecht und waagerecht. Mit einer
        # gemeinsamen verschluckt ein gerade laufender senkrechter Abgleich
        # den waagerechten, der im selben Ereignis ausgeloest wird - beim
        # Anspringen eines Unterschieds bewegen sich beide Richtungen.
        self._scroll = ScrollLink()
        self._scroll_h = ScrollLink()
        self._dirty: set[str] = set()
        #: Seite -> zuletzt gewaehlter Speicherort. Nur fuer die Statuszeile;
        #: er wird bewusst NICHT als neuer Speicherort gemerkt, damit auch das
        #: zweite Speichern wieder ausdruecklich gefragt wird.
        self._saved_as: dict[str, str] = {}

        self.key = compare_key(pair.left_image.id, pair.right_image.id)
        self.title = pair.left_image.filename + "  <>  " + pair.right_image.filename

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(RESIZE_DEBOUNCE_MS)
        self._resize_timer.timeout.connect(self._equalize_heights)

        self._build(font_size)
        self.show_pair(pair)

    # Aufbau -------------------------------------------------------------------

    def _build(self, font_size: int) -> None:
        self._left_view = ImageView()
        self._right_view = ImageView()
        for view in (self._left_view, self._right_view):
            view.context_action.connect(self.context_action.emit)
            view.image_clicked.connect(lambda item: self.context_action.emit("open", item))

        self._left_pane = DiffPane(LEFT, self._palette, font_size=font_size)
        self._right_pane = DiffPane(RIGHT, self._palette, font_size=font_size)
        self._left_pane.context_action.connect(self._on_pane_action)
        self._right_pane.context_action.connect(self._on_pane_action)

        self._left_bar = OverviewBar(self._palette)
        self._right_bar = OverviewBar(self._palette)
        self._left_bar.row_clicked.connect(self._jump_to_row)
        self._right_bar.row_clicked.connect(self._jump_to_row)

        self._status = QLabel("")
        self._status.setContentsMargins(6, 4, 6, 4)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._left_view)
        split.addWidget(self._panel(self._left_pane, self._left_bar))
        split.addWidget(self._panel(self._right_pane, self._right_bar))
        split.addWidget(self._right_view)
        for index, weight in enumerate((2, 3, 3, 2)):
            split.setStretchFactor(index, weight)
        split.setSizes([320, 430, 430, 320])
        self._splitter = split
        split.splitterMoved.connect(lambda *_a: self._resize_timer.start())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._status)
        layout.addWidget(split, 1)

        self._connect_scrolling()

    def _panel(self, pane: DiffPane, bar: OverviewBar) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        header = QLabel("")
        header.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(header)
        setattr(pane, "_header_label", header)

        zeile = QHBoxLayout()
        zeile.setContentsMargins(0, 0, 0, 0)
        zeile.setSpacing(2)
        zeile.addWidget(pane, 1)
        zeile.addWidget(bar)
        layout.addLayout(zeile, 1)
        return panel

    def _build_toolbar(self) -> QToolBar:
        bar = QToolBar()
        bar.setMovable(False)

        self._act_prev = QAction("Vorheriger", self)
        self._act_prev.setShortcut(QKeySequence("Alt+Up"))
        self._act_prev.setToolTip("Zum vorherigen Unterschied (Alt+Auf)")
        self._act_prev.triggered.connect(self.goto_previous_difference)

        self._act_next = QAction("Naechster", self)
        self._act_next.setShortcut(QKeySequence("Alt+Down"))
        self._act_next.setToolTip("Zum naechsten Unterschied (Alt+Ab)")
        self._act_next.triggered.connect(self.goto_next_difference)

        bar.addAction(self._act_prev)
        bar.addAction(self._act_next)
        bar.addSeparator()

        self._act_to_right = QAction("A -> B", self)
        self._act_to_right.setToolTip(
            "Diesen Unterschied von links nach rechts uebernehmen"
        )
        self._act_to_right.triggered.connect(lambda: self.merge_current(TO_RIGHT))

        self._act_to_left = QAction("B -> A", self)
        self._act_to_left.setToolTip(
            "Diesen Unterschied von rechts nach links uebernehmen"
        )
        self._act_to_left.triggered.connect(lambda: self.merge_current(TO_LEFT))

        bar.addAction(self._act_to_right)
        bar.addAction(self._act_to_left)
        bar.addSeparator()

        self._act_save_left = QAction("A speichern unter...", self)
        self._act_save_left.triggered.connect(lambda: self.save_side(LEFT))
        self._act_save_right = QAction("B speichern unter...", self)
        self._act_save_right.triggered.connect(lambda: self.save_side(RIGHT))
        bar.addAction(self._act_save_left)
        bar.addAction(self._act_save_right)
        bar.addSeparator()

        self._act_wrap = QAction("Umbruch", self)
        self._act_wrap.setCheckable(True)
        self._act_wrap.setChecked(self._setting_bool("compare.word_wrap", True))
        self._act_wrap.setToolTip(
            "Zeilen umbrechen. Aus entspricht WinMerge und verlangt"
            " waagerechtes Scrollen."
        )
        self._act_wrap.toggled.connect(self.set_word_wrap)

        self._act_words = QAction("Wortfeinheit", self)
        self._act_words.setCheckable(True)
        self._act_words.setChecked(True)
        self._act_words.setToolTip(
            "Innerhalb geaenderter Zeilen die einzelnen Woerter hervorheben"
        )
        self._act_words.toggled.connect(self._set_word_emphasis)

        bar.addAction(self._act_wrap)
        bar.addAction(self._act_words)

        self._update_actions()
        return bar

    def _setting_bool(self, key: str, fallback: bool) -> bool:
        if self._settings is None:
            return fallback
        try:
            return self._settings.get_bool(key)
        except Exception:  # noqa: BLE001 - eine fehlende Einstellung ist kein Grund,
            return fallback  # die Ansicht nicht zu oeffnen

    # Gleichlauf ---------------------------------------------------------------

    def _connect_scrolling(self) -> None:
        links = self._left_pane.verticalScrollBar()
        rechts = self._right_pane.verticalScrollBar()
        links.valueChanged.connect(lambda v: self._sync_vertical(links, rechts, v))
        rechts.valueChanged.connect(lambda v: self._sync_vertical(rechts, links, v))

        # Waagerecht wird ebenfalls gekoppelt. Bei ausgeschaltetem Umbruch ist
        # das der Regelfall: die Spalten stehen dann untereinander, und eine
        # Seite allein zu verschieben zerstoert genau die Zuordnung, derentwegen
        # man die Ansicht geoeffnet hat.
        links_h = self._left_pane.horizontalScrollBar()
        rechts_h = self._right_pane.horizontalScrollBar()
        links_h.valueChanged.connect(lambda v: self._sync_horizontal(rechts_h, v))
        rechts_h.valueChanged.connect(lambda v: self._sync_horizontal(links_h, v))

    def _sync_vertical(self, source, target, value: int) -> None:
        if not self._scroll.transfer(source, target, value):
            return
        self._update_overview_ranges()

    def _sync_horizontal(self, target, value: int) -> None:
        """Waagerecht wird ABSOLUT uebernommen, nicht anteilig.

        Der senkrechte Gleichlauf darf anteilig rechnen, solange die Hoehen
        noch nicht ausgeglichen sind. Waagerecht waere das falsch: dort geht es
        um Spalten, und Spalte 40 links ist Spalte 40 rechts - unabhaengig
        davon, wie weit die laengste Zeile der jeweiligen Seite reicht.
        """
        with self._scroll_h.held() as frei:
            if frei:
                target.setValue(min(target.maximum(), max(target.minimum(), value)))

    def _update_overview_ranges(self) -> None:
        for pane, bar in ((self._left_pane, self._left_bar), (self._right_pane, self._right_bar)):
            balken = pane.verticalScrollBar()
            gesamt = balken.maximum() + balken.pageStep()
            if gesamt <= 0:
                bar.set_visible_range(0.0, 1.0)
                continue
            bar.set_visible_range(
                balken.value() / gesamt, (balken.value() + balken.pageStep()) / gesamt
            )

    # Hoehenausgleich ----------------------------------------------------------

    def _equalize_heights(self) -> None:
        """Gleicht die Zeilenhoehen beider Seiten aneinander an.

        Erst beide Seiten ohne Abstaende messen, dann die Differenzen setzen.
        Zwei Durchlaeufe waeren nicht noetig und auch nicht harmlos: ein
        Abstand veraendert den Zeilenumbruch nicht, also konvergiert ein
        einziger Durchgang - ein zweiter wuerde nur auf bereits ausgeglichenen
        Hoehen erneut Differenzen von null berechnen.
        """
        if self._result is None:
            return

        links = self._left_pane.base_heights()
        rechts = self._right_pane.base_heights()
        if not any(links) and not any(rechts):
            # Vor dem ersten Layout gibt es nichts zu messen. Ein Ausgleich auf
            # lauter Nullen setzt ueberall den Abstand null und gilt danach als
            # erledigt - die Seiten blieben ungleich hoch, ohne dass etwas
            # darauf hinweist.
            #
            # Hier NICHT den Zeitgeber neu starten: das waere eine Schleife,
            # die bis zum Programmende alle 120 ms laeuft, falls der Tab nie
            # sichtbar wird. Der Weg zurueck fuehrt ueber showEvent.
            return

        links_rand, rechts_rand = equalizing_margins(links, rechts)
        self._left_pane.apply_row_margins(links_rand)
        self._right_pane.apply_row_margins(rechts_rand)
        self._update_overview_ranges()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().resizeEvent(event)
        self._resize_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt)
        """Der zweite Weg zum Hoehenausgleich.

        Ein Tab, der im Hintergrund aufgebaut wird, hat beim Aufbau kein
        Layout - dort ist nichts zu messen. Auf resizeEvent allein ist kein
        Verlass: hat das Widget seine endgueltige Groesse schon vor dem
        Anzeigen bekommen, kommt danach kein Groessenereignis mehr, und der
        Ausgleich unterbliebe fuer immer.
        """
        super().showEvent(event)
        self._resize_timer.start()

    def set_word_wrap(self, enabled: bool) -> None:
        self._left_pane.set_wrap(enabled)
        self._right_pane.set_wrap(enabled)
        if self._settings is not None:
            try:
                self._settings.set("compare.word_wrap", bool(enabled))
            except Exception:  # noqa: BLE001
                pass
        self._resize_timer.start()

    def _set_word_emphasis(self, enabled: bool) -> None:
        self._left_pane.highlighter.set_enabled(enabled)
        self._right_pane.highlighter.set_enabled(enabled)

    # Inhalt -------------------------------------------------------------------

    def show_pair(self, pair: ComparePair) -> None:
        self._pair = pair
        self._dirty.clear()
        result = pair.comparison
        if result is None and self._compare is not None:
            links = pair.left_prompt.text if pair.left_prompt else ""
            rechts = pair.right_prompt.text if pair.right_prompt else ""
            result = self._compare.compare_now_texts(links, rechts)

        getattr(self._left_pane, "_header_label").setText(
            "A  -  " + pair.left_image.filename
        )
        getattr(self._right_pane, "_header_label").setText(
            "B  -  " + pair.right_image.filename
        )

        self._apply_result(result)
        self._load_image(self._left_view, pair.left_image)
        self._load_image(self._right_view, pair.right_image)

    def _apply_result(self, result: ComparisonResult | None) -> None:
        """Uebernimmt ein Vergleichsmodell in beide Seiten.

        Der Aufruf des Hoehenausgleichs laeuft ueber den Zeitgeber und nicht
        unmittelbar: Qt hat den Text zu diesem Zeitpunkt noch nicht umgebrochen,
        und eine Messung vor dem Layout liefert Hoehen, die gleich wieder
        veraltet sind.
        """
        self._result = result
        self._current = -1

        self._left_pane.show_result(result)
        self._right_pane.show_result(result)
        self._left_bar.set_result(result)
        self._right_bar.set_result(result)

        self._left_pane.set_wrap(self._act_wrap.isChecked())
        self._right_pane.set_wrap(self._act_wrap.isChecked())

        self._status.setText(self._describe())
        self._update_actions()
        self._resize_timer.start()

    def _describe(self) -> str:
        if self._result is None:
            return "Kein Vergleich moeglich - zu einem Bild fehlt der Prompt"

        teile = [self._result.summary()]
        if self._current >= 0:
            block = self._result.real_differences[self._current]
            teile.append(
                "Unterschied "
                + str(self._current + 1)
                + " von "
                + str(self._result.difference_count)
                + "  ("
                + block.describe()
                + ")"
            )
        if self._dirty:
            seiten = ", ".join("A" if s == LEFT else "B" for s in sorted(self._dirty))
            teile.append("Ungespeichert: " + seiten)
        for seite in sorted(self._saved_as):
            teile.append(
                ("A" if seite == LEFT else "B")
                + " gespeichert unter "
                + Path(self._saved_as[seite]).name
            )

        diff = getattr(self._pair, "diff", None)
        if diff is not None and getattr(diff, "stats", None) is not None:
            teile.append(
                "Uebereinstimmung "
                + format(diff.stats.agreement * 100, ".0f")
                + " % der Saetze"
            )
        return "   |   ".join(teile)

    def _update_actions(self) -> None:
        hat = self._result is not None and self._result.difference_count > 0
        self._act_prev.setEnabled(hat)
        self._act_next.setEnabled(hat)

        auf_block = hat and self._current >= 0
        self._act_to_right.setEnabled(auf_block)
        self._act_to_left.setEnabled(auf_block)

        darf = self._prompts is not None and getattr(self._prompts, "can_write", False)
        self._act_save_left.setEnabled(bool(darf and LEFT in self._dirty))
        self._act_save_right.setEnabled(bool(darf and RIGHT in self._dirty))

    # Navigation ---------------------------------------------------------------

    def goto_next_difference(self) -> None:
        if self._result is None or not self._result.difference_count:
            return
        self._goto_index((self._current + 1) % self._result.difference_count)

    def goto_previous_difference(self) -> None:
        if self._result is None or not self._result.difference_count:
            return
        self._goto_index((self._current - 1) % self._result.difference_count)

    def _goto_index(self, index: int) -> None:
        if self._result is None:
            return
        bloecke = self._result.real_differences
        if not 0 <= index < len(bloecke):
            return
        self._current = index
        block = bloecke[index]
        self._highlight_current(block)
        self._left_pane.scroll_to_row(block.first_row)
        self._status.setText(self._describe())
        self._update_actions()

    def _highlight_current(self, block: DifferenceBlock | None) -> None:
        nummer = block.index if block is not None else None
        self._left_pane.set_current_block(nummer)
        self._right_pane.set_current_block(nummer)
        self._left_bar.set_current_block(nummer)
        self._right_bar.set_current_block(nummer)

    def _jump_to_row(self, display_index: int) -> None:
        """Sprung aus der Uebersichtsleiste.

        Liegt die angeklickte Stelle in einem Unterschied, wird dieser gleich
        zum aktuellen. Sonst waere der naechste Klick auf "A -> B" wirkungslos,
        obwohl der Benutzer sichtbar auf einem Unterschied steht.
        """
        self._left_pane.scroll_to_row(display_index)
        if self._result is None:
            return
        block = self._result.block_at_row(display_index)
        if block is not None and block.is_real:
            bloecke = self._result.real_differences
            if block in bloecke:
                self._current = bloecke.index(block)
                self._highlight_current(block)
                self._status.setText(self._describe())
                self._update_actions()

    def _on_pane_action(self, action: str) -> None:
        if action == "next_difference":
            self.goto_next_difference()
        elif action == "previous_difference":
            self.goto_previous_difference()
        elif action == "copy_left_to_right":
            self.merge_current(TO_RIGHT)
        elif action == "copy_right_to_left":
            self.merge_current(TO_LEFT)

    # Uebernahme ---------------------------------------------------------------

    def merge_current(self, direction: str) -> None:
        """Uebertraegt den aktuellen Unterschied auf die andere Seite.

        Gearbeitet wird auf dem QUELLMODELL. Die Platzhalter der Anzeige
        gehen dabei nicht mit - `merge_block` uebersetzt sie in ein Einfuegen
        oder Loeschen. Wuerde statt dessen der angezeigte Text kopiert,
        entstuenden auf der Zielseite Leerzeilen, die niemand geschrieben hat,
        und beim naechsten Vergleich waeren sie echte Unterschiede.

        Anschliessend wird der Vergleich neu gerechnet. Das ist billiger, als
        es klingt, und schliesst eine ganze Fehlerklasse aus: es gibt keinen
        Zwischenzustand, der zwischen zwei Uebernahmen veralten koennte.
        """
        if self._result is None or self._current < 0:
            return
        block = self._result.real_differences[self._current]
        ergebnis = merge_block(self._result, block, direction)
        if ergebnis.is_noop:
            return

        self._dirty.add(ergebnis.changed_side)
        neu = (
            self._compare.compare_now_texts(ergebnis.left_text, ergebnis.right_text)
            if self._compare is not None
            else None
        )
        vorher = self._current
        self._apply_result(neu)

        # Auf dem naechsten Unterschied stehenbleiben statt an den Anfang zu
        # springen. Wer einen Block uebernimmt, will meistens den naechsten
        # ansehen - nicht wieder oben anfangen.
        if neu is not None and neu.difference_count:
            self._goto_index(min(vorher, neu.difference_count - 1))

    def current_texts(self) -> tuple[str, str]:
        """Die Quelltexte beider Seiten im aktuellen Stand."""
        if self._result is None:
            return "", ""
        return self._result.left_source_text(), self._result.right_source_text()

    # Speichern ----------------------------------------------------------------

    def save_side(self, side: str) -> None:
        """Speichert eine Seite in eine NEUE, vom Benutzer gewaehlte Datei.

        **Eine Originaldatei wird dabei niemals ueberschrieben.** Das ist die
        Regel, und sie steht hier nicht als Rueckfrage, sondern als Sperre: der
        PromptService kennt gar keine Methode, die in `prompt.full_path`
        schreibt, und die beiden Originalpfade werden zusaetzlich ausdruecklich
        abgelehnt.

        Warum beide? Weil auch das Gegenstueck ein Original ist. Wer den
        zusammengefuehrten Text von A ueber die Datei von B speichert, hat
        genauso eine Vorlage vernichtet - nur eine andere als die, an die man
        beim Wort "Original" zuerst denkt.
        """
        if self._result is None or self._prompts is None:
            return
        prompt = self._pair.left_prompt if side == LEFT else self._pair.right_prompt
        if prompt is None:
            return
        if not getattr(self._prompts, "can_write", False):
            QMessageBox.information(
                self,
                "Speichern abgeschaltet",
                "Das Speichern ist in den Einstellungen abgeschaltet"
                " (compare.allow_export).",
            )
            return

        ziel = self._ask_for_target(prompt)
        if not ziel:
            return

        text = (
            self._result.left_source_text()
            if side == LEFT
            else self._result.right_source_text()
        )
        self._pending_save = (side, prompt, ziel)
        try:
            self._prompts.save_finished.connect(self._on_save_finished)
        except (TypeError, RuntimeError):
            pass
        # Der Dateidialog hat das Ueberschreiben einer vorhandenen ZIELdatei
        # bereits erfragt; eine zweite Rueckfrage waere Schikane.
        self._prompts.export_async(prompt, text, ziel, overwrite=True)

    def _ask_for_target(self, prompt) -> str:
        """Fragt nach dem Speicherort und lehnt die Originale ab.

        Vorgeschlagen wird der Ordner des Originals mit einem abgewandelten
        Namen. Der Ordner, weil ein Prompt neben sein Bild gehoert und die
        neue Datei dort am ehesten gebraucht wird; der abgewandelte Name, damit
        ein unbedachtes Bestaetigen nichts ueberschreiben kann.
        """
        vorschlag = self._suggested_name(prompt)
        while True:
            gewaehlt, _filter = QFileDialog.getSaveFileName(
                self,
                "Zusammengefuehrten Prompt speichern unter",
                vorschlag,
                "Textdateien (*.txt);;Alle Dateien (*)",
            )
            if not gewaehlt:
                return ""
            if not self._is_original(gewaehlt):
                return gewaehlt

            QMessageBox.warning(
                self,
                "Originaldatei",
                "Diese Datei ist eine der beiden verglichenen Vorlagen und"
                " wird nicht ueberschrieben.\n\n"
                + gewaehlt
                + "\n\nBitte einen anderen Namen waehlen.",
            )
            vorschlag = gewaehlt

    def _is_original(self, path: str) -> bool:
        """Ob dieser Pfad auf eine der beiden verglichenen Vorlagen zeigt."""
        for prompt in (self._pair.left_prompt, self._pair.right_prompt):
            if prompt is not None and same_path(path, prompt.full_path):
                return True
        return False

    @staticmethod
    def _suggested_name(prompt) -> str:
        quelle = Path(prompt.full_path)
        return str(quelle.with_name(quelle.stem + "_zusammengefuehrt" + quelle.suffix))

    def _on_save_finished(self, prompt_id: int, status: str, message: str) -> None:
        pending = getattr(self, "_pending_save", None)
        if pending is None or pending[1].id != prompt_id:
            return  # die Meldung gehoert zu einem anderen Tab
        side, _prompt, ziel = pending
        self._pending_save = None

        if status != "saved":
            QMessageBox.warning(self, "Nicht gespeichert", message)
            return

        self._dirty.discard(side)
        self._saved_as[side] = ziel
        self._status.setText(self._describe())
        self._update_actions()
        QMessageBox.information(
            self,
            "Gespeichert",
            "Gespeichert unter\n\n"
            + ziel
            + "\n\nDie verglichenen Vorlagen sind unveraendert geblieben.",
        )

    # Bilder -------------------------------------------------------------------

    def _load_image(self, view: ImageView, item: ImageItem) -> None:
        cached = self._images.cached_thumbnail(item)
        if cached:
            pixmap = QPixmap()
            if pixmap.loadFromData(cached):
                view.show_pixmap(item, pixmap)

        job = QueryJob(
            lambda: self._images.load_full_image(item),
            self._app.reporter,
            title="Bild wird geladen",
        )
        job.signals.result.connect(
            lambda _id, payload, target=item, widget=view: widget.show_image(
                target, payload[1]
            )
        )
        self._app.jobs.submit(job)

    # Schnittstelle fuer den TabManager ----------------------------------------

    def current_item(self) -> ImageItem:
        return self._pair.left_image

    def set_palette_colors(self, palette: Palette) -> None:
        self._palette = palette
        self._left_pane.set_palette_colors(palette)
        self._right_pane.set_palette_colors(palette)
        self._left_bar.set_palette_colors(palette)
        self._right_bar.set_palette_colors(palette)

    def set_tile_size(self, size: int) -> None:
        """Kein Kachelraster in diesem Tab."""

    def has_unsaved_changes(self) -> bool:
        return bool(self._dirty)


def compare_key(left_image_id: int, right_image_id: int) -> str:
    """Schluessel des Vergleichstabs.

    Die Reihenfolge geht ein: A gegen B ist eine andere Ansicht als B gegen A,
    weil "nur links" und "nur rechts" die Seiten tauschen.
    """
    return "compare:" + str(left_image_id) + ":" + str(right_image_id)
