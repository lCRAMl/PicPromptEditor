"""PromptService - Laden, Suchen und Ausleiten von Prompts.

Der Prompttext kommt zur ANZEIGE vollstaendig aus der Datenbank; eine
Promptdatei wird dafuer niemals vom NAS gelesen.

**Eine bestehende Promptdatei wird von diesem Programm niemals ueberschrieben.**

Das ist eine Produktentscheidung, und sie ist hier baulich durchgesetzt statt
nur vereinbart: es gibt keine Methode, die in `prompt.full_path` schreibt.
`export_async` schreibt ausschliesslich an einen Pfad, den der Aufrufer nennt,
und die Vergleichsansicht laesst diesen Pfad vom Benutzer waehlen. Eine
Vereinbarung haette man versehentlich brechen koennen; eine fehlende Methode
nicht.

Deshalb ruehrt das Ausleiten die Datenbank auch nicht an. Die neue Datei ist
eine neue Datei - kein geaenderter Prompt. Liegt sie in einer Bibliothekswurzel,
nimmt der naechste Abgleich sie von selbst auf, samt Auswertung. Die Zeile des
Originals bleibt richtig, weil das Original unveraendert ist.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from app.core.errors import ErrorReporter
from app.dto.models import PromptData
from app.filesystem.prompt_writer import FAILED, PromptWriter
from app.jobs.job_manager import JobManager
from app.jobs.scan_jobs import QueryJob
from app.repositories.prompt_repo import PromptRepository


class PromptService(QObject):
    """prompt_ready(request_id, PromptData | None), search_ready(request_id, list[int]).

    Beide Signale tragen eine Anfragenummer. Mehrere Vorschauen und Suchfelder
    teilen sich denselben Service; ohne Nummer landet die Antwort auf die Anfrage
    des einen Tabs im anderen. Der Empfaenger merkt sich seine letzte Nummer und
    verwirft alles Aeltere.
    """

    prompt_ready = pyqtSignal(int, object)
    search_ready = pyqtSignal(int, object)
    #: save_finished(prompt_id, status, meldung). status ist einer der Werte
    #: aus app/filesystem/prompt_writer.py.
    save_finished = pyqtSignal(int, str, str)

    def __init__(
        self,
        prompts: PromptRepository,
        jobs: JobManager,
        reporter: ErrorReporter,
        *,
        max_results: int = 5000,
        writer: PromptWriter | None = None,
        read_only: bool = True,
    ) -> None:
        super().__init__()
        self._prompts = prompts
        self._jobs = jobs
        self._reporter = reporter
        self._max_results = max_results
        self._request_id = 0
        self._writer = writer
        self._read_only = read_only

    # Laden --------------------------------------------------------------------

    def load_for_image(self, image_id: int) -> int:
        """Laedt den Prompt eines Bildes im Hintergrund. Liefert die Anfragenummer.

        Auch wenn eine lokale SQLite-Abfrage schnell ist, gehoert sie nach
        Spezifikation 06 nicht in den GUI-Thread. Bei einer kalten Datenbank auf
        langsamer Platte ist der Unterschied sicht- und spuerbar.
        """
        self._request_id += 1
        request_id = self._request_id
        job = QueryJob(
            lambda: self._prompts.by_image_id(image_id),
            self._reporter,
            request_id=request_id,
            title="Prompt wird geladen",
        )
        job.signals.result.connect(
            lambda _id, payload: self.prompt_ready.emit(payload[0], payload[1])
        )
        self._jobs.submit(job)
        return request_id

    def load_by_id(self, prompt_id: int) -> PromptData | None:
        """Synchroner Zugriff. Nur fuer Aufrufer, die bereits in einem Job laufen."""
        return self._prompts.by_id(prompt_id)

    # Ausleiten ----------------------------------------------------------------

    @property
    def can_write(self) -> bool:
        """Ob das Ausleiten ueberhaupt moeglich ist.

        Zwei Bedingungen: es muss einen Schreiber geben, und die Einstellung
        `compare.allow_export` darf nicht abgeschaltet sein. Die Oberflaeche
        fragt das ab, bevor sie einen Knopf anbietet - ein Knopf, der immer nur
        eine Fehlermeldung erzeugt, ist schlimmer als keiner.
        """
        return self._writer is not None and not self._read_only

    def set_read_only(self, read_only: bool) -> None:
        self._read_only = read_only

    def export_async(
        self,
        prompt: PromptData,
        text: str,
        target_path: str,
        *,
        overwrite: bool = False,
    ) -> None:
        """Schreibt einen zusammengefuehrten Text in eine NEUE Datei.

        `target_path` kommt vom Benutzer, nicht aus dem Prompt. Der Aufrufer
        muss sichergestellt haben, dass er nicht auf eine der beiden
        Originaldateien zeigt - `is_original_path` ist dafuer da.

        Encoding und Zeilenenden werden vom Original uebernommen. Das ist kein
        Selbstzweck: der ausgeleitete Text soll dieselbe Datei sein koennen wie
        das Original, nur mit anderem Inhalt. Eine cp1252-Vorlage als UTF-8
        auszuleiten machte aus jedem Umlaut Unrat, und ein Wechsel der
        Zeilenenden aendert jede einzelne Zeile.

        `overwrite` bezieht sich auf eine bereits vorhandene ZIELdatei - der
        Dateidialog hat dann schon gefragt. Es kann eine Originaldatei nicht
        freigeben; die ist vorher ausgeschlossen worden.
        """
        if not self.can_write:
            self.save_finished.emit(
                prompt.id, FAILED, "Das Speichern ist in den Einstellungen abgeschaltet"
            )
            return

        job = QueryJob(
            lambda: self._export(prompt, text, target_path, overwrite),
            self._reporter,
            request_id=prompt.id,
            title="Prompt wird gespeichert",
        )
        job.signals.result.connect(self._on_saved)
        self._jobs.submit(job)

    def _export(
        self, prompt: PromptData, text: str, target_path: str, overwrite: bool
    ) -> tuple[str, str]:
        """Laeuft im Arbeitsfaden.

        Die Datenbank bleibt unberuehrt. Die neue Datei ist eine neue Datei,
        kein geaenderter Prompt; liegt sie in einer Bibliothekswurzel, nimmt der
        naechste Abgleich sie von selbst auf.
        """
        assert self._writer is not None  # can_write hat das geprueft

        ergebnis = self._writer.save(
            target_path,
            text,
            encoding=prompt.encoding or "utf-8",
            eol=_dominant_eol(prompt.text),
            # Keine Konflikterkennung gegen den Stand des ORIGINALS - hier wird
            # eine andere Datei geschrieben, deren Groesse und Zeitstempel mit
            # dem Original nichts zu tun haben. Ob die Zieldatei ueberschrieben
            # werden darf, hat der Dateidialog geklaert.
            force=True if overwrite else False,
            expected_size=0,
            expected_mtime=0,
        )
        return ergebnis.status, ergebnis.message

    def _on_saved(self, _job_id: int, payload: object) -> None:
        prompt_id, ergebnis = payload  # type: ignore[misc]
        status, meldung = ergebnis
        self.save_finished.emit(int(prompt_id), status, meldung)

    # Suche --------------------------------------------------------------------

    def search_async(self, query: str, folder_ids: list[int] | None = None) -> int:
        """Volltextsuche ueber den FTS5-Index. Liefert Bild-Ids.

        folder_ids schraenkt auf einen Ordner samt Unterordnern ein. Die
        Einschraenkung passiert in der Abfrage, nicht nachtraeglich im Speicher:
        bei hunderttausend Prompts waere das Nachfiltern von zehntausend Treffern
        auf zwanzig verschwendete Arbeit.
        """
        self._request_id += 1
        request_id = self._request_id
        text = query.strip()
        if not text:
            self.search_ready.emit(request_id, [])
            return request_id

        scope = list(folder_ids) if folder_ids else None
        job = QueryJob(
            lambda: self._prompts.search_ids(text, self._max_results, scope),
            self._reporter,
            request_id=request_id,
            title="Suche laeuft",
        )
        job.signals.result.connect(self._on_search)
        self._jobs.submit(job)
        return request_id

    def _on_search(self, _job_id: int, payload: object) -> None:
        request_id, ids = payload  # type: ignore[misc]
        if request_id != self._request_id:
            return  # der Benutzer hat inzwischen weitergetippt
        self.search_ready.emit(request_id, ids)


def _dominant_eol(text: str) -> str:
    """Das Zeilenende, das in diesem Text ueberwiegt.

    Der Prompttext liegt in der Datenbank mit den Zeilenenden, die auch in der
    Datei stehen - `decode_bytes` vereinheitlicht sie nicht. Beim
    Zurueckschreiben muessen sie erhalten bleiben: eine Datei mit
    Windows-Enden auf Unix-Enden umzustellen waere eine Aenderung an jeder
    einzelnen Zeile, die niemand angefordert hat.

    Bei gemischten Enden - es gibt solche Dateien - gewinnt die Mehrheit. Bei
    Gleichstand oder in einer Datei ohne jeden Umbruch bleibt es beim einfachen
    Zeilenvorschub.
    """
    crlf = text.count(chr(13) + chr(10))
    lf = text.count(chr(10)) - crlf
    return chr(13) + chr(10) if crlf > lf else chr(10)
