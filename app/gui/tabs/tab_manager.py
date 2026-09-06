"""Verwaltung der dynamischen Tabs.

Drei Aufgaben, die sonst im Hauptfenster verstreut liegen wuerden:

* Doppeloeffnungen verhindern. Jeder Tab traegt einen Schluessel (etwa
  "search:golden sunlight" oder "detail:4711"). Ist er schon offen, wird er in
  den Vordergrund geholt statt ein zweites Mal erzeugt.
* Das Bibliothekstab schuetzen. Es ist fest und traegt deshalb kein
  Schliessen-Kreuz (Spezifikation 01).
* Obergrenze durchsetzen. Jeder Ergebnistab haelt ein eigenes Kachelmodell samt
  Vorschaubildern im Speicher; unbegrenzt viele offene Tabs waeren ein
  schleichendes Speicherleck. Der aelteste nicht angesehene Tab weicht.
* Ungesicherte Arbeit nicht wegwerfen. Ein Tab darf `has_unsaved_changes()`
  anbieten; der Vergleichstab tut das, seit sich Bloecke von Hand uebernehmen
  lassen. Ohne diese Abfrage verschwand eine zusammengefuehrte Fassung beim
  Klick auf das Schliessen-Kreuz ohne Rueckfrage - die Methode gab es, gefragt
  hat sie niemand.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QTabWidget, QWidget

MAX_DYNAMIC_TABS = 12


class TabManager(QObject):
    """tab_opened(widget), tab_closed(key)"""

    tab_opened = pyqtSignal(object)
    tab_closed = pyqtSignal(str)

    def __init__(self, tabs: QTabWidget, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tabs = tabs
        self._by_key: dict[str, QWidget] = {}
        self._order: list[str] = []
        tabs.tabCloseRequested.connect(self._on_close_requested)

    # Abfragen -----------------------------------------------------------------

    def is_open(self, key: str) -> bool:
        return key in self._by_key

    def widget_for(self, key: str) -> QWidget | None:
        return self._by_key.get(key)

    def keys(self) -> list[str]:
        return list(self._order)

    def count(self) -> int:
        return len(self._by_key)

    def current(self) -> QWidget | None:
        return self._tabs.currentWidget()

    def dynamic_widgets(self) -> list[QWidget]:
        return [self._by_key[key] for key in self._order]

    # Oeffnen und Schliessen ---------------------------------------------------

    def open(self, widget: QWidget, key: str, title: str, *, focus: bool = True) -> QWidget:
        """Oeffnet einen Tab oder holt den bereits offenen nach vorn."""
        existing = self._by_key.get(key)
        if existing is not None:
            widget.deleteLater()
            if focus:
                self._tabs.setCurrentWidget(existing)
            return existing

        index = self._tabs.addTab(widget, _elide(title))
        self._tabs.setTabToolTip(index, title)
        self._by_key[key] = widget
        self._order.append(key)
        self._enforce_limit()
        if focus:
            self._tabs.setCurrentWidget(widget)
        self.tab_opened.emit(widget)
        return widget

    def focus(self, key: str) -> bool:
        widget = self._by_key.get(key)
        if widget is None:
            return False
        self._tabs.setCurrentWidget(widget)
        return True

    def close_key(self, key: str) -> bool:
        widget = self._by_key.get(key)
        if widget is None:
            return False
        index = self._tabs.indexOf(widget)
        if index >= 0:
            self._remove(index, key, widget)
        return True

    def close_all(self) -> int:
        """Schliesst alle dynamischen Tabs. Fragt EINMAL nach, wenn Arbeit offen ist.

        Bei "Nein" bleiben genau die Tabs mit ungesicherten Aenderungen stehen
        und die uebrigen gehen zu. Ein Sammelbefehl, der wegen eines einzigen
        Tabs gar nichts tut, waere die schlechtere Antwort.
        """
        offen = [k for k in self._order if _hat_ungesichertes(self._by_key.get(k))]
        verwerfen = True
        if offen:
            verwerfen = self._frage_verwerfen(
                str(len(offen)) + " Tab(s) enthalten nicht gespeicherte Aenderungen."
                " Trotzdem schliessen?"
            )
        closed = 0
        for key in list(self._order):
            if not verwerfen and key in offen:
                continue
            if self.close_key(key):
                closed += 1
        return closed

    def rename(self, key: str, title: str) -> None:
        widget = self._by_key.get(key)
        if widget is None:
            return
        index = self._tabs.indexOf(widget)
        if index >= 0:
            self._tabs.setTabText(index, _elide(title))
            self._tabs.setTabToolTip(index, title)

    # Intern -------------------------------------------------------------------

    def _on_close_requested(self, index: int) -> None:
        widget = self._tabs.widget(index)
        key = self._key_of(widget)
        if key is None:
            return  # das feste Bibliothekstab
        if _hat_ungesichertes(widget) and not self._frage_verwerfen(
            "Dieser Tab enthaelt nicht gespeicherte Aenderungen. Trotzdem schliessen?"
        ):
            return
        self._remove(index, key, widget)

    def _frage_verwerfen(self, text: str) -> bool:
        antwort = QMessageBox.question(
            self._tabs,
            "Nicht gespeicherte Aenderungen",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return antwort == QMessageBox.StandardButton.Yes

    def _remove(self, index: int, key: str, widget: QWidget | None) -> None:
        self._tabs.removeTab(index)
        self._by_key.pop(key, None)
        if key in self._order:
            self._order.remove(key)
        if widget is not None:
            widget.deleteLater()
        self.tab_closed.emit(key)

    def _key_of(self, widget: QWidget | None) -> str | None:
        for key, candidate in self._by_key.items():
            if candidate is widget:
                return key
        return None

    def _enforce_limit(self) -> None:
        """Draengt ueberzaehlige Tabs hinaus - aber nie ungesicherte Arbeit.

        Diese Verdraengung laeuft ohne Zutun des Benutzers ab. Sie darf
        deshalb weder fragen (ein Dialog aus dem Nichts) noch stillschweigend
        etwas wegwerfen. Bleiben nur noch Tabs mit offenen Aenderungen, gilt
        die Obergrenze eben voruebergehend nicht; sie schuetzt den Speicher,
        und das wiegt weniger als eine verlorene Zusammenfuehrung.
        """
        current = self._tabs.currentWidget()
        while len(self._order) > MAX_DYNAMIC_TABS:
            for key in list(self._order):
                widget = self._by_key.get(key)
                if widget is not current and not _hat_ungesichertes(widget):
                    self.close_key(key)
                    break
            else:
                break


def _hat_ungesichertes(widget: QWidget | None) -> bool:
    """Ob ein Tab offene Aenderungen meldet.

    Absichtlich ueber die Form statt ueber einen Typ: der Tabverwalter kennt
    weder den Vergleichstab noch den Editor und soll das auch nicht. Wer die
    Frage beantworten kann, beantwortet sie; wer sie nicht kennt, hat nichts
    zu verlieren.
    """
    frage = getattr(widget, "has_unsaved_changes", None)
    if not callable(frage):
        return False
    try:
        return bool(frage())
    except Exception:  # noqa: BLE001 - ein defekter Tab darf nicht das
        return False    # Schliessen blockieren


def _elide(title: str, limit: int = 28) -> str:
    """Haelt die Tableiste lesbar, der vollstaendige Titel steht im Tooltip."""
    text = title.strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"
