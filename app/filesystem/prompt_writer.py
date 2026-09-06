"""Promptdateien zurueckschreiben - atomar, mit Konflikterkennung.

Bis hierher war die Anwendung schreibgeschuetzt. Das war kein Versehen: eine
Promptdatei auf einer Netzfreigabe zu ueberschreiben, hat drei Fallen, und alle
drei kosten Daten, wenn man sie uebersieht.

**1. Der abgebrochene Schreibvorgang.** Wird direkt in die Zieldatei geschrieben
und faellt dabei die Verbindung aus, ist die Datei halb geschrieben - der alte
Inhalt ist weg, der neue unvollstaendig. Deshalb wird in eine Nachbardatei
geschrieben und erst danach umbenannt. `os.replace` ist auf einem Dateisystem
atomar: entweder steht der neue Inhalt vollstaendig da, oder der alte
unveraendert.

Die Nachbardatei muss im **selben Verzeichnis** liegen. Ein Umbenennen ueber
eine Geraetegrenze hinweg ist kein Umbenennen, sondern ein Kopieren mit
anschliessendem Loeschen - und damit wieder unterbrechbar.

**2. Der fremde Schreiber.** Zwischen dem Einlesen in die Datenbank und dem
Zurueckschreiben koennen Stunden liegen. Hat jemand die Datei inzwischen
geaendert, wuerde das Speichern seine Arbeit stillschweigend verwerfen.
Deshalb werden Groesse und Aenderungszeit gegen den Stand geprueft, den die
Datenbank kennt.

**3. Das Encoding.** Die Bibliothek enthaelt Dateien in UTF-8, UTF-8 mit BOM
und vereinzelt cp1252. Wird eine cp1252-Datei als UTF-8 zurueckgeschrieben,
sind alle Umlaute und typografischen Zeichen kaputt. Geschrieben wird deshalb
in dem Encoding, das beim Einlesen erkannt wurde - dasselbe gilt fuer die
Zeilenenden.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ErrorReporter
from app.core.paths import long_path
from app.filesystem.nas_health import NasHealthMonitor

LF = chr(10)
CR = chr(13)
CRLF = CR + LF

#: Toleranz beim Vergleich der Aenderungszeit, in Nanosekunden. Dieselbe wie
#: beim Scanner: SMB-Freigaben und FAT-Dateisysteme runden Zeitstempel, und ein
#: Konflikt, den es nicht gibt, ist genauso stoerend wie ein uebersehener.
MTIME_TOLERANCE_NS = 2 * 1_000_000_000

#: Endung der Nachbardatei. Fuehrender Punkt, damit sie im Explorer nicht
#: auffaellt, und ein Name, der nicht nach Prompt aussieht - der Scanner darf
#: sie nicht als Promptdatei einlesen, falls doch einmal eine liegenbleibt.
TEMP_SUFFIX = ".ppe-tmp"

SAVED = "saved"
CONFLICT = "conflict"
FAILED = "failed"
UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """Was beim Schreiben herausgekommen ist.

    Kein Werfen: der Aufrufer laeuft in einem Job, und ein Konflikt ist kein
    Programmfehler, sondern ein normaler Betriebszustand, ueber den der
    Benutzer entscheiden muss.
    """

    status: str
    message: str = ""
    file_size: int = 0
    modified_time: int = 0

    @property
    def ok(self) -> bool:
        return self.status in (SAVED, UNCHANGED)

    @property
    def is_conflict(self) -> bool:
        return self.status == CONFLICT


class PromptWriter:
    def __init__(self, health: NasHealthMonitor, reporter: ErrorReporter) -> None:
        self._health = health
        self._reporter = reporter

    def save(
        self,
        path: str,
        text: str,
        *,
        encoding: str = "utf-8",
        eol: str = "\n",
        expected_size: int = 0,
        expected_mtime: int = 0,
        force: bool = False,
    ) -> WriteOutcome:
        """Schreibt einen Prompttext zurueck.

        `expected_size` und `expected_mtime` sind der Stand, den die Datenbank
        kennt. Stimmen sie nicht mit der Datei ueberein, wird nicht geschrieben,
        sondern CONFLICT gemeldet - es sei denn, `force` ist gesetzt, weil der
        Benutzer die Rueckfrage bereits beantwortet hat.

        Ein `expected_mtime` von 0 heisst "unbekannt" und schaltet die Pruefung
        ab. Das betrifft Prompts, die vor der Einfuehrung dieser Spalte
        indiziert wurden.
        """
        ziel = Path(path)

        if not force:
            konflikt = self._check_conflict(ziel, expected_size, expected_mtime)
            if konflikt is not None:
                return konflikt

        roh = _encode(text, encoding, eol)
        temp = ziel.with_name(ziel.name + TEMP_SUFFIX)

        try:
            self._health.guard(str(ziel))
            with open(long_path(temp), "wb") as handle:
                handle.write(roh)
                # Erst auf die Platte, dann umbenennen. Ohne flush und fsync
                # kann das Umbenennen dem Schreiben vorauslaufen, und nach
                # einem Stromausfall steht die Datei unter dem richtigen Namen
                # mit leerem Inhalt da.
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(long_path(temp), long_path(ziel))
            self._health.record_success(str(ziel))
        except OSError as exc:
            self._cleanup(temp)
            self._health.record_failure(str(ziel), exc)
            self._reporter.warning(
                "filesystem.write",
                "Prompt konnte nicht gespeichert werden: " + ziel.name,
                message_key="prompt:write_failed",
                details=str(exc),
            )
            return WriteOutcome(FAILED, "Schreiben fehlgeschlagen: " + str(exc))

        info = self._stat(ziel)
        if info is None:
            # Geschrieben ist geschrieben; nur die neue Signatur fehlt. Sie
            # holt der naechste Abgleich nach.
            return WriteOutcome(SAVED, "Gespeichert", len(roh), 0)
        return WriteOutcome(SAVED, "Gespeichert", info[0], info[1])

    # Intern -------------------------------------------------------------------

    def _check_conflict(
        self, ziel: Path, expected_size: int, expected_mtime: int
    ) -> WriteOutcome | None:
        """Prueft, ob die Datei noch so ist, wie die Datenbank sie kennt."""
        if expected_mtime <= 0:
            return None  # kein Vergleichsstand vorhanden

        info = self._stat(ziel)
        if info is None:
            # Die Datei ist verschwunden. Das ist ein Konflikt, kein Grund, sie
            # neu anzulegen - vielleicht wurde sie bewusst geloescht.
            return WriteOutcome(
                CONFLICT,
                "Die Promptdatei ist nicht mehr vorhanden: " + ziel.name,
            )

        groesse, mtime = info
        if groesse == expected_size and abs(mtime - expected_mtime) <= MTIME_TOLERANCE_NS:
            return None

        return WriteOutcome(
            CONFLICT,
            "Die Datei wurde seit dem Einlesen geaendert: " + ziel.name,
            groesse,
            mtime,
        )

    def _stat(self, ziel: Path) -> tuple[int, int] | None:
        try:
            self._health.guard(str(ziel))
            info = os.stat(long_path(ziel))
            return info.st_size, info.st_mtime_ns
        except OSError:
            return None

    def _cleanup(self, temp: Path) -> None:
        """Raeumt die Nachbardatei weg. Ein Fehler dabei ist unerheblich."""
        try:
            os.remove(long_path(temp))
        except OSError:
            pass


def _encode(text: str, encoding: str, eol: str) -> bytes:
    """Text in Bytes, mit den Zeilenenden der urspruenglichen Datei.

    Eine Datei mit Windows-Enden darf nicht stillschweigend auf Unix-Enden
    umgestellt werden: das waere eine Aenderung an jeder einzelnen Zeile, die
    niemand angefordert hat, und sie wuerde bei der naechsten
    Versionsverwaltung als Vollumbau auftauchen.

    **Erst vereinheitlichen, dann setzen.** Ein schlichtes Ersetzen von "\\n"
    durch das Zielzeilenende ist falsch, sobald der Text bereits Windows-Enden
    traegt - aus einem CRLF wuerde ein CR CRLF, aus jeder Zeile also eine Zeile
    plus einer Leerzeile. Die Datei waechst bei jedem Speichern um ihre eigene
    Zeilenzahl.

    Der Text kommt hier durchaus mit Windows-Enden an: `decode_bytes`
    vereinheitlicht sie nicht, die Datenbank haelt sie so, wie sie in der Datei
    standen.

    Unbekannte Encodings fallen auf UTF-8 zurueck, statt zu werfen. Der Prompt
    ist dann zwar moeglicherweise anders kodiert als zuvor, aber lesbar - und
    ein gespeicherter Prompt in UTF-8 ist besser als ein verlorener.
    """
    vereinheitlicht = text.replace(CRLF, LF).replace(CR, LF)
    if eol and eol != LF:
        vereinheitlicht = vereinheitlicht.replace(LF, eol)
    try:
        return vereinheitlicht.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return vereinheitlicht.encode("utf-8", errors="replace")
