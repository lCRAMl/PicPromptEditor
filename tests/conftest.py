"""Gemeinsame Testfixtures.

Jeder Test bekommt eine frische library.db in einem temporaeren Verzeichnis.
Keine Testdatenbank im Speicher: WAL-Modus, mehrere Verbindungen und der
Writer-Thread verhalten sich bei ":memory:" anders als im Betrieb, und genau
dieses Verhalten soll geprueft werden.
"""

from __future__ import annotations

import os

import pytest

from app.application import Application
from app.config.bootstrap import BootstrapConfig, save_bootstrap
from app.core.errors import AppError, ErrorReporter
from app.database.connection import Database
from app.database.migrations import apply_migrations
from app.database.writer import DbWriter

# Ohne Bildschirm laufen zu koennen ist Voraussetzung fuer automatisierte Laeufe.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# In dieser Python-Installation liegen PyQt6 6.11 und PySide6 6.9 nebeneinander.
# Beide bringen eigene Qt6-DLLs gleichen Namens mit. Wird PySide6 zuerst geladen,
# scheitert PyQt6 anschliessend mit "DLL load failed while importing QtCore".
# pytest-qt waehlt seine Bindung sonst selbst - hier wird sie festgelegt.
os.environ.setdefault("PYTEST_QT_API", "pyqt6")
os.environ.setdefault("QT_API", "pyqt6")


@pytest.fixture
def reporter() -> ErrorReporter:
    return ErrorReporter()


@pytest.fixture
def collected(reporter: ErrorReporter) -> list[AppError]:
    """Sammelt alle gemeldeten Fehler in einer Liste."""
    seen: list[AppError] = []

    class Collector:
        def handle(self, error: AppError) -> None:
            seen.append(error)

    reporter.add_sink(Collector())
    return seen


@pytest.fixture
def database(tmp_path, reporter: ErrorReporter) -> Database:
    db = Database(tmp_path / "library.db", reporter)
    apply_migrations(db, reporter)
    yield db
    db.close()


@pytest.fixture
def writer(database: Database, reporter: ErrorReporter) -> DbWriter:
    w = DbWriter(database, reporter)
    w.start()
    yield w
    w.stop()


@pytest.fixture(scope="session")
def qapp():
    """Eine QApplication fuer die gesamte Sitzung.

    Qt erlaubt nur eine Instanz je Prozess, deshalb Sitzungsgeltung. Nur Tests,
    die Widgets oder Qt-Signale brauchen, fordern sie an - core, database,
    filesystem und analysis kommen bewusst ohne aus.
    """
    from PyQt6.QtWidgets import QApplication

    instance = QApplication.instance() or QApplication([])
    yield instance
    instance.processEvents()


def pump_until(qapp, condition, timeout_ms: int = 15000) -> bool:
    """Laesst die Ereignisschleife laufen, bis die Bedingung gilt.

    Ersatz fuer pytest-qt (nicht installiert). Liefert True, wenn die Bedingung
    eingetreten ist, und False bei Zeitablauf - so scheitert ein Test mit einer
    aussagekraeftigen Meldung statt an einem Folgefehler.
    """
    from PyQt6.QtCore import QEventLoop, QTimer

    if condition():
        return True
    loop = QEventLoop()
    ticker = QTimer()
    ticker.setInterval(10)
    ticker.timeout.connect(lambda: loop.quit() if condition() else None)
    ticker.start()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    ticker.stop()
    return condition()


@pytest.fixture
def application(tmp_path) -> Application:
    """Vollstaendig gestartete Anwendung mit isolierter Bootstrap-Konfiguration."""
    bootstrap_file = tmp_path / "bootstrap.json"
    save_bootstrap(
        BootstrapConfig(
            db_path=str(tmp_path / "library.db"),
            thumbnail_cache_dir=str(tmp_path / "thumbnails"),
            log_dir=str(tmp_path / "logs"),
            log_level="CRITICAL",
        ),
        bootstrap_file,
    )
    app = Application(bootstrap_file=bootstrap_file)
    result = app.startup()
    assert result.ok, result.message
    yield app
    app.shutdown()
