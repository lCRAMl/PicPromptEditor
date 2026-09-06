"""Job der Parsing-Engine.

Laeuft im cpu-Pool: Parsen ist reine Rechenarbeit auf lokalen Daten, es wird
keine Datei angefasst. Der Prompttext kommt aus der Datenbank, wo der Scanner
ihn abgelegt hat.

Der Job arbeitet in Stapeln. Ein Stapel ist eine Schreibaufgabe und damit ein
Savepoint; das haelt die Zahl der Transaktionen klein und macht einen Abbruch
zwischen zwei Stapeln unschaedlich - beim naechsten Lauf werden schlicht die
noch nicht geparsten Prompts geholt.

Deduplizierung nach Prompttext: Batch-Generierungen erzeugen viele Dateien mit
identischem Text. Geparst wird einmal je eindeutigem Text, das Ergebnis gilt
fuer alle Prompts mit diesem Text. Die relationalen Zeilen entstehen trotzdem
fuer jeden Prompt, damit die Segmentsuche alle Bilder findet.
"""

from __future__ import annotations

import time

from app.analysis.parsing.base import PARSE_VERSION, ParsedPrompt, ParseStats
from app.analysis.parsing.pipeline import ParsingPipeline
from app.core.cancellation import CancellationToken
from app.core.errors import ErrorReporter
from app.core.text import text_hash
from app.jobs.job import Job
from app.repositories.token_repo import TokenRepository


class PromptParserJob(Job):
    pool = "cpu"
    title = "Prompts werden analysiert"

    def __init__(
        self,
        tokens: TokenRepository,
        pipeline: ParsingPipeline,
        reporter: ErrorReporter,
        *,
        batch_size: int = 200,
        max_prompts: int = 0,
        token: CancellationToken | None = None,
    ) -> None:
        super().__init__(reporter, token)
        self._tokens = tokens
        self._pipeline = pipeline
        self._batch_size = max(1, batch_size)
        self._max_prompts = max_prompts

    def run_job(self) -> ParseStats:
        started = time.perf_counter()
        stats = ParseStats()

        # Zuerst die Beitraege geloeschter Prompts abziehen. Sonst zaehlen sie in
        # doc_count weiter mit, und commonness wie idf werden falsch.
        removed = self._tokens.purge_absent_prompts()
        if removed:
            self._reporter.info(
                "jobs.parser",
                "Analysedaten von " + str(removed) + " entfernten Prompts bereinigt",
                message_key="parser:purged",
            )

        remaining = self._max_prompts if self._max_prompts > 0 else None
        total = self._tokens.unparsed_count(PARSE_VERSION)
        if total == 0:
            return stats

        while True:
            self.check_cancelled()
            limit = self._batch_size
            if remaining is not None:
                limit = min(limit, remaining)
                if limit <= 0:
                    break

            prompt_ids = self._tokens.unparsed_prompt_ids(PARSE_VERSION, limit)
            if not prompt_ids:
                break

            batch = self._parse_batch(prompt_ids, stats)
            if batch:
                stats.segments += self._tokens.sync_many(batch)
                stats.params += sum(len(parsed.params) for _pid, parsed in batch)
            stats.prompts += len(prompt_ids)

            if remaining is not None:
                remaining -= len(prompt_ids)
            self.report_progress(stats.prompts, total, "Prompts analysiert")

        stats.duration = time.perf_counter() - started
        return stats

    def _parse_batch(
        self, prompt_ids: list[int], stats: ParseStats
    ) -> list[tuple[int, ParsedPrompt]]:
        texts = self._tokens.prompt_texts(prompt_ids)
        by_hash: dict[str, ParsedPrompt] = {}
        batch: list[tuple[int, ParsedPrompt]] = []

        for prompt_id, text in texts:
            self.check_cancelled()
            digest = text_hash(text or "")
            parsed = by_hash.get(digest)
            if parsed is None:
                try:
                    parsed = self._pipeline.parse(text or "")
                except Exception as exc:
                    stats.failed += 1
                    self._reporter.warning(
                        "jobs.parser",
                        "Prompt " + str(prompt_id) + " nicht analysierbar",
                        message_key="parser:failed",
                        details=str(exc),
                    )
                    parsed = ParsedPrompt(format="error")
                by_hash[digest] = parsed
                stats.formats[parsed.format] = stats.formats.get(parsed.format, 0) + 1
            batch.append((prompt_id, parsed))

        return batch
