"""Hauptfenster.

Aufbau nach Spezifikation 01: ein festes, nicht schliessbares Tab [Bibliothek]

    +------------+----------------------+-------------+
    | Ordnerbaum |    Prompt Editor     | Bildansicht |
    +------------+----------------------+-------------+
    |             Thumbnail Explorer                  |
    +-------------------------------------------------+

Dazu dynamische Tabs fuer Suchergebnisse und Bilddetails; Vergleich (M5) und
Evolution (M6) reihen sich spaeter in dieselbe Verwaltung ein.

Das Fenster enthaelt keine Fachlogik. Es verbindet Widgets mit Services und
uebersetzt Benutzeraktionen in Serviceaufrufe - kein SQL, kein Dateizugriff,
keine Analyse.
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QSettings, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QCloseEvent, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app import version_label
from app.application import Application
from app.dto.models import FolderNode, ImageItem, ResultSet, ScanStats
from app.gui.common.preview import PreviewController
from app.gui.common.search_bar import SCOPE_FOLDER, SearchBar
from app.gui.library.explorer_panel import ExplorerPanel
from app.gui.library.folder_tree import FolderTree
from app.gui.status.problem_dialog import ProblemDialog
from app.gui.status.status_bar import StatusBar
from app.gui.tabs.detail_tab import DetailTab
from app.gui.tabs.result_tab import ResultTab
from app.gui.tabs.tab_manager import TabManager
from app.gui.theme import ThemeManager

LIBRARY_TAB = 0

#: Ruhezeit, bevor die Fensterbreite eingerastet wird.
SNAP_DELAY_MS = 250


def _folder_label(node: FolderNode) -> str:
    """Ordnername mit dem des Elternordners davor.

    In dieser Bibliothek heissen die interessanten Ordner alle "ai" - ein Tab
    mit diesem Titel waere von jedem anderen ununterscheidbar. Angezeigt wird
    deshalb "Person/ai".

    Ein Laufwerksbuchstabe zaehlt nicht als Elternordner: aus "Z:/Instagram"
    soll "Instagram" werden und nicht "Z:/Instagram".
    """
    from pathlib import PurePath

    teile = PurePath(node.path.replace(chr(92), "/")).parts
    if len(teile) < 2:
        return node.name
    eltern = teile[-2].strip("/").strip(chr(92))
    if not eltern or eltern.endswith(":"):
        return node.name
    return eltern + "/" + node.name


class MainWindow(QMainWindow):
    def __init__(self, application: Application) -> None:
        super().__init__()
        self._app = application
        self._settings = application.settings
        self._images = application.image_service
        self._prompts = application.prompt_service
        self._index = application.index_service
        self._system = application.system_service
        self._selection = application.selection_service
        self._analysis = application.analysis_service
        self._compare = application.compare_service
        self._evolution = application.evolution_service
        self._watch = application.watch_service
        self._nas_warned = False
        self._snapping = False
        #: Feuert erst, wenn die Groessenaenderung zur Ruhe gekommen ist.
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.setInterval(SNAP_DELAY_MS)
        self._snap_timer.timeout.connect(self._snap_width)

        self._theme = ThemeManager(self._settings.get_str("ui.theme"), self._settings)
        self._current_folder: FolderNode | None = None
        self._pending_search: tuple[str, str] | None = None

        self.setWindowTitle(version_label())
        self.resize(1600, 1000)

        self._build_ui()
        self._build_menu()
        self._connect_services()
        self._restore_geometry()

        # Zweites Netz: closeEvent laeuft nur, wenn das FENSTER geschlossen
        # wird. Endet die Anwendung anders - ueber die Taskleiste, ein Signal
        # im Terminal -, waere die Groesse sonst verloren. aboutToQuit greift
        # auch dann.
        instance = QApplication.instance()
        if instance is not None:
            instance.aboutToQuit.connect(self._save_geometry)

        self._apply_window_icon()
        QTimer.singleShot(0, self._apply_size_increment)

        self._system.start()
        self._watch.start()
        QTimer.singleShot(0, self._initial_load)

    # Aufbau -------------------------------------------------------------------

    def _build_ui(self) -> None:
        palette = self._theme.palette
        tile = self._settings.get_int("thumbnails.default_size")
        font_size = self._settings.get_int("ui.editor_font_size")

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.setMovable(True)
        # Ein benannter Behaelter um die Tableiste - er traegt den Rahmen des
        # Programms. Direkt am Tab-Widget geht es nicht: es laeuft im
        # Dokumentmodus, und dort zeichnet Qt grundsaetzlich keinen Rahmen um
        # den Inhaltsbereich. Genau deshalb hatte das Fenster keine Aussenlinie.
        rahmen = QWidget()
        rahmen.setObjectName("fensterRahmen")
        rahmen_layout = QVBoxLayout(rahmen)
        rahmen_layout.setContentsMargins(1, 1, 1, 1)
        rahmen_layout.setSpacing(0)
        rahmen_layout.addWidget(self._tabs)
        self.setCentralWidget(rahmen)

        # --- Bibliothekstab ---------------------------------------------------
        self._folder_tree = FolderTree()
        self._folder_tree.set_show_counts(self._settings.get_bool("ui.show_folder_counts"))

        self._preview = PreviewController(self._app, font_size, palette=palette, parent=self)
        self._explorer = ExplorerPanel(
            self._app, palette, self._selection, tile, show_origin=False
        )
        self._search = SearchBar(self._settings.get_int("search.debounce_ms"))
        self._search.set_folder_scope_enabled(False)

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self._folder_tree)
        top.addWidget(self._preview.editor_panel())
        top.addWidget(self._preview.image_view)
        top.setStretchFactor(0, 1)
        top.setStretchFactor(1, 3)
        top.setStretchFactor(2, 2)
        top.setSizes([260, 700, 500])
        self._top_splitter = top

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(2)
        bottom_layout.addWidget(self._search)
        bottom_layout.addWidget(self._explorer, 1)

        library = QSplitter(Qt.Orientation.Vertical)
        library.addWidget(top)
        library.addWidget(bottom)
        library.setStretchFactor(0, 3)
        library.setStretchFactor(1, 2)
        library.setSizes([600, 400])
        self._main_splitter = library

        self._tabs.addTab(library, "Bibliothek")
        bar = self._tabs.tabBar()
        bar.setTabButton(LIBRARY_TAB, bar.ButtonPosition.RightSide, None)

        self._tab_manager = TabManager(self._tabs, self)
        # Rueckt das Schliesskreuz an den Tabrand. Qt kennt nur zwei Zustaende:
        # ohne Formatvorlage haelt es starr dreizehn Bildpunkte frei - genug
        # Platz fuer ein zweites Symbol, was wie ein Versehen aussieht -, mit
        # Formatvorlage einen. Ein eigener Knopf koennte jeden Wert dazwischen,
        # brachte aber die Anwendung beim Anklicken zum Absturz; Qts eigener
        # Knopf laeuft ueber tabCloseRequested und ist erprobt.
        # Das Innenmass der Tabs steht im globalen Stylesheet
        # (app/gui/theme.py, style_sheet). Ein zweites Stylesheet am
        # Widget haette dort die Akzentlinie ueber dem aktiven Tab
        # verdraengt - zwei Regelsaetze fuer dieselben Tabs sind eine
        # Falle, die erst beim Themewechsel auffaellt.

        self._status = StatusBar(palette, self)
        self.setStatusBar(self._status)
        self._status.problems_requested.connect(self._show_problems)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&Datei")
        add_root = QAction("Bibliotheksordner hinzufuegen...", self)
        add_root.triggered.connect(self._add_root)
        file_menu.addAction(add_root)
        file_menu.addSeparator()
        settings_action = QAction("Einstellungen...", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._show_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        quit_action = QAction("Beenden", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        index_menu = self.menuBar().addMenu("&Bibliothek")
        scan = QAction("Abgleichen", self)
        scan.setShortcut("F5")
        scan.triggered.connect(self._index.start_scan)
        index_menu.addAction(scan)
        reindex = QAction("Neu indizieren", self)
        reindex.triggered.connect(self._confirm_reindex)
        index_menu.addAction(reindex)
        cancel = QAction("Abgleich abbrechen", self)
        cancel.triggered.connect(self._index.cancel_scan)
        cancel.setEnabled(False)
        index_menu.addAction(cancel)
        # Ein laufender Abgleich soll im Menue ablesbar sein: Abbrechen ist nur
        # dann waehlbar, Starten nur sonst. Der Dienst weist einen Doppelstart
        # zwar ohnehin ab, aber ein Menuepunkt, der nichts tut, ist eine
        # schlechte Auskunft.
        self._scan_actions = (scan, reindex)
        self._cancel_action = cancel
        index_menu.addSeparator()
        probe = QAction("NAS-Erreichbarkeit pruefen", self)
        probe.triggered.connect(lambda: self._watch.probe_now())
        index_menu.addAction(probe)
        index_menu.addSeparator()
        newest = QAction("Neueste Prompt-Paare in neuem Tab", self)
        newest.setShortcut("Ctrl+N")
        newest.triggered.connect(self._open_newest_pairs)
        index_menu.addAction(newest)
        templates = QAction("Neueste Vorlagen in neuem Tab", self)
        templates.setShortcut("Ctrl+Shift+N")
        templates.triggered.connect(self._open_newest_templates)
        index_menu.addAction(templates)
        index_menu.addSeparator()
        history = QAction("Abgleichprotokoll...", self)
        history.triggered.connect(self._show_scan_history)
        index_menu.addAction(history)

        search_menu = self.menuBar().addMenu("&Suchen")
        focus_search = QAction("Suchfeld fokussieren", self)
        focus_search.setShortcut(QKeySequence.StandardKey.Find)
        focus_search.triggered.connect(self._search.focus)
        search_menu.addAction(focus_search)
        open_tab = QAction("Ergebnis in neuem Tab oeffnen", self)
        open_tab.setShortcut("Ctrl+Return")
        open_tab.triggered.connect(self._open_search_tab)
        search_menu.addAction(open_tab)

        view_menu = self.menuBar().addMenu("&Ansicht")
        for label, size in (("Klein (128)", 128), ("Mittel (256)", 256), ("Gross (512)", 512)):
            action = QAction(label, self)
            action.triggered.connect(lambda _checked=False, s=size: self._set_tile_size(s))
            view_menu.addAction(action)
        view_menu.addSeparator()
        close_tabs = QAction("Alle Zusatztabs schliessen", self)
        close_tabs.triggered.connect(self._tab_manager.close_all)
        view_menu.addAction(close_tabs)
        view_menu.addSeparator()
        problems = QAction("Probleme anzeigen", self)
        problems.triggered.connect(self._show_problems)
        view_menu.addAction(problems)

        analysis_menu = self.menuBar().addMenu("&Analyse")
        parse = QAction("Prompts analysieren", self)
        parse.triggered.connect(lambda: self._analysis.start_parsing())
        analysis_menu.addAction(parse)
        # Jeder Lauf bekommt seinen eigenen Abbruch, und der Name sagt welchen.
        # "Analyse abbrechen" stand hier allein und traf nur das Zerlegen -
        # ausgerechnet die beiden Laeufe, die auf einer grossen Bibliothek
        # Minuten dauern, liessen sich nicht anhalten, obwohl die Dienste
        # cancel_analysis() und cancel_build() die ganze Zeit anboten.
        cancel_parse = QAction("Zerlegen abbrechen", self)
        cancel_parse.triggered.connect(self._analysis.cancel_parsing)
        analysis_menu.addAction(cancel_parse)
        analysis_menu.addSeparator()
        similarity = QAction("Aehnlichkeiten und Familien berechnen", self)
        similarity.triggered.connect(lambda: self._analysis.start_analysis())
        analysis_menu.addAction(similarity)
        cancel_similarity = QAction("Aehnlichkeitsberechnung abbrechen", self)
        cancel_similarity.triggered.connect(self._analysis.cancel_analysis)
        analysis_menu.addAction(cancel_similarity)
        analysis_menu.addSeparator()
        evolution = QAction("Evolutionsbaeume berechnen", self)
        evolution.triggered.connect(lambda: self._evolution.start_build())
        analysis_menu.addAction(evolution)
        cancel_evolution = QAction("Baumberechnung abbrechen", self)
        cancel_evolution.triggered.connect(self._evolution.cancel_build)
        analysis_menu.addAction(cancel_evolution)
        analysis_menu.addSeparator()
        self._action_highlight = QAction("Segmente hervorheben", self)
        self._action_highlight.setCheckable(True)
        self._action_highlight.setChecked(True)
        self._action_highlight.toggled.connect(self._toggle_highlighting)
        analysis_menu.addAction(self._action_highlight)

        compare_menu = self.menuBar().addMenu("&Vergleich")
        self._action_clear_marks = QAction("Markierungen aufheben", self)
        self._action_clear_marks.triggered.connect(self._selection.clear)
        compare_menu.addAction(self._action_clear_marks)
        self._action_compare = QAction("Vergleich starten", self)
        self._action_compare.setShortcut("Ctrl+D")
        self._action_compare.setEnabled(False)
        self._action_compare.triggered.connect(self._start_compare)
        compare_menu.addAction(self._action_compare)

    def _connect_services(self) -> None:
        self._folder_tree.folder_selected.connect(self._on_folder_selected)
        self._folder_tree.context_action.connect(self._on_folder_action)

        self._explorer.selection_changed.connect(self._preview.show_item)
        self._explorer.item_activated.connect(self._open_detail_tab)
        self._explorer.context_action.connect(self._on_image_action)
        self._preview.context_action.connect(self._on_image_action)
        self._preview.open_requested.connect(self._images.open_in_viewer)
        self._preview.segment_action.connect(self._on_segment_action)

        self._images.results_ready.connect(self._on_results)
        self._images.thumbnails_ready.connect(self._on_thumbnails)
        self._images.folders_ready.connect(self._folder_tree.set_folders)

        self._prompts.search_ready.connect(self._on_search_results)

        self._analysis.segment_results.connect(self._on_segment_results)
        self._analysis.parse_finished.connect(self._on_parse_finished)
        self._analysis.similar_results.connect(self._on_similar_results)
        self._compare.comparison_ready.connect(self._on_comparison_ready)
        self._watch.sync_requested.connect(self._on_auto_sync)
        self._watch.nas_state_changed.connect(self._on_nas_state)
        self._evolution.tree_ready.connect(self._on_tree_ready)
        self._evolution.tree_missing.connect(self._on_tree_missing)
        self._evolution.build_finished.connect(self._on_evolution_built)
        self._evolution.build_progress.connect(
            lambda current, total: self._status.set_progress(current, total)
        )
        self._compare.comparison_failed.connect(self._on_comparison_failed)
        self._analysis.analysis_finished.connect(self._on_analysis_finished)
        self._analysis.analysis_failed.connect(self._on_analysis_failed)
        self._analysis.analysis_progress.connect(
            lambda current, total: self._status.set_progress(current, total)
        )
        self._analysis.parse_progress.connect(
            lambda current, total: self._status.set_progress(current, total)
        )

        self._index.scan_finished.connect(self._on_scan_finished)
        self._index.scan_progress.connect(self._on_scan_progress)
        self._index.scan_started.connect(self._on_scan_started)
        self._index.scan_rejected.connect(lambda text: self._status.showMessage(text, 5000))

        self._search.search_requested.connect(self._on_search_requested)
        self._search.cleared.connect(self._on_search_cleared)

        self._selection.marks_changed.connect(self._on_marks_changed)

        self._app.jobs.activity_changed.connect(self._status.set_activity)
        self._system.status_changed.connect(self._status.set_status)

        self._theme.palette_changed.connect(self._on_palette_changed)
        self._settings.subscribe("ui.theme", lambda _k, v: self._theme.set_preference(str(v)))
        self._settings.subscribe(
            "search.debounce_ms", lambda _k, v: self._search.set_debounce(int(v))
        )

    # Start --------------------------------------------------------------------

    def _initial_load(self) -> None:
        self._images.load_folder_tree_async()
        if self._settings.get_bool("index.scan_on_startup") and self._index.roots():
            self._index.start_scan()
        elif not self._index.roots():
            self._status.set_origin(
                "Kein Bibliotheksordner konfiguriert - Datei / Bibliotheksordner hinzufuegen"
            )

    # Ordner -------------------------------------------------------------------

    def _on_folder_selected(self, node: FolderNode, recursive: bool) -> None:
        self._current_folder = node
        self._search.set_folder_scope_enabled(True)
        self._images.load_folder_async(node, recursive)
        # Der gerade angesehene Ordner wird zusaetzlich ueberwacht; dort
        # sind Aenderungen am ehesten zu erwarten und am staerksten sichtbar.
        self._watch.watch_folder(node.path)

    def _on_folder_action(self, action_id: str, node: FolderNode) -> None:
        if action_id == "open_tab":
            self._open_folder_tab(node)
        elif action_id == "rescan":
            self._index.start_scan()
        elif action_id == "reveal":
            if not self._images.open_folder(node.path):
                self._status.showMessage("Ordner nicht zu oeffnen: " + node.path, 5000)

    def _open_folder_tab(self, node: FolderNode) -> None:
        """Oeffnet den Ordner als eigenen Tab mit Kachelraster und Sortierung.

        Derselbe Tab wie "Neueste Prompt-Paare", nur mit dem angeklickten Pfad
        als Inhalt. Ersetzt "Inhalt mit Unterordnern anzeigen" - das wurde nie
        gebraucht, ein zweiter Blick auf denselben Ordner dagegen staendig.
        """
        key = "folder:" + str(node.id)
        if self._tab_manager.focus(key):
            return
        tab = self._make_result_tab(key, _folder_label(node))
        self._images.load_folder_for(node, node.path, tab.set_results)

    # Ergebnisse ---------------------------------------------------------------

    def _on_results(self, results: ResultSet) -> None:
        """Ergebnisse des Bibliothekstabs.

        Suchergebnisse laufen ueber _on_search_results in einen eigenen Tab und
        kommen hier nicht an.
        """
        self._explorer.set_results(results)
        self._status.set_origin(results.origin + "  -  " + str(len(results)) + " Bilder")
        if not results.items:
            self._preview.clear()

    def _on_thumbnails(self, batch: list) -> None:
        """Fertige Vorschaubilder an alle Ansichten verteilen.

        Ein Bild kann in mehreren Tabs sichtbar sein. Das Modell ignoriert Ids,
        die es nicht kennt, deshalb ist das Verteilen an alle unbedenklich.
        """
        self._explorer.apply_thumbnails(batch)
        for widget in self._tab_manager.dynamic_widgets():
            apply_batch = getattr(widget, "apply_thumbnails", None)
            if apply_batch is not None:
                apply_batch(batch)

    # Bildaktionen -------------------------------------------------------------

    def _on_image_action(self, action_id: str, item: ImageItem) -> None:
        if action_id == "open":
            self._images.open_in_viewer(item)
        elif action_id == "reveal":
            self._images.reveal_in_explorer(item)
        elif action_id == "mark":
            marked = self._selection.toggle(item.id)
            self._status.showMessage(
                ("Markiert: " if marked else "Markierung entfernt: ") + item.filename, 3000
            )
        elif action_id == "detail":
            self._open_detail_tab(item)
        elif action_id == "similar" and item.prompt_id is not None:
            self._request_similar(item)
        elif action_id == "family" and item.prompt_id is not None:
            self._show_family(item)
        elif action_id == "compare":
            self._start_compare()
        elif action_id == "evolution":
            self._show_evolution(item)

    def _open_detail_tab(self, item: ImageItem) -> None:
        """Spezifikation 01: Doppelklick im Explorer oeffnet einen neuen Tab."""
        key = "detail:" + str(item.id)
        if self._tab_manager.focus(key):
            return
        tab = DetailTab(
            self._app,
            item,
            font_size=self._settings.get_int("ui.editor_font_size"),
            palette=self._theme.palette,
        )
        tab.context_action.connect(self._on_image_action)
        self._tab_manager.open(tab, key, item.filename)

    def _on_marks_changed(self, marks: list) -> None:
        count = len(marks)
        self._action_clear_marks.setEnabled(count > 0)
        self._action_compare.setEnabled(self._selection.can_compare)
        self._action_compare.setToolTip(
            "Strg+D" if self._selection.can_compare else "Zwei Bilder markieren"
        )
        if count == 0:
            self._status.showMessage("Markierungen aufgehoben", 2000)
        else:
            self._status.showMessage(
                str(count) + " von 2 Bildern fuer den Vergleich markiert", 3000
            )

    # Segmente -----------------------------------------------------------------


    def _on_segment_results(self, _request_id: int, _digest: str, image_ids: list) -> None:
        text = getattr(self, "_pending_segment", "")
        shortened = text if len(text) <= 50 else text[:49] + "…"
        label = 'Segment: "' + shortened + '"'

        if not image_ids:
            # Zwischen "Segment existiert, aber nur hier" und "war gar kein
            # Segment" unterscheiden. Markiert der Benutzer zwei Saetze auf
            # einmal, ist die Auswahl kein Segment - "keine weiteren
            # Verwendungen" waere dann eine irrefuehrende Auskunft.
            prompts, _images = self._analysis.segment_usage(text)
            if prompts == 0:
                self._status.showMessage(
                    label + " - kein bekanntes Segment. Genau einen Satz oder ein Tag markieren.",
                    7000,
                )
            else:
                self._status.showMessage(label + " - nur in diesem Prompt verwendet", 5000)
            return

        # Spezifikation 01: die Segmentsuche oeffnet ein eigenes Tab.
        self._open_result_tab("segment:" + text, label, list(image_ids))

    def _on_segment_action(self, action_id: str, text: str) -> None:
        if action_id == "search_segment" and text.strip():
            self._pending_segment = text.strip()
            self._analysis.search_segment_async(text)
        elif action_id == "similar":
            item = self._preview.current_item
            if item is not None and item.prompt_id is not None:
                self._request_similar(item)
        elif action_id == "evolution":
            item = self._preview.current_item
            if item is not None:
                self._show_evolution(item)

    def _request_similar(self, item) -> None:
        self._pending_result = ("similar", item.filename)
        self._analysis.similar_images_async(item.prompt_id)

    def _show_family(self, item) -> None:
        """Zeigt alle Bilder der Promptfamilie in einem eigenen Tab."""
        cluster = self._analysis.cluster_of(item.prompt_id)
        if cluster is None:
            self._status.showMessage(
                "Zu diesem Prompt ist noch keine Familie berechnet."
                " Analyse ueber das Menue starten.",
                6000,
            )
            return
        label = (
            "Familie: "
            + str(cluster["member_count"])
            + " Prompts / "
            + str(cluster["image_count"])
            + " Bilder"
        )
        self._open_result_tab(
            "family:" + str(cluster["id"]),
            label,
            self._analysis.cluster_images(int(cluster["id"])),
        )

    def _on_similar_results(self, _request_id: int, image_ids: list) -> None:
        kind, name = getattr(self, "_pending_result", ("similar", ""))
        if kind != "similar":
            return
        if not image_ids:
            self._status.showMessage(
                "Keine aehnlichen Prompts gefunden."
                " Analyse ausfuehren oder Schwellwert senken.",
                6000,
            )
            return
        self._open_result_tab("similar:" + name, "Aehnlich zu " + name, list(image_ids))

    def _open_newest_pairs(self) -> None:
        """Was zuletzt entstanden ist - bibliotheksweit, in einem eigenen Tab.

        Der Ordnerbaum beantwortet die Frage "was liegt wo"; diese Ansicht
        beantwortet "was ist neu", und dafuer ist die Ordnerstruktur im Weg.
        Sortiert wird nach dem Zeitstempel des Prompts, und der Tab bringt sein
        eigenes Sortierfeld mit.
        """
        limit = self._settings.get_int("search.max_results")
        key = "newest"
        label = "Neueste Prompt-Paare"
        if self._tab_manager.focus(key):
            return
        tab = self._make_result_tab(key, label)
        self._images.load_newest_pairs(limit, label, tab.set_results)

    def _open_newest_templates(self) -> None:
        """Was direkt in den Namensordnern liegt - die Vorlagen.

        Gegenstueck zu "Neueste Prompt-Paare": dort das laufende Ergebnis aus
        den ai-Ordnern, hier das Ausgangsmaterial eine Ebene darueber. Bilder
        aus Unterordnern bleiben aussen vor, sonst waere es dieselbe Ansicht.
        """
        key = "templates"
        label = "Neueste Vorlagen"
        if self._tab_manager.focus(key):
            return
        tab = self._make_result_tab(key, label)
        self._images.load_newest_templates(
            self._settings.get_int("search.max_results"), label, tab.set_results
        )

    def _make_result_tab(self, key: str, label: str) -> ResultTab:
        """Ein Ergebnistab mit Kachelraster, Vorschau und Sortierfeld."""
        tab = ResultTab(
            self._app,
            self._theme.palette,
            self._selection,
            title=label,
            key=key,
            tile_size=self._explorer.view.tile_size,
            font_size=self._settings.get_int("ui.editor_font_size"),
        )
        tab.context_action.connect(self._on_image_action)
        tab.item_activated.connect(self._open_detail_tab)
        tab.preview.segment_action.connect(self._on_segment_action)
        self._tab_manager.open(tab, key, label)
        return tab

    def _open_result_tab(self, key: str, label: str, image_ids: list) -> None:
        """Gemeinsamer Weg fuer Segment-, Aehnlichkeits- und Familientabs."""
        if self._tab_manager.focus(key):
            return
        tab = self._make_result_tab(key, label)
        self._images.load_ids_for(image_ids, label, tab.set_results)

    def _on_analysis_finished(self, result) -> None:
        self._status.set_progress(0, 0)
        self._status.showMessage("Analyse: " + result.summary(), 8000)
        # Neu gebildete Familien machen alle Baeume ungueltig; sie werden
        # anschliessend im Hintergrund neu aufgebaut.
        if result.clusters.clusters:
            self._evolution.invalidate_all()
            self._evolution.start_build()

    # Vergleich ----------------------------------------------------------------

    def _start_compare(self) -> None:
        """Oeffnet die Vergleichsansicht fuer die beiden markierten Bilder."""
        marks = self._selection.marks()
        if len(marks) != 2:
            self._status.showMessage(
                "Fuer einen Vergleich genau zwei Bilder markieren"
                " (Rechtsklick, Fuer Vergleich markieren)",
                6000,
            )
            return
        self._compare.compare_async(marks[0], marks[1])

    def _on_comparison_ready(self, pair) -> None:
        from app.gui.compare.compare_tab import CompareTab, compare_key

        key = compare_key(pair.left_image.id, pair.right_image.id)
        if self._tab_manager.focus(key):
            return
        tab = CompareTab(
            self._app,
            pair,
            self._theme.palette,
            font_size=self._settings.get_int("ui.editor_font_size"),
        )
        tab.context_action.connect(self._on_image_action)
        title = pair.left_image.stem + " <> " + pair.right_image.stem
        self._tab_manager.open(tab, key, title)

    def _on_comparison_failed(self, message: str) -> None:
        self._status.showMessage(message, 7000)

    # Evolution ----------------------------------------------------------------

    def _show_evolution(self, item) -> None:
        """Oeffnet den Evolutionsbaum der Familie dieses Bildes.

        Es wird nicht gerechnet: liegt kein Baum vor, sagt der Dienst das, und
        der Benutzer entscheidet. Spezifikation 06 verbietet die Neuberechnung
        beim Oeffnen ausdruecklich.
        """
        self._evolution.tree_for_image_async(item.id)

    def _on_tree_ready(self, _request_id: int, tree) -> None:
        from app.gui.evolution.evolution_tab import EvolutionTab, evolution_key

        key = evolution_key(tree.cluster_id)
        if self._tab_manager.focus(key):
            return
        tab = EvolutionTab(
            self._app,
            tree,
            self._theme.palette,
            font_size=self._settings.get_int("ui.editor_font_size"),
        )
        tab.context_action.connect(self._on_image_action)
        tab.node_activated.connect(self._on_evolution_node)
        self._tab_manager.open(tab, key, "Evolution: Familie " + str(tree.cluster_id))

    def _on_evolution_node(self, prompt_id: int, label: str) -> None:
        """Doppelklick auf einen Knoten zeigt dessen Bilder im Kachelraster."""
        image_ids = self._evolution.images_for_prompt(prompt_id)
        if not image_ids:
            self._status.showMessage("Zu diesem Knoten gibt es kein Bild", 4000)
            return
        self._open_result_tab("node:" + str(prompt_id), label, image_ids)

    def _on_tree_missing(self, message: str) -> None:
        self._status.showMessage(message, 8000)

    def _on_evolution_built(self, stats) -> None:
        self._status.set_progress(0, 0)
        self._status.showMessage("Evolution: " + stats.summary(), 8000)

    # Ueberwachung -------------------------------------------------------------

    def _on_auto_sync(self, reason: str) -> None:
        """Ein Abgleich, den der Benutzer nicht angestossen hat.

        Er soll ihn bemerken, aber nicht bei der Arbeit gestoert werden -
        deshalb die Statuszeile und kein Dialog.
        """
        label = {
            "periodic": "Turnusmaessiger Abgleich laeuft",
            "watcher": "Aenderung erkannt, Abgleich laeuft",
        }.get(reason, "Abgleich laeuft")
        self._status.showMessage(label, 5000)

    def _on_nas_state(self, state: dict) -> None:
        """Meldet nur echte Wechsel.

        Die erste Pruefung nach dem Start aendert den Zustand von "noch nichts
        geprueft" auf "erreichbar". Das ist kein Ereignis, ueber das jemand
        unterrichtet werden will - eine Entwarnung wird nur gemeldet, wenn
        vorher tatsaechlich gewarnt wurde.
        """
        unreachable = [root for root, value in state.items() if value == "open"]
        if unreachable:
            self._nas_warned = True
            self._status.showMessage(
                "NAS nicht erreichbar: " + ", ".join(unreachable)
                + " - Suche und Analyse laufen mit lokalen Daten weiter",
                10000,
            )
        elif self._nas_warned:
            self._nas_warned = False
            self._status.showMessage("NAS wieder erreichbar", 5000)
        self._system.refresh()

    def _show_settings(self) -> None:
        from app.gui.dialogs.settings_dialog import SettingsDialog

        # Der ThemeManager wird uebergeben, damit eine geaenderte Farbe sofort
        # erscheint: der Dialog laesst die Palette danach neu aufloesen, und
        # palette_changed traegt sie in alle offenen Tabs.
        SettingsDialog(self._settings, self._analysis, self, theme=self._theme).exec()

    def _show_scan_history(self) -> None:
        from app.gui.dialogs.scan_history_dialog import ScanHistoryDialog

        ScanHistoryDialog(self._index, self._app.watch_service, self).exec()

    def _on_parse_finished(self, stats) -> None:
        self._status.set_progress(0, 0)
        if stats.prompts:
            self._status.showMessage("Analyse: " + stats.summary(), 6000)
            # Hervorhebung des aktuell gezeigten Prompts auffrischen.
            if self._preview.current_item is not None:
                self._preview.show_item(self._preview.current_item)
            # Erst wenn geparst ist, koennen Aehnlichkeiten entstehen.
            self._analysis.start_analysis()

    def _toggle_highlighting(self, enabled: bool) -> None:
        self._preview.editor.highlighter.set_enabled(enabled)
        for widget in self._tab_manager.dynamic_widgets():
            preview = getattr(widget, "preview", None)
            if preview is not None:
                preview.editor.highlighter.set_enabled(enabled)

    # Suche --------------------------------------------------------------------

    def _on_search_requested(self, text: str, scope: str) -> None:
        folder_ids = None
        if scope == SCOPE_FOLDER and self._current_folder is not None:
            folder_ids = self._images.folder_scope(self._current_folder)
        self._pending_search = (text, scope)
        self._prompts.search_async(text, folder_ids)

    def _on_search_cleared(self) -> None:
        self._pending_search = None
        if self._current_folder is not None:
            self._images.load_folder_async(self._current_folder)

    def _on_search_results(self, _request_id: int, image_ids: list) -> None:
        if self._pending_search is None:
            return
        text, scope = self._pending_search
        label = "Suche: " + text
        if scope == SCOPE_FOLDER and self._current_folder is not None:
            label += "  (in " + self._current_folder.name + ")"

        if not image_ids:
            self._status.set_origin(label + "  -  keine Treffer")
            self._explorer.clear()
            self._preview.clear()
            return

        self._images.load_ids_async(list(image_ids), label)
        self._status.showMessage(
            label + ": " + str(len(image_ids)) + " Treffer  (Strg+Enter oeffnet einen Tab)", 6000
        )

    def _open_search_tab(self) -> None:
        """Haelt das aktuelle Ergebnis in einem eigenen Tab fest.

        Spezifikation 01 verlangt eigene Tabs fuer Suchergebnisse. Der Tab wird
        bewusst erst auf Anforderung erzeugt: bei jeder Tastatureingabe einen
        aufzumachen waere unbrauchbar.
        """
        if self._explorer.item_count() == 0:
            self._status.showMessage("Kein Ergebnis zum Festhalten", 3000)
            return

        origin = self._explorer.model.origin or "Ergebnis"
        key = "result:" + origin
        if self._tab_manager.focus(key):
            return

        tab = ResultTab(
            self._app,
            self._theme.palette,
            self._selection,
            title=origin,
            key=key,
            tile_size=self._explorer.view.tile_size,
            font_size=self._settings.get_int("ui.editor_font_size"),
        )
        tab.context_action.connect(self._on_image_action)
        tab.item_activated.connect(self._open_detail_tab)
        tab.preview.segment_action.connect(self._on_segment_action)
        tab.set_results(
            ResultSet(items=self._explorer.model.items(), origin=origin)
        )
        self._tab_manager.open(tab, key, origin)
        tab.explorer.refresh_visible()

    # Indexierung --------------------------------------------------------------

    def _on_analysis_failed(self, message: str) -> None:
        """Sichtbar machen, dass nichts gerechnet wurde.

        Ohne diese Meldung sah ein Fehlschlag aus wie "nichts zu tun" - die
        Statusleiste meldete Erfolg, und der Fehler stand nur in der
        Problemliste, in die niemand ohne Anlass sieht.
        """
        self._status.set_progress(0, 0)
        self._status.showMessage(
            "Analyse fehlgeschlagen: " + message + " - Einzelheiten in der Problemliste",
            15000,
        )

    def _on_scan_started(self, scan_type: str) -> None:
        self._set_scan_actions(running=True)
        self._status.set_activity(
            1,
            "Neuindizierung laeuft" if scan_type == "full" else "Abgleich laeuft",
        )

    def _set_scan_actions(self, *, running: bool) -> None:
        for action in self._scan_actions:
            action.setEnabled(not running)
        self._cancel_action.setEnabled(running)

    def _on_scan_progress(self, current: int, total: int, text: str) -> None:
        self._status.set_progress(current, total)
        if text:
            self._status.set_activity(1, "Abgleich: " + text)

    def _on_scan_finished(self, stats: ScanStats) -> None:
        self._set_scan_actions(running=False)
        self._status.set_progress(0, 0)
        message = "Abgleich abgebrochen" if stats.cancelled else "Abgleich: " + stats.summary()
        self._status.showMessage(message, 8000)
        self._images.load_folder_tree_async()
        # Nicht auf den naechsten Abfragetakt warten: nach einem Abgleich
        # muessen Bild- und Promptzahl sofort stimmen.
        self._system.refresh()
        # Neue oder geaenderte Prompts analysieren. Der Job prueft selbst, ob
        # etwas zu tun ist, und laeuft im cpu-Pool, ohne die Bedienung zu stoeren.
        self._analysis.start_parsing()

    def _confirm_reindex(self) -> None:
        answer = QMessageBox.question(
            self,
            "Neu indizieren",
            "Alle Dateien werden erneut eingelesen. Bei grossen Bibliotheken kann das"
            " lange dauern.\n\nFortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._index.start_full_reindex()

    def _add_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Bibliotheksordner waehlen")
        if not directory:
            return
        if self._index.add_root(directory):
            self._index.start_scan()
        else:
            self._status.showMessage("Ordner ist bereits eingebunden", 4000)

    # Ansicht ------------------------------------------------------------------

    def _set_tile_size(self, size: int) -> None:
        self._explorer.set_tile_size(size)
        for widget in self._tab_manager.dynamic_widgets():
            widget.set_tile_size(size)
        self._images.set_display_size(size)
        self._settings.set("thumbnails.default_size", size)

    def _on_palette_changed(self, palette) -> None:
        """Neue Farben an ALLE Ansichten, auch die des Bibliothekstabs.

        Der Prompt-Editor des Bibliothekstabs fehlte hier. Eine geaenderte
        Hervorhebungsfarbe erschien dadurch in jedem geoeffneten Tab, nur nicht
        dort, wo man sie zuerst sucht - im Hauptfenster.
        """
        self._preview.set_palette_colors(palette)
        self._explorer.set_palette_colors(palette)
        self._status.set_palette_colors(palette)
        for widget in self._tab_manager.dynamic_widgets():
            widget.set_palette_colors(palette)

    def _show_problems(self) -> None:
        ProblemDialog(self._system, self).exec()

    # Fensterzustand -----------------------------------------------------------

    def _qsettings(self) -> QSettings:
        return QSettings("PicPromptEditor", "MainWindow")

    def _restore_geometry(self) -> None:
        store = self._qsettings()
        geometry = store.value("geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        for key, splitter in (("top", self._top_splitter), ("main", self._main_splitter)):
            state = store.value("splitter/" + key)
            if isinstance(state, QByteArray):
                splitter.restoreState(state)

    def _save_geometry(self) -> None:
        """Merkt sich Groesse, Position und die Teilerpositionen.

        Ein maximiertes Fenster wird als solches gespeichert; saveGeometry
        haelt beides fest, den Maximierungszustand und die Groesse davor.
        """
        store = self._qsettings()
        store.setValue("geometry", self.saveGeometry())
        store.setValue("splitter/top", self._top_splitter.saveState())
        store.setValue("splitter/main", self._main_splitter.saveState())
        store.sync()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().resizeEvent(event)
        if not self._settings.get_bool("ui.snap_window_to_tiles"):
            return
        # NICHT waehrend des Ziehens korrigieren. Ein resize() bei jedem
        # Ereignis laesst das Fenster unter dem Mauszeiger flackern, weil es
        # gegen die Bewegung zurueckspringt. Der Zeitgeber wird bei jeder
        # Groessenaenderung neu gestartet und feuert erst, wenn der Benutzer
        # loslaesst.
        self._snap_timer.start()

    def _apply_window_icon(self) -> None:
        """Setzt das Programmsymbol fuer Fenster und Taskleiste.

        Auf der ganzen ANWENDUNG, nicht nur auf diesem Fenster: sonst traegt
        jeder Dialog weiter das Standardsymbol von Qt, und in der Taskleiste
        steht das Programm ohne eigenes Zeichen.

        Ein fehlendes Symbol ist kein Grund, den Start abzubrechen - der
        Startbericht meldet fehlende Ressourcen ohnehin.
        """
        from PyQt6.QtGui import QIcon

        from app.gui.resources import APP_ICON, asset_path

        pfad = asset_path(APP_ICON)
        if not pfad.is_file():
            return
        symbol = QIcon(str(pfad))
        if symbol.isNull():
            return
        self.setWindowIcon(symbol)
        instance = QApplication.instance()
        if instance is not None:
            instance.setWindowIcon(symbol)

    def _apply_size_increment(self) -> None:
        """Laesst den Fenstermanager selbst in Kachelschritten einrasten.

        Das ist der Weg ohne Flackern: Windows kennt Groessenschritte und
        bewegt den Fensterrand beim Ziehen gleich in ganzen Stufen - es wird
        also nichts nachtraeglich zurueckgesetzt. Wirksam nur beim Ziehen mit
        der Maus, nicht bei resize() aus dem Programm; deshalb bleibt die
        verzoegerte Korrektur unten als zweites Netz bestehen.
        """
        if not self._settings.get_bool("ui.snap_window_to_tiles"):
            self.setSizeIncrement(QSize(1, 1))
            return
        view = self._explorer.view
        step = view.grid_step_width()
        if step <= 0:
            return
        chrome = self.width() - view.viewport().width()
        self.setBaseSize(QSize(chrome + view.edge_width(), 0))
        self.setSizeIncrement(QSize(step, 1))

    def _snap_width(self) -> None:
        """Macht die Fensterbreite zu einem Vielfachen der Kachelbreite.

        Ohne das bleibt rechts ein Streifen stehen, in den fast noch eine
        Kachel passt, und der Rollbalken steht halb ueber der letzten Spalte.
        Gemessen wird am Sichtfenster des Kachelrasters, der Rollbalken zaehlt
        also nicht mit.

        Verkleinert wird, nie vergroessert: eine Anwendung, die von selbst
        breiter wird, waere uebergriffig - und auf einem vollen Bildschirm ginge
        es ohnehin nicht.
        """
        self._apply_size_increment()
        if self._snapping or self.isMaximized() or self.isFullScreen():
            return
        surplus = self._explorer.view.surplus_width()
        if surplus <= 0:
            return

        self._snapping = True
        try:
            self.resize(max(self.minimumWidth(), self.width() - surplus), self.height())
        finally:
            self._snapping = False

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt)
        self._save_geometry()
        self._index.cancel_scan()
        super().closeEvent(event)
