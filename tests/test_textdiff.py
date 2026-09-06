"""Der zeilenweise Vergleichskern - ohne Qt, ohne Datenbank pruefbar.

Die Klasse `TestAuftrag` bildet die zehn Faelle aus der Beauftragung Punkt fuer
Punkt ab. Sie sind der Massstab, an dem der Umbau gemessen wird; laeuft einer
von ihnen nicht, ist die Ausrichtung falsch, egal wie gut die Ansicht aussieht.

Der wichtigste ist TEST 7 - eine Einfuegung MITTEN in einem Aenderungsblock.
Genau daran scheitert jeder Vergleich, der Zeilen der Reihe nach paart: er
stellt "Enable=true" gegen "Ramp=50", und ab dort ist alles Weitere um eine
Zeile verschoben.
"""

from __future__ import annotations

import pytest

from app.analysis.textdiff import (
    CompareOptions,
    DiffStatus,
    SegmentStatus,
    compare_texts,
)
from app.analysis.textdiff.options import (
    ALGO_PATIENCE,
    ALGO_SEQUENCE_MATCHER,
    INTRA_CHARACTER,
)

LF = chr(10)
CRLF = chr(13) + LF


def paare(result) -> list[tuple[str | None, str | None]]:
    """Die Ausrichtung als schlichte Liste - so, wie der Auftrag sie notiert."""
    return [
        (
            row.left_line.text if row.left_line else None,
            row.right_line.text if row.right_line else None,
        )
        for row in result.rows
    ]


def zustaende(result) -> list[str]:
    return [row.status.value for row in result.rows]


class TestAuftrag:
    """Die zehn Faelle aus der Beauftragung, §22."""

    def test_1_identische_dateien(self) -> None:
        text = LF.join(["A", "B", "C"])
        ergebnis = compare_texts(text, text)
        assert ergebnis.difference_count == 0
        assert ergebnis.is_identical
        assert zustaende(ergebnis) == ["equal"] * 3

    def test_2_einzelne_geaenderte_zeile(self) -> None:
        ergebnis = compare_texts(
            LF.join(["A", "Speed=100", "C"]),
            LF.join(["A", "Speed=120", "C"]),
        )
        assert paare(ergebnis) == [
            ("A", "A"),
            ("Speed=100", "Speed=120"),
            ("C", "C"),
        ]
        assert ergebnis.difference_count == 1
        assert ergebnis.differences[0].type is DiffStatus.CHANGED

    def test_3_eingefuegte_zeile(self) -> None:
        ergebnis = compare_texts(
            LF.join(["A", "B", "C"]),
            LF.join(["A", "B", "X", "C"]),
        )
        assert paare(ergebnis) == [
            ("A", "A"),
            ("B", "B"),
            (None, "X"),
            ("C", "C"),
        ]
        assert ergebnis.differences[0].type is DiffStatus.ADDED

    def test_4_geloeschte_zeile(self) -> None:
        ergebnis = compare_texts(
            LF.join(["A", "B", "X", "C"]),
            LF.join(["A", "B", "C"]),
        )
        assert paare(ergebnis) == [
            ("A", "A"),
            ("B", "B"),
            ("X", None),
            ("C", "C"),
        ]
        assert ergebnis.differences[0].type is DiffStatus.REMOVED

    def test_5_mehrere_einfuegungen(self) -> None:
        ergebnis = compare_texts(
            LF.join(["A", "B", "C"]),
            LF.join(["A", "X", "Y", "B", "C"]),
        )
        assert paare(ergebnis) == [
            ("A", "A"),
            (None, "X"),
            (None, "Y"),
            ("B", "B"),
            ("C", "C"),
        ]
        assert ergebnis.difference_count == 1, (
            "Zwei zusammenhaengende Einfuegungen sind EIN Unterschied"
        )

    def test_6_aehnliche_geaenderte_zeile(self) -> None:
        """Eine geaenderte Zeile plus Wortunterschied nur um die Zahl herum."""
        ergebnis = compare_texts("Speed = 100;", "Speed = 120;")
        assert len(ergebnis.rows) == 1
        zeile = ergebnis.rows[0]
        assert zeile.status is DiffStatus.CHANGED

        geaendert = [
            teil for teil in zeile.left_segments if teil.status is not SegmentStatus.EQUAL
        ]
        assert [teil.text for teil in geaendert] == ["100"]
        geaendert_rechts = [
            teil for teil in zeile.right_segments if teil.status is not SegmentStatus.EQUAL
        ]
        assert [teil.text for teil in geaendert_rechts] == ["120"]

    def test_7_einfuegung_innerhalb_eines_replace_bereichs(self) -> None:
        """Der entscheidende Fall.

        Wer der Reihe nach paart, stellt "Enable=true" gegen "Ramp=50" und
        verschiebt ab dort alles Weitere. Die Aehnlichkeitszuordnung erkennt,
        dass Speed zu Speed gehoert und Ramp neu ist.
        """
        ergebnis = compare_texts(
            LF.join(["A", "Speed=100", "Enable=true", "Timeout=500", "Z"]),
            LF.join(["A", "Speed=120", "Ramp=50", "Enable=true", "Timeout=500", "Z"]),
        )
        assert paare(ergebnis) == [
            ("A", "A"),
            ("Speed=100", "Speed=120"),
            (None, "Ramp=50"),
            ("Enable=true", "Enable=true"),
            ("Timeout=500", "Timeout=500"),
            ("Z", "Z"),
        ]

    def test_8_komplett_unterschiedliche_dateien(self) -> None:
        """Darf nicht abstuerzen und muss einen konsistenten Block liefern."""
        ergebnis = compare_texts(
            LF.join("ABCDEFGH"),
            LF.join(["zeta", "eta", "theta", "iota"]),
        )
        assert ergebnis.difference_count >= 1
        assert len(ergebnis.rows) >= 8
        # Jede reale Zeile beider Seiten kommt genau einmal vor.
        links = [r.left_line.text for r in ergebnis.rows if r.left_line]
        rechts = [r.right_line.text for r in ergebnis.rows if r.right_line]
        assert links == list("ABCDEFGH")
        assert rechts == ["zeta", "eta", "theta", "iota"]

    def test_9_leere_datei_gegen_nichtleere(self) -> None:
        ergebnis = compare_texts("", LF.join(["A", "B", "C"]))
        assert paare(ergebnis) == [(None, "A"), (None, "B"), (None, "C")]
        assert ergebnis.differences[0].type is DiffStatus.ADDED

    def test_10_leerraum_ignorieren(self) -> None:
        ergebnis = compare_texts(
            "Speed = 100;",
            "Speed    =    100;",
            CompareOptions(ignore_whitespace_changes=True),
        )
        assert ergebnis.difference_count == 0, "Der Unterschied ist unerheblich"
        assert ergebnis.rows[0].status is DiffStatus.IGNORED, (
            "Unerheblich heisst nicht gleich - der Text unterscheidet sich"
        )


class TestKeineZeilennummernpaarung:
    """§4: der naive Ansatz ist ausdruecklich verboten."""

    def test_eine_einfuegung_verschiebt_nicht_alles_weitere(self) -> None:
        ergebnis = compare_texts(
            LF.join(["A", "B", "C", "D", "E"]),
            LF.join(["A", "B", "X", "C", "D", "E"]),
        )
        assert paare(ergebnis) == [
            ("A", "A"),
            ("B", "B"),
            (None, "X"),
            ("C", "C"),
            ("D", "D"),
            ("E", "E"),
        ]
        assert zustaende(ergebnis).count("changed") == 0, (
            "Eine Einfuegung darf keine Aenderungen erzeugen"
        )

    def test_beide_seiten_haben_gleich_viele_zeilen(self) -> None:
        """Die Voraussetzung dafuer, dass Zeile neben Zeile stehen kann."""
        for links, rechts in (
            (LF.join("ABC"), LF.join(["A", "X", "Y", "Z", "B", "C"])),
            (LF.join("ABCDEFG"), "A"),
            ("", LF.join("ABC")),
            ("", ""),
        ):
            ergebnis = compare_texts(links, rechts)
            assert len(ergebnis.left_display_text().split(LF)) == len(
                ergebnis.right_display_text().split(LF)
            )

    def test_gemeinsamer_schluss_steht_auf_gleicher_hoehe(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Gleich.", "Nur A.", "Schluss."]),
            LF.join(["Gleich.", "Nur B eins.", "Nur B zwei.", "Schluss."]),
        )
        schluss = [
            row.display_index
            for row in ergebnis.rows
            if row.left_text == "Schluss." or row.right_text == "Schluss."
        ]
        assert len(set(schluss)) == 1, "Der gemeinsame Schluss steht nicht buendig"


class TestPlatzhalter:
    """§8: fehlende Zeilen sind virtuell und werden niemals gespeichert."""

    def test_platzhalter_hat_keine_zeilennummer(self) -> None:
        ergebnis = compare_texts(LF.join(["A", "C"]), LF.join(["A", "B", "C"]))
        nummern = ergebnis.left_line_numbers()
        assert nummern == (1, None, 2), (
            "Der Platzhalter darf keine Nummer tragen, und die Zeile danach"
            " behaelt ihre eigene"
        )

    def test_platzhalter_steht_nicht_im_quelltext(self) -> None:
        links = LF.join(["A", "C"])
        ergebnis = compare_texts(links, LF.join(["A", "B", "C"]))
        assert ergebnis.left_source_text() == links
        assert ergebnis.left_display_text() != links, (
            "In der ANZEIGE muss der Platzhalter dagegen vorkommen"
        )

    def test_platzhalter_wird_gemeldet(self) -> None:
        ergebnis = compare_texts(LF.join(["A", "C"]), LF.join(["A", "B", "C"]))
        assert ergebnis.left_placeholder_rows() == (1,)
        assert ergebnis.right_placeholder_rows() == ()


class TestBloecke:
    """§6: zusammenhaengende Aenderungen sind EIN Unterschied."""

    def test_drei_zeilen_hintereinander_sind_ein_block(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "a1", "a2", "a3", "Fuss"]),
            LF.join(["Kopf", "b1", "b2", "b3", "Fuss"]),
        )
        assert ergebnis.difference_count == 1
        assert ergebnis.differences[0].row_count == 3

    def test_zwei_getrennte_bloecke(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "a1", "Mitte", "a2", "Fuss"]),
            LF.join(["Kopf", "b1", "Mitte", "b2", "Fuss"]),
        )
        assert ergebnis.difference_count == 2

    def test_block_kennt_seine_zeilennummern(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "a1", "a2", "Fuss"]),
            LF.join(["Kopf", "b1", "b2", "Fuss"]),
        )
        block = ergebnis.differences[0]
        assert (block.left_start, block.left_end) == (2, 3)
        assert (block.right_start, block.right_end) == (2, 3)

    def test_reine_einfuegung_hat_links_keine_zeilennummern(self) -> None:
        ergebnis = compare_texts(
            LF.join(["A", "C"]), LF.join(["A", "B", "C"])
        )
        block = ergebnis.differences[0]
        assert block.left_start is None and block.left_end is None
        assert (block.right_start, block.right_end) == (2, 2)
        assert block.left_slice == (1, 1), (
            "Die Einfuegestelle muss trotzdem bestimmt sein - sonst laesst sich"
            " der Block nicht uebernehmen"
        )

    def test_jede_zeile_kennt_ihren_block(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "a1", "a2", "Fuss"]),
            LF.join(["Kopf", "b1", "b2", "Fuss"]),
        )
        innen = [r for r in ergebnis.rows if r.status is not DiffStatus.EQUAL]
        assert all(r.difference_index == 0 for r in innen)
        aussen = [r for r in ergebnis.rows if r.status is DiffStatus.EQUAL]
        assert all(r.difference_index is None for r in aussen)


class TestNavigation:
    def test_naechster_und_vorheriger(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "a1", "Mitte", "a2", "Fuss"]),
            LF.join(["Kopf", "b1", "Mitte", "b2", "Fuss"]),
        )
        erster = ergebnis.next_difference(-1)
        assert erster is not None and erster.index == 0
        zweiter = ergebnis.next_difference(erster.last_row)
        assert zweiter is not None and zweiter.index == 1
        assert ergebnis.next_difference(zweiter.last_row) is None
        assert ergebnis.previous_difference(zweiter.first_row).index == 0

    def test_block_an_zeile(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "a1", "Fuss"]), LF.join(["Kopf", "b1", "Fuss"])
        )
        assert ergebnis.block_at_row(1) is not None
        assert ergebnis.block_at_row(0) is None


class TestIntraLine:
    """§10 und §11: Wort- und Zeichenebene."""

    def test_abschnitte_ueberdecken_die_zeile_lueckenlos(self) -> None:
        """Der Renderer soll keine Luecken selbst ausrechnen muessen."""
        ergebnis = compare_texts("Speed = 100 units;", "Speed = 120 units;")
        zeile = ergebnis.rows[0]
        for teile, text in (
            (zeile.left_segments, zeile.left_line.text),
            (zeile.right_segments, zeile.right_line.text),
        ):
            assert teile[0].start == 0
            assert teile[-1].end == len(text)
            for vorher, nachher in zip(teile, teile[1:]):
                assert vorher.end == nachher.start, "Luecke zwischen Abschnitten"

    def test_positionen_zaehlen_ab_zeilenanfang(self) -> None:
        """Der Kern der Sache - keine Dokumentpositionen mehr."""
        ergebnis = compare_texts(
            LF.join(["Eine lange erste Zeile ohne Aenderung.", "Speed = 100;"]),
            LF.join(["Eine lange erste Zeile ohne Aenderung.", "Speed = 120;"]),
        )
        zeile = ergebnis.rows[1]
        for teil in zeile.left_segments:
            assert zeile.left_line.text[teil.start : teil.end] == teil.text

    def test_zeichenmodus_ist_feiner(self) -> None:
        wort = compare_texts("Speed = 100;", "Speed = 120;")
        zeichen = compare_texts(
            "Speed = 100;", "Speed = 120;", CompareOptions(intra_line_mode=INTRA_CHARACTER)
        )
        wort_laenge = sum(
            t.length for t in wort.rows[0].left_segments
            if t.status is not SegmentStatus.EQUAL
        )
        zeichen_laenge = sum(
            t.length for t in zeichen.rows[0].left_segments
            if t.status is not SegmentStatus.EQUAL
        )
        assert zeichen_laenge < wort_laenge

    def test_interpunktion_wird_eigenstaendig_zerlegt(self) -> None:
        """§11: foo(bar, 100) muss vernuenftig zerfallen."""
        ergebnis = compare_texts("foo(bar, 100)", "foo(bar, 120)")
        geaendert = [
            t.text for t in ergebnis.rows[0].right_segments
            if t.status is not SegmentStatus.EQUAL
        ]
        assert geaendert == ["120"], (
            "Klammer und Komma haengen am Wort - die Markierung ist zu grob"
        )

    def test_randleerraum_wird_nicht_mitgefaerbt(self) -> None:
        ergebnis = compare_texts("a = 1", "a = 22")
        for teil in ergebnis.rows[0].right_segments:
            if teil.status is not SegmentStatus.EQUAL:
                assert teil.text == teil.text.strip()

    def test_geloeschtes_wort_bekommt_eigenen_zustand(self) -> None:
        """Damit die Ansicht "weggefallen" von "ausgetauscht" trennen kann."""
        ergebnis = compare_texts("alpha beta gamma", "alpha gamma")
        zustand = {
            t.status for t in ergebnis.rows[0].left_segments
            if t.status is not SegmentStatus.EQUAL
        }
        assert SegmentStatus.REMOVED in zustand

    def test_sehr_lange_zeilen_werden_nicht_verfeinert(self) -> None:
        """Schutz gegen quadratische Laufzeit bei einem Prompt ohne Umbrueche.

        Solche Zeilen gibt es in dieser Bibliothek: ein Prompt, der ohne
        Umbrueche gespeichert wurde, ist eine einzige Zeile aus zwanzigtausend
        Zeichen. Die Zeile bleibt flaechig markiert - richtig, nur weniger
        genau.
        """
        from app.analysis.textdiff.intraline import MAX_REFINE_LENGTH

        links = "a" * (MAX_REFINE_LENGTH + 10)
        rechts = links[:-5] + "bbbbb"  # aehnlich genug, um gepaart zu werden
        ergebnis = compare_texts(links, rechts)

        assert ergebnis.rows[0].status is DiffStatus.CHANGED
        teile = ergebnis.rows[0].left_segments
        assert len(teile) == 1 and teile[0].status is SegmentStatus.CHANGED, (
            "Die Laengengrenze hat nicht gegriffen"
        )


class TestOptionen:
    def test_optionen_veraendern_den_originaltext_nicht(self) -> None:
        """§3: normalisiert wird nur fuer die Entscheidung."""
        links = "SPEED = 100;"
        ergebnis = compare_texts(
            links, "speed = 100;", CompareOptions(ignore_case=True)
        )
        assert ergebnis.rows[0].left_line.text == links
        assert ergebnis.left_source_text() == links

    def test_gross_klein_ignorieren(self) -> None:
        ohne = compare_texts("Speed", "SPEED")
        mit = compare_texts("Speed", "SPEED", CompareOptions(ignore_case=True))
        assert ohne.difference_count == 1
        assert mit.difference_count == 0

    def test_leerzeilen_ignorieren(self) -> None:
        links = LF.join(["A", "", "B"])
        rechts = LF.join(["A", "B"])
        ohne = compare_texts(links, rechts)
        mit = compare_texts(links, rechts, CompareOptions(ignore_blank_lines=True))
        assert ohne.difference_count == 1
        assert mit.difference_count == 0
        assert mit.rows[1].status is DiffStatus.IGNORED

    def test_jeder_leerraum_ignorieren(self) -> None:
        mit = compare_texts(
            "a b c", "abc", CompareOptions(ignore_whitespace=True)
        )
        assert mit.difference_count == 0

    def test_zeilenenden_werden_per_vorgabe_nicht_verglichen(self) -> None:
        """Die Bibliothek enthaelt beides nebeneinander."""
        ergebnis = compare_texts(
            CRLF.join(["A", "B"]), LF.join(["A", "B"])
        )
        assert ergebnis.difference_count == 0

    def test_zeilenenden_koennen_verglichen_werden(self) -> None:
        ergebnis = compare_texts(
            CRLF.join(["A", "B"]),
            LF.join(["A", "B"]),
            CompareOptions(compare_eol=True),
        )
        assert ergebnis.difference_count > 0

    def test_hohe_schwelle_unterdrueckt_die_wortmarkierung(self) -> None:
        """Die Schwelle steuert, wann eine Zuordnung noch etwas AUSSAGT.

        Die Zeilen stehen weiterhin nebeneinander - innerhalb eines
        Ersetzungsblocks ist die Reihenfolge die einzige Auskunft, die es gibt.
        Aber die wortweise Verfeinerung unterbleibt, weil sie bei zwei
        unverwandten Zeilen nur zufaellige Uebereinstimmungen in Artikeln und
        Satzzeichen markieren wuerde.
        """
        locker = compare_texts(
            "Speed = 100;", "Speed = 120;", CompareOptions(similarity_threshold=0.5)
        )
        streng = compare_texts(
            "Speed = 100;", "Speed = 120;", CompareOptions(similarity_threshold=1.0)
        )
        assert locker.rows[0].status is DiffStatus.CHANGED
        assert streng.rows[0].status is DiffStatus.CHANGED
        assert locker.rows[0].left_segments, "Bei 0,5 muss verfeinert werden"
        assert not streng.rows[0].left_segments, "Bei 1,0 darf nicht verfeinert werden"

    def test_unaehnliche_zeilen_erzeugen_kein_konfetti(self) -> None:
        """Zwei Zeilen ohne Verwandtschaft, die nur nebeneinander liegen."""
        ergebnis = compare_texts(
            LF.join(["Kopf", "Ein voellig anderer Satz ueber Licht.", "Fuss"]),
            LF.join(["Kopf", "Rendering at 8k, sharp focus, detailed.", "Fuss"]),
        )
        zeile = ergebnis.rows[1]
        assert zeile.status is DiffStatus.CHANGED
        assert not zeile.left_segments, (
            "Unverwandte Zeilen duerfen keine Wortmarkierungen bekommen"
        )


class TestVerfahren:
    """§5: der Algorithmus muss austauschbar sein."""

    @pytest.mark.parametrize("algorithmus", [ALGO_SEQUENCE_MATCHER, ALGO_PATIENCE])
    def test_beide_verfahren_liefern_dieselbe_ausrichtung(self, algorithmus) -> None:
        ergebnis = compare_texts(
            LF.join(["A", "B", "C", "D", "E"]),
            LF.join(["A", "B", "X", "C", "D", "E"]),
            CompareOptions(algorithm=algorithmus),
        )
        assert paare(ergebnis) == [
            ("A", "A"), ("B", "B"), (None, "X"), ("C", "C"), ("D", "D"), ("E", "E")
        ]

    def test_patience_bei_wiederkehrenden_bausteinen(self) -> None:
        """Der Fall dieser Bibliothek: 41 % teilen denselben Textbaustein.

        Ein Verfahren, das den laengsten gemeinsamen Block sucht, kann bei so
        viel Wiederholung die falsche Wiederholung zuordnen. Patience laesst
        mehrdeutige Zeilen als Anker gar nicht erst zu.
        """
        baustein = ["REFERENCE IMAGE GUIDANCE", "STRUCTURAL LOCK", ""]
        links = LF.join(baustein + ["Motiv A"] + baustein + ["Ende"])
        rechts = LF.join(baustein + ["Motiv B"] + baustein + ["Ende"])
        ergebnis = compare_texts(
            links, rechts, CompareOptions(algorithm=ALGO_PATIENCE)
        )
        assert ergebnis.difference_count == 1
        geaendert = [r for r in ergebnis.rows if r.status is DiffStatus.CHANGED]
        assert len(geaendert) == 1
        assert geaendert[0].left_line.text == "Motiv A"

    def test_unbekanntes_verfahren_faellt_auf_die_vorgabe_zurueck(self) -> None:
        ergebnis = compare_texts(
            LF.join(["A", "B"]),
            LF.join(["A", "C"]),
            CompareOptions(algorithm="gibt-es-nicht"),
        )
        assert ergebnis.difference_count == 1


class TestVerschobeneBloecke:
    """§12: MOVED, und ausdruecklich abschaltbar."""

    def test_per_vorgabe_abgeschaltet(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Verschiebbarer Absatz", "Mitte", "Ende"]),
            LF.join(["Mitte", "Ende", "Verschiebbarer Absatz"]),
        )
        assert not any(r.status is DiffStatus.MOVED for r in ergebnis.rows)

    def test_eingeschaltet_erkennt_die_verschiebung(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Verschiebbarer Absatz", "Mitte", "Ende"]),
            LF.join(["Mitte", "Ende", "Verschiebbarer Absatz"]),
            CompareOptions(detect_moved=True),
        )
        verschoben = [r for r in ergebnis.rows if r.status is DiffStatus.MOVED]
        assert len(verschoben) == 2
        assert verschoben[0].moved_partner == verschoben[1].display_index

    def test_mehrdeutiges_bleibt_unangetastet(self) -> None:
        """Bei wiederkehrendem Text waere jede Zuordnung geraten."""
        ergebnis = compare_texts(
            LF.join(["Wiederholung hier", "Wiederholung hier", "A"]),
            LF.join(["A", "Wiederholung hier", "Wiederholung hier"]),
            CompareOptions(detect_moved=True),
        )
        # Kein Absturz, und keine erfundene Paarung ueber mehrdeutige Zeilen.
        for row in ergebnis.rows:
            if row.status is DiffStatus.MOVED:
                assert row.moved_partner is not None


class TestZeilenzerlegung:
    def test_abschliessender_umbruch_erzeugt_keine_zeile(self) -> None:
        ergebnis = compare_texts("A" + LF + "B" + LF, "A" + LF + "B" + LF)
        assert len(ergebnis.left_lines) == 2

    def test_leerer_text_hat_null_zeilen(self) -> None:
        ergebnis = compare_texts("", "")
        assert ergebnis.left_lines == () and ergebnis.right_lines == ()
        assert ergebnis.rows == ()
        assert ergebnis.is_identical

    def test_zeilenenden_werden_je_zeile_gemerkt(self) -> None:
        gemischt = "A" + CRLF + "B" + LF + "C"
        ergebnis = compare_texts(gemischt, gemischt)
        assert [z.eol for z in ergebnis.left_lines] == [CRLF, LF, ""]

    def test_alleinstehender_wagenruecklauf(self) -> None:
        ergebnis = compare_texts("A" + chr(13) + "B", "A" + chr(13) + "B")
        assert len(ergebnis.left_lines) == 2
        assert ergebnis.is_identical


class TestKeineGuiAbhaengigkeit:
    """§21: im Modell steht kein einziges Farb- oder Qt-Wort."""

    def test_das_paket_importiert_kein_qt(self) -> None:
        """Geprueft werden IMPORTE, nicht der Wortlaut.

        Eine Textsuche nach "QColor" faende auch den Kommentar, der erklaert,
        warum dort kein QColor stehen darf. Der Syntaxbaum kennt den
        Unterschied zwischen einem Import und einer Erwaehnung.
        """
        import ast
        import pathlib

        paket = (
            pathlib.Path(__file__).resolve().parent.parent
            / "app" / "analysis" / "textdiff"
        )
        verstoesse: list[str] = []
        for datei in sorted(paket.rglob("*.py")):
            baum = ast.parse(datei.read_text(encoding="utf-8"))
            for knoten in ast.walk(baum):
                namen: list[str] = []
                if isinstance(knoten, ast.Import):
                    namen = [alias.name for alias in knoten.names]
                elif isinstance(knoten, ast.ImportFrom) and knoten.module:
                    namen = [knoten.module]
                for name in namen:
                    if name.split(".")[0] in ("PyQt6", "PySide6", "qtpy"):
                        verstoesse.append(datei.name + ": " + name)

        assert not verstoesse, "Der Vergleichskern kennt Qt:\n  " + "\n  ".join(
            verstoesse
        )

    def test_keine_farbfelder_im_modell(self) -> None:
        """§21: `DifferenceBlock.color = QColor(...)` ist ausdruecklich verboten."""
        import dataclasses

        from app.analysis.textdiff.model import (
            AlignedRow,
            DiffSegment,
            DifferenceBlock,
        )

        verboten = {"color", "background", "format", "brush", "pen", "font"}
        for klasse in (AlignedRow, DiffSegment, DifferenceBlock):
            felder = {f.name for f in dataclasses.fields(klasse)}
            assert not (felder & verboten), (
                klasse.__name__ + " traegt ein Darstellungsfeld: "
                + str(felder & verboten)
            )

    def test_zustaende_sind_zeichenketten_keine_farben(self) -> None:
        ergebnis = compare_texts("a", "b")
        assert ergebnis.rows[0].status.value == "changed"
        assert not hasattr(ergebnis.rows[0], "color")
        assert not hasattr(ergebnis.differences[0], "color")


class TestGrossePrompts:
    """§23: keine quadratischen Vollvergleiche."""

    def test_langer_prompt_bleibt_zuegig(self) -> None:
        import time

        links = LF.join("Zeile " + str(n) for n in range(2000))
        rechts = LF.join(
            "Zeile " + str(n) + ("!" if n % 200 == 0 else "") for n in range(2000)
        )
        begonnen = time.perf_counter()
        ergebnis = compare_texts(links, rechts)
        gebraucht = time.perf_counter() - begonnen

        assert ergebnis.difference_count == 10
        assert gebraucht < 5.0, (
            "2000 Zeilen brauchten " + format(gebraucht, ".1f") + " s"
        )

    def test_grosser_ersetzungsblock_faellt_auf_positionsweise_zurueck(self) -> None:
        """Bei zwei voellig unverwandten Prompts gibt es nichts zuzuordnen."""
        import time

        links = LF.join("links " + str(n) for n in range(400))
        rechts = LF.join("rechts " + str(n) for n in range(400))
        begonnen = time.perf_counter()
        ergebnis = compare_texts(links, rechts)
        gebraucht = time.perf_counter() - begonnen

        assert len(ergebnis.rows) == 400
        assert gebraucht < 10.0, (
            "Der Notnagel greift nicht: " + format(gebraucht, ".1f") + " s"
        )


class TestZusammenfassung:
    def test_identisch_wird_benannt(self) -> None:
        assert "identisch" in compare_texts("A", "A").summary()

    def test_anzahl_wird_genannt(self) -> None:
        text = compare_texts(
            LF.join(["Kopf", "a1", "Mitte", "a2", "Fuss"]),
            LF.join(["Kopf", "b1", "Mitte", "b2", "Fuss"]),
        ).summary()
        assert "2 Unterschiede" in text

    def test_ignorierte_werden_genannt(self) -> None:
        text = compare_texts(
            "Speed = 100;",
            "Speed    =    100;",
            CompareOptions(ignore_whitespace_changes=True),
        ).summary()
        assert "ignoriert" in text
