"""Uebernahme zwischen den Seiten und das Zurueckschreiben auf die Platte.

Zwei Ebenen, die getrennt geprueft werden:

`merge.py` rechnet auf dem QUELLMODELL und liefert neuen Text. Die Falle dort
sind die Platzhalter: sie stehen in der Anzeige, aber in keiner Datei. Wer den
gerenderten Text kopiert, erzeugt auf der Zielseite Leerzeilen, die niemand
geschrieben hat - und beim naechsten Vergleich sind sie echte Unterschiede.
Aus einer Uebernahme wird so bei jedem Durchgang neuer Muell.

`prompt_writer.py` schreibt die Datei. Die Fallen dort sind der abgebrochene
Schreibvorgang, der fremde Schreiber und das Encoding. Die Tests arbeiten auf
echten Dateien in tmp_path, nicht auf Attrappen - bei atomarem Umbenennen ist
gerade das Dateisystemverhalten der Pruefgegenstand.
"""

from __future__ import annotations

import os

import pytest

from app.analysis.textdiff import compare_texts, merge_block
from app.analysis.textdiff.merge import TO_LEFT, TO_RIGHT, merge_all
from app.core.errors import ErrorReporter
from app.filesystem.nas_health import NasHealthMonitor
from app.filesystem.prompt_writer import (
    CONFLICT,
    FAILED,
    SAVED,
    TEMP_SUFFIX,
    PromptWriter,
)

LF = chr(10)
CR = chr(13)
CRLF = CR + LF


class TestUebernahme:
    def test_geaenderte_zeile_nach_rechts(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "Speed = 100;", "Fuss"]),
            LF.join(["Kopf", "Speed = 120;", "Fuss"]),
        )
        uebernommen = merge_block(ergebnis, ergebnis.differences[0], TO_RIGHT)
        assert uebernommen.right_text == LF.join(["Kopf", "Speed = 100;", "Fuss"])
        assert uebernommen.left_text == ergebnis.left_source_text()
        assert uebernommen.changed_side == "right"

    def test_geaenderte_zeile_nach_links(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "Speed = 100;", "Fuss"]),
            LF.join(["Kopf", "Speed = 120;", "Fuss"]),
        )
        uebernommen = merge_block(ergebnis, ergebnis.differences[0], TO_LEFT)
        assert uebernommen.left_text == LF.join(["Kopf", "Speed = 120;", "Fuss"])
        assert uebernommen.changed_side == "left"

    def test_platzhalter_wird_zu_einer_einfuegung(self) -> None:
        """Der Kern der Sache: der Platzhalter darf nicht mitkopiert werden."""
        ergebnis = compare_texts(
            LF.join(["A", "C"]),
            LF.join(["A", "B", "C"]),
        )
        block = ergebnis.differences[0]
        assert block.left_slice == (1, 1), "Einfuegestelle, nichts zu entfernen"

        uebernommen = merge_block(ergebnis, block, TO_LEFT)
        assert uebernommen.left_text == LF.join(["A", "B", "C"])
        assert uebernommen.lines_removed == 0
        assert uebernommen.lines_inserted == 1
        assert "" not in uebernommen.left_text.split(LF), (
            "Der Platzhalter ist als Leerzeile mitgewandert"
        )

    def test_platzhalter_wird_zu_einem_loeschen(self) -> None:
        ergebnis = compare_texts(
            LF.join(["A", "B", "C"]),
            LF.join(["A", "C"]),
        )
        uebernommen = merge_block(ergebnis, ergebnis.differences[0], TO_LEFT)
        assert uebernommen.left_text == LF.join(["A", "C"])
        assert uebernommen.lines_removed == 1
        assert uebernommen.lines_inserted == 0

    def test_mehrere_zeilen_auf_einmal(self) -> None:
        ergebnis = compare_texts(
            LF.join(["Kopf", "a1", "a2", "a3", "Fuss"]),
            LF.join(["Kopf", "b1", "Fuss"]),
        )
        uebernommen = merge_block(ergebnis, ergebnis.differences[0], TO_RIGHT)
        assert uebernommen.right_text == LF.join(["Kopf", "a1", "a2", "a3", "Fuss"])

    def test_uebernahme_ist_wiederholbar(self) -> None:
        """Nach der Uebernahme muss der neue Vergleich den Block los sein.

        Das ist die Probe darauf, dass keine Platzhalter mitgewandert sind:
        waeren sie es, faende der naechste Vergleich sie als neue
        Unterschiede, und die Zahl der Bloecke saenke nie auf null.
        """
        links = LF.join(["Kopf", "Speed = 100;", "Enable = TRUE;", "Fuss"])
        rechts = LF.join(["Kopf", "Speed = 120;", "Ramp = 500;", "Enable = TRUE;", "Fuss"])

        ergebnis = compare_texts(links, rechts)
        durchgaenge = 0
        while ergebnis.difference_count and durchgaenge < 10:
            uebernommen = merge_block(ergebnis, ergebnis.real_differences[0], TO_RIGHT)
            ergebnis = compare_texts(uebernommen.left_text, uebernommen.right_text)
            durchgaenge += 1

        assert ergebnis.is_identical
        assert ergebnis.right_source_text() == links

    def test_alles_auf_einmal(self) -> None:
        links = LF.join(["Kopf", "a1", "Mitte", "a2", "Fuss"])
        rechts = LF.join(["Kopf", "b1", "Mitte", "b2", "b3", "Fuss"])
        ergebnis = compare_texts(links, rechts)

        uebernommen = merge_all(ergebnis, TO_RIGHT)
        assert uebernommen.right_text == links, (
            "Von hinten nach vorn gearbeitet - sonst zeigen die Bereiche der"
            " noch offenen Bloecke ins Leere"
        )

    def test_alles_in_die_andere_richtung(self) -> None:
        links = LF.join(["Kopf", "a1", "Mitte", "a2", "Fuss"])
        rechts = LF.join(["Kopf", "b1", "Mitte", "b2", "b3", "Fuss"])
        ergebnis = compare_texts(links, rechts)
        assert merge_all(ergebnis, TO_LEFT).left_text == rechts

    def test_unbekannte_richtung_wird_gemeldet(self) -> None:
        ergebnis = compare_texts("a", "b")
        with pytest.raises(ValueError):
            merge_block(ergebnis, ergebnis.differences[0], "seitwaerts")


class TestSchreiber:
    @pytest.fixture
    def schreiber(self) -> PromptWriter:
        reporter = ErrorReporter()
        return PromptWriter(NasHealthMonitor(reporter), reporter)

    @pytest.fixture
    def datei(self, tmp_path):
        pfad = tmp_path / "prompt.txt"
        pfad.write_bytes(("Alt" + LF + "Zeile").encode("utf-8"))
        return pfad

    def _signatur(self, pfad) -> tuple[int, int]:
        info = os.stat(pfad)
        return info.st_size, info.st_mtime_ns

    def test_schreibt_den_text(self, schreiber, datei) -> None:
        groesse, mtime = self._signatur(datei)
        ergebnis = schreiber.save(
            str(datei), "Neu" + LF + "Zeile",
            expected_size=groesse, expected_mtime=mtime,
        )
        assert ergebnis.status == SAVED
        assert datei.read_text(encoding="utf-8") == "Neu" + LF + "Zeile"

    def test_liefert_die_neue_signatur(self, schreiber, datei) -> None:
        """Ohne sie meldete der naechste Speichervorgang einen Konflikt."""
        groesse, mtime = self._signatur(datei)
        ergebnis = schreiber.save(
            str(datei), "Deutlich laengerer Text",
            expected_size=groesse, expected_mtime=mtime,
        )
        assert ergebnis.file_size == os.stat(datei).st_size
        assert ergebnis.modified_time > 0

        # Zweiter Durchgang mit der gemeldeten Signatur - kein Konflikt.
        zweites = schreiber.save(
            str(datei), "Noch etwas anderes",
            expected_size=ergebnis.file_size, expected_mtime=ergebnis.modified_time,
        )
        assert zweites.status == SAVED

    def test_fremde_aenderung_wird_erkannt(self, schreiber, datei) -> None:
        """Sonst verwirft das Speichern stillschweigend fremde Arbeit."""
        groesse, mtime = self._signatur(datei)
        datei.write_bytes(b"Jemand anders war schneller und schrieb mehr Text")

        ergebnis = schreiber.save(
            str(datei), "Meine Fassung",
            expected_size=groesse, expected_mtime=mtime,
        )
        assert ergebnis.status == CONFLICT
        assert not ergebnis.ok
        assert datei.read_bytes().startswith(b"Jemand anders"), (
            "Die fremde Fassung wurde ueberschrieben"
        )

    def test_erzwingen_uebergeht_die_pruefung(self, schreiber, datei) -> None:
        groesse, mtime = self._signatur(datei)
        datei.write_bytes(b"Jemand anders war schneller")

        ergebnis = schreiber.save(
            str(datei), "Meine Fassung",
            expected_size=groesse, expected_mtime=mtime, force=True,
        )
        assert ergebnis.status == SAVED
        assert datei.read_text(encoding="utf-8") == "Meine Fassung"

    def test_ohne_vergleichsstand_wird_nicht_geprueft(self, schreiber, datei) -> None:
        """Prompts aus der Zeit vor dieser Spalte tragen 0."""
        ergebnis = schreiber.save(
            str(datei), "Neu", expected_size=0, expected_mtime=0
        )
        assert ergebnis.status == SAVED

    def test_verschwundene_datei_ist_ein_konflikt(self, schreiber, datei) -> None:
        """Vielleicht wurde sie bewusst geloescht - dann nicht neu anlegen."""
        groesse, mtime = self._signatur(datei)
        datei.unlink()
        ergebnis = schreiber.save(
            str(datei), "Neu", expected_size=groesse, expected_mtime=mtime
        )
        assert ergebnis.status == CONFLICT
        assert not datei.exists()

    def test_zeilenenden_bleiben_erhalten(self, schreiber, tmp_path) -> None:
        """Sonst aendert das Speichern jede einzelne Zeile der Datei."""
        pfad = tmp_path / "windows.txt"
        pfad.write_bytes(CRLF.join(["A", "B"]).encode("utf-8"))
        schreiber.save(str(pfad), LF.join(["A", "C"]), eol=CRLF)
        assert pfad.read_bytes() == CRLF.join(["A", "C"]).encode("utf-8")

    def test_vorhandene_windows_enden_werden_nicht_verdoppelt(
        self, schreiber, tmp_path
    ) -> None:
        """Der Text kommt oft SCHON mit Windows-Enden an.

        `decode_bytes` vereinheitlicht sie nicht - die Datenbank haelt sie so,
        wie sie in der Datei standen. Ein schlichtes Ersetzen von "\\n" durch
        CRLF macht daraus CR CRLF: jede Zeile bekommt eine Leerzeile
        hinterher, und die Datei waechst bei jedem Speichern um ihre eigene
        Zeilenzahl.
        """
        pfad = tmp_path / "schon_crlf.txt"
        pfad.write_bytes(b"alt")
        schreiber.save(str(pfad), CRLF.join(["A", "B", "C"]), eol=CRLF)

        roh = pfad.read_bytes()
        assert roh == CRLF.join(["A", "B", "C"]).encode("utf-8")
        assert (CR + CRLF).encode("utf-8") not in roh, "Wagenrueckluf verdoppelt"
        assert roh.count(b"\n") == 2, "Zusaetzliche Zeilen entstanden"

    def test_wiederholtes_speichern_laesst_die_datei_nicht_wachsen(
        self, schreiber, tmp_path
    ) -> None:
        """Die Probe aufs Exempel: dreimal dasselbe speichern."""
        pfad = tmp_path / "stabil.txt"
        pfad.write_bytes(b"alt")
        text = CRLF.join(["Erste", "Zweite", "Dritte"])

        groessen = []
        for _ in range(3):
            schreiber.save(str(pfad), text, eol=CRLF)
            groessen.append(pfad.stat().st_size)
            text = pfad.read_bytes().decode("utf-8")  # so kommt er zurueck
        assert len(set(groessen)) == 1, "Die Datei waechst bei jedem Speichern: " + str(
            groessen
        )

    def test_encoding_bleibt_erhalten(self, schreiber, tmp_path) -> None:
        """Eine cp1252-Datei als UTF-8 zurueckzuschreiben zerstoert die Umlaute."""
        pfad = tmp_path / "cp1252.txt"
        text = "Gr" + chr(0xFC) + "n"
        pfad.write_bytes(text.encode("cp1252"))
        schreiber.save(str(pfad), text + "er", encoding="cp1252")
        assert pfad.read_bytes() == (text + "er").encode("cp1252")
        assert pfad.read_bytes().decode("cp1252") == text + "er"

    def test_bom_bleibt_erhalten(self, schreiber, tmp_path) -> None:
        pfad = tmp_path / "bom.txt"
        pfad.write_bytes("A".encode("utf-8-sig"))
        schreiber.save(str(pfad), "AB", encoding="utf-8-sig")
        assert pfad.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_unbekanntes_encoding_verliert_den_text_nicht(
        self, schreiber, tmp_path
    ) -> None:
        """Ein Prompt in UTF-8 ist besser als ein verlorener."""
        pfad = tmp_path / "seltsam.txt"
        pfad.write_bytes(b"A")
        ergebnis = schreiber.save(str(pfad), "Neu", encoding="gibt-es-nicht-42")
        assert ergebnis.status == SAVED
        assert pfad.read_bytes() == b"Neu"

    def test_keine_nachbardatei_bleibt_liegen(self, schreiber, datei) -> None:
        schreiber.save(str(datei), "Neu")
        uebrig = [p.name for p in datei.parent.iterdir() if TEMP_SUFFIX in p.name]
        assert not uebrig, "Die Nachbardatei wurde nicht umbenannt: " + str(uebrig)

    def test_unschreibbares_ziel_meldet_statt_zu_werfen(
        self, schreiber, tmp_path
    ) -> None:
        """Ein Fehler beim Schreiben ist ein Betriebszustand, keine Ausnahme."""
        pfad = tmp_path / "gibt-es-nicht" / "tief" / "prompt.txt"
        ergebnis = schreiber.save(str(pfad), "Neu")
        assert ergebnis.status == FAILED
        assert not ergebnis.ok

    def test_die_alte_fassung_ueberlebt_einen_fehlschlag(
        self, schreiber, datei, monkeypatch
    ) -> None:
        """Der Grund fuer das Umbenennen statt des direkten Schreibens.

        Bricht der Vorgang ab, muss der alte Inhalt vollstaendig dastehen -
        nicht halb ueberschrieben.
        """
        alt = datei.read_bytes()

        def scheitert(*_a, **_k):
            raise OSError("Verbindung weg")

        monkeypatch.setattr(os, "replace", scheitert)
        ergebnis = schreiber.save(str(datei), "Neu und viel laenger als vorher")

        assert ergebnis.status == FAILED
        assert datei.read_bytes() == alt, "Die alte Fassung wurde beschaedigt"


class TestUebernahmeUndSchreiben:
    """Die beiden Ebenen zusammen - vom Block bis in die Datei."""

    def test_uebernommener_block_landet_ohne_platzhalter_in_der_datei(
        self, tmp_path
    ) -> None:
        reporter = ErrorReporter()
        schreiber = PromptWriter(NasHealthMonitor(reporter), reporter)

        links = LF.join(["Kopf", "Speed = 100;", "Fuss"])
        rechts = LF.join(["Kopf", "Speed = 120;", "Ramp = 500;", "Fuss"])
        ziel = tmp_path / "rechts.txt"
        ziel.write_bytes(rechts.encode("utf-8"))

        ergebnis = compare_texts(links, rechts)
        uebernommen = merge_all(ergebnis, TO_RIGHT)
        schreiber.save(str(ziel), uebernommen.right_text)

        geschrieben = ziel.read_text(encoding="utf-8")
        assert geschrieben == links
        assert "" not in geschrieben.split(LF), (
            "Eine Platzhalter-Leerzeile ist in die Datei gelangt"
        )
