"""Was passiert, wenn die Freigabe mitten im Abgleich wegbricht.

Das ist die Kernzusage von Spezifikation 08 und war bisher nur in Einzelteilen
geprueft: der Breaker soll greifen, der Scan sauber enden und die Anwendung mit
den bereits eingelesenen Daten weiterarbeiten.

Der Ausfall wird ueber os.scandir nachgestellt. Das ist naeher an der
Wirklichkeit als ein geloeschtes Verzeichnis: eine tote SMB-Freigabe
verschwindet nicht, sie antwortet mit einem Fehler - oder erst nach zwanzig
Sekunden gar nicht. Gezaehlt wird deshalb nicht nur das Ergebnis, sondern auch
die Zahl der Zugriffsversuche: jeder einzelne waere im Ernstfall eine
Zeitueberschreitung.
"""

from __future__ import annotations

import os
import time

import pytest
from PIL import Image

from app.core.cancellation import CancellationToken
from app.filesystem.nas_health import BreakerState, NasHealthMonitor, TargetUnavailableError
from app.filesystem.scanner import Scanner
from app.repositories.folder_repo import FolderRepository
from app.repositories.image_repo import ImageRepository
from app.repositories.prompt_repo import PromptRepository

ORDNER = 12


def make_pair(folder, stem: str, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (120, 90, 60)).save(folder / (stem + ".jpg"))
    (folder / (stem + ".txt")).write_text(text, encoding="utf-8")


@pytest.fixture
def library(tmp_path):
    """Genug Ordner, um zu sehen, ob nach dem Ausfall weitergemacht wird."""
    root = tmp_path / "lib"
    for index in range(ORDNER):
        make_pair(root / ("ordner%02d" % index), "bild", "ein prompt %d" % index)
    return root


@pytest.fixture
def setup(database, writer, reporter):
    folders = FolderRepository(database, writer)
    images = ImageRepository(database, writer)
    prompts = PromptRepository(database, writer)
    health = NasHealthMonitor(reporter, failure_threshold=3, cooldown_s=0.2)
    scanner = Scanner(folders, images, prompts, reporter, health)
    return scanner, folders, images, prompts, health


class Freigabe:
    """Eine Freigabe, die sich mitten im Betrieb abschalten laesst."""

    def __init__(self, root, monkeypatch) -> None:
        self.root = os.path.normcase(str(root))
        self.versuche: list[str] = []
        self._online = True
        self._verbleibend: int | None = None
        self._echt = os.scandir
        monkeypatch.setattr(os, "scandir", self._scandir)

    def _betrifft(self, path) -> bool:
        text = os.path.normcase(str(path)).replace("\\\\?\\", "")
        return text.startswith(self.root)

    def _scandir(self, path="."):
        if not self._betrifft(path):
            return self._echt(path)
        self.versuche.append(str(path))
        if self._verbleibend is not None:
            if self._verbleibend > 0:
                self._verbleibend -= 1
            else:
                self._online = False
        if not self._online:
            raise OSError(64, "Der angegebene Netzwerkname ist nicht mehr verfuegbar")
        return self._echt(path)

    def abschalten(self, nach: int | None = None) -> None:
        """Sofort abschalten, oder nach n weiteren erfolgreichen Zugriffen."""
        if nach is None:
            self._online = False
        else:
            self._verbleibend = nach

    def einschalten(self) -> None:
        self._online = True
        self._verbleibend = None


def erster_lauf(scanner, library) -> None:
    scanner.scan_root(str(library), CancellationToken())


class TestAusfallMittenImLauf:
    """Die Freigabe antwortet erst, dann nicht mehr - der interessante Fall."""

    def test_erster_scan_liest_alles_ein(self, setup, library) -> None:
        scanner, _folders, images, _prompts, health = setup
        stats = scanner.scan_root(str(library), CancellationToken())

        assert images.count() == ORDNER
        assert stats.errors == 0
        assert health.state_of(str(library)) is BreakerState.CLOSED

    def test_breaker_oeffnet_und_bleibt_offen(self, setup, library, monkeypatch) -> None:
        scanner, _folders, _images, _prompts, health = setup
        erster_lauf(scanner, library)

        freigabe = Freigabe(library, monkeypatch)
        # Das Einsammeln der Verzeichnisse gelingt noch, dann bricht das Netz weg.
        freigabe.abschalten(nach=ORDNER + 1)
        stats = scanner.scan_root(str(library), CancellationToken())

        assert stats.errors > 0
        assert health.state_of(str(library)) is BreakerState.OPEN, (
            "Der Breaker muss nach dem Ausfall offen bleiben. Wird am Ende des"
            " Scans bedingungslos Erfolg gemeldet, faellt der Zaehler zurueck,"
            " der Schwellwert wird nie erreicht und der Breaker ist wirkungslos."
        )

    def test_der_scan_hoert_auf_zu_fragen(self, setup, library, monkeypatch) -> None:
        """Der eigentliche Zweck: keine zwoelf Zeitueberschreitungen hintereinander.

        Auf einer echten Freigabe kostet jeder Versuch zwanzig Sekunden. Bei
        zwoelf Ordnern waeren das vier Minuten, bei einer echten Bibliothek
        Stunden - deshalb muss der Lauf enden, sobald der Breaker offen ist.
        """
        scanner, _folders, _images, _prompts, _health = setup
        erster_lauf(scanner, library)

        freigabe = Freigabe(library, monkeypatch)
        freigabe.abschalten(nach=ORDNER + 1)
        scanner.scan_root(str(library), CancellationToken())

        fehlversuche = len(freigabe.versuche) - (ORDNER + 1)
        assert fehlversuche <= 4, (
            "Nach dem Oeffnen des Breakers wurde weiter zugegriffen: "
            + str(fehlversuche)
            + " Fehlversuche statt hoechstens vier"
        )

    def test_der_lauf_endet_und_haengt_nicht(self, setup, library, monkeypatch) -> None:
        scanner, _folders, _images, _prompts, _health = setup
        erster_lauf(scanner, library)

        freigabe = Freigabe(library, monkeypatch)
        freigabe.abschalten(nach=ORDNER + 1)
        stats = scanner.scan_root(str(library), CancellationToken())

        assert not stats.cancelled, "Ein Netzausfall ist kein Abbruch durch den Benutzer"
        assert stats.duration >= 0.0

    def test_bereits_eingelesene_daten_bleiben_erhalten(
        self, setup, library, monkeypatch
    ) -> None:
        """Die Anwendung arbeitet mit lokalen Daten weiter."""
        scanner, _folders, images, prompts, _health = setup
        erster_lauf(scanner, library)
        vorher = images.count()

        freigabe = Freigabe(library, monkeypatch)
        freigabe.abschalten(nach=ORDNER + 1)
        scanner.scan_root(str(library), CancellationToken())

        # Nichts wird als geloescht markiert, nur weil das Netz weg ist.
        assert images.count() == vorher
        assert prompts.count() == vorher

    def test_weiterer_scan_kostet_keine_wartezeit_mehr(
        self, setup, library, monkeypatch
    ) -> None:
        scanner, _folders, _images, _prompts, health = setup
        erster_lauf(scanner, library)

        freigabe = Freigabe(library, monkeypatch)
        freigabe.abschalten(nach=ORDNER + 1)
        scanner.scan_root(str(library), CancellationToken())
        assert health.state_of(str(library)) is BreakerState.OPEN

        with pytest.raises(TargetUnavailableError):
            health.guard(str(library / "ordner00"))

        vorher = len(freigabe.versuche)
        stats = scanner.scan_root(str(library), CancellationToken())
        assert stats.errors == 1
        assert len(freigabe.versuche) == vorher, "Trotz offenem Breaker wurde zugegriffen"


class TestFreigabeVonAnfangAnTot:
    def test_ein_lauf_meldet_einen_fehler_und_belastet_den_zaehler(
        self, setup, library, monkeypatch
    ) -> None:
        """Ein einzelner Fehlschlag oeffnet den Breaker noch nicht.

        Das ist Absicht: eine kurze Stoerung soll die Bibliothek nicht fuer die
        Sperrzeit aussperren. Erst der dritte Fehlschlag in Folge tut das.
        """
        scanner, _folders, _images, _prompts, health = setup
        erster_lauf(scanner, library)

        freigabe = Freigabe(library, monkeypatch)
        freigabe.abschalten()
        stats = scanner.scan_root(str(library), CancellationToken())

        assert stats.errors == 1
        assert health.state_of(str(library)) is BreakerState.CLOSED

    def test_drei_fehlschlaege_oeffnen_den_breaker(
        self, setup, library, monkeypatch
    ) -> None:
        scanner, _folders, _images, _prompts, health = setup
        erster_lauf(scanner, library)

        freigabe = Freigabe(library, monkeypatch)
        freigabe.abschalten()
        for _ in range(3):
            scanner.scan_root(str(library), CancellationToken())

        assert health.state_of(str(library)) is BreakerState.OPEN

    def test_die_wurzel_wird_nicht_geleert(self, setup, library, monkeypatch) -> None:
        scanner, _folders, images, _prompts, _health = setup
        erster_lauf(scanner, library)
        vorher = images.count()

        freigabe = Freigabe(library, monkeypatch)
        freigabe.abschalten()
        scanner.scan_root(str(library), CancellationToken())

        assert images.count() == vorher


class TestRueckkehr:
    def test_nach_der_sperrzeit_wird_wieder_versucht(
        self, setup, library, monkeypatch
    ) -> None:
        scanner, _folders, images, _prompts, health = setup
        erster_lauf(scanner, library)

        freigabe = Freigabe(library, monkeypatch)
        freigabe.abschalten(nach=ORDNER + 1)
        scanner.scan_root(str(library), CancellationToken())
        assert health.state_of(str(library)) is BreakerState.OPEN

        freigabe.einschalten()
        time.sleep(0.25)  # Sperrzeit der Fixture
        assert health.state_of(str(library)) is BreakerState.HALF_OPEN

        make_pair(library / "neu", "spaeter", "nach der rueckkehr")
        stats = scanner.scan_root(str(library), CancellationToken())

        assert stats.errors == 0
        assert health.state_of(str(library)) is BreakerState.CLOSED
        assert images.count() == ORDNER + 1

    def test_probe_schliesst_den_breaker(self, setup, library) -> None:
        _scanner, _folders, _images, _prompts, health = setup
        health.register_root(str(library))
        for _ in range(3):
            health.record_failure(str(library), OSError("weg"))
        assert health.state_of(str(library)) is BreakerState.OPEN

        assert health.probe(str(library)) is True
        assert health.state_of(str(library)) is BreakerState.CLOSED
