"""Einstellungsdialog - vollstaendig aus dem Schema erzeugt.

`app/config/schema.py` ist laut Spezifikation 11 die eine Stelle, an der ein
Parameter definiert wird. Dieser Dialog schreibt deshalb keinen Schluessel,
keine Grenze und keine Beschreibung ein zweites Mal auf: er liest die
Definitionen und baut daraus die Eingabefelder. Eine neue Einstellung im Schema
erscheint hier von selbst, ohne dass diese Datei angefasst werden muss.

Drei Dinge, die sonst still schiefgehen:

* **Analyseparameter muessen eine Neuanalyse ausloesen.** Die Analyse laeuft
  nur, wenn sie angestossen wird; ein geaenderter Schwellwert allein bewirkt
  nichts. Ohne diesen Anstoss stellt man einen Wert ein, sieht keine Wirkung
  und haelt die Einstellung fuer kaputt. (Bei der Kalibrierung genau so
  passiert.)
* **requires_restart muss sichtbar sein.** Drei Einstellungen koennen laufende
  Komponenten nicht uebernehmen. Wer das nicht erfaehrt, sucht den Fehler.
* **Ein ungueltiger Wert darf nicht die uebrigen mitreissen.** Jede Einstellung
  wird einzeln geschrieben; was durchfaellt, wird benannt, der Rest bleibt.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.schema import SettingDef, ValidationError, all_defs
from app.gui.theme import OVERRIDABLE, scheme_name

#: Deutsche Namen der Gruppen. Die Reihenfolge ist die der Reiter und folgt dem
#: Weg der Daten durch das Programm: erst woher sie kommen, dann was damit
#: geschieht, zuletzt Anzeige und Betrieb.
GROUPS: tuple[tuple[str, str], ...] = (
    ("library", "Bibliothek"),
    ("index", "Abgleich"),
    ("parsing", "Auswertung"),
    ("analysis", "Analyse"),
    ("compare", "Vergleich"),
    ("search", "Suche"),
    ("ui", "Anzeige"),
    # Die Vorschaubilder liegen im Schema in der Gruppe "cache" - sie sind
    # Zwischenspeicher, kein eigener Bereich.
    ("cache", "Vorschau und Zwischenspeicher"),
    ("jobs", "Hintergrundarbeit"),
    ("system", "System"),
)

#: Aenderungen an diesen Gruppen machen die vorhandene Analyse ungueltig.
REANALYSE_GROUPS = frozenset({"analysis", "parsing"})

RESTART_MARK = " *"

#: Breite der Beschriftungsspalte. Fest, damit die Eingabefelder auf JEDER
#: Seite an derselben Stelle beginnen - sonst springt die Spalte beim Wechsel
#: der Kategorie, und der Dialog wirkt unaufgeraeumt.
LABEL_WIDTH = 320
#: Breite der Zahlen- und Auswahlfelder. Ohne feste Breite waren sie zwischen
#: 14 und 108 Pixel breit, je nach Wertebereich - nichts stand untereinander.
FIELD_WIDTH = 150
#: Breite der Kategorienliste links.
NAV_WIDTH = 210


class ListEditor(QWidget):
    """Editor fuer Listeneinstellungen.

    Vier Einstellungen sind Listen, und sie sind verschieden genug, dass ein
    Textfeld mit Kommas nicht reicht: Wurzelverzeichnisse werden ausgewaehlt,
    Dateiendungen getippt. Deshalb eine Liste mit Knoepfen und, wo es passt,
    einem Ordnerdialog.
    """

    def __init__(self, definition: SettingDef, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._definition = definition
        self._numeric = definition.key == "thumbnails.sizes"
        self._folders = definition.key == "library.roots"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.setMaximumHeight(96)
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        add = QPushButton("Ordner waehlen..." if self._folders else "Hinzufuegen")
        remove = QPushButton("Entfernen")
        add.clicked.connect(self._add)
        remove.clicked.connect(self._remove)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._entry = QLineEdit()
        self._entry.setPlaceholderText("Neuer Eintrag, dann Eingabetaste")
        self._entry.returnPressed.connect(self._add_from_entry)
        if not self._folders:
            layout.addWidget(self._entry)

    def set_value(self, values: list[Any]) -> None:
        self._list.clear()
        for value in values:
            self._list.addItem(str(value))

    def value(self) -> list[Any]:
        texts = [
            self._list.item(row).text() for row in range(self._list.count())
        ]
        if self._numeric:
            # Die Pruefung bleibt beim Validator; hier wird nur so weit
            # gewandelt, dass er ueberhaupt Zahlen zu sehen bekommt.
            numbers = []
            for text in texts:
                try:
                    numbers.append(int(text))
                except ValueError:
                    numbers.append(text)
            return numbers
        return texts

    def _add(self) -> None:
        if self._folders:
            directory = QFileDialog.getExistingDirectory(self, "Verzeichnis waehlen")
            if directory:
                self._list.addItem(directory)
            return
        self._add_from_entry()

    def _add_from_entry(self) -> None:
        text = self._entry.text().strip()
        if text:
            self._list.addItem(text)
            self._entry.clear()

    def _remove(self) -> None:
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))


class ColorEditor(QWidget):
    """Ein Regler fuer eine Farbe - und zwar die des GERADE aktiven Schemas.

    Gespeichert wird je Schema (hell und dunkel getrennt), sichtbar ist nur
    einer. Wer im Dunkelmodus eine Farbe waehlt, aendert den Dunkelmodus; das
    helle Schema behaelt seine Vorgabe. Ein einziger gemeinsamer Wert waere in
    einem der beiden Schemata kaum lesbar - ein helles Blau verschwindet auf
    dunklem Grund.
    """

    def __init__(
        self,
        definition: SettingDef,
        palette,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._definition = definition
        self._palette = palette
        self._stored: dict[str, str] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._button = QPushButton()
        self._button.setFixedSize(FIELD_WIDTH - 90, 24)
        self._button.clicked.connect(self._choose)
        layout.addWidget(self._button)

        self._reset = QPushButton("Standard")
        self._reset.setFixedWidth(84)
        self._reset.clicked.connect(self._clear)
        layout.addWidget(self._reset)
        layout.addStretch(1)

        self._refresh()

    # Zustand ------------------------------------------------------------------

    @property
    def _scheme(self) -> str:
        return scheme_name(bool(getattr(self._palette, "dark", False)))

    def _default_color(self) -> QColor:
        """Die Farbe, die ohne Einstellung gilt - als Vorgabe des Reglers.

        Ein Eintrag kann mehrere Palettenfelder setzen (die Hintergrundfarbe
        faerbt vier auf einmal). Fuer die Anzeige genuegt das erste: sie
        tragen ohnehin denselben Wert, sonst waeren sie nicht zusammengefasst.
        """
        felder = OVERRIDABLE.get(self._definition.key, ())
        if not felder:
            return QColor("#808080")
        return QColor(getattr(self._palette, felder[0], QColor("#808080")))

    def effective_color(self) -> QColor:
        code = self._stored.get(self._scheme)
        if code:
            color = QColor(code)
            if color.isValid():
                return color
        return self._default_color()

    def set_value(self, raw: str) -> None:
        self._stored = {}
        text = (raw or "").strip()
        if text:
            import json

            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    self._stored = {
                        str(k): str(v) for k, v in parsed.items() if k in ("light", "dark")
                    }
            except ValueError:
                self._stored = {}
        self._refresh()

    def value(self) -> str:
        import json

        return json.dumps(self._stored, sort_keys=True) if self._stored else ""

    # Bedienung ----------------------------------------------------------------

    def _choose(self) -> None:
        chosen = QColorDialog.getColor(
            self.effective_color(), self, "Farbe fuer das " +
            ("dunkle" if self._scheme == "dark" else "helle") + " Schema",
        )
        if chosen.isValid():
            self._stored[self._scheme] = chosen.name()
            self._refresh()

    def _clear(self) -> None:
        """Nur das aktive Schema zuruecksetzen, nicht beide."""
        self._stored.pop(self._scheme, None)
        self._refresh()

    def _refresh(self) -> None:
        color = self.effective_color()
        eigen = self._scheme in self._stored
        textfarbe = "#111111" if color.lightness() > 128 else "#f0f0f0"
        self._button.setStyleSheet(
            "background-color: " + color.name()
            + "; color: " + textfarbe
            + "; border: 1px solid rgba(128,128,128,0.6); border-radius: 3px"
        )
        self._button.setText(color.name() + ("" if eigen else " (Vorgabe)"))
        self._reset.setEnabled(eigen)
        self.setToolTip(
            self._definition.key
            + " - gilt fuer das "
            + ("dunkle" if self._scheme == "dark" else "helle")
            + " Schema"
        )


class SettingsDialog(QDialog):
    """Zeigt und schreibt alle Einstellungen."""

    def __init__(
        self,
        settings,
        analysis_service=None,
        parent: QWidget | None = None,
        *,
        theme=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._analysis = analysis_service
        # Der ThemeManager wird fuer die Farbregler gebraucht: sie muessen
        # wissen, welches Schema gerade gilt und wie die Vorgabe darin
        # aussieht - und nach einer Aenderung muss die Palette neu aufgeloest
        # werden, damit die Farbe sofort erscheint statt erst nach Neustart.
        #
        # Schluesselwort-only: ein positionell eingefuegtes palette vor parent
        # hat in M3 die Hervorhebung in allen Tabs stillschweigend zerstoert
        # (siehe doc/STATUS.md).
        if theme is None:
            from app.gui.theme import ThemeManager

            theme = ThemeManager(settings.get_str("ui.theme"), settings)
        self._theme = theme
        self._palette = theme.palette
        self._editors: dict[str, tuple[SettingDef, QWidget]] = {}
        self._titles: list[str] = []

        self.setWindowTitle("Einstellungen")

        layout = QVBoxLayout(self)

        # Kategorien links statt Reiter oben. Neun Reiter brauchten 1404 Pixel
        # Breite; das Fenster war 720 breit, und Qt blendete Pfeiltasten ein,
        # mit denen die Haelfte der Kategorien unsichtbar war. Eine Liste
        # waechst nach unten und bleibt vollstaendig lesbar.
        split = QHBoxLayout()
        self._nav = QListWidget()
        self._nav.setFixedWidth(NAV_WIDTH)
        self._nav.currentRowChanged.connect(self._on_category)
        split.addWidget(self._nav)

        self._pages = QStackedWidget()
        split.addWidget(self._pages, 1)
        layout.addLayout(split, 1)

        self._build_pages()
        self._nav.setCurrentRow(0)

        hint = QLabel(
            RESTART_MARK.strip()
            + " Diese Einstellung wird erst beim naechsten Programmstart wirksam."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        defaults = buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        defaults.setText("Standardwerte")
        defaults.clicked.connect(self._restore_defaults)
        layout.addWidget(buttons)

        self.load()
        self._size_to_content()

    def _size_to_content(self) -> None:
        """Waehlt eine Groesse, in die der umfangreichste Bereich passt.

        Eine fest gesetzte Groesse war zu klein: der Bereich mit den meisten
        Einstellungen musste gerollt werden, obwohl auf dem Bildschirm Platz
        war. Gerechnet statt geraten - und nach oben begrenzt, damit der Dialog
        auf einem kleinen Bildschirm nicht ueber den Rand hinauswaechst.
        """
        needed = max(
            (self._pages.widget(i).widget().sizeHint().height()
             for i in range(self._pages.count())),
            default=480,
        )
        screen = self.screen().availableGeometry() if self.screen() else None
        max_h = int(screen.height() * 0.85) if screen else 900
        max_w = int(screen.width() * 0.7) if screen else 1100
        width = min(max_w, NAV_WIDTH + LABEL_WIDTH + 2 * FIELD_WIDTH + 120)
        self.resize(width, min(max_h, needed + 150))

    # Aufbau -------------------------------------------------------------------

    def _on_category(self, row: int) -> None:
        if 0 <= row < self._pages.count():
            self._pages.setCurrentIndex(row)

    def _build_pages(self) -> None:
        by_group: dict[str, list[SettingDef]] = {}
        for definition in all_defs():
            by_group.setdefault(definition.group, []).append(definition)

        known = [name for name, _ in GROUPS]
        # Eine Gruppe, die im Schema auftaucht, hier aber nicht benannt ist,
        # bekommt trotzdem einen Reiter - sonst waere sie unerreichbar.
        ordered = list(GROUPS) + [
            (name, name) for name in sorted(by_group) if name not in known
        ]

        for group, title in ordered:
            definitions = by_group.get(group)
            if not definitions:
                continue
            self._nav.addItem(title)
            self._pages.addWidget(self._build_page(definitions))
            self._titles.append(title)

    def _build_page(self, definitions: list[SettingDef]) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(10)
        form.setContentsMargins(16, 16, 16, 16)

        # Reihenfolge des Schemas beibehalten, nicht alphabetisch nach
        # Schluessel: im Schema stehen zusammengehoerige Einstellungen
        # beieinander, alphabetisch reisst es sie auseinander.
        for definition in definitions:
            editor = self._build_editor(definition)
            self._editors[definition.key] = (definition, editor)

            label = QLabel(
                definition.description + (RESTART_MARK if definition.requires_restart else "")
            )
            label.setWordWrap(True)
            label.setFixedWidth(LABEL_WIDTH)
            # Der Schluessel steht im Tooltip: er ist die Verbindung zu
            # Dokumentation und Fehlermeldungen, gehoert aber nicht dauerhaft
            # in die Oberflaeche.
            label.setToolTip(definition.key)
            editor.setToolTip(definition.key)
            form.addRow(label, editor)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(page)
        return area

    def _build_editor(self, definition: SettingDef) -> QWidget:
        """Ein Eingabefeld je Typ, mit einheitlicher Breite.

        Die feste Breite ist keine Kosmetik: Zahlenfelder richten sich sonst
        nach ihrem Wertebereich, und ein Bereich bis 86400 ergibt ein doppelt
        so breites Feld wie einer bis 20. Untereinander sah das aus wie
        hingewuerfelt.
        """
        editor = self._create_editor(definition)
        if isinstance(editor, ColorEditor):
            return editor
        if isinstance(editor, (QSpinBox, QDoubleSpinBox, QComboBox)):
            editor.setFixedWidth(FIELD_WIDTH)
        elif isinstance(editor, QLineEdit):
            editor.setMinimumWidth(FIELD_WIDTH)
            editor.setMaximumWidth(2 * FIELD_WIDTH)
        elif isinstance(editor, ListEditor):
            editor.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
            )
        return editor

    def _create_editor(self, definition: SettingDef) -> QWidget:
        if definition.key in OVERRIDABLE:
            return ColorEditor(definition, self._palette)

        if definition.type == "bool":
            return QCheckBox()

        if definition.type == "list":
            return ListEditor(definition)

        allowed = definition.allowed
        if allowed:
            box = QComboBox()
            box.addItems(list(allowed))
            return box

        bounds = definition.bounds
        if definition.type == "int":
            spin = QSpinBox()
            low, high = bounds if bounds else (0, 1_000_000)
            spin.setRange(int(low), int(high))
            spin.setGroupSeparatorShown(int(high) >= 10000)
            return spin

        if definition.type == "float":
            spin = QDoubleSpinBox()
            low, high = bounds if bounds else (0.0, 1_000_000.0)
            spin.setRange(float(low), float(high))
            spin.setDecimals(2)
            spin.setSingleStep(0.05 if float(high) <= 1.0 else 1.0)
            return spin

        return QLineEdit()

    # Werte --------------------------------------------------------------------

    def load(self) -> None:
        """Uebernimmt die gespeicherten Werte in die Felder."""
        for key, (definition, editor) in self._editors.items():
            self._set_editor(editor, definition, self._settings.get(key))

    def _set_editor(self, editor: QWidget, definition: SettingDef, value: Any) -> None:
        if isinstance(editor, ColorEditor):
            editor.set_value(str(value or ""))
        elif isinstance(editor, QCheckBox):
            editor.setChecked(bool(value))
        elif isinstance(editor, ListEditor):
            editor.set_value(list(value or []))
        elif isinstance(editor, QComboBox):
            index = editor.findText(str(value))
            editor.setCurrentIndex(index if index >= 0 else 0)
        elif isinstance(editor, QSpinBox):
            editor.setValue(int(value))
        elif isinstance(editor, QDoubleSpinBox):
            editor.setValue(float(value))
        elif isinstance(editor, QLineEdit):
            editor.setText(str(value))

    def _editor_value(self, editor: QWidget) -> Any:
        if isinstance(editor, ColorEditor):
            return editor.value()
        if isinstance(editor, QCheckBox):
            return editor.isChecked()
        if isinstance(editor, ListEditor):
            return editor.value()
        if isinstance(editor, QComboBox):
            return editor.currentText()
        if isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            return editor.value()
        if isinstance(editor, QLineEdit):
            return editor.text()
        return None

    def values(self) -> dict[str, Any]:
        """Der aktuelle Stand der Felder. Fuer Tests und zum Vergleichen."""
        return {
            key: self._editor_value(editor)
            for key, (_definition, editor) in self._editors.items()
        }

    # Schreiben ----------------------------------------------------------------

    def apply(self) -> tuple[list[str], list[str], bool]:
        """Schreibt geaenderte Werte.

        Liefert (geschrieben, abgewiesen, neustart_noetig). Jede Einstellung
        wird einzeln geschrieben: ein ungueltiger Wert darf die uebrigen nicht
        mitreissen.

        **Warum hier jede Ausnahme abgefangen wird und nicht nur die
        Pruefungsfehler.** Genau daran ist der Dialog im Betrieb gescheitert:
        das Schreiben lief in einen `TimeoutError`, der hier nicht vorgesehen
        war. Er verliess die Schleife, alle noch nicht bearbeiteten
        Einstellungen wurden nie geschrieben, und das Programm zeigte den
        Absturzdialog. Sichtbar wurde es als "die Hintergrundfarbe wird nicht
        gespeichert" - sie stand in der Aufzaehlung hinter der Farbe, die den
        Fehler ausloeste.

        Das Versprechen des Absatzes darueber gilt fuer JEDEN Fehlschlag, nicht
        nur fuer den ungueltigen Wert.
        """
        written: list[str] = []
        rejected: list[str] = []
        restart = False

        for key, (definition, editor) in self._editors.items():
            new_value = self._editor_value(editor)
            if new_value is None:
                continue
            try:
                coerced = definition.coerce(new_value)
            except (ValidationError, ValueError) as exc:
                rejected.append(definition.description + ": " + str(exc))
                continue
            if coerced == self._settings.get(key):
                continue
            try:
                self._settings.set(key, coerced)
            except Exception as exc:  # noqa: BLE001 - siehe Docstring
                rejected.append(definition.description + ": " + str(exc))
                continue
            written.append(key)
            restart = restart or definition.requires_restart

        return written, rejected, restart

    def _on_accept(self) -> None:
        written, rejected, restart = self.apply()

        if rejected:
            QMessageBox.warning(
                self,
                "Nicht uebernommen",
                "Diese Einstellungen wurden nicht uebernommen:\n\n" + "\n".join(rejected),
            )
            self.load()
            return

        if any(key in OVERRIDABLE for key in written):
            # Sofort sichtbar machen: eine Farbe, die erst nach einem Neustart
            # erscheint, laedt zum Herumprobieren nicht ein.
            self._theme.refresh()

        if self._needs_reanalysis(written):
            self._offer_reanalysis()

        if restart:
            QMessageBox.information(
                self,
                "Neustart noetig",
                "Eine geaenderte Einstellung wird erst beim naechsten"
                " Programmstart wirksam.",
            )
        self.accept()

    @staticmethod
    def _needs_reanalysis(written: list[str]) -> bool:
        return any(key.split(".", 1)[0] in REANALYSE_GROUPS for key in written)

    def _offer_reanalysis(self) -> None:
        """Bietet die Neuanalyse an, statt sie stillschweigend zu unterlassen.

        Der Anstoss ist noetig, weil die Analyse sonst nicht laeuft und die
        Aenderung wirkungslos bliebe. Gefragt wird trotzdem: bei einer grossen
        Bibliothek dauert der Lauf, und wer nur die Schriftgroesse im Editor
        sucht, soll nicht ungefragt eine Minute warten.
        """
        if self._analysis is None:
            return
        answer = QMessageBox.question(
            self,
            "Analyse erneuern",
            "Die geaenderten Werte betreffen die Analyse. Ohne einen neuen Lauf"
            " bleiben Familien, Varianten und Baeume unveraendert.\n\n"
            "Analyse jetzt erneuern?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._analysis.start_analysis()

    def _restore_defaults(self) -> None:
        """Setzt die Felder des sichtbaren Reiters auf die Standardwerte.

        Bewusst nur den sichtbaren Reiter: ein Knopf, der unbemerkt auch die
        Wurzelverzeichnisse leert, waere eine Falle. Geschrieben wird erst mit
        OK.
        """
        page = self._pages.currentWidget()
        if page is None:
            return
        title = self._titles[self._pages.currentIndex()]
        answer = QMessageBox.question(
            self,
            "Standardwerte",
            'Alle Einstellungen im Reiter "' + title + '" auf die Standardwerte'
            " zuruecksetzen?\n\nGeschrieben wird erst mit OK.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        for key, (definition, editor) in self._editors.items():
            if editor.isVisibleTo(page) or self._belongs_to(editor, page):
                self._set_editor(editor, definition, definition.default)

    @staticmethod
    def _belongs_to(editor: QWidget, page: QWidget) -> bool:
        parent = editor.parentWidget()
        while parent is not None:
            if parent is page:
                return True
            parent = parent.parentWidget()
        return False
