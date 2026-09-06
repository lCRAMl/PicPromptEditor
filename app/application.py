"""Composition Root - hier und nur hier werden die Schichten verdrahtet.

Startsequenz (Reihenfolge ist bindend):

  1. Bootstrap-Konfiguration laden (Datenbankpfad, Cachepfad, Logging-Ziel)
  2. Logging einrichten und Exception-Hooks installieren
  3. ErrorReporter mit LoggingSink aufsetzen
  4. Datenbank oeffnen und pruefen (Spezifikation 08: Existenz, Lesbarkeit, Konsistenz)
  5. Migrationen anwenden
  6. DbWriter starten
  7. Repositories anlegen, DatabaseSink am Reporter anmelden
  8. SettingsService laden (ergaenzt fehlende Werte, repariert ungueltige)

Erst danach duerfen Jobs, Services und GUI entstehen. Schlaegt Schritt 4 oder 5
fehl, liefert startup() ein Ergebnis mit recovery_required=True, damit die
Oberflaeche einen Wiederherstellungs- oder Neuindizierungsmodus anbieten kann,
statt zu starten und dann zu scheitern.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from app import APP_NAME, __version__
from app.config.bootstrap import BootstrapConfig, load_bootstrap
from app.config.settings_service import SettingsService
from app.core.errors import ErrorReporter, Severity
from app.core.logging_setup import LoggingSink, install_excepthooks, setup_logging
from app.core.paths import ensure_dir
from app.database.connection import Database
from app.database.migrations import SCHEMA_VERSION, MigrationError, apply_migrations
from app.database.writer import DbWriter
from app.filesystem.file_access import FileAccess
from app.filesystem.nas_health import NasHealthMonitor
from app.filesystem.prompt_writer import PromptWriter
from app.filesystem.scanner import Scanner
from app.filesystem.thumbnail_cache import ThumbnailCache
from app.filesystem.thumbnail_renderer import ThumbnailRenderer
from app.repositories.error_repo import DatabaseSink, ErrorRepository
from app.repositories.folder_repo import FolderRepository
from app.repositories.image_repo import ImageRepository
from app.repositories.prompt_repo import PromptRepository
from app.repositories.scan_repo import ScanRepository
from app.repositories.settings_repo import SettingsRepository
from app.repositories.stats_repo import StatsRepository
from app.repositories.cluster_repo import ClusterRepository
from app.repositories.evolution_repo import EvolutionRepository
from app.repositories.token_repo import TokenRepository


@dataclass
class StartupResult:
    ok: bool
    recovery_required: bool = False
    schema_version: int = 0
    applied_migrations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration: float = 0.0
    message: str = ""


class Application:
    """Haelt die langlebigen Komponenten und ihren Lebenszyklus."""

    def __init__(self, bootstrap_file: str | Path | None = None) -> None:
        self.bootstrap_file = bootstrap_file
        self.config: BootstrapConfig | None = None
        self.reporter = ErrorReporter()
        self.database: Database | None = None
        self.writer: DbWriter | None = None
        self.settings: SettingsService | None = None
        self.errors: ErrorRepository | None = None
        self.stats: StatsRepository | None = None
        self.log_file: Path | None = None
        self._started = False

        # Ab M1: Repositories, Dateisystem und Services. Erst nach startup()
        # verfuegbar, weil sie Datenbank und Einstellungen brauchen.
        self.folders: FolderRepository | None = None
        self.images: ImageRepository | None = None
        self.prompts: PromptRepository | None = None
        self.scans: ScanRepository | None = None
        self.tokens: TokenRepository | None = None
        self.clusters: ClusterRepository | None = None
        self.evolution: EvolutionRepository | None = None
        self.health: NasHealthMonitor | None = None
        self.files: FileAccess | None = None
        self.thumbnail_cache: ThumbnailCache | None = None
        self.scanner: Scanner | None = None
        self.jobs = None
        self.image_service = None
        self.prompt_service = None
        self.index_service = None
        self.system_service = None
        self.selection_service = None
        self.analysis_service = None
        self.compare_service = None
        self.evolution_service = None
        self.watch_service = None

    # Start --------------------------------------------------------------------

    def startup(self) -> StartupResult:
        began = time.perf_counter()
        notes: list[str] = []

        # 1 + 2 -----------------------------------------------------------------
        self.config, boot_notes = load_bootstrap(self.bootstrap_file)
        notes.extend(boot_notes)
        self.log_file = setup_logging(self.config.log_dir, self.config.log_level)
        self.reporter.add_sink(LoggingSink())
        install_excepthooks(self.reporter)
        self.reporter.info(
            "app",
            APP_NAME + " " + __version__ + " startet (Schema " + str(SCHEMA_VERSION) + ")",
            message_key="app:startup",
        )
        ensure_dir(self.config.thumbnail_cache_dir)

        # 4 --------------------------------------------------------------------
        db_path = Path(self.config.db_path)
        existed = db_path.exists()
        self.database = Database(db_path, self.reporter)
        if existed:
            healthy, detail = self.database.quick_check()
            if not healthy:
                self.reporter.critical(
                    "database",
                    "Datenbank ist beschaedigt: " + detail,
                    message_key="database:corrupt",
                )
                return StartupResult(
                    ok=False,
                    recovery_required=True,
                    notes=notes,
                    duration=time.perf_counter() - began,
                    message="Datenbank beschaedigt: " + detail,
                )
        else:
            notes.append("Neue Datenbank wird angelegt: " + str(db_path))

        # 5 --------------------------------------------------------------------
        try:
            applied = apply_migrations(self.database, self.reporter)
        except MigrationError as exc:
            return StartupResult(
                ok=False,
                recovery_required=True,
                notes=notes,
                duration=time.perf_counter() - began,
                message=str(exc),
            )
        migration_names = [str(m.version) + ":" + m.name for m in applied]
        if migration_names:
            notes.append("Migrationen angewandt: " + ", ".join(migration_names))

        # 6 + 7 ----------------------------------------------------------------
        self.writer = DbWriter(self.database, self.reporter)
        self.writer.start()

        self.errors = ErrorRepository(self.database, self.writer)
        self.stats = StatsRepository(self.database, self.writer)
        self.reporter.add_sink(DatabaseSink(self.errors, min_severity=Severity.WARNING))

        # 8 --------------------------------------------------------------------
        self.settings = SettingsService(
            SettingsRepository(self.database, self.writer), self.reporter
        )
        self.settings.load()

        if self.config.first_run:
            notes.append("Erststart: Standardeinstellungen angelegt")
        self.errors.set_status("index_status", "ready" if existed else "empty")

        # Erst hier anwendbar: beide Werte stehen in der Datenbank, und die
        # Bausteine, die sie steuern, mussten vorher existieren. Ohne diese
        # zwei Zeilen sind `jobs.db_write_batch` und `system.error_log_max_rows`
        # Regler ohne Wirkung - im Einstellungsdialog stehen sie trotzdem.
        self.writer.configure(batch_size=self.settings.get_int("jobs.db_write_batch"))
        self.errors.prune(self.settings.get_int("system.error_log_max_rows"))

        # 9: Repositories und Dateisystem (Qt-frei, deshalb schon hier) ---------
        self.folders = FolderRepository(self.database, self.writer)
        self.images = ImageRepository(self.database, self.writer)
        self.prompts = PromptRepository(self.database, self.writer)
        self.scans = ScanRepository(self.database, self.writer)
        self.tokens = TokenRepository(self.database, self.writer)
        self.clusters = ClusterRepository(self.database, self.writer)
        self.evolution = EvolutionRepository(self.database, self.writer)

        self.health = NasHealthMonitor(
            self.reporter,
            cooldown_s=float(self.settings.get_int("system.nas_probe_interval_s")),
            probe_timeout_s=self.settings.get_float("system.nas_probe_timeout_s"),
        )
        for root in self.settings.get_list("library.roots"):
            self.health.register_root(str(root))

        self.files = FileAccess(self.health, self.reporter)
        self.thumbnail_cache = ThumbnailCache(
            self.config.thumbnail_cache_dir,
            self.reporter,
            extension=self.settings.get_str("thumbnails.format"),
        )
        self.scanner = Scanner(
            self.folders,
            self.images,
            self.prompts,
            self.reporter,
            self.health,
            image_extensions=tuple(str(e) for e in self.settings.get_list("library.image_extensions")),
            prompt_extension=self.settings.get_str("library.prompt_extension"),
            mtime_tolerance_s=self.settings.get_float("index.mtime_tolerance_s"),
        )

        self._started = True
        return StartupResult(
            ok=True,
            schema_version=self.database.user_version,
            applied_migrations=migration_names,
            notes=notes,
            duration=time.perf_counter() - began,
            message="Start abgeschlossen",
        )

    # Qt-abhaengige Teile ------------------------------------------------------

    def build_services(self) -> None:
        """Erzeugt JobManager und Services.

        Getrennt von startup(), weil ab hier Qt im Spiel ist: JobManager und
        Services sind QObjects. Wer nur Datenbank, Scanner oder Analyse
        braucht - etwa ein Test oder ein Kommandozeilenwerkzeug - kommt ohne
        QApplication aus.
        """
        if not self._started:
            raise RuntimeError("startup() muss vor build_services() laufen")

        from app.jobs.job_manager import JobManager, make_pool_config
        from app.services.image_service import ImageService
        from app.services.index_service import IndexService
        from app.services.analysis_service import AnalysisService
        from app.services.compare_service import CompareService
        from app.services.evolution_service import EvolutionService
        from app.services.watch_service import WatchService
        from app.services.prompt_service import PromptService
        from app.services.selection_service import SelectionService
        from app.services.system_service import SystemService

        settings = self.settings
        self.jobs = JobManager(self.reporter, make_pool_config(settings.get_int))

        sizes = tuple(int(s) for s in settings.get_list("thumbnails.sizes"))
        renderer = ThumbnailRenderer(
            settings.get_str("thumbnails.format"), settings.get_int("thumbnails.quality")
        )

        self.image_service = ImageService(
            self.folders,
            self.images,
            self.thumbnail_cache,
            renderer,
            self.health,
            self.files,
            self.jobs,
            self.reporter,
            ram_cache_bytes=settings.get_int("cache.ram_thumbnails_mb") * 1024 * 1024,
            sizes=sizes,
            display_size=settings.get_int("thumbnails.default_size"),
        )
        self.prompt_service = PromptService(
            self.prompts, self.jobs, self.reporter,
            max_results=settings.get_int("search.max_results"),
            writer=PromptWriter(self.health, self.reporter),
            read_only=not settings.get_bool("compare.allow_export"),
        )
        self.index_service = IndexService(
            self.scanner, self.folders, self.images, self.prompts, self.stats,
            self.scans, self.writer, self.jobs, settings, self.reporter,
        )
        self.selection_service = SelectionService()
        self.analysis_service = AnalysisService(
            self.tokens, self.clusters, self.jobs, settings, self.reporter
        )
        self.compare_service = CompareService(
            self.images, self.prompts, self.tokens, self.jobs, settings, self.reporter
        )
        self.evolution_service = EvolutionService(
            self.evolution, self.clusters, self.jobs, settings, self.reporter
        )
        self.watch_service = WatchService(
            self.index_service, self.health, self.jobs, settings, self.reporter
        )
        self.system_service = SystemService(
            self.database, self.writer, self.errors, self.stats, self.health,
            self.jobs, self.reporter,
        )

        # Einstellungen, die laufende Komponenten sofort uebernehmen koennen.
        settings.subscribe(
            "compare.allow_export",
            lambda _k, v: self.prompt_service.set_read_only(not bool(v)),
        )
        settings.subscribe("thumbnails.default_size", lambda _k, v: self.image_service.set_display_size(int(v)))
        settings.subscribe(
            "cache.ram_thumbnails_mb",
            lambda _k, v: self.image_service.set_ram_limit(int(v) * 1024 * 1024),
        )

    # Zustand ------------------------------------------------------------------

    def status_report(self) -> dict[str, object]:
        """Kompakte Zustandsuebersicht. Grundlage der spaeteren Statusleiste."""
        report: dict[str, object] = {
            "app": APP_NAME + " " + __version__,
            "started": self._started,
        }
        if self.config is not None:
            report["db_path"] = self.config.db_path
            report["thumbnail_cache_dir"] = self.config.thumbnail_cache_dir
            report["log_file"] = str(self.log_file) if self.log_file else ""
        if self.database is not None:
            report["schema_version"] = self.database.user_version
        if self.writer is not None:
            report["writer"] = self.writer.stats
            report["writer_pending"] = self.writer.pending
        if self.stats is not None:
            report["counters"] = self.stats.all_counters()
        if self.errors is not None:
            report["open_errors"] = {
                s.name: c for s, c in self.errors.counts_by_severity().items()
            }
            report["system_status"] = self.errors.status()
        report["reported"] = {s.name: c for s, c in self.reporter.counts().items() if c}
        return report

    # Ende ---------------------------------------------------------------------

    def shutdown(self) -> None:
        """Reihenfolge umgekehrt zum Start: erst Jobs stoppen, dann Schreibvorgaenge
        leeren, dann schliessen. Ein noch laufender Job wuerde sonst auf eine
        geschlossene Datenbank schreiben."""
        if self.watch_service is not None:
            self.watch_service.stop()
            self.watch_service = None
        if self.system_service is not None:
            self.system_service.stop()
            self.system_service = None
        if self.jobs is not None:
            self.jobs.shutdown()
            self.jobs = None
        if self.writer is not None:
            try:
                self.writer.flush(timeout=15.0)
            except Exception as exc:
                self.reporter.exception(exc, source="app.shutdown", message_key="app:flush")
            self.writer.stop()
            self.writer = None
        if self.database is not None:
            self.database.close()
            self.database = None
        self._started = False
