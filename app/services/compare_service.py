"""CompareService - stellt zwei Bilder samt Prompts und fertigem Diff bereit.

Ein eigener Service statt eines Anhaengsels am AnalysisService: der Vergleich
zieht Daten aus drei Repositories zusammen (Bild, Prompt, Segmente) und ist die
einzige Stelle, an der das noetig ist.

Alles wird in EINEM Job geladen. Vier Einzelabfragen mit vier Signalen wuerden
die Ansicht in vier Stufen aufbauen und dabei jedes Mal neu zeichnen; ausserdem
muesste jede Stufe ihre eigene Veraltungspruefung mitbringen.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from app.analysis.diff import PromptDiff, compare_prompts
from app.analysis.textdiff import ComparisonResult, CompareOptions, compare_texts
from app.core.errors import ErrorReporter
from app.dto.models import ComparePair
from app.jobs.job_manager import JobManager
from app.jobs.scan_jobs import QueryJob
from app.repositories.image_repo import ImageRepository
from app.repositories.prompt_repo import PromptRepository
from app.repositories.token_repo import TokenRepository


class CompareService(QObject):
    """comparison_ready(ComparePair), comparison_failed(str)"""

    comparison_ready = pyqtSignal(object)
    comparison_failed = pyqtSignal(str)

    def __init__(
        self,
        images: ImageRepository,
        prompts: PromptRepository,
        tokens: TokenRepository,
        jobs: JobManager,
        settings,
        reporter: ErrorReporter,
    ) -> None:
        super().__init__()
        self._images = images
        self._prompts = prompts
        self._tokens = tokens
        self._jobs = jobs
        self._settings = settings
        self._reporter = reporter
        self._request_id = 0

    def compare_async(self, left_image_id: int, right_image_id: int) -> int:
        """Laedt beide Seiten und berechnet den Diff im Hintergrund."""
        self._request_id += 1
        request_id = self._request_id

        job = QueryJob(
            lambda: self._build(left_image_id, right_image_id, request_id),
            self._reporter,
            request_id=request_id,
            title="Vergleich wird vorbereitet",
        )
        job.signals.result.connect(self._on_result)
        self._jobs.submit(job)
        return request_id

    def compare_now(self, left_image_id: int, right_image_id: int) -> ComparePair | None:
        """Synchroner Zugriff. Nur fuer Aufrufer, die bereits in einem Job laufen."""
        return self._build(left_image_id, right_image_id, 0)

    def diff_of(self, left_prompt_id: int, right_prompt_id: int) -> PromptDiff:
        """Reiner Segmentvergleich ohne Bilder - fuer Tests und Kennzahlen."""
        return compare_prompts(
            self._tokens.diff_segments_for(left_prompt_id),
            self._tokens.diff_segments_for(right_prompt_id),
            self._settings.get_float("analysis.change_threshold"),
        )

    # Intern -------------------------------------------------------------------

    def _build(self, left_id: int, right_id: int, request_id: int) -> ComparePair | None:
        left_image = self._images.by_id(left_id)
        right_image = self._images.by_id(right_id)
        if left_image is None or right_image is None:
            return None

        left_prompt = (
            self._prompts.by_id(left_image.prompt_id) if left_image.prompt_id else None
        )
        right_prompt = (
            self._prompts.by_id(right_image.prompt_id) if right_image.prompt_id else None
        )

        diff: PromptDiff | None = None
        comparison: ComparisonResult | None = None
        if left_prompt is not None and right_prompt is not None:
            diff = compare_prompts(
                self._tokens.diff_segments_for(left_prompt.id),
                self._tokens.diff_segments_for(right_prompt.id),
                self._settings.get_float("analysis.change_threshold"),
            )
            # Beide Vergleiche laufen hier, im Arbeitsfaden, und nicht in der
            # Ansicht. Ein zeilenweiser Vergleich ueber zwei Prompts von je
            # vierhundert Zeilen ist keine Rechnung fuer den GUI-Faden - und
            # erst recht keine fuer ein Zeichenereignis.
            comparison = compare_texts(
                left_prompt.text, right_prompt.text, self.compare_options()
            )

        return ComparePair(
            left_image=left_image,
            right_image=right_image,
            left_prompt=left_prompt,
            right_prompt=right_prompt,
            diff=diff,
            comparison=comparison,
            request_id=request_id,
        )

    def compare_options(self) -> CompareOptions:
        """Die Vergleichsoptionen aus den Einstellungen.

        Jedes Mal frisch gelesen statt einmal zwischengespeichert: der
        Einstellungsdialog ist waehrend eines offenen Vergleichs erreichbar,
        und eine geaenderte Option soll beim naechsten Vergleich gelten, ohne
        dass jemand daran denken muss, sie irgendwohin durchzureichen.
        """
        return CompareOptions(
            ignore_case=self._settings.get_bool("compare.ignore_case"),
            ignore_whitespace_changes=self._settings.get_bool(
                "compare.ignore_whitespace_changes"
            ),
            ignore_blank_lines=self._settings.get_bool("compare.ignore_blank_lines"),
            intra_line_mode=self._settings.get_str("compare.intra_line_mode"),
            detect_moved=self._settings.get_bool("compare.detect_moved"),
            similarity_threshold=self._settings.get_float(
                "compare.similarity_threshold"
            ),
            algorithm=self._settings.get_str("compare.algorithm"),
        )

    def compare_now_texts(self, left: str, right: str) -> ComparisonResult:
        """Zeilenweiser Vergleich zweier Texte, ohne Bilder und ohne Datenbank.

        Der Weg, auf dem die Ansicht nach einer Uebernahme neu rechnet: dort
        liegen die neuen Texte bereits vor, ein erneutes Laden waere sinnlos.
        """
        return compare_texts(left, right, self.compare_options())

    def _on_result(self, _job_id: int, payload: object) -> None:
        request_id, pair = payload  # type: ignore[misc]
        if request_id != self._request_id:
            return  # inzwischen wurde ein anderer Vergleich angefordert
        if pair is None:
            self.comparison_failed.emit("Ein Bild des Vergleichs ist nicht mehr vorhanden")
            return
        if not pair.complete:
            self.comparison_failed.emit(
                "Zu mindestens einem Bild fehlt der Prompt - kein Vergleich moeglich"
            )
            return
        self.comparison_ready.emit(pair)
