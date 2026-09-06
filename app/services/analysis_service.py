"""AnalysisService - Parsing, Segmentsuche und Segmentstatistik.

Ab M3 die Schnittstelle der Oberflaeche zur Analyse. Similarity, Cluster und
Varianten kommen in M4 hinzu und reihen sich hier ein.

Die Segmentsuche laeuft ueber prompt_tokens, nicht ueber den Volltext
(Spezifikation 05). Der Unterschied ist nicht nur Geschwindigkeit: die Suche
nach einem markierten Satz findet genau die Prompts, die ihn als Segment
enthalten - nicht die, in denen die Woerter zufaellig verstreut vorkommen.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from app.analysis.parsing.base import PARSE_VERSION, ParseStats
from app.analysis.parsing.pipeline import ParsingPipeline
from app.core.errors import ErrorReporter
from app.core.text import normalize_segment, segment_hash
from app.jobs.job_manager import JobManager
from app.jobs.analysis_jobs import ALGO_VERSION, PromptAnalysisJob
from app.jobs.parser_jobs import PromptParserJob
from app.jobs.scan_jobs import QueryJob
from app.repositories.cluster_repo import ClusterRepository
from app.repositories.token_repo import TokenRepository

PARSE_GROUP = "parse"
ANALYSIS_GROUP = "analysis"


class AnalysisService(QObject):
    """parse_finished(ParseStats), parse_progress(current, total),
    segment_results(request_id, hash, list[int])"""

    parse_finished = pyqtSignal(object)
    parse_progress = pyqtSignal(int, int)
    segment_results = pyqtSignal(int, str, object)
    analysis_finished = pyqtSignal(object)
    #: Der Lauf ist gescheitert. Getrennt von analysis_finished, damit ein
    #: Fehlschlag nicht als Erfolg mit leerem Ergebnis erscheint.
    analysis_failed = pyqtSignal(str)
    analysis_progress = pyqtSignal(int, int)
    similar_results = pyqtSignal(int, object)

    def __init__(
        self,
        tokens: TokenRepository,
        clusters: ClusterRepository,
        jobs: JobManager,
        settings,
        reporter: ErrorReporter,
    ) -> None:
        super().__init__()
        self._tokens = tokens
        self._clusters = clusters
        self._jobs = jobs
        self._settings = settings
        self._reporter = reporter
        self._running = False
        self._analysing = False
        self._request_id = 0
        self._last_error = ""

    # Pipeline -----------------------------------------------------------------

    def _pipeline(self) -> ParsingPipeline:
        return ParsingPipeline(
            format_preference=self._settings.get_str("parsing.format"),
            negative_markers=tuple(
                str(m) for m in self._settings.get_list("parsing.negative_markers")
            ),
            min_segment_words=self._settings.get_int("analysis.min_segment_words"),
        )

    def parse_text(self, text: str):
        """Einzelnen Text parsen, ohne Datenbank. Fuer Vorschau und Tests."""
        return self._pipeline().parse(text)

    # Hintergrundlauf ----------------------------------------------------------

    @property
    def parse_running(self) -> bool:
        return self._running

    def pending_count(self) -> int:
        """Zu erledigende Arbeit: neu zu parsen ODER aufzuraeumen.

        Beides muss zaehlen. Nach dem Loeschen von Dateien gibt es nichts zu
        parsen, aber die Analysezeilen der entfernten Prompts blaehen die
        Haeufigkeiten weiter auf - der Lauf muss trotzdem starten.
        """
        return self._tokens.unparsed_count(PARSE_VERSION) + self._tokens.stale_analysis_count()

    def start_parsing(self, max_prompts: int = 0) -> int | None:
        if self._running:
            return None
        if not self._settings.get_bool("analysis.enabled"):
            return None
        if self.pending_count() == 0:
            self.parse_finished.emit(ParseStats())
            return None

        self._running = True
        job = PromptParserJob(
            self._tokens, self._pipeline(), self._reporter, max_prompts=max_prompts
        )
        job.signals.progress.connect(
            lambda _id, current, total, _text: self.parse_progress.emit(current, total)
        )
        job.signals.result.connect(self._on_result)
        job.signals.finished.connect(self._on_finished)
        return self._jobs.submit(job, group=PARSE_GROUP)

    def cancel_parsing(self) -> None:
        self._jobs.cancel_group(PARSE_GROUP)

    def _on_result(self, _job_id: int, payload: object) -> None:
        self._last = payload

    def _on_finished(self, _job_id: int, _state: str) -> None:
        self._running = False
        self.parse_finished.emit(getattr(self, "_last", ParseStats()))
        self._last = ParseStats()

    # Aehnlichkeit, Familien, Varianten ----------------------------------------

    @property
    def analysis_running(self) -> bool:
        return self._analysing

    def analysis_pending_count(self) -> int:
        return self._clusters.pending_count(ALGO_VERSION)

    def start_analysis(self) -> int | None:
        """Berechnet Aehnlichkeiten, Familien und Varianten neu.

        Bewusst ein vollstaendiger Neuaufbau: Familien sind globale Strukturen,
        ein neuer Prompt kann zwei bestehende verbinden. Der Aufwand bleibt
        beherrschbar, weil nur Repraesentanten verglichen werden.
        """
        if self._analysing or self._running:
            return None
        if not self._settings.get_bool("analysis.enabled"):
            return None

        self._analysing = True
        job = PromptAnalysisJob(
            self._clusters,
            self._reporter,
            similarity_threshold=self._settings.get_float("analysis.similarity_threshold"),
            cluster_threshold=self._settings.get_float("analysis.cluster_threshold"),
            neighbours_k=self._settings.get_int("analysis.neighbors_k"),
            base_min_commonness=self._settings.get_float(
                "analysis.base_prompt_min_commonness"
            ),
        )
        job.signals.progress.connect(
            lambda _id, current, total, _text: self.analysis_progress.emit(current, total)
        )
        job.signals.result.connect(self._on_analysis_result)
        job.signals.failed.connect(self._on_analysis_failed)
        job.signals.finished.connect(self._on_analysis_finished)
        return self._jobs.submit(job, group=ANALYSIS_GROUP)

    def cancel_analysis(self) -> None:
        self._jobs.cancel_group(ANALYSIS_GROUP)

    def _on_analysis_result(self, _job_id: int, payload: object) -> None:
        self._last_analysis = payload
        self._last_error = ""

    def _on_analysis_failed(self, _job_id: int, message: str) -> None:
        """Ein gescheiterter Lauf darf nicht wie ein leeres Ergebnis aussehen.

        Vorher lieferte ein Fehlschlag dasselbe Bild wie "nichts zu tun": eine
        leere Statistik. Der Fehler stand nur im Protokoll, und die Anwendung
        meldete beruhigend "keine Aenderungen". Wer die Analyse anstoesst und
        eine Erfolgsmeldung bekommt, sucht den Fehler nicht.
        """
        self._last_error = message or "unbekannter Fehler"

    def _on_analysis_finished(self, _job_id: int, state: str) -> None:
        from app.jobs.analysis_jobs import AnalysisResult

        self._analysing = False
        if self._last_error or state == "failed":
            meldung = self._last_error or "Der Lauf wurde nicht abgeschlossen"
            self._last_error = ""
            self.analysis_failed.emit(meldung)
            return
        self.analysis_finished.emit(getattr(self, "_last_analysis", AnalysisResult()))
        self._last_analysis = AnalysisResult()

    def similar_images_async(self, prompt_id: int) -> int:
        """Bilder aehnlicher Prompts. Grundlage von "Aehnliche Verwendungen finden"."""
        self._request_id += 1
        request_id = self._request_id
        limit = self._settings.get_int("search.max_results")
        job = QueryJob(
            lambda: self._clusters.similar_images(prompt_id, limit),
            self._reporter,
            request_id=request_id,
            title="Aehnliche Prompts",
        )
        job.signals.result.connect(
            lambda _id, payload: self.similar_results.emit(payload[0], payload[1])
        )
        self._jobs.submit(job)
        return request_id

    def cluster_of(self, prompt_id: int):
        return self._clusters.cluster_of(prompt_id)

    def variant_of(self, prompt_id: int):
        return self._clusters.variant_of(prompt_id)

    def cluster_images(self, cluster_id: int) -> list[int]:
        return self._clusters.cluster_images(
            cluster_id, self._settings.get_int("search.max_results")
        )

    def largest_clusters(self, limit: int = 50):
        return self._clusters.largest_clusters(limit)

    # Segmentsuche -------------------------------------------------------------

    def segment_hash_for(self, text: str) -> str:
        """Vergleichsform und Hash einer Benutzerauswahl."""
        return segment_hash(normalize_segment(text))

    def search_segment_async(self, text: str, kind: str | None = None) -> int:
        """Sucht alle Bilder, deren Prompt dieses Segment enthaelt."""
        self._request_id += 1
        request_id = self._request_id
        normalized = normalize_segment(text)
        if not normalized:
            self.segment_results.emit(request_id, "", [])
            return request_id

        digest = segment_hash(normalized)
        limit = self._settings.get_int("search.max_results")

        job = QueryJob(
            lambda: self._tokens.images_for_segment(digest, kind, limit),
            self._reporter,
            request_id=request_id,
            title="Segmentsuche",
        )
        job.signals.result.connect(
            lambda _id, payload, h=digest: self._on_segment(payload, h)
        )
        self._jobs.submit(job)
        return request_id

    def _on_segment(self, payload: object, digest: str) -> None:
        request_id, ids = payload  # type: ignore[misc]
        if request_id != self._request_id:
            return
        self.segment_results.emit(request_id, digest, ids)

    def segment_usage(self, text: str) -> tuple[int, int]:
        return self._tokens.segment_usage(segment_hash(normalize_segment(text)))

    # Statistik ----------------------------------------------------------------

    def segment_stats_for_prompt(self, prompt_id: int) -> list[dict[str, object]]:
        return self._tokens.segment_stats_for_prompt(prompt_id)

    def top_segments(self, kind: str | None = None, limit: int = 100):
        return self._tokens.top_segments(kind, limit)

    def param_keys(self):
        return self._tokens.param_keys()

    def param_values(self, key_norm: str):
        return self._tokens.param_values(key_norm)

    def images_for_param(self, key_norm: str, value_text: str) -> list[int]:
        return self._tokens.images_for_param(
            key_norm, value_text, self._settings.get_int("search.max_results")
        )

    def counts(self) -> dict[str, int]:
        merged = dict(self._tokens.counts())
        merged.update(self._clusters.counts())
        return merged
