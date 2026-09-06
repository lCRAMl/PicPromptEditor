"""Nachladen der Vorschaubilder beim Scrollen.

Hintergrund: die Ansicht bestimmte ihren sichtbaren Bereich frueher durch
Abtasten - `indexAt()` auf der oberen linken und der unteren rechten Ecke des
Sichtfensters. Das ging still schief. Die Ansicht legt ihre Elemente
stapelweise aus (`LayoutMode.Batched`, `batchSize` 200), und ausserhalb des
bereits ausgelegten Stapels trifft `indexAt()` nichts. Die untere Ecke lieferte
deshalb praktisch immer einen ungueltigen Index, das Ende fiel auf den
Ersatzwert 200 zurueck - und wer weiter als Zeile 200 gescrollt hatte, bekam
einen Bereich, dessen Anfang hinter seinem Ende lag. `items_in_range()` liefert
dann eine leere Liste: **es wurde gar kein Vorschaubild mehr nachgeladen.**

In einer Bibliothek mit 3.481 Bildern in einem Ordner heisst das: die ersten
paar hundert Kacheln bekommen ein Bild, der Rest bleibt fuer immer leer.

Die Ansicht rechnet den Bereich jetzt aus Rollbalkenstand und Rastermass aus.
Alle Kacheln sind gleich gross (`setUniformItemSizes`), also trifft die
Rechnung immer.
"""

from __future__ import annotations

import pytest

from app.dto.models import ImageItem, ResultSet
from app.gui.library.thumbnail_view import ThumbnailView
from app.gui.theme import _LIGHT
from app.services.selection_service import SelectionService

ANZAHL = 3000


def make_item(index: int) -> ImageItem:
    return ImageItem(
        id=index,
        folder_id=1,
        filename="p%d.jpg" % index,
        stem="p%d" % index,
        full_path="Z:/lib/p%d.jpg" % index,
        thumbnail_key="k%d" % index,
        thumbnail_state="missing",
        prompt_id=None,
        file_size=100,
        modified_time=0.0,
    )


@pytest.fixture
def view(qapp):
    """Eine Ansicht mit dreitausend Kacheln, vollstaendig ausgelegt."""
    import time

    instance = ThumbnailView(_LIGHT, SelectionService())
    instance.thumbnail_model.set_results(
        ResultSet(items=[make_item(i) for i in range(ANZAHL)], origin="test")
    )
    instance.resize(900, 600)
    instance.show()
    # Die stapelweise Auslegung zu Ende laufen lassen, sonst reicht der
    # Rollbalken nur ueber die ersten Stapel.
    deadline = time.time() + 10
    while time.time() < deadline:
        qapp.processEvents()
    yield instance
    instance.deleteLater()


@pytest.fixture
def requested(view):
    """Sammelt die angeforderten Bereiche."""
    seen: list[tuple[int, int]] = []
    view.thumbnail_model.thumbnails_needed.connect(
        lambda items: seen.append((items[0].id, items[-1].id))
    )
    return seen


def scroll_to(qapp, view, value: int) -> None:
    view.verticalScrollBar().setValue(value)
    qapp.processEvents()


class TestSichtbarerBereich:
    def test_am_anfang_wird_der_anfang_geladen(self, qapp, view, requested) -> None:
        scroll_to(qapp, view, 0)
        view.request_visible()
        assert requested, "Am Listenanfang wurde nichts angefordert"
        assert requested[0][0] == 0

    def test_am_ende_wird_das_ende_geladen(self, qapp, view, requested) -> None:
        """Die letzte Kachel muss ein Vorschaubild bekommen koennen."""
        bar = view.verticalScrollBar()
        if bar.maximum() == 0:
            pytest.skip("Die Ansicht ist nicht scrollbar")
        scroll_to(qapp, view, bar.maximum())
        requested.clear()
        view.request_visible()
        assert requested, "Am Listenende wurde nichts angefordert"
        assert requested[-1][1] == ANZAHL - 1, (
            "Die letzten Kacheln bekommen kein Vorschaubild: bis "
            + str(requested[-1][1])
            + " statt " + str(ANZAHL - 1)
        )

    def test_jede_scrollposition_fordert_etwas_an(self, qapp, view, requested) -> None:
        """Der eigentliche Fehler: ab Zeile 200 kam gar nichts mehr.

        Frueher lieferte alles ab etwa einem Viertel der Liste eine leere
        Anforderung, weil Anfang und Ende vertauscht waren.
        """
        bar = view.verticalScrollBar()
        if bar.maximum() == 0:
            pytest.skip("Die Ansicht ist nicht scrollbar")

        leer: list[float] = []
        for schritt in range(21):
            anteil = schritt / 20
            requested.clear()
            scroll_to(qapp, view, int(bar.maximum() * anteil))
            view.request_visible()
            if not requested:
                leer.append(anteil)

        assert not leer, (
            "Keine Anforderung bei " + ", ".join("%.0f %%" % (a * 100) for a in leer)
        )

    def test_der_bereich_folgt_dem_rollbalken(self, qapp, view, requested) -> None:
        """Weiter unten muessen auch spaetere Bilder angefordert werden."""
        bar = view.verticalScrollBar()
        if bar.maximum() == 0:
            pytest.skip("Die Ansicht ist nicht scrollbar")

        anfaenge = []
        for anteil in (0.0, 0.25, 0.5, 0.75, 1.0):
            requested.clear()
            scroll_to(qapp, view, int(bar.maximum() * anteil))
            view.request_visible()
            assert requested
            anfaenge.append(requested[0][0])

        assert anfaenge == sorted(anfaenge), (
            "Der angeforderte Bereich waechst nicht mit dem Rollbalken: "
            + str(anfaenge)
        )
        assert anfaenge[-1] > anfaenge[0], "Ganz unten wird derselbe Bereich geladen wie oben"

    def test_durchscrollen_erreicht_die_ganze_liste(self, qapp, view, requested) -> None:
        """In feinen Schritten muss jede Kachel einmal an die Reihe kommen."""
        bar = view.verticalScrollBar()
        if bar.maximum() == 0:
            pytest.skip("Die Ansicht ist nicht scrollbar")

        abgedeckt: set[int] = set()
        schritte = 120
        for schritt in range(schritte + 1):
            requested.clear()
            scroll_to(qapp, view, int(bar.maximum() * schritt / schritte))
            view.request_visible()
            for erste, letzte in requested:
                abgedeckt.update(range(erste, letzte + 1))

        fehlend = ANZAHL - len(abgedeckt)
        assert fehlend == 0, str(fehlend) + " Bilder wurden beim Durchscrollen nie angefordert"


class TestRastermass:
    def test_rastermass_ist_brauchbar(self, view) -> None:
        step = view._grid_step()
        assert step.width() > 0 and step.height() > 0

    def test_leere_liste_fordert_nichts_an(self, qapp, requested, view) -> None:
        view.thumbnail_model.set_results(ResultSet(items=[], origin="leer"))
        qapp.processEvents()
        requested.clear()
        view.request_visible()
        assert requested == []
