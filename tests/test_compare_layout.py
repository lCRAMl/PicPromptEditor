"""Gleiche Zeilenhoehen und Gleichlauf - der gemeldete Fehler.

Gemeldet war: "Aktuell sind die Zeilen anfangs auf gleicher Hoehe, nach ein
bisschen Scrollen verschiebt sich ein Text immer weiter nach unten."

Die Ursache waren zwei Fehler, die sich addierten:

**1. Der Gleichlauf rechnete anteilig.** Wert durch Reichweite, auf die andere
Reichweite umgerechnet. Das stimmt bei 0 % und bei 100 % exakt und dazwischen
nicht - genau das Bild, das gemeldet wurde.

**2. Der Zeilenumbruch war eingeschaltet.** Die Ausrichtung sorgt fuer gleich
viele LOGISCHE Zeilen. Bei Umbruch belegt dieselbe Zeile links vier und rechts
sechs ANZEIGEZEILEN, weil rechts ein paar Woerter mehr stehen. Damit sind die
Dokumente unterschiedlich hoch, die Rollbalken haben verschiedene Reichweiten,
und Fehler 1 wird sichtbar.

Die Tests hier pruefen beide Ebenen: die reine Rechnung ohne Qt, und die
tatsaechlichen Pixelhoehen in einem echten Widget. Die zweite Ebene ist die
wichtigere - die Rechnung kann stimmen und trotzdem nie angewandt werden.
"""

from __future__ import annotations

import pytest

from app.analysis.textdiff import compare_texts
from app.gui.compare.diff_pane import LEFT, RIGHT, DiffPane
from app.gui.compare.row_heights import (
    EPSILON,
    cumulative_offsets,
    equalizing_margins,
    is_aligned,
)
from app.gui.compare.scroll_sync import synchronized_value
from app.gui.theme import _LIGHT

LF = chr(10)

#: Zwei Prompts, deren Zeilen unterschiedlich lang sind. Genau daran zeigte
#: sich der Fehler: bei schmalen Spalten bricht die laengere Seite oefter um.
LANG_A = LF.join(
    [
        "Gemeinsame Einleitung ueber Licht und Schatten in der Szene.",
        "Ein kurzer Satz.",
        "Speed = 100;",
        "Noch eine gemeinsame Zeile, die etwas laenger ausfaellt als andere.",
        "Schluss.",
    ]
)
LANG_B = LF.join(
    [
        "Gemeinsame Einleitung ueber Licht und Schatten in der Szene, dazu"
        " ausfuehrliche Angaben zur Beleuchtung, zur Tageszeit und zur"
        " Stimmung des gesamten Bildes, damit diese Zeile deutlich mehr"
        " Platz braucht als ihr Gegenstueck.",
        "Ein kurzer Satz.",
        "Speed = 120;",
        "Noch eine gemeinsame Zeile, die etwas laenger ausfaellt als andere.",
        "Schluss.",
    ]
)


class TestRechnung:
    """Die reine Funktion - ohne Qt pruefbar."""

    def test_kuerzere_seite_bekommt_die_differenz(self) -> None:
        links, rechts = equalizing_margins([10.0, 20.0, 10.0], [10.0, 50.0, 10.0])
        assert links == [0.0, 30.0, 0.0]
        assert rechts == [0.0, 0.0, 0.0]

    def test_beide_richtungen(self) -> None:
        links, rechts = equalizing_margins([50.0, 10.0], [10.0, 50.0])
        assert links == [0.0, 40.0]
        assert rechts == [40.0, 0.0]

    def test_gleiche_hoehen_bekommen_keinen_abstand(self) -> None:
        links, rechts = equalizing_margins([10.0, 20.0], [10.0, 20.0])
        assert links == [0.0, 0.0] and rechts == [0.0, 0.0]

    def test_winzige_unterschiede_bleiben_unbehandelt(self) -> None:
        """Fliesskommamasse weichen um Bruchteile ab - das ist keine Ausrichtung."""
        links, rechts = equalizing_margins([10.0], [10.0 + EPSILON / 2])
        assert links == [0.0] and rechts == [0.0]

    def test_nach_dem_ausgleich_ist_jede_zeile_gleich_hoch(self) -> None:
        a = [12.0, 48.0, 12.0, 36.0]
        b = [12.0, 12.0, 24.0, 36.0]
        links, rechts = equalizing_margins(a, b)
        assert is_aligned(
            [h + m for h, m in zip(a, links)],
            [h + m for h, m in zip(b, rechts)],
        )

    def test_ungleich_lange_listen_stuerzen_nicht_ab(self) -> None:
        """Ein Zeichenereignis ist der falsche Ort fuer eine Ausnahme."""
        links, rechts = equalizing_margins([10.0, 20.0, 30.0], [10.0])
        assert len(links) == 3 and len(rechts) == 1

    def test_leere_eingabe(self) -> None:
        assert equalizing_margins([], []) == ([], [])

    def test_is_aligned_prueft_jede_zeile(self) -> None:
        """Gleiche Gesamthoehe genuegt nicht - in der Mitte darf nichts driften."""
        assert not is_aligned([10.0, 30.0], [30.0, 10.0]), (
            "Gesamthoehe gleich, Zeilen aber vertauscht"
        )

    def test_kumulierte_raender(self) -> None:
        assert cumulative_offsets([10.0, 20.0, 5.0]) == [0.0, 10.0, 30.0]

    def test_gleiche_hoehen_ergeben_gleiche_raender(self) -> None:
        """Der Nachweis: Zeile n beginnt auf beiden Seiten an derselben Stelle."""
        a = [12.0, 48.0, 12.0]
        b = [12.0, 12.0, 24.0]
        links, rechts = equalizing_margins(a, b)
        assert cumulative_offsets([h + m for h, m in zip(a, links)]) == (
            cumulative_offsets([h + m for h, m in zip(b, rechts)])
        )


class TestGleichlauf:
    def test_gleiche_reichweite_driftet_nicht(self) -> None:
        """Der Regelfall nach dem Ausgleich - dieselbe Zahl, ohne Rechnung."""
        for schritt in range(0, 5000, 13):
            assert synchronized_value(schritt, 4999, 4999) == schritt

    def test_der_alte_weg_haette_gedriftet(self) -> None:
        """Zum Vergleich: anteilig bei ungleicher Reichweite.

        Bei 0 und beim Maximum stimmt es, dazwischen nicht. Der Test haelt
        fest, warum der Ausgleich noetig ist und nicht nur schoen.
        """
        assert synchronized_value(0, 1000, 1400) == 0
        assert synchronized_value(1000, 1000, 1400) == 1400
        assert synchronized_value(500, 1000, 1400) == 700, (
            "In der Mitte laeuft die eine Seite der anderen um 200 px davon"
        )


class TestEchteWidgets:
    """Die Pixelhoehen in einem echten QTextEdit.

    Die Rechnung kann stimmen und trotzdem nie angewandt werden - deshalb
    wird hier nachgemessen, nicht nur nachgerechnet.
    """

    @pytest.fixture
    def flaechen(self, qapp):
        ergebnis = compare_texts(LANG_A, LANG_B)
        links = DiffPane(LEFT, _LIGHT)
        rechts = DiffPane(RIGHT, _LIGHT)
        for flaeche in (links, rechts):
            flaeche.resize(320, 400)  # schmal genug fuer echten Umbruch
            flaeche.show_result(ergebnis)
        qapp.processEvents()
        return links, rechts, ergebnis

    def _ausgleichen(self, links, rechts):
        a = links.base_heights()
        b = rechts.base_heights()
        links_rand, rechts_rand = equalizing_margins(a, b)
        links.apply_row_margins(links_rand)
        rechts.apply_row_margins(rechts_rand)
        return a, b

    def test_beide_seiten_haben_gleich_viele_bloecke(self, flaechen) -> None:
        links, rechts, ergebnis = flaechen
        assert links.document().blockCount() == rechts.document().blockCount()
        assert links.document().blockCount() == ergebnis.row_count

    def test_ohne_ausgleich_sind_die_hoehen_verschieden(self, flaechen) -> None:
        """Die Voraussetzung des Tests - sonst prueft er nichts."""
        links, rechts, _ = flaechen
        a, b = links.base_heights(), rechts.base_heights()
        assert not is_aligned(a, b), (
            "Die Testdaten brechen nicht unterschiedlich um - der Test taugt"
            " so nicht als Nachweis"
        )

    def test_nach_dem_ausgleich_ist_jede_zeile_gleich_hoch(self, flaechen) -> None:
        """Der eigentliche Nachweis."""
        links, rechts, _ = flaechen
        self._ausgleichen(links, rechts)

        a = links.effective_heights()
        b = rechts.effective_heights()
        assert is_aligned(a, b, tolerance=1.5), (
            "Zeilenhoehen weichen ab:\n  links : " + str([round(h, 1) for h in a])
            + "\n  rechts: " + str([round(h, 1) for h in b])
        )

    def test_jede_zeile_beginnt_auf_gleicher_hoehe(self, flaechen) -> None:
        """Was der Benutzer tatsaechlich sieht.

        Gleiche Hoehen sind Mittel zum Zweck; der Zweck ist, dass Zeile n
        links und rechts an derselben Stelle beginnt - und zwar auch die
        letzte, nicht nur die erste.
        """
        links, rechts, ergebnis = flaechen
        self._ausgleichen(links, rechts)

        for index in range(ergebnis.row_count):
            oben_links = links.row_top(index)
            oben_rechts = rechts.row_top(index)
            assert abs(oben_links - oben_rechts) <= 1.5, (
                "Zeile " + str(index) + " beginnt links bei "
                + format(oben_links, ".1f") + " und rechts bei "
                + format(oben_rechts, ".1f")
            )

    def test_der_abstand_summiert_sich_nicht_auf(self, flaechen) -> None:
        """Mehrfaches Ausgleichen darf die Zeilen nicht wachsen lassen.

        Genau das passiert, wenn vor dem Messen die alten Abstaende nicht
        zurueckgesetzt werden - bei jedem Fensterwechsel kaeme einer dazu.
        """
        links, rechts, _ = flaechen
        self._ausgleichen(links, rechts)
        erste = links.effective_heights()
        for _ in range(3):
            self._ausgleichen(links, rechts)
        assert links.effective_heights() == pytest.approx(erste, abs=0.5)

    def test_ohne_umbruch_braucht_es_keinen_ausgleich(self, flaechen) -> None:
        """Eine logische Zeile ist dann genau eine Anzeigezeile."""
        links, rechts, _ = flaechen
        for flaeche in (links, rechts):
            flaeche.set_wrap(False)
        links.document().adjustSize()
        rechts.document().adjustSize()

        assert is_aligned(links.base_heights(), rechts.base_heights(), tolerance=1.5)

    def test_platzhalter_bekommt_keine_zeilennummer(self, flaechen) -> None:
        links, _rechts, ergebnis = flaechen
        for zeile in ergebnis.left_placeholder_rows():
            assert links._numbers[zeile] is None

    def test_zeilennummern_ueberspringen_den_platzhalter(self, qapp) -> None:
        """Die Nummer danach zaehlt weiter, als waere nichts gewesen."""
        ergebnis = compare_texts(LF.join(["A", "C"]), LF.join(["A", "B", "C"]))
        flaeche = DiffPane(LEFT, _LIGHT)
        flaeche.show_result(ergebnis)
        assert flaeche._numbers == (1, None, 2)

    def test_kopieren_liefert_den_quelltext_ohne_platzhalter(self, qapp) -> None:
        """Wer den Prompt herauskopiert, will den Prompt - nicht die Anzeige."""
        links_text = LF.join(["A", "C"])
        ergebnis = compare_texts(links_text, LF.join(["A", "B", "C"]))
        flaeche = DiffPane(LEFT, _LIGHT)
        flaeche.show_result(ergebnis)

        assert flaeche.toPlainText() == LF.join(["A", "", "C"]), "Anzeige"
        flaeche._copy_source_text()
        from PyQt6.QtGui import QGuiApplication

        assert QGuiApplication.clipboard().text() == links_text

    def test_der_aktuelle_unterschied_hebt_sich_ab(self, qapp) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "a1", "Fuss"]), LF.join(["Kopf", "b1", "Fuss"])
        )
        flaeche = DiffPane(LEFT, _LIGHT)
        flaeche.show_result(ergebnis)

        normal = flaeche._row_color(ergebnis.rows[1])
        flaeche.set_current_block(0)
        hervorgehoben = flaeche._row_color(ergebnis.rows[1])
        assert hervorgehoben != normal
        assert hervorgehoben == _LIGHT.diff_current

    def test_platzhalter_schlaegt_den_zustand(self, qapp) -> None:
        """Eine Zeile, die es hier nicht gibt, ist grau - unabhaengig vom Rest."""
        ergebnis = compare_texts(LF.join(["A", "C"]), LF.join(["A", "B", "C"]))
        flaeche = DiffPane(LEFT, _LIGHT)
        flaeche.show_result(ergebnis)
        assert flaeche._row_color(ergebnis.rows[1]) == _LIGHT.diff_placeholder

    def test_gemalte_farben_stimmen_mit_der_rangfolge(self, qapp) -> None:
        """Nachgemessen an den PIXELN, nicht an `_row_color`.

        Dieser Test haette einen Fehler gefunden, den die Zustandstests nicht
        fanden: die Zeilenfarbe lag zuerst als Extraauswahl mit
        `FullWidthSelection` auf der Zeile. Eine solche Auswahl wird ueber die
        Zeichenformate gezeichnet - die Wortmarkierung war damit vollstaendig
        verdeckt, obwohl das Modell sie korrekt lieferte und `_row_color` die
        richtige Farbe meldete. Sichtbar war es nur im Bild.

        Geprueft wird die Rangfolge aus §14 des Auftrags:

            Wortunterschied  ueber  Zeilenfarbe  ueber  Grundfarbe
        """
        flaeche = DiffPane(LEFT, _LIGHT, font_size=10)
        flaeche.resize(400, 60)
        flaeche.show_result(compare_texts("Speed = 100;", "Speed = 120;"))
        flaeche.show()
        for _ in range(30):
            qapp.processEvents()

        bild = flaeche.grab().toImage()
        hoehe = 14
        gefunden: dict[str, int] = {}
        for x in range(flaeche.gutter_width() + 2, 390):
            gefunden[bild.pixelColor(x, hoehe).name()] = (
                gefunden.get(bild.pixelColor(x, hoehe).name(), 0) + 1
            )

        assert _LIGHT.diff_block.name() in gefunden, (
            "Die Zeilenfarbe fehlt ganz. Gefunden: " + str(sorted(gefunden))
        )
        assert _LIGHT.diff_word.name() in gefunden, (
            "Die Wortmarkierung wird verdeckt - genau der Fehler, den die"
            " Extraauswahl verursacht hat. Gefunden: " + str(sorted(gefunden))
        )
        assert gefunden[_LIGHT.diff_block.name()] > gefunden[_LIGHT.diff_word.name()], (
            "Die Wortmarkierung deckt mehr Flaeche ab als die Zeile - dann"
            " markiert sie nicht ein Wort, sondern die halbe Zeile"
        )

    def test_zeilenfarbe_reicht_bis_zum_rand(self, qapp) -> None:
        """WinMerge faerbt die ganze Zeile, nicht nur bis zum letzten Zeichen."""
        flaeche = DiffPane(LEFT, _LIGHT, font_size=10)
        flaeche.resize(400, 60)
        flaeche.show_result(compare_texts("ab", "cd"))
        flaeche.show()
        for _ in range(30):
            qapp.processEvents()

        bild = flaeche.grab().toImage()
        assert bild.pixelColor(380, 14).name() == _LIGHT.diff_block.name(), (
            "Weit rechts vom Text endet die Faerbung"
        )

    def test_platzhalter_wird_grau_gemalt(self, qapp) -> None:
        """Eine Zeile ohne ein einziges Zeichen - ueber Zeichenformate unmoeglich."""
        flaeche = DiffPane(LEFT, _LIGHT, font_size=10)
        flaeche.resize(400, 90)
        flaeche.show_result(compare_texts(LF.join(["A", "C"]), LF.join(["A", "B", "C"])))
        flaeche.show()
        for _ in range(30):
            qapp.processEvents()

        mitte = flaeche.row_top(1) + flaeche.effective_heights()[1] / 2
        bild = flaeche.grab().toImage()
        assert bild.pixelColor(200, int(mitte)).name() == _LIGHT.diff_placeholder.name()

    def test_leeres_ergebnis_stuerzt_nicht_ab(self, qapp) -> None:
        """Ein leeres Qt-Dokument hat immer genau einen Block, nicht null."""
        flaeche = DiffPane(LEFT, _LIGHT)
        flaeche.show_result(None)
        assert flaeche.toPlainText() == ""
        assert flaeche._numbers == ()
        assert len(flaeche.base_heights()) == 1
        assert len(flaeche.effective_heights()) == 1
        assert flaeche.row_top(0) >= 0.0
