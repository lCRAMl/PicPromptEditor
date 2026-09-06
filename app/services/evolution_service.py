"""EvolutionService - Baeume liefern, ohne beim Oeffnen zu rechnen.

Spezifikation 06: "Prompt Evolution wird niemals beim Oeffnen eines Tabs neu
berechnet. Existiert ein gueltiger Eintrag im evolution_cache, wird dieser
sofort verwendet."

Der Service setzt das woertlich um. tree_for_image() liefert entweder sofort
einen fertigen Baum oder gar keinen; im zweiten Fall meldet es, dass gerechnet
werden muss, und der Benutzer entscheidet. Ein stilles Nachrechnen beim Klick
waere bei grossen Familien eine Wartezeit ohne Erklaerung.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from app.analysis.evolution import ALGO_VERSION, EvolutionStats
from app.core.errors import ErrorReporter
from app.jobs.evolution_jobs import EvolutionJob
from app.jobs.job_manager import JobManager
from app.jobs.scan_jobs import QueryJob
from app.repositories.cluster_repo import ClusterRepository
from app.repositories.evolution_repo import EvolutionRepository

EVOLUTION_GROUP = "evolution"


class EvolutionService(QObject):
    """tree_ready(request_id, EvolutionTree), tree_missing(str),
    build_finished(EvolutionStats), build_progress(current, total)"""

    tree_ready = pyqtSignal(int, object)
    tree_missing = pyqtSignal(str)
    build_finished = pyqtSignal(object)
    build_progress = pyqtSignal(int, int)

    def __init__(
        self,
        evolution: EvolutionRepository,
        clusters: ClusterRepository,
        jobs: JobManager,
        settings,
        reporter: ErrorReporter,
    ) -> None:
        super().__init__()
        self._repo = evolution
        self._clusters = clusters
        self._jobs = jobs
        self._settings = settings
        self._reporter = reporter
        self._request_id = 0
        self._running = False

    # Abfragen -----------------------------------------------------------------

    @property
    def build_running(self) -> bool:
        return self._running

    def pending_count(self) -> int:
        return len(self._repo.stale_clusters(ALGO_VERSION))

    def counts(self) -> dict[str, int]:
        return self._repo.counts()

    def cluster_for_image(self, image_id: int) -> int | None:
        return self._repo.cluster_for_image(image_id)

    def images_for_prompt(self, prompt_id: int) -> list[int]:
        return self._repo.images_for_prompt(prompt_id)

    def segment_texts(self, token_ids) -> dict[int, str]:
        return self._repo.segment_texts(list(token_ids))

    def families(self, limit: int = 200) -> list[dict[str, int]]:
        """Die Familien mit fertigem Baum - fuer die Auswahlliste.

        Synchron: es ist eine einzelne Abfrage auf einen Index, und die Liste
        wird beim Aufbau des Tabs genau einmal gebraucht. Ein Job dafuer waere
        mehr Verwaltung als Arbeit.
        """
        return self._repo.available_clusters(ALGO_VERSION, limit)

    def tree_for_cluster(self, cluster_id: int):
        """Fertiger Baum einer Familie, oder None.

        Synchron, weil der Aufrufer bereits einen Baum anzeigt und nur
        umschaltet - das Lesen ist eine Abfrage samt JSON-Auswertung, keine
        Rechnung. Der erste Baum eines Tabs kommt weiterhin ueber
        `tree_for_image_async`, denn dort haengt eine Kette aus drei Abfragen
        daran.
        """
        return self._repo.tree_for_cluster(cluster_id, ALGO_VERSION)

    def tree_for_image_async(self, image_id: int) -> int:
        """Laedt den Baum der Familie dieses Bildes - ohne zu rechnen."""
        self._request_id += 1
        request_id = self._request_id

        def load():
            """Liefert (Baum, Grund).

            Der Grund unterscheidet drei Faelle, die sich fuer den Benutzer
            voellig verschieden anfuehlen und frueher alle dieselbe Meldung
            bekamen - eine, die zu einer Handlung aufforderte, die in zwei von
            drei Faellen nichts aendern konnte.
            """
            if not self._repo.has_prompt(image_id):
                return None, "kein_prompt"
            cluster_id = self._repo.cluster_for_image(image_id)
            if cluster_id is None:
                return None, "keine_familie"
            return self._repo.tree_for_cluster(cluster_id, ALGO_VERSION), "ok"

        job = QueryJob(
            load, self._reporter, request_id=request_id, title="Evolution wird geladen"
        )
        job.signals.result.connect(self._on_tree)
        self._jobs.submit(job)
        return request_id

    def _on_tree(self, _job_id: int, payload: object) -> None:
        request_id, result = payload  # type: ignore[misc]
        if request_id != self._request_id:
            return
        tree, reason = result

        # Nur der dritte Fall laesst sich durch Berechnen beheben. Die beiden
        # anderen dorthin zu schicken, waere eine Sackgasse.
        if reason == "kein_prompt":
            self.tree_missing.emit(
                "Zu diesem Bild gibt es keine Promptdatei - ohne Prompt kein"
                " Baum."
            )
            return
        if reason == "keine_familie":
            self.tree_missing.emit(
                "Der Prompt dieses Bildes gehoert zu keiner Familie, deshalb"
                " gibt es keinen Baum. Verwandtes findet 'Aehnliche Prompts"
                " finden'."
            )
            return
        if tree is None:
            self.tree_missing.emit(
                "Fuer diese Familie liegt noch kein Evolutionsbaum vor."
                " Ueber Analyse / Evolutionsbaeume berechnen anlegen."
            )
            return
        if tree.size < 2:
            self.tree_missing.emit(
                "Die Familie dieses Bildes hat nur ein Mitglied - kein Baum darstellbar."
            )
            return
        self.tree_ready.emit(request_id, tree)

    # Bauen --------------------------------------------------------------------

    def start_build(self, only_cluster: int | None = None) -> int | None:
        if self._running:
            return None
        if not self._settings.get_bool("analysis.enabled"):
            return None
        if only_cluster is None and self.pending_count() == 0:
            # Nichts zu tun heisst: jede Familie hat schon einen gueltigen
            # Baum. Frueher meldete das "keine Evolutionsdaten" - das klang
            # nach Fehlschlag, obwohl alles in Ordnung ist.
            self.build_finished.emit(EvolutionStats(up_to_date=self._repo.counts()["trees"]))
            return None

        self._running = True
        job = EvolutionJob(
            self._repo,
            self._clusters.idf_map(),
            self._all_segment_texts(),
            self._reporter,
            max_nodes=self._settings.get_int("analysis.evolution_max_nodes"),
            change_threshold=self._settings.get_float("analysis.change_threshold"),
            only_cluster=only_cluster,
        )
        job.signals.progress.connect(
            lambda _id, current, total, _text: self.build_progress.emit(current, total)
        )
        job.signals.result.connect(self._on_build_result)
        job.signals.finished.connect(self._on_build_finished)
        return self._jobs.submit(job, group=EVOLUTION_GROUP)

    def _all_segment_texts(self) -> dict[int, str]:
        """Alle Segmenttexte - Grundlage von Aenderungserkennung und Beschriftung."""
        return self._clusters.segment_text_map()

    def cancel_build(self) -> None:
        self._jobs.cancel_group(EVOLUTION_GROUP)

    def invalidate_all(self) -> None:
        """Nach neu berechneten Familien sind alle Baeume ueberholt."""
        self._repo.mark_all_dirty()

    def _on_build_result(self, _job_id: int, payload: object) -> None:
        self._last = payload

    def _on_build_finished(self, _job_id: int, _state: str) -> None:
        self._running = False
        self.build_finished.emit(getattr(self, "_last", EvolutionStats()))
        self._last = EvolutionStats()
